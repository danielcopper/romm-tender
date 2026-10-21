"""Tests for ``SqliteArtworkSlotChoiceRepository`` over the ``artwork_slot_choices`` table."""

from __future__ import annotations

import sqlite3

import pytest

from adapters.repositories.unit_of_work import SqliteUnitOfWork
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_choice import ArtworkSlotChoice
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


def _choice(rom_id: int = 5, slot: ArtworkSlot = ArtworkSlot.LOGO, image_id: int = 4242) -> ArtworkSlotChoice:
    return ArtworkSlotChoice(rom_id=rom_id, slot=slot, image_id=image_id, chosen_at=1_800_000_000.5)


class TestRoundTrip:
    @pytest.mark.parametrize("slot", list(ArtworkSlot))
    def test_a_choice_for_each_slot_reads_back_equal(self, uow: SqliteUnitOfWork, slot: ArtworkSlot):
        _seed_rom(uow, 5)

        uow.artwork_slot_choices.save(_choice(slot=slot))

        assert uow.artwork_slot_choices.get(5, slot) == _choice(slot=slot)

    def test_a_slot_with_no_choice_reads_none(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        uow.artwork_slot_choices.save(_choice(slot=ArtworkSlot.LOGO))

        assert uow.artwork_slot_choices.get(5, ArtworkSlot.HERO) is None

    def test_a_second_choice_replaces_the_first(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        uow.artwork_slot_choices.save(_choice(image_id=1))

        uow.artwork_slot_choices.save(_choice(image_id=2))

        assert uow.artwork_slot_choices.get(5, ArtworkSlot.LOGO) == _choice(image_id=2)


class TestTheRowsBelongToTheirRom:
    def test_a_choice_for_a_rom_that_is_not_synced_is_refused(self, uow: SqliteUnitOfWork):
        orphan = _choice(rom_id=99)
        with pytest.raises(sqlite3.IntegrityError):
            uow.artwork_slot_choices.save(orphan)

    def test_the_rows_go_with_their_rom(self, db: str):
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
            uow.artwork_slot_choices.save(_choice())
        with SqliteUnitOfWork(db) as uow:
            uow.roms.delete(5)
        with SqliteUnitOfWork(db) as uow:
            assert uow.artwork_slot_choices.get(5, ArtworkSlot.LOGO) is None

    def test_a_resync_of_the_rom_keeps_its_choices(self, db: str):
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
            uow.artwork_slot_choices.save(_choice())
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
        with SqliteUnitOfWork(db) as uow:
            assert uow.artwork_slot_choices.get(5, ArtworkSlot.LOGO) == _choice()


class TestSettingASlotBackToAutomatic:
    def test_deleting_a_slot_drops_its_choice_only(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        uow.artwork_slot_choices.save(_choice(slot=ArtworkSlot.LOGO))
        uow.artwork_slot_choices.save(_choice(slot=ArtworkSlot.HERO))

        uow.artwork_slot_choices.delete_slot(5, ArtworkSlot.LOGO)

        assert uow.artwork_slot_choices.get(5, ArtworkSlot.LOGO) is None
        assert uow.artwork_slot_choices.get(5, ArtworkSlot.HERO) == _choice(slot=ArtworkSlot.HERO)

    def test_deleting_a_slot_without_a_choice_is_a_no_op(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)

        uow.artwork_slot_choices.delete_slot(5, ArtworkSlot.LOGO)

        assert uow.artwork_slot_choices.list_for_rom(5) == []

    def test_deleting_a_roms_choices_leaves_another_roms(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        _seed_rom(uow, 6)
        uow.artwork_slot_choices.save(_choice(rom_id=5))
        uow.artwork_slot_choices.save(_choice(rom_id=6))

        uow.artwork_slot_choices.delete_for_rom(5)

        assert uow.artwork_slot_choices.list_for_rom(5) == []
        assert uow.artwork_slot_choices.list_for_rom(6) == [_choice(rom_id=6)]


class TestListForRom:
    def test_lists_every_slot_of_that_rom_only(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        _seed_rom(uow, 6)
        for slot in (ArtworkSlot.LOGO, ArtworkSlot.COVER):
            uow.artwork_slot_choices.save(_choice(slot=slot))
        uow.artwork_slot_choices.save(_choice(rom_id=6))

        listed = uow.artwork_slot_choices.list_for_rom(5)

        assert sorted(listed, key=lambda choice: choice.slot.value) == [
            _choice(slot=ArtworkSlot.COVER),
            _choice(slot=ArtworkSlot.LOGO),
        ]
