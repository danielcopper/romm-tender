"""Tests for services/prune/results.py — which chunk of a completion carries its lease."""

from __future__ import annotations

from typing import Any

from _factories import _make_conflict_rules, _make_prune_conflicts

from services.prune import results
from services.prune.results import PruneResultReporter, PruneResultReporterConfig


def _repoint(app_id: int) -> dict[str, Any]:
    """One group's result that leaves Steam needing a publish."""
    return {
        "status": "removed",
        "removed_rom_ids": [app_id],
        "committed_action": "repoint_shortcut",
        "app_id": app_id,
        "target_rom_id": app_id + 1000,
    }


async def test_only_the_final_chunk_of_a_completion_carries_its_lease(monkeypatch):
    # A budget one result already exceeds puts every result in a chunk of its own.
    monkeypatch.setattr(results, "_COMPLETION_BUDGET_BYTES", 1)
    prune_conflicts = _make_prune_conflicts()
    frames: list[dict[str, Any]] = []

    async def emit(event: str, payload: Any, /) -> bool:
        assert event == "prune_complete"
        frames.append(payload)
        return True

    reporter = PruneResultReporter(
        config=PruneResultReporterConfig(
            emit=emit, conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts)
        )
    )

    await reporter.emit_completion("run-1", [_repoint(1), _repoint(2)], cancelled=False, reason=None, message=None)

    assert [frame["final"] for frame in frames] == [False, True]
    assert all(frame["publication_required"] is True for frame in frames)
    assert "prune_lease_token" not in frames[0]
    assert frames[1]["prune_lease_token"].startswith("prune_complete:")
    assert prune_conflicts.conflicting_operations == 1
