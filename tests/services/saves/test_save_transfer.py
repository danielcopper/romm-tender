"""Tests for SaveTransfer — moving one save file between this device and RomM and recording what moved."""

from __future__ import annotations

import hashlib
import os
from typing import Any

import pytest

from domain.live_device_id import LiveDeviceId
from domain.rom_save_sync_state import RomSaveSyncState
from lib.errors import (
    DeviceWriteError,
    RommAcknowledgementNotTaken,
    RommApiError,
    RommConflictError,
    RommConnectionError,
    RommTimeoutError,
)
from services.saves._sync_stop import SyncStop, SyncStopped
from tests.services.saves._helpers import (
    _create_save,
    _do_upload,
    _enable_sync_with_device,
    _install_rom,
    _server_save,
    _set_device_id,
    make_service,
    rom_save_sync_state_from_dict,
)


class TestFetchingAServerCopy:
    """A server copy lands in the file it is asked into, names no device and records nothing."""

    def test_the_copy_is_left_in_place_and_its_hash_answered(self, tmp_path):
        svc, fake = make_service(tmp_path)
        fake.saves[100] = _server_save(save_id=100)
        fake.set_server_save_content(100, b"server bytes")
        tmp = tmp_path / "pokemon.srm.tmp"

        answered = svc._save_transfer.fetch_server_copy(100, str(tmp))

        assert answered == hashlib.md5(b"server bytes").hexdigest()
        assert tmp.read_bytes() == b"server bytes"
        assert [c for c in fake.call_log if c[0].startswith("download_save")] == [
            ("download_save", (100, str(tmp)), {})
        ]

    @pytest.mark.parametrize(
        "failure",
        [RommConnectionError("offline"), DeviceWriteError(28, "No space left on device")],
        ids=["romm-unreachable", "this-device-cannot-write"],
    )
    def test_a_fetch_that_breaks_off_removes_what_it_wrote_and_raises_on(self, tmp_path, failure):
        svc, fake = make_service(tmp_path)
        tmp = tmp_path / "pokemon.srm.tmp"

        def partial_download(save_id: int, dest_path: str) -> None:
            with open(dest_path, "wb") as f:
                f.write(b"half a save")
            raise failure

        fake.download_save = partial_download  # type: ignore[method-assign]

        with pytest.raises(type(failure)) as caught:
            svc._save_transfer.fetch_server_copy(100, str(tmp))

        assert caught.value is failure
        assert not tmp.exists()


