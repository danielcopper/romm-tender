"""The prune conflict gate: what may run beside a removed-game cleanup, and what holds it off.

``PruneConflicts`` records the four kinds of claim that conflict with a cleanup —
operation, lease, reservation and run claim, defined in CONTEXT.md → Prune
conflicts. An endpoint marked ``@prune_active_blocked`` is refused while a
reservation or a run claim is held, and holds an operation for its call
otherwise. An endpoint marked ``@prune_exclusive_start`` is refused while an
operation or a lease is held, and holds a reservation for its call otherwise; a
run claim does not refuse it, because the prune service refuses a second start
itself.

Both decorators must wrap an ``async def``, because each wrapper awaits it;
decorating a ``def`` raises ``TypeError`` when the class is defined, not when the
method is first called. Both read the gate off the instance's ``_prune_conflicts``
and raise ``RuntimeError`` when it is not wired, rather than run ungated.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

_BLOCKED_MESSAGE = "A removed-game cleanup is in progress; wait for it to finish before changing local game data."
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
    """One live claim, named so a refusal can say who is holding the gate."""

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
        """How many operations and leases are held — the two kinds of claim the exclusive start is refused on."""
        return len(self._operations) + len(self._leases)

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

    async def reserve_start(self) -> str | None:
        """Reserve a cleanup's exclusive start, or answer the refusal message.

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
                self._logger.info(f"Cleanup start refused — gate held by: {self._describe_holders(now)}")
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
        self._log_debug(f"[prune-gate] registered cleanup run {run_id}")

    def release_run(self, run_id: str) -> None:
        """Drop *run_id*'s run claim; releasing one that is not held changes nothing."""
        if run_id in self._runs:
            self._runs.discard(run_id)
            self._log_debug(f"[prune-gate] released cleanup run {run_id}")

    async def retain(self, task: asyncio.Task[Any], label: str) -> None:
        """Hold an operation named *label* until *task* ends."""
        async with self._lock:
            registration = self._register_operation(label)
        self._log_debug(f"[prune-gate] retained {label} for detached work (#{registration})")

        async def release() -> None:
            async with self._lock:
                self._operations.pop(registration, None)
            self._log_debug(f"[prune-gate] released {label} (#{registration})")

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
        self._log_debug(f"[prune-gate] acquired lease {token} ({key})")
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
        self._log_debug(f"[prune-gate] renewed lease {token} (held {held:.0f}s)")
        return True

    async def release_lease(self, token: str) -> None:
        """Release a lease; an unknown or expired token is ignored."""
        async with self._lock:
            self._expire_leases()
            released = self._leases.pop(token, None)
        if released is not None:
            held = asyncio.get_running_loop().time() - released[0].acquired_at
            self._log_debug(f"[prune-gate] released lease {token} after {held:.0f}s")

    async def release_orphaned_leases(self) -> int:
        """Drop every lease and answer how many there were.

        A lease is released by the continuation that received it. A frontend
        whose JS context is torn down mid-call — the double mount at load —
        never reaches that release and never renews either, so the lease pins
        the gate for its full TTL with nobody behind it.

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
                f"[prune-gate] released orphaned lease {token} ({holder.label}) held "
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
                f"[prune-gate] lease {token} ({holder.label}) expired after "
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


def _wired_conflicts(owner: object, decorator: str, method_name: str) -> PruneConflicts:
    conflicts = getattr(owner, "_prune_conflicts", None)
    if conflicts is None:
        raise RuntimeError(
            f"@{decorator} on {method_name!r}: _prune_conflicts is unwired; refusing to bypass the gate."
        )
    return conflicts


def prune_active_blocked(method):
    """Refuse with the canonical failure while a cleanup is running; hold an operation otherwise."""
    if not inspect.iscoroutinefunction(method):
        raise TypeError(
            f"@prune_active_blocked on {method.__name__!r}: the gate awaits what it wraps, so it must be async def."
        )

    @functools.wraps(method)
    async def wrapper(self, *args: Any, **kwargs: Any):
        conflicts = _wired_conflicts(self, "prune_active_blocked", method.__name__)
        registration = await conflicts.hold_operation(method.__name__)
        if registration is None:
            return {"success": False, "reason": "prune_active", "message": _BLOCKED_MESSAGE}
        try:
            return await method(self, *args, **kwargs)
        finally:
            await conflicts.release_operation(registration)

    wrapper._prune_active_blocked = True  # type: ignore[attr-defined]
    return wrapper


def prune_exclusive_start(method):
    """Refuse a cleanup's start while an operation or lease is held; reserve it otherwise.

    The refusal check and the reservation share one lock hold, so no
    conflicting registration can slip between them. The start itself then
    runs unlocked: the reservation already refuses every conflicting endpoint,
    and holding the lock across the start's preview rebuild would make each of
    them wait for that rebuild instead of learning its verdict immediately.
    """
    if not inspect.iscoroutinefunction(method):
        raise TypeError(
            f"@prune_exclusive_start on {method.__name__!r}: the gate awaits what it wraps, so it must be async def."
        )

    @functools.wraps(method)
    async def wrapper(self, *args: Any, **kwargs: Any):
        conflicts = _wired_conflicts(self, "prune_exclusive_start", method.__name__)
        refusal = await conflicts.reserve_start()
        if refusal is not None:
            return {"success": False, "reason": "operation_active", "message": refusal}
        try:
            return await method(self, *args, **kwargs)
        finally:
            conflicts.release_reservation()

    wrapper._prune_exclusive_start = True  # type: ignore[attr-defined]
    return wrapper
