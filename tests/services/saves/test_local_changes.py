"""Tests for the saves package's changed-since-last-sync comparison."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from domain.rom_save_sync_state import FileSyncState
from services.saves._local_changes import changed_since_last_sync

if TYPE_CHECKING:
    from services.protocols import SaveFileStore


class _HashingStore:
    """A ``SaveFileStore`` that answers ``content_hash`` from a map and records each path it hashed."""

    def __init__(self, hashes: dict[str, str], *, raises: BaseException | None = None) -> None:
        self.hashes = hashes
        self.raises = raises
        self.hashed: list[str] = []

    def content_hash(self, path: str) -> str:
        self.hashed.append(path)
        if self.raises is not None:
            raise self.raises
        return self.hashes[path]


def _changed(local_files, files_state, store: _HashingStore) -> list[str]:
    return list(changed_since_last_sync(local_files, files_state, cast("SaveFileStore", store)))


_SRM = {"path": "/saves/game.srm", "filename": "game.srm"}
_RTC = {"path": "/saves/game.rtc", "filename": "game.rtc"}


class TestChangedSinceLastSync:
    def test_a_file_whose_content_differs_from_its_recorded_hash_is_listed(self):
        store = _HashingStore({"/saves/game.srm": "hash-B"})

        assert _changed([_SRM], {"game.srm": FileSyncState(last_sync_hash="hash-A")}, store) == ["game.srm"]

    def test_a_file_whose_content_matches_its_recorded_hash_is_not_listed(self):
        store = _HashingStore({"/saves/game.srm": "hash-A"})

        assert _changed([_SRM], {"game.srm": FileSyncState(last_sync_hash="hash-A")}, store) == []
        assert store.hashed == ["/saves/game.srm"]

    def test_a_file_with_no_recorded_hash_is_not_listed_and_never_hashed(self):
        store = _HashingStore({"/saves/game.srm": "hash-A"})

        assert _changed([_SRM], {"game.srm": FileSyncState(last_sync_hash=None)}, store) == []
        assert store.hashed == []

    def test_a_file_with_no_sync_state_at_all_is_not_listed_and_never_hashed(self):
        store = _HashingStore({"/saves/game.srm": "hash-A"})

        assert _changed([_SRM], {}, store) == []
        assert store.hashed == []

    def test_every_changed_file_is_listed_in_discovery_order(self):
        store = _HashingStore({"/saves/game.srm": "srm-NEW", "/saves/game.rtc": "rtc-NEW"})
        files_state = {
            "game.srm": FileSyncState(last_sync_hash="srm-base"),
            "game.rtc": FileSyncState(last_sync_hash="rtc-base"),
        }

        assert _changed([_SRM, _RTC], files_state, store) == ["game.srm", "game.rtc"]

    def test_a_caller_that_stops_at_the_first_change_hashes_nothing_after_it(self):
        store = _HashingStore({"/saves/game.srm": "srm-NEW", "/saves/game.rtc": "rtc-NEW"})
        files_state = {
            "game.srm": FileSyncState(last_sync_hash="srm-base"),
            "game.rtc": FileSyncState(last_sync_hash="rtc-base"),
        }

        changed = changed_since_last_sync([_SRM, _RTC], files_state, cast("SaveFileStore", store))

        assert next(changed) == "game.srm"
        assert store.hashed == ["/saves/game.srm"]

    def test_a_hash_failure_reaches_the_caller(self):
        store = _HashingStore({}, raises=OSError("file vanished"))

        with pytest.raises(OSError, match="file vanished"):
            _changed([_SRM], {"game.srm": FileSyncState(last_sync_hash="hash-A")}, store)
