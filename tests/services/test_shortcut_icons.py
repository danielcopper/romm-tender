"""Tests for services/shortcut_icons.py — the background job that gives waiting shortcuts their icons."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from _factories import _make_conflict_rules, _make_prune_conflicts
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.system_time import FakeSleeper

from domain.rom import Rom
from domain.shortcut_icon import LOGO_ICON_NAME, PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG
from lib.errors import SteamGridDirMissingError
from models.shortcut_icon import IconAnswer, IconFetch
from services import shortcut_icons
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
    """Answers fetches from a script per ROM; records what the job wrote, and how many fetches ran at once.

    A fetch runs on an executor thread, as the real one does; ``fetch_gate``
    holds every fetch there until it is set.
    """

    def __init__(self) -> None:
        self.answers: dict[int, list[IconFetch]] = {}
        self.generations: dict[int, int] = {}
        self.fetched: list[int] = []
        self.written: dict[int, bytes] = {}
        self.write_gate: asyncio.Event | None = None
        self.fetch_gate: threading.Event | None = None
        self.on_fetch: Any = None
        self.in_flight = 0
        self.peak_in_flight = 0
        self._count = threading.Lock()

    def answer(self, rom_id: int, *fetches: IconFetch) -> None:
        self.answers[rom_id] = list(fetches)

    def icon_generation(self, app_id: int) -> int:
        return self.generations.get(app_id, 0)

    def do_fetch_shortcut_icon(self, rom_id: int) -> IconFetch:
        with self._count:
            self.fetched.append(rom_id)
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.on_fetch is not None:
                self.on_fetch(rom_id)
            if self.fetch_gate is not None:
                self.fetch_gate.wait(timeout=5)
            script = self.answers.get(rom_id) or [IconFetch(IconAnswer.ICON, b"icon")]
            return script.pop(0) if len(script) > 1 else script[0]
        finally:
            with self._count:
                self.in_flight -= 1

    async def write_job_icon(self, app_id: int, icon_bytes: bytes, generation: int) -> str | None:
        if self.write_gate is not None:
            await self.write_gate.wait()
        if self.icon_generation(app_id) != generation:
            return None
        self.written[app_id] = icon_bytes
        return f"/grid/{app_id}_icon.png"


class _RecordErrors(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.records.append(record)


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
        self.removal_running = False
        self.error_handler = _RecordErrors()
        logger = logging.getLogger(f"test_shortcut_icons.{id(self)}")
        logger.addHandler(self.error_handler)
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
                removal_in_flight=lambda: self.removal_running,
                panel_connected=lambda: self.connected,
                tender_logo=lambda: logo,
                loop=asyncio.get_running_loop(),
                logger=logger,
                log_debug=lambda msg: None,
            )
        )

    async def run(self, expect_error: bool = False, **options: Any) -> None:
        self.service.request_run(**options)
        task = self.service._task
        assert task is not None
        await task
        if not expect_error:
            unexpected = [r.getMessage() for r in self.error_handler.records if "unexpected error" in r.getMessage()]
            assert not unexpected, f"Unexpected error in shortcut icons run: {unexpected}"

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

        def pick_while_fetching(rom_id: int) -> None:
            job.source.generations[100 * rom_id] = 1

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
    async def test_an_answer_between_two_429s_starts_the_pause_over(self, monkeypatch):
        monkeypatch.setattr(shortcut_icons, "_WORKERS", 1)
        job = _Job(icons={100: "", 200: "", 300: ""}, bound={100: 1, 200: 2, 300: 3})
        limited = IconFetch(IconAnswer.RATE_LIMITED)
        job.source.answer(1, limited, IconFetch(IconAnswer.ICON, b"one"))
        job.source.answer(3, limited, IconFetch(IconAnswer.ICON, b"three"))

        await job.run()

        assert job.source.fetched == [1, 2, 3, 1, 3]
        assert job.sleeper.calls == [30.0, 30.0]
        assert set(job.delivered()) == {100, 200, 300}

    @pytest.mark.asyncio
    async def test_a_run_starts_its_pause_over_whatever_the_last_one_reached(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.answer(1, IconFetch(IconAnswer.RATE_LIMITED), IconFetch(IconAnswer.ICON, b"icon"))
        job.service._backoff = 480.0

        await job.run()

        assert job.sleeper.calls == [30.0]

    @pytest.mark.asyncio
    async def test_a_429_near_the_end_of_the_worklist_still_hands_every_icon_over(self):
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})
        job.source.answer(1, IconFetch(IconAnswer.RATE_LIMITED), IconFetch(IconAnswer.ICON, b"icon"))

        async def a_pause_that_yields(seconds: float) -> None:
            job.sleeper.calls.append(seconds)
            await asyncio.sleep(0.05)

        job.sleeper.sleep = a_pause_that_yields  # type: ignore[method-assign]

        await job.run()

        assert job.delivered() == {100: "/grid/100_icon.png", 200: "/grid/200_icon.png"}

    @pytest.mark.asyncio
    async def test_a_sync_that_starts_during_a_pause_keeps_every_worker_from_fetching_after_it(self):
        job = _Job(icons={100: "", 200: "", 300: ""}, bound={100: 1, 200: 2, 300: 3})
        job.source.answer(1, IconFetch(IconAnswer.RATE_LIMITED), IconFetch(IconAnswer.ICON, b"icon"))
        pause = asyncio.Event()

        async def a_pause_held_by_the_test(seconds: float) -> None:
            job.sleeper.calls.append(seconds)
            await pause.wait()

        job.sleeper.sleep = a_pause_held_by_the_test  # type: ignore[method-assign]
        job.service.request_run()
        # How many of the other icons are fetched before the 429's pause begins depends on thread timing; what is
        # asserted is that none is fetched after it.
        for _ in range(500):
            if job.sleeper.calls:
                break
            await asyncio.sleep(0.01)
        assert job.sleeper.calls == [30.0]
        await asyncio.sleep(0.05)

        job.sync_running = True
        fetched_before_the_pause_ended = list(job.source.fetched)
        pause.set()
        task = job.service._task
        assert task is not None
        await task

        assert job.source.fetched == fetched_before_the_pause_ended

    @pytest.mark.asyncio
    async def test_no_worker_takes_another_icon_while_a_pause_is_in_force(self):
        bound = {1000 + n: n for n in range(8)}
        job = _Job(icons=dict.fromkeys(bound, ""), bound=bound)
        pause = asyncio.Event()
        pause_began = threading.Event()
        four_started = threading.Event()
        started: list[int] = []
        lock = threading.Lock()
        fetch = job.source.do_fetch_shortcut_icon

        def the_first_fetch_is_answered_429_once_four_are_in_flight(rom_id: int) -> IconFetch:
            with lock:
                started.append(rom_id)
                first = len(started) == 1
                if len(started) == 4:
                    four_started.set()
            if first:
                four_started.wait(timeout=5)
                return IconFetch(IconAnswer.RATE_LIMITED)
            pause_began.wait(timeout=5)
            return fetch(rom_id)

        async def a_pause_held_by_the_test(seconds: float) -> None:
            job.sleeper.calls.append(seconds)
            pause_began.set()
            await pause.wait()

        job.source.do_fetch_shortcut_icon = the_first_fetch_is_answered_429_once_four_are_in_flight  # type: ignore[method-assign]
        job.sleeper.sleep = a_pause_held_by_the_test  # type: ignore[method-assign]
        job.service.request_run()
        for _ in range(500):
            if job.sleeper.calls:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        started_during_the_pause = len(started)

        pause.set()
        task = job.service._task
        assert task is not None
        await task

        assert job.sleeper.calls == [30.0]
        assert started_during_the_pause == 4
        assert len(job.delivered()) == 8


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
    async def test_runs_again_once_the_cleanup_resumes_it(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        await job.service.stop_for_cleanup()

        job.service.resume_after_cleanup()
        task = job.service._task
        assert task is not None
        await task

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
    async def test_shutdown_stops_a_run_under_way(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        pause = asyncio.Event()

        def slow_fetch(_rom_id: int) -> IconFetch:
            pause.set()
            return IconFetch(IconAnswer.ICON, b"icon")

        job.source.do_fetch_shortcut_icon = slow_fetch  # type: ignore[method-assign]
        job.service.request_run()
        await pause.wait()
        await job.service.shutdown()

        assert job.service._closed is True
        assert job.service._stopped is True
        assert job.delivered() == {}

    @pytest.mark.asyncio
    async def test_key_refused_resets_on_next_run(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.answer(1, IconFetch(IconAnswer.KEY_REFUSED))
        await job.run()
        assert job.service._key_refused is True

        job.source.answer(1, IconFetch(IconAnswer.ICON, b"icon"))
        await job.run()
        assert job.service._key_refused is False
        assert job.delivered() == {100: "/grid/100_icon.png"}

    @pytest.mark.asyncio
    async def test_settle_resets_after_sleeping(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        await job.run(settle=True)
        assert job.service._settle is False
        assert job.sleeper.calls == [10.0]

    @pytest.mark.asyncio
    async def test_an_unexpected_error_ends_the_run_with_a_log_line(self, caplog):
        job = _Job(icons={100: ""}, bound={100: 1})

        def broken(_rom_id: int) -> IconFetch:
            raise RuntimeError("boom")

        job.source.do_fetch_shortcut_icon = broken  # type: ignore[method-assign]

        await job.run(expect_error=True)

        assert "unexpected error" in caplog.text


class TestThePlaceholderPath:
    @pytest.mark.asyncio
    async def test_writes_the_placeholder_and_answers_its_path(self):
        job = _Job(icons={}, bound={})

        assert await job.service.placeholder_path() == _PLACEHOLDER
        assert job.steam_config.ensured == {PLACEHOLDER_ICON_NAME: PLACEHOLDER_ICON_PNG}

    @pytest.mark.asyncio
    async def test_without_an_api_key_answers_none_and_writes_nothing(self):
        job = _Job(icons={}, bound={}, key="")

        assert await job.service.placeholder_path() is None
        assert job.steam_config.ensured == {}

    @pytest.mark.asyncio
    async def test_answers_none_where_steam_has_no_grid_directory(self):
        job = _Job(icons={}, bound={})
        job.steam_config.grid_missing = True

        assert await job.service.placeholder_path() is None

    @pytest.mark.asyncio
    async def test_answers_none_where_the_grid_directory_cannot_be_written(self):
        job = _Job(icons={}, bound={})

        def full_disk(_name: str, _content: bytes) -> str:
            raise OSError("No space left on device")

        job.steam_config.ensure_grid_file = full_disk  # type: ignore[method-assign]

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


class TestFourAtATime:
    @pytest.mark.asyncio
    async def test_runs_exactly_four_fetches_at_once(self):
        bound = {1000 + n: n for n in range(10)}
        job = _Job(icons=dict.fromkeys(bound, ""), bound=bound)
        job.source.fetch_gate = threading.Event()
        job.service.request_run()
        for _ in range(200):
            if job.source.in_flight >= 4:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        peak_while_held = job.source.peak_in_flight

        job.source.fetch_gate.set()
        task = job.service._task
        assert task is not None
        await task

        assert peak_while_held == 4
        assert job.source.peak_in_flight == 4
        assert len(job.delivered()) == 10


class TestWhatIsWrittenIsNotStranded:
    @pytest.mark.asyncio
    async def test_an_unexpected_error_still_hands_over_what_was_written_before_it(self, monkeypatch):
        monkeypatch.setattr(shortcut_icons, "_WORKERS", 1)
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})

        def the_second_fetch_breaks(rom_id: int) -> None:
            if rom_id == 2:
                raise RuntimeError("boom")

        job.source.on_fetch = the_second_fetch_breaks

        await job.run(expect_error=True)

        assert job.delivered() == {100: "/grid/100_icon.png"}

    @pytest.mark.asyncio
    async def test_write_unless_yields_ignores_data_when_answer_not_icon(self):
        job = _Job(icons={}, bound={})
        plan = shortcut_icons._Plan(logo_path="/grid/logo.png", worklist=[])
        fetch = IconFetch(IconAnswer.NO_ICON, b"ignored")
        res = await job.service._write_unless_the_job_yields(100, fetch, 0, plan)
        assert res == "/grid/logo.png"
        assert job.source.written == {}

    @pytest.mark.asyncio
    async def test_write_unless_yields_suppresses_logo_when_generation_bumps(self):
        job = _Job(icons={}, bound={})
        plan = shortcut_icons._Plan(logo_path="/grid/logo.png", worklist=[])
        fetch = IconFetch(IconAnswer.NO_ICON, None)
        job.source.generations[100] = 1
        res = await job.service._write_unless_the_job_yields(100, fetch, 0, plan)
        assert res is None

    @pytest.mark.asyncio
    async def test_an_unexpected_error_leaves_no_worker_running(self):
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})

        def fail_first(rom_id: int) -> None:
            if rom_id == 1:
                raise RuntimeError("boom")

        job.source.on_fetch = fail_first

        async def pause(_sec: float) -> None:
            await asyncio.sleep(10)

        job.sleeper.sleep = pause  # type: ignore[method-assign]
        job.source.answer(2, IconFetch(IconAnswer.RATE_LIMITED))

        await job.run(expect_error=True)
        await asyncio.sleep(0)

        running = [
            t for t in asyncio.all_tasks() if getattr(t.get_coro(), "__name__", None) == "_work" and not t.done()
        ]
        for t in running:
            t.cancel()
        assert running == []


class TestStoppingAndResuming:
    @pytest.mark.asyncio
    async def test_a_request_made_while_a_stopped_run_winds_down_runs_it_again(self):
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})
        second_fetch = threading.Event()
        cleanup_seen = False

        def a_cleanup_starts_during_the_first_fetch(rom_id: int) -> None:
            nonlocal cleanup_seen
            if rom_id == 1 and not cleanup_seen:
                cleanup_seen = True
                job.prune_conflicts.register_run("cleanup")
            if rom_id == 2:
                second_fetch.wait(timeout=5)

        job.source.on_fetch = a_cleanup_starts_during_the_first_fetch
        job.service.request_run()
        while not job.service._stopped:
            await asyncio.sleep(0.01)

        job.prune_conflicts.release_run("cleanup")
        job.service.request_run()
        second_fetch.set()
        task = job.service._task
        assert task is not None
        await task

        assert job.delivered() == {100: "/grid/100_icon.png", 200: "/grid/200_icon.png"}

    @pytest.mark.asyncio
    async def test_no_run_starts_between_a_cleanup_stopping_the_job_and_resuming_it(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.write_gate = asyncio.Event()
        job.service.request_run()
        while not job.service._writing:
            await asyncio.sleep(0)
        first = job.service._task
        assert first is not None

        stopping = asyncio.ensure_future(job.service.stop_for_cleanup())
        while not first.done():
            await asyncio.sleep(0)
        job.service.request_run()
        assert job.service._task is first

        job.source.write_gate.set()
        await stopping
        assert job.sink.events == []
        job.service.resume_after_cleanup()
        resumed = job.service._task
        assert resumed is not None
        assert resumed is not first
        await resumed

        assert job.delivered() == {100: "/grid/100_icon.png"}

    @pytest.mark.asyncio
    async def test_two_cleanup_starts_hold_it_until_both_resume_it(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        await job.service.stop_for_cleanup()
        await job.service.stop_for_cleanup()

        job.service.resume_after_cleanup()
        started_after_one_resume = job.service._task is not None

        job.service.resume_after_cleanup()
        task = job.service._task
        assert task is not None
        await task
        assert started_after_one_resume is False
        assert job.delivered() == {100: "/grid/100_icon.png"}

    @pytest.mark.asyncio
    async def test_a_run_that_met_a_cleanup_ends_there_even_once_the_cleanup_is_over(self, monkeypatch):
        monkeypatch.setattr(shortcut_icons, "_WORKERS", 1)
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})
        armed = False
        answered = 0

        def a_cleanup_seen_once() -> bool:
            nonlocal answered
            if armed and not answered:
                answered += 1
                return True
            return False

        def arm(_rom_id: int) -> None:
            nonlocal armed
            armed = True

        job.service._cleanup_running = a_cleanup_seen_once  # type: ignore[method-assign]
        job.source.on_fetch = arm

        await job.run()

        assert job.source.fetched == [1]
        assert job.source.written == {}
        assert job.sink.events == []

    @pytest.mark.asyncio
    async def test_a_caller_cancelled_while_it_waits_for_the_stop_is_cancelled(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.fetch_gate = threading.Event()
        job.service.request_run()
        while not job.source.fetched:
            await asyncio.sleep(0.01)

        stopping = asyncio.ensure_future(job.service.stop_for_cleanup())
        await asyncio.sleep(0)
        stopping.cancel()
        job.source.fetch_gate.set()

        with pytest.raises(asyncio.CancelledError):
            await stopping


class TestAShortcutRemoval:
    @pytest.mark.asyncio
    async def test_one_under_way_keeps_a_run_from_fetching(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.removal_running = True

        await job.run()

        assert job.source.fetched == []

    @pytest.mark.asyncio
    async def test_one_that_starts_during_a_fetch_keeps_the_icon_from_being_written(self):
        job = _Job(icons={100: ""}, bound={100: 1})

        def a_removal_starts(_rom_id: int) -> None:
            job.removal_running = True

        job.source.on_fetch = a_removal_starts

        await job.run()

        assert job.source.written == {}
        assert job.sink.events == []

    @pytest.mark.asyncio
    async def test_stopping_for_it_waits_for_an_icon_being_written_and_holds_nothing_off(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.source.write_gate = asyncio.Event()
        job.service.request_run()
        while not job.service._writing:
            await asyncio.sleep(0)

        stopping = asyncio.ensure_future(job.service.stop_for_removal())
        await asyncio.sleep(0.01)
        assert not stopping.done()
        job.source.write_gate.set()
        await stopping

        assert job.sink.events == []
        await job.run()
        assert job.delivered() == {100: "/grid/100_icon.png"}


class TestASync:
    @pytest.mark.asyncio
    async def test_one_that_starts_after_a_write_keeps_the_batch_back(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        write = job.source.write_job_icon

        async def write_then_a_sync_starts(app_id: int, icon_bytes: bytes, generation: int) -> str | None:
            path = await write(app_id, icon_bytes, generation)
            job.sync_running = True
            return path

        job.source.write_job_icon = write_then_a_sync_starts  # type: ignore[method-assign]

        await job.run()

        assert job.source.written == {100: b"icon"}
        assert job.sink.events == []


class TestARefusedKey:
    @pytest.mark.asyncio
    async def test_ends_the_run_with_one_warning(self, caplog):
        bound = {1000 + n: n for n in range(10)}
        job = _Job(icons=dict.fromkeys(bound, ""), bound=bound)
        for rom_id in bound.values():
            job.source.answer(rom_id, IconFetch(IconAnswer.KEY_REFUSED))

        with caplog.at_level(logging.WARNING, logger="test_shortcut_icons"):
            await job.run()

        assert len(job.source.fetched) <= 4
        assert caplog.text.count("refused the API key") == 1

    @pytest.mark.asyncio
    async def test_what_was_written_before_it_is_handed_over(self, monkeypatch):
        monkeypatch.setattr(shortcut_icons, "_WORKERS", 1)
        job = _Job(icons={100: "", 200: "", 300: ""}, bound={100: 1, 200: 2, 300: 3})
        job.source.answer(2, IconFetch(IconAnswer.KEY_REFUSED))

        await job.run()

        assert job.source.fetched == [1, 2]
        assert job.delivered() == {100: "/grid/100_icon.png"}

    @pytest.mark.asyncio
    async def test_no_other_worker_takes_a_cached_icon_after_it(self, monkeypatch):
        monkeypatch.setattr(shortcut_icons, "_WORKERS", 2)
        bound = {100 * n: n for n in range(1, 7)}
        job = _Job(icons=dict.fromkeys(bound, ""), bound=bound)
        job.source.answer(1, IconFetch(IconAnswer.KEY_REFUSED))

        def answer_once_the_key_is_refused(rom_id: int) -> None:
            if rom_id == 1:
                return
            for _ in range(5000):
                if job.service._key_refused:
                    return
                time.sleep(0.001)

        job.source.on_fetch = answer_once_the_key_is_refused

        await job.run()

        assert sorted(job.source.fetched) == [1, 2]


class TestABatchNobodyHeard:
    @pytest.mark.asyncio
    async def test_stops_the_run_and_runs_again_once_a_panel_connects(self, monkeypatch):
        monkeypatch.setattr(shortcut_icons, "_WORKERS", 1)
        monkeypatch.setattr(shortcut_icons, "_BATCH_SIZE", 1)
        job = _Job(icons={100: "", 200: ""}, bound={100: 1, 200: 2})
        job.sink.delivers = False
        job.connected = False

        async def a_panel_connects_while_it_waits(seconds: float) -> None:
            job.sleeper.calls.append(seconds)
            job.connected = True
            job.sink.delivers = True

        job.sleeper.sleep = a_panel_connects_while_it_waits  # type: ignore[method-assign]

        await job.run()
        waiting = job.service._waiting_for_a_panel
        assert waiting is not None
        await waiting
        task = job.service._task
        assert task is not None
        await task

        assert job.source.fetched == [1, 1, 2]
        assert job.delivered() == {100: "/grid/100_icon.png", 200: "/grid/200_icon.png"}
        assert job.prune_conflicts.held_claims().count("shortcut_icons") == 2

    @pytest.mark.asyncio
    async def test_shutdown_cancels_waiting_for_a_panel(self):
        job = _Job(icons={100: ""}, bound={100: 1})
        job.sleeper.sleep = lambda _sec: asyncio.sleep(10)  # type: ignore[method-assign]
        job.service._wait_for_a_panel()
        waiting = job.service._waiting_for_a_panel
        assert waiting is not None and not waiting.done()

        await job.service.shutdown()
        assert waiting.cancelling() or waiting.cancelled()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        assert waiting.cancelled()


class TestWhatTheRunLogs:
    @pytest.mark.asyncio
    async def test_how_many_bound_shortcuts_the_file_does_not_hold_yet(self, caplog):
        job = _Job(icons={100: ""}, bound={100: 1, 200: 2, 300: 3})

        with caplog.at_level(logging.INFO, logger="test_shortcut_icons"):
            await job.run()

        assert "shortcuts.vdf does not hold 2 bound shortcut(s) yet" in caplog.text
