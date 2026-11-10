"""A user's emulator pick as Tender stores it: the entry's display label and the emulator source it was chosen from.

A label is what ES-DE stores a choice by, unique within one system's catalogue;
the source says whose catalogue that is, so a label that no longer exists there
can be named with it (#2195 D5, D20). A pick stored before sources were
recorded was made RetroDECK's when the source was added.

Pure compute — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class StoredPick:
    """One stored emulator pick: its ``label`` and the kind of its emulator ``source``."""

    label: str
    source: str


def game_platform_pick(
    platform_fs_slug: str | None,
    platform_slug: str,
    pick_under: Callable[[str], StoredPick | None],
    kept_folders: Callable[[], frozenset[str]],
) -> StoredPick | None:
    """The per-platform pick a game takes, or ``None`` where the platform's default applies (#2452 D15).

    A game with its ``fs_slug`` takes that platform's pick and nothing else. A
    game with none recorded — every game until the first sync after an update
    from 0.33.0 records it — takes the pick still kept under its slug; once
    the refiling has moved that key to the platforms of the slug, the pick of
    the one platform kept for the slug (*kept_folders*, read only then); where
    several share the slug, or none is kept, no pick.
    """
    if platform_fs_slug:
        return pick_under(platform_fs_slug)
    stored = pick_under(platform_slug)
    if stored is not None:
        return stored
    folders = kept_folders()
    return pick_under(next(iter(folders))) if len(folders) == 1 else None


def platform_pick(value: object) -> StoredPick | None:
    """The per-platform pick a ``settings.json`` ``platform_cores`` value holds, or ``None`` where it holds none.

    The value is ``{"label", "source"}`` (settings version 14); anything else,
    an empty label included, is no pick.
    """
    if not isinstance(value, dict):
        return None
    label = value.get("label")
    source = value.get("source")
    if not isinstance(label, str) or not label or not isinstance(source, str) or not source:
        return None
    return StoredPick(label=label, source=source)


def pick_for(pick: StoredPick | None, starting_source: str | None) -> StoredPick | None:
    """*pick* while its source is the one games start through (*starting_source*), else ``None``.

    A pick whose source is not that one is kept as it is stored, the default
    applies meanwhile, and nothing is said of it; its label is only ever
    checked against its own source's list (#2222 D5).
    """
    return pick if pick is not None and pick.source == starting_source else None


def source_kind(answer: dict[str, Any]) -> str | None:
    """The kind of the source an emulator-options *answer* came from, ``None`` where none answered."""
    source = answer["source"]
    return source["kind"] if source is not None else None


def platform_pick_value(label: str, source: str) -> dict[str, str]:
    """The ``settings.json`` ``platform_cores`` value that stores *label*, chosen from the source of kind *source*."""
    return {"label": label, "source": source}
