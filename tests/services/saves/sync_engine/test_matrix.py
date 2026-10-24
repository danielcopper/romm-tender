"""Tests for MatrixExecutor — newest-wins matrix evaluation and its per-file
dispatch. Anything that decides "which side wins for this file" and hands the
decision to the step that carries it out lives in
backend/services/saves/sync_engine/matrix.py and is exercised here; the steps
that move bytes between local saves_dir and the RomM server are tested in
tests/services/saves/test_save_transfer.py and test_save_backup.py. Public
use-case orchestration (lock acquisition, guards) lives in test_engine.py;
device registration in test_devices.py; conflict rollback in test_rollback.py.
"""

import hashlib
import os
import zipfile
from dataclasses import replace
from typing import Any

import pytest
from fakes.fake_save_location_reader import FakeSaveLocationReader

from domain.live_device_id import LiveDeviceId
from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from domain.save_answer import SaveGroup, build_save_answer
from domain.sync_action import Conflict, Skip
from lib.errors import RommApiError, classify_error
from services.saves.sync_engine.matrix import (
    DispatchSink,
    RomDispatchContext,
    SyncRunOptions,
)
from tests.services.saves._helpers import (
    _GAVEL,
    _create_save,
    _do_sync,
    _do_upload,
    _enable_sync_with_device,
    _file_md5,
    _get_save_state,
    _install_rom,
    _require_save_state,
    _seed_save_state,
    _seed_save_state_dict,
    _server_save,
    _server_save_with_syncs,
    _set_device_id,
    make_service,
    rom_save_sync_state_from_dict,
)


class TestUploadSpecialChars:
    """Upload with special characters (spaces, parentheses) in filename."""

    def test_find_saves_with_special_chars(self, tmp_path):
        svc, _ = make_service(tmp_path)
        rom_name = "Metroid - Zero Mission (USA)"
        file_name = f"{rom_name}.gba"
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name=file_name)
        _create_save(tmp_path, system="gba", rom_name=rom_name)

        result = svc._rom_info.find_save_files(42)

        assert len(result) == 1
        assert result[0]["filename"] == f"{rom_name}.srm"


class TestV47SyncFlow:
    def test_list_saves_passes_device_id(self, tmp_path):
        """v4.7: list_saves receives server_device_id."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "server-dev-123")
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)

        _do_sync(svc, 42)

        list_calls = [c for c in fake.call_log if c[0] == "list_saves"]
        assert len(list_calls) >= 1
        assert list_calls[0][2]["device_id"] == "server-dev-123"

    def test_upload_passes_device_id_and_slot(self, tmp_path):
        """v4.7: upload_save receives device_id and slot."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "server-dev-123")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)

        _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba")

        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        assert upload_calls[0][2]["device_id"] == "server-dev-123"
        # Brand-new ROM (no active_slot, no slots) → first upload lands in the
        # configured default slot, now "autosave" (#1529).
        assert upload_calls[0][2]["slot"] == "autosave"

    def test_legacy_slot_uploads_as_null_not_default(self, tmp_path):
        """#1061: a sync on the explicit legacy slot uploads slot=None (slot:null), not 'default'.

        Regression: the upload slot resolution returned 'default' for
        ``active_slot=None``, misfiling a save played on the legacy slot into the
        default slot — so switching back to legacy found nothing on the server
        and the local file had already been backed up by the intervening switch.
        """
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "server-dev-123")
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)
        # Explicit legacy: active_slot=None with a populated slots dict — exactly
        # the state after switching to the legacy slot via the saves tab.
        _seed_save_state(
            svc,
            42,
            RomSaveSyncState(
                system="gba",
                active_slot=None,
                slot_confirmed=True,
                slots={"": {"source": "local", "count": 0, "latest_updated_at": None}},
            ),
        )

        _do_sync(svc, 42)  # server has no saves in the slot → local file → Upload

        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        assert upload_calls[0][2]["slot"] is None  # legacy → slot:null, NOT "default"

    def test_v47_skip_when_is_current(self, tmp_path):
        """v4.7: server says is_current=True, local unchanged → skip."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        content = b"same content"
        _create_save(tmp_path, content=content)
        local_hash = hashlib.md5(content).hexdigest()

        # Pre-populate sync state (simulating previous sync)
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "last_sync_hash": local_hash,
                        "last_sync_server_updated_at": "2026-02-17T06:00:00Z",
                        "last_sync_server_save_id": 100,
                        "last_sync_server_size": len(content),
                    }
                }
            },
        )

        # Set up server save with device_syncs showing is_current=True
        fake.saves[100] = {
            "id": 100,
            "rom_id": 42,
            "file_name": "pokemon.srm",
            "updated_at": "2026-02-17T06:00:00Z",
            "file_size_bytes": len(content),
            "device_syncs": [{"device_id": "dev-1", "is_current": True}],
        }

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)
        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        assert conflicts == []

    def test_v47_download_when_not_current(self, tmp_path):
        """v4.7: server says is_current=False, local unchanged → download."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        content = b"old content"
        _create_save(tmp_path, content=content)
        local_hash = hashlib.md5(content).hexdigest()

        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "last_sync_hash": local_hash,
                        "last_sync_server_updated_at": "2026-02-17T06:00:00Z",
                        "last_sync_server_save_id": 100,
                        "last_sync_server_size": len(content),
                    }
                }
            },
        )

        # Server has newer save, device is not current
        fake.saves[100] = {
            "id": 100,
            "rom_id": 42,
            "file_name": "pokemon.srm",
            "updated_at": "2026-02-17T08:00:00Z",
            "file_size_bytes": 2048,
            "device_syncs": [{"device_id": "dev-1", "is_current": False}],
        }

        uploaded, downloaded, errors, _conflicts = _do_sync(svc, 42)
        assert uploaded == 0
        assert downloaded == 1
        assert errors == []
        # Verify download happened
        assert 100 in fake.downloaded_files

    def test_sync_ignores_newer_legacy_save_under_named_slot(self, tmp_path):
        """#877: a slot:null save NEWER than the named-slot head never enters the sync decision.

        Non-vacuous regression: the legacy save (id=200, updated 20:00) is newer
        than the named-slot head (id=100, updated 06:00) the local file is synced
        to. Absent slot isolation the newest-wins pick for the "pokemon.srm"
        target under the active "default" slot would choose 200 and download it —
        so the leaky filter would report ``synced==1`` with 200 in the downloads.
        With ``filter_saves_to_slot`` the "default" slot only sees 100
        (matches local → Skip) and the legacy save is never pulled in.
        """
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        content = b"named-slot content"
        _create_save(tmp_path, content=content)
        local_hash = hashlib.md5(content).hexdigest()

        # Local is synced to the named-slot head; active slot is "default".
        _seed_save_state_dict(
            svc,
            42,
            {
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {
                    "pokemon.srm": {
                        "last_sync_hash": local_hash,
                        "last_sync_server_updated_at": "2026-02-17T06:00:00Z",
                        "last_sync_server_save_id": 100,
                        "last_sync_server_size": len(content),
                    }
                },
            },
        )
        # Named-slot head (older, is_current) + a NEWER legacy save on the server.
        fake.saves[100] = {
            "id": 100,
            "rom_id": 42,
            "file_name": "pokemon.srm",
            "updated_at": "2026-02-17T06:00:00Z",
            "file_size_bytes": len(content),
            "slot": "default",
            "device_syncs": [{"device_id": "dev-1", "is_current": True}],
        }
        fake.saves[200] = {
            "id": 200,
            "rom_id": 42,
            "file_name": "pokemon.srm",
            "updated_at": "2026-02-17T20:00:00Z",
            "file_size_bytes": 4096,
            "slot": None,
            "device_syncs": [{"device_id": "dev-1", "is_current": False}],
        }

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        assert conflicts == []
        # The newer legacy save was never downloaded into the named slot.
        assert 200 not in fake.downloaded_files


