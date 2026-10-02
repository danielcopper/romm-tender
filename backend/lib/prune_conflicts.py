"""The prune conflicts: what may run beside a removed-game cleanup, and what holds it off.

``PruneConflicts`` records the four kinds of claim that conflict with a cleanup —
operation, lease, reservation and run claim, defined in GLOSSARY.md → Prune
conflicts. An operation is refused while a reservation or a run claim is held;
a reservation is refused while an operation or a lease is held. A run claim
does not refuse a reservation, because the prune service refuses a second
start itself. Use cases reach it through the conflict rules
(``lib/conflict_rules.py``), which check it at their entry; the prune service
registers and releases its run claim here directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

_OPERATION_BLOCKED_MESSAGE = (
    "Another local-data operation is in progress; wait for it to finish before starting cleanup."
)
_LEASE_SECONDS = 300.0

# Plain-language names for the claim keys a user can actually be blocked behind.
# A key with no entry here falls back to the generic refusal text rather than
# leaking an internal token into the UI.
_HOLDER_NAMES = {
    "launch_reconfirm": "checking a game's launch settings",
    "installed_reconcile": "checking which games are installed",
    "sgdb_artwork": "downloading artwork",
    "version_switch": "switching versions",
    "shortcut_removal": "updating Steam shortcuts",
    "rom_uninstall": "uninstalling a game",
    "bulk_uninstall": "uninstalling games",
    "system_core": "changing an emulator core",
    "game_core": "changing an emulator core",
    "disc_selection": "changing the selected disc",
    "sync_complete": "finishing a library sync",
    "sync_stale": "finishing a library sync",
    "download_complete": "finishing a download",
    "prune_complete": "finishing a cleanup",
    "migration_relaunch_options": "finishing a RetroDECK move",
}


class _InfoLogger(Protocol):
    def info(self, msg: str, /) -> None: ...


@dataclass(frozen=True)
class _Holder:
    """One live claim, named so a refusal can say who holds it."""

    label: str
    kind: str
    acquired_at: float


class PruneConflicts:
    """The one record of every claim that conflicts with a removed-game cleanup."""

    def __init__(self, *, logger: _InfoLogger, log_debug: Callable[[str], None]) -> None:
        self._logger = logger
        self._log_debug = log_debug
        self._lock = asyncio.Lock()
        self._operations: dict[int, _Holder] = {}
        # Keyed by token; the value carries the lease's deadline.
        self._leases: dict[str, tuple[_Holder, float]] = {}
        self._reservations = 0
        self._runs: set[str] = set()
        # Separate counters: a lease token is user-visible state the frontend
        # round-trips, so operation registrations must not shift the ids it sees.
        self._next_lease_id = 1
        self._next_operation_id = 1
        # A retained claim's release runs as its own task after the detached work
        # ends, and the loop keeps only a weak reference to a task.
        self._release_tasks: set[asyncio.Task[None]] = set()

    @property
    def conflicting_operations(self) -> int:
        """How many operations and leases are held — the two kinds of claim the exclusive start is refused on.

        A lease past its deadline counts until the next call that sweeps expired leases.
        """
        return len(self._operations) + len(self._leases)

    def held_claims(self) -> tuple[str, ...]:
        """The name of every operation and lease held now, expired leases swept first.

        Answers in one loop turn and takes no lock: the sweep and the read are
        plain dictionary work, so no holder's critical section can be half done
        underneath them.
        """
        self._expire_leases()
        return (
            *(holder.label for holder in self._operations.values()),
            *(holder.label for holder, _deadline in self._leases.values()),
        )

    @property
    def cleanup_running(self) -> bool:
        """Whether a reservation or a registered run is held."""
        return bool(self._reservations) or bool(self._runs)

    async def hold_operation(self, label: str) -> int | None:
        """Register *label* as a conflicting operation; ``None`` while a cleanup is running.

        The check and the registration share one lock hold, so a cleanup cannot
        reserve its start between them.
        """
        async with self._lock:
            self._expire_leases()
            if self.cleanup_running:
                return None
            return self._register_operation(label)

    async def release_operation(self, registration: int) -> None:
        """Drop one operation registration; unknown ids are ignored."""
        async with self._lock:
            self._operations.pop(registration, None)

    async def reserve_start(self, label: str) -> str | None:
        """Reserve a cleanup's exclusive start for the endpoint *label* names, or answer the refusal message.

        ``None`` means the reservation is held and must be given back with
        :meth:`release_reservation`. A refusal logs the complete holder
        inventory at INFO: without it a refused cleanup is indistinguishable
        from a backend that has stopped answering, and the holder cannot be
        identified after the fact.
        """
        async with self._lock:
            self._expire_leases()
            if self._operations or self._leases:
                now = asyncio.get_running_loop().time()
                self._logger.info(f"Cleanup start refused for {label} — held by: {self._describe_holders(now)}")
                return self._blocked_message()
            self._reservations += 1
            return None

    def release_reservation(self) -> None:
        """Give back one reservation taken by :meth:`reserve_start`.

        Synchronous on purpose: the single-threaded loop cannot interleave a
        bare decrement, while awaiting a contended lock here could lose the
        release to a cancellation.
        """
        self._reservations -= 1

    def register_run(self, run_id: str) -> None:
        """Hold *run_id*'s run claim until :meth:`release_run` is called for it."""
        self._runs.add(run_id)
        self._log_debug(f"[prune-conflicts] registered cleanup run {run_id}")

    def release_run(self, run_id: str) -> None:
        """Drop *run_id*'s run claim; releasing one that is not held changes nothing."""
        if run_id in self._runs:
            self._runs.discard(run_id)
            self._log_debug(f"[prune-conflicts] released cleanup run {run_id}")

    async def retain(self, task: asyncio.Task[Any], label: str) -> None:
        """Hold an operation named *label* until *task* ends."""
        async with self._lock:
            registration = self._register_operation(label)
        self._log_debug(f"[prune-conflicts] retained {label} for detached work (#{registration})")

        async def release() -> None:
            async with self._lock:
                self._operations.pop(registration, None)
            self._log_debug(f"[prune-conflicts] released {label} (#{registration})")

        def done(_task: asyncio.Task[Any]) -> None:
            release_task = asyncio.get_running_loop().create_task(release())
            self._release_tasks.add(release_task)
            release_task.add_done_callback(self._release_tasks.discard)

        task.add_done_callback(done)

    async def acquire_lease(self, key: str) -> str:
        """Hold a bounded, tokenized claim across frontend-owned Steam writes."""
        async with self._lock:
            self._expire_leases()
            now = asyncio.get_running_loop().time()
            token = f"{key}:{self._next_lease_id}"
            self._next_lease_id += 1
            self._leases[token] = (_Holder(label=key, kind="lease", acquired_at=now), now + _LEASE_SECONDS)
        self._log_debug(f"[prune-conflicts] acquired lease {token} ({key})")
        return token

    async def renew_lease(self, token: str) -> bool:
        """Extend a live lease; never revive an expired one."""
        async with self._lock:
            self._expire_leases()
            current = self._leases.get(token)
            if current is None:
                return False
            now = asyncio.get_running_loop().time()
            self._leases[token] = (current[0], now + _LEASE_SECONDS)
            held = now - current[0].acquired_at
        self._log_debug(f"[prune-conflicts] renewed lease {token} (held {held:.0f}s)")
        return True

    async def release_lease(self, token: str) -> None:
        """Release a lease; an unknown or expired token is ignored."""
        async with self._lock:
            self._expire_leases()
            released = self._leases.pop(token, None)
        if released is not None:
            held = asyncio.get_running_loop().time() - released[0].acquired_at
            self._log_debug(f"[prune-conflicts] released lease {token} after {held:.0f}s")

    async def release_orphaned_leases(self) -> int:
        """Drop every lease and answer how many there were.

        A lease is released by the continuation that received it. A frontend
        whose JS context is torn down mid-call never reaches that release and
        never renews either, so the lease holds off every cleanup for its full
        TTL with nobody behind it.

        A newly mounted frontend is the proof that no earlier continuation can
        still be running: the context that owned them is gone. That makes mount
        the one moment an orphan is provably safe to drop, which is why this is
        called there and nowhere else. Runs, reservations and operations are
        untouched — only the frontend's own leases are the frontend's to disown.
        """
        async with self._lock:
            self._expire_leases()
            orphaned = list(self._leases.items())
            self._leases.clear()
        now = asyncio.get_running_loop().time()
        for token, (holder, _deadline) in orphaned:
            self._logger.info(
                f"[prune-conflicts] released orphaned lease {token} ({holder.label}) held "
                f"{now - holder.acquired_at:.0f}s by a frontend that is no longer mounted"
            )
        return len(orphaned)

    def _register_operation(self, label: str) -> int:
        registration = self._next_operation_id
        self._next_operation_id += 1
        self._operations[registration] = _Holder(
            label=label, kind="operation", acquired_at=asyncio.get_running_loop().time()
        )
        return registration

    def _expire_leases(self) -> None:
        """Drop timed-out leases.

        An expiry is logged at INFO, not debug: a lease that reached its deadline
        means its owner never released it, which is a leak worth seeing without
        turning debug logging on.
        """
        now = asyncio.get_running_loop().time()
        expired = [token for token, (_holder, deadline) in self._leases.items() if deadline <= now]
        for token in expired:
            holder, _deadline = self._leases.pop(token)
            self._logger.info(
                f"[prune-conflicts] lease {token} ({holder.label}) expired after "
                f"{now - holder.acquired_at:.0f}s without being released"
            )

    def _describe_holders(self, now: float) -> str:
        """One-line inventory of every current holder, for a refusal log line."""
        parts = [
            f"{holder.label} (operation, held {now - holder.acquired_at:.0f}s)" for holder in self._operations.values()
        ]
        parts += [
            f"{holder.label} (lease {token}, held {now - holder.acquired_at:.0f}s, expires in {deadline - now:.0f}s)"
            for token, (holder, deadline) in self._leases.items()
        ]
        return ", ".join(parts) if parts else "none"

    def _blocked_message(self) -> str:
        """Name the holder in the refusal when its claim has a user-facing name.

        The oldest claim is the one actually standing in the way, and a lease
        outranks an operation because it is the one that can persist long
        enough for a person to notice being blocked.
        """
        leases = sorted(self._leases.values(), key=lambda item: item[0].acquired_at)
        operations = sorted(self._operations.values(), key=lambda holder: holder.acquired_at)
        for holder in [item[0] for item in leases] + operations:
            name = _HOLDER_NAMES.get(holder.label)
            if name is not None:
                return (
                    f"Another local-data operation is in progress ({name}); wait for it to finish before starting "
                    "cleanup."
                )
        return _OPERATION_BLOCKED_MESSAGE
