"""Tests for Keep Local's identity check and adopt — the sync's own adopt step, and the upload it falls back to."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fakes.fake_active_core_resolver import FakeActiveCoreResolver

from domain.rom_save_sync_state import RomSaveSyncState
from lib.errors import DeviceWriteError
from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _file_md5,
    _install_rom,
    _require_save_state,
    _seed_save_state,
    _server_save_with_syncs,
    make_service,
)

_BYTES = b"identical content"


def _setup(tmp_path):
    """A conflict whose server head holds exactly the bytes of the local file.

    The ROM's state knows no system and no slot yet, and records a core an
    earlier upload ran under; the core resolved today is a different one.
    """
    svc, fake = make_service(tmp_path, active_core=FakeActiveCoreResolver(default=("snes9x_libretro", "Snes9x")))
    _enable_sync_with_device(svc)
    _install_rom(svc, tmp_path)
    save_path = _create_save(tmp_path, content=_BYTES)
    head = _server_save_with_syncs(device_syncs=[{"device_id": "device-1", "is_current": False}])
    head["content_hash"] = _file_md5(str(save_path))
    fake.saves[100] = head
    fake.uploaded_files[100] = str(save_path)
    earlier = RomSaveSyncState(last_synced_core="mgba_libretro")
    earlier.record_synced_core("mgba_libretro", "retroarch-mgba")
    _seed_save_state(svc, 42, earlier)
    return svc, fake, save_path


def _uploads(fake) -> list[dict[str, Any]]:
    return [c[2] for c in fake.call_log if c[0] == "upload_save"]


class TestKeepLocalAdoptsAsTheSyncDoes:
    @pytest.mark.asyncio
    async def test_an_identical_head_records_the_system_and_the_slot_and_keeps_the_last_uploads_core(self, tmp_path):
        svc, fake, _path = _setup(tmp_path)

        result = await svc.resolve_sync_conflict(
            rom_id=42, filename="pokemon.srm", server_save_id=100, action="keep_local"
        )

        assert result == {"success": True, "action": "keep_local"}
        assert _uploads(fake) == []
        state = _require_save_state(svc, 42)
        assert (state.system, state.active_slot) == ("gba", "autosave")
        assert (state.last_synced_core, state.emulator) == ("mgba_libretro", "retroarch-mgba")
        assert state.files["pokemon.srm"].tracked_save_id == 100

    @pytest.mark.asyncio
    async def test_a_file_that_changed_after_the_identity_check_is_uploaded_with_overwrite(self, tmp_path):
        svc, fake, save_path = _setup(tmp_path)
        hash_server_copy = svc._save_transfer.hash_server_copy

        def then_the_game_writes(server_save):
            answered = hash_server_copy(server_save)
            save_path.write_bytes(b"written after the comparison")
            return answered

        svc._save_transfer.hash_server_copy = then_the_game_writes

        result = await svc.resolve_sync_conflict(
            rom_id=42, filename="pokemon.srm", server_save_id=100, action="keep_local"
        )

        assert result == {"success": True, "action": "keep_local"}
        assert not any(c[0] == "confirm_download" and c[1][0] == 100 for c in fake.call_log)
        uploads = _uploads(fake)
        assert len(uploads) == 1
        assert (uploads[0]["save_id"], uploads[0]["overwrite"]) == (None, True)
        assert _require_save_state(svc, 42).files["pokemon.srm"].tracked_save_id != 100


class TestKeepLocalWhenTheServerCopyCannotBeWritten:
    @pytest.mark.asyncio
    async def test_a_device_write_error_is_logged_as_a_warning_and_the_local_file_is_uploaded(self, tmp_path, caplog):
        svc, fake, _path = _setup(tmp_path)

        def no_space(save_id: int, dest_path: str) -> None:
            raise DeviceWriteError(28, "No space left on device")

        fake.download_save = no_space  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING):
            result = await svc.resolve_sync_conflict(
                rom_id=42, filename="pokemon.srm", server_save_id=100, action="keep_local"
            )

        assert result == {"success": True, "action": "keep_local"}
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "keep_local" in r.getMessage()]
        assert len(warnings) == 1
        assert "No space left on device" in warnings[0].getMessage()
        uploads = _uploads(fake)
        assert len(uploads) == 1
        assert uploads[0]["overwrite"] is True
