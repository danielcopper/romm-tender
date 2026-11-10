"""In-memory ``ListingRefiler`` for service tests: records every listing it is handed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.platform_names import listed_platform_folders

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class FakeListingRefiler:
    """Keeps each listing ``do_refile`` was handed in ``listings``; ``listed_folders`` answers for the last one."""

    def __init__(self, listed: frozenset[str] | None = None) -> None:
        self.listings: list[list[Mapping[str, Any]]] = []
        self._listed = listed

    def do_refile(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        self.listings.append(list(platforms))
        self._listed = listed_platform_folders(platforms)

    def listed_folders(self) -> frozenset[str] | None:
        return self._listed
