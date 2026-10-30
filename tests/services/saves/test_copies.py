"""Tests for SaveCopyService — the per-save copy-to-slot flow.

The copy takes one server save (a named slot or the legacy no-slot bucket)
and copies its content into a target slot, which becomes the ROM's active
slot with the copied save as its current save. The source save is never
deleted. A distinctive target name (never the default "autosave" fallback)
is used throughout so the tests prove the target value actually flows.
"""

import hashlib
from typing import Any

import pytest
from fakes.fake_save_location_reader import FakeSaveLocationReader

from domain.save_answer import save_shape_message, unestablished_answer
from lib.errors import DeviceWriteError, RommNotFoundError, RommRateLimitedError, RommTimeoutError
from tests.services.saves._helpers import (
    _create_save,
    _do_sync,
    _enable_sync_with_device,
    _file_md5,
    _install_rom,
    _no_save_directory,
    _require_save_state,
    _seed_save_state_dict,
    _server_save,
    _server_save_with_syncs,
    make_service,
)

# Distinctive target slot — never the "autosave" default, so a passing test
# proves the value flowed through rather than a fallback landing on the default.
TARGET = "promoted"


def _setup_configured(
    svc,
    tmp_path,
    *,
    active_slot: str = "autosave",
    tracked_id: int | None = 100,
    last_sync_hash: str | None = None,
    system: str = "gba",
) -> None:
    """Seed an installed, sync-enabled, CONFIRMED-slot ROM (rom 42).

    Mirrors the rollback suite's ``_setup_state`` but sets ``slot_confirmed`` so
    the copy flow's ``not_configured`` guard passes. ``last_sync_hash=None`` seeds
    no per-file baseline (the matrix falls back to its in-memory default).
    """
    _install_rom(svc, tmp_path, system=system)
    _enable_sync_with_device(svc)
    files = (
        {"pokemon.srm": {"tracked_save_id": tracked_id, "last_sync_hash": last_sync_hash}}
        if last_sync_hash is not None
        else {}
    )
    _seed_save_state_dict(
        svc,
        42,
        {
            "system": system,
            "active_slot": active_slot,
            "slot_confirmed": True,
            "files": files,
        },
    )


def _tracked_save(save_id: int, *, slot: str, updated_at: str = "2026-03-10T10:00:00Z") -> dict[str, Any]:
    """A slot head with our device flagged ``is_current`` — a clean matrix pre-flight."""
    return _server_save_with_syncs(
        save_id=save_id,
        slot=slot,
        updated_at=updated_at,
        device_syncs=[{"device_id": "device-1", "is_current": True, "last_synced_at": updated_at}],
    )


