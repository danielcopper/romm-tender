"""In-memory ``FirmwareStoreRepository`` and ``FirmwareLinkRepository`` implementations for service tests."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from domain.firmware_store import FirmwareLink, StoredFirmware


class FakeFirmwareStoreRepository:
    """Dict-backed ``FirmwareStoreRepository`` keyed by ``(platform_slug, file_name)``."""

    def __init__(self) -> None:
        self._files: dict[tuple[str, str], StoredFirmware] = {}

    def get(self, platform_slug: str, file_name: str) -> StoredFirmware | None:
        return copy.deepcopy(self._files.get((platform_slug, file_name)))

    def save(self, stored: StoredFirmware) -> None:
        self._files[(stored.platform_slug, stored.file_name)] = copy.deepcopy(stored)

    def delete(self, platform_slug: str, file_name: str) -> None:
        self._files.pop((platform_slug, file_name), None)

    def iter_all(self) -> Iterator[StoredFirmware]:
        return iter([copy.deepcopy(f) for f in self._files.values()])

    def get_folders(self) -> frozenset[str]:
        return frozenset(f.folder for f in self._files.values())

    def _snapshot(self) -> dict[tuple[str, str], StoredFirmware]:
        return copy.deepcopy(self._files)

    def _restore(self, state: dict[tuple[str, str], StoredFirmware]) -> None:
        self._files = state


class FakeFirmwareLinkRepository:
    """Dict-backed ``FirmwareLinkRepository`` keyed by place."""

    def __init__(self) -> None:
        self._links: dict[str, FirmwareLink] = {}

    def get(self, place: str) -> FirmwareLink | None:
        return copy.deepcopy(self._links.get(place))

    def save(self, link: FirmwareLink) -> None:
        self._links[link.place] = copy.deepcopy(link)

    def delete(self, place: str) -> None:
        self._links.pop(place, None)

    def iter_all(self) -> Iterator[FirmwareLink]:
        return iter([copy.deepcopy(link) for link in self._links.values()])

    def _snapshot(self) -> dict[str, FirmwareLink]:
        return copy.deepcopy(self._links)

    def _restore(self, state: dict[str, FirmwareLink]) -> None:
        self._links = state
