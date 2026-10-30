"""The sync before a start tells the panel when it begins, and nothing else does.

The panel's Play reads "Syncing saves..." only from the ``pre_launch_sync_started``
event, so the event goes out once every reason not to sync has passed — save sync
on, Sync before launch on, a game whose saves can sync, RomM answering — and
before the sync's first request that names the device. Each absence case is
parametrized with its control: the same fixture with the one condition flipped
sends the event, so a test that would pass over an engine sending nothing at all
turns red instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from domain.save_answer import SaveAnswer
from lib.errors import RommConnectionError, ServerUnreachable
from services.saves._refusals import SaveShapeUnsupported
from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _install_rom,
    _seed_save_state_dict,
    _server_save,
    make_service,
)

if TYPE_CHECKING:
    from fakes.fake_save_api import FakeSaveApi
    from fakes.fake_save_location_reader import FakeSaveLocationReader

    from services.saves import SaveService

_STARTED = ("pre_launch_sync_started", {"rom_id": 42})


class _Heard:
    """An emitter that records every event, with the RomM calls made before it."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.calls_before: list[list[str]] = []
        self.fake: FakeSaveApi | None = None

    async def __call__(self, event: str, payload: object, /) -> bool:
        self.events.append((event, payload))
        assert self.fake is not None
        self.calls_before.append([call[0] for call in self.fake.call_log])
        return True

    def started(self) -> list[tuple[str, object]]:
        return [event for event in self.events if event[0] == _STARTED[0]]


def _service(tmp_path) -> tuple[SaveService, FakeSaveApi, _Heard]:
    """A ROM whose saves sync, with save sync on, a device bound and a save on RomM."""
    heard = _Heard()
    svc, fake = make_service(tmp_path, emit=heard)
    heard.fake = fake
    _enable_sync_with_device(svc)
    _install_rom(svc, tmp_path)
    _create_save(tmp_path)
    _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True})
    fake.saves[100] = _server_save()
    return svc, fake, heard


def _refusing() -> SaveAnswer:
    """A save shared by many games, which a sync refuses as a skip."""
    return SaveAnswer(
        state="shared",
        unestablished=None,
        emulator="PCSX2 (Standalone)",
        directory="/saves/ps2/pcsx2/memcards",
        backing_directory=None,
        granularity="shared-card",
        needs=(),
        components=(),
        caveats=(),
        content_installed=True,
    )


class TestASyncThatProceedsIsAnnounced:
    @pytest.mark.asyncio
    async def test_the_event_names_the_rom_once_after_romm_answered_and_before_any_request_naming_the_device(
        self, tmp_path
    ):
        svc, fake, heard = _service(tmp_path)

        result = await svc.pre_launch_sync(42)

        assert result["success"] is True
        assert heard.started() == [_STARTED]
        assert heard.calls_before == [["heartbeat"]]
        assert any(call[0] == "list_saves" for call in fake.call_log)


class TestNoEventWhereNoSyncRuns:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("enabled", [False, True], ids=["save-sync-off", "control-on"])
    async def test_save_sync_switched_off(self, tmp_path, enabled: bool):
        svc, _fake, heard = _service(tmp_path)
        svc._config.settings["save_sync_enabled"] = enabled

        await svc.pre_launch_sync(42)

        assert heard.started() == ([_STARTED] if enabled else [])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("before_launch", [False, True], ids=["sync-before-launch-off", "control-on"])
    async def test_sync_before_launch_switched_off(self, tmp_path, before_launch: bool):
        svc, _fake, heard = _service(tmp_path)
        svc._config.settings["sync_before_launch"] = before_launch

        await svc.pre_launch_sync(42)

        assert heard.started() == ([_STARTED] if before_launch else [])

    @pytest.mark.asyncio
    async def test_a_game_whose_saves_cannot_sync(self, tmp_path):
        svc, _fake, heard = _service(tmp_path)
        cast("FakeSaveLocationReader", svc._rom_info._save_locations).answer_with("gba", _refusing())
        sync = svc.pre_launch_sync(42)

        with pytest.raises(SaveShapeUnsupported):
            await sync

        assert heard.started() == []

    @pytest.mark.asyncio
    async def test_the_control_a_game_whose_saves_sync(self, tmp_path):
        svc, _fake, heard = _service(tmp_path)

        await svc.pre_launch_sync(42)

        assert heard.started() == [_STARTED]

    @pytest.mark.asyncio
    async def test_romm_not_answering(self, tmp_path):
        svc, fake, heard = _service(tmp_path)
        fake.heartbeat_raises = RommConnectionError("Connection refused")
        sync = svc.pre_launch_sync(42)

        with pytest.raises(ServerUnreachable):
            await sync

        assert heard.started() == []

    @pytest.mark.asyncio
    async def test_the_control_romm_answering(self, tmp_path):
        svc, fake, heard = _service(tmp_path)
        fake.heartbeat_raises = None

        await svc.pre_launch_sync(42)

        assert heard.started() == [_STARTED]


class TestOnlyTheSyncBeforeAStartSendsIt:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("entry", "args"),
        [("sync_rom_saves", (42,)), ("sync_all_saves", ()), ("post_exit_sync", (42,))],
        ids=["sync-saves", "sync-all-saves", "after-a-game"],
    )
    async def test_another_sync_that_runs_sends_nothing(self, tmp_path, entry: str, args: tuple[Any, ...]):
        svc, fake, heard = _service(tmp_path)

        await getattr(svc, entry)(*args)

        assert any(call[0] == "list_saves" for call in fake.call_log), "the sync must have run"
        assert heard.started() == []

    @pytest.mark.asyncio
    async def test_the_control_the_sync_before_a_start_on_the_same_game(self, tmp_path):
        svc, fake, heard = _service(tmp_path)

        await svc.pre_launch_sync(42)

        assert any(call[0] == "list_saves" for call in fake.call_log)
        assert heard.started() == [_STARTED]