class TestCopySaveToSlotHappyPaths:
    """The three happy paths from the spec: legacy→new named, default→autosave, inactive→active."""

    @pytest.mark.asyncio
    async def test_no_slot_save_into_new_named_slot(self, tmp_path):
        """A legacy (slot:null) save copied into a brand-new named slot.

        The original scope of the issue: promote a no-slot save into a named
        slot. The new slot becomes active; the legacy source is preserved.
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        # Current-slot head (clean pre-flight) + a legacy no-slot save to copy.
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.set_server_save_content(10, b"legacy-save-bytes")

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "ok"}
        state = _require_save_state(svc, 42)
        assert state.active_slot == TARGET
        assert state.slot_confirmed is True
        # Source preserved (still a legacy no-slot save).
        assert 10 in fake.saves
        assert fake.saves[10].get("slot") is None
        # A new save landed in the target slot.
        assert [s for s in fake.saves.values() if s.get("slot") == TARGET]

    @pytest.mark.asyncio
    async def test_default_save_into_autosave(self, tmp_path):
        """A save in the old ``default`` slot copied onto ``autosave`` (#1529 manual path).

        ``default`` is the active slot; copying its save into ``autosave`` makes
        autosave active. Uses the real default target name here (the value under
        test IS "autosave"), distinct from the source "default".
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="default", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="default")

        result = await svc.copy_save_to_slot(42, 100, "autosave")

        assert result == {"status": "ok"}
        state = _require_save_state(svc, 42)
        assert state.active_slot == "autosave"
        assert state.slot_confirmed is True
        # Source in "default" preserved.
        assert fake.saves[100].get("slot") == "default"
        # A new save landed in autosave.
        assert [s for s in fake.saves.values() if s.get("slot") == "autosave"]

    @pytest.mark.asyncio
    async def test_inactive_slot_save_into_active_slot(self, tmp_path):
        """A save from an INACTIVE slot copied into the active slot.

        The active slot already holds a head we're synced to, so the target POST
        stacks cleanly (no 409). The inactive source save is preserved.
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        # We're current on the active head per the ledger, so a POST into autosave
        # does not 409 on it.
        fake.stage_device_sync(100, "device-1", "2026-03-10T10:00:00Z")
        # Inactive-slot source save.
        fake.saves[50] = _server_save(save_id=50, rom_id=42, slot="backup", updated_at="2026-02-01T10:00:00Z")

        result = await svc.copy_save_to_slot(42, 50, "autosave")

        assert result == {"status": "ok"}
        state = _require_save_state(svc, 42)
        assert state.active_slot == "autosave"
        # Source in the inactive slot preserved.
        assert fake.saves[50].get("slot") == "backup"
        # A new save was created (id from the fake's 1000+ range), distinct from
        # both the source and the pre-existing head.
        autosave_ids = {s["id"] for s in fake.saves.values() if s.get("slot") == "autosave"}
        assert autosave_ids - {100} != set()  # at least one NEW save id in autosave

    @pytest.mark.asyncio
    async def test_source_preserved_and_target_becomes_active(self, tmp_path):
        """Dedicated non-vacuous check: the source save survives AND the target is active."""
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")

        before_ids = set(fake.saves)
        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "ok"}
        # The source id is still present (a copy, never a move/delete).
        assert 10 in fake.saves
        # The target is now the ROM's active slot.
        assert _require_save_state(svc, 42).active_slot == TARGET
        # A brand-new save id appeared for the copy (the source was not mutated in place).
        assert set(fake.saves) - before_ids


class TestTheCopysDownloadIsNotAcknowledged:
    """#2664 D2: the copy's own POST records this device on RomM, so its download sends no acknowledgement."""

    @pytest.mark.asyncio
    async def test_the_download_of_the_source_save_sends_no_acknowledgement(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.set_server_save_content(10, b"legacy-save-bytes")

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "ok"}
        downloads = [c for c in fake.call_log if c[0] == "download_save_content"]
        assert [(c[1][0], c[2]["optimistic"]) for c in downloads] == [(10, False)]
        assert not any(c[0] == "confirm_download" and c[1][0] == 10 for c in fake.call_log)


