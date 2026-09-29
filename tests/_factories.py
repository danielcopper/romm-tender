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

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from lib.conflict_rules import ConflictRuleSet
from lib.prune_conflicts import PruneConflicts


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
    migration_pending: bool = False,
    sync_in_flight: bool = False,
) -> ConflictRuleSet:
    """The real conflict rules over fixed conditions; by default none of them refuses."""
    return ConflictRuleSet(
        prune_conflicts=prune_conflicts if prune_conflicts is not None else _make_prune_conflicts(),
        migration_pending=lambda: migration_pending,
        sync_in_flight=lambda: sync_in_flight,
    )


def _make_testable_plugin():
    """Return a TestablePlugin instance with test-only attributes declared.

    Pre-wires a no-op ``_debug_logger`` so any service that consumes
    ``Plugin._log_debug`` (which forwards through ``_debug_logger``) works
    out of the box. Tests that want to assert on debug-log behaviour can
    override ``_debug_logger`` after construction (e.g. with the real
    ``SettingsAwareDebugLogger`` bound to a settings dict they control).
    """
    from main import Plugin

    class TestablePlugin(Plugin):
        """Plugin subclass that declares test-only attributes for type safety."""

        _fake_api: Any
        _save_settings: Any
        _uow_factory: Any
        _prune_conflicts: Any

    instance = TestablePlugin()
    instance._prune_conflicts = _make_prune_conflicts()
    instance._debug_logger = lambda msg: None
    return instance