class TestTrackedSaveIdMatching:
    """Tests that sync uses tracked_save_id to match server saves instead of filename."""

    def test_timestamp_server_save_not_treated_as_separate_download(self, tmp_path):
        """Server save matched by tracked_save_id should not appear as server-only download."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)
        local_hash = _file_md5(str(save_path))

        fake.saves[42] = {
            "id": 42,
            "rom_id": 42,
            "file_name": "pokemon [2026-03-24_15-18-50].srm",
            "updated_at": "2026-03-20T10:00:00",
            "file_size_bytes": 1024,
            "emulator": "retroarch",
            "download_path": "/saves/pokemon [2026-03-24_15-18-50].srm",
        }

        _seed_save_state_dict(
            svc,
            42,
            {
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 42,
                        "last_sync_hash": local_hash,
                        "last_sync_at": "2026-03-20T10:00:00",
                        "last_sync_server_updated_at": "2026-03-20T10:00:00",
                        "last_sync_server_save_id": 42,
                        "last_sync_server_size": 1024,
                        "local_mtime_at_last_sync": "2026-03-20T10:00:00",
                    },
                },
            },
        )

        # Sync should NOT download the timestamp-named file as a new server-only save
        _uploaded, _downloaded, errors, _conflicts = _do_sync(svc, 42)
        assert len(errors) == 0
        # No downloads should have occurred (files are in sync)
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert len(download_calls) == 0

    @pytest.mark.asyncio
    async def test_get_save_status_uses_tracked_save_id(self, tmp_path):
        """get_save_status should not show timestamp-named server save as separate file."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)

        fake.saves[42] = {
            "id": 42,
            "rom_id": 42,
            "file_name": "pokemon [2026-03-24_15-18-50].srm",
            "updated_at": "2026-03-20T10:00:00",
            "file_size_bytes": 1024,
            "emulator": "retroarch",
            "download_path": "/saves/pokemon [2026-03-24_15-18-50].srm",
        }

        _seed_save_state_dict(
            svc,
            42,
            {
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 42,
                        "last_sync_hash": hashlib.md5(b"\x00" * 1024).hexdigest(),
                        "last_sync_at": "2026-03-20T10:00:00",
                        "last_sync_server_updated_at": "2026-03-20T10:00:00",
                        "last_sync_server_save_id": 42,
                        "last_sync_server_size": 1024,
                        "local_mtime_at_last_sync": "2026-03-20T10:00:00",
                    },
                },
            },
        )

        result = await svc.get_save_status(42)
        filenames = [f["filename"] for f in result["files"]]
        # The timestamp-named server save should NOT appear as a separate file
        assert "pokemon [2026-03-24_15-18-50].srm" not in filenames
        # The local filename should appear
        assert "pokemon.srm" in filenames

    @pytest.mark.asyncio
    async def test_status_fallback_matches_newest_no_phantom_downloads(self, tmp_path):
        """Status with no tracked_save_id matches newest server save, no phantom downloads."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)

        fake.saves[10] = {
            "id": 10,
            "rom_id": 42,
            "file_name": "pokemon [old].srm",
            "updated_at": "2026-03-24T10:00:00",
            "file_size_bytes": 100,
            "emulator": "retroarch",
            "download_path": "/saves/pokemon [old].srm",
            "slot": "default",
        }
        fake.saves[20] = {
            "id": 20,
            "rom_id": 42,
            "file_name": "pokemon [new].srm",
            "updated_at": "2026-03-24T15:00:00",
            "file_size_bytes": 200,
            "emulator": "retroarch",
            "download_path": "/saves/pokemon [new].srm",
            "slot": "default",
        }

        _seed_save_state_dict(
            svc,
            42,
            {
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {},
            },
        )

        result = await svc.get_save_status(42)
        filenames = [f["filename"] for f in result["files"]]

        # The local file should appear (matched to newest server save)
        assert "pokemon.srm" in filenames
        # Timestamp server files should NOT appear as separate entries
        assert "pokemon [old].srm" not in filenames
        assert "pokemon [new].srm" not in filenames

    def test_server_only_downloads_newest_with_local_filename(self, tmp_path):
        """Case 2: no local file, server has multiple timestamped saves.
        Should download only the newest, saved as the correct local filename."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        # NO local save created — Case 2

        # Server has 3 timestamped versions of the same save
        for sid, ts in [(16, "15-18-50"), (17, "15-19-15"), (18, "15-19-26")]:
            fake.saves[sid] = {
                "id": sid,
                "rom_id": 42,
                "file_name": f"pokemon [2026-03-24_{ts}].srm",
                "file_name_no_tags": "pokemon",
                "file_extension": "srm",
                "updated_at": f"2026-03-24T{ts.replace('-', ':')}",
                "file_size_bytes": 1024,
                "emulator": "retroarch-mgba",
                "slot": "default",
                "download_path": f"/saves/pokemon [2026-03-24_{ts}].srm",
            }

        _seed_save_state_dict(
            svc,
            42,
            {
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {},
            },
        )

        uploaded, downloaded, errors, _conflicts = _do_sync(svc, 42)
        assert len(errors) == 0
        assert uploaded == 0
        assert downloaded == 1  # only ONE download

        # Should download only once (the newest, id=18)
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert len(download_calls) == 1
        assert download_calls[0][1][0] == 18  # save_id=18 (newest)

        # File should be saved as pokemon.srm (local name), NOT timestamp name
        saves_dir = tmp_path / "saves" / "gba"
        assert (saves_dir / "pokemon.srm").exists()
        assert not (saves_dir / "pokemon [2026-03-24_15-19-26].srm").exists()

    @pytest.mark.asyncio
    async def test_status_server_only_shows_local_filename(self, tmp_path):
        """Status display should show local filename for server-only saves, not timestamp."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        # NO local save

        fake.saves[18] = {
            "id": 18,
            "rom_id": 42,
            "file_name": "pokemon [2026-03-24_15-19-26].srm",
            "file_name_no_tags": "pokemon",
            "file_extension": "srm",
            "updated_at": "2026-03-24T15:19:26",
            "file_size_bytes": 1024,
            "emulator": "retroarch-mgba",
            "slot": "default",
            "download_path": "/saves/pokemon [2026-03-24_15-19-26].srm",
        }

        _seed_save_state_dict(
            svc,
            42,
            {
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {},
            },
        )

        result = await svc.get_save_status(42)
        filenames = [f["filename"] for f in result["files"]]
        assert "pokemon.srm" in filenames
        assert "pokemon [2026-03-24_15-19-26].srm" not in filenames


class TestOlderVersionSkipping:
    """Older stacked versions in the same slot must not be downloaded."""

    def test_different_slot_filtered_out(self, tmp_path):
        """Saves in a different slot should be filtered out entirely."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        _create_save(tmp_path, content=b"local save")

        # Matched in slot=default
        fake.saves[10] = _server_save(
            save_id=10,
            filename="pokemon.srm",
            updated_at="2026-03-24T15:00:00",
            slot="default",
        )
        # Unmatched in slot=portable — filtered out by active_slot
        fake.saves[20] = _server_save(
            save_id=20,
            filename="pokemon [old].srm",
            updated_at="2026-03-20T10:00:00",
            slot="portable",
        )
        # This device is current on save 10 (its baseline matches) so the matrix
        # Skips it — the only thing under test is that the portable save 20 is
        # slot-filtered out and never downloaded.
        fake.stage_device_sync(10, "dev-1", "2026-03-24T15:00:00")

        local_hash = _file_md5(tmp_path / "saves" / "gba" / "pokemon.srm")
        _seed_save_state_dict(
            svc,
            42,
            {
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 10,
                        "last_sync_hash": local_hash,
                        "last_sync_at": "2026-03-24T15:00:00",
                        "last_sync_server_updated_at": "2026-03-24T15:00:00",
                        "last_sync_server_save_id": 10,
                        "last_sync_server_size": 1024,
                        "local_mtime_at_last_sync": "2026-03-24T15:00:00",
                    },
                },
            },
        )

        _uploaded, _downloaded, _errors, _conflicts = _do_sync(svc, 42)
        # pokemon [old].srm in slot=portable is filtered out — no download
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert len(download_calls) == 0


class TestMultiFileSaveSetGrouping:
    """Regression for #1006.

    A multi-file save set (e.g. GBA ``Game.srm`` + ``Game.rtc``) must be
    matrix-evaluated extension-by-extension: each local file is compared only
    against the server saves sharing its canonical target. Before the fix the
    local-file loop handed ``compute_sync_action`` the whole slot, so
    ``Game.srm`` was evaluated against ``Game.rtc``'s (newer) server record —
    cross-extension corruption.
    """

    def test_each_local_file_evaluated_only_against_its_own_target(self, tmp_path):
        """Each local file's outcome carries only the server saves whose canonical
        target is that file — and the resolved chosen server is the same-extension
        record, never the newer save of a sibling extension."""
        from domain.save_status_builders import resolve_chosen_server

        svc, _ = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        # Two-extension save set: .srm + .rtc both present on disk.
        srm_path = _create_save(tmp_path, content=b"srm bytes", ext=".srm")
        rtc_path = _create_save(tmp_path, content=b"rtc bytes", ext=".rtc")
        srm_hash = _file_md5(str(srm_path))
        rtc_hash = _file_md5(str(rtc_path))

        # .srm server record: canonical target pokemon.srm, OLDER. is_current so
        # the matrix picks Skip(synced) → chosen-server falls back to candidates.
        srm_ss = _server_save_with_syncs(
            save_id=10,
            filename="pokemon [old].srm",
            updated_at="2026-03-24T10:00:00",
            slot="default",
            device_syncs=[{"device_id": "dev-1", "is_current": True}],
        )
        srm_ss["file_extension"] = "srm"
        # .rtc server record: canonical target pokemon.rtc, slot-wide NEWEST.
        rtc_ss = _server_save_with_syncs(
            save_id=20,
            filename="pokemon [new].rtc",
            updated_at="2026-03-24T15:00:00",
            slot="default",
            device_syncs=[{"device_id": "dev-1", "is_current": True}],
        )
        rtc_ss["file_extension"] = "rtc"

        # Per-file baselines matching each local hash → both resolve to Skip(synced).
        save_state = rom_save_sync_state_from_dict(
            {
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 10,
                        "last_sync_hash": srm_hash,
                        "last_sync_server_updated_at": "2026-03-24T10:00:00",
                    },
                    "pokemon.rtc": {
                        "tracked_save_id": 20,
                        "last_sync_hash": rtc_hash,
                        "last_sync_server_updated_at": "2026-03-24T15:00:00",
                    },
                },
            }
        )
        info = svc._sync_engine._rom_info.get_rom_save_info(42)
        assert info is not None

        outcomes = {
            o.filename: o
            for o in svc._sync_engine._matrix.iter_matrix_outcomes(
                [srm_ss, rtc_ss],
                save_state=save_state,
                device_id="dev-1",
                info=info,
                synced_files=info["save_answer"].synced_files,
                saves_dir=info["saves_dir"],
            )
        }

        # Each canonical target appears, evaluated against ITS OWN server record only.
        srm_outcome = outcomes["pokemon.srm"]
        assert [s["id"] for s in srm_outcome.server_candidates] == [10]
        # The chosen server (consumed at dispatch/status time) is the .srm record,
        # NOT the slot-wide newest .rtc one.
        chosen_srm = resolve_chosen_server(srm_outcome.action, srm_outcome.server_candidates)
        assert chosen_srm is not None
        assert chosen_srm["id"] == 10

        rtc_outcome = outcomes["pokemon.rtc"]
        assert [s["id"] for s in rtc_outcome.server_candidates] == [20]
        chosen_rtc = resolve_chosen_server(rtc_outcome.action, rtc_outcome.server_candidates)
        assert chosen_rtc is not None
        assert chosen_rtc["id"] == 20