class TestRecordTransferredFile:
    """Tests for SaveTransfer._record_transferred_file, the sync-state writer a download and an upload share."""

    def test_creates_proper_entry(self, tmp_path):
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path)
        server_resp = {"id": 200, "updated_at": "2026-02-17T15:00:00Z"}

        state = RomSaveSyncState()
        svc._save_transfer._record_transferred_file(state, "pokemon.srm", server_resp, str(save_file), "gba")

        entry = state.files["pokemon.srm"]
        assert entry.last_sync_hash == svc._save_file_store.checksum_md5(str(save_file))
        assert entry.last_sync_at is not None
        assert entry.last_sync_server_save_id == 200

    def test_records_server_content_hash_from_response(self, tmp_path):
        """#1468 — the server response's ``content_hash`` (RomM's own digest of the
        bytes) is stored as ``last_sync_server_hash``, the provenance anchor."""
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path)
        server_resp = {"id": 200, "updated_at": "2026-02-17T15:00:00Z", "content_hash": "srv-abc123"}

        state = RomSaveSyncState()
        svc._save_transfer._record_transferred_file(state, "pokemon.srm", server_resp, str(save_file), "gba")

        assert state.files["pokemon.srm"].last_sync_server_hash == "srv-abc123"

    def test_records_none_server_hash_when_response_lacks_content_hash(self, tmp_path):
        """#1468 — a response without ``content_hash`` records ``None`` (never a
        fabricated or locally-computed value); the identity check falls back to parity."""
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path)
        server_resp = {"id": 200, "updated_at": "2026-02-17T15:00:00Z"}  # no content_hash

        state = RomSaveSyncState()
        svc._save_transfer._record_transferred_file(state, "pokemon.srm", server_resp, str(save_file), "gba")

        assert state.files["pokemon.srm"].last_sync_server_hash is None

    def test_creates_entry_with_new_fields(self, tmp_path):
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path)
        server_resp = {"id": 200, "updated_at": "2026-02-17T15:00:00Z"}

        state = RomSaveSyncState()
        svc._save_transfer._record_transferred_file(
            state,
            "pokemon.srm",
            server_resp,
            str(save_file),
            "gba",
            emulator_tag="retroarch-mgba",
            core_so="mgba_libretro",
        )

        assert state.emulator == "retroarch-mgba"
        assert state.last_synced_core == "mgba_libretro"
        # No default_slot passed → brand-new state seeds the canonical default ("autosave").
        assert state.active_slot == "autosave"

        file_state = state.files["pokemon.srm"]
        assert file_state.tracked_save_id == 200
        assert file_state.last_sync_server_save_id == 200

    def test_updates_emulator_on_existing_entry(self, tmp_path):
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path)
        # Pre-populate with old emulator tag
        state = rom_save_sync_state_from_dict(
            {
                "files": {},
                "emulator": "retroarch",
                "system": "gba",
                "last_synced_core": None,
                "active_slot": "default",
            }
        )
        server_resp = {"id": 200, "updated_at": "2026-02-17T15:00:00Z"}

        svc._save_transfer._record_transferred_file(
            state,
            "pokemon.srm",
            server_resp,
            str(save_file),
            "gba",
            emulator_tag="retroarch-mgba",
            core_so="mgba_libretro",
        )

        assert state.emulator == "retroarch-mgba"
        assert state.last_synced_core == "mgba_libretro"

    def test_core_so_none_does_not_overwrite(self, tmp_path):
        """core_so=None should not reset an already-set last_synced_core."""
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path)
        state = rom_save_sync_state_from_dict(
            {
                "files": {},
                "emulator": "retroarch-mgba",
                "system": "gba",
                "last_synced_core": "mgba_libretro",
                "active_slot": "default",
            }
        )
        server_resp = {"id": 200, "updated_at": "2026-02-17T15:00:00Z"}

        svc._save_transfer._record_transferred_file(
            state,
            "pokemon.srm",
            server_resp,
            str(save_file),
            "gba",
            emulator_tag="retroarch",
        )

        # last_synced_core unchanged because core_so=None
        assert state.last_synced_core == "mgba_libretro"

    def test_writes_last_sync_local_mtime_as_float(self, tmp_path):
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"\x00" * 1024)
        local_path = str(save_file)
        server_response = _server_save()

        state = RomSaveSyncState()
        svc._save_transfer._record_transferred_file(state, "pokemon.srm", server_response, local_path, "gba")

        file_state = state.files["pokemon.srm"]
        assert isinstance(file_state.last_sync_local_mtime, float)
        assert file_state.last_sync_local_mtime == pytest.approx(os.path.getmtime(local_path))

    def test_writes_last_sync_local_size_as_int(self, tmp_path):
        svc, _ = make_service(tmp_path)
        save_file = _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"\x00" * 2048)
        local_path = str(save_file)
        server_response = _server_save()

        state = RomSaveSyncState()
        svc._save_transfer._record_transferred_file(state, "pokemon.srm", server_response, local_path, "gba")

        file_state = state.files["pokemon.srm"]
        assert isinstance(file_state.last_sync_local_size, int)
        assert file_state.last_sync_local_size == 2048

    def test_skips_baseline_for_missing_file(self, tmp_path):
        """A missing local file yields an empty hash → no untrackable baseline (invariant 1)."""
        svc, _ = make_service(tmp_path)
        local_path = str(tmp_path / "saves" / "gba" / "missing.srm")
        server_response = _server_save()

        state = RomSaveSyncState()
        svc._save_transfer._record_transferred_file(state, "missing.srm", server_response, local_path, "gba")

        # No baseline is recorded — the aggregate rejects a hash-less file entry.
        assert "missing.srm" not in state.files


class TestServerHashBaselineWriterFlows:
    """#1468 — each transfer flow records the SERVER's content_hash as the provenance
    anchor, with honest provenance per flow (upload response vs adopted server save)."""

    def test_upload_flow_records_server_content_hash_from_response(self, tmp_path):
        """The upload flow records the upload RESPONSE's ``content_hash`` (RomM's
        digest of the bytes it received), not a locally-recomputed value."""
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_file = _create_save(tmp_path)

        state = _do_upload(svc, 42, str(save_file), "pokemon.srm", "gba")

        # The fake server returns content_hash = its own hash of the received bytes.
        expected = svc._save_file_store.content_hash(str(save_file))
        assert state.files["pokemon.srm"].last_sync_server_hash == expected

    def test_download_flow_records_adopted_server_save_content_hash(self, tmp_path):
        """The download flow records the ADOPTED server save's ``content_hash``."""
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        saves_dir = str(tmp_path / "saves" / "gba")
        server_save = _server_save(save_id=100)
        server_save["content_hash"] = "srv-download-hash"

        state = RomSaveSyncState()
        svc._save_transfer.download_save_file(
            server_save, saves_dir, "pokemon.srm", state, LiveDeviceId("device-1"), "gba"
        )

        assert state.files["pokemon.srm"].last_sync_server_hash == "srv-download-hash"

    def test_download_flow_records_none_when_server_save_lacks_content_hash(self, tmp_path):
        """A server save with no ``content_hash`` records ``None`` (parity fallback)."""
        svc, _ = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        saves_dir = str(tmp_path / "saves" / "gba")
        server_save = _server_save(save_id=100)  # no content_hash

        state = RomSaveSyncState()
        svc._save_transfer.download_save_file(
            server_save, saves_dir, "pokemon.srm", state, LiveDeviceId("device-1"), "gba"
        )

        assert state.files["pokemon.srm"].last_sync_server_hash is None


