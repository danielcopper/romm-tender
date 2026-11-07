"""The refusal details a caller hands ``ConflictRuleSet.hold``: every refusal it raises carries them.

Each rule is put in its refusing state on its own, over a real ``PruneConflicts``,
and ``hold`` is asked with details and without. A caller that hands none gets
each refusal as it was, the folder rule's own object included.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from lib.background_tasks import BackgroundTasks
from lib.conflict_rules import ConflictRuleSet
from lib.errors import Refused
from lib.prune_conflicts import PruneConflicts

_DETAILS = {"synced": 0, "conflicts": 0}
_FOLDER_DETAILS = {"folder": "/run/media/deck/SD/Tender", "state": "drive_not_mounted"}


class _Logger:
    def info(self, message: str) -> None:
        pass


class _Machine:
    """A rule set whose conditions each test sets; the folder reading answers the one refusal object it keeps."""

    def __init__(self) -> None:
        self.conflicts = PruneConflicts(
            logger=_Logger(),
            log_debug=lambda _msg: None,
            release_tasks=BackgroundTasks(logger=logging.getLogger("test-prune-conflicts")),
        )
        self.update = False
        self.folder: Refused | None = None
        self.migration = False
        self.sync = False
        self.rules = ConflictRuleSet(
            prune_conflicts=self.conflicts,
            update_in_progress=lambda: self.update,
            folder_unreachable=lambda: self.folder,
            migration_pending=lambda: self.migration,
            sync_in_flight=lambda: self.sync,
        )

    def put_in(self, state: str) -> None:
        if state == "update":
            self.update = True
        elif state == "folder":
            self.folder = Refused("folder_unreachable", "Tender's folder cannot be reached.", **_FOLDER_DETAILS)
        elif state == "migration":
            self.migration = True
        elif state == "sync":
            self.sync = True
        else:
            self.conflicts.register_run("held-run")


async def _refusal(rules: ConflictRuleSet, **named: Any) -> Refused:
    ran = False
    with pytest.raises(Refused) as refused:
        async with rules.hold("the_endpoint", **named):
            ran = True
    assert not ran, "a refused block ran"
    return refused.value


_EVERY_RULE = dict.fromkeys(("update", "folder", "migration", "sync", "prune"), True)

# Each rule's refusing state, its reason, and the details the rule's own refusal carries.
_STATES = [
    pytest.param("update", "blocked_by_update", {}, id="update"),
    pytest.param("folder", "folder_unreachable", _FOLDER_DETAILS, id="folder"),
    pytest.param("migration", "blocked_by_migration", {}, id="migration"),
    pytest.param("sync", "sync_active", {}, id="sync"),
    pytest.param("cleanup", "prune_active", {}, id="cleanup"),
]


@pytest.mark.parametrize(("state", "reason", "own"), _STATES)
async def test_every_refusal_carries_the_details_handed_to_it(state, reason, own):
    machine = _Machine()
    machine.put_in(state)

    refused = await _refusal(machine.rules, **_EVERY_RULE, refusal_details=_DETAILS)

    assert (refused.reason, refused.details) == (reason, {**own, **_DETAILS})


@pytest.mark.parametrize(("state", "reason", "own"), _STATES)
async def test_with_no_details_handed_every_refusal_carries_only_its_own(state, reason, own):
    machine = _Machine()
    machine.put_in(state)

    refused = await _refusal(machine.rules, **_EVERY_RULE)

    assert (refused.reason, refused.details) == (reason, own)


async def test_with_no_details_handed_the_folder_refusal_is_the_rule_s_own():
    machine = _Machine()
    machine.put_in("folder")

    assert await _refusal(machine.rules, folder=True) is machine.folder


async def test_an_update_pressed_while_the_operation_waited_to_register_carries_the_details():
    machine = _Machine()
    await machine.conflicts._lock.acquire()
    waiting = asyncio.ensure_future(_refusal(machine.rules, update=True, prune=True, refusal_details=_DETAILS))
    await asyncio.sleep(0)
    machine.update = True
    machine.conflicts._lock.release()

    refused = await waiting

    assert (refused.reason, refused.details) == ("blocked_by_update", _DETAILS)
    assert machine.conflicts.conflicting_operations == 0
