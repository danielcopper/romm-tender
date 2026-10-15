"""Tests for services/prune/results.py — the frames one cleanup run publishes."""

from __future__ import annotations

from typing import Any

from domain.rom import Rom
from domain.version_metadata import VersionMetadata
from services.prune.results import GroupOutcome, PruneResultReporter, PruneResultReporterConfig


def _recording_reporter() -> tuple[PruneResultReporter, list[tuple[str, dict[str, Any]]]]:
    frames: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, payload: Any, /) -> bool:
        frames.append((event, payload))
        return True

    reporter = PruneResultReporter(config=PruneResultReporterConfig(emit=emit))
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
