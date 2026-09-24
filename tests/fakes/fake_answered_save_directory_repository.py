"""In-memory ``AnsweredSaveDirectoryRepository`` implementation for service tests."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.answered_save_directory import AnsweredSaveDirectory


class FakeAnsweredSaveDirectoryRepository:
    """Dict-backed ``AnsweredSaveDirectoryRepository`` keyed by ``rom_id``."""

    def __init__(self) -> None:
        self._records: dict[int, AnsweredSaveDirectory] = {}
        self.save_count = 0

    def get(self, rom_id: int) -> AnsweredSaveDirectory | None:
        return copy.deepcopy(self._records.get(rom_id))

    def save(self, record: AnsweredSaveDirectory) -> None:
        self.save_count += 1
        self._records[record.rom_id] = copy.deepcopy(record)

    def delete(self, rom_id: int) -> None:
        self._records.pop(rom_id, None)

    def _snapshot(self) -> dict[int, AnsweredSaveDirectory]:
        return copy.deepcopy(self._records)

    def _restore(self, state: dict[int, AnsweredSaveDirectory]) -> None:
        self._records = state
