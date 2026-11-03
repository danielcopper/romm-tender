"""Running-Steam adapter — concrete ``RunningSteamReader`` over Steam's pid file and ``/proc``.

Owns the reading of which Steam runs on this machine and how: its pid from
``~/.steam/steam.pid``, the executable that pid resolves to, the kernel's start
time for it, whether its environment says gamescope started it, and the X
display of the desktop session it was started from. Direct file reads only.

Every read is fail-soft: a Steam that is not running, a stale pid file, a pid
that is something else now, and a file that cannot be read all answer ``None``,
which the watcher treats as "nothing to do".
"""

from __future__ import annotations

import os

from domain.controller_focus import GAMESCOPE_DISPLAY_VAR, RunningSteam, SteamIdentity, XDisplay

_PROC = "/proc"

# Where Steam writes its own pid; a Steam installed as a Flatpak keeps its pid
# file inside the sandbox's home and writes a sandbox pid, so it is never read.
_PID_FILE = os.path.join(".steam", "steam.pid")
_STEAM_COMM = "steam"

# The X client conventions: an unset XAUTHORITY means this file in the home.
_DEFAULT_AUTHORITY = ".Xauthority"

# Bound on the walk up from Steam. The 64-bit client runs a few levels deep
# inside its container; the cap only bounds a process table rewritten
# underneath the walk.
_MAX_DEPTH = 16

# ``/proc/<pid>/stat`` field 22 is the start time in clock ticks since boot;
# the fields after the parenthesised command start at field 3.
_START_TIME_AFTER_COMM = 22 - 3


class RunningSteamAdapter:
    """Real ``RunningSteamReader``: one reading of the Steam this user runs, from ``/proc``."""

    def __init__(self, *, user_home: str) -> None:
        self._user_home = user_home

    def read(self) -> RunningSteam | None:
        """Return the running Steam, or ``None`` where none runs or it could not be read.

        A pid file naming a process that is not ``steam``, or whose executable,
        start time or environment cannot be read, answers ``None``.

        Gamescope started Steam where its own environment, or that of a process
        above it in this process's user namespace, carries
        :data:`GAMESCOPE_DISPLAY_VAR`: the 64-bit client's container need not
        pass the variable on. The display is the one named by the first
        process from Steam upwards in this process's user namespace whose
        authority file can be read from here, ``None`` where none names one —
        a process just outside the container still names the container's own
        authority file, which does not exist on the host.
        """
        pid = self._pid_from_file()
        if pid is None or _comm(pid) != _STEAM_COMM:
            return None
        start_time = _start_time(pid)
        environ = _environ(pid)
        try:
            exe = os.readlink(f"{_PROC}/{pid}/exe")
        except OSError:
            return None
        if start_time is None or environ is None:
            return None
        host_environs = _host_environs_from(pid)
        return RunningSteam(
            identity=SteamIdentity(pid=pid, start_time=start_time),
            exe=exe,
            under_gamescope=any(GAMESCOPE_DISPLAY_VAR in env for env in (environ, *host_environs)),
            display=next((d for d in map(self._display_of, host_environs) if d is not None), None),
        )

    def _pid_from_file(self) -> int | None:
        try:
            with open(os.path.join(self._user_home, _PID_FILE), encoding="utf-8") as f:
                text = f.read().strip()
        except (OSError, UnicodeDecodeError):
            return None
        return int(text) if text.isdigit() and int(text) > 0 else None

    def _display_of(self, environ: dict[str, str]) -> XDisplay | None:
        name = environ.get("DISPLAY", "")
        if not name:
            return None
        authority = environ.get("XAUTHORITY") or os.path.join(self._user_home, _DEFAULT_AUTHORITY)
        if not (os.path.isfile(authority) and os.access(authority, os.R_OK)):
            return None
        return XDisplay(name=name, authority_path=authority)


def _host_environs_from(pid: int) -> list[dict[str, str]]:
    """The readable environments of *pid* and the processes above it that share this process's user namespace."""
    own_namespace = _user_namespace("self")
    if own_namespace is None:
        return []
    environs: list[dict[str, str]] = []
    current = pid
    for _ in range(_MAX_DEPTH):
        if _user_namespace(str(current)) == own_namespace:
            environ = _environ(current)
            if environ is not None:
                environs.append(environ)
        parent = _parent(current)
        if parent is None or parent <= 0:
            break
        current = parent
    return environs


def _comm(pid: int) -> str | None:
    try:
        with open(f"{_PROC}/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _start_time(pid: int) -> int | None:
    try:
        with open(f"{_PROC}/{pid}/stat", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    # The command sits in parentheses and may itself contain spaces and ")".
    fields = text[text.rfind(")") + 1 :].split()
    if len(fields) <= _START_TIME_AFTER_COMM or not fields[_START_TIME_AFTER_COMM].isdigit():
        return None
    return int(fields[_START_TIME_AFTER_COMM])


def _environ(pid: int) -> dict[str, str] | None:
    try:
        with open(f"{_PROC}/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    environ: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        name, sep, value = entry.partition(b"=")
        if sep and name:
            environ[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return environ


def _parent(pid: int) -> int | None:
    try:
        with open(f"{_PROC}/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


def _user_namespace(pid: str) -> str | None:
    try:
        return os.readlink(f"{_PROC}/{pid}/ns/user")
    except OSError:
        return None
