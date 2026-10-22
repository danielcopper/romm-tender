"""Tests for ``SqliteArtworkSlotHandoverRepository`` over the ``artwork_slot_handovers`` table."""

from __future__ import annotations

import sqlite3

import pytest

from adapters.repositories.unit_of_work import SqliteUnitOfWork
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_handover import HANDED_SLOTS, ArtworkSlotHandover
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


def _handover(rom_id: int = 5, slot: ArtworkSlot = ArtworkSlot.LOGO, image_id: int = 4242) -> ArtworkSlotHandover:
    return ArtworkSlotHandover.hand_over(
        rom_id=rom_id, slot=slot, image_id=image_id, image_sha256=f"sha-{image_id}", handed_at=1_800_000_000.5
    )


class TestRoundTrip:
    @pytest.mark.parametrize("slot", sorted(HANDED_SLOTS))
    def test_a_hand_over_for_each_slot_reads_back_equal(self, uow: SqliteUnitOfWork, slot: ArtworkSlot):
        _seed_rom(uow, 5)

        uow.artwork_slot_handovers.save(_handover(slot=slot))

        assert uow.artwork_slot_handovers.get(5, slot) == _handover(slot=slot)

    def test_a_second_hand_over_replaces_the_first_and_delete_slot_drops_it(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        uow.artwork_slot_handovers.save(_handover(image_id=1))
        uow.artwork_slot_handovers.save(_handover(image_id=2))
        assert uow.artwork_slot_handovers.get(5, ArtworkSlot.LOGO) == _handover(image_id=2)

        uow.artwork_slot_handovers.delete_slot(5, ArtworkSlot.LOGO)

        assert uow.artwork_slot_handovers.get(5, ArtworkSlot.LOGO) is None

    def test_the_table_refuses_the_icon(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        icon = ArtworkSlotHandover(rom_id=5, slot=ArtworkSlot.ICON, image_id=1, image_sha256="x", handed_at=1.0)
        with pytest.raises(sqlite3.IntegrityError):
            uow.artwork_slot_handovers.save(icon)


class TestTheRowsBelongToTheirRom:
    def test_a_hand_over_for_a_rom_that_is_not_synced_is_refused(self, uow: SqliteUnitOfWork):
        with pytest.raises(sqlite3.IntegrityError):
            uow.artwork_slot_handovers.save(_handover(rom_id=99))

    def test_the_rows_go_with_their_rom(self, db: str):
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
            uow.artwork_slot_handovers.save(_handover())
        with SqliteUnitOfWork(db) as uow:
            uow.roms.delete(5)
        with SqliteUnitOfWork(db) as uow:
            assert uow.artwork_slot_handovers.get(5, ArtworkSlot.LOGO) is None
