"""In-memory ``GameProcessControl`` implementation for service tests."""

from __future__ import annotations

# The shortcut appId the single-tree shorthand runs under. Tests that only care
# about the ladder bind their ROM to this appId, so the tree is found and the
# ladder runs; tests that care about WHICH tree build theirs with ``add_tree``.
DEFAULT_APP_ID = 3467250184


class FakeGameProcessControlAdapter:
    """In-memory ``GameProcessControl`` for tests.

    Models the process trees Steam started for its shortcuts without touching
    ``/proc``. The table maps a shortcut appId to its tree's pids; :attr:`pids`
    is the single-tree shorthand (assigning it replaces the table with one tree
    under :data:`DEFAULT_APP_ID`, and reading it flattens whatever is there),
    and :meth:`add_tree` builds the multi-tree tables the choice of tree is
    about. ``alive`` is the set of pids that still answer :meth:`is_alive`;
    ``survive_stop`` marks pids that stay alive through the stop request so the
    escalation to :meth:`force_kill` is exercised.

    The two signal-failure switches are deliberately separate, because a test
    that wants a failing force kill must still get a *successful* stop request
    (a pid whose stop request fails never reaches the force rung at all):

    - ``unsignalable`` — :meth:`request_stop` AND :meth:`force_kill` both fail
      (the process vanished before the ladder reached it, or is foreign).
    - ``unkillable`` — only :meth:`force_kill` fails (the process survived the
      stop request, then exited or became unreachable during the grace window).

    Every call is recorded so a test can assert the exact ladder: ``stop_calls``
    is append-only across the fake's whole lifetime, so a duplicate stop request
    for one pid — whether from a retry loop or from a second concurrent call —
    is directly observable. That is the guard for the never-re-request
    save-safety invariant. ``kill_calls`` carries the same weight for the
    never-signal-another-game rule: a pid of another shortcut's tree must
    appear in neither list.
    """

    def __init__(self, pids: list[int] | None = None) -> None:
        self.trees: dict[int, list[int]] = {}
        self.alive: set[int] = set()
        self.unsignalable: set[int] = set()
        self.unkillable: set[int] = set()
        self.survive_stop: set[int] = set()
        self.find_calls: list[int] = []
        self.stop_calls: list[int] = []
        self.kill_calls: list[int] = []
        self.alive_calls: list[int] = []
        if pids:
            self.pids = list(pids)

    @property
    def pids(self) -> list[int]:
        """Every live pid across the table, in tree order."""
        return [pid for tree in self.trees.values() for pid in tree]

    @pids.setter
    def pids(self, value: list[int]) -> None:
        """Replace the table with ONE tree under :data:`DEFAULT_APP_ID`.

        Also re-seeds ``alive`` from *value*: a fresh table describes a fresh set
        of live processes, and a test that wants a pid to read as dead assigns
        ``alive`` afterwards (as it always had to).
        """
        self.trees = {DEFAULT_APP_ID: list(value)} if value else {}
        self.alive = set(value)

    def add_tree(self, app_id: int, pids: list[int]) -> None:
        """Add the live tree Steam started for shortcut *app_id*."""
        self.trees[app_id] = list(pids)
        self.alive.update(pids)

    def find_game_processes(self, app_id: int) -> list[int]:
        self.find_calls.append(app_id)
        return list(self.trees.get(app_id, []))

    def request_stop(self, pid: int) -> bool:
        self.stop_calls.append(pid)
        if pid in self.unsignalable:
            return False
        if pid not in self.survive_stop:
            self.alive.discard(pid)
        return True

    def force_kill(self, pid: int) -> bool:
        self.kill_calls.append(pid)
        if pid in self.unsignalable or pid in self.unkillable:
            return False
        self.alive.discard(pid)
        return True

    def is_alive(self, pid: int) -> bool:
        self.alive_calls.append(pid)
        return pid in self.alive
