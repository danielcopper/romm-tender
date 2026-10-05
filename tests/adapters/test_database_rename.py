"""Tests for the start-up move of the database from its old name to its current one.

Every case runs against real files under ``tmp_path`` and a real SQLite: what
the adapter is for is what a rename does to a database and the files SQLite
keeps beside it, and a faked filesystem would prove none of it.
"""

from __future__ import annotations

import errno
import logging
import sqlite3
import subprocess
import sys

import pytest

import adapters.database_rename as database_rename
from adapters.database_rename import DatabaseNotFoldedError, DatabaseRenameAdapter

_LEGACY = "romm_sync.db"
_CURRENT = "romm-tender.db"


def _make(tmp_path, logger: logging.Logger) -> DatabaseRenameAdapter:
    return DatabaseRenameAdapter(legacy=str(tmp_path / _LEGACY), current=str(tmp_path / _CURRENT), logger=logger)


def _database_with_a_hot_wal(path) -> None:
    """A WAL-mode database whose last commit lives only in its WAL, left as a process that died leaves it.

    ``os._exit`` skips the close that would fold the WAL into the file, so the
    commit is in ``-wal`` and nowhere else.
    """
    script = (
        "import os, sqlite3\n"
        f"db = sqlite3.connect({str(path)!r}, isolation_level=None)\n"
        "db.execute('PRAGMA journal_mode=WAL')\n"
        "db.execute('CREATE TABLE marker (note TEXT)')\n"
        "db.execute(\"INSERT INTO marker VALUES ('in the wal')\")\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


def _notes(path) -> list[str]:
    db = sqlite3.connect(path)
    try:
        return [row[0] for row in db.execute("SELECT note FROM marker ORDER BY note")]
    finally:
        db.close()


def _names(tmp_path) -> list[str]:
    return sorted(path.name for path in tmp_path.iterdir())


class TestOnlyTheOldNameExists:
    def test_the_library_is_under_the_current_name_with_the_commit_its_wal_held(self, tmp_path, logger, caplog):
        _database_with_a_hot_wal(tmp_path / _LEGACY)
        assert _names(tmp_path) == [_LEGACY, f"{_LEGACY}-shm", f"{_LEGACY}-wal"]

        with caplog.at_level(logging.INFO, logger=logger.name):
            _make(tmp_path, logger).rename()

        assert _names(tmp_path) == [_CURRENT]
        assert _notes(tmp_path / _CURRENT) == ["in the wal"]
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.INFO, f"Renamed the database {tmp_path / _LEGACY} to {tmp_path / _CURRENT}")
        ]

    def test_a_second_start_finds_nothing_to_do(self, tmp_path, logger, caplog):
        _database_with_a_hot_wal(tmp_path / _LEGACY)
        _make(tmp_path, logger).rename()
        before = (tmp_path / _CURRENT).read_bytes()

        with caplog.at_level(logging.DEBUG, logger=logger.name):
            _make(tmp_path, logger).rename()

        assert _names(tmp_path) == [_CURRENT]
        assert (tmp_path / _CURRENT).read_bytes() == before
        assert caplog.records == []

    def test_hand_made_copies_beside_it_are_left_alone(self, tmp_path, logger):
        _database_with_a_hot_wal(tmp_path / _LEGACY)
        backups = {
            f"{_LEGACY}.backup-20260901": b"a copy made by hand\n",
            f"{_LEGACY}.backup-20260902-wal": b"and its log\n",
        }
        for name, content in backups.items():
            (tmp_path / name).write_bytes(content)

        _make(tmp_path, logger).rename()

        assert _names(tmp_path) == sorted([_CURRENT, *backups])
        assert {name: (tmp_path / name).read_bytes() for name in backups} == backups


