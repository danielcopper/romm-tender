"""When two Steam collection names are the same collection.

Steam's collection identity ignores case, and the rule this program matches it
with is ``docs/architecture/steam-non-steam-shortcuts.md`` § Name identity is
case-insensitive. The frontend applies the same rule in
``frontend/src/utils/collectionName.ts``; ``tests/domain/collection_name_folds.json``
holds the names both are tested against. No I/O, no state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def fold_collection_name(name: str) -> str:
    """Return the key under which *name* is one Steam collection with every name sharing it."""
    return name.lower().upper().lower()


def spelling_by_fold(names: Iterable[str]) -> dict[str, str]:
    """Map each folded name among *names* to its sorted-first spelling among *names*."""
    spelling: dict[str, str] = {}
    for name in sorted(names):
        spelling.setdefault(fold_collection_name(name), name)
    return spelling
