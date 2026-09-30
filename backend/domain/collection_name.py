"""When two Steam collection names are the same collection.

Steam's collection identity ignores case, and the rule this program matches it
with is ``docs/architecture/steam-non-steam-shortcuts.md`` § Name identity is
case-insensitive. The frontend applies the same rule in
``frontend/src/utils/collectionName.ts``; ``tests/domain/collection_name_folds.json``
holds the names both are tested against. No I/O, no state.
"""

from __future__ import annotations


def fold_collection_name(name: str) -> str:
    """Return the key under which *name* is one Steam collection with every name sharing it.

    Lower, upper, then lower case again — not ``str.casefold``, which JavaScript
    has no counterpart for, so the frontend could not compute the same key; and
    not a single ``lower``, which keeps ``Straße`` apart from ``STRASSE``.
    """
    return name.lower().upper().lower()
