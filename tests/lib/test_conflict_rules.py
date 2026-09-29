from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lib.conflict_rules import (
    ConflictRuleSet,
    migration_refusal,
    operation_active_refusal,
    prune_active_refusal,
    sync_refusal,
    update_refusal,
)
from lib.prune_conflicts import PruneConflicts

_UPDATE_REFUSAL = {
    "success": False,
    "reason": "blocked_by_update",
    "message": "Tender is installing an update and will restart in a moment.",
}
_MIGRATION_REFUSAL = {
    "success": False,
    "reason": "blocked_by_migration",
    "message": "Pending RetroDECK migration. Open the plugin QAM to migrate or dismiss.",
}
_SYNC_REFUSAL = {
    "success": False,
    "reason": "sync_active",
    "message": "A library sync is in progress — wait for it to finish or cancel it first.",
}
_PRUNE_REFUSAL = {
    "success": False,
    "reason": "prune_active",
    "message": "A removed-game cleanup is in progress; wait for it to finish before changing local game data.",
}


class _RecordingLogger:
    def __init__(self) -> None:
        self.info_lines: list[str] = []

    def info(self, message: str) -> None:
        self.info_lines.append(message)


class _Condition:
    """A condition a rule reads, counting how often it was asked."""

    def __init__(self, *, holds: bool = False) -> None:
        self.holds = holds
        self.asked = 0

    def __call__(self) -> bool:
        self.asked += 1
        return self.holds


class _Rules:
    def __init__(
        self, *, update: bool = False, migration: bool = False, sync: bool = False, cleanup: bool = False
    ) -> None:
        self.logger = _RecordingLogger()
        self.conflicts = PruneConflicts(logger=self.logger, log_debug=lambda _msg: None)
        if cleanup:
            self.conflicts.register_run("held-run")
        self.update = _Condition(holds=update)
        self.migration = _Condition(holds=migration)
        self.sync = _Condition(holds=sync)
        self.rules = ConflictRuleSet(
            prune_conflicts=self.conflicts,
            update_in_progress=self.update,
            migration_pending=self.migration,
            sync_in_flight=self.sync,
        )


async def _refusal(rules: ConflictRuleSet, **named: bool) -> dict[str, Any] | None:
    async with rules.hold("the_endpoint", **named) as refusal:
        return refusal


# ── The refusals ─────────────────────────────────────────────────────────────


def test_each_refusal_is_the_canonical_failure_shape():
    assert update_refusal() == _UPDATE_REFUSAL
    assert migration_refusal() == _MIGRATION_REFUSAL
    assert sync_refusal() == _SYNC_REFUSAL
    assert prune_active_refusal() == _PRUNE_REFUSAL
    assert operation_active_refusal("held by someone") == {
        "success": False,
        "reason": "operation_active",
        "message": "held by someone",
    }


# ── Each rule on its own ─────────────────────────────────────────────────────


async def test_an_update_in_progress_refuses_a_use_case_that_names_the_update_rule():
    assert await _refusal(_Rules(update=True).rules, update=True) == _UPDATE_REFUSAL


async def test_an_update_in_progress_does_not_refuse_a_use_case_that_names_only_the_migration_rule():
    rules = _Rules(update=True)

    assert await _refusal(rules.rules, migration=True) is None
    assert rules.update.asked == 0


async def test_a_pending_migration_refuses_a_use_case_that_names_the_migration_rule():
    assert await _refusal(_Rules(migration=True).rules, migration=True) == _MIGRATION_REFUSAL


async def test_a_sync_in_flight_refuses_a_use_case_that_names_the_sync_rule():
    assert await _refusal(_Rules(sync=True).rules, sync=True) == _SYNC_REFUSAL


async def test_a_running_cleanup_refuses_a_use_case_that_names_the_prune_rule():
    assert await _refusal(_Rules(cleanup=True).rules, prune=True) == _PRUNE_REFUSAL


async def test_no_condition_holding_lets_the_block_run():
    assert await _refusal(_Rules().rules, update=True, migration=True, sync=True, prune=True) is None


