"""Steam-process-tree adapter — concrete ``GameProcessControl`` over ``/proc``.

Owns the host-side view of what a shortcut Steam started is running: the
``reaper`` process Steam starts each shortcut under, the ``/proc`` tree beneath
it, and the signals sent to that tree's processes. Direct file reads and
``os.kill`` only — no ``subprocess``.

Steam starts every shortcut as ``steam-launch-wrapper … -- reaper SteamLaunch
AppId=<appId> -- <exe> <launch options>``, one ``reaper`` per start, so two
games in the same emulator are two trees and the shortcut's appId names the one
to stop (docs/architecture/save-file-sync-architecture.md, "Stop Game"). What
the emulator is — a Flatpak, an AppImage, a native program — does not matter:
whatever the shortcut's command started stays below its ``reaper``.

Every read is guarded and fail-soft, like ``renderer_rss``: a process that
vanishes mid-scan is a normal race and is skipped, so one dead pid never blanks
the whole result. A pid that cannot be identified is never signalled.
"""

from __future__ import annotations

import os
import signal

_PROC = "/proc"

# Steam's ``reaper`` and the arguments it is started with: ``SteamLaunch``
# first, the shortcut's ``AppId=<id>`` among its own options, and ``--`` before
# the command it runs. A token after that ``--`` belongs to the game's command
# and never identifies the tree.
_REAPER_COMM = "reaper"
_STEAM_LAUNCH = "SteamLaunch"
_APP_ID_OPTION = "AppId="
_END_OF_OPTIONS = "--"

# ``bwrap`` is the sandbox scaffolding, never the game. The descent passes
# through it, but it is never returned as a signal target: killing it collapses
# the sandbox under the emulator instead of letting the emulator flush its save.
_BWRAP_COMM = "bwrap"

# Process states that mean "already exited": ``Z`` is a zombie awaiting reaping
# and ``X``/``x`` is a dead task. Neither will ever run code again.
_EXITED_STATES = frozenset({"Z", "X", "x"})

# Depth cap on the ``/proc`` descent. The real tree is shallow (reaper → bwrap →
# bwrap → the emulator's launcher → the emulator); the cap only bounds a
# pathological read of a process table being rewritten underneath us.
_MAX_DEPTH = 16


