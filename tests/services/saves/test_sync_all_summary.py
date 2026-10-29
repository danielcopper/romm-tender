"""Sync All Saves Now counts only the games it compared, and names each reason it skipped one for (#2151).

Each ROM sits on a system of its own, because the fake seam answers per system:
what is under test is what the sweep does with each answer, not how the answer
is read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from fakes.fake_running_rom_ids import FakeRunningRomIds
from fakes.fake_save_location_reader import FakeSaveLocationReader

from domain.save_answer import SaveAnswer, saves_root_missing_answer
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

    from services.saves import SaveService

_MISSING_ROOT = "/run/media/deck/sd/retrodeck/saves"

_SHARED_CARD = SaveAnswer(
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


def _service(tmp_path, *running: int) -> tuple[SaveService, FakeSaveApi]:
    svc, fake = make_service(tmp_path, running_rom_ids=FakeRunningRomIds(running))
    _enable_sync_with_device(svc, "test-device")
    return svc, fake


def _reader(svc) -> FakeSaveLocationReader:
    return cast("FakeSaveLocationReader", svc._rom_info._save_locations)


def _game(svc, tmp_path, rom_id: int, system: str, *, confirmed: bool, save: bool = False) -> None:
    """ROM *rom_id* installed on *system*, its slot confirmed or not, with a local save or none."""
    _install_rom(svc, tmp_path, rom_id=rom_id, system=system, file_name=f"game{rom_id}.rom")
    if confirmed:
        _seed_save_state_dict(svc, rom_id, {"active_slot": "default", "slot_confirmed": True}, platform_slug=system)
    if save:
        _create_save(tmp_path, system=system, rom_name=f"game{rom_id}", content=f"save {rom_id}".encode())


def _beside_the_game_file(tmp_path, system: str, rom_id: int) -> SaveAnswer:
    return FakeSaveLocationReader(beside_content=True).resolve_save_answer(
        system=system,
        content_path=str(tmp_path / "retrodeck" / "roms" / system / f"game{rom_id}.rom"),
        emulator_label=None,
    )


def _missing_root() -> SaveAnswer:
    return saves_root_missing_answer(path=_MISSING_ROOT, emulator="mGBA", content_installed=True)


class TestTheLineCountsWhatItComparedAndNamesWhatItSkipped:
    @pytest.mark.asyncio
    async def test_every_kind_of_game_lands_in_its_own_count(self, tmp_path):
        svc, fake = _service(tmp_path, 6)
        _game(svc, tmp_path, 1, "gba", confirmed=True, save=True)  # synced: its save goes up
        _game(svc, tmp_path, 2, "snes", confirmed=True)  # in step: nothing on either side
        _game(svc, tmp_path, 3, "nes", confirmed=False, save=True)  # not set up
        _game(svc, tmp_path, 4, "ps2", confirmed=True)  # cannot be synced
        _game(svc, tmp_path, 5, "gb", confirmed=True)  # beside the game file
        _game(svc, tmp_path, 6, "n64", confirmed=True)  # running
        _reader(svc).answer_with("ps2", _SHARED_CARD)
        _reader(svc).answer_with("gb", _beside_the_game_file(tmp_path, "gb", 5))

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert result["message"] == (
            "Synced 1 save(s) across 2 ROM(s)"
            "; 1 game(s) not set up for save sync"
            "; 1 game(s) skipped — their saves cannot be synced"
            "; 1 game(s) skipped — their saves are written beside the game file"
            "; rom-6 was skipped because it is running"
        )
        assert result["success"] is True
        assert result["synced"] == 1
        assert result["roms_checked"] == 6
        # Only the two compared games were listed on RomM.
        assert sorted(c[1][0] for c in fake.call_log if c[0] == "list_saves") == [1, 2]

    @pytest.mark.asyncio
    async def test_a_game_never_set_up_whose_saves_cannot_sync_counts_as_cannot_be_synced(self, tmp_path):
        svc, _fake = _service(tmp_path)
        _game(svc, tmp_path, 1, "gba", confirmed=True)
        _game(svc, tmp_path, 2, "ps2", confirmed=False, save=True)
        _reader(svc).answer_with("ps2", _SHARED_CARD)

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert result["message"] == (
            "Synced 0 save(s) across 1 ROM(s); 1 game(s) skipped — their saves cannot be synced"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_a_game_whose_start_stopped_its_comparison_is_named_and_not_counted(self, tmp_path):
        svc, fake = _service(tmp_path)
        _game(svc, tmp_path, 1, "gba", confirmed=True)
        _game(svc, tmp_path, 2, "snes", confirmed=True)
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
        assert result["message"] == "Synced 1 save(s) across 1 ROM(s); rom-1 was skipped because it is running"


class TestAMissingSavesFolder:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("confirmed", [True, False], ids=["set-up", "never-set-up"])
    async def test_it_is_named_and_fails_the_run(self, tmp_path, confirmed: bool):
        svc, _fake = _service(tmp_path)
        _game(svc, tmp_path, 1, "gba", confirmed=True, save=True)
        _game(svc, tmp_path, 2, "gbc", confirmed=confirmed)
        _reader(svc).answer_with("gbc", _missing_root())

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert result["message"] == (
            f"Synced 1 save(s) across 1 ROM(s); 1 game(s) skipped — the saves folder {_MISSING_ROOT} does not exist"
        )
        assert result["success"] is False
        assert result["synced"] == 1
        assert result["errors"] == []
