"""Tests for services/firmware/older_records.py — the download records a platform's page offers (#2452 D6)."""

from __future__ import annotations

import logging

from fakes.fake_listing_refiler import FakeListingRefiler
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.bios_file import BiosFile
from services.firmware.older_records import platform_records


def _uow_with(*folders: str) -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    for folder in folders:
        uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_folder=folder,
                file_name="IPL.bin",
                file_path=f"/bios/{folder}/IPL.bin",
                downloaded_at="2026-01-01T00:00:00+00:00",
            )
        )
    return uow


def _records(uow: FakeUnitOfWork, refiling: FakeListingRefiler, folder: str, slug: str) -> list[str]:
    found = platform_records(FakeUnitOfWorkFactory(uow), refiling, logging.getLogger("test"), folder, slug)
    return [record.platform_folder for record in found]


class TestPlatformRecords:
    def test_a_record_under_the_shared_slug_is_offered_once_a_listing_is_read(self):
        refiling = FakeListingRefiler(listed=frozenset({"gc", "ngc"}))

        assert _records(_uow_with("gamecube", "gc"), refiling, "gc", "gamecube") == ["gamecube", "gc"]

    def test_before_any_listing_a_record_under_the_slug_is_held_back_and_said_at_debug(self, caplog):
        refiling = FakeListingRefiler()

        with caplog.at_level(logging.DEBUG):
            records = _records(_uow_with("gamecube", "gc"), refiling, "gc", "gamecube")

        assert records == ["gc"]
        assert (
            "Not offering the BIOS records under gamecube on gc's page until RomM's platform listing is read: IPL.bin"
            in caplog.text
        )

    def test_nothing_held_back_says_nothing(self, caplog):
        with caplog.at_level(logging.DEBUG):
            _records(_uow_with("gc"), FakeListingRefiler(), "gc", "gamecube")

        assert "Not offering" not in caplog.text

    def test_once_a_listing_is_read_nothing_is_said(self, caplog):
        refiling = FakeListingRefiler(listed=frozenset({"gc", "ngc"}))

        with caplog.at_level(logging.DEBUG):
            _records(_uow_with("gamecube"), refiling, "gc", "gamecube")

        assert "Not offering" not in caplog.text