class TestZipSaveContentHashParity:
    """#1457: a zip-container local save must be fed to the kernel as RomM's
    per-entry content hash, never the whole-archive MD5.

    A multi-file save uploaded by another client / the web UI lands locally as a
    zip file (downloads are written verbatim, no unzip). Before the fix the matrix
    hashed it whole, so its ``local_hash`` could never equal its own
    ``server.content_hash`` and the #1013 byte-identical dedup (branch 6) and the
    branch-5 byte-identical adoption silently degraded to spurious ``Conflict``.
    """

    @staticmethod
    def _write_zip_save(saves_dir, filename: str) -> None:
        """Write a two-member zip at ``saves_dir/filename`` (a zipped multi-file save)."""
        saves_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(saves_dir / filename, "w") as zf:
            zf.writestr("battery.srm", b"battery-bytes")
            zf.writestr("rtc.bin", b"rtc-bytes")

    def test_zip_local_byte_identical_to_server_adopts_not_conflicts(self, tmp_path):
        svc, _ = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)

        saves_dir = tmp_path / "saves" / "gba"
        self._write_zip_save(saves_dir, "pokemon.srm")
        zip_path = str(saves_dir / "pokemon.srm")

        store = svc._save_file_store
        per_entry_hash = store.content_hash(zip_path)  # RomM parity (zip-aware)
        whole_archive_md5 = store.checksum_md5(zip_path)  # the pre-fix (wrong) scheme
        # The bug's precondition: the two schemes disagree for a zip container.
        assert per_entry_hash != whole_archive_md5

        # Server holds this exact save (content_hash = per-entry) with NO
        # device_syncs entry for us → branch 6 (never-touched head).
        ss = _server_save_with_syncs(filename="pokemon.srm", slot="default", device_syncs=[])
        ss["file_extension"] = "srm"
        ss["content_hash"] = per_entry_hash

        save_state = rom_save_sync_state_from_dict(
            {"system": "gba", "active_slot": "default", "slot_confirmed": True, "files": {}}
        )
        info = svc._sync_engine._rom_info.get_rom_save_info(42)
        assert info is not None

        outcomes = list(
            svc._sync_engine._matrix.iter_matrix_outcomes(
                [ss],
                save_state=save_state,
                device_id="dev-1",
                info=info,
                synced_files=info["save_answer"].synced_files,
                saves_dir=info["saves_dir"],
            )
        )
        assert len(outcomes) == 1
        outcome = outcomes[0]
        # Post-fix: the matrix fed the per-entry hash, so it matched the server's
        # content_hash → dedup adopt-baseline (no duplicate upload, no conflict).
        assert outcome.local_hash == per_entry_hash
        assert isinstance(outcome.action, Skip)
        assert outcome.action.adopt_baseline is True

    def test_regression_shape_whole_archive_md5_would_conflict(self, tmp_path):
        """Pin the degraded shape: feeding the whole-archive MD5 (the pre-fix
        value) into the same branch-6 inputs yields ``Conflict``, while the
        per-entry hash yields ``Skip(adopt_baseline)``."""
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        self._write_zip_save(saves_dir, "pokemon.srm")
        zip_path = str(saves_dir / "pokemon.srm")

        store = svc._save_file_store
        per_entry_hash = store.content_hash(zip_path)
        whole_archive_md5 = store.checksum_md5(zip_path)

        server = _server_save_with_syncs(filename="pokemon.srm", slot="default", device_syncs=[])
        server["content_hash"] = per_entry_hash
        local_file = {"filename": "pokemon.srm", "path": zip_path, "size": os.path.getsize(zip_path), "mtime": 1.0}

        def _decide(local_hash: str):
            return _GAVEL.compute_sync_action(
                local_file=local_file,
                server_saves_in_slot=[server],
                files_state={},
                device_id="dev-1",
                local_hash=local_hash,
            )

        # Whole-archive MD5 (pre-fix): never matches the per-entry server hash →
        # unknown-provenance local colliding with a never-synced head → Conflict.
        assert isinstance(_decide(whole_archive_md5), Conflict)
        # Per-entry hash (post-fix): byte-identity proven → Skip(adopt_baseline).
        post_fix = _decide(per_entry_hash)
        assert isinstance(post_fix, Skip)
        assert post_fix.adopt_baseline is True


class TestOwnUploadIds:
    """Tests for own_upload_ids tracking and the uploaded_by_us flag."""

    @pytest.mark.asyncio
    async def test_post_upload_appends_own_upload_id(self, tmp_path):
        """After a POST upload (new save), the returned save_id is added to own_upload_ids."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)

        # No pre-existing server save — this will be a POST (save_id=None)
        await svc.sync_rom_saves(42)

        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        returned_id = upload_calls[0][2]["save_id"]  # save_id kwarg from upload_save call
        # The save_id passed to upload_save should be None (POST path)
        assert returned_id is None

        rom_state = _require_save_state(svc, 42)
        own_ids = rom_state.own_upload_ids or []
        assert len(own_ids) == 1
        # The id in the list must match what fake returned
        new_save_id = next(iter(fake.saves.values()))["id"]
        assert new_save_id in own_ids

    @pytest.mark.asyncio
    async def test_post_upload_idempotent_in_own_list(self, tmp_path):
        """Calling upload_save_file twice with the same resulting save_id does not duplicate."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_file = _create_save(tmp_path)

        # Pre-populate own_upload_ids with the id that fake will return (1000)
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {},
                "system": "gba",
                "active_slot": "default",
                "own_upload_ids": [1000],
            },
        )
        # Fake will return the same id=1000 because filename matches existing
        fake.saves[1000] = _server_save(save_id=1000, rom_id=42)

        # Call internal upload with no server_save (POST path)
        _do_upload(svc, 42, str(save_file), "pokemon.srm", "gba", server_save=None)

        rom_state = _require_save_state(svc, 42)
        assert rom_state.own_upload_ids is not None
        # Should still have exactly one entry for that id
        assert rom_state.own_upload_ids.count(1000) == 1

    @pytest.mark.asyncio
    async def test_put_upload_appends_own_upload_id(self, tmp_path):
        """A PUT upload (existing save id) records that id in own_upload_ids."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_file = _create_save(tmp_path)

        # Pre-existing server save (id=100) — upload_save called with save_id=100 → PUT
        fake.saves[100] = _server_save(save_id=100, rom_id=42)
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {"pokemon.srm": {"tracked_save_id": 100, "last_sync_hash": "old"}},
                "system": "gba",
                "active_slot": "default",
                "own_upload_ids": [99],  # pre-existing unrelated id
            },
        )

        server_save = fake.saves[100]
        _do_upload(svc, 42, str(save_file), "pokemon.srm", "gba", server_save=server_save)

        rom_state = _require_save_state(svc, 42)
        # This device pushed new content to id 100 → 100 is now ours; 99 untouched.
        assert rom_state.own_upload_ids == [99, 100]

    @pytest.mark.asyncio
    async def test_put_upload_idempotent_in_own_list(self, tmp_path):
        """Two PUT uploads to the same save id record it only once (dedup)."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_file = _create_save(tmp_path)

        fake.saves[100] = _server_save(save_id=100, rom_id=42)
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {"pokemon.srm": {"tracked_save_id": 100, "last_sync_hash": "old"}},
                "system": "gba",
                "active_slot": "default",
                "own_upload_ids": [],
            },
        )

        server_save = fake.saves[100]
        _do_upload(svc, 42, str(save_file), "pokemon.srm", "gba", server_save=server_save)
        _do_upload(svc, 42, str(save_file), "pokemon.srm", "gba", server_save=server_save)

        rom_state = _require_save_state(svc, 42)
        assert rom_state.own_upload_ids is not None
        assert rom_state.own_upload_ids.count(100) == 1
        assert rom_state.own_upload_ids == [100]

    @pytest.mark.asyncio
    async def test_get_save_status_legacy_rom_state_returns_none(self, tmp_path):
        """When rom state exists but own_upload_ids key is absent, uploaded_by_us is None."""
        svc, fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        svc._config.settings["save_sync_enabled"] = True

        fake.saves[26] = _server_save(save_id=26, rom_id=42, filename="pokemon.srm")

        # Legacy state: own_upload_ids key is absent
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {},
                "system": "gba",
                "active_slot": None,
                # no own_upload_ids key
            },
        )

        result = await svc.get_save_status(42)

        files_by_id = {f["server_save_id"]: f for f in result["files"] if f.get("server_save_id")}
        assert files_by_id[26]["uploaded_by_us"] is None

    @pytest.mark.asyncio
    async def test_rollback_to_foreign_version_records_target_id(self, tmp_path):
        """Rolling back PUTs the target's content back, so this device now owns that id."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)

        save_file = _create_save(tmp_path)
        local_hash = _file_md5(str(save_file))

        # own save is 26, tracked is 26 (clean state)
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 26,
                        "last_sync_hash": local_hash,
                    }
                },
                "system": "gba",
                "active_slot": "default",
                "own_upload_ids": [26],
            },
        )
        fake.saves[26] = _server_save(save_id=26, rom_id=42, slot="default")
        # Older version to roll back to
        fake.saves[27] = _server_save(save_id=27, rom_id=42, slot="default", updated_at="2026-01-01T00:00:00Z")

        result = await svc.rollback_to_version(42, "default", 27)

        assert result["status"] == "ok"
        # The switch re-uploads (PUTs) id=27's content to bump updated_at, so this
        # device is now the uploader of the bytes at id 27 → 27 joins the own list.
        rom_state = _require_save_state(svc, 42)
        assert rom_state.own_upload_ids == [26, 27]


class TestPromoteLocalSlotPersistsState:
    """Regression for #346.

    The PUT-path edge case from the issue: server save tracked but the slot
    marker is still ``'local'`` (stale). On promotion, the in-memory mutation
    must reach disk so the next backend start sees ``source='server'``.
    """

    def test_put_path_promotion_survives_reload(self, tmp_path):
        """A PUT upload that promotes a stale local-slot marker persists to disk."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc, device_id="dev-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)

        # Pre-existing tracked server save → upload_save called with save_id=100 (PUT path).
        fake.saves[100] = _server_save(save_id=100, rom_id=42, slot="default")
        server_save = fake.saves[100]

        # rom_state has the slot still flagged 'local' (stale marker).
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {"pokemon.srm": {"tracked_save_id": 100, "last_sync_hash": "old"}},
                "system": "gba",
                "active_slot": "default",
                "slots": {"default": {"source": "local", "count": 1}},
            },
        )

        in_mem = _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba", server_save=server_save).slots["default"]
        assert in_mem["source"] == "server"
        assert in_mem["count"] == 1

        # The promotion reached SQLite — re-reading the aggregate sees source=server.
        reloaded = _require_save_state(svc, 42).slots["default"]
        assert reloaded["source"] == "server"
        assert reloaded["count"] == 1