class SteamProcessTreeAdapter:
    """Real ``GameProcessControl`` backed by Steam's ``reaper`` trees in ``/proc``."""

    def find_game_processes(self, app_id: int) -> list[int]:
        """Return the signal targets of the tree Steam started for shortcut *app_id*, deepest first.

        Finds this user's ``reaper`` whose own options carry ``AppId=<app_id>``
        and walks its ``/proc`` descendants. The ``reaper`` itself, ``bwrap``
        processes and pids whose identity cannot be read are excluded. An empty
        list means the shortcut is not running — or ``/proc`` is unreadable,
        which is indistinguishable from here and means the same thing: nothing
        to stop.
        """
        pids: list[int] = []
        for reaper in self._reapers_of(app_id):
            pids.extend(pid for pid in self._descendants(reaper) if pid not in pids)
        return pids

    def request_stop(self, pid: int) -> bool:
        """Send one ``SIGTERM`` to *pid*, returning whether it was delivered.

        Delivery only — this method has no memory and no retry; sending exactly
        one stop request per process is the caller's contract (and a hard save-
        safety rule; see ``services.game_process``).
        """
        return self._signal(pid, signal.SIGTERM)

    def force_kill(self, pid: int) -> bool:
        """Send ``SIGKILL`` to *pid*, returning whether it was delivered."""
        return self._signal(pid, signal.SIGKILL)

    def is_alive(self, pid: int) -> bool:
        """Return True while *pid* is a live process, reading ``/proc/<pid>/status``.

        A zombie or dead task reads as **not** alive: it has already run its
        exit path, so treating it as running would spend the caller's whole
        grace window waiting for something that will never change. An
        unreadable ``status`` means the process is gone — the normal exit.
        """
        try:
            with open(f"{_PROC}/{pid}/status", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("State:"):
                        # Line shape: "State:\tS (sleeping)" — the second field
                        # is the single-letter state code.
                        return line.split()[1] not in _EXITED_STATES
        except (OSError, UnicodeDecodeError, IndexError):
            return False
        return False

    # ── Steam's reaper ───────────────────────────────────────────────────────

    def _reapers_of(self, app_id: int) -> list[int]:
        """Return the pid of every ``reaper`` of this user that Steam started for *app_id*."""
        try:
            entries = os.listdir(_PROC)
        except OSError:
            return []
        uid = os.getuid()
        return sorted(
            int(entry) for entry in entries if entry.isdigit() and self._is_reaper_of(int(entry), app_id, uid)
        )

    def _is_reaper_of(self, pid: int, app_id: int, uid: int) -> bool:
        """Return True when *pid* is a ``reaper`` owned by *uid* whose own options name *app_id*.

        A process of another user is never one: its tree is not ours to signal.
        """
        try:
            if os.stat(f"{_PROC}/{pid}").st_uid != uid:
                return False
        except OSError:
            return False
        if self._comm(pid) != _REAPER_COMM:
            return False
        tokens = self._cmdline_tokens(pid)
        if len(tokens) < 2 or tokens[1] != _STEAM_LAUNCH:
            return False
        options = tokens[2 : tokens.index(_END_OF_OPTIONS)] if _END_OF_OPTIONS in tokens else tokens[2:]
        return f"{_APP_ID_OPTION}{app_id}" in options

    # ── /proc process tree ───────────────────────────────────────────────────

    def _descendants(self, root_pid: int) -> list[int]:
        """Return *root_pid*'s descendants as signal targets, deepest level first.

        Breadth-first by level, emitted in reverse level order: the emulator
        (the deepest process) is handed back before the shell wrappers whose
        exit would tear it down mid-write. The root itself is never returned.
        The visited set makes a process table that mutates during the walk
        harmless, and ``_MAX_DEPTH`` bounds it.
        """
        levels: list[list[int]] = []
        seen = {root_pid}
        frontier = self._unseen_children([root_pid], seen)
        for _ in range(_MAX_DEPTH):
            if not frontier:
                break
            level = [pid for pid in frontier if self._is_signal_target(pid)]
            if level:
                levels.append(level)
            frontier = self._unseen_children(frontier, seen)
        return [pid for level in reversed(levels) for pid in level]

    def _unseen_children(self, parents: list[int], seen: set[int]) -> list[int]:
        """Return the children of *parents* not in *seen*, adding them to it."""
        children: list[int] = []
        for pid in parents:
            for child in self._children(pid):
                if child not in seen:
                    seen.add(child)
                    children.append(child)
        return children

    @staticmethod
    def _children(pid: int) -> list[int]:
        """Return *pid*'s direct child PIDs from ``/proc/<pid>/task/<pid>/children``.

        Empty on any read failure — a process that exits between the parent scan
        and this read simply has no children to contribute.
        """
        try:
            with open(f"{_PROC}/{pid}/task/{pid}/children", encoding="utf-8") as f:
                raw = f.read()
        except (OSError, UnicodeDecodeError):
            return []
        # Space-separated pid list, possibly with a trailing space and newline.
        return [int(token) for token in raw.split() if token.isdigit()]

    @staticmethod
    def _cmdline_tokens(pid: int) -> list[str]:
        """Return *pid*'s argv tokens from ``/proc/<pid>/cmdline``.

        The file is a NUL-separated argv with a trailing NUL, so the empty
        trailing field is dropped. Empty on any read failure and for a process
        that has no argv at all (a kernel thread, or one already reaped).
        """
        try:
            with open(f"{_PROC}/{pid}/cmdline", encoding="utf-8") as f:
                raw = f.read()
        except (OSError, UnicodeDecodeError):
            return []
        return [token for token in raw.split("\0") if token]

    @staticmethod
    def _comm(pid: int) -> str | None:
        """Return *pid*'s ``comm``, or None when it cannot be read."""
        try:
            with open(f"{_PROC}/{pid}/comm", encoding="utf-8") as f:
                return f.read().strip()
        except (OSError, UnicodeDecodeError):
            return None

    def _is_signal_target(self, pid: int) -> bool:
        """Return True when *pid* is a process the stop ladder may signal.

        False for the ``bwrap`` sandbox scaffolding, and false for any pid whose
        ``comm`` cannot be read — an unidentifiable process is never signalled,
        and a vanished one is the ordinary mid-scan race.
        """
        comm = self._comm(pid)
        return comm is not None and comm != _BWRAP_COMM

    @staticmethod
    def _signal(pid: int, sig: signal.Signals) -> bool:
        """Send *sig* to *pid*, returning whether the kernel accepted it.

        False on every failure — the process already exited
        (``ProcessLookupError``) or belongs to another user
        (``PermissionError``); both are subclasses of ``OSError`` and neither is
        actionable here.
        """
        try:
            os.kill(pid, sig)
        except OSError:
            return False
        return True
