"""A user's emulator pick as Tender stores it: the entry's display label and the emulator source it was chosen from.

A label is what ES-DE stores a choice by, unique within one system's catalogue;
the source says whose catalogue that is, so a label that no longer exists there
can be named with it (#2195 D5, D20). ``source`` is ``None`` on a pick stored
before the source was recorded: nothing says which catalogue it came from, and
it is read against the source the game starts through.

Pure compute — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoredPick:
    """One stored emulator pick: its ``label`` and the kind of its emulator ``source``, ``None`` where unrecorded."""

    label: str
    source: str | None


def platform_pick(value: object) -> StoredPick | None:
    """The per-platform pick a ``settings.json`` ``platform_cores`` value holds, or ``None`` where it holds none.

    The value is ``{"label", "source"}`` (settings version 14); anything else,
    an empty label included, is no pick.
    """
    if not isinstance(value, dict):
        return None
    label = value.get("label")
    source = value.get("source")
    if not isinstance(label, str) or not label or not (source is None or isinstance(source, str)):
        return None
    return StoredPick(label=label, source=source)


def platform_pick_value(label: str, source: str) -> dict[str, str]:
    """The ``settings.json`` ``platform_cores`` value that stores *label*, chosen from the source of kind *source*."""
    return {"label": label, "source": source}
