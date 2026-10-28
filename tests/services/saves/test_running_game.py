"""No save sync changes the save of a game that is running (#2620).

Steam's running apps, matched to ROMs, are faked by ``FakeRunningRomIds``; what
is under test is what each save path does with the answer: the sweep and a
platform's delete pass the game over and name it, every press on the game's
page is refused, a start stops another sync of its game, and the follow of a
moved save directory does nothing — except in the syncs before a start and
after a game.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from fakes.fake_running_rom_ids import FakeRunningRomIds

from domain.answered_save_directory import AnsweredSaveDirectory
from domain.rom_save_sync_state import RomSaveSyncState
from domain.save_answer import SaveAnswer, SaveComponent
from lib.errors import Refused
from services.saves._refusals import GameRunning
from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _install_rom,
    _seed_save_state,
    _server_save,
    _uow,
    make_service,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fakes.fake_save_api import FakeSaveApi
    from fakes.fake_save_location_reader import FakeSaveLocationReader

    from services.saves import SaveService

_ROM = 42
_RUNNING_MESSAGE = "rom-42 is running. Quit it first."
_TRANSFERS = ("list_saves", "download_save_content", "upload_save", "delete_server_saves", "confirm_download")


def _service(tmp_path, *running: int) -> tuple[SaveService, FakeSaveApi, FakeRunningRomIds]:
    games = FakeRunningRomIds(running)
    svc, fake = make_service(tmp_path, running_rom_ids=games)
    _enable_sync_with_device(svc, "test-device")
    return svc, fake, games


def _confirmed_rom(svc, tmp_path, rom_id: int, *, content: bytes | None = b"local progress") -> None:
    """ROM *rom_id* on gba, its slot ``default`` confirmed beside a slot ``other``, with a local save."""
    _install_rom(svc, tmp_path, rom_id=rom_id, file_name=f"game{rom_id}.gba")
    state = RomSaveSyncState(
        system="gba",
        slot_confirmed=True,
        active_slot="default",
        slots={"default": {"source": "server"}, "other": {"source": "server"}},
    )
    _seed_save_state(svc, rom_id, state)
    if content is not None:
        _create_save(tmp_path, rom_name=f"game{rom_id}", content=content)


def _transfers_of(fake: FakeSaveApi, rom_id: int) -> list[str]:
    """The RomM calls that move or name a save of *rom_id*."""
    by_save = {save_id for save_id, save in fake.saves.items() if save.get("rom_id") == rom_id}
    found = []
    for name, args, _kwargs in fake.call_log:
        if (
            (name == "list_saves" and args[0] == rom_id)
            or (name in ("download_save_content", "confirm_download") and args[0] in by_save)
            or (name == "upload_save" and args[0] == rom_id)
        ):
            found.append(name)
    return found


async def _outcome(call: Awaitable[Any]) -> Any:
    """*call*'s answer, or the refusal it raised."""
    try:
        return await call
    except Refused as refusal:
        return refusal


