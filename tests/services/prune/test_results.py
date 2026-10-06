"""Tests for services/prune/results.py — the frames one cleanup run publishes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _factories import _make_conflict_rules, _make_prune_conflicts

from domain.rom import Rom
from domain.version_metadata import VersionMetadata
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


def _named(name: str) -> Rom:
    return Rom.synced(
        rom_id=1,
        platform_slug="dc",
        name=name,
        fs_name="game.chd",
        shortcut_app_id=None,
        synced_at="now",
        version=VersionMetadata(sibling_group_key="g"),
    )


async def test_a_progress_frame_flags_the_name_and_path_it_shortened():
    reporter, frames = _recording_reporter()

    await reporter.emit_progress("run-1", 1, 1, "recovery_sealed", [_named("n" * 513)], bundle_path="/" + "p" * 2048)

    _, payload = frames[0]
    assert len(payload["name"]) == 512
    assert payload["name_truncated"] is True
    assert len(payload["bundle_path"]) == 2048
    assert payload["bundle_path_truncated"] is True


async def test_a_progress_frame_at_its_caps_flags_nothing():
    reporter, frames = _recording_reporter()

    await reporter.emit_progress("run-1", 1, 1, "checking", [_named("n" * 512)], bundle_path="p" * 2048)
    await reporter.emit_progress("run-1", 1, 1, "checking", [_named("n")])

    assert frames[0][1]["name_truncated"] is False
    assert frames[0][1]["bundle_path_truncated"] is False
    assert "bundle_path_truncated" not in frames[1][1]


async def test_a_completion_flags_a_run_message_it_shortened():
    reporter, frames = _recording_reporter()

    await reporter.emit_completion("run-1", [], cancelled=False, reason="unknown", message="m" * 513)
    await reporter.emit_completion("run-2", [], cancelled=False, reason="unknown", message="m" * 512)
    await reporter.emit_completion("run-3", [], cancelled=False, reason=None, message=None)

    shortened, whole, silent = (payload for _, payload in frames)
    assert len(shortened["message"]) == 512
    assert shortened["message_truncated"] is True
    assert whole["message_truncated"] is False
    assert "message" not in silent
    assert "message_truncated" not in silent


def test_every_truncation_flag_sent_is_read_by_the_panel():
    """A flag the panel never reads tells the user nothing, so it is not sent at all.

    Matched by name: a flag two frames share counts as read when the panel reads
    either, and a flag built under a name not spelled out here is not seen.
    """
    backend = Path(results.__file__).parent
    sent = {
        flag
        for module in ("preview.py", "results.py")
        for flag in re.findall(r'"(\w+_(?:truncated|omitted))"', (backend / module).read_text(encoding="utf-8"))
    }
    panel = (backend.parents[2] / "frontend" / "src" / "bigpicture" / "RemovedGamesCleanup.tsx").read_text(
        encoding="utf-8"
    )

    assert sent
    assert sorted(flag for flag in sent if f".{flag}" not in panel) == []
