"""Copies of the live data a pre-install check builds on, taken without changing the originals.

Owns reading the database and the settings of the install the check stands
beside, and writing copies of them under the check's own roots. The backend
that owns those files may be running while the copy is taken, so nothing here
creates, removes or rewrites a file under the directory it copies FROM. The one
write it cannot avoid is the one every SQLite reader makes: its read marks in
the WAL index of a database another connection holds open.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
from pathlib import Path

# What SQLite puts beside a database it has open or did not close cleanly: the
# WAL and its index, and a rollback journal (https://sqlite.org/tempfiles.html).
_WAL = "-wal"
_SHM = "-shm"
_JOURNAL = "-journal"
_SIDECAR_SUFFIXES = (_WAL, _SHM, _JOURNAL)

_ATTEMPTS = 2

# A file's inode, size and modification time in nanoseconds; ``None`` for no file.
type _Stat = tuple[int, int, int] | None


class LiveDatabaseChangedError(RuntimeError):
    """The live database changed under every copy of it that was tried."""


def copy_database(source: str, target: str) -> bool:
    """Copy the SQLite database at *source* to *target*, as one consistent state of it.

    Returns ``False`` where there is no database at *source*, ``True`` once the
    copy is written. Raises what SQLite raises for a file it cannot read, and
    :class:`LiveDatabaseChangedError` where the database changed while each of
    two copies was taken.

    A plain file copy is not one: a backend still writing leaves the database and
    its WAL out of step between the two reads. How the source is read depends on
    what lies beside it, because a read-only open in WAL mode CREATES the WAL and
    its index where they are missing, and leaves them there:

    - a WAL and its index, or a rollback journal: something has the database
      open, or left it open. A read-only open reads it inside one transaction,
      through SQLite's backup API, and creates nothing.
    - nothing: every committed page is in the file itself, and ``immutable=1``
      reads it without creating anything (https://sqlite.org/uri.html#uriimmutable)
      — and without any lock, so nothing stops a writer that opens it meanwhile.
    - a WAL without its index: no connection has it open, and a read-only open
      would create the index. The two files are copied as bytes beside the
      target and opened there, which folds the WAL into the copy.

    The last two take no lock, so each is judged afterwards by the database's
    and its sidecars' inode, size and modification time: where any of them
    moved, the copy is taken again, by whichever of the three ways fits then. A
    change that moves none of them goes unseen: a write that keeps the size and
    leaves the modification time as it was, which a filesystem that keeps
    coarser timestamps than the writes come can do.
    """
    if not os.path.isfile(source):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for _attempt in range(_ATTEMPTS):
        if _copy_once(source, target):
            return True
    raise LiveDatabaseChangedError(f"{source} changed while each of {_ATTEMPTS} copies of it was taken")


def _copy_once(source: str, target: str) -> bool:
    """Copy *source* to *target* once; whether the copy is known to be one state of it."""
    _remove_database(target)
    before = _footprint(source)
    database, wal, shm, journal = before
    if database is None:
        raise FileNotFoundError(source)
    if wal is not None and shm is None:
        _fold_bytes(source, target)
    elif wal is None and shm is None and journal is None:
        _backup(f"{Path(source).absolute().as_uri()}?mode=ro&immutable=1", target)
    else:
        _backup(f"{Path(source).absolute().as_uri()}?mode=ro", target)
        return True
    return _footprint(source) == before


def _backup(uri: str, target: str) -> None:
    live = sqlite3.connect(uri, uri=True)
    try:
        copy = sqlite3.connect(target)
        try:
            live.backup(copy)
        finally:
            copy.close()
    finally:
        live.close()


def _fold_bytes(source: str, target: str) -> None:
    shutil.copyfile(source, target)
    shutil.copyfile(source + _WAL, target + _WAL)
    copy = sqlite3.connect(target)
    try:
        copy.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        copy.close()


def _footprint(source: str) -> tuple[_Stat, _Stat, _Stat, _Stat]:
    return (_stat(source), _stat(source + _WAL), _stat(source + _SHM), _stat(source + _JOURNAL))


def _stat(path: str) -> _Stat:
    try:
        found = os.stat(path)
    except FileNotFoundError:
        return None
    return (found.st_ino, found.st_size, found.st_mtime_ns)


def _remove_database(target: str) -> None:
    for suffix in ("", *_SIDECAR_SUFFIXES):
        with contextlib.suppress(FileNotFoundError):
            os.remove(target + suffix)


def copy_file(source: str, target: str) -> bool:
    """Copy the file at *source* to *target*; ``False`` where there is none to copy."""
    if not os.path.isfile(source):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(source, target)
    return True