class TestTheSweepPassesARunningGameOver:
    """D2: Sync All Saves Now skips a game that runs when it reaches it, syncs the rest, and names it."""

    @pytest.mark.asyncio
    async def test_a_running_game_s_saves_are_untouched_and_the_answer_names_it(self, tmp_path):
        svc, fake, _games = _service(tmp_path, 1)
        _confirmed_rom(svc, tmp_path, 1)
        _confirmed_rom(svc, tmp_path, 2, content=None)
        fake.saves[100] = _server_save(save_id=100, rom_id=1, filename="game1.srm", slot="default")
        fake.saves[200] = _server_save(save_id=200, rom_id=2, filename="game2.srm", slot="default")

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert (tmp_path / "saves" / "gba" / "game1.srm").read_bytes() == b"local progress"
        assert not list((tmp_path / "saves" / "gba").glob("game1.srm.*"))
        assert _transfers_of(fake, 1) == []
        assert (tmp_path / "saves" / "gba" / "game2.srm").exists()
        assert result["success"] is True
        assert result["synced"] == 1
        assert result["skipped_running"] == [{"rom_id": 1, "name": "rom-1"}]
        assert result["message"] == "Synced 1 save(s) across 2 ROM(s); rom-1 was skipped because it is running"

    @pytest.mark.asyncio
    async def test_it_asks_when_it_reaches_the_game_not_when_the_sweep_starts(self, tmp_path):
        svc, fake, games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1, content=None)
        _confirmed_rom(svc, tmp_path, 2, content=None)
        fake.saves[100] = _server_save(save_id=100, rom_id=1, filename="game1.srm", slot="default")
        fake.saves[200] = _server_save(save_id=200, rom_id=2, filename="game2.srm", slot="default")
        list_saves = fake.list_saves

        def start_game_2_once_game_1_is_listed(rom_id, **kwargs):
            if rom_id == 1:
                games.running.add(2)
            return list_saves(rom_id, **kwargs)

        fake.list_saves = start_game_2_once_game_1_is_listed  # type: ignore[method-assign]

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert (tmp_path / "saves" / "gba" / "game1.srm").exists()
        assert not (tmp_path / "saves" / "gba" / "game2.srm").exists()
        assert result["skipped_running"] == [{"rom_id": 2, "name": "rom-2"}]

    @pytest.mark.asyncio
    async def test_it_reads_steam_once_for_each_rom_it_syncs(self, tmp_path):
        svc, _fake, games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1)
        _confirmed_rom(svc, tmp_path, 2)

        await svc.sync_all_saves()

        assert games.readings == 2

    @pytest.mark.asyncio
    async def test_two_running_games_are_named_in_one_sentence(self, tmp_path):
        svc, _fake, _games = _service(tmp_path, 1, 2)
        _confirmed_rom(svc, tmp_path, 1)
        _confirmed_rom(svc, tmp_path, 2)

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert result["message"].endswith("; rom-1 and rom-2 were skipped because they are running")

    @pytest.mark.asyncio
    async def test_a_running_game_the_sweep_would_not_sync_anyway_is_not_named(self, tmp_path):
        svc, _fake, _games = _service(tmp_path, 1)
        _install_rom(svc, tmp_path, rom_id=1, file_name="game1.gba")

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert result["skipped_running"] == []
        assert "running" not in result["message"]


def _presses() -> list[Any]:
    return [
        pytest.param(lambda svc: svc.sync_rom_saves(_ROM), {"synced": 0}, id="sync-saves"),
        pytest.param(lambda svc: svc.switch_slot(_ROM, "other"), {}, id="slot-switch"),
        pytest.param(lambda svc: svc.copy_save_to_slot(_ROM, 100, "other"), {}, id="copy-to-slot"),
        pytest.param(lambda svc: svc.rollback_to_version(_ROM, "default", 100), {}, id="version-restore"),
        pytest.param(lambda svc: svc.delete_slot(_ROM, "other"), {}, id="slot-delete"),
        pytest.param(lambda svc: svc.delete_local_saves(_ROM), {}, id="delete-local-saves"),
        pytest.param(
            lambda svc: svc.resolve_sync_conflict(_ROM, "game42.srm", 100, "use_server"), {}, id="resolve-conflict"
        ),
        pytest.param(lambda svc: svc.confirm_slot_choice(_ROM, "default", True, None), {}, id="wizard-migration"),
    ]