class TestDoUploadSaveFileStatePersistence:
    """Regression for #409.

    The PUT branch with a slot already marked ``source='server'`` is a no-op
    for slot promotion. Without an unconditional persist at the end of
    ``upload_save_file``, the per-file ``last_sync_hash`` / ``tracked_save_id``
    written with the transfer's record never reaches disk on that path —
    so after a backend restart the next sync re-detects drift and re-uploads
    the same content. This test asserts the upload outcome is persisted
    regardless of which slot-promotion branch fired.
    """

    def test_put_path_persists_file_sync_state_when_slot_already_server(self, tmp_path):
        """PUT with slot.source='server' (no promotion) still persists file sync state."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc, device_id="dev-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"freshly-edited save")
        expected_hash = _file_md5(str(save_path))

        # Pre-existing tracked server save → upload_save called with save_id=100 (PUT path).
        fake.saves[100] = _server_save(save_id=100, rom_id=42, slot="default")
        server_save = fake.saves[100]

        # Slot already known-server: _promote_local_slot_to_server is a no-op
        # on this branch, so the file-state writes have no incidental persist
        # to ride on. File state holds a stale baseline hash to make the
        # regression visible — after the upload, the on-disk hash must be the
        # current local hash (not the stale one).
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {"pokemon.srm": {"tracked_save_id": 100, "last_sync_hash": "stale-pre-upload"}},
                "system": "gba",
                "active_slot": "default",
                "slots": {"default": {"source": "server", "count": 1}},
            },
        )

        in_mem_file = _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba", server_save=server_save).files[
            "pokemon.srm"
        ]
        # In-memory state captured the fresh hash.
        assert in_mem_file.last_sync_hash == expected_hash
        assert in_mem_file.tracked_save_id == 100

        # The fresh hash reached SQLite — without it, the next sync re-detects
        # drift and uploads the same content again (#409 leak).
        reloaded_file = _require_save_state(svc, 42).files["pokemon.srm"]
        assert reloaded_file.last_sync_hash == expected_hash
        assert reloaded_file.tracked_save_id == 100


class TestAutocleanupLimitThreading:
    """The user's ``autocleanup_limit`` setting reaches the POST upload, POST-only.

    Drives the real public ``sync_rom_saves`` so the value is resolved from
    ``settings.json`` in ``_run_rom_sync`` and threaded the whole way down to
    ``upload_save`` — the path the dead-setting bug (#1060) left disconnected.
    """

    @pytest.mark.asyncio
    async def test_post_upload_carries_autocleanup_limit(self, tmp_path):
        """A POST (new save) upload sends the configured ``autocleanup_limit``."""
        svc, fake = make_service(tmp_path)
        svc._config.settings["save_sync_enabled"] = True
        svc._config.settings["autocleanup_limit"] = 25
        _install_rom(svc, tmp_path)
        _create_save(tmp_path)  # local only → POST

        await svc.sync_rom_saves(42)

        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        assert upload_calls[0][2]["save_id"] is None  # POST path
        assert upload_calls[0][2]["autocleanup_limit"] == 25

    @pytest.mark.asyncio
    async def test_offline_edit_upload_posts_and_carries_autocleanup_limit(self, tmp_path):
        """The is_current offline-edit path now POSTs a new save (no longer a PUT,
        ADR-0017) and still carries the configured ``autocleanup_limit``.

        The device is current on the slot head, so the ``overwrite=false`` POST is
        accepted without a 409; the retention cap rides the create.
        """
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc, device_id="device-1")
        svc._config.settings["autocleanup_limit"] = 25
        _install_rom(svc, tmp_path)
        _create_save(tmp_path, content=b"diverged local content")

        # is_current=true on our device + local diverged from the baseline →
        # Upload. Stage the sync ledger so the slot POST is accepted (no 409).
        fake.saves[100] = _server_save_with_syncs(
            save_id=100,
            slot="default",
            device_syncs=[{"device_id": "device-1", "is_current": True}],
        )
        fake.stage_device_sync(100, "device-1", "2026-02-17T06:00:00Z")
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {"pokemon.srm": {"tracked_save_id": 100, "last_sync_hash": "oldhash"}},
                "system": "gba",
                "active_slot": "default",
                "slot_confirmed": True,
            },
        )
        # Confirmed non-legacy → opens a transport-only negotiate session (ADR-0017),
        # but detection is the local matrix: is_current + local diverged → Upload,
        # POSTed as a new save. The negotiate operations are ignored.

        await svc.sync_rom_saves(42)

        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        assert upload_calls[0][2]["save_id"] is None  # POST path
        assert upload_calls[0][2]["overwrite"] is False
        assert upload_calls[0][2]["autocleanup_limit"] == 25


class TestSyncRomSavesDispatch:
    def test_sync_rom_saves_skip_when_synced(self, tmp_path):
        """is_current=true + matching hash + tracked → Skip, no I/O."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"pristine save")
        local_hash = _file_md5(str(save_path))

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": True}],
        )
        fake.saves[100] = ss

        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 100,
                        "last_sync_hash": local_hash,
                        "last_sync_server_updated_at": ss["updated_at"],
                    }
                }
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        assert conflicts == []
        # No upload/download initiated.
        assert not any(c[0] in ("upload_save", "download_save_content") for c in fake.call_log)

    def test_sync_rom_saves_upload_post_when_no_server_save(self, tmp_path):
        """No server saves in slot but local exists → Upload (POST)."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        _create_save(tmp_path, content=b"new local")

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 1
        assert downloaded == 0
        assert errors == []
        assert conflicts == []
        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        # POST → save_id is None
        assert upload_calls[0][2]["save_id"] is None

        file_state = _require_save_state(svc, 42).files["pokemon.srm"]
        assert file_state.tracked_save_id is not None
        assert file_state.last_sync_hash

    def test_sync_rom_saves_download_when_server_changed(self, tmp_path):
        """is_current=false + local hash matches last_sync_hash → Download."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"unchanged local")
        local_hash = _file_md5(str(save_path))

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": False}],
        )
        fake.saves[100] = ss

        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 100,
                        "last_sync_hash": local_hash,
                        "last_sync_server_updated_at": "2025-01-01T00:00:00Z",
                    }
                }
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 1
        assert errors == []
        assert conflicts == []
        # Download_save_content was called against the server save id.
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert len(download_calls) == 1
        assert download_calls[0][1][0] == 100

        file_state = _require_save_state(svc, 42).files["pokemon.srm"]
        assert file_state.tracked_save_id == 100
        assert file_state.last_sync_hash  # updated to downloaded content's hash

    def test_sync_rom_saves_conflict_when_both_changed(self, tmp_path):
        """is_current=false + local hash diverges → Conflict, no I/O."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"diverged local")
        local_hash = _file_md5(str(save_path))

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": False}],
        )
        fake.saves[100] = ss

        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 100,
                        "last_sync_hash": "deadbeef" * 4,  # baseline differs from current local
                        "last_sync_server_updated_at": "2025-01-01T00:00:00Z",
                    }
                }
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        assert len(conflicts) == 1
        c = conflicts[0]
        assert isinstance(c, dict)
        assert c["type"] == "sync_conflict"
        assert c["rom_id"] == 42
        assert c["filename"] == "pokemon.srm"
        assert c["server_save_id"] == 100
        assert c["server_updated_at"] == ss["updated_at"]
        assert c["server_size"] == ss["file_size_bytes"]
        assert c["local_path"] == str(save_path)
        assert c["local_hash"] == local_hash
        assert c["local_mtime"] is not None
        assert c["local_size"] == os.path.getsize(str(save_path))
        assert "created_at" in c

    def test_sync_rom_saves_adopts_and_acknowledges_when_both_moved_to_identical_content(self, tmp_path):
        """Row 12a (#1480) — is_current=false + local diverged from baseline, but
        byte-identical to the moved-past head (``server.content_hash ==
        local_hash``) → adopt, not Conflict. Both sides independently landed on
        the same bytes, so there is nothing to reconcile: the head is adopted with
        no transfer and acknowledged (re-establishing baseline + is_current), and
        no conflict is surfaced.
        """
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"converged content")
        local_hash = _file_md5(str(save_path))

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": False}],
        )
        # The moved-past head holds exactly the local's bytes.
        ss["content_hash"] = local_hash
        fake.saves[100] = ss

        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 100,
                        "last_sync_hash": "deadbeef" * 4,  # baseline differs → diverged
                        "last_sync_server_updated_at": "2025-01-01T00:00:00Z",
                    }
                }
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        assert conflicts == []
        assert not any(c[0] == "download_save_content" for c in fake.call_log)
        assert [c[1] for c in fake.call_log if c[0] == "confirm_download"] == [(100, "device-1")]
        file_state = _require_save_state(svc, 42).files["pokemon.srm"]
        assert (file_state.tracked_save_id, file_state.last_sync_hash) == (100, local_hash)

        file_state = _require_save_state(svc, 42).files["pokemon.srm"]
        assert file_state.tracked_save_id == 100
        assert file_state.last_sync_hash

    def test_sync_rom_saves_server_only_downloads(self, tmp_path):
        """No local file, one server save in slot → Download."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": False}],
        )
        fake.saves[100] = ss

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 1
        assert errors == []
        assert conflicts == []
        saves_dir = tmp_path / "saves" / "gba"
        assert (saves_dir / "pokemon.srm").exists()

    def test_sync_rom_saves_mixed_upload_and_download_counts_both(self, tmp_path):
        """One file uploads and a sibling extension downloads in one run → 1 up, 1 down (#250).

        A local ``.srm`` with no server counterpart is POSTed (upload); a
        server-only ``.sav`` this device is not current on is pulled (download).
        The two directions are tallied separately so the completion toast can
        report the mix.
        """
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        # Local .srm with no matching server save → Upload (POST).
        _create_save(tmp_path, content=b"local srm progress", ext=".srm")
        # Server-only .sav (different canonical target), not current here → Download.
        sav = _server_save_with_syncs(
            save_id=200,
            filename="pokemon.sav",
            device_syncs=[{"device_id": "device-1", "is_current": False}],
        )
        sav["file_extension"] = "sav"
        fake.saves[200] = sav

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 1
        assert downloaded == 1
        assert errors == []
        assert conflicts == []
        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert [c[1][0] for c in download_calls] == [200]
        assert (tmp_path / "saves" / "gba" / "pokemon.sav").exists()

    def test_sync_rom_saves_upload_posts_when_local_diverged(self, tmp_path):
        """is_current=true + local hash diverges from baseline → Upload, POSTed as a
        new save (overwrite=false). ``target_save_id`` no longer selects a PUT
        (ADR-0017); the automatic dispatch always POSTs and relies on the 409
        backstop for the cross-device currency race."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"diverged offline")
        local_hash = _file_md5(str(save_path))

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": True}],
        )
        fake.saves[100] = ss

        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 100,
                        "last_sync_hash": "0" * 32,  # baseline differs from current local
                        "last_sync_server_updated_at": ss["updated_at"],
                    }
                }
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 1
        assert downloaded == 0
        assert errors == []
        assert conflicts == []
        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        # POST — a new save in the slot, overwrite=false so RomM's 409 can backstop.
        assert upload_calls[0][2]["save_id"] is None
        assert upload_calls[0][2]["overwrite"] is False

        file_state = _require_save_state(svc, 42).files["pokemon.srm"]
        assert file_state.last_sync_hash == local_hash

    def test_sync_rom_saves_zero_byte_local_conflicts_no_put(self, tmp_path):
        """#1062: is_current=true + a 0-byte diverged local → Conflict, NO PUT.

        A crashed emulator / full disk left a 0-byte save. RomM PUTs in place
        (no recoverable version), so the matrix must refuse the upload and surface
        a conflict the user resolves instead of silently destroying the only good
        server copy. The local 0-byte file is left untouched on disk.
        """
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        # Local save is 0 bytes (truncated by the crash).
        save_path = _create_save(tmp_path, content=b"")
        local_hash = _file_md5(str(save_path))

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": True}],
        )
        fake.saves[100] = ss

        # Baseline recorded a healthy 8 KiB save; the divergent local is now empty.
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 100,
                        "last_sync_hash": "0" * 32,  # baseline differs from current local
                        "last_sync_server_updated_at": ss["updated_at"],
                        "last_sync_local_size": 8192,
                    }
                }
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        # NO upload (PUT) was issued — the destructive overwrite was refused.
        assert not any(c[0] == "upload_save" for c in fake.call_log)
        # A conflict entry was surfaced for the user to resolve.
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c["type"] == "sync_conflict"
        assert c["rom_id"] == 42
        assert c["filename"] == "pokemon.srm"
        assert c["server_save_id"] == 100
        assert c["local_hash"] == local_hash
        assert c["local_size"] == 0
        # The 0-byte local file is left in place (not deleted by the refusal).
        assert save_path.exists()

    def test_sync_rom_saves_skip_with_adopt_baseline_writes_hash(self, tmp_path):
        """Row 8a — is_current=true + local present + no baseline, local identical to
        the head → Skip + adopt_baseline: no transfer and no acknowledgement, and the
        head is recorded as the file's full baseline."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"first sync")
        local_hash = _file_md5(str(save_path))

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": True}],
        )
        ss["content_hash"] = local_hash
        fake.saves[100] = ss

        # No file_state at all — no baseline yet.
        _seed_save_state(svc, 42, RomSaveSyncState())

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        assert conflicts == []
        # No I/O initiated.
        assert not any(c[0] in ("upload_save", "download_save_content", "download_save") for c in fake.call_log)
        assert not any(c[0] == "confirm_download" for c in fake.call_log)
        # Baseline now persisted, anchored on the adopted head.
        file_state = _require_save_state(svc, 42).files["pokemon.srm"]
        assert file_state.last_sync_hash == local_hash
        assert file_state.tracked_save_id == 100
        assert file_state.last_sync_server_hash == local_hash
        assert file_state.last_sync_local_size == len(b"first sync")

    def test_sync_rom_saves_recovery_download_when_no_local(self, tmp_path):
        """is_current=true on the picked save but local file is gone → Download
        to recover the canonical content."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        # No _create_save here — local file is absent.

        ss = _server_save_with_syncs(
            device_syncs=[{"device_id": "device-1", "is_current": True}],
        )
        fake.saves[100] = ss

        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {
                    "pokemon.srm": {
                        "tracked_save_id": 100,
                        "last_sync_hash": "abc",
                        "last_sync_server_updated_at": ss["updated_at"],
                    }
                }
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 1
        assert errors == []
        assert conflicts == []
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert len(download_calls) == 1
        assert download_calls[0][1][0] == 100
        saves_dir = tmp_path / "saves" / "gba"
        assert (saves_dir / "pokemon.srm").exists()

    def test_sync_rom_saves_upload_409_downgrades_to_download(self, tmp_path):
        """A slot that was empty when listed + local unchanged since baseline: the
        POST planned for it (row 2) 409s because another device's save landed
        first, and the backstop re-fetches the slot and downloads that fresh head,
        no conflict."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"in-sync local")
        local_hash = _file_md5(str(save_path))
        _seed_save_state_dict(
            svc,
            42,
            {
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {"pokemon.srm": {"last_sync_hash": local_hash}},
            },
        )
        foreign: dict[str, Any] = {}
        upload_save = fake.upload_save

        def upload_after_another_device(*args, **kwargs):
            if not foreign:
                foreign.update(
                    fake.seed_foreign_save(
                        42,
                        uploaded_by="device-B",
                        slot="default",
                        updated_at="2026-03-01T00:00:00Z",
                        content=b"newer from device B",
                    )
                )
            return upload_save(*args, **kwargs)

        fake.upload_save = upload_after_another_device

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert conflicts == []
        # The 409-downgraded transfer is attributed as a download, not an upload (#250).
        assert uploaded == 0
        assert downloaded == 1
        # The POST was attempted overwrite=false (so RomM's 409 could fire)…
        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        assert upload_calls[0][2]["overwrite"] is False
        # …then the backstop downloaded the fresh server head into the local file.
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert [c[1][0] for c in download_calls] == [foreign["id"]]
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == b"newer from device B"

    def test_sync_rom_saves_newer_foreign_head_with_unchanged_local_downloads_without_a_post(self, tmp_path):
        """A newer foreign head + local unchanged since baseline: RomM answers this
        device a not-current placeholder on it, so row 10b downloads the head
        directly — no POST, no conflict."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"in-sync local")
        local_hash = _file_md5(str(save_path))

        # A newer save from another device this device has never synced.
        foreign = fake.seed_foreign_save(
            42,
            uploaded_by="device-B",
            slot="default",
            updated_at="2026-03-01T00:00:00Z",
            content=b"newer from device B",
        )
        _seed_save_state_dict(
            svc,
            42,
            {
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {"pokemon.srm": {"last_sync_hash": local_hash}},
            },
        )

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert conflicts == []
        assert uploaded == 0
        assert downloaded == 1
        assert not any(c[0] == "upload_save" for c in fake.call_log)
        download_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert [c[1][0] for c in download_calls] == [foreign["id"]]
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == b"newer from device B"

    def test_sync_rom_saves_foreign_head_with_no_baseline_surfaces_conflict(self, tmp_path):
        """A foreign head in the slot and no local baseline to prove innocence →
        the matrix decides Conflict itself, before any upload is attempted;
        nothing is overwritten on either side."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"local unsynced edit")
        local_hash = _file_md5(str(save_path))

        foreign = fake.seed_foreign_save(
            42,
            uploaded_by="device-B",
            slot="default",
            updated_at="2026-03-01T00:00:00Z",
            content=b"server content",
        )
        # No baseline (files empty) — this device has never synced the slot.
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True, "files": {}})

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert uploaded == 0
        assert downloaded == 0
        assert errors == []
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c["type"] == "sync_conflict"
        assert c["server_save_id"] == foreign["id"]
        assert c["local_hash"] == local_hash
        # Nothing overwritten: local file untouched, no content downloaded, and
        # no upload attempted.
        assert save_path.read_bytes() == b"local unsynced edit"
        assert not any(x[0] == "download_save_content" for x in fake.call_log)
        assert not any(x[0] == "upload_save" for x in fake.call_log)

    def test_sync_rom_saves_upload_409_conflict_on_a_save_without_id_is_a_file_error(self, tmp_path):
        """The backstop's re-fetch returns a head without an ``id``: the conflict
        it would surface is refused as this file's error instead, so no
        descriptor with ``server_save_id: None`` reaches the frontend — and the
        run goes on to the next file, which still downloads."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"local unsynced edit")

        srm_head = fake.seed_foreign_save(
            42,
            uploaded_by="device-B",
            slot="default",
            updated_at="2026-03-01T00:00:00Z",
            content=b"server content",
        )
        rtc = fake.seed_foreign_save(
            42,
            uploaded_by="device-B",
            slot="default",
            filename="pokemon.rtc",
            updated_at="2026-02-01T00:00:00Z",
            content=b"server clock",
        )
        fake.saves[rtc["id"]]["file_extension"] = "rtc"
        _seed_save_state_dict(svc, 42, {"active_slot": "default", "slot_confirmed": True, "files": {}})
        list_saves = fake.list_saves
        listings = 0

        # The planning listing misses the .srm head, so the matrix plans an
        # upload that the POST 409s; the backstop's re-fetch then sees every
        # save without its id. The .rtc was planned as a download off the first
        # listing, ids intact.
        def list_saves_hiding_then_losing_ids(*args, **kwargs):
            nonlocal listings
            listings += 1
            saves = list_saves(*args, **kwargs)
            if listings == 1:
                return [s for s in saves if s["id"] != srm_head["id"]]
            return [{k: v for k, v in s.items() if k != "id"} for s in saves]

        fake.list_saves = list_saves_hiding_then_losing_ids

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert listings == 2
        assert conflicts == []
        assert errors == ["pokemon.srm: RomM returned a server save without an id"]
        assert save_path.read_bytes() == b"local unsynced edit"
        assert (uploaded, downloaded) == (0, 1)
        assert (tmp_path / "saves" / "gba" / "pokemon.rtc").read_bytes() == b"server clock"

    def test_sync_rom_saves_dedup_to_non_head_surfaces_conflict(self, tmp_path):
        """On-device #1482 repro: slot holds an older save A and a newer head B
        (different content); this device is current on B; local content reverted
        to A. The automatic POST content-dedups to A while B still leads the slot
        — the upload never became the head. The guard routes it through the 409
        backstop, surfacing the true state as a conflict on B instead of a false
        ``synced`` on A, and leaves the DB baseline untouched."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"reverted-to-A content")

        # B: the newer head this device is current on (branch 4 → Upload).
        head = fake.seed_foreign_save(
            42,
            save_id=200,
            uploaded_by="device-1",
            slot="default",
            updated_at="2026-03-01T00:00:00Z",
            content=b"head B content",
        )
        # A: an older sibling version we never synced; the POST dedups to it.
        older = fake.seed_foreign_save(
            42,
            save_id=100,
            uploaded_by="device-B",
            slot="default",
            updated_at="2026-02-17T06:00:00Z",
            content=b"old A content",
        )
        _seed_save_state_dict(
            svc,
            42,
            {
                "active_slot": "default",
                "slot_confirmed": True,
                "files": {
                    "pokemon.srm": {
                        "last_sync_hash": hashlib.md5(b"head B content").hexdigest(),
                        "tracked_save_id": 200,
                        "last_sync_server_save_id": 200,
                    }
                },
            },
        )
        fake.arm_add_save_dedup(older["id"])

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert uploaded == 0
        assert downloaded == 0
        # The true state surfaced: a conflict against the head B, not a false sync on A.
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c["type"] == "sync_conflict"
        assert c["server_save_id"] == head["id"]
        # Exactly one POST was attempted (overwrite=false), then no download/confirm.
        upload_calls = [x for x in fake.call_log if x[0] == "upload_save"]
        assert len(upload_calls) == 1
        assert upload_calls[0][2]["overwrite"] is False
        assert not any(x[0] == "download_save_content" for x in fake.call_log)
        assert not any(x[0] == "confirm_download" for x in fake.call_log)
        # Local file untouched (no download), and the baseline still points at B —
        # the dedup response A was never recorded as synced.
        assert save_path.read_bytes() == b"reverted-to-A content"
        file_state = _require_save_state(svc, 42).files["pokemon.srm"]
        assert file_state.tracked_save_id == 200
        assert file_state.last_sync_server_save_id == 200
        assert file_state.last_sync_hash == hashlib.md5(b"head B content").hexdigest()

    def test_handle_upload_409_empty_regroup_records_error(self, tmp_path):
        """A 409 whose re-fetch finds no server save in the file's canonical group
        is a non-fatal error — never a crash or a blind download."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"local")
        # No server saves at all → the re-fetch inside the backstop returns [].

        errors: list[str] = []
        conflicts: list[dict[str, Any]] = []
        result = svc._sync_engine._matrix._handle_upload_409(
            ctx=RomDispatchContext(
                rom_id=42,
                save_state=RomSaveSyncState(active_slot="default"),
                device_id=LiveDeviceId("device-1"),
                rom_name="pokemon",
                save_names=("pokemon.srm", "pokemon.rtc", "pokemon.sav"),
                saves_dir=str(tmp_path / "saves" / "gba"),
                system="gba",
                core_so=None,
            ),
            filename="pokemon.srm",
            local_path=str(save_path),
            local_hash=_file_md5(str(save_path)),
            last_sync_hash=None,
            last_sync_server_hash=None,
            options=SyncRunOptions(default_slot="default"),
            sink=DispatchSink(errors=errors, conflicts=conflicts),
        )

        assert result is None
        assert conflicts == []
        assert len(errors) == 1
        assert "pokemon.srm" in errors[0]
        assert not any(c[0] == "download_save_content" for c in fake.call_log)

    def test_sync_rom_saves_persists_last_sync_check_at(self, tmp_path):
        """Every sync run records last_sync_check_at on the rom-level entry."""
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        # Pure no-op: no local, no server saves.

        before_entry = _get_save_state(svc, 42)
        assert before_entry is None or before_entry.last_sync_check_at is None

        _do_sync(svc, 42)

        after = _require_save_state(svc, 42).last_sync_check_at
        assert after is not None and isinstance(after, str)


class TestADownloadThatFailsLeavesNoTmp:
    """A per-file download removes its ``.tmp`` on every failure and raises on."""

    @pytest.mark.parametrize(
        "failure",
        [RommApiError("upstream 502"), RuntimeError("a bug")],
        ids=["romm-answers-an-error", "any-other-fault"],
    )
    def test_the_per_file_sync_leaves_no_tmp(self, tmp_path, failure):
        from domain.sync_action import Download

        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True, exist_ok=True)
        fake.break_download_on(100, failure)

        errors: list[str] = []
        svc._sync_engine._matrix._dispatch_sync_action(
            Download(server_save={"id": 100, "file_name": "pokemon.srm"}),
            ctx=RomDispatchContext(
                rom_id=42,
                save_state=RomSaveSyncState(),
                device_id=LiveDeviceId("device-1"),
                rom_name="pokemon",
                save_names=("pokemon.srm", "pokemon.rtc", "pokemon.sav"),
                saves_dir=str(saves_dir),
                system="gba",
                core_so=None,
            ),
            filename="pokemon.srm",
            local_path=None,
            local_hash=None,
            last_sync_hash=None,
            last_sync_server_hash=None,
            server_candidates=[],
            options=SyncRunOptions(),
            sink=DispatchSink(errors=errors, conflicts=[]),
        )

        assert len(errors) == 1
        assert not (saves_dir / "pokemon.srm.tmp").exists()


class TestHandleUnexpectedError:
    """_handle_unexpected_error records the error. Reached from
    _dispatch_sync_action's generic except branch."""

    def test_dispatch_sync_action_handles_unexpected_exception(self, tmp_path):
        """A non-RommApiError raised during dispatch is classified and recorded."""
        from domain.sync_action import Download

        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True, exist_ok=True)

        # download_save_file is reached for Download action; make it raise an
        # unexpected (non-RommApi) Exception so _handle_unexpected_error fires.
        def _raise(*_args, **_kwargs):
            raise RuntimeError("disk full")

        fake.download_save_content = _raise  # type: ignore[method-assign]

        errors: list[str] = []
        conflicts: list[dict[str, Any]] = []
        action = Download(server_save={"id": 100, "file_name": "pokemon.srm"})
        direction = svc._sync_engine._matrix._dispatch_sync_action(
            action,
            ctx=RomDispatchContext(
                rom_id=42,
                save_state=RomSaveSyncState(),
                device_id=LiveDeviceId("device-1"),
                rom_name="pokemon",
                save_names=("pokemon.srm", "pokemon.rtc", "pokemon.sav"),
                saves_dir=str(saves_dir),
                system="gba",
                core_so=None,
            ),
            filename="pokemon.srm",
            local_path=None,
            local_hash=None,
            last_sync_hash=None,
            last_sync_server_hash=None,
            server_candidates=[],
            options=SyncRunOptions(),
            sink=DispatchSink(errors=errors, conflicts=conflicts),
        )

        assert direction is None
        assert len(errors) == 1
        assert errors[0].startswith("pokemon.srm:")


