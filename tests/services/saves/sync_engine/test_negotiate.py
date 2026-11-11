"""Tests for NegotiateEnvelope — RomM's negotiate session around a sync, and the inventory it sends."""

import hashlib
import logging
import os

import pytest
from fakes.fake_rom_save_sync_state_repository import FakeRomSaveSyncStateRepository

from domain.iso_time import epoch_to_iso
from domain.live_device_id import LiveDeviceId
from domain.rom_save_sync_state import RomSaveSyncState
from tests.services.saves._helpers import (
    _create_save,
    _install_rom,
    _seed_save_state,
    make_service,
)


class TestBuildSaveInventory:
    """``NegotiateEnvelope.build_inventory`` — the inventory a negotiate session sends.

    Scope predicate: ``slot_confirmed`` ∧ a non-legacy (truthy) ``active_slot``.
    Per-file granularity; ``content_hash`` is the zip-aware store hash and
    ``updated_at`` is the local mtime as a UTC ISO string.
    """

    def test_one_confirmed_rom_one_file(self, tmp_path):
        """A confirmed non-null slot with one local save → one fully-populated entry."""
        svc, _ = make_service(tmp_path)
        content = b"pokemon-save-bytes"
        save_path = _create_save(tmp_path, system="gba", rom_name="pokemon", content=content)
        os.utime(save_path, (1705320000.0, 1705320000.0))
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba")

        state = RomSaveSyncState(emulator="retroarch-mgba", system="gba")
        state.confirm_slot("A")
        _seed_save_state(svc, 42, state)

        inventory = svc._sync_engine._negotiate.build_inventory()

        assert inventory == [
            {
                "rom_id": 42,
                "file_name": "pokemon.srm",
                "slot": "A",
                "emulator": "retroarch-mgba",
                "content_hash": hashlib.md5(content).hexdigest(),
                "updated_at": epoch_to_iso(1705320000.0),
                "file_size_bytes": len(content),
            }
        ]

    def test_multiple_local_files_yield_one_entry_each(self, tmp_path):
        """Two local save files for one confirmed ROM → two entries (per-file)."""
        svc, _ = make_service(tmp_path)
        _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"a", ext=".srm")
        _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"bb", ext=".rtc")
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba")

        state = RomSaveSyncState(system="gba")
        state.confirm_slot("A")
        _seed_save_state(svc, 42, state)

        inventory = svc._sync_engine._negotiate.build_inventory()

        assert len(inventory) == 2
        assert {e["file_name"] for e in inventory} == {"pokemon.srm", "pokemon.rtc"}
        assert all(e["rom_id"] == 42 for e in inventory)
        assert all(e.get("slot") == "A" for e in inventory)

    def test_excludes_legacy_null_slot(self, tmp_path):
        """A confirmed ROM whose active_slot is the legacy None is excluded."""
        svc, _ = make_service(tmp_path)
        _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"data")
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba")

        # Construct the legacy confirmed state directly — confirm_slot(None) is
        # rejected now (#1276), but a pre-#1276 DB row (or one mid-migration
        # before 005 un-confirms it) can still carry active_slot=None +
        # slot_confirmed=True, and build_inventory must still exclude it.
        state = RomSaveSyncState(system="gba", slot_confirmed=True, active_slot=None)
        assert state.slot_confirmed is True
        assert state.active_slot is None
        _seed_save_state(svc, 42, state)

        assert svc._sync_engine._negotiate.build_inventory() == []

    def test_excludes_unconfirmed_slot(self, tmp_path):
        """A ROM with a real active_slot but slot_confirmed=False is excluded."""
        svc, _ = make_service(tmp_path)
        _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"data")
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba")

        state = RomSaveSyncState(system="gba")
        state.switch_active_slot("A")  # active_slot="A", slot_confirmed stays False
        assert state.active_slot == "A"
        assert state.slot_confirmed is False
        _seed_save_state(svc, 42, state)

        assert svc._sync_engine._negotiate.build_inventory() == []

    def test_confirmed_rom_with_no_local_files_contributes_nothing(self, tmp_path):
        """A confirmed non-null slot whose ROM has no local save files yields no entry."""
        svc, _ = make_service(tmp_path)
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba")
        # No _create_save — the saves directory has no matching files.

        state = RomSaveSyncState(system="gba")
        state.confirm_slot("A")
        _seed_save_state(svc, 42, state)

        assert svc._sync_engine._negotiate.build_inventory() == []

    def test_no_confirmed_roms_returns_empty(self, tmp_path):
        """No tracked ROMs at all → empty inventory."""
        svc, _ = make_service(tmp_path)
        assert svc._sync_engine._negotiate.build_inventory() == []

    def test_rom_id_scopes_inventory_to_one_rom(self, tmp_path):
        """``build_inventory(rom_id=42)`` returns only rom 42's entries.

        The scoped form backs the single-ROM negotiate trigger; the whole-device
        form (``rom_id=None``) backs the bulk pre-negotiate.
        """
        svc, _ = make_service(tmp_path)
        _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"a")
        _create_save(tmp_path, system="snes", rom_name="mario", content=b"bb")
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba")
        _install_rom(svc, tmp_path, rom_id=99, system="snes", file_name="mario.sfc")

        state42 = RomSaveSyncState(system="gba")
        state42.confirm_slot("A")
        _seed_save_state(svc, 42, state42)
        state99 = RomSaveSyncState(system="snes")
        state99.confirm_slot("A")
        _seed_save_state(svc, 99, state99, platform_slug="snes")

        # Whole-device inventory sees both ROMs.
        assert {e["rom_id"] for e in svc._sync_engine._negotiate.build_inventory()} == {42, 99}

        # Scoped to 42 → only rom 42's entry.
        scoped = svc._sync_engine._negotiate.build_inventory(rom_id=42)
        assert len(scoped) == 1
        assert scoped[0]["rom_id"] == 42
        assert scoped[0]["file_name"] == "pokemon.srm"

    def test_rom_id_scope_excludes_unconfirmed_target(self, tmp_path):
        """A scoped build still applies the in-scope predicate — an unconfirmed
        target yields nothing."""
        svc, _ = make_service(tmp_path)
        _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"a")
        _install_rom(svc, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba")

        state = RomSaveSyncState(system="gba")
        state.switch_active_slot("A")  # not confirmed
        _seed_save_state(svc, 42, state)

        assert svc._sync_engine._negotiate.build_inventory(rom_id=42) == []


class TestTheWholeDeviceSession:
    """``open_device_session`` answers no session where it cannot open one, as ``open_rom_session`` does."""

    @pytest.mark.asyncio
    async def test_an_inventory_that_cannot_be_built_answers_no_session(self, tmp_path, monkeypatch, caplog):
        svc, fake = make_service(tmp_path)

        def the_database_is_locked(_self):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(FakeRomSaveSyncStateRepository, "iter_all", the_database_is_locked)

        with caplog.at_level(logging.WARNING):
            session_id = await svc._sync_engine._negotiate.open_device_session(LiveDeviceId("device-1"))

        assert session_id is None
        assert not any(c[0] == "negotiate_sync" for c in fake.call_log)
        assert [r.getMessage() for r in caplog.records] == [
            "sync_all_saves: negotiate session open failed (database is locked) — per-ROM sessions"
        ]
