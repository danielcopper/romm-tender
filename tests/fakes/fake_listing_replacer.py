"""In-memory ``ListingReplacer`` for service tests: records every listing it is handed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class FakeListingReplacer:
    """Keeps each listing ``replace_listing`` was handed in ``listings``."""

    def __init__(self) -> None:
        self.listings: list[list[Mapping[str, Any]]] = []

    def replace_listing(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        self.listings.append(list(platforms))
