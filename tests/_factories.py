"""Construction helpers shared across the suite.

These live outside ``conftest.py`` on purpose. A conftest is imported by
pytest under its own module name; importing it a second time by plain name
(`from conftest import ...`) creates a *separate* module object that re-runs
the module body: a second suite home with ``HOME`` moved to it, ``backend/``
and ``tests/`` inserted into ``sys.path`` again, and the hypothesis profile
registered and loaded again. Anything a test module needs to import belongs
here instead.

Import as ``from _factories import _make_retry`` — ``tests/`` is on the path
via the root conftest, the same way ``fakes/`` is reached.
"""

import contextlib
import dataclasses
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from bootstrap import Application, ServicesBundle
from fakes.running_loop import running_loop

from lib.conflict_rules import ConflictRuleSet
from lib.errors import Refused
from lib.prune_conflicts import PruneConflicts

# How the conflict rules word the refusal of each rule (``lib/conflict_rules.py``).
# A cleanup's refused start, ``operation_active``, names its holder instead.
_CONFLICT_REFUSAL_MESSAGES = {
    "blocked_by_update": "Tender is installing an update and will restart in a moment.",
    "blocked_by_migration": "Pending RetroDECK migration. Open the Tender menu (QAM) to migrate or dismiss.",
    "sync_active": "A library sync is in progress — wait for it to finish or cancel it first.",
    "prune_active": "A removed-game cleanup is in progress; wait for it to finish before changing local game data.",
}


def _no_retry(fn, *a, **kw):
    """Pass-through Retry side_effect: invoke the wrapped callable once, no backoff."""
    return fn(*a, **kw)


def _make_retry():
    """Build a Retry ``MagicMock`` that runs ``with_retry`` callables exactly once
    and reports every exception as non-retryable. Used everywhere services
    take a ``Retry`` Protocol injection in tests."""
    retry = MagicMock()
    retry.with_retry.side_effect = _no_retry
    retry.is_retryable.return_value = False
    return retry


def _make_prune_conflicts() -> PruneConflicts:
    """The real prune conflicts, logging nowhere a test would look."""
    return PruneConflicts(logger=logging.getLogger("test-prune-conflicts"), log_debug=lambda _msg: None)


def _record_operations_at_lease(prune_conflicts: PruneConflicts, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record, at every lease taken on *prune_conflicts*, the labels of the operations registered at that moment."""
    seen: list[list[str]] = []
    acquire = prune_conflicts.acquire_lease

    async def _recording_acquire(key: str) -> str:
        seen.append(sorted(holder.label for holder in prune_conflicts._operations.values()))
        return await acquire(key)

    monkeypatch.setattr(prune_conflicts, "acquire_lease", _recording_acquire)
    return seen


def _make_conflict_rules(
    *,
    prune_conflicts: PruneConflicts | None = None,
    update_in_progress: bool = False,
    migration_pending: bool = False,
    sync_in_flight: bool = False,
) -> ConflictRuleSet:
    """The real conflict rules over fixed conditions; by default none of them refuses."""
    return ConflictRuleSet(
        prune_conflicts=prune_conflicts if prune_conflicts is not None else _make_prune_conflicts(),
        update_in_progress=lambda: update_in_progress,
        migration_pending=lambda: migration_pending,
        sync_in_flight=lambda: sync_in_flight,
    )


@contextlib.contextmanager
def _refused_by_conflict_rule(reason: str) -> Iterator[None]:
    """Expect the block to raise the conflict rules' refusal *reason*: its own message, and nothing beside it."""
    with pytest.raises(Refused) as refused:
        yield
    assert (refused.value.reason, refused.value.message) == (reason, _CONFLICT_REFUSAL_MESSAGES[reason])
    assert refused.value.details == {}


def _make_services_bundle(**services: Any) -> ServicesBundle:
    """A ``ServicesBundle`` whose every field is a ``MagicMock``, except the ones named."""
    wired: dict[str, Any] = {field.name: MagicMock() for field in dataclasses.fields(ServicesBundle)}
    wired.update(services)
    return ServicesBundle(**wired)


def _make_application(services: ServicesBundle) -> Application:
    """A real ``Application`` over *services*, as a synchronous fixture can build one."""
    return Application(
        services,
        logger=logging.getLogger("test-application"),
        loop=running_loop(),
        user_agent="romm-tender/0.0.0-test",
    )
