"""Copies of the live data a pre-install check builds on, and the originals left as they were."""

from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING

import pytest

import adapters.live_data_copy as live_data_copy
from adapters.live_data_copy import LiveDatabaseChangedError, copy_database, copy_file

if TYPE_CHECKING:
    from pathlib import Path


def _database(path: Path, *notes: str) -> None:
    """A WAL-mode database holding *notes*, closed — so nothing lies beside it."""
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    with db:
        db.execute("CREATE TABLE marker (note TEXT)")
        db.executemany("INSERT INTO marker VALUES (?)", [(note,) for note in notes])
    db.close()


def _notes(path: Path) -> list[str]:
    db = sqlite3.connect(path)
    try:
        return [row[0] for row in db.execute("SELECT note FROM marker ORDER BY rowid")]
    finally:
        db.close()


class TestCopyDatabase:
    def test_it_copies_what_the_database_holds(self, tmp_path):
        _database(tmp_path / "live.db", "one", "two")

        assert copy_database(str(tmp_path / "live.db"), str(tmp_path / "copy" / "romm_sync.db")) is True
        assert _notes(tmp_path / "copy" / "romm_sync.db") == ["one", "two"]

    def test_a_database_nothing_holds_open_gets_nothing_put_beside_it(self, tmp_path):
        """A read-only open of a WAL database would create its WAL and index, and leave them."""
        live = tmp_path / "live"
        live.mkdir()
        _database(live / "romm_sync.db", "one")
        before = (live / "romm_sync.db").read_bytes()

        copy_database(str(live / "romm_sync.db"), str(tmp_path / "copy" / "romm_sync.db"))

        assert [path.name for path in live.iterdir()] == ["romm_sync.db"]
        assert (live / "romm_sync.db").read_bytes() == before

    def test_a_database_held_open_is_copied_with_what_its_wal_holds_and_nothing_is_put_beside_it(self, tmp_path):
        """The WAL index is left out of the byte comparison: every reader writes its read marks there."""
        live = tmp_path / "live"
        live.mkdir()
        _database(live / "romm_sync.db", "in the file")
        writer = sqlite3.connect(live / "romm_sync.db")
        try:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO marker VALUES ('in the wal')")
            writer.commit()
            names = sorted(path.name for path in live.iterdir())
            assert names == ["romm_sync.db", "romm_sync.db-shm", "romm_sync.db-wal"]
            before = {name: (live / name).read_bytes() for name in names if not name.endswith("-shm")}

            copy_database(str(live / "romm_sync.db"), str(tmp_path / "copy.db"))

            assert sorted(path.name for path in live.iterdir()) == names
            assert {name: (live / name).read_bytes() for name in before} == before
        finally:
            writer.close()

        assert _notes(tmp_path / "copy.db") == ["in the file", "in the wal"]

    def test_a_path_with_characters_a_uri_escapes_is_found(self, tmp_path):
        live = tmp_path / "a dir #1?"
        live.mkdir()
        _database(live / "romm_sync.db", "one")

        assert copy_database(str(live / "romm_sync.db"), str(tmp_path / "copy.db")) is True
        assert _notes(tmp_path / "copy.db") == ["one"]

    def test_no_database_is_nothing_to_copy(self, tmp_path):
        assert copy_database(str(tmp_path / "absent.db"), str(tmp_path / "copy" / "romm_sync.db")) is False
        assert not (tmp_path / "copy").exists()

    def test_a_wal_left_without_its_index_is_folded_into_the_copy_and_nothing_is_put_beside_the_live_one(
        self, tmp_path
    ):
        """A read-only open would create the index in the live directory; the bytes route opens a copy instead."""
        held = tmp_path / "held"
        held.mkdir()
        _database(held / "romm_sync.db", "in the file")
        live = tmp_path / "live"
        live.mkdir()
        writer = sqlite3.connect(held / "romm_sync.db")
        try:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO marker VALUES ('in the wal')")
            writer.commit()
            for name in ("romm_sync.db", "romm_sync.db-wal"):
                (live / name).write_bytes((held / name).read_bytes())
        finally:
            writer.close()
        before = {path.name: path.read_bytes() for path in live.iterdir()}
        assert sorted(before) == ["romm_sync.db", "romm_sync.db-wal"]

        assert copy_database(str(live / "romm_sync.db"), str(tmp_path / "copy" / "romm_sync.db")) is True

        assert {path.name: path.read_bytes() for path in live.iterdir()} == before
        assert _notes(tmp_path / "copy" / "romm_sync.db") == ["in the file", "in the wal"]
        assert sorted(path.name for path in (tmp_path / "copy").iterdir()) == ["romm_sync.db"]

    def test_an_index_left_without_its_wal_gets_no_wal_put_beside_it(self, tmp_path):
        """A read-only open would create the WAL in the live directory; with no WAL the file holds every page."""
        held = tmp_path / "held"
        held.mkdir()
        _database(held / "romm_sync.db", "in the file")
        live = tmp_path / "live"
        live.mkdir()
        reader = sqlite3.connect(held / "romm_sync.db")
        try:
            assert reader.execute("SELECT count(*) FROM marker").fetchone() == (1,)
            for name in ("romm_sync.db", "romm_sync.db-shm"):
                (live / name).write_bytes((held / name).read_bytes())
        finally:
            reader.close()
        before = {path.name: path.read_bytes() for path in live.iterdir()}
        assert sorted(before) == ["romm_sync.db", "romm_sync.db-shm"]

        assert copy_database(str(live / "romm_sync.db"), str(tmp_path / "copy" / "romm_sync.db")) is True

        assert {path.name: path.read_bytes() for path in live.iterdir()} == before
        assert _notes(tmp_path / "copy" / "romm_sync.db") == ["in the file"]

    def test_a_write_during_a_copy_that_takes_no_lock_is_seen_and_the_copy_taken_again(self, tmp_path, monkeypatch):
        """The first copy reads a quiet database; a writer opens it meanwhile, and the second copy has its row."""
        _database(tmp_path / "live.db", "before")
        reads: list[str] = []
        writers: list[sqlite3.Connection] = []
        real_backup = live_data_copy._backup

        def backup_then_write(uri: str, target: str) -> None:
            reads.append(uri)
            real_backup(uri, target)
            if len(reads) == 1:
                writer = sqlite3.connect(tmp_path / "live.db")
                writers.append(writer)
                writer.execute("PRAGMA wal_autocheckpoint=0")
                writer.execute("INSERT INTO marker VALUES ('during')")
                writer.commit()

        monkeypatch.setattr(live_data_copy, "_backup", backup_then_write)
        try:
            copy_database(str(tmp_path / "live.db"), str(tmp_path / "copy.db"))
        finally:
            for writer in writers:
                writer.close()

        assert ["immutable=1" in uri for uri in reads] == [True, False]
        assert _notes(tmp_path / "copy.db") == ["before", "during"]

    def test_a_database_that_changes_under_both_copies_raises(self, tmp_path, monkeypatch):
        _database(tmp_path / "live.db", "before")
        real_backup = live_data_copy._backup

        def backup_then_touch(uri: str, target: str) -> None:
            real_backup(uri, target)
            stamp = (tmp_path / "live.db").stat().st_mtime_ns + 1_000_000_000
            os.utime(tmp_path / "live.db", ns=(stamp, stamp))

        monkeypatch.setattr(live_data_copy, "_backup", backup_then_touch)

        with pytest.raises(LiveDatabaseChangedError):
            copy_database(str(tmp_path / "live.db"), str(tmp_path / "copy.db"))

    def test_a_file_that_is_not_a_database_raises(self, tmp_path):
        (tmp_path / "live.db").write_bytes(b"not a database, and long enough to have a header of sorts" * 4)

        with pytest.raises(sqlite3.DatabaseError):
            copy_database(str(tmp_path / "live.db"), str(tmp_path / "copy.db"))


class TestCopyFile:
    def test_it_copies_the_bytes_into_a_directory_it_creates(self, tmp_path):
        (tmp_path / "settings.json").write_bytes(b'{"version": 13}\n')

        assert copy_file(str(tmp_path / "settings.json"), str(tmp_path / "copy" / "settings.json")) is True
        assert (tmp_path / "copy" / "settings.json").read_bytes() == b'{"version": 13}\n'
        assert (tmp_path / "settings.json").read_bytes() == b'{"version": 13}\n'

    def test_no_file_is_nothing_to_copy(self, tmp_path):
        assert copy_file(str(tmp_path / "absent.json"), str(tmp_path / "copy" / "settings.json")) is False
        assert not (tmp_path / "copy").exists()