class TestTheOldNameCannotBeMoved:
    def test_a_file_sqlite_cannot_open_is_renamed_nowhere_and_the_start_fails(self, tmp_path, logger, caplog):
        content = b"not a database, and long enough to have a header" * 4
        (tmp_path / _LEGACY).write_bytes(content)
        adapter = _make(tmp_path, logger)

        with caplog.at_level(logging.INFO, logger=logger.name), pytest.raises(sqlite3.DatabaseError):
            adapter.rename()

        assert _names(tmp_path) == [_LEGACY]
        assert (tmp_path / _LEGACY).read_bytes() == content
        assert [r.levelno for r in caplog.records] == [logging.ERROR]
        assert "nothing was renamed" in caplog.records[0].getMessage()

    def test_a_database_another_connection_holds_is_not_renamed_away_from_its_wal(self, tmp_path, logger):
        """The close folds the WAL only as the LAST connection; renaming the file alone would leave commits behind."""
        _database_with_a_hot_wal(tmp_path / _LEGACY)
        holder = sqlite3.connect(tmp_path / _LEGACY)
        try:
            holder.execute("SELECT count(*) FROM marker").fetchone()
            adapter = _make(tmp_path, logger)

            with pytest.raises(DatabaseNotFoldedError):
                adapter.rename()

            assert _names(tmp_path) == [_LEGACY, f"{_LEGACY}-shm", f"{_LEGACY}-wal"]
        finally:
            holder.close()
        assert _notes(tmp_path / _LEGACY) == ["in the wal"]

    def test_a_rename_the_filesystem_refuses_leaves_the_old_file_and_fails_the_start(
        self, tmp_path, logger, monkeypatch
    ):
        _database_with_a_hot_wal(tmp_path / _LEGACY)

        def refuse(*_args):
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        monkeypatch.setattr(database_rename, "rename_noreplace_at", refuse)
        adapter = _make(tmp_path, logger)

        with pytest.raises(OSError, match="cross-device"):
            adapter.rename()

        assert _names(tmp_path) == [_LEGACY]
        assert _notes(tmp_path / _LEGACY) == ["in the wal"]


class TestTheCurrentNameExists:
    def test_with_both_the_current_one_is_the_database_and_nothing_is_moved_or_deleted(self, tmp_path, logger, caplog):
        (tmp_path / _LEGACY).write_bytes(b"the old library\n")
        (tmp_path / f"{_LEGACY}-wal").write_bytes(b"its log\n")
        (tmp_path / _CURRENT).write_bytes(b"the current library\n")
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

        with caplog.at_level(logging.INFO, logger=logger.name):
            _make(tmp_path, logger).rename()

        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (
                logging.WARNING,
                f"Both {tmp_path / _LEGACY} and {tmp_path / _CURRENT} exist: "
                f"starting on {tmp_path / _CURRENT} and leaving {tmp_path / _LEGACY} as it is",
            )
        ]

    def test_a_current_database_that_appears_during_the_move_is_never_replaced(
        self, tmp_path, logger, caplog, monkeypatch
    ):
        _database_with_a_hot_wal(tmp_path / _LEGACY)
        fold = DatabaseRenameAdapter._fold

        def fold_then_appear(self):
            fold(self)
            (tmp_path / _CURRENT).write_bytes(b"written meanwhile\n")

        monkeypatch.setattr(DatabaseRenameAdapter, "_fold", fold_then_appear)

        with caplog.at_level(logging.INFO, logger=logger.name):
            _make(tmp_path, logger).rename()

        assert (tmp_path / _CURRENT).read_bytes() == b"written meanwhile\n"
        assert _notes(tmp_path / _LEGACY) == ["in the wal"]
        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    def test_only_the_current_name_is_left_as_it_is(self, tmp_path, logger, caplog):
        (tmp_path / _CURRENT).write_bytes(b"the current library\n")

        with caplog.at_level(logging.DEBUG, logger=logger.name):
            _make(tmp_path, logger).rename()

        assert _names(tmp_path) == [_CURRENT]
        assert (tmp_path / _CURRENT).read_bytes() == b"the current library\n"
        assert caplog.records == []

    def test_neither_creates_nothing(self, tmp_path, logger, caplog):
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            _make(tmp_path, logger).rename()

        assert _names(tmp_path) == []
        assert caplog.records == []
