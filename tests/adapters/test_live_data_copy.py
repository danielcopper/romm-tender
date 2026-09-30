"""Copies of the live data a pre-install check builds on, and the originals left as they were."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from adapters.live_data_copy import copy_database, copy_file

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

    def test_a_database_held_open_is_copied_with_what_its_wal_holds(self, tmp_path):
        _database(tmp_path / "live.db", "in the file")
        writer = sqlite3.connect(tmp_path / "live.db")
        try:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO marker VALUES ('in the wal')")
            writer.commit()

            copy_database(str(tmp_path / "live.db"), str(tmp_path / "copy.db"))
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