class TestDispatchSyncActionErrorBranches:
    """_dispatch_sync_action's typed-error branches (matrix.py lines 476-481).
    RommApiError → classify + record; other Exception → _handle_unexpected_error."""

    def test_dispatch_sync_action_records_rommapi_error(self, tmp_path, caplog):
        """A RommApiError from a Download action is recorded with classify_error
        message AND logged at WARNING (so a sync failure leaves a log trace, not
        only an errors-list entry)."""
        from domain.sync_action import Download

        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True, exist_ok=True)

        def _raise(*_args, **_kwargs):
            raise RommApiError("upstream 502")

        fake.download_save_content = _raise  # type: ignore[method-assign]

        errors: list[str] = []
        conflicts: list[dict[str, Any]] = []
        action = Download(server_save={"id": 100, "file_name": "pokemon.srm"})
        direction = svc._sync_engine._matrix._dispatch_sync_action(
            action,
            ctx=RomDispatchContext(
                rom_id=42,
                save_state=RomSaveSyncState(),
                device_id=LiveDeviceId("device-1"),
                rom_name="pokemon",
                save_names=("pokemon.srm", "pokemon.rtc", "pokemon.sav"),
                saves_dir=str(saves_dir),
                system="gba",
                core_so=None,
            ),
            filename="pokemon.srm",
            local_path=None,
            local_hash=None,
            last_sync_hash=None,
            last_sync_server_hash=None,
            server_candidates=[],
            options=SyncRunOptions(),
            sink=DispatchSink(errors=errors, conflicts=conflicts),
        )

        assert direction is None
        assert len(errors) == 1
        assert errors[0].startswith("pokemon.srm:")
        assert any(
            r.levelname == "WARNING"
            and "_dispatch_sync_action(42)" in r.getMessage()
            and "pokemon.srm" in r.getMessage()
            for r in caplog.records
        )