async def test_a_condition_the_use_case_does_not_name_is_never_asked():
    rules = _Rules(update=True, migration=True, sync=True, cleanup=True)

    assert await _refusal(rules.rules) is None

    assert rules.update.asked == 0
    assert rules.migration.asked == 0
    assert rules.sync.asked == 0
    assert rules.conflicts.conflicting_operations == 0


async def test_each_refusal_is_a_fresh_dict():
    rules = _Rules(migration=True).rules
    first = await _refusal(rules, migration=True)
    assert first is not None
    first["message"] = "changed by a caller"

    assert await _refusal(rules, migration=True) == _MIGRATION_REFUSAL


# ── The order they are asked in ──────────────────────────────────────────────


async def test_an_update_in_progress_answers_before_every_other_rule():
    rules = _Rules(update=True, migration=True, sync=True, cleanup=True)

    refusal = await _refusal(rules.rules, update=True, migration=True, sync=True, prune=True)

    assert refusal == _UPDATE_REFUSAL
    assert rules.migration.asked == 0
    assert rules.sync.asked == 0


async def test_a_pending_migration_answers_before_a_sync_in_flight_and_a_running_cleanup():
    rules = _Rules(migration=True, sync=True, cleanup=True)

    assert await _refusal(rules.rules, migration=True, sync=True, prune=True) == _MIGRATION_REFUSAL
    assert rules.sync.asked == 0


async def test_a_sync_in_flight_answers_before_a_running_cleanup():
    rules = _Rules(sync=True, cleanup=True)

    assert await _refusal(rules.rules, migration=True, sync=True, prune=True) == _SYNC_REFUSAL


# ── What a call holds, and for how long ──────────────────────────────────────


@pytest.mark.parametrize(
    ("condition", "named"),
    [
        ({"update": True}, {"update": True, "prune": True}),
        ({"migration": True}, {"migration": True, "prune": True}),
        ({"sync": True}, {"sync": True, "prune": True}),
        ({"cleanup": True}, {"prune": True}),
    ],
)
async def test_a_refused_call_registers_nothing(condition, named):
    rules = _Rules(**condition)

    assert await _refusal(rules.rules, **named) is not None

    assert rules.conflicts.conflicting_operations == 0


async def test_the_prune_rule_holds_an_operation_named_after_the_endpoint_for_the_whole_block():
    rules = _Rules()

    async with rules.rules.hold("the_endpoint", prune=True) as refusal:
        assert refusal is None
        assert rules.conflicts.conflicting_operations == 1
        assert await rules.conflicts.reserve_start("start_prune") is not None
        assert "the_endpoint (operation" in rules.logger.info_lines[-1]

    assert rules.conflicts.conflicting_operations == 0


async def test_an_update_pressed_while_the_operation_waited_to_register_refuses_the_block():
    """The first ask passed; the press came while the registration waited for the prune conflicts' lock."""
    rules = _Rules()
    await rules.conflicts._lock.acquire()
    entered: list[dict[str, Any] | None] = []

    async def use_case() -> None:
        async with rules.rules.hold("the_endpoint", update=True, prune=True) as refusal:
            entered.append(refusal)

    waiting = asyncio.ensure_future(use_case())
    await asyncio.sleep(0)
    assert rules.update.asked == 1
    rules.update.holds = True
    rules.conflicts._lock.release()
    await waiting

    assert entered == [_UPDATE_REFUSAL]
    assert rules.conflicts.conflicting_operations == 0


async def _raise_inside_a_prune_block(rules: _Rules) -> None:
    async with rules.rules.hold("the_endpoint", prune=True):
        raise RuntimeError("boom")


async def test_the_operation_is_released_when_the_block_raises():
    rules = _Rules()

    with pytest.raises(RuntimeError, match="boom"):
        await _raise_inside_a_prune_block(rules)

    assert rules.conflicts.conflicting_operations == 0


async def test_a_block_without_the_prune_rule_holds_nothing():
    rules = _Rules()

    async with rules.rules.hold("the_endpoint", migration=True, sync=True) as refusal:
        assert refusal is None
        assert rules.conflicts.conflicting_operations == 0


