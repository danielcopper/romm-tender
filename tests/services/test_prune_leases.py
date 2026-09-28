from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _factories import _make_conflict_rules, _make_prune_conflicts

from services.prune_leases import PruneLeaseService, PruneLeaseServiceConfig

if TYPE_CHECKING:
    from lib.prune_conflicts import PruneConflicts


@pytest.fixture
def prune_conflicts() -> PruneConflicts:
    return _make_prune_conflicts()


@pytest.fixture
def service(prune_conflicts) -> PruneLeaseService:
    # Every rule a use case can name holds, so a use case that asked one would be refused.
    prune_conflicts.register_run("held-run")
    rules = _make_conflict_rules(prune_conflicts=prune_conflicts, migration_pending=True, sync_in_flight=True)
    return PruneLeaseService(config=PruneLeaseServiceConfig(conflict_rules=rules))


async def test_releasing_a_lease_lets_a_cleanup_start_again(service, prune_conflicts):
    token = await prune_conflicts.acquire_lease("sgdb_artwork")

    assert await service.release_prune_conflict_lease(token) == {
        "success": True,
        "message": "Operation lease released.",
    }

    assert prune_conflicts.conflicting_operations == 0


async def test_releasing_an_unknown_lease_still_succeeds(service):
    assert (await service.release_prune_conflict_lease("sgdb_artwork:99"))["success"] is True


async def test_a_live_lease_renews(service, prune_conflicts):
    token = await prune_conflicts.acquire_lease("sgdb_artwork")

    assert await service.renew_prune_conflict_lease(token) == {
        "success": True,
        "message": "Operation lease renewed.",
    }


async def test_a_lease_that_is_gone_answers_stale(service):
    assert await service.renew_prune_conflict_lease("sgdb_artwork:99") == {
        "success": False,
        "reason": "stale_lease",
        "message": "Operation lease is no longer active.",
    }


async def test_disowning_answers_how_many_leases_it_dropped(service, prune_conflicts):
    await prune_conflicts.acquire_lease("sgdb_artwork")
    await prune_conflicts.acquire_lease("installed_reconcile")

    assert await service.release_orphaned_prune_leases() == {"success": True, "released": 2}

    assert prune_conflicts.conflicting_operations == 0
    # The run claim is not a lease, so it is not the frontend's to disown.
    assert prune_conflicts.cleanup_running is True
