"""Copies of the live data a pre-install check builds on, taken without changing the originals.

Owns reading the database and the settings of the install the check stands
beside, and writing copies of them under the check's own roots. Nothing here
ever writes under the directory it copies FROM: the backend that owns those
files may be running while the copy is taken.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

# What SQLite puts beside a database it has open or did not close cleanly: the
# WAL and its index, and a rollback journal (https://sqlite.org/tempfiles.html).
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def copy_database(source: str, target: str) -> bool:
    """Copy the SQLite database at *source* to *target* through SQLite's backup API.

    Returns ``False`` where there is no database at *source*, ``True`` once the
    copy is written. Raises what SQLite raises for a file it cannot read.

    A plain file copy is not one: a backend still writing leaves the database and
    its WAL out of step between the two reads. The backup reads both inside one
    transaction instead.

    How the source is opened depends on what lies beside it, because a
    read-only open in WAL mode CREATES the WAL and its index where they are
    missing, and leaves them there. With a sidecar present something has the
    database open, or left it open, and a read-only open is the one that sees
    what the WAL holds; it writes only the index's read marks, which every
    reader writes. With none, every committed page is in the file itself, and
    ``immutable=1`` reads it without creating anything
    (https://sqlite.org/uri.html#uriimmutable).
    """
    if not os.path.isfile(source):
        return False
    quiet = not any(os.path.exists(source + suffix) for suffix in _SIDECAR_SUFFIXES)
    uri = f"{Path(source).absolute().as_uri()}?mode=ro{'&immutable=1' if quiet else ''}"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    live = sqlite3.connect(uri, uri=True)
    try:
        copy = sqlite3.connect(target)
        try:
            live.backup(copy)
        finally:
            copy.close()
    finally:
        live.close()
    return True


def copy_file(source: str, target: str) -> bool:
    """Copy the file at *source* to *target*; ``False`` where there is none to copy."""
    if not os.path.isfile(source):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(source, target)
    return True
