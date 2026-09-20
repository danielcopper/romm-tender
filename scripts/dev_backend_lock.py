#!/usr/bin/env python3
"""Who holds the single-instance lock? Asked before a dev task touches Steam.

Contract: two questions, one reading. By default, exit 0 when a backend started
now could take the lock and exit 1 with a message on stderr when one already
holds it — what a task that starts a backend needs. With ``--require-running``
the answers swap: a task that restarts Steam and starts no backend of its own
needs one to be there, because the panel it just built is loaded by the backend
and by nothing else.

Either way the question comes before Steam is touched, so a refusal costs a
message rather than a restart with no panel at the end of it.

The lock is ``backend.lock`` beside the database (``host/single_instance.py``
names the file; ``domain/app_directories.py`` decides where the data directory
is). Both are imported rather than restated here: the ladder that resolves the
directory has three rungs and a copy of it would answer for the wrong one the
first time a ``TENDER_*`` variable is set.

The lock is taken non-blocking and let go again, because that is the only
honest reading of an ``flock``: the file is there whether or not anyone holds
it. So the answer is about the moment it was asked — nothing stops a backend
started in another terminal a second later.

A reading that cannot be made at all is reported as such and lets the task
through: the backend refuses on its own either way, and the only thing lost is
the early word.

Usage:
    python scripts/dev_backend_lock.py                      # refuse if one holds it
    python scripts/dev_backend_lock.py --require-running    # refuse if none does
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from domain.app_directories import resolve_directories  # noqa: E402
from host.single_instance import LOCK_FILENAME  # noqa: E402


def lock_path() -> str:
    """Where the running backend's lock would lie, for this environment."""
    directories = resolve_directories(os.environ, os.path.expanduser("~"), str(REPO))
    return os.path.join(directories.data_dir, LOCK_FILENAME)


def holder_pid(path: str) -> int | None:
    """The pid holding an ``flock`` on *path*, when the kernel says.

    ``/proc/locks`` names the file by device and inode only, so the match is
    made on that pair rather than on any path. No match is ``None``: the lock
    is held, and by whom is simply not established.
    """
    try:
        info = os.stat(path)
        with open("/proc/locks", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    wanted = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
    for line in lines:
        # "1: FLOCK  ADVISORY  WRITE 4021 103:08:4526468 0 EOF"
        fields = line.split()
        if len(fields) < 6 or fields[1] != "FLOCK" or fields[5] != wanted:
            continue
        try:
            return int(fields[4])
        except ValueError:
            return None
    return None


def holder_name(pid: int) -> str | None:
    """What that process calls itself, or ``None`` if it is gone or unreadable."""
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def is_held(path: str) -> bool | None:
    """Does something hold the lock? ``None`` when that could not be read.

    A lock file that is not there is held by nobody — the backend creates it,
    so its absence is an answer rather than a question.
    """
    if not os.path.exists(path):
        return False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return True
        return None
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def describe_holder(path: str) -> str:
    """Which process holds the lock, as far as this machine will say."""
    pid = holder_pid(path)
    if pid is None:
        return "not established"
    name = holder_name(pid)
    return f"pid {pid} ({name})" if name is not None else f"pid {pid}"


def refuse_because_held(path: str) -> None:
    print(
        "tender: a backend is already running — it holds the single-instance lock.\n"
        f"  lock:     {path}\n"
        f"  held by:  {describe_holder(path)}\n"
        "Stop that backend (Ctrl-C in the terminal it runs in), or use a -reset task,\n"
        "which restarts Steam and starts no backend of its own.\n"
        "Steam has not been restarted.",
        file=sys.stderr,
    )


def refuse_because_free(path: str) -> None:
    print(
        "tender: no backend is running — nothing would load the panel into the fresh Steam.\n"
        f"  lock:     {path}\n"
        "Start one first: `mise run dev:backend` runs one against the Steam that is up,\n"
        "and `mise run dev` restarts Steam and runs one itself.\n"
        "Steam has not been restarted.",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-running",
        action="store_true",
        help="refuse when NO backend holds the lock, rather than when one does",
    )
    arguments = parser.parse_args()

    path = lock_path()
    held = is_held(path)
    if held is None:
        print(f"tender: could not read the single-instance lock at {path} — carrying on anyway.", file=sys.stderr)
        return 0
    if arguments.require_running:
        if held:
            return 0
        refuse_because_free(path)
    else:
        if not held:
            return 0
        refuse_because_held(path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
