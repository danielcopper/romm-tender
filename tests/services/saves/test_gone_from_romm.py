"""A save sync of a game RomM no longer has says so, stops uploading, and leaves the saves on this device (#2837).

RomM is faked by ``FakeSaveApi``: a ROM in ``gone_rom_ids`` lists no saves, and
an upload or an exact-id read of it answers RomM's entity 404. What is under
test is what each sync path does with that: the first upload's 404, confirmed
by the ROM's own lookup, ends the ROM's run before any further upload; a single
game's sync answers with one sentence, and Sync All Saves Now names the game in
a clause of its own; nothing local is touched and nothing is recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fakes.fake_running_rom_ids import FakeRunningRomIds

from domain.rom_save_sync_state import RomSaveSyncState
from lib.errors import Refused, RommApiError, RommNotFoundError, classify_error
from services.saves._refusals import GoneFromRomm
from services.saves.sync_engine._gone_from_romm import GoneRoms
from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _get_save_state,
    _install_rom,
    _seed_save_state,
    make_service,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from fakes.fake_save_api import FakeSaveApi

    from services.saves import SaveService

_GONE_MESSAGE = "RomM no longer has this game — its saves stay on this device"
_LOCAL = {".srm": b"battery progress", ".rtc": b"clock state"}


def _service(tmp_path, *running: int) -> tuple[SaveService, FakeSaveApi]:
    svc, fake = make_service(tmp_path, running_rom_ids=FakeRunningRomIds(running))
    _enable_sync_with_device(svc, "test-device")
    return svc, fake


def _confirmed_rom(svc, tmp_path, rom_id: int) -> None:
    """ROM *rom_id* on gba, its slot ``default`` confirmed, with two local save files."""
    _install_rom(svc, tmp_path, rom_id=rom_id, file_name=f"game{rom_id}.gba")
    _seed_save_state(svc, rom_id, RomSaveSyncState(system="gba", slot_confirmed=True, active_slot="default"))
    for ext, content in _LOCAL.items():
        _create_save(tmp_path, rom_name=f"game{rom_id}", content=content, ext=ext)


def _local_saves(tmp_path, rom_id: int) -> dict[str, bytes]:
    """Every file of *rom_id*'s game in the saves folder, by name — a backup or a moved copy included."""
    return {path.name: path.read_bytes() for path in sorted((tmp_path / "saves" / "gba").glob(f"game{rom_id}*"))}


def _calls(fake: FakeSaveApi, name: str, rom_id: int) -> int:
    """How many *name* requests *fake* answered for *rom_id*."""
    return sum(1 for call, args, _kwargs in fake.call_log if call == name and args[0] == rom_id)


async def _outcome(call: Awaitable[Any]) -> Any:
    """*call*'s answer, or the refusal it raised."""
    try:
        return await call
    except Refused as refusal:
        return refusal


class TestASingleGamesSync:
    """D1 to D4: a single game's sync stops at the first upload 404 its lookup confirms, and answers one sentence."""

    @pytest.mark.parametrize("path", ["sync_rom_saves", "post_exit_sync", "pre_launch_sync"])
    @pytest.mark.asyncio
    async def test_it_uploads_nothing_further_and_answers_the_sentence(self, tmp_path, path):
        svc, fake = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1)
        fake.gone_rom_ids.add(1)

        outcome = await _outcome(getattr(svc, path)(1))

        assert isinstance(outcome, GoneFromRomm)
        assert outcome.reason == "gone_from_romm"
        assert outcome.message == _GONE_MESSAGE
        assert outcome.details == {"synced": 0}
        # Two local files, one upload: the first 404, once the lookup confirms
        # it, ends the run before the second file is sent.
        assert _calls(fake, "upload_save", 1) == 1
        assert _calls(fake, "get_rom", 1) == 1

    @pytest.mark.asyncio
    async def test_the_local_saves_and_the_save_state_are_left_as_they_are(self, tmp_path):
        svc, fake = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1)
        fake.gone_rom_ids.add(1)
        before = _get_save_state(svc, 1)

        await _outcome(svc.sync_rom_saves(1))

        assert _local_saves(tmp_path, 1) == {"game1.rtc": b"clock state", "game1.srm": b"battery progress"}
        assert _get_save_state(svc, 1) == before