class TestConfirmDownloadAfterSync:
    """Verify the device's last_synced_at ends up registered with RomM after each
    upload (PUT/POST) and download.

    is_current is computed server-side as
    ``device_save_sync.last_synced_at >= save.updated_at``. ``add_save`` (POST)
    and ``update_save`` (PUT) both upsert the calling device's
    ``last_synced_at = updated_at`` when the request names a device, so a normal
    upload leaves us current without a follow-up ack — the POST response proves
    it and the upload's own acknowledgement skips the redundant round-trip (#1458). When
    a content-dedup answer still needs the ack is in
    docs/architecture/save-file-sync-architecture.md, "POST and PUT both upsert
    the calling device's sync row". A download streams without RomM's
    optimistic mark and writes the row with ``confirm_download`` once the file
    is in place.
    """

    def test_upload_save_file_post_skips_confirm_when_response_current(self, tmp_path):
        """POST whose response proves us current → confirm_download is skipped (#1458).

        The add_save upsert already made us ``is_current`` on the new save, so the
        redundant ack is elided — but we still end up current on the next read.
        """
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)

        _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba")

        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        # FakeSaveApi mints a new save_id starting from 1000 on POST
        new_save_id = next(iter(fake.saves.values()))["id"]

        # The confirm round-trip is skipped — the upload response proved current.
        confirm_calls = [c for c in fake.call_log if c[0] == "confirm_download"]
        assert confirm_calls == []
        # ...yet the device is genuinely current on the new save (the upload
        # recorded the DeviceSaveSync row itself, so nothing is lost by skipping).
        listed = fake.list_saves(42, device_id="dev-1")
        our_sync = next(ds for ds in listed[0]["device_syncs"] if ds["device_id"] == "dev-1")
        assert our_sync["is_current"] is True
        assert new_save_id == listed[0]["id"]

    def test_upload_save_file_put_calls_confirm_download(self, tmp_path):
        """PUT (existing save_id) → confirm_download fires for that save_id."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)

        # Pre-existing tracked server save
        fake.saves[100] = _server_save(save_id=100, rom_id=42)
        server_save = fake.saves[100]

        _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba", server_save=server_save)

        upload_calls = [c for c in fake.call_log if c[0] == "upload_save"]
        assert len(upload_calls) == 1
        # PUT path: save_id kwarg passed to upload_save
        assert upload_calls[0][2]["save_id"] == 100

        confirm_calls = [c for c in fake.call_log if c[0] == "confirm_download"]
        assert len(confirm_calls) == 1
        assert confirm_calls[0][1] == (100, "dev-1")

    def test_upload_save_file_swallows_confirm_download_error(self, tmp_path):
        """confirm_download failure must NOT bubble — upload is reported successful.

        Driven down a content dedup onto an older save this device holds no sync
        row on: the answer reads it not current, so the ack still fires and the
        swallow-the-error contract stays exercised (#1458).
        """
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)

        # The POST goes to "autosave" (a brand-new ROM's fallback slot). Its head is
        # current for dev-1, so RomM's slot gate lets the POST through; the content
        # matches the older save there, which dev-1 holds no row on.
        fake.saves[200] = _server_save(save_id=200, rom_id=42, slot="autosave", updated_at="2026-03-01T00:00:00Z")
        fake.stage_device_sync(200, "dev-1", "2026-03-01T00:00:00Z")
        fake.saves[100] = _server_save(save_id=100, rom_id=42, slot="autosave")
        fake.arm_add_save_dedup(100)

        # Patch confirm_download to raise; the upload itself must still complete.
        original_confirm = fake.confirm_download

        def boom(save_id: int, device_id: str, **_kwargs: Any) -> dict[str, Any]:
            fake.call_log.append(("confirm_download", (save_id, device_id), {}))
            raise RommApiError("HTTP 500: Server Error", url="/api/saves/x/downloaded", method="POST")

        fake.confirm_download = boom  # type: ignore[method-assign]
        state = RomSaveSyncState()
        try:
            result = svc._save_transfer.upload_save_file(
                42, str(save_path), "pokemon.srm", state, LiveDeviceId("dev-1"), "gba", None
            )
        finally:
            fake.confirm_download = original_confirm  # type: ignore[method-assign]

        # Upload completed, returned a result with id, AND the file_state was updated.
        assert result.get("id") is not None
        confirm_calls = [c for c in fake.call_log if c[0] == "confirm_download"]
        assert len(confirm_calls) == 1
        # File state still recorded the upload (not blocked by confirm failure)
        file_state = state.files["pokemon.srm"]
        assert file_state.tracked_save_id is not None

    def test_download_save_file_streams_unmarked_and_acknowledges_once_placed(self, tmp_path, monkeypatch):
        """download_save_content names the device with optimistic=False, and
        confirm_download follows once the file is in place."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "dev-1")
        saves_dir = str(tmp_path / "saves" / "gba")
        os.makedirs(saves_dir, exist_ok=True)
        server_save = _server_save(save_id=99)
        placed_when_acknowledged: list[bool] = []
        confirm = fake.confirm_download

        def recording_confirm(save_id: int, device_id: str, **kwargs: Any) -> dict[str, Any]:
            placed_when_acknowledged.append(os.path.exists(os.path.join(saves_dir, "pokemon.srm")))
            return confirm(save_id, device_id, **kwargs)

        monkeypatch.setattr(fake, "confirm_download", recording_confirm)
        svc._save_transfer.download_save_file(
            server_save, saves_dir, "pokemon.srm", RomSaveSyncState(), LiveDeviceId("dev-1"), "gba"
        )

        dl_calls = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert len(dl_calls) == 1
        kwargs = dl_calls[0][2]
        assert kwargs["device_id"] == "dev-1"
        assert kwargs["optimistic"] is False
        assert [c[1] for c in fake.call_log if c[0] == "confirm_download"] == [(99, "dev-1")]
        assert placed_when_acknowledged == [True]


class TestEveryRequestCarriesTheContentHash:
    """#2664 D4/D5: uploads, in-place updates and acknowledgements name the hash of the file they are about."""

    def test_a_post_and_an_in_place_put_carry_the_uploaded_file_s_hash(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"first bytes")
        _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba")
        posted = next(iter(fake.saves.values()))
        save_path.write_bytes(b"second bytes")

        _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba", server_save=posted)

        uploads = [(c[2]["save_id"], c[2]["content_hash"]) for c in fake.call_log if c[0] == "upload_save"]
        assert uploads == [
            (None, hashlib.md5(b"first bytes").hexdigest()),
            (posted["id"], hashlib.md5(b"second bytes").hexdigest()),
        ]

    def test_the_upload_s_own_acknowledgement_carries_the_uploaded_file_s_hash(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"put bytes")
        fake.saves[100] = _server_save(save_id=100, rom_id=42)

        _do_upload(svc, 42, str(save_path), "pokemon.srm", "gba", server_save=fake.saves[100])

        acks = [(c[1], c[2]["content_hash"]) for c in fake.call_log if c[0] == "confirm_download"]
        assert acks == [((100, "device-1"), hashlib.md5(b"put bytes").hexdigest())]

    def test_a_download_s_acknowledgement_carries_the_placed_file_s_hash(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        fake.saves[100] = _server_save(save_id=100)
        fake.set_server_save_content(100, b"server bytes")

        svc._save_transfer.download_save_file(
            fake.saves[100],
            str(tmp_path / "saves" / "gba"),
            "pokemon.srm",
            RomSaveSyncState(),
            LiveDeviceId("device-1"),
            "gba",
        )

        acks = [(c[1], c[2]["content_hash"]) for c in fake.call_log if c[0] == "confirm_download"]
        assert acks == [((100, "device-1"), hashlib.md5(b"server bytes").hexdigest())]

    def test_an_adopt_s_acknowledgement_carries_the_local_file_s_hash(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        save_path = _create_save(tmp_path, content=b"identical")
        local_hash = hashlib.md5(b"identical").hexdigest()
        fake.saves[100] = {**_server_save(save_id=100), "content_hash": local_hash}

        adopted = svc._save_transfer.adopt_server_head(
            RomSaveSyncState(),
            "pokemon.srm",
            fake.saves[100],
            str(save_path),
            local_hash,
            device_id=LiveDeviceId("device-1"),
            system="gba",
        )

        assert adopted is True
        acks = [(c[1], c[2]["content_hash"]) for c in fake.call_log if c[0] == "confirm_download"]
        assert acks == [((100, "device-1"), local_hash)]


class TestAnAcknowledgementRomMDidNotTake:
    """#2664 D6: an answer still showing this device not current is acknowledged once more, and only once."""

    def _download(self, svc, fake, tmp_path) -> None:
        svc._save_transfer.download_save_file(
            fake.saves[100],
            str(tmp_path / "saves" / "gba"),
            "pokemon.srm",
            RomSaveSyncState(),
            LiveDeviceId("device-1"),
            "gba",
        )

    def test_a_row_left_without_a_hash_is_taken_by_the_second_acknowledgement(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        content = b"replaced in place"
        fake.saves[100] = {
            **_server_save(save_id=100, updated_at="2026-03-01T00:00:00Z"),
            "content_hash": hashlib.md5(content).hexdigest(),
        }
        fake.set_server_save_content(100, content)
        fake.stage_device_sync(100, "device-1", "2026-02-01T00:00:00Z", last_sync_server_hash="an-older-hash")

        self._download(svc, fake, tmp_path)

        assert [c[0] for c in fake.call_log if c[0] == "confirm_download"] == ["confirm_download"] * 2
        (listed,) = fake.list_saves(42, device_id="device-1")
        assert listed["device_syncs"][0]["is_current"] is True

    def test_one_still_not_taken_after_the_second_fails_with_the_file_in_place_and_nothing_recorded(
        self, tmp_path, monkeypatch
    ):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        save_path = _create_save(tmp_path, content=b"local bytes")
        fake.saves[100] = _server_save(save_id=100)
        fake.set_server_save_content(100, b"server bytes")
        acks: list[int] = []

        def not_taken(save_id: int, device_id: str, **_kwargs: Any) -> dict[str, Any]:
            acks.append(save_id)
            return {
                "id": save_id,
                "content_hash": hashlib.md5(b"server bytes").hexdigest(),
                "device_syncs": [{"device_id": device_id, "is_current": False}],
            }

        monkeypatch.setattr(fake, "confirm_download", not_taken)
        state = RomSaveSyncState()
        download = svc._save_transfer.download_save_file
        server_save, saves_dir, device_id = fake.saves[100], str(save_path.parent), LiveDeviceId("device-1")

        with pytest.raises(RommAcknowledgementNotTaken):
            download(server_save, saves_dir, "pokemon.srm", state, device_id, "gba")

        assert acks == [100, 100]
        assert save_path.read_bytes() == b"server bytes"
        assert state.files == {}

    def test_a_save_replaced_since_the_download_is_not_acknowledged_again(self, tmp_path, monkeypatch):
        """A second acknowledgement would mark this device current on bytes another client put there meanwhile."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        save_path = _create_save(tmp_path, content=b"local bytes")
        fake.saves[100] = {
            **_server_save(save_id=100, updated_at="2026-03-01T00:00:00Z"),
            "content_hash": hashlib.md5(b"server bytes").hexdigest(),
        }
        fake.set_server_save_content(100, b"server bytes")
        fake.stage_device_sync(100, "device-1", "2026-02-01T00:00:00Z", last_sync_server_hash="an-older-hash")
        download_content = fake.download_save_content

        def download_then_another_client_replaces_it(save_id, dest_path, **kwargs):
            download_content(save_id, dest_path, **kwargs)
            fake.saves[100]["updated_at"] = "2026-03-02T00:00:00Z"
            fake.saves[100]["content_hash"] = hashlib.md5(b"replaced meanwhile").hexdigest()

        monkeypatch.setattr(fake, "download_save_content", download_then_another_client_replaces_it)
        state = RomSaveSyncState()
        download = svc._save_transfer.download_save_file
        server_save, saves_dir, device_id = fake.saves[100], str(save_path.parent), LiveDeviceId("device-1")

        with pytest.raises(RommAcknowledgementNotTaken):
            download(server_save, saves_dir, "pokemon.srm", state, device_id, "gba")

        assert [c[0] for c in fake.call_log if c[0] == "confirm_download"] == ["confirm_download"]
        (listed,) = fake.list_saves(42, device_id="device-1")
        assert listed["device_syncs"][0]["is_current"] is False
        assert save_path.read_bytes() == b"server bytes"
        assert state.files == {}


_UPLOADED_HASH = "0123456789abcdef0123456789abcdef"


class TestTheUploadsOwnAcknowledgement:
    """``SaveTransfer._acknowledge_upload`` skips the ack only on a provably-current response.

    The upload response's ``device_syncs`` is the discriminator: skip the
    confirm round-trip only when THIS device is present with ``is_current=true``.
    Every other shape — dedup ``is_current=false``, a different device, a missing
    entry, no ``device_syncs`` at all, or a garbage shape — fails open and
    confirms (#1458). Each case asserts the presence/absence of the
    ``confirm_download`` call on the fake, never just that the call returned.
    """

    @staticmethod
    def _transfer_and_fake(tmp_path):
        svc, fake = make_service(tmp_path)
        return svc._save_transfer, fake

    @staticmethod
    def _confirmed(fake) -> bool:
        return any(c[0] == "confirm_download" for c in fake.call_log)

    def test_skips_confirm_when_this_device_is_current(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)
        response = {"id": 500, "device_syncs": [{"device_id": "dev-1", "is_current": True}]}

        transfer._acknowledge_upload(response, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert self._confirmed(fake) is False

    def test_confirms_when_this_device_not_current(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)
        response = {"id": 500, "device_syncs": [{"device_id": "dev-1", "is_current": False}]}

        transfer._acknowledge_upload(response, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert fake.call_log[-1] == ("confirm_download", (500, "dev-1"), {"content_hash": _UPLOADED_HASH})

    def test_confirms_when_only_a_different_device_is_current(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)
        response = {"id": 500, "device_syncs": [{"device_id": "other", "is_current": True}]}

        transfer._acknowledge_upload(response, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert self._confirmed(fake) is True

    def test_confirms_when_device_syncs_missing(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)

        transfer._acknowledge_upload({"id": 500}, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert self._confirmed(fake) is True

    def test_confirms_when_device_syncs_empty(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)

        transfer._acknowledge_upload({"id": 500, "device_syncs": []}, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert self._confirmed(fake) is True

    def test_confirms_when_device_syncs_not_a_list(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)

        transfer._acknowledge_upload({"id": 500, "device_syncs": "garbage"}, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert self._confirmed(fake) is True

    def test_confirms_when_is_current_not_strictly_true(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)
        # A truthy-but-not-True value is "unexpected shape" → fail open, confirm.
        response = {"id": 500, "device_syncs": [{"device_id": "dev-1", "is_current": "yes"}]}

        transfer._acknowledge_upload(response, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert self._confirmed(fake) is True

    def test_noop_when_no_upload_id(self, tmp_path):
        transfer, fake = self._transfer_and_fake(tmp_path)
        response = {"device_syncs": [{"device_id": "dev-1", "is_current": False}]}

        transfer._acknowledge_upload(response, LiveDeviceId("dev-1"), _UPLOADED_HASH)

        assert self._confirmed(fake) is False


class TestDedupToNonHeadUploadGuard:
    """``SaveTransfer.upload_save_file`` routes a dedup-to-non-head POST response through the
    409 backstop instead of recording a false ``synced`` baseline (#1482).

    RomM's ``add_save`` content-dedup can early-return an older matching save
    while a newer, different head still leads the slot — no new version, the
    foreign head stays authoritative. The automatic POST path passes its
    pre-upload ``list_saves`` snapshot as ``planned_group`` so the response can
    be classified; every other caller passes none and is inert.
    """

    def test_dedup_to_non_head_raises_conflict_without_baseline_or_confirm(self, tmp_path):
        """Non-head dedup → RommConflictError before any baseline / own-upload /
        confirm write (value-exact: nothing was stamped on the non-head save)."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "device-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"local reverted content")

        older = _server_save(save_id=100, slot="default", updated_at="2026-02-17T06:00:00Z")
        head = _server_save(save_id=200, slot="default", updated_at="2026-03-01T00:00:00Z")
        fake.saves[100] = older
        fake.saves[200] = head
        # Current on the head, so RomM's slot gate lets the POST through to the dedup.
        fake.stage_device_sync(200, "device-1", "2026-03-01T00:00:00Z")
        fake.arm_add_save_dedup(100)  # the POST dedups to the OLDER save

        state = RomSaveSyncState(active_slot="default")
        file_path, device_id = str(save_path), LiveDeviceId("device-1")
        with pytest.raises(RommConflictError):
            svc._save_transfer.upload_save_file(
                42,
                file_path,
                "pokemon.srm",
                state,
                device_id,
                "gba",
                None,
                planned_group=[older, head],
            )

        # No baseline recorded for the dedup response…
        assert "pokemon.srm" not in state.files
        # …no own-upload attribution, no slot promotion, and no confirm ack that
        # would falsely stamp currency on the non-head save.
        assert state.own_upload_ids is None
        assert not any(c[0] == "confirm_download" for c in fake.call_log)

    def test_dedup_to_head_records_baseline_without_a_confirm(self, tmp_path):
        """Dedup to the head itself is benign — baseline recorded on the head. RomM
        reaches its dedup only past the slot gate, so the device is already current
        on the head, the answer says so, and the #1458 confirm ack is not sent."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "device-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"head content")

        older = _server_save(save_id=100, slot="default", updated_at="2026-02-17T06:00:00Z")
        head = _server_save(save_id=200, slot="default", updated_at="2026-03-01T00:00:00Z")
        fake.saves[100] = older
        fake.saves[200] = head
        # Current on the head, so RomM's slot gate lets the POST through to the dedup.
        fake.stage_device_sync(200, "device-1", "2026-03-01T00:00:00Z")
        fake.arm_add_save_dedup(200)  # dedup to the HEAD → benign

        state = RomSaveSyncState(active_slot="default")
        result = svc._save_transfer.upload_save_file(
            42,
            str(save_path),
            "pokemon.srm",
            state,
            LiveDeviceId("device-1"),
            "gba",
            None,
            planned_group=[older, head],
        )

        assert result["id"] == 200
        assert state.files["pokemon.srm"].tracked_save_id == 200
        # The dedup answer shows this device current on the head → no ack.
        assert not any(c[0] == "confirm_download" for c in fake.call_log)

    def test_no_planned_group_leaves_guard_inert(self, tmp_path):
        """A caller that passes no ``planned_group`` (one that plans no listing)
        never triggers the guard, even on a non-head dedup shape — baseline is
        recorded as before."""
        svc, fake = make_service(tmp_path)
        _set_device_id(svc, "device-1")
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path, content=b"local content")

        older = _server_save(save_id=100, slot="default", updated_at="2026-02-17T06:00:00Z")
        head = _server_save(save_id=200, slot="default", updated_at="2026-03-01T00:00:00Z")
        fake.saves[100] = older
        fake.saves[200] = head
        # Current on the head, so RomM's slot gate lets the POST through to the dedup.
        fake.stage_device_sync(200, "device-1", "2026-03-01T00:00:00Z")
        fake.arm_add_save_dedup(100)  # non-head dedup, but no planned_group passed

        state = RomSaveSyncState(active_slot="default")
        result = svc._save_transfer.upload_save_file(
            42, str(save_path), "pokemon.srm", state, LiveDeviceId("device-1"), "gba", None
        )

        assert result["id"] == 100
        assert state.files["pokemon.srm"].tracked_save_id == 100


class TestHashServerCopyNonRetryable:
    """hash_server_copy swallows non-retryable RomM errors and returns None,
    and raises a fault of this device's. The retryable-raise path is already
    covered by TestResolveSyncConflict.test_resolve_keep_local_falls_back_*."""

    def test_hash_server_copy_returns_none_on_non_retryable_error_and_raises_a_device_fault(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)

        # download_save raises, and retry.is_retryable returns False (default
        # in _make_retry), so the matrix should swallow and return None.
        def _raise_on_download(save_id: int, dest_path: str) -> None:
            fake.call_log.append(("download_save", (save_id, dest_path), {}))
            raise RommApiError("permanent failure")

        fake.download_save = _raise_on_download  # type: ignore[method-assign]

        result = svc._save_transfer.hash_server_copy({"id": 100})
        assert result is None
        # download_save was attempted exactly once.
        download_calls = [c for c in fake.call_log if c[0] == "download_save"]
        assert len(download_calls) == 1

        def _cannot_write(save_id: int, dest_path: str) -> None:
            raise DeviceWriteError(28, "No space left on device")

        fake.download_save = _cannot_write  # type: ignore[method-assign]

        with pytest.raises(DeviceWriteError):
            svc._save_transfer.hash_server_copy({"id": 100})

    def test_hash_server_copy_returns_none_when_save_id_missing(self, tmp_path):
        """No save_id on the server-save dict → short-circuit to None (line 120)."""
        svc, _ = make_service(tmp_path)

        result = svc._save_transfer.hash_server_copy({"file_name": "x.srm"})
        assert result is None


class TestADownloadThatFailsLeavesNoTmp:
    """``SaveTransfer.download_save_file`` removes its ``.tmp`` on every failure and raises on."""

    @pytest.mark.parametrize(
        "failure",
        [
            RommTimeoutError("read timed out"),
            DeviceWriteError(28, "No space left on device"),
            RuntimeError("a bug"),
        ],
        ids=["stalled", "this-device-cannot-write", "any-other-fault"],
    )
    def test_a_download_that_breaks_off_leaves_no_tmp(self, tmp_path, failure):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        save_path = _create_save(tmp_path, content=b"local bytes")
        fake.saves[100] = _server_save(save_id=100)
        fake.break_download_on(100, failure)
        state = RomSaveSyncState()
        download = svc._save_transfer.download_save_file
        server_save, saves_dir_path, device_id = fake.saves[100], str(save_path.parent), LiveDeviceId("device-1")

        with pytest.raises(type(failure)) as caught:
            download(server_save, saves_dir_path, "pokemon.srm", state, device_id, "gba")

        assert caught.value is failure
        assert not (save_path.parent / "pokemon.srm.tmp").exists()
        assert save_path.read_bytes() == b"local bytes"
        assert state.files == {}

    def test_a_download_that_cannot_be_moved_into_place_leaves_no_tmp(self, tmp_path, monkeypatch):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)
        fake.saves[100] = _server_save(save_id=100)

        def failing_rename(_src: str, _dst: str) -> None:
            raise PermissionError("save directory is read-only")

        monkeypatch.setattr(svc._save_file_store, "rename", failing_rename)

        download = svc._save_transfer.download_save_file
        server_save, saves_dir_path, state = fake.saves[100], str(saves_dir), RomSaveSyncState()
        device_id = LiveDeviceId("device-1")
        with pytest.raises(PermissionError, match="read-only"):
            download(server_save, saves_dir_path, "pokemon.srm", state, device_id, "gba")

        assert not (saves_dir / "pokemon.srm.tmp").exists()

    def test_a_tmp_that_cannot_be_removed_leaves_the_download_s_own_failure_raised(self, tmp_path, monkeypatch):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        saves_dir = tmp_path / "saves" / "gba"
        saves_dir.mkdir(parents=True)
        fake.saves[100] = _server_save(save_id=100)
        failure = RommTimeoutError("read timed out")
        fake.break_download_on(100, failure)
        removals: list[str] = []

        def failing_remove(path: str) -> None:
            removals.append(path)
            raise PermissionError("save directory is read-only")

        monkeypatch.setattr(svc._save_file_store, "remove_file", failing_remove)

        download = svc._save_transfer.download_save_file
        server_save, saves_dir_path, state = fake.saves[100], str(saves_dir), RomSaveSyncState()
        device_id = LiveDeviceId("device-1")
        with pytest.raises(RommTimeoutError) as caught:
            download(server_save, saves_dir_path, "pokemon.srm", state, device_id, "gba")

        assert caught.value is failure
        assert removals == [str(saves_dir / "pokemon.srm.tmp")]


class TestADownloadItsStopEnded:
    """#2184 D4: a download whose stop was asked by the time it finished is never put in place."""

    def test_the_finished_download_is_discarded_and_the_local_file_left_as_it_was(self, tmp_path, monkeypatch):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        save_path = _create_save(tmp_path, content=b"local bytes")
        fake.saves[100] = _server_save(save_id=100)
        stop = SyncStop()
        download = fake.download_save_content

        def download_then_stop(save_id, dest_path, **kwargs):
            download(save_id, dest_path, **kwargs)
            stop.request()

        monkeypatch.setattr(fake, "download_save_content", download_then_stop)
        state = RomSaveSyncState()

        download_file = svc._save_transfer.download_save_file
        server_save, saves_dir, device_id = fake.saves[100], str(save_path.parent), LiveDeviceId("device-1")

        with pytest.raises(SyncStopped):
            download_file(server_save, saves_dir, "pokemon.srm", state, device_id, "gba", stop=stop)

        assert not (save_path.parent / "pokemon.srm.tmp").exists()
        assert save_path.read_bytes() == b"local bytes"
        assert not (save_path.parent / ".romm-backup").exists()
        assert state.files == {}
        assert stop.stopped_the_sync() is True
        assert not any(c[0] == "confirm_download" for c in fake.call_log)
        assert fake._device_sync_ledger == {}

    def test_a_stoppable_download_is_acknowledged_only_once_it_is_in_place(self, tmp_path, monkeypatch):
        """#2184 D8: no optimistic sync mark at the GET; the acknowledgement follows the rename."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        saves_dir = tmp_path / "saves" / "gba"
        fake.saves[100] = _server_save(save_id=100)
        steps: list[str] = []
        rename = svc._save_file_store.rename
        confirm = fake.confirm_download

        def recording_rename(src: str, dst: str) -> None:
            rename(src, dst)
            steps.append("placed")

        def recording_confirm(save_id: int, device_id: str, **kwargs: Any) -> dict[str, Any]:
            steps.append("acknowledged")
            return confirm(save_id, device_id, **kwargs)

        monkeypatch.setattr(svc._save_file_store, "rename", recording_rename)
        monkeypatch.setattr(fake, "confirm_download", recording_confirm)

        svc._save_transfer.download_save_file(
            fake.saves[100],
            str(saves_dir),
            "pokemon.srm",
            RomSaveSyncState(),
            LiveDeviceId("device-1"),
            "gba",
            stop=SyncStop(),
        )

        downloads = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert [c[2]["optimistic"] for c in downloads] == [False]
        assert steps == ["placed", "acknowledged"]
        assert fake._device_sync_ledger == {("device-1", 100): fake.saves[100]["updated_at"]}

    def test_an_acknowledgement_that_fails_leaves_the_file_in_place_with_its_backup_and_records_nothing(
        self, tmp_path, monkeypatch
    ):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        save_path = _create_save(tmp_path, content=b"local bytes")
        fake.saves[100] = _server_save(save_id=100)
        failure = RommApiError("acknowledgement refused")

        def failing_confirm(_save_id: int, _device_id: str, **_kwargs: Any) -> dict[str, Any]:
            raise failure

        monkeypatch.setattr(fake, "confirm_download", failing_confirm)
        state = RomSaveSyncState()

        download_file = svc._save_transfer.download_save_file
        server_save, saves_dir, device_id, stop = (
            fake.saves[100],
            str(save_path.parent),
            LiveDeviceId("device-1"),
            SyncStop(),
        )

        with pytest.raises(RommApiError) as caught:
            download_file(server_save, saves_dir, "pokemon.srm", state, device_id, "gba", stop=stop)

        assert caught.value is failure
        assert save_path.read_bytes() != b"local bytes"
        backups = list((save_path.parent / ".romm-backup").iterdir())
        assert [b.read_bytes() for b in backups] == [b"local bytes"]
        assert state.files == {}

    def test_a_stop_not_asked_places_the_download(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        saves_dir = tmp_path / "saves" / "gba"
        fake.saves[100] = _server_save(save_id=100)
        stop = SyncStop()
        state = RomSaveSyncState()

        svc._save_transfer.download_save_file(
            fake.saves[100], str(saves_dir), "pokemon.srm", state, LiveDeviceId("device-1"), "gba", stop=stop
        )

        assert (saves_dir / "pokemon.srm").exists()
        assert set(state.files) == {"pokemon.srm"}
        assert stop.stopped_the_sync() is False


class TestRecordServerHead:
    def test_record_server_head_records_the_head_as_the_baseline(self, tmp_path):
        """SaveTransfer.record_server_head records the head as the baseline on the passed aggregate."""
        svc, _ = make_service(tmp_path)
        save_path = _create_save(tmp_path, content=b"identical")
        local_hash = hashlib.md5(b"identical").hexdigest()
        head = {**_server_save(save_id=77, filename="pokemon.srm"), "content_hash": local_hash}

        state = RomSaveSyncState()
        recorded = svc._save_transfer.record_server_head(
            state, "pokemon.srm", head, str(save_path), local_hash, system="gba"
        )

        assert recorded is True
        assert (state.files["pokemon.srm"].tracked_save_id, state.files["pokemon.srm"].last_sync_hash) == (
            77,
            local_hash,
        )
