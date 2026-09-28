from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lib.conflict_rules import ConflictRuleSet
from lib.prune_gate import PruneConflicts

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
    def __init__(self, *, migration: bool = False, sync: bool = False, cleanup: bool = False) -> None:
        self.logger = _RecordingLogger()
        self.conflicts = PruneConflicts(logger=self.logger, log_debug=lambda _msg: None)
        if cleanup:
            self.conflicts.register_run("held-run")
        self.migration = _Condition(holds=migration)
        self.sync = _Condition(holds=sync)
        self.rules = ConflictRuleSet(
            prune_conflicts=self.conflicts,
            migration_pending=self.migration,
            sync_in_flight=self.sync,
        )


async def _refusal(rules: ConflictRuleSet, **named: bool) -> dict[str, Any] | None:
    async with rules.hold("the_endpoint", **named) as refusal:
        return refusal


# ── Each rule on its own ─────────────────────────────────────────────────────


async def test_a_pending_migration_refuses_a_use_case_that_names_the_migration_rule():
    assert await _refusal(_Rules(migration=True).rules, migration=True) == _MIGRATION_REFUSAL


async def test_a_sync_in_flight_refuses_a_use_case_that_names_the_sync_rule():
    assert await _refusal(_Rules(sync=True).rules, sync=True) == _SYNC_REFUSAL


async def test_a_running_cleanup_refuses_a_use_case_that_names_the_prune_rule():
    assert await _refusal(_Rules(cleanup=True).rules, prune=True) == _PRUNE_REFUSAL


async def test_no_condition_holding_lets_the_block_run():
    assert await _refusal(_Rules().rules, migration=True, sync=True, prune=True) is None


async def test_a_condition_the_use_case_does_not_name_is_never_asked():
    rules = _Rules(migration=True, sync=True, cleanup=True)

    assert await _refusal(rules.rules) is None

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
        assert await rules.conflicts.reserve_start() is not None
        assert "the_endpoint (operation" in rules.logger.info_lines[-1]

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
    assert await rules.conflicts.reserve_start() is not None
    await rules.rules.release_lease(token)
    assert rules.conflicts.conflicting_operations == 0
    assert await rules.conflicts.reserve_start() is None


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

    with pytest.raises(RuntimeError, match="transport rejected event"):
        await rules.rules.emit_under_lease("download_complete", _Emit(raises=RuntimeError("transport rejected event")))

    assert rules.conflicts.conflicting_operations == 0


async def test_an_event_under_a_lease_checks_no_rule():
    """The emit happens while something else still refuses a cleanup's start."""
    rules = _Rules(migration=True, sync=True, cleanup=True)

    await rules.rules.emit_under_lease("download_complete", _Emit())

    assert rules.conflicts.conflicting_operations == 1
    assert rules.migration.asked == 0
    assert rules.sync.asked == 0
