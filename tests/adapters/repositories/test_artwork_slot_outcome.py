"""Tests for ``SqliteArtworkSlotOutcomeRepository`` over the ``artwork_slot_outcomes`` table."""

from __future__ import annotations

import sqlite3

import pytest

from adapters.repositories.unit_of_work import SqliteUnitOfWork
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import ArtworkSlotOutcome
from domain.rom import Rom


def _seed_rom(uow: SqliteUnitOfWork, rom_id: int) -> None:
    uow.roms.save(
        Rom(
            rom_id=rom_id,
            platform_slug="snes",
            name=f"Game {rom_id}",
            fs_name=f"game_{rom_id}.sfc",
            shortcut_app_id=1000 + rom_id,
            last_synced_at="2026-01-01T00:00:00Z",
        )
    )


def _set(rom_id: int = 5, slot: ArtworkSlot = ArtworkSlot.WIDE_GRID) -> ArtworkSlotOutcome:
    return ArtworkSlotOutcome.record_set(
        rom_id=rom_id, slot=slot, sgdb_id=77, image_id=12345, image_sha256="ab" * 32, recorded_at=1_800_000_000.5
    )


class TestRoundTrip:
    def test_a_set_outcome_reads_back_equal(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)

        uow.artwork_slot_outcomes.save(_set())

        assert uow.artwork_slot_outcomes.get(5, ArtworkSlot.WIDE_GRID) == _set()

    def test_a_none_outcome_without_an_id_reads_back_equal(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        record = ArtworkSlotOutcome.record_none(rom_id=5, slot=ArtworkSlot.LOGO, sgdb_id=None, recorded_at=1.0)

        uow.artwork_slot_outcomes.save(record)

        assert uow.artwork_slot_outcomes.get(5, ArtworkSlot.LOGO) == record

    def test_each_slot_has_its_own_row(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        uow.artwork_slot_outcomes.save(_set(slot=ArtworkSlot.WIDE_GRID))

        assert uow.artwork_slot_outcomes.get(5, ArtworkSlot.LOGO) is None

    def test_a_second_save_replaces_the_slots_row(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        uow.artwork_slot_outcomes.save(_set())
        refused = ArtworkSlotOutcome.record_refused(rom_id=5, slot=ArtworkSlot.WIDE_GRID, sgdb_id=77, recorded_at=2.0)

        uow.artwork_slot_outcomes.save(refused)

        assert uow.artwork_slot_outcomes.get(5, ArtworkSlot.WIDE_GRID) == refused


class TestDeletingAGamesRows:
    def test_delete_for_rom_drops_every_slot_of_that_rom_only(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        _seed_rom(uow, 6)
        uow.artwork_slot_outcomes.save(_set(5, ArtworkSlot.WIDE_GRID))
        uow.artwork_slot_outcomes.save(_set(5, ArtworkSlot.LOGO))
        uow.artwork_slot_outcomes.save(_set(6, ArtworkSlot.LOGO))

        uow.artwork_slot_outcomes.delete_for_rom(5)

        assert uow.artwork_slot_outcomes.get(5, ArtworkSlot.WIDE_GRID) is None
        assert uow.artwork_slot_outcomes.get(5, ArtworkSlot.LOGO) is None
        assert uow.artwork_slot_outcomes.get(6, ArtworkSlot.LOGO) == _set(6, ArtworkSlot.LOGO)

    def test_the_rows_go_with_their_rom(self, db: str):
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
            uow.artwork_slot_outcomes.save(_set())
        with SqliteUnitOfWork(db) as uow:
            uow.roms.delete(5)
        with SqliteUnitOfWork(db) as uow:
            assert uow.artwork_slot_outcomes.get(5, ArtworkSlot.WIDE_GRID) is None


class TestTheSchemaHoldsTheShape:
    def test_a_row_for_a_rom_that_is_not_synced_is_refused(self, uow: SqliteUnitOfWork):
        record = _set(rom_id=99)

        with pytest.raises(sqlite3.IntegrityError):
            uow.artwork_slot_outcomes.save(record)

    @pytest.mark.parametrize(
        ("outcome", "image_id", "image_sha256"),
        [("set", None, "ab"), ("set", 1, None), ("none", 1, None), ("refused", None, "ab"), ("lost", None, None)],
    )
    def test_an_outcome_and_its_image_must_agree(self, db: str, outcome, image_id, image_sha256):
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
        conn = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO artwork_slot_outcomes VALUES (5, 'grid', ?, 7, 1.0, ?, ?)",
                    (outcome, image_id, image_sha256),
                )
        finally:
            conn.close()

    def test_an_unknown_slot_has_no_row(self, db: str):
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
        conn = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO artwork_slot_outcomes VALUES (5, 'banner', 'none', 7, 1.0, NULL, NULL)")
        finally:
            conn.close()