class TestAnUploadThatFailsAfterTheDownload:
    """What the next sync does with a copy whose upload failed after the download."""

    COPIED = b"C" * 1024
    HEAD = b"H" * 1024

    def _a_copy_whose_upload_fails_once(self, tmp_path, monkeypatch, *, target_head: dict[str, Any] | None):
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path, content=b"current-slot-bytes")
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.set_server_save_content(10, self.COPIED)
        if target_head is not None:
            fake.saves[target_head["id"]] = target_head
            fake.set_server_save_content(target_head["id"], self.HEAD)
        working_upload = fake.upload_save
        failures = [RuntimeError("server 500")]

        def upload_failing_once(*args, **kwargs):
            if failures:
                raise failures.pop()
            return working_upload(*args, **kwargs)

        monkeypatch.setattr(fake, "upload_save", upload_failing_once)
        return svc, fake

    def _target_head(self) -> dict[str, Any]:
        head = _server_save(save_id=200, rom_id=42, slot=TARGET, updated_at="2026-03-01T10:00:00Z")
        head["content_hash"] = hashlib.md5(self.HEAD).hexdigest()
        return head

    def _a_copy_another_device_races(self, tmp_path, monkeypatch, *, target_head: dict[str, Any] | None, race):
        """A copy into a slot whose head, if any, this device is current on, with *race* run inside its upload.

        The check before the download passes; *race* is another device's
        upload into the target slot landing between that check and the copy's
        POST, which the fake's ``add_save`` gate then refuses with a 409.
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path, content=b"current-slot-bytes")
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.set_server_save_content(10, self.COPIED)
        if target_head is not None:
            fake.saves[target_head["id"]] = target_head
            fake.set_server_save_content(target_head["id"], self.HEAD)
            fake.stage_device_sync(target_head["id"], "device-1", target_head["updated_at"])
        working_upload = fake.upload_save
        races = [race]

        def upload_raced_once(*args, **kwargs):
            if races:
                races.pop()(fake)
            return working_upload(*args, **kwargs)

        monkeypatch.setattr(fake, "upload_save", upload_raced_once)
        return svc, fake

    @staticmethod
    def _another_device_adds_a_newer_save(fake) -> None:
        fake.seed_foreign_save(42, save_id=300, slot=TARGET, updated_at="2026-03-05T10:00:00Z", content=b"F" * 1024)

    @staticmethod
    def _another_device_changes_the_head(fake) -> None:
        fake.saves[200]["updated_at"] = "2026-03-05T10:00:00Z"
        if "content_hash" in fake.saves[200]:
            fake.saves[200]["content_hash"] = hashlib.md5(b"F" * 1024).hexdigest()
        fake.set_server_save_content(200, b"F" * 1024)
        fake.stage_device_sync(200, "device-B", "2026-03-05T10:00:00Z")

    @pytest.mark.asyncio
    async def test_the_next_sync_uploads_a_copy_into_an_existing_slot(self, tmp_path, monkeypatch):
        svc, fake = self._a_copy_whose_upload_fails_once(tmp_path, monkeypatch, target_head=self._target_head())
        fake.stage_device_sync(200, "device-1", "2026-03-01T10:00:00Z")

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert (errors, conflicts) == ([], [])
        new_ids = [i for i, s in fake.saves.items() if s.get("slot") == TARGET and i != 200]
        assert len(new_ids) == 1
        assert fake._save_content[new_ids[0]] == self.COPIED
        assert 200 in fake.saves
        assert _require_save_state(svc, 42).files["pokemon.srm"].tracked_save_id == new_ids[0]

    @pytest.mark.asyncio
    async def test_a_copy_under_half_the_target_head_s_size_makes_the_next_sync_a_conflict(self, tmp_path, monkeypatch):
        svc, fake = self._a_copy_whose_upload_fails_once(tmp_path, monkeypatch, target_head=self._target_head())
        fake.stage_device_sync(200, "device-1", "2026-03-01T10:00:00Z")
        fake.set_server_save_content(10, b"C" * 10)

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert [c["server_save_id"] for c in conflicts] == [200]
        assert [i for i, s in fake.saves.items() if s.get("slot") == TARGET] == [200]

    @pytest.mark.asyncio
    async def test_the_next_sync_uploads_a_copy_into_a_new_slot(self, tmp_path, monkeypatch):
        svc, fake = self._a_copy_whose_upload_fails_once(tmp_path, monkeypatch, target_head=None)

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert (errors, conflicts) == ([], [])
        new_ids = [i for i, s in fake.saves.items() if s.get("slot") == TARGET]
        assert len(new_ids) == 1
        assert fake._save_content[new_ids[0]] == self.COPIED

    @pytest.mark.asyncio
    async def test_a_target_head_this_device_never_synced_makes_the_next_sync_a_conflict(self, tmp_path, monkeypatch):
        svc, fake = self._a_copy_another_device_races(
            tmp_path, monkeypatch, target_head=self._target_head(), race=self._another_device_adds_a_newer_save
        )

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert [c["server_save_id"] for c in conflicts] == [300]
        assert sorted(i for i, s in fake.saves.items() if s.get("slot") == TARGET) == [200, 300]
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == self.COPIED

    @pytest.mark.asyncio
    async def test_a_target_head_another_device_moved_on_makes_the_next_sync_a_conflict(self, tmp_path, monkeypatch):
        svc, fake = self._a_copy_another_device_races(
            tmp_path, monkeypatch, target_head=self._target_head(), race=self._another_device_changes_the_head
        )

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert [c["server_save_id"] for c in conflicts] == [200]
        assert [i for i, s in fake.saves.items() if s.get("slot") == TARGET] == [200]
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == self.COPIED

    @pytest.mark.asyncio
    async def test_a_save_another_device_adds_to_a_slot_empty_at_the_check_makes_the_next_sync_a_conflict(
        self, tmp_path, monkeypatch
    ):
        svc, fake = self._a_copy_another_device_races(
            tmp_path, monkeypatch, target_head=None, race=self._another_device_adds_a_newer_save
        )

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert [c["server_save_id"] for c in conflicts] == [300]
        assert [i for i, s in fake.saves.items() if s.get("slot") == TARGET] == [300]
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == self.COPIED

    @pytest.mark.asyncio
    async def test_an_upload_failing_otherwise_into_an_empty_slot_keeps_the_download_s_baseline(
        self, tmp_path, monkeypatch
    ):
        svc, _fake = self._a_copy_whose_upload_fails_once(tmp_path, monkeypatch, target_head=None)

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"

        baseline = _require_save_state(svc, 42).files["pokemon.srm"]
        assert (baseline.tracked_save_id, baseline.last_sync_hash) == (10, hashlib.md5(self.COPIED).hexdigest())

    def _a_head_without_a_content_hash(self) -> dict[str, Any]:
        head = self._target_head()
        del head["content_hash"]
        return head

    @pytest.mark.parametrize("missing", ["content_hash", "id"])
    @pytest.mark.asyncio
    async def test_without_a_content_hash_or_id_a_head_this_device_is_current_on_makes_the_next_sync_a_conflict(
        self, tmp_path, monkeypatch, missing
    ):
        head = self._a_head_without_a_content_hash() if missing == "content_hash" else self._target_head()
        svc, fake = self._a_copy_whose_upload_fails_once(tmp_path, monkeypatch, target_head=head)
        fake.stage_device_sync(200, "device-1", "2026-03-01T10:00:00Z")
        listing = fake.list_saves
        copying = [missing == "id"]

        def listing_the_head_without_its_id_while_copying(*args, **kwargs):
            saves = listing(*args, **kwargs)
            if copying[0]:
                saves = [{k: v for k, v in s.items() if k != "id"} if s.get("id") == 200 else s for s in saves]
            return saves

        monkeypatch.setattr(fake, "list_saves", listing_the_head_without_its_id_while_copying)

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        copying[0] = False
        assert "pokemon.srm" not in _require_save_state(svc, 42).files
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert [c["server_save_id"] for c in conflicts] == [200]
        assert [i for i, s in fake.saves.items() if s.get("slot") == TARGET] == [200]
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == self.COPIED
        assert "pokemon.srm" not in _require_save_state(svc, 42).files

    @pytest.mark.parametrize(
        ("race", "conflicting", "in_target"),
        [("_another_device_adds_a_newer_save", 300, [200, 300]), ("_another_device_changes_the_head", 200, [200])],
        ids=["never-synced", "moved-on"],
    )
    @pytest.mark.asyncio
    async def test_without_a_content_hash_a_head_this_device_is_not_current_on_makes_the_next_sync_a_conflict(
        self, tmp_path, monkeypatch, race, conflicting, in_target
    ):
        svc, fake = self._a_copy_another_device_races(
            tmp_path, monkeypatch, target_head=self._a_head_without_a_content_hash(), race=getattr(self, race)
        )

        assert (await svc.copy_save_to_slot(42, 10, TARGET))["status"] == "upload_failed"
        _uploaded, _downloaded, errors, conflicts = _do_sync(svc, 42)

        assert errors == []
        assert [c["server_save_id"] for c in conflicts] == [conflicting]
        assert sorted(i for i, s in fake.saves.items() if s.get("slot") == TARGET) == in_target
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == self.COPIED


class TestCopySaveToSlotDedup:
    """The content-already-in-target dedup pre-check (no-churn guarantee)."""

    @pytest.mark.asyncio
    async def test_already_present_when_content_in_target_slot(self, tmp_path):
        """The chosen save's content is already in the target slot → refuse the copy.

        Copying content RomM would dedup server-side churns the tracked save for
        no gain. The pre-check surfaces the existing save's id and touches no copy
        state — no download, no new save, no make-current.
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        # A legacy source and a save already in the target slot share content.
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.saves[10]["content_hash"] = "SAME-CONTENT"
        fake.saves[200] = _server_save(save_id=200, rom_id=42, slot=TARGET, updated_at="2026-01-01T10:00:00Z")
        fake.saves[200]["content_hash"] = "SAME-CONTENT"

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "already_present", "existing_id": 200}
        # No copy I/O: the source was never downloaded.
        download_ids = [c[1][0] for c in fake.call_log if c[0] == "download_save_content"]
        assert 10 not in download_ids
        # No new save landed in the target slot — still just the pre-existing one.
        assert {s["id"] for s in fake.saves.values() if s.get("slot") == TARGET} == {200}
        # No make-current: the active slot and tracked save are unchanged.
        state = _require_save_state(svc, 42)
        assert state.active_slot == "autosave"
        assert state.files["pokemon.srm"].tracked_save_id == 100

    @pytest.mark.asyncio
    async def test_falls_through_to_copy_when_content_hash_absent(self, tmp_path):
        """A source save without a content_hash skips the pre-check and copies.

        Older servers omit content_hash; the copy then relies on RomM's own
        server-side dedup. A distinctive target proves the value flowed.
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        # Source carries no content_hash — even though a same-slot save exists, the
        # pre-check can't compare, so the copy proceeds.
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.saves[200] = _server_save(save_id=200, rom_id=42, slot=TARGET, updated_at="2026-01-01T10:00:00Z")
        fake.saves[200]["content_hash"] = "SOMETHING"
        # We're synced to the target head, so the copy's POST stacks cleanly (no 409).
        fake.stage_device_sync(200, "device-1", "2026-01-01T10:00:00Z")

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "ok"}
        assert _require_save_state(svc, 42).active_slot == TARGET

    @pytest.mark.asyncio
    async def test_ok_copy_tracks_the_new_save_not_a_deduped_existing(self, tmp_path):
        """A genuine copy tracks the newly-created target save (no churn artifact).

        Regression lock: the tracked save becomes the new POST-created save, never
        the source or a deduped existing head — so the current save is excluded
        from its own version history (``list_file_versions`` excludes the tracked id).
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        # Source content is unique (target slot is empty) → a real copy.
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.saves[10]["content_hash"] = "UNIQUE-CONTENT"

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "ok"}
        tracked_id = _require_save_state(svc, 42).files["pokemon.srm"].tracked_save_id
        # The tracked save is the newly-created target save, not the source (10) or
        # the old tracked head (100).
        assert tracked_id not in (10, 100)
        assert tracked_id is not None
        assert fake.saves[tracked_id]["slot"] == TARGET


