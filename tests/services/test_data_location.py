"""Tests for the data-location condition the panel raises and the answer it records."""

from __future__ import annotations

import logging

import pytest
from fakes.running_loop import running_loop
from models.data_location import SourceDescription, UserDataLocations

from services.data_location import DataLocationService, DataLocationServiceConfig

_DECKY_SETTINGS = "/home/deck/homebrew/settings/romm-tender"
_DECKY_DATA = "/home/deck/homebrew/data/romm-tender"
_OWN_SETTINGS = "/home/deck/.config/romm-tender"
_OWN_DATA = "/home/deck/.local/share/romm-tender"


class FakeDataLocationStore:
    """In-memory stand-in for the migration adapter's two live operations."""

    def __init__(self) -> None:
        self.described: list[SourceDescription] = []
        self.recorded: list[str] = []
        self.describe_error: Exception | None = None
        self.record_error: Exception | None = None

    def describe_sources(self) -> list[SourceDescription]:
        if self.describe_error is not None:
            raise self.describe_error
        return self.described

    def record_answer(self, name: str) -> None:
        if self.record_error is not None:
            raise self.record_error
        self.recorded.append(name)


def _make(locations: UserDataLocations, store: FakeDataLocationStore | None = None):
    used = store if store is not None else FakeDataLocationStore()
    service = DataLocationService(
        config=DataLocationServiceConfig(
            locations=locations,
            store=used,
            loop=running_loop(),
            logger=logging.getLogger("test"),
        ),
    )
    return service, used


def _settled() -> UserDataLocations:
    return UserDataLocations(settings_dir=_OWN_SETTINGS, data_dir=_OWN_DATA, choice_required=False, failure=None)


class TestGetDataLocationNotice:
    def test_a_finished_migration_raises_nothing(self):
        service, _ = _make(_settled())
        assert service.get_data_location_notice() == {"pending": False, "kind": None, "message": None}

    def test_two_libraries_raise_the_choice(self):
        service, _ = _make(
            UserDataLocations(
                settings_dir=_DECKY_SETTINGS,
                data_dir=_DECKY_DATA,
                choice_required=True,
                failure=None,
            ),
        )
        assert service.get_data_location_notice() == {"pending": True, "kind": "choice", "message": None}

    def test_a_failed_copy_reports_what_went_wrong(self):
        """The plugin keeps running from Decky's directories and says why."""
        locations = UserDataLocations(
            settings_dir=_DECKY_SETTINGS,
            data_dir=_DECKY_DATA,
            choice_required=False,
            failure="[Errno 28] No space left on device",
        )
        service, _ = _make(locations)

        assert service.get_data_location_notice() == {
            "pending": True,
            "kind": "failed",
            "message": "[Errno 28] No space left on device",
        }
        assert locations.settings_dir == _DECKY_SETTINGS
        assert locations.data_dir == _DECKY_DATA


class TestGetDataLocationCandidates:
    @pytest.mark.asyncio
    async def test_hands_back_what_the_store_measured(self):
        store = FakeDataLocationStore()
        store.described = [
            {
                "source": "decky-romm-sync",
                "path": "/a",
                "present": True,
                "size_bytes": 12,
                "changed_at": "2026-09-01T00:00:00+00:00",
            },
        ]
        service, _ = _make(_settled(), store)

        assert await service.get_data_location_candidates() == {"candidates": store.described}

    @pytest.mark.asyncio
    async def test_a_location_that_is_gone_is_passed_through_rather_than_dropped(self):
        """A choice shown with one option is not the question that was asked."""
        store = FakeDataLocationStore()
        store.described = [
            {"source": "decky-romm-sync", "path": "/a", "present": True, "size_bytes": 12, "changed_at": None},
            {"source": "romm-tender", "path": "/b", "present": False, "size_bytes": None, "changed_at": None},
        ]
        service, _ = _make(_settled(), store)

        result = await service.get_data_location_candidates()

        assert [entry["source"] for entry in result["candidates"]] == ["decky-romm-sync", "romm-tender"]
        assert result["candidates"][1]["present"] is False


class TestChooseDataLocation:
    @pytest.mark.asyncio
    async def test_records_the_answer_and_copies_nothing(self):
        store = FakeDataLocationStore()
        service, _ = _make(_settled(), store)

        assert await service.choose_data_location("decky-romm-sync") == {"success": True}
        assert store.recorded == ["decky-romm-sync"]

    @pytest.mark.asyncio
    async def test_an_unknown_location_is_refused(self):
        store = FakeDataLocationStore()
        store.record_error = ValueError("nope is not one of this plugin's older data locations")
        service, _ = _make(_settled(), store)

        result = await service.choose_data_location("nope")

        assert result["success"] is False
        assert result["reason"] == "unknown_source"
        assert "older data locations" in result["message"]

    @pytest.mark.asyncio
    async def test_an_answer_that_cannot_be_written_is_reported(self):
        store = FakeDataLocationStore()
        store.record_error = OSError("Read-only file system")
        service, _ = _make(_settled(), store)

        result = await service.choose_data_location("romm-tender")

        assert result["success"] is False
        assert result["reason"] == "write_failed"
        assert "Read-only file system" in result["message"]
        assert store.recorded == []