async def test_retain_holds_an_operation_until_the_detached_task_ends():
    rules = _Rules()
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())

    await rules.rules.retain(task, "the_endpoint")
    assert rules.conflicts.conflicting_operations == 1

    release.set()
    await task
    await asyncio.gather(*rules.conflicts._release_tasks)
    assert rules.conflicts.conflicting_operations == 0


async def test_a_lease_holds_off_a_cleanup_until_it_is_released_by_its_token():
    rules = _Rules()

    token = await rules.rules.acquire_lease("shortcut_removal")

    assert token.startswith("shortcut_removal:")
    assert await rules.conflicts.reserve_start("start_prune") is not None
    await rules.rules.release_lease(token)
    assert rules.conflicts.conflicting_operations == 0
    assert await rules.conflicts.reserve_start("start_prune") is None


async def test_a_lease_checks_no_rule():
    """The lease is taken inside the call's own ``hold``, which has already answered."""
    rules = _Rules(migration=True, sync=True, cleanup=True)

    await rules.rules.acquire_lease("shortcut_removal")

    assert rules.conflicts.conflicting_operations == 1
    assert rules.migration.asked == 0
    assert rules.sync.asked == 0


# ── An event under a lease ───────────────────────────────────────────────────


class _Emit:
    """The emit a use case passes: records the token it was handed and answers whether anybody heard."""

    def __init__(self, *, heard: bool = True, raises: BaseException | None = None) -> None:
        self.heard = heard
        self.raises = raises
        self.tokens: list[str] = []

    async def __call__(self, token: str) -> bool:
        self.tokens.append(token)
        if self.raises is not None:
            raise self.raises
        return self.heard


async def test_an_event_somebody_heard_keeps_the_lease_its_token_names():
    rules = _Rules()
    emit = _Emit()

    await rules.rules.emit_under_lease("download_complete", emit)

    (token,) = emit.tokens
    assert token.startswith("download_complete:")
    assert rules.conflicts.conflicting_operations == 1
    await rules.rules.release_lease(token)
    assert rules.conflicts.conflicting_operations == 0


async def test_an_event_nobody_heard_gives_its_lease_back():
    rules = _Rules()

    await rules.rules.emit_under_lease("download_complete", _Emit(heard=False))

    assert rules.conflicts.conflicting_operations == 0


async def test_an_event_whose_emit_raises_gives_its_lease_back():
    rules = _Rules()
    emit = _Emit(raises=RuntimeError("transport rejected event"))

    with pytest.raises(RuntimeError, match="transport rejected event"):
        await rules.rules.emit_under_lease("download_complete", emit)

    assert rules.conflicts.conflicting_operations == 0


async def test_an_event_under_a_lease_checks_no_rule():
    """The emit happens while something else still refuses a cleanup's start."""
    rules = _Rules(migration=True, sync=True, cleanup=True)

    await rules.rules.emit_under_lease("download_complete", _Emit())

    assert rules.conflicts.conflicting_operations == 1
    assert rules.migration.asked == 0
    assert rules.sync.asked == 0


# ── A cleanup's exclusive start ──────────────────────────────────────────────


async def _start_refusal(rules: ConflictRuleSet, **named: bool) -> dict[str, Any] | None:
    async with rules.hold_start("start_prune", **named) as refusal:
        return refusal


async def test_a_held_operation_refuses_the_start_naming_its_holder():
    rules = _Rules()
    await rules.rules.acquire_lease("launch_reconfirm")

    refusal = await _start_refusal(rules.rules, migration=True, sync=True)

    assert refusal == {
        "success": False,
        "reason": "operation_active",
        "message": (
            "Another local-data operation is in progress (checking a game's launch settings); wait for it to "
            "finish before starting cleanup."
        ),
    }
    assert rules.conflicts.cleanup_running is False


async def test_the_reservation_is_asked_before_the_migration_and_the_sync_rule():
    rules = _Rules(migration=True, sync=True)
    await rules.rules.acquire_lease("launch_reconfirm")

    refusal = await _start_refusal(rules.rules, migration=True, sync=True)

    assert refusal is not None
    assert refusal["reason"] == "operation_active"
    assert rules.migration.asked == 0
    assert rules.sync.asked == 0