class TestEveryPressOnARunningGamesPageIsRefused:
    """D3: each press that would change a running game's saves is refused, naming it; it works once the game quit."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("press", "details"), _presses())
    async def test_it_is_refused_with_its_own_reason_and_changes_nothing(
        self, tmp_path, press: Callable[[SaveService], Awaitable[Any]], details: dict[str, Any]
    ):
        svc, fake, _games = _service(tmp_path, _ROM)
        _confirmed_rom(svc, tmp_path, _ROM)
        fake.saves[100] = _server_save(save_id=100, rom_id=_ROM, filename="game42.srm", slot="default")

        with pytest.raises(GameRunning) as refused:
            await press(svc)

        assert refused.value.reason == "game_running"
        assert refused.value.message == _RUNNING_MESSAGE
        assert refused.value.details == details
        assert (tmp_path / "saves" / "gba" / "game42.srm").read_bytes() == b"local progress"
        assert not any(name in _TRANSFERS for name, _args, _kwargs in fake.call_log)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("press", "details"), _presses())
    async def test_it_is_not_refused_for_a_game_that_is_not_running(
        self, tmp_path, press: Callable[[SaveService], Awaitable[Any]], details: dict[str, Any]
    ):
        del details
        svc, fake, games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, _ROM)
        fake.saves[100] = _server_save(save_id=100, rom_id=_ROM, filename="game42.srm", slot="default")

        outcome = await _outcome(press(svc))

        assert not isinstance(outcome, GameRunning)
        assert games.readings >= 1

    @pytest.mark.asyncio
    async def test_a_conflict_the_launch_check_resolves_before_the_start_is_not_refused(self, tmp_path):
        svc, fake, _games = _service(tmp_path, _ROM)
        _confirmed_rom(svc, tmp_path, _ROM)
        fake.saves[100] = _server_save(save_id=100, rom_id=_ROM, filename="game42.srm", slot="default")

        outcome = await _outcome(svc.resolve_sync_conflict(_ROM, "game42.srm", 100, "use_server", before_start=True))

        assert not isinstance(outcome, GameRunning)
        assert any(name == "list_saves" for name, _args, _kwargs in fake.call_log)

    @pytest.mark.asyncio
    async def test_confirming_a_slot_without_a_migration_writes_no_save_and_is_not_refused(self, tmp_path):
        svc, _fake, _games = _service(tmp_path, _ROM)
        _install_rom(svc, tmp_path)

        result = await svc.confirm_slot_choice(_ROM, "default", False, None)

        assert result["success"] is True


class TestAStartStopsTheOtherSyncsOfItsGame:
    """D4: Play Anyway's stop reaches a sweep's or a Sync Saves' sync of that game, and no other game's."""

    @pytest.mark.asyncio
    async def test_the_sweep_s_sync_of_the_game_stops_and_the_sweep_goes_on(self, tmp_path):
        svc, fake, _games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1, content=None)
        _confirmed_rom(svc, tmp_path, 2, content=None)
        fake.saves[100] = _server_save(save_id=100, rom_id=1, filename="game1.srm", slot="default")
        fake.saves[200] = _server_save(save_id=200, rom_id=2, filename="game2.srm", slot="default")
        list_saves = fake.list_saves

        def start_game_1_while_it_is_listed(rom_id, **kwargs):
            if rom_id == 1:
                svc.stop_pre_launch_sync(1)
            return list_saves(rom_id, **kwargs)

        fake.list_saves = start_game_1_while_it_is_listed  # type: ignore[method-assign]

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert not (tmp_path / "saves" / "gba" / "game1.srm").exists()
        assert "download_save_content" not in _transfers_of(fake, 1)
        assert (tmp_path / "saves" / "gba" / "game2.srm").exists()
        assert result["skipped_running"] == [{"rom_id": 1, "name": "rom-1"}]

    @pytest.mark.asyncio
    async def test_a_stop_before_the_sweep_follows_the_game_s_save_directory_follows_nothing(
        self, tmp_path, monkeypatch
    ):
        svc, _fake, _games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1, content=None)
        _confirmed_rom(svc, tmp_path, 2, content=None)
        engine = svc._sync_engine
        followed: list[int] = []
        follow = engine.follow_save_directory

        async def recording_follow(rom_id, answer, **kwargs):
            followed.append(rom_id)
            await follow(rom_id, answer, **kwargs)

        from services.saves.sync_engine import engine as engine_module

        live_save_answer = engine_module.live_save_answer

        def answer_then_start_game_1(rom_info, rom_id):
            answer = live_save_answer(rom_info, rom_id)
            if rom_id == 1:
                svc.stop_pre_launch_sync(1)
            return answer

        monkeypatch.setattr(engine, "follow_save_directory", recording_follow)
        monkeypatch.setattr(engine_module, "live_save_answer", answer_then_start_game_1)

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert followed == [2]
        assert result["skipped_running"] == [{"rom_id": 1, "name": "rom-1"}]

    @pytest.mark.asyncio
    async def test_a_sync_saves_of_the_game_stops_and_is_refused_as_running(self, tmp_path):
        svc, fake, _games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, _ROM, content=None)
        fake.saves[100] = _server_save(save_id=100, rom_id=_ROM, filename="game42.srm", slot="default")
        list_saves = fake.list_saves

        def start_the_game_while_it_is_listed(rom_id, **kwargs):
            svc.stop_pre_launch_sync(rom_id)
            return list_saves(rom_id, **kwargs)

        fake.list_saves = start_the_game_while_it_is_listed  # type: ignore[method-assign]

        with pytest.raises(GameRunning) as refused:
            await svc.sync_rom_saves(_ROM)

        assert refused.value.message == _RUNNING_MESSAGE
        assert refused.value.details == {"synced": 0, "uploaded": 0, "downloaded": 0}
        assert not (tmp_path / "saves" / "gba" / "game42.srm").exists()
        assert "download_save_content" not in _transfers_of(fake, _ROM)

    @pytest.mark.asyncio
    async def test_a_stop_of_one_game_leaves_a_sync_saves_of_another_alone(self, tmp_path):
        svc, fake, _games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, _ROM, content=None)
        fake.saves[100] = _server_save(save_id=100, rom_id=_ROM, filename="game42.srm", slot="default")
        list_saves = fake.list_saves

        def start_another_game_while_it_is_listed(rom_id, **kwargs):
            svc.stop_pre_launch_sync(7)
            return list_saves(rom_id, **kwargs)

        fake.list_saves = start_another_game_while_it_is_listed  # type: ignore[method-assign]

        result = await svc.sync_rom_saves(_ROM)

        assert result["downloaded"] == 1
        assert (tmp_path / "saves" / "gba" / "game42.srm").exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sync",
        [lambda svc: svc.sync_rom_saves(_ROM), lambda svc: svc.sync_all_saves()],
        ids=["sync-saves", "sync-all"],
    )
    async def test_their_downloads_are_unmarked_at_the_get_and_acknowledged_once_placed(self, tmp_path, sync):
        svc, fake, _games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, _ROM, content=None)
        fake.saves[100] = _server_save(save_id=100, rom_id=_ROM, filename="game42.srm", slot="default")

        await sync(svc)

        calls = [c for c in fake.call_log if c[0] in ("download_save_content", "confirm_download")]
        assert [c[0] for c in calls] == ["download_save_content", "confirm_download"]
        assert calls[0][2]["optimistic"] is False


class TestAPlatformsDeleteLeavesARunningGameAlone:
    """D6: deleting all of a platform's local saves leaves a running game's saves, names it, and deletes the rest."""

    @pytest.mark.asyncio
    async def test_the_running_game_keeps_its_saves_and_is_named(self, tmp_path):
        svc, _fake, _games = _service(tmp_path, 1)
        _confirmed_rom(svc, tmp_path, 1)
        _confirmed_rom(svc, tmp_path, 2)
        _confirmed_rom(svc, tmp_path, 3)

        result = await svc.delete_platform_saves("gba")

        assert isinstance(result, dict)
        assert (tmp_path / "saves" / "gba" / "game1.srm").read_bytes() == b"local progress"
        assert not (tmp_path / "saves" / "gba" / "game2.srm").exists()
        assert not (tmp_path / "saves" / "gba" / "game3.srm").exists()
        assert result["deleted_count"] == 2
        assert result["skipped_running"] == [{"rom_id": 1, "name": "rom-1"}]
        assert result["message"] == "Deleted 2 save file(s) from 2 ROM(s); rom-1 was skipped because it is running"

    @pytest.mark.asyncio
    async def test_with_no_game_running_the_answer_names_none(self, tmp_path):
        svc, _fake, _games = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1)

        result = await svc.delete_platform_saves("gba")

        assert isinstance(result, dict)
        assert result["skipped_running"] == []
        assert result["message"] == "Deleted 1 save file(s) from 1 ROM(s)"


def _moved_answer(directory: str) -> SaveAnswer:
    return SaveAnswer(
        state=cast("Any", "per_game_files"),
        unestablished=None,
        emulator="mGBA",
        directory=directory,
        backing_directory=None,
        granularity="per-game-file",
        needs=(),
        components=(SaveComponent(name="game42.srm", directory=directory, role="battery", granularity=None),),
        caveats=(),
        content_installed=True,
        root_kind="savefile_directory",
    )


def _moved_save_directory(svc, tmp_path) -> tuple[Any, Any]:
    """ROM 42's save recorded in its old directory and answered in a new one; returns ``(old, new)``."""
    old = tmp_path / "saves" / "gba"
    new = tmp_path / "saves" / "gba" / "mGBA"
    _confirmed_rom(svc, tmp_path, _ROM)
    with _uow(svc) as uow:
        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=_ROM, directory=str(old)))
    cast("FakeSaveLocationReader", svc._rom_info._save_locations).answer_with("gba", _moved_answer(str(new)))
    return old, new


