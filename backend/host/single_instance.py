"""One backend at a time, and where to find the one that is running.

Contract: the lock that makes a second backend refuse to start, and the small
file that tells whoever refused where the first one listens.

**The lock lies beside the database, not in the runtime directory**, because a
lock belongs where the thing it protects lies. What it protects is not the port
— the bind falls back, so a taken port costs nothing — but the database: every
unit of work opens with ``BEGIN IMMEDIATE`` and takes the write lock, and two
backends would also both run the start-up routines over the same rows.

``flock`` is used rather than a PID file because the kernel releases it when the
process ends, however it ends. There are no stale locks to reason about and
nothing to clean up after a crash.

**The retry window is not politeness.** A service restart overlaps: the outgoing
process is finishing a database operation while systemd has already started the
replacement. Without a few seconds of patience every restart would write a
failure line and leave the user with no backend.

**The port file is a hint; connecting is the proof.** It carries a port and
nothing else — no PID — and a reader is expected to try the port rather than
believe the file. How long a stale one lingers after a crash depends on where it
landed: under ``XDG_RUNTIME_DIR`` the session clears it at logout, and on the
fallback rung it is the state directory, which nothing clears
(``domain/app_directories.py``). Neither matters to a reader who tries the port.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import socket
import time

# How long a starting backend waits for a predecessor to let go. Long enough to
# cover a service restart's overlap, short enough that a genuinely stuck process
# is reported rather than waited on.
LOCK_RETRY_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.2

# How long a reader waits for the running instance to answer on the port the
# file names. Loopback answers immediately or not at all.
PROBE_TIMEOUT_SECONDS = 0.5

PORT_FILENAME = "port"
LOCK_FILENAME = "backend.lock"


class SingleInstanceLock:
    """An exclusive ``flock`` held for the life of this process."""

    def __init__(self, path: str, retry_seconds: float = LOCK_RETRY_SECONDS) -> None:
        self._path = path
        self._retry_seconds = retry_seconds
        self._fd: int | None = None

    @property
    def path(self) -> str:
        """Where the lock file lives."""
        return self._path

    def acquire(self) -> bool:
        """Take the lock, retrying for the configured window; answer whether we got it.

        Blocking on purpose: this runs before anything else exists — no event
        loop, no database, no port — and there is nothing else for the process
        to be doing until the question is settled.
        """
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        deadline = time.monotonic() + self._retry_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    os.close(fd)
                    raise
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(_LOCK_POLL_INTERVAL)
                continue
            self._fd = fd
            return True

    def release(self) -> None:
        """Let the lock go. Idempotent; the kernel does this anyway at exit."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class PortFile:
    """The note a running backend leaves saying which port it bound."""

    def __init__(self, path: str) -> None:
        self._path = path

    @property
    def path(self) -> str:
        """Where the note lives."""
        return self._path

    def write(self, port: int) -> None:
        """Record *port*, replacing any earlier note.

        Written through a temporary file and renamed, so a reader arriving
        mid-write sees either the old port or the new one and never half a
        number.
        """
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        temporary = f"{self._path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(str(port))
        os.replace(temporary, self._path)

    def read(self) -> int | None:
        """The port the note names, or ``None`` if there is no readable note."""
        try:
            with open(self._path, encoding="utf-8") as handle:
                return int(handle.read().strip())
        except (OSError, ValueError):
            return None

    def remove(self) -> None:
        """Take the note away. Idempotent."""
        with contextlib.suppress(OSError):
            os.unlink(self._path)


def someone_listening(port: int, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Does anything accept a loopback connection on *port*?

    The proof the port file is not. It says only that the port is answered —
    not that a backend answered it — which is all a refusing second process
    needs in order to tell the user something is there.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False