class TestDispatchUploadDefensiveBranches:
    """_dispatch_upload's defensive no-local-file guard.

    Unreachable from the algorithm's normal output (an ``Upload`` is only
    emitted when a local file is present) but the branch exists to keep a
    future caller's bug from POSTing a phantom save.
    """

    def test_dispatch_upload_records_error_when_local_path_missing(self, tmp_path):
        """_dispatch_upload with local_path=None records an error and skips (no POST)."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)

        errors: list[str] = []
        conflicts: list[dict[str, Any]] = []
        result = svc._sync_engine._matrix._dispatch_upload(
            ctx=RomDispatchContext(
                rom_id=42,
                save_state=RomSaveSyncState(),
                device_id=LiveDeviceId("device-1"),
                rom_name="pokemon",
                save_names=("pokemon.srm", "pokemon.rtc", "pokemon.sav"),
                saves_dir=str(tmp_path / "saves" / "gba"),
                system="gba",
                core_so=None,
            ),
            filename="pokemon.srm",
            local_path=None,
            local_hash=None,
            last_sync_hash=None,
            last_sync_server_hash=None,
            server_candidates=[],
            options=SyncRunOptions(),
            sink=DispatchSink(errors=errors, conflicts=conflicts),
        )

        assert result is None
        assert len(errors) == 1
        assert "upload requested but no local file" in errors[0]
        # No upload was attempted.
        assert not any(c[0] == "upload_save" for c in fake.call_log)


class TestRecordOwnUploadNoneId:
    """upload_save_file's own-upload attribution skips a result with no save id."""

    def test_own_upload_unchanged_when_upload_result_has_no_id(self, tmp_path):
        """An upload whose server response carries no id leaves own_upload_ids alone."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)
        # Pre-seed own_upload_ids to assert it stays unchanged.
        _seed_save_state_dict(
            svc,
            42,
            {
                "files": {},
                "system": "gba",
                "active_slot": "default",
                "own_upload_ids": [50, 51],
            },
        )

        # Force the upload to return a result with no id (the None-guard path).
        fake.upload_save = lambda *a, **k: {"updated_at": "2026-01-01T00:00:00Z"}

        state = _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba")

        assert state.own_upload_ids == [50, 51]


_PROGRESS = b"progress" + b"\xff" * 24  # 32 bytes, content size 8


class _TriesThrice:
    """A retry strategy that calls the wrapped function up to three times."""

    def is_retryable(self, exc: Exception) -> bool:
        del exc
        return True

    def with_retry(self, fn, *args, max_attempts: int = 3, base_delay: int = 1, **kwargs):
        del base_delay
        for attempt in range(max_attempts):
            try:
                return fn(*args, **kwargs)
            except Exception:
                if attempt == max_attempts - 1:
                    raise
        raise AssertionError("unreachable")


def _head(content: bytes, *, content_hash: str | None = None) -> dict[str, Any]:
    head = _server_save(save_id=100, file_size_bytes=len(content), updated_at="2026-02-17T06:00:00Z")
    head["content_hash"] = content_hash if content_hash is not None else hashlib.md5(content).hexdigest()
    return head


def _confirm_calls(fake) -> list[tuple[int, str]]:
    return [c[1] for c in fake.call_log if c[0] == "confirm_download"]


def _transfers(fake) -> list[str]:
    return [c[0] for c in fake.call_log if c[0] in ("upload_save", "download_save_content", "download_save")]


class TestAnAdoptOfTheHead:
    """romm-gavel 2.0.0: a local file identical to the head is adopted, never transferred (#2525)."""

    def _setup(self, tmp_path, *, content: bytes = _PROGRESS, current: bool, head_hash: str | None = None, retry=None):
        overrides = {"retry": retry} if retry is not None else {}
        svc, fake = make_service(tmp_path, **overrides)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=content)
        fake.saves[100] = _head(content, content_hash=head_hash)
        if current:
            fake.stage_device_sync(100, "device-1", "2026-02-17T06:00:00Z")
        _seed_save_state(svc, 42, RomSaveSyncState(active_slot=None))
        return svc, fake, save_path

    def test_a_current_device_without_a_baseline_and_a_differing_local_file_gets_a_conflict(self, tmp_path):
        svc, fake, _path = self._setup(tmp_path, current=True, head_hash="someone-elses-bytes")

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert (uploaded, downloaded, errors) == (0, 0, [])
        assert [c["server_save_id"] for c in conflicts] == [100]
        assert _transfers(fake) == []
        assert "pokemon.srm" not in _require_save_state(svc, 42).files

    def test_an_identical_file_beside_a_head_this_device_is_not_current_on_is_adopted_and_acknowledged(self, tmp_path):
        svc, fake, save_path = self._setup(tmp_path, current=False)

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert (uploaded, downloaded, errors, conflicts) == (0, 0, [], [])
        assert _transfers(fake) == []
        assert _confirm_calls(fake) == [(100, "device-1")]
        assert not (save_path.parent / ".romm-backup").exists()
        assert save_path.read_bytes() == _PROGRESS
        fs = _require_save_state(svc, 42).files["pokemon.srm"]
        assert fs.tracked_save_id == fs.last_sync_server_save_id == 100
        assert fs.last_sync_hash == fs.last_sync_server_hash == hashlib.md5(_PROGRESS).hexdigest()
        assert fs.last_sync_server_updated_at == "2026-02-17T06:00:00Z"
        assert (fs.last_sync_server_size, fs.last_sync_local_size, fs.last_sync_content_size) == (32, 32, 8)
        assert fs.last_sync_local_mtime == os.path.getmtime(save_path)

    def test_an_adopt_of_a_head_this_device_is_current_on_sends_no_acknowledgement(self, tmp_path):
        svc, fake, _path = self._setup(tmp_path, current=True)

        assert _do_sync(svc, 42) == (0, 0, [], [])

        assert _confirm_calls(fake) == []
        assert _require_save_state(svc, 42).files["pokemon.srm"].tracked_save_id == 100

    def test_a_failed_acknowledgement_records_nothing_and_is_reported_and_the_next_sync_decides_again(
        self, tmp_path, monkeypatch
    ):
        svc, fake, _path = self._setup(tmp_path, current=False)
        earlier = FileSyncState(tracked_save_id=90, last_sync_hash="an-earlier-baseline", last_sync_local_size=32)
        _seed_save_state(svc, 42, RomSaveSyncState(files={"pokemon.srm": earlier}))
        failure = RommApiError("upstream 502")
        monkeypatch.setattr(fake, "confirm_download", _raising(fake, failure))

        uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

        assert (uploaded, downloaded, conflicts) == (0, 0, [])
        assert errors == [f"pokemon.srm: {classify_error(failure)[1]}"]
        assert _require_save_state(svc, 42).files["pokemon.srm"] == earlier
        assert _transfers(fake) == []

        monkeypatch.undo()
        assert _do_sync(svc, 42) == (0, 0, [], [])
        assert _require_save_state(svc, 42).files["pokemon.srm"].tracked_save_id == 100

    def test_the_acknowledgement_goes_through_the_retry_strategy(self, tmp_path, monkeypatch):
        svc, fake, _path = self._setup(tmp_path, current=False, retry=_TriesThrice())
        attempts = []
        confirm = fake.confirm_download

        def fails_twice(save_id, device_id):
            attempts.append(save_id)
            if len(attempts) < 3:
                raise RommApiError("upstream 502")
            return confirm(save_id, device_id)

        monkeypatch.setattr(fake, "confirm_download", fails_twice)

        assert _do_sync(svc, 42) == (0, 0, [], [])

        assert attempts == [100, 100, 100]
        assert _require_save_state(svc, 42).files["pokemon.srm"].tracked_save_id == 100

    def test_a_file_changed_since_the_decision_is_not_recorded_as_the_head(self, tmp_path):
        svc, fake, save_path = self._setup(tmp_path, current=True)
        state = RomSaveSyncState()

        recorded = svc._save_transfer.record_server_head(
            state, "pokemon.srm", fake.saves[100], str(save_path), "the-hash-the-decision-saw", system="gba"
        )

        assert recorded is False
        assert state.files == {}

    def test_a_file_changed_after_the_decision_is_neither_acknowledged_nor_recorded(self, tmp_path):
        svc, fake, save_path = self._setup(tmp_path, current=False)
        head = fake.saves[100]
        decided_on = hashlib.md5(_PROGRESS).hexdigest()
        save_path.write_bytes(b"written after the decision")
        state = RomSaveSyncState()
        errors: list[str] = []

        direction = svc._sync_engine._matrix._dispatch_sync_action(
            Skip(reason="synced", adopt_baseline=True, server_save=head, acknowledge=True),
            ctx=RomDispatchContext(
                rom_id=42,
                save_state=state,
                device_id=LiveDeviceId("device-1"),
                rom_name="pokemon",
                save_names=("pokemon.srm",),
                saves_dir=str(save_path.parent),
                system="gba",
                core_so=None,
            ),
            filename="pokemon.srm",
            local_path=str(save_path),
            local_hash=decided_on,
            last_sync_hash=None,
            last_sync_server_hash=None,
            server_candidates=[head],
            options=SyncRunOptions(),
            sink=DispatchSink(errors=errors, conflicts=[]),
        )

        assert direction is None
        assert errors == []
        assert _confirm_calls(fake) == []
        assert state.files == {}

    def test_a_fresh_install_s_adopt_leaves_the_rom_state_a_download_would_have(self, tmp_path):
        def after_first_sync(root, *, local: bool) -> tuple[Any, ...]:
            svc, fake = make_service(root)
            _enable_sync_with_device(svc)
            _install_rom(svc, root)
            if local:
                _create_save(root, content=_PROGRESS)
            fake.saves[100] = _head(_PROGRESS)
            fake.set_server_save_content(100, _PROGRESS)

            uploaded, downloaded, errors, conflicts = _do_sync(svc, 42)

            assert (uploaded, errors, conflicts) == (0, [], [])
            assert downloaded == (0 if local else 1)
            state = _require_save_state(svc, 42)
            assert state.files["pokemon.srm"].tracked_save_id == 100
            return state.active_slot, state.slots, state.system, state.emulator, state.last_synced_core

        adopted = after_first_sync(tmp_path / "adopt", local=True)
        downloaded = after_first_sync(tmp_path / "download", local=False)

        assert adopted == downloaded
        assert adopted[0] == "autosave"
        assert adopted[2] == "gba"