class TestCopySaveToSlotRefusals:
    """Every refusal branch of the discriminated-status union."""

    @pytest.mark.asyncio
    async def test_invalid_slot_name_empty(self, tmp_path):
        svc, _fake = make_service(tmp_path)
        assert await svc.copy_save_to_slot(42, 10, "") == {"status": "invalid_slot_name"}

    @pytest.mark.asyncio
    async def test_invalid_slot_name_whitespace(self, tmp_path):
        svc, _fake = make_service(tmp_path)
        assert await svc.copy_save_to_slot(42, 10, "   ") == {"status": "invalid_slot_name"}

    @pytest.mark.asyncio
    async def test_not_configured_when_slot_unconfirmed(self, tmp_path):
        """An installed ROM whose slot is not confirmed is refused up front (#1529 lesson)."""
        svc, _fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _enable_sync_with_device(svc)
        # active_slot set but slot_confirmed defaults False → not configured.
        _seed_save_state_dict(svc, 42, {"system": "gba", "active_slot": "autosave"})

        assert await svc.copy_save_to_slot(42, 10, TARGET) == {"status": "not_configured"}

    @pytest.mark.asyncio
    async def test_not_configured_when_legacy_active_slot(self, tmp_path):
        """The legacy no-slot mode ("") is not a confirmed named slot — refused."""
        svc, _fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _enable_sync_with_device(svc)
        _seed_save_state_dict(svc, 42, {"system": "gba", "active_slot": "", "slot_confirmed": True})

        assert await svc.copy_save_to_slot(42, 10, TARGET) == {"status": "not_configured"}

    @pytest.mark.asyncio
    async def test_rom_not_installed(self, tmp_path):
        """A configured ROM that isn't installed locally → rom_not_installed."""
        svc, _fake = make_service(tmp_path)
        # Configured state but no rom_installs row.
        _seed_save_state_dict(svc, 42, {"system": "gba", "active_slot": "autosave", "slot_confirmed": True})

        result = await svc.copy_save_to_slot(42, 10, TARGET)
        assert result == {"status": "rom_not_installed"}

    @pytest.mark.asyncio
    async def test_multi_file_slot_unsupported(self, tmp_path):
        """A multi-file slot (e.g. Saturn .bkr/.bcr) refuses the copy (#908 guard)."""
        svc, fake = make_service(tmp_path)
        _install_rom(svc, tmp_path, system="saturn", file_name="rally.cue")
        _enable_sync_with_device(svc)
        _create_save(tmp_path, system="saturn", rom_name="rally", ext=".bkr")
        _create_save(tmp_path, system="saturn", rom_name="rally", ext=".bcr")
        _seed_save_state_dict(svc, 42, {"system": "saturn", "active_slot": "autosave", "slot_confirmed": True})
        fake.saves[50] = _server_save(save_id=50, rom_id=42, slot="backup", updated_at="2026-02-01T10:00:00Z")

        result = await svc.copy_save_to_slot(42, 50, TARGET)

        assert result == {"status": "unsupported"}
        # No destructive I/O ran — the guard fired before any list/download/upload.
        assert not any(c[0] in ("upload_save", "download_save_content", "list_saves") for c in fake.call_log)

    @pytest.mark.asyncio
    async def test_content_dir_unsupported(self, tmp_path):
        """RetroArch content-dir layout (#239) refuses the copy with the reason slug."""
        svc, fake = make_service(tmp_path, save_locations=FakeSaveLocationReader(beside_content=True))
        _install_rom(svc, tmp_path)
        _enable_sync_with_device(svc)
        _create_save(tmp_path)
        _seed_save_state_dict(svc, 42, {"system": "gba", "active_slot": "autosave", "slot_confirmed": True})
        fake.saves[50] = _server_save(save_id=50, rom_id=42, slot="backup", updated_at="2026-02-01T10:00:00Z")

        result = await svc.copy_save_to_slot(42, 50, TARGET)

        assert result == {"status": "unsupported", "reason": "savefiles_in_content_dir"}
        # Gate fired before any I/O.
        assert not any(c[0] in ("upload_save", "download_save_content", "list_saves") for c in fake.call_log)

    @pytest.mark.asyncio
    async def test_no_save_directory_is_unsupported_and_writes_nothing(self, tmp_path):
        """No placement resolved: nowhere to write the copy, and never a guessed directory."""
        svc, fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _enable_sync_with_device(svc)
        _no_save_directory(svc)
        _seed_save_state_dict(svc, 42, {"system": "gba", "active_slot": "autosave", "slot_confirmed": True})
        fake.saves[50] = _server_save(save_id=50, rom_id=42, slot="backup", updated_at="2026-02-01T10:00:00Z")

        result = await svc.copy_save_to_slot(42, 50, TARGET)

        assert result == {
            "status": "unsupported",
            "reason": "save_shape_unsupported",
            "message": save_shape_message(unestablished_answer()),
        }
        assert not any(c[0] in ("upload_save", "download_save_content", "list_saves") for c in fake.call_log)

    @pytest.mark.asyncio
    async def test_version_deleted_when_save_id_missing(self, tmp_path):
        """A clean pre-flight, but the chosen save id is absent from the server list."""
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")

        result = await svc.copy_save_to_slot(42, 999, TARGET)

        assert result == {"status": "version_deleted"}

    @pytest.mark.asyncio
    async def test_a_deleted_save_copied_into_a_busy_slot_is_version_deleted(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.seed_foreign_save(42, save_id=77, slot=TARGET, uploaded_by="device-B")

        result = await svc.copy_save_to_slot(42, 999, TARGET)

        assert result == {"status": "version_deleted"}
        assert _require_save_state(svc, 42).active_slot == "autosave"

    @pytest.mark.asyncio
    async def test_server_unreachable_on_post_preflight_list(self, tmp_path):
        """A ``list_saves`` failure AFTER a clean pre-flight → server_unreachable."""
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")

        original_list = fake.list_saves
        call_count = {"n": 0}

        def fail_second_list(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise OSError("server unreachable after preflight")
            return original_list(*args, **kwargs)

        fake.list_saves = fail_second_list  # type: ignore[method-assign]
        try:
            result = await svc.copy_save_to_slot(42, 100, TARGET)
        finally:
            fake.list_saves = original_list  # type: ignore[method-assign]

        assert result["status"] == "server_unreachable"
        assert "unreachable" in result.get("message", "").lower()
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_a_429_on_post_preflight_list_is_copy_failed_not_unreachable(self, tmp_path):
        """RomM rate-limiting the listing answered — the copy names that, never an outage."""
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")

        original_list = fake.list_saves
        call_count = {"n": 0}

        def rate_limit_second_list(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RommRateLimitedError("Rate limited")
            return original_list(*args, **kwargs)

        fake.list_saves = rate_limit_second_list  # type: ignore[method-assign]
        try:
            result = await svc.copy_save_to_slot(42, 100, TARGET)
        finally:
            fake.list_saves = original_list  # type: ignore[method-assign]

        assert result == {"status": "copy_failed", "message": "RomM is rate-limiting requests — try again in a moment"}

    @pytest.mark.asyncio
    async def test_definitive_404_on_post_preflight_list_is_not_found(self, tmp_path):
        """A 404 on the post-preflight ``list_saves`` → not_found, not offline (#1570).

        ``list_saves`` is rom- AND device-scoped, so a definitive 404 is the
        #1560 family (dead ROM or dropped device registration) — the server
        answered. It must read as ``not_found`` rather than ``server_unreachable``,
        and — like the transport branch — it must still persist whatever the
        pre-flight mutated before bailing, or that work is lost.
        """
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        # Seeded state has no sync-check timestamp; the pre-flight sets one.
        assert _require_save_state(svc, 42).last_sync_check_at is None

        original_list = fake.list_saves
        call_count = {"n": 0}

        def fail_second_list(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RommNotFoundError("HTTP 404: Not Found")
            return original_list(*args, **kwargs)

        fake.list_saves = fail_second_list  # type: ignore[method-assign]
        try:
            result = await svc.copy_save_to_slot(42, 100, TARGET)
        finally:
            fake.list_saves = original_list  # type: ignore[method-assign]

        assert result["status"] == "not_found"
        assert result["status"] != "server_unreachable"
        assert "message" in result
        # The pre-flight mutation was persisted before bailing (mark_sync_evaluated
        # ran, so the seeded-None timestamp is now set on disk).
        assert _require_save_state(svc, 42).last_sync_check_at is not None

    @pytest.mark.asyncio
    async def test_a_target_slot_holding_a_save_this_device_never_synced_is_busy(self, tmp_path):
        """The target slot holds another device's save this device never synced → target_slot_busy,
        before anything is copied."""
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        # A legacy source to copy.
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        # The target slot already holds a foreign head we've never synced.
        fake.seed_foreign_save(42, save_id=77, slot=TARGET, uploaded_by="device-B")

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result["status"] == "target_slot_busy"
        assert result.get("message")
        # The source save is untouched.
        assert 10 in fake.saves
        self._assert_nothing_was_copied(svc, fake, tmp_path, local_hash)

    @pytest.mark.parametrize(
        "this_device_synced_the_head_at", [None, "2026-02-20T10:00:00Z"], ids=["never-synced", "moved-on"]
    )
    @pytest.mark.asyncio
    async def test_a_copy_into_a_slot_whose_newest_save_this_device_is_not_current_on_changes_nothing(
        self, tmp_path, this_device_synced_the_head_at
    ):
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.set_server_save_content(10, b"legacy-save-bytes")
        # This device is current on an older save of another file in the slot.
        fake.saves[150] = _server_save(
            save_id=150, rom_id=42, filename="pokemon.rtc", slot=TARGET, updated_at="2026-02-10T10:00:00Z"
        )
        fake.stage_device_sync(150, "device-1", "2026-02-10T10:00:00Z")
        fake.seed_foreign_save(42, save_id=77, slot=TARGET, uploaded_by="device-B", updated_at="2026-03-01T10:00:00Z")
        if this_device_synced_the_head_at is not None:
            fake.stage_device_sync(77, "device-1", this_device_synced_the_head_at)

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result["status"] == "target_slot_busy"
        self._assert_nothing_was_copied(svc, fake, tmp_path, local_hash)

    @pytest.mark.asyncio
    async def test_a_target_slot_whose_times_cannot_be_read_is_left_to_romm(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.saves[200] = _server_save(save_id=200, rom_id=42, slot=TARGET, updated_at="not a time")
        fake.stage_device_sync(200, "device-1", "not a time")

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "ok"}
        assert _require_save_state(svc, 42).active_slot == TARGET

    @staticmethod
    def _assert_nothing_was_copied(svc, fake, tmp_path, local_hash: str) -> None:
        assert 10 not in [c[1][0] for c in fake.call_log if c[0] == "download_save_content"]
        assert not any(c[0] == "upload_save" for c in fake.call_log)
        state = _require_save_state(svc, 42)
        assert (state.active_slot, state.slot_confirmed) == ("autosave", True)
        assert TARGET not in state.slots
        assert (state.files["pokemon.srm"].tracked_save_id, state.files["pokemon.srm"].last_sync_hash) == (
            100,
            local_hash,
        )
        saves_dir = tmp_path / "saves" / "gba"
        assert _file_md5(str(saves_dir / "pokemon.srm")) == local_hash
        assert not (saves_dir / ".romm-backup").exists()

    @pytest.mark.asyncio
    async def test_a_save_this_device_cannot_write_is_copy_failed(self, tmp_path):
        """The chosen save cannot be written here — an answer of the copy's own, never a transport error."""
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path)
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        fake.fail_download_on(10, DeviceWriteError(28, "No space left on device"))

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {
            "status": "copy_failed",
            "message": "it could not be written on this device (No space left on device)",
        }
        assert not any(call[0] == "upload_save" for call in fake.call_log)

    @pytest.mark.asyncio
    async def test_a_copy_whose_download_breaks_off_on_romm_keeps_the_previous_slot(self, tmp_path):
        svc, fake = self._a_copy_from_a_legacy_save(tmp_path)
        failure = RommTimeoutError("read timed out")
        fake.break_download_on(10, failure)

        with pytest.raises(RommTimeoutError) as caught:
            await svc.copy_save_to_slot(42, 10, TARGET)

        assert caught.value is failure
        self._assert_the_previous_slot_is_kept(svc, fake, tmp_path)

    @pytest.mark.asyncio
    async def test_a_copy_whose_download_this_device_cannot_write_keeps_the_previous_slot(self, tmp_path):
        svc, fake = self._a_copy_from_a_legacy_save(tmp_path)
        fake.break_download_on(10, DeviceWriteError(28, "No space left on device"))

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result["status"] == "copy_failed"
        self._assert_the_previous_slot_is_kept(svc, fake, tmp_path)

    @pytest.mark.asyncio
    async def test_a_copy_whose_upload_fails_after_the_download_is_upload_failed(self, tmp_path, monkeypatch):
        svc, fake = self._a_copy_from_a_legacy_save(tmp_path)
        fake.set_server_save_content(10, b"legacy-save-bytes")

        def failing_upload(*_args, **_kwargs):
            raise RuntimeError("server 500")

        monkeypatch.setattr(fake, "upload_save", failing_upload)

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result == {"status": "upload_failed", "message": "server 500"}
        state = _require_save_state(svc, 42)
        assert (state.active_slot, state.slot_confirmed) == (TARGET, True)
        assert TARGET in state.slots
        assert (tmp_path / "saves" / "gba" / "pokemon.srm").read_bytes() == b"legacy-save-bytes"

    @staticmethod
    def _a_copy_from_a_legacy_save(tmp_path):
        svc, fake = make_service(tmp_path)
        _create_save(tmp_path, content=b"current-slot-bytes")
        local_hash = _file_md5(str(tmp_path / "saves" / "gba" / "pokemon.srm"))
        _setup_configured(svc, tmp_path, active_slot="autosave", tracked_id=100, last_sync_hash=local_hash)
        fake.saves[100] = _tracked_save(100, slot="autosave")
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")
        return svc, fake

    @staticmethod
    def _assert_the_previous_slot_is_kept(svc, fake, tmp_path) -> None:
        state = _require_save_state(svc, 42)
        assert (state.active_slot, state.slot_confirmed) == ("autosave", True)
        assert TARGET not in state.slots
        assert state.files["pokemon.srm"].tracked_save_id == 100
        saves_dir = tmp_path / "saves" / "gba"
        assert (saves_dir / "pokemon.srm").read_bytes() == b"current-slot-bytes"
        assert not (saves_dir / "pokemon.srm.tmp").exists()
        assert not any(call[0] == "upload_save" for call in fake.call_log)

    @pytest.mark.asyncio
    async def test_conflict_blocked_on_dirty_current_slot(self, tmp_path):
        """A real conflict on the CURRENT slot blocks the copy before any copy I/O."""
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        _seed_save_state_dict(
            svc,
            42,
            {
                "system": "gba",
                "active_slot": "autosave",
                "slot_confirmed": True,
                "files": {"pokemon.srm": {"tracked_save_id": 100, "last_sync_hash": "aabbcc001122334455667788"}},
            },
        )
        # Local diverged from the baseline, and the server head moved past us
        # (is_current False) → the matrix returns Conflict.
        _create_save(tmp_path, content=b"\xff" * 1024)
        fake.saves[100] = _server_save_with_syncs(
            save_id=100,
            slot="autosave",
            updated_at="2026-03-15T10:00:00Z",
            device_syncs=[{"device_id": "device-1", "is_current": False, "last_synced_at": "2026-03-10T10:00:00Z"}],
        )
        fake.saves[10] = _server_save(save_id=10, rom_id=42, slot=None, updated_at="2026-02-01T10:00:00Z")

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result["status"] == "conflict_blocked"
        assert len(result["conflicts"]) == 1
        # No copy download of the source ran, and the target slot never received a save.
        download_ids = [c[1][0] for c in fake.call_log if c[0] == "download_save_content"]
        assert 10 not in download_ids
        assert not [s for s in fake.saves.values() if s.get("slot") == TARGET]

    @pytest.mark.asyncio
    async def test_preflight_failed_on_server_error(self, tmp_path):
        """A non-conflict error during the pre-flight aborts with preflight_failed."""
        svc, fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _enable_sync_with_device(svc)
        _seed_save_state_dict(svc, 42, {"system": "gba", "active_slot": "autosave", "slot_confirmed": True})
        fake.fail_next_list_saves(Exception("network error"))

        result = await svc.copy_save_to_slot(42, 10, TARGET)

        assert result["status"] == "preflight_failed"
        assert any("network" in err.lower() for err in result.get("errors", []))
        # No copy ran — the target slot got no save.
        assert not [s for s in fake.saves.values() if s.get("slot") == TARGET]
