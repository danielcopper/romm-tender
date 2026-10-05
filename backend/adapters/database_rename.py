"""Single owner of moving the database from the name it had before the program was Tender.

Everything that move touches on the filesystem lives here: deciding whether the
old file is there to move, folding its write-ahead log into it, and renaming it
without replacing anything. Which names the two files carry is not decided here
— the composition root hands both paths in.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from adapters.descriptor_paths import rename_noreplace_at

if TYPE_CHECKING:
    import logging

# What SQLite keeps beside a database by the database's own name
# (https://sqlite.org/tempfiles.html): a rename that leaves one of them behind
# leaves whatever it holds behind with it.
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class DatabaseNotFoldedError(RuntimeError):
    """Something beside the old database survived the fold, so moving the file alone would leave it behind."""


class DatabaseRenameAdapter:
    """Moves the database from its old name to its current one, once.

    Run on every start, before anything opens the database under its current
    name, and kept in every later release: a start that finds no old file does
    nothing, so the step costs nothing once it has happened, and an update that
    skips releases still gets it.
    """

    def __init__(self, *, legacy: str, current: str, logger: logging.Logger) -> None:
        self._legacy = legacy
        self._current = current
        self._logger = logger

    def rename(self) -> None:
        """Move the database at the old path to the current one, where only the old one exists.

        With no old file there is nothing to do. With both there, the current
        one is the database and the old one is left exactly as it is, which the
        log says. Otherwise the old database is opened under its own name, read
        and closed — that close folds its write-ahead log into it and removes
        the log and its index (see ``_fold``) — and then the one file left is
        renamed in one atomic step, so an interruption leaves one complete
        database under one of the two names, and never over an existing name.

        Raises what SQLite raises for an old file it cannot open,
        :class:`DatabaseNotFoldedError` where something beside it survived the
        close, and what the rename raises for any reason but a current database
        that appeared meanwhile; in each case nothing is renamed. Starting
        anyway would start on an empty database beside the user's library.
        """
        if not os.path.exists(self._legacy):
            return
        if os.path.exists(self._current):
            self._warn_both()
            return
        try:
            self._fold()
            self._rename_file()
        except FileExistsError:
            self._warn_both()
            return
        except Exception as e:
            self._logger.error(
                f"Could not rename the database {self._legacy} to {self._current}; nothing was renamed: {e}"
            )
            raise
        self._logger.info(f"Renamed the database {self._legacy} to {self._current}")

    def _warn_both(self) -> None:
        self._logger.warning(
            f"Both {self._legacy} and {self._current} exist: "
            f"starting on {self._current} and leaving {self._legacy} as it is"
        )

    def _fold(self) -> None:
        """Open the old database, read it, and close it, so that only the main file is left.

        A connection that reads nothing never looks at the log, so the read is
        what makes SQLite recover it; the close of the last connection is
        what checkpoints it and removes it. ``mode=rw`` opens without creating.
        """
        connection = sqlite3.connect(f"{Path(self._legacy).absolute().as_uri()}?mode=rw", uri=True)
        try:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            connection.close()
        left = [self._legacy + suffix for suffix in _SIDECAR_SUFFIXES if os.path.lexists(self._legacy + suffix)]
        if left:
            raise DatabaseNotFoldedError(
                f"{', '.join(left)} still there after the fold: it is open elsewhere or cannot be written"
            )

    def _rename_file(self) -> None:
        """Rename the old file onto the current name, refusing where the current name exists.

        Not ``os.rename``, which replaces an existing destination without a word.
        """
        legacy_dir = _open_directory(os.path.dirname(self._legacy))
        try:
            current_dir = _open_directory(os.path.dirname(self._current))
            try:
                rename_noreplace_at(
                    legacy_dir, os.path.basename(self._legacy), current_dir, os.path.basename(self._current)
                )
            finally:
                os.close(current_dir)
        finally:
            os.close(legacy_dir)


def _open_directory(path: str) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY)