async def test_the_rules_are_asked_while_the_reservation_is_held():
    """Nothing can start between the sync rule's answer and the reservation: the reservation came first."""
    rules = _Rules()
    seen: list[bool] = []

    def sync_in_flight() -> bool:
        seen.append(rules.conflicts.cleanup_running)
        return False

    rules.rules = ConflictRuleSet(
        prune_conflicts=rules.conflicts,
        update_in_progress=rules.update,
        migration_pending=rules.migration,
        sync_in_flight=sync_in_flight,
    )

    assert await _start_refusal(rules.rules, sync=True) is None

    assert seen == [True]


async def test_an_update_in_progress_answers_the_start_before_a_pending_migration():
    rules = _Rules(update=True, migration=True)

    assert await _start_refusal(rules.rules, update=True, migration=True, sync=True) == _UPDATE_REFUSAL
    assert rules.migration.asked == 0


async def test_a_pending_migration_answers_the_start_before_a_sync_in_flight():
    rules = _Rules(migration=True, sync=True)

    assert await _start_refusal(rules.rules, migration=True, sync=True) == _MIGRATION_REFUSAL
    assert rules.sync.asked == 0


async def test_a_sync_in_flight_refuses_the_start():
    assert await _start_refusal(_Rules(sync=True).rules, migration=True, sync=True) == _SYNC_REFUSAL


@pytest.mark.parametrize("condition", [{"update": True}, {"migration": True}, {"sync": True}])
async def test_a_refused_start_gives_its_reservation_back_before_it_answers(condition):
    rules = _Rules(**condition)

    async with rules.rules.hold_start("start_prune", update=True, migration=True, sync=True) as refusal:
        assert refusal is not None
        assert rules.conflicts.cleanup_running is False

    assert rules.conflicts.cleanup_running is False
    assert await _refusal(rules.rules, prune=True) is None


async def test_the_start_holds_its_reservation_for_the_whole_block():
    rules = _Rules()

    async with rules.rules.hold_start("start_prune", migration=True, sync=True) as refusal:
        assert refusal is None
        assert rules.conflicts.cleanup_running is True
        assert await _refusal(rules.rules, prune=True) == _PRUNE_REFUSAL

    assert rules.conflicts.cleanup_running is False


async def _raise_inside_a_start_block(rules: _Rules) -> None:
    async with rules.rules.hold_start("start_prune"):
        raise RuntimeError("boom")


async def test_the_reservation_is_given_back_when_the_block_raises():
    rules = _Rules()

    with pytest.raises(RuntimeError, match="boom"):
        await _raise_inside_a_start_block(rules)

    assert rules.conflicts.cleanup_running is False


async def test_a_rule_that_raises_gives_the_reservation_back():
    rules = _Rules()

    def migration_pending() -> bool:
        raise RuntimeError("database locked")

    rules.rules = ConflictRuleSet(
        prune_conflicts=rules.conflicts,
        update_in_progress=rules.update,
        migration_pending=migration_pending,
        sync_in_flight=rules.sync,
    )

    with pytest.raises(RuntimeError, match="database locked"):
        await _start_refusal(rules.rules, migration=True)

    assert rules.conflicts.cleanup_running is False


async def test_a_run_claim_does_not_refuse_the_start():
    """The prune service refuses a second start itself."""
    assert await _start_refusal(_Rules(cleanup=True).rules, migration=True, sync=True) is None


# ── Leases the frontend renews and disowns ───────────────────────────────────


async def test_a_live_lease_renews_and_an_unknown_one_does_not():
    rules = _Rules()
    token = await rules.rules.acquire_lease("sgdb_artwork")

    assert await rules.rules.renew_lease(token) is True
    await rules.rules.release_lease(token)
    assert await rules.rules.renew_lease(token) is False


async def test_disowning_drops_every_lease_and_counts_them():
    rules = _Rules()
    await rules.rules.acquire_lease("sgdb_artwork")
    await rules.rules.acquire_lease("installed_reconcile")

    assert await rules.rules.release_orphaned_leases() == 2
    assert rules.conflicts.conflicting_operations == 0
