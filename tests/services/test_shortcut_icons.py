"""Tests for services/shortcut_icons.py — the background job that gives waiting shortcuts their icons."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import pytest
from _factories import _make_conflict_rules, _make_prune_conflicts
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeSleeper
from models.shortcut_icon import IconAnswer, IconFetch

from domain.rom import Rom
from domain.shortcut_icon import LOGO_ICON_NAME, PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG
from lib.errors import SteamGridDirMissingError
from services.shortcut_icons import ShortcutIconService, ShortcutIconServiceConfig

if TYPE_CHECKING:
    from lib.prune_conflicts import PruneConflicts

_PLACEHOLDER = f"/grid/{PLACEHOLDER_ICON_NAME}"
_LOGO = f"/grid/{LOGO_ICON_NAME}"


class _FakeSteamConfig:
    """The two things the job asks of Steam's config: write its fixed icons, read every shortcut's icon."""

    def __init__(self, icons: dict[int, str] | None) -> None:
        self.icons = icons
        self.ensured: dict[str, bytes] = {}
        self.grid_missing = False

    def ensure_grid_file(self, name: str, content: bytes) -> str:
        if self.grid_missing:
            raise SteamGridDirMissingError("Cannot find Steam grid directory")
        self.ensured[name] = content
        return f"/grid/{name}"

    def read_shortcut_icons(self) -> dict[int, str] | None:
        return None if self.icons is None else dict(self.icons)


class _FakeIconSource:
    """Answers fetches from a script per ROM; records what the job wrote."""

    def __init__(self) -> None:
        self.answers: dict[int, list[IconFetch]] = {}
        self.generations: dict[int, int] = {}
        self.fetched: list[int] = []
        self.written: dict[int, bytes] = {}
        self.write_gate: asyncio.Event | None = None
        self.on_fetch: Any = None

    def answer(self, rom_id: int, *fetches: IconFetch) -> None:
        self.answers[rom_id] = list(fetches)

    def icon_generation(self, app_id: int) -> int:
        return self.generations.get(app_id, 0)

    def fetch_shortcut_icon_io(self, rom_id: int) -> IconFetch:
        self.fetched.append(rom_id)
        if self.on_fetch is not None:
            self.on_fetch(rom_id)
        script = self.answers.get(rom_id) or [IconFetch(IconAnswer.ICON, b"icon")]
        return script.pop(0) if len(script) > 1 else script[0]

    async def write_job_icon(self, app_id: int, icon_bytes: bytes, generation: int) -> str | None:
        if self.write_gate is not None:
            await self.write_gate.wait()
        if self.icon_generation(app_id) != generation:
            return None
        self.written[app_id] = icon_bytes
        return f"/grid/{app_id}_icon.png"


class _Job:
    def __init__(
        self,
        *,
        icons: dict[int, str] | None,
        bound: dict[int, int],
        key: str = "key",
        logo: bytes | None = b"logo",
        prune_conflicts: PruneConflicts | None = None,
    ) -> None:
        self.steam_config = _FakeSteamConfig(icons)
        self.source = _FakeIconSource()
        self.sink = FakeEventSink()
        self.sleeper = FakeSleeper()
        self.settings: dict[str, Any] = {"steamgriddb_api_key": key}
        self.sync_running = False
        self.connected = True
        self.prune_conflicts = prune_conflicts or _make_prune_conflicts()
        uow = FakeUnitOfWork()
        with uow:
            for app_id, rom_id in bound.items():
                uow.roms.save(
                    Rom(
                        rom_id=rom_id,
                        platform_slug="n64",
                        name=f"Game {rom_id}",
                        fs_name=f"game{rom_id}.z64",
                        shortcut_app_id=app_id,
                        last_synced_at="2025-01-01T00:00:00",
                    )
                )
        self.service = ShortcutIconService(
            config=ShortcutIconServiceConfig(
                steam_config=self.steam_config,  # type: ignore[arg-type]
                icons=self.source,
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                settings=self.settings,
                conflict_rules=_make_conflict_rules(prune_conflicts=self.prune_conflicts),
                emit=self.sink.emit,
                sleeper=self.sleeper,
                sync_in_flight=lambda: self.sync_running,
                cleanup_running=lambda: self.prune_conflicts.cleanup_running,
                panel_connected=lambda: self.connected,
                tender_logo=lambda: logo,
                loop=asyncio.get_running_loop(),
                logger=logging.getLogger("test_shortcut_icons"),
                log_debug=lambda msg: None,
            )
        )

    async def run(self, **options: Any) -> None:
        self.service.request_run(**options)
        task = self.service._task
        assert task is not None
        await task

    def batches(self) -> list[list[dict[str, Any]]]:
        return [payload["icons"] for name, payload in self.sink.events if name == "shortcut_icons"]

    def delivered(self) -> dict[int, str]:
        return {icon["app_id"]: icon["icon_path"] for batch in self.batches() for icon in batch}


class TestARun:
    @pytest.mark.asyncio
    async def test_gives_every_waiting_shortcut_its_icon_under_one_lease(self):
        job = _Job(icons={100: "", 200: _PLACEHOLDER}, bound={100: 1, 200: 2})

        await job.run()

        assert job.delivered() == {100: "/grid/100_icon.png", 200: "/grid/200_icon.png"}
        ((name, payload),) = job.sink.events
        assert name == "shortcut_icons"
        assert payload["prune_lease_token"]

    @pytest.mark.asyncio
    async def test_writes_the_placeholder_and_the_logo_into_the_grid_first(self):
        job = _Job(icons={}, bound={})

        await job.run()

        assert job.steam_config.ensured == {PLACEHOLDER_ICON_NAME: PLACEHOLDER_ICON_PNG, LOGO_ICON_NAME: b"logo"}

    @pytest.mark.asyncio
    async def test_leaves_the_logo_and_an_icon_set_by_hand_alone(self):
        job = _Job(icons={100: _LOGO, 200: "/home/deck/mine.png"}, bound={100: 1, 200: 2})

        await job.run()

        assert job.source.fetched == []
        assert job.sink.events == []

    @pytest.mark.asyncio
    async def test_a_game_with_no_icon_gets_the_logo(self):
        job = _Job(icons={100: _PLACEHOLDER}, bound={100: 1})
        job.source.answer(1, IconFetch(IconAnswer.NO_ICON))

        await job.run()

        assert job.delivered() == {100: _LOGO}
        assert job.source.written == {}

    @pytest.mark.asyncio
    async def test_without_a_logo_to_give_a_game_with_no_icon_keeps_its_placeholder(self):
        job = _Job(icons={100: _PLACEHOLDER}, bound={100: 1}, logo=None)
        job.source.answer(1, IconFetch(IconAnswer.NO_ICON))

        await job.run()

        assert job.sink.events == []

    @pytest.mark.asyncio
    async def test_a_failed_fetch_leaves_the_placeholder_for_the_next_run(self):
        job = _Job(icons={100: _PLACEHOLDER, 200: ""}, bound={100: 1, 200: 2})
        job.source.answer(1, IconFetch(IconAnswer.FAILED))

        await job.run()

        assert job.delivered() == {200: "/grid/200_icon.png"}

    @pytest.mark.asyncio
    async def test_sends_the_icons_in_batches_of_25(self):
        bound = {1000 + n: n for n in range(30)}
        job = _Job(icons=dict.fromkeys(bound, ""), bound=bound)

        await job.run()

        assert sorted(len(batch) for batch in job.batches()) == [5, 25]
        assert len(job.delivered()) == 30

    @pytest.mark.asyncio
    async def test_drops_the_icon_of_a_shortcut_whose_icon_was_picked_on_the_game_page_meanwhile(self):
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})
        job.source.answer(2, IconFetch(IconAnswer.NO_ICON))

        def pick_while_fetching(_rom_id: int) -> None:
            job.source.generations[100] = 1
            job.source.generations[200] = 1

        job.source.on_fetch = pick_while_fetching

        await job.run()

        assert job.sink.events == []

    @pytest.mark.asyncio
    async def test_a_pick_after_the_logo_was_queued_keeps_the_logo_out_of_the_batch(self):
        job = _Job(icons={100: _PLACEHOLDER, 200: ""}, bound={100: 1, 200: 2})
        job.source.answer(1, IconFetch(IconAnswer.NO_ICON))
        flush = job.service._flush

        async def pick_then_flush(batch: list[Any]) -> None:
            job.source.generations[100] = 1
            await flush(batch)

        job.service._flush = pick_then_flush  # type: ignore[method-assign]

        await job.run()

        assert job.delivered() == {200: "/grid/200_icon.png"}

    @pytest.mark.asyncio
    async def test_a_batch_that_a_pick_empties_is_not_handed_over(self):
        job = _Job(icons={100: _PLACEHOLDER}, bound={100: 1})
        job.source.answer(1, IconFetch(IconAnswer.NO_ICON))
        flush = job.service._flush

        async def pick_then_flush(batch: list[Any]) -> None:
            job.source.generations[100] = 1
            await flush(batch)

        job.service._flush = pick_then_flush  # type: ignore[method-assign]

        await job.run()

        assert job.sink.events == []
        assert job.prune_conflicts.held_claims() == ()


class TestWhatItWaitsFor:
    @pytest.mark.asyncio
    async def test_does_nothing_without_an_api_key(self):
        job = _Job(icons={100: ""}, bound={100: 1}, key="")

        await job.run()

        assert job.source.fetched == []
        assert job.steam_config.ensured == {}

    @pytest.mark.asyncio
    async def test_does_nothing_while_a_sync_runs(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.sync_running = True

        await job.run()

        assert job.source.fetched == []

    @pytest.mark.asyncio
    async def test_stops_between_icons_once_a_sync_starts(self):
        bound = {1000 + n: n for n in range(10)}
        job = _Job(icons=dict.fromkeys(bound, ""), bound=bound)

        def start_a_sync(_rom_id: int) -> None:
            job.sync_running = True

        job.source.on_fetch = start_a_sync

        await job.run()

        assert len(job.source.fetched) <= 4
        assert job.sink.events == []

    @pytest.mark.asyncio
    async def test_does_nothing_where_steam_has_no_grid_directory(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.steam_config.grid_missing = True

        await job.run()

        assert job.source.fetched == []

    @pytest.mark.asyncio
    async def test_does_nothing_when_the_shortcut_file_cannot_be_read(self):
        job = _Job(icons=None, bound={100: 1})

        await job.run()

        assert job.source.fetched == []

    @pytest.mark.asyncio
    async def test_after_a_sync_waits_for_steam_to_rewrite_its_file_first(self):
        job = _Job(icons={}, bound={})

        await job.run(settle=True)

        assert job.sleeper.calls == [10.0]

    @pytest.mark.asyncio
    async def test_at_start_up_waits_until_a_panel_is_connected(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.connected = False
        polls = 0

        async def connect_on_the_third_poll(seconds: float) -> None:
            nonlocal polls
            job.sleeper.calls.append(seconds)
            polls += 1
            if polls == 3:
                job.connected = True

        job.sleeper.sleep = connect_on_the_third_poll  # type: ignore[method-assign]

        await job.service.run_when_a_panel_connects()
        task = job.service._task
        assert task is not None
        await task

        assert job.sleeper.calls == [5.0, 5.0, 5.0]
        assert job.delivered() == {100: "/grid/100_icon.png"}


class TestRateLimits:
    @pytest.mark.asyncio
    async def test_a_429_pauses_then_asks_again(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.answer(1, IconFetch(IconAnswer.RATE_LIMITED), IconFetch(IconAnswer.ICON, b"icon"))

        await job.run()

        assert job.sleeper.calls == [30.0]
        assert job.delivered() == {100: "/grid/100_icon.png"}

    @pytest.mark.asyncio
    async def test_each_429_in_a_row_doubles_the_pause_up_to_15_minutes(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        limited = IconFetch(IconAnswer.RATE_LIMITED)
        job.source.answer(1, *([limited] * 7), IconFetch(IconAnswer.ICON, b"icon"))

        await job.run()

        assert job.sleeper.calls == [30.0, 60.0, 120.0, 240.0, 480.0, 900.0, 900.0]

    @pytest.mark.asyncio
    async def test_an_answer_resets_the_pause(self):
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})
        limited = IconFetch(IconAnswer.RATE_LIMITED)
        job.source.answer(1, limited, IconFetch(IconAnswer.ICON, b"one"))
        job.source.answer(2, limited, IconFetch(IconAnswer.ICON, b"two"))
        job.service._backoff = 0.0

        await job.run()

        assert job.sleeper.calls[0] == 30.0
        assert set(job.delivered()) == {100, 200}


class TestACleanup:
    @pytest.mark.asyncio
    async def test_a_cleanup_already_running_stops_the_job_before_it_writes(self):
        prune_conflicts = _make_prune_conflicts()
        prune_conflicts.register_run("cleanup")
        job = _Job(icons={100: ""}, bound={100: 1}, prune_conflicts=prune_conflicts)

        await job.run()

        assert job.source.written == {}
        assert job.sink.events == []

    @pytest.mark.asyncio
    async def test_stopping_for_a_cleanup_waits_for_an_icon_being_written(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.write_gate = asyncio.Event()
        job.service.request_run()
        while not job.service._writing:
            await asyncio.sleep(0)

        stopping = asyncio.ensure_future(job.service.stop_for_cleanup())
        await asyncio.sleep(0.01)
        assert not stopping.done()

        job.source.write_gate.set()
        await stopping

        assert job.source.written == {100: b"icon"}
        assert job.sink.events == []
        assert job.prune_conflicts.cleanup_running is False
        assert await job.prune_conflicts.reserve_start("start_prune") is None

    @pytest.mark.asyncio
    async def test_after_a_stop_the_next_request_runs_again(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        await job.service.stop_for_cleanup()

        await job.run()

        assert job.delivered() == {100: "/grid/100_icon.png"}


class TestRequests:
    @pytest.mark.asyncio
    async def test_a_request_during_a_run_runs_it_once_more_afterwards(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.write_gate = asyncio.Event()
        job.service.request_run()
        while not job.service._writing:
            await asyncio.sleep(0)

        job.service.request_run()
        job.service.request_run()
        job.source.write_gate.set()
        task = job.service._task
        assert task is not None
        await task

        assert job.source.fetched == [1, 1]

    @pytest.mark.asyncio
    async def test_after_shutdown_nothing_starts_a_run(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        await job.service.shutdown()

        job.service.request_run()

        assert job.service._task is None

    @pytest.mark.asyncio
    async def test_an_unexpected_error_ends_the_run_with_a_log_line(self, caplog):
        job = _Job(icons={100: ""}, bound={100: 1})

        def broken(_rom_id: int) -> IconFetch:
            raise RuntimeError("boom")

        job.source.fetch_shortcut_icon_io = broken  # type: ignore[method-assign]

        with caplog.at_level(logging.ERROR, logger="test_shortcut_icons"):
            await job.run()

        assert "unexpected error" in caplog.text


class TestThePlaceholderPath:
    @pytest.mark.asyncio
    async def test_writes_the_placeholder_and_answers_its_path(self):
        job = _Job(icons={}, bound={})

        assert await job.service.placeholder_path() == _PLACEHOLDER
        assert job.steam_config.ensured == {PLACEHOLDER_ICON_NAME: PLACEHOLDER_ICON_PNG}

    @pytest.mark.asyncio
    async def test_answers_none_where_steam_has_no_grid_directory(self):
        job = _Job(icons={}, bound={})
        job.steam_config.grid_missing = True

        assert await job.service.placeholder_path() is None


class TestMoreThatStopsARun:
    @pytest.mark.asyncio
    async def test_tenders_icons_that_cannot_be_written_stop_the_run_before_any_fetch(self):
        job = _Job(icons={100: ""}, bound={100: 1})

        def full_disk(_name: str, _content: bytes) -> str:
            raise OSError("No space left on device")

        job.steam_config.ensure_grid_file = full_disk  # type: ignore[method-assign]

        await job.run()

        assert job.source.fetched == []

    @pytest.mark.asyncio
    async def test_a_cleanup_that_starts_before_a_batch_is_handed_over_keeps_it_back(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        write = job.source.write_job_icon

        async def write_then_a_cleanup_starts(app_id: int, icon_bytes: bytes, generation: int) -> str | None:
            path = await write(app_id, icon_bytes, generation)
            job.prune_conflicts.register_run("cleanup")
            return path

        job.source.write_job_icon = write_then_a_cleanup_starts  # type: ignore[method-assign]

        await job.run()

        assert job.source.written == {100: b"icon"}
        assert job.sink.events == []


class TestTwo429sAtOnce:
    @pytest.mark.asyncio
    async def test_pause_every_worker_once(self):
        job = _Job(icons={}, bound={})
        gate = asyncio.Event()

        async def pause_until_released(seconds: float) -> None:
            job.sleeper.calls.append(seconds)
            await gate.wait()

        job.sleeper.sleep = pause_until_released  # type: ignore[method-assign]

        first = asyncio.ensure_future(job.service._wait_out_rate_limit())
        await asyncio.sleep(0)
        second = asyncio.ensure_future(job.service._wait_out_rate_limit())
        await asyncio.sleep(0)
        assert not second.done()

        gate.set()
        await asyncio.gather(first, second)

        assert job.sleeper.calls == [30.0]
