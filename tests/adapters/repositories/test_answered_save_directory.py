"""Tests for ``SqliteAnsweredSaveDirectoryRepository`` over the ``answered_save_directories`` table."""

from __future__ import annotations

from adapters.repositories.unit_of_work import SqliteUnitOfWork
from domain.answered_save_directory import AnsweredSaveDirectory
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


class TestRoundTrip:
    def test_a_saved_record_reads_back_equal(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        record = AnsweredSaveDirectory.record(rom_id=5, directory="/saves/snes/Snes9x")

        uow.answered_save_directories.save(record)

        assert uow.answered_save_directories.get(5) == record

    def test_nothing_recorded_reads_none(self, uow: SqliteUnitOfWork):
        assert uow.answered_save_directories.get(5) is None

    def test_a_second_save_replaces_the_directory(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=5, directory="/saves/snes"))

        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=5, directory="/saves/snes/Snes9x"))

        assert uow.answered_save_directories.get(5) == AnsweredSaveDirectory(rom_id=5, directory="/saves/snes/Snes9x")


class TestTheSaveSyncStateIsUntouched:
    def test_recording_a_directory_creates_no_save_sync_state(self, uow: SqliteUnitOfWork):
        # A save-sync state row means "tracked for save sync"; this record is
        # written for games that never were.
        _seed_rom(uow, 5)

        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=5, directory="/saves/snes"))

        assert uow.rom_save_sync_states.get(5) is None


class TestTheRecordGoesWithItsRom:
    def test_deleting_the_rom_deletes_the_record(self, db: str):
        with SqliteUnitOfWork(db) as uow:
            _seed_rom(uow, 5)
            uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=5, directory="/saves/snes"))
        with SqliteUnitOfWork(db) as uow:
            uow.roms.delete(5)
        with SqliteUnitOfWork(db) as uow:
            assert uow.answered_save_directories.get(5) is None


class TestDelete:
    def test_delete_drops_only_the_named_rom(self, uow: SqliteUnitOfWork):
        _seed_rom(uow, 5)
        _seed_rom(uow, 6)
        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=5, directory="/saves/snes"))
        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=6, directory="/saves/gba"))

        uow.answered_save_directories.delete(5)

        assert uow.answered_save_directories.get(5) is None
        assert uow.answered_save_directories.get(6) is not None

    def test_deleting_nothing_is_a_no_op(self, uow: SqliteUnitOfWork):
        uow.answered_save_directories.delete(5)

        assert uow.answered_save_directories.get(5) is None