class TestSyncAllSavesNow:
    """D3/D4: the sweep names each game RomM no longer has in a clause of its own and does not fail for it."""

    @pytest.mark.asyncio
    async def test_a_gone_game_is_named_counted_nowhere_and_fails_nothing(self, tmp_path):
        svc, fake = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1)
        _confirmed_rom(svc, tmp_path, 2)
        fake.gone_rom_ids.add(1)
        before = _get_save_state(svc, 1)

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert result["message"] == (
            "Synced 2 save(s) across 1 ROM(s); rom-1 is no longer on RomM — its saves stay on this device"
        )
        assert result["success"] is True
        assert result["errors"] == []
        assert result["synced"] == 2
        assert _calls(fake, "upload_save", 1) == 1
        assert _calls(fake, "upload_save", 2) == 2
        assert _local_saves(tmp_path, 1) == {"game1.rtc": b"clock state", "game1.srm": b"battery progress"}
        assert _get_save_state(svc, 1) == before

    @pytest.mark.asyncio
    async def test_two_gone_games_share_one_clause_beside_a_running_one(self, tmp_path):
        svc, fake = _service(tmp_path, 3)
        for rom_id in (1, 2, 3):
            _confirmed_rom(svc, tmp_path, rom_id)
        fake.gone_rom_ids.update({1, 2})

        result = await svc.sync_all_saves()

        assert isinstance(result, dict)
        assert result["message"] == (
            "Synced 0 save(s) across 0 ROM(s)"
            "; rom-1 and rom-2 are no longer on RomM — their saves stay on this device"
            "; rom-3 was skipped because it is running"
        )
        assert result["success"] is True


class TestAnUnconfirmed404StaysAnError:
    """D1: only the lookup's own 404 proves the ROM is gone; any other outcome leaves the upload's 404 an error."""

    @pytest.mark.parametrize(
        "lookup",
        [
            pytest.param(None, id="the lookup answers"),
            pytest.param(RommApiError("Not Found"), id="the lookup proves nothing"),
        ],
    )
    @pytest.mark.asyncio
    async def test_every_file_is_tried_and_fails_as_before(self, tmp_path, lookup):
        svc, fake = _service(tmp_path)
        _confirmed_rom(svc, tmp_path, 1)
        upload_404 = RommNotFoundError("Rom with id '1' not found")
        upload_save = fake.upload_save
        get_rom = fake.get_rom

        def refused_upload(*args, **kwargs):
            upload_save(*args, **kwargs)
            raise upload_404

        def looked_up(rom_id):
            get_rom(rom_id)
            if lookup is not None:
                raise lookup
            return {"id": rom_id}

        fake.upload_save = refused_upload  # type: ignore[method-assign]
        fake.get_rom = looked_up  # type: ignore[method-assign]

        result = await svc.sync_rom_saves(1)

        reason = classify_error(upload_404)[1]
        assert result["success"] is False
        assert sorted(result["errors"]) == [f"game1.rtc: {reason}", f"game1.srm: {reason}"]
        assert _GONE_MESSAGE not in result["message"]
        assert _calls(fake, "upload_save", 1) == 2


class TestGoneRoms:
    def test_three_names_are_joined_as_the_running_clause_joins_them(self):
        gone = GoneRoms()
        for name in ("A", "B", "C"):
            gone.record(name)

        assert gone.annotate("Synced") == ("Synced; A, B and C are no longer on RomM — their saves stay on this device")

    def test_no_name_leaves_the_message_alone(self):
        assert GoneRoms().annotate("Synced") == "Synced"
