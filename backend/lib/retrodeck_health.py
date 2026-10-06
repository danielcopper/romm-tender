"""Health classification for RetroDECK path resolution from ``retrodeck.json``.

Cross-cutting enum shared by the adapter that reads ``retrodeck.json``
(``adapters/retrodeck_paths.py``) and the Protocol services depend on
(``services/protocols/paths.py``). It lives in ``lib/`` because both
layers import it and ``import-linter`` forbids the adapter→service and
service→adapter directions.
"""

from __future__ import annotations

from enum import StrEnum


class RetroDeckConfigHealth(StrEnum):
    """How trustworthy the resolved RetroDECK roots are right now.

    The path getters are always best-effort and never raise; this enum
    says when the resolved roots are likely wrong. Nothing reads it: the
    panel's health notices come from the resolver's findings per emulator
    source.
    """

    OK = "ok"
    """``retrodeck.json`` read successfully AND the resolved RetroDECK home exists on disk."""

    ABSENT = "absent"
    """``retrodeck.json`` not found — the legitimate fresh-install fallback to ``~/retrodeck``."""

    UNREADABLE = "unreadable"
    """``retrodeck.json`` exists but cannot be read or parsed — we know RetroDECK is configured but not where."""

    ROOT_MISSING = "root_missing"
    """``retrodeck.json`` read OK, but the resolved home is missing on disk (e.g. SD card ejected)."""