def _recorded(svc) -> str | None:
    with _uow(svc) as uow:
        record = uow.answered_save_directories.get(_ROM)
    return record.directory if record is not None else None


class TestNoFollowWhileTheGameRuns:
    """D8: the automatic follow does nothing for a running game; the exempt paths follow while it is listed.

    The exempt paths are the syncs before a start and after a game, and a conflict resolved before a start (D9).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "read",
        [
            lambda svc: svc.get_save_status(_ROM),
            lambda svc: svc.check_local_drift(_ROM),
            lambda svc: svc.count_platform_saves("gba"),
        ],
        ids=["game-page-status", "local-save-check", "platform-count"],
    )
    async def test_a_read_of_a_running_game_follows_no_save_directory(self, tmp_path, read):
        svc, _fake, _games = _service(tmp_path, _ROM)
        old, new = _moved_save_directory(svc, tmp_path)

        await _outcome(read(svc))

        assert (old / "game42.srm").read_bytes() == b"local progress"
        assert not (new / "game42.srm").exists()
        assert _recorded(svc) == str(old)

    @pytest.mark.asyncio
    async def test_the_same_read_follows_once_the_game_is_quit(self, tmp_path):
        svc, _fake, games = _service(tmp_path, _ROM)
        old, new = _moved_save_directory(svc, tmp_path)
        await svc.get_save_status(_ROM)
        games.running.clear()

        await svc.get_save_status(_ROM)

        assert (new / "game42.srm").read_bytes() == b"local progress"
        assert not (old / "game42.srm").exists()
        assert _recorded(svc) == str(new)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sync",
        [
            lambda svc: svc.pre_launch_sync(_ROM),
            lambda svc: svc.post_exit_sync(_ROM),
            lambda svc: svc.resolve_sync_conflict(_ROM, "game42.srm", 100, "keep_local", before_start=True),
        ],
        ids=["before-a-start", "after-a-game", "conflict-resolved-before-a-start"],
    )
    async def test_the_syncs_before_a_start_and_after_a_game_follow_while_it_is_listed(self, tmp_path, sync):
        svc, _fake, _games = _service(tmp_path, _ROM)
        _old, new = _moved_save_directory(svc, tmp_path)

        await _outcome(sync(svc))

        assert (new / "game42.srm").read_bytes() == b"local progress"
        assert _recorded(svc) == str(new)
