"""In-memory ``BiosFileRepository`` implementation for service tests."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from domain.bios_file import BiosFile


class FakeBiosFileRepository:
    """Dict-backed ``BiosFileRepository`` keyed by ``(platform_folder, file_name)``."""

    def __init__(self) -> None:
        self._files: dict[tuple[str, str], BiosFile] = {}
        self.save_count = 0

    def get(self, platform_folder: str, file_name: str) -> BiosFile | None:
        return copy.deepcopy(self._files.get((platform_folder, file_name)))

    def save(self, bios_file: BiosFile) -> None:
        self.save_count += 1
        self._files[(bios_file.platform_folder, bios_file.file_name)] = copy.deepcopy(bios_file)

    def delete(self, platform_folder: str, file_name: str) -> None:
        self._files.pop((platform_folder, file_name), None)

    def iter_all(self) -> Iterator[BiosFile]:
        return iter([copy.deepcopy(f) for f in self._files.values()])

    def _snapshot(self) -> dict[tuple[str, str], BiosFile]:
        return copy.deepcopy(self._files)

    def _restore(self, state: dict[tuple[str, str], BiosFile]) -> None:
        self._files = state