def _raising(fake, exc: Exception):
    def confirm_download(save_id, device_id):
        fake.call_log.append(("confirm_download", (save_id, device_id), {}))
        raise exc

    return confirm_download


class TestTheContentSizeAtTheBoundaries:
    """The content size is supplied to the core and recorded at every sync boundary (#2525)."""

    def _setup(self, tmp_path, *, content: bytes, current: bool, files: dict[str, FileSyncState]):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=content)
        fake.saves[100] = _head(b"the head's bytes")
        if current:
            fake.stage_device_sync(100, "device-1", "2026-02-17T06:00:00Z")
        _seed_save_state(svc, 42, RomSaveSyncState(files=files))
        return svc, fake, save_path

    def test_an_edit_that_only_dropped_trailing_fill_uploads_once_a_content_size_is_recorded(self, tmp_path):
        edited = b"edited!!" + b"\xff" * 56  # 64 bytes, content size 8
        recorded = FileSyncState(
            tracked_save_id=100, last_sync_hash="the-baseline", last_sync_local_size=256, last_sync_content_size=8
        )
        svc, _fake, _path = self._setup(tmp_path, content=edited, current=True, files={"pokemon.srm": recorded})

        uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert (uploaded, errors, conflicts) == (1, [], [])
        assert _require_save_state(svc, 42).files["pokemon.srm"].last_sync_content_size == 8

    def test_without_a_recorded_content_size_the_same_edit_is_judged_by_its_raw_size(self, tmp_path):
        edited = b"edited!!" + b"\xff" * 56
        recorded = FileSyncState(tracked_save_id=100, last_sync_hash="the-baseline", last_sync_local_size=256)
        svc, _fake, _path = self._setup(tmp_path, content=edited, current=True, files={"pokemon.srm": recorded})

        uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert (uploaded, errors) == (0, [])
        assert [c["server_save_id"] for c in conflicts] == [100]

    def test_a_download_records_the_content_size_of_the_bytes_it_wrote(self, tmp_path):
        svc, fake, save_path = self._setup(tmp_path, content=b"", current=False, files={})
        save_path.unlink()
        fake.set_server_save_content(100, b"head" + b"\x00" * 28)

        _uploaded, downloaded, errors, _conflicts = _do_sync(svc, 42)

        assert (downloaded, errors) == (1, [])
        fs = _require_save_state(svc, 42).files["pokemon.srm"]
        assert (fs.last_sync_local_size, fs.last_sync_content_size) == (32, 4)

    def test_a_file_unchanged_since_its_baseline_gets_its_content_size_recorded(self, tmp_path):
        unchanged = b"progress" + b"\x00" * 120
        recorded = FileSyncState(
            tracked_save_id=100,
            last_sync_hash=hashlib.md5(unchanged).hexdigest(),
            last_sync_server_hash=hashlib.md5(b"the head's bytes").hexdigest(),
            last_sync_local_size=128,
        )
        svc, _fake, _path = self._setup(tmp_path, content=unchanged, current=True, files={"pokemon.srm": recorded})

        assert _do_sync(svc, 42) == (0, 0, [], [])

        fs = _require_save_state(svc, 42).files["pokemon.srm"]
        assert fs.last_sync_content_size == 8
        assert fs == replace(recorded, last_sync_content_size=8)

    def test_a_file_edited_since_its_baseline_gets_no_content_size_from_a_sync_that_records_nothing(self, tmp_path):
        recorded = FileSyncState(tracked_save_id=90, last_sync_hash="the-baseline", last_sync_local_size=128)
        svc, _fake, _path = self._setup(
            tmp_path, content=b"edited" + b"\x00" * 122, current=False, files={"pokemon.srm": recorded}
        )

        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert [c["server_save_id"] for c in conflicts] == [100]
        assert _require_save_state(svc, 42).files["pokemon.srm"].last_sync_content_size is None


