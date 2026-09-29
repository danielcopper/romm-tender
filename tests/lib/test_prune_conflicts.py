from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lib.conflict_rules import ConflictRuleSet
from lib.prune_conflicts import _LEASE_SECONDS, PruneConflicts


class _RecordingLogger:
    def __init__(self) -> None:
        self.info_lines: list[str] = []

    def info(self, message: str) -> None:
        self.info_lines.append(message)


class _LoopClock:
    """The running loop's clock, which the prune conflicts read their lease deadlines from, held still until advanced.

    A test that sleeps through a lease measures the machine's load along with
    the lease. Nothing a test using this awaits may sleep: a timer on a clock
    that does not move never fires.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loop = asyncio.get_running_loop()
        self._now = loop.time()
        monkeypatch.setattr(loop, "time", lambda: self._now)

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _conflicts() -> tuple[PruneConflicts, _RecordingLogger, list[str]]:
    logger = _RecordingLogger()
    debug_lines: list[str] = []
    return PruneConflicts(logger=logger, log_debug=debug_lines.append), logger, debug_lines


class _Endpoints:
    """Two use cases over one prune conflicts record, each checking its rule the way a service does."""

    def __init__(self, conflicts: PruneConflicts) -> None:
        self._prune_conflicts = conflicts
        self._rules = ConflictRuleSet(
            prune_conflicts=conflicts,
            update_in_progress=lambda: False,
            migration_pending=lambda: False,
            sync_in_flight=lambda: False,
        )
        self.called = False

    async def mutate(self) -> dict[str, Any]:
        async with self._rules.hold("mutate", prune=True) as refusal:
            if refusal is not None:
                return refusal
            self.called = True
            return {"success": True}

    async def start_prune(self) -> dict[str, Any]:
        async with self._rules.hold_start("start_prune") as refusal:
            if refusal is not None:
                return refusal
            return await self._start()

    async def _start(self) -> dict[str, Any]:
        return {"success": True}


def _endpoints() -> tuple[_Endpoints, PruneConflicts, _RecordingLogger, list[str]]:
    conflicts, logger, debug_lines = _conflicts()
    return _Endpoints(conflicts), conflicts, logger, debug_lines


@pytest.mark.asyncio
async def test_a_registered_run_refuses_with_the_canonical_shape() -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    conflicts.register_run("run-1")

    result = await endpoints.mutate()

    assert result["success"] is False
    assert result["reason"] == "prune_active"
    assert result["message"]
    assert endpoints.called is False
    # The refused call registered nothing.
    assert conflicts.conflicting_operations == 0


@pytest.mark.asyncio
async def test_allows_operation_when_no_cleanup_is_running() -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()

    assert await endpoints.mutate() == {"success": True}
    assert endpoints.called is True
    assert conflicts.conflicting_operations == 0


@pytest.mark.asyncio
async def test_releasing_the_run_frees_the_endpoint() -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    conflicts.register_run("run-1")
    assert (await endpoints.mutate())["reason"] == "prune_active"

    conflicts.release_run("run-1")

    assert conflicts.cleanup_running is False
    assert await endpoints.mutate() == {"success": True}


@pytest.mark.asyncio
async def test_a_double_release_of_a_run_is_harmless() -> None:
    endpoints, conflicts, _logger, debug_lines = _endpoints()
    conflicts.register_run("run-1")

    conflicts.release_run("run-1")
    conflicts.release_run("run-1")

    assert conflicts.cleanup_running is False
    assert await endpoints.mutate() == {"success": True}
    assert sum("released cleanup run run-1" in line for line in debug_lines) == 1


def test_releasing_one_run_leaves_another_registered() -> None:
    conflicts, _logger, _debug = _conflicts()
    conflicts.register_run("run-1")
    conflicts.register_run("run-2")

    conflicts.release_run("run-1")

    assert conflicts.cleanup_running is True


@pytest.mark.asyncio
async def test_a_registered_run_does_not_refuse_the_exclusive_start() -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    conflicts.register_run("run-1")

    # The cleanup's owner refuses a second start itself; the reservation's check
    # is about the operations and leases a cleanup would run over.
    assert await endpoints.start_prune() == {"success": True}


@pytest.mark.asyncio
async def test_prune_start_refuses_operation_that_entered_before_it() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Endpoints(_Endpoints):
        async def slow_mutation(self):
            async with self._rules.hold("slow_mutation", prune=True) as refusal:
                assert refusal is None
                entered.set()
                await release.wait()
                return {"success": True}

    conflicts, _logger, _debug = _conflicts()
    endpoints = Endpoints(conflicts)
    mutation = asyncio.create_task(endpoints.slow_mutation())
    await entered.wait()
    result = await endpoints.start_prune()
    assert result["success"] is False
    assert result["reason"] == "operation_active"
    release.set()
    await mutation


@pytest.mark.asyncio
async def test_conflicting_operation_is_refused_without_awaiting_a_slow_prune_start() -> None:
    starting = asyncio.Event()
    finish_start = asyncio.Event()
    order: list[str] = []

    class Endpoints(_Endpoints):
        async def _start(self):
            starting.set()
            await finish_start.wait()
            order.append("start")
            return {"success": True}

    conflicts, _logger, _debug = _conflicts()
    endpoints = Endpoints(conflicts)
    start = asyncio.create_task(endpoints.start_prune())
    await starting.wait()

    result = await endpoints.mutate()
    order.append("mutation")

    assert result["success"] is False
    assert result["reason"] == "prune_active"
    assert endpoints.called is False
    assert order == ["mutation"]

    finish_start.set()
    assert await start == {"success": True}
    assert order == ["mutation", "start"]


@pytest.mark.asyncio
async def test_a_run_registered_inside_the_reservation_keeps_refusing_after_the_start_returns() -> None:
    """The reservation and the run claim overlap, so the refusal has no gap between them."""
    observed: list[str] = []

    class Endpoints(_Endpoints):
        async def _start(self):
            observed.append((await self.mutate())["reason"])
            self._prune_conflicts.register_run("run-1")
            observed.append((await self.mutate())["reason"])
            return {"success": True}

    conflicts, _logger, _debug = _conflicts()
    endpoints = Endpoints(conflicts)

    assert await endpoints.start_prune() == {"success": True}

    assert observed == ["prune_active", "prune_active"]
    assert (await endpoints.mutate())["reason"] == "prune_active"
    conflicts.release_run("run-1")
    assert await endpoints.mutate() == {"success": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["refused", "raised"])
async def test_reservation_is_released_when_the_start_begins_no_run(outcome) -> None:
    class Endpoints(_Endpoints):
        async def _start(self):
            if outcome == "raised":
                raise RuntimeError("start blew up")
            return {"success": False, "reason": "stale_preview", "message": "stale"}

    conflicts, _logger, _debug = _conflicts()
    endpoints = Endpoints(conflicts)
    if outcome == "raised":
        with pytest.raises(RuntimeError, match="start blew up"):
            await endpoints.start_prune()
    else:
        assert (await endpoints.start_prune())["reason"] == "stale_preview"

    assert await endpoints.mutate() == {"success": True}
    assert endpoints.called is True


@pytest.mark.asyncio
async def test_detached_task_retains_conflict_claim_for_its_full_lifetime() -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    await conflicts.retain(task, "start_download")
    assert (await endpoints.start_prune())["reason"] == "operation_active"

    release.set()
    await task
    await asyncio.sleep(0)
    assert await endpoints.start_prune() == {"success": True}


@pytest.mark.asyncio
async def test_a_retained_claims_release_is_held_by_the_prune_conflicts_until_it_finishes() -> None:
    """The loop holds a task only weakly, so an unowned release could be collected before it runs."""
    conflicts, _logger, _debug = _conflicts()
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    await conflicts.retain(task, "start_download")

    release.set()
    # The retain's done callback was added before this await's, so it has
    # already created the release task by the time the await returns.
    await task
    pending = set(conflicts._release_tasks)
    assert len(pending) == 1
    assert not next(iter(pending)).done()

    await asyncio.gather(*pending)
    await asyncio.sleep(0)
    assert conflicts._release_tasks == set()
    assert conflicts.conflicting_operations == 0


@pytest.mark.asyncio
async def test_refusal_logs_the_holder_that_is_actually_blocking() -> None:
    endpoints, conflicts, logger, _debug = _endpoints()
    await conflicts.acquire_lease("launch_reconfirm")

    result = await endpoints.start_prune()

    assert result["reason"] == "operation_active"
    refusal = next(line for line in logger.info_lines if "start refused" in line)
    assert "refused for start_prune" in refusal
    assert "launch_reconfirm" in refusal
    assert "lease launch_reconfirm:1" in refusal
    assert "held 0s" in refusal
    assert "expires in" in refusal


@pytest.mark.asyncio
async def test_refusal_message_names_a_labelled_holder_in_plain_language() -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    await conflicts.acquire_lease("launch_reconfirm")

    result = await endpoints.start_prune()

    assert "checking a game's launch settings" in result["message"]
    assert "wait for it to finish" in result["message"]


@pytest.mark.asyncio
async def test_refusal_message_stays_generic_for_an_unnamed_holder() -> None:
    endpoints, conflicts, logger, _debug = _endpoints()
    await conflicts.acquire_lease("some_internal_key")

    result = await endpoints.start_prune()

    # An internal token must never reach the user as if it were a sentence.
    assert "some_internal_key" not in result["message"]
    assert result["message"] == (
        "Another local-data operation is in progress; wait for it to finish before starting cleanup."
    )
    # …but the log still names it, because that is where diagnosis happens.
    assert any("some_internal_key" in line for line in logger.info_lines)


@pytest.mark.asyncio
async def test_refusal_names_a_blocking_callable_registration() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Endpoints(_Endpoints):
        async def set_game_core(self):
            async with self._rules.hold("set_game_core", prune=True) as refusal:
                assert refusal is None
                entered.set()
                await release.wait()
                return {"success": True}

    conflicts, logger, _debug = _conflicts()
    endpoints = Endpoints(conflicts)
    running = asyncio.create_task(endpoints.set_game_core())
    await entered.wait()

    await endpoints.start_prune()

    refusal = next(line for line in logger.info_lines if "start refused" in line)
    assert "set_game_core (operation" in refusal
    release.set()
    await running


@pytest.mark.asyncio
async def test_lease_lifecycle_is_traceable_at_debug() -> None:
    conflicts, logger, debug_lines = _conflicts()

    token = await conflicts.acquire_lease("sgdb_artwork")
    await conflicts.renew_lease(token)
    await conflicts.release_lease(token)

    joined = "\n".join(debug_lines)
    assert f"acquired lease {token}" in joined
    assert f"renewed lease {token}" in joined
    assert f"released lease {token}" in joined
    # Lifecycle is debug-only; it must not spam the INFO log.
    assert logger.info_lines == []


@pytest.mark.asyncio
async def test_expired_lease_is_reported_at_info_as_never_released(monkeypatch) -> None:
    endpoints, conflicts, logger, _debug = _endpoints()
    monkeypatch.setattr("lib.prune_conflicts._LEASE_SECONDS", 0.0)
    await conflicts.acquire_lease("installed_reconcile")

    assert await endpoints.start_prune() == {"success": True}

    # A lease reaching its deadline means its owner leaked it — visible without
    # having to turn debug logging on first.
    assert any(
        "installed_reconcile" in line and "expired" in line and "without being released" in line
        for line in logger.info_lines
    )


@pytest.mark.asyncio
async def test_detached_retention_is_labelled_by_its_originating_callable() -> None:
    endpoints, conflicts, logger, _debug = _endpoints()
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    await conflicts.retain(task, "start_download")

    await endpoints.start_prune()

    refusal = next(line for line in logger.info_lines if "start refused" in line)
    assert "start_download (operation" in refusal
    release.set()
    await task
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_new_frontend_disowns_a_lease_its_predecessor_stranded() -> None:
    endpoints, conflicts, logger, _debug = _endpoints()
    # The double mount at plugin load: mount 1 acquires, its context dies before
    # the continuation that would release, so nothing ever releases or renews.
    await conflicts.acquire_lease("installed_reconcile")
    assert (await endpoints.start_prune())["reason"] == "operation_active"

    released = await conflicts.release_orphaned_leases()

    assert released == 1
    assert await endpoints.start_prune() == {"success": True}
    assert any(
        "orphaned lease installed_reconcile:1" in line and "no longer mounted" in line for line in logger.info_lines
    )


@pytest.mark.asyncio
async def test_disowning_leaves_callable_registrations_and_run_claims_alone() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Endpoints(_Endpoints):
        async def set_game_core(self):
            async with self._rules.hold("set_game_core", prune=True) as refusal:
                assert refusal is None
                entered.set()
                await release.wait()
                return {"success": True}

    conflicts, _logger, _debug = _conflicts()
    endpoints = Endpoints(conflicts)
    running = asyncio.create_task(endpoints.set_game_core())
    await entered.wait()

    # Only the frontend's own leases are the frontend's to disown; a live
    # endpoint still holds off a cleanup on its own account.
    assert await conflicts.release_orphaned_leases() == 0
    assert (await endpoints.start_prune())["reason"] == "operation_active"

    release.set()
    await running

    conflicts.register_run("run-1")
    assert await conflicts.release_orphaned_leases() == 0
    assert conflicts.cleanup_running is True


@pytest.mark.asyncio
async def test_disowning_when_no_lease_is_held_is_a_silent_no_op() -> None:
    conflicts, logger, _debug = _conflicts()

    assert await conflicts.release_orphaned_leases() == 0

    # An ordinary mount must not log as though it cleaned something up.
    assert logger.info_lines == []


@pytest.mark.asyncio
async def test_concurrent_multicall_leases_are_reference_counted() -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    first = await conflicts.acquire_lease("shortcut_removal")
    second = await conflicts.acquire_lease("shortcut_removal")
    await conflicts.release_lease(first)
    assert (await endpoints.start_prune())["reason"] == "operation_active"

    await conflicts.release_lease(second)
    assert await endpoints.start_prune() == {"success": True}


@pytest.mark.asyncio
async def test_abandoned_multicall_lease_expires_before_prune_start(monkeypatch) -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    monkeypatch.setattr("lib.prune_conflicts._LEASE_SECONDS", 0.0)
    await conflicts.acquire_lease("shortcut_removal")

    assert await endpoints.start_prune() == {"success": True}


@pytest.mark.asyncio
async def test_renewed_frontend_lease_cannot_expire_while_heartbeats_continue(monkeypatch) -> None:
    endpoints, conflicts, _logger, _debug = _endpoints()
    clock = _LoopClock(monkeypatch)
    token = await conflicts.acquire_lease("long_rebake")
    clock.advance(0.6 * _LEASE_SECONDS)

    assert await conflicts.renew_lease(token) is True
    clock.advance(0.6 * _LEASE_SECONDS)
    assert (await endpoints.start_prune())["reason"] == "operation_active"

    clock.advance(0.6 * _LEASE_SECONDS)
    assert await endpoints.start_prune() == {"success": True}
    assert await conflicts.renew_lease(token) is False
