"""Tests for services/prune/results.py — the frames one cleanup run publishes."""

from __future__ import annotations

from typing import Any

from _factories import _make_conflict_rules, _make_prune_conflicts

from domain.rom import Rom
from domain.version_metadata import VersionMetadata
from services.prune import results
from services.prune.results import GroupOutcome, PruneResultReporter, PruneResultReporterConfig


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


def _recording_reporter() -> tuple[PruneResultReporter, list[tuple[str, dict[str, Any]]]]:
    frames: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, payload: Any, /) -> bool:
        frames.append((event, payload))
        return True

    reporter = PruneResultReporter(
        config=PruneResultReporterConfig(
            emit=emit, conflict_rules=_make_conflict_rules(prune_conflicts=_make_prune_conflicts())
        )
    )
    reporter.bind_run("preview-1")
    return reporter, frames


def _keys(value: object) -> list[str]:
    """Every key in a payload, at every depth."""
    if isinstance(value, dict):
        return [key for key, item in value.items() for key in (key, *_keys(item))]
    if isinstance(value, list):
        return [key for item in value for key in _keys(item)]
    return []


async def test_no_progress_or_completion_frame_shortens_its_text_or_sends_a_truncation_flag():
    long = 10_000
    reporter, frames = _recording_reporter()
    row = Rom.synced(
        rom_id=1,
        platform_slug="dc",
        name="n" * long,
        fs_name="game.chd",
        shortcut_app_id=None,
        synced_at="now",
        version=VersionMetadata(sibling_group_key="g" * long),
    )
    group = reporter.group_result(
        [row],
        "partial",
        "r" * long,
        "m" * long,
        GroupOutcome(
            bundle_path="/" + "b" * long,
            mutations=["u" * long],
            ambiguous_mutations=["a" * long],
            warnings=[f"{index}" + "w" * long for index in range(5)],
        ),
    )

    await reporter.emit_progress("run-1", 1, 1, "recovery_sealed", [row], bundle_path="/" + "p" * long)
    await reporter.emit_completion("run-1", [group], cancelled=False, reason="x" * long, message="y" * long)

    (_, progress), (_, complete) = frames
    assert progress["name"] == "n" * long
    assert progress["bundle_path"] == "/" + "p" * long
    assert (complete["reason"], complete["message"]) == ("x" * long, "y" * long)
    result = complete["results"][0]
    assert (result["group_id"], result["name"], result["reason"], result["message"]) == (
        "g" * long,
        "n" * long,
        "r" * long,
        "m" * long,
    )
    assert result["bundle_path"] == "/" + "b" * long
    assert (result["mutations"], result["ambiguous_mutations"]) == (["u" * long], ["a" * long])
    assert result["warnings"] == [f"{index}" + "w" * long for index in range(5)]
    assert [key for key in _keys([progress, complete]) if key.endswith("_truncated")] == []