# Each shape a save answer can state ``pokemon.srm``'s size in — one group per
# claim, listing that file — and the ``size_fixed`` the decision is handed for
# it; ``_ABSENT`` is the key left out.
_ABSENT = object()
_CLAIMS: dict[str, tuple[tuple[bool | None, ...], object]] = {
    "fixed": ((True,), True),
    "not-fixed": ((False,), False),
    "null": ((None,), _ABSENT),
    "no-group": ((), _ABSENT),
    "two-groups-agree": ((True, True), True),
    "two-groups-disagree": ((True, False), _ABSENT),
}


class TestTheSizeClaimReachesTheDecision:
    """Each save file's ``size_fixed`` reaches the decision as the save answer states it (#2538 D3, D4).

    The sync and the save status's preview walk the same matrix with the same
    synced components, so both hand the decision the same claim. A file two
    groups list is two components, and so two decisions; what is pinned here is
    the claim each is handed, not how many there are.
    """

    @staticmethod
    def _setup(
        tmp_path,
        claims: tuple[bool | None, ...],
        *,
        elsewhere: tuple[tuple[str, bool | None], ...] = (),
    ):
        """An answer whose groups list ``pokemon.srm`` once per claim in *claims*.

        *elsewhere* adds one group per ``(name, claim)`` in another directory.
        """
        decided: list[dict[str, Any] | None] = []

        def recording(**kwargs: Any):
            decided.append(kwargs["local_file"])
            return _GAVEL.compute_sync_action(**kwargs)

        svc, fake = make_service(tmp_path, compute_sync_action=recording)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        saves_dir = str(tmp_path / "saves" / "gba")
        groups = tuple(
            SaveGroup(
                directory=saves_dir,
                files=("pokemon.srm",),
                role="battery",
                granularity="per-game-file",
                size_fixed=claim,
            )
            for claim in claims
        ) + tuple(
            SaveGroup(
                directory=str(tmp_path / "elsewhere"),
                files=(name,),
                role="battery",
                granularity="per-game-file",
                size_fixed=claim,
            )
            for name, claim in elsewhere
        )
        save_locations = svc._rom_info._save_locations
        assert isinstance(save_locations, FakeSaveLocationReader)
        save_locations.answer_with(
            "gba",
            build_save_answer(
                emulator="mGBA",
                directory=saves_dir,
                backing_directory=None,
                granularity="per-game-file",
                needs=(),
                file_set_state="declared",
                files=("pokemon.srm",),
                groups=groups,
                caveats=(),
                content_installed=True,
            ),
        )
        return svc, fake, decided

    @staticmethod
    def _stated(local_file: dict[str, Any] | None) -> object:
        assert local_file is not None
        return local_file.get("size_fixed", _ABSENT)

    @pytest.mark.parametrize("shape", sorted(_CLAIMS))
    def test_the_sync_hands_the_decision_the_files_claim(self, tmp_path, shape: str):
        claims, expected = _CLAIMS[shape]
        svc, _fake, decided = self._setup(tmp_path, claims)
        _create_save(tmp_path, content=_PROGRESS)

        _do_sync(svc, 42)

        assert decided
        assert {self._stated(local_file) for local_file in decided} == {expected}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("shape", sorted(_CLAIMS))
    async def test_the_save_status_hands_the_decision_the_files_claim(self, tmp_path, shape: str):
        claims, expected = _CLAIMS[shape]
        svc, _fake, decided = self._setup(tmp_path, claims)
        _create_save(tmp_path, content=_PROGRESS)

        await svc.get_save_status(42)

        assert decided
        assert {self._stated(local_file) for local_file in decided} == {expected}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["sync", "status"])
    async def test_a_claim_from_another_directory_is_never_lent_to_the_file(self, tmp_path, path: str):
        # Both files are probed in the answer's own directory. ``pokemon.srm``
        # there is fixed whatever a group elsewhere says about a file of that
        # name; ``pokemon.sav`` there is in no group of that directory at all, so
        # nothing is established about it.
        svc, _fake, decided = self._setup(tmp_path, (True,), elsewhere=(("pokemon.srm", False), ("pokemon.sav", True)))
        _create_save(tmp_path, content=_PROGRESS, ext=".srm")
        _create_save(tmp_path, content=_PROGRESS, ext=".sav")

        if path == "sync":
            _do_sync(svc, 42)
        else:
            await svc.get_save_status(42)

        stated = {(local_file or {})["filename"]: self._stated(local_file) for local_file in decided}
        assert stated == {"pokemon.srm": True, "pokemon.sav": _ABSENT}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("claim", "conflicts"), [(True, [100]), (None, [])])
    async def test_a_fixed_size_file_cut_short_conflicts_in_the_sync_and_in_the_status_alike(
        self, tmp_path, claim: bool | None, conflicts: list[int]
    ):
        # 100 bytes of content where 128 were recorded: no shrink under 50%, so
        # only the fixed size makes it a cut-short file. The unknown claim is
        # the control, which uploads it as an edit.
        svc, fake, _decided = self._setup(tmp_path, (claim,))
        _create_save(tmp_path, content=b"\x01" * 100)
        fake.saves[100] = _head(b"the head's bytes")
        fake.stage_device_sync(100, "device-1", "2026-02-17T06:00:00Z")
        _seed_save_state(
            svc,
            42,
            RomSaveSyncState(
                files={
                    "pokemon.srm": FileSyncState(
                        tracked_save_id=100,
                        last_sync_hash="the-baseline",
                        last_sync_local_size=128,
                        last_sync_content_size=128,
                    )
                }
            ),
        )

        status = await svc.get_save_status(42)
        uploaded, _downloaded, errors, synced = _do_sync(svc, 42)

        assert [c["server_save_id"] for c in status["conflicts"]] == conflicts
        assert [c["server_save_id"] for c in synced] == conflicts
        assert (uploaded, errors) == (0 if conflicts else 1, [])
