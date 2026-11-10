"""In-memory ``ListingRefiler`` for service tests: records every listing it is handed, and can be made to fail."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class FakeListingRefiler:
    """Keeps each listing ``do_refile`` was handed in ``listings``; raises ``error`` instead where one is set."""

    def __init__(self, error: Exception | None = None) -> None:
        self.listings: list[list[Mapping[str, Any]]] = []
        self.error = error

    def do_refile(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        if self.error is not None:
            raise self.error
        self.listings.append(list(platforms))
