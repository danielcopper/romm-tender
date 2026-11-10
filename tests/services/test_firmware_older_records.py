"""Tests for services/firmware/older_records.py — the download records a platform's page offers (#2452 D6)."""

from __future__ import annotations

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


def _records(uow: FakeUnitOfWork, refiling: FakeListingRefiler, folder: str, slug: str) -> tuple[list[str], list[str]]:
    """The folders of the records offered, and what was said to the debug log."""
    said: list[str] = []
    found = platform_records(FakeUnitOfWorkFactory(uow), refiling, lambda msg: said.append(msg), folder, slug)
    return [record.platform_folder for record in found], said


class TestPlatformRecords:
    def test_a_record_under_the_shared_slug_is_offered_once_a_listing_is_read(self):
        refiling = FakeListingRefiler(listed=frozenset({"gc", "ngc"}))

        records, _ = _records(_uow_with("gamecube", "gc"), refiling, "gc", "gamecube")

        assert records == ["gamecube", "gc"]

    def test_before_any_listing_a_record_under_the_slug_is_held_back_and_said_to_the_debug_log(self):
        records, said = _records(_uow_with("gamecube", "gc"), FakeListingRefiler(), "gc", "gamecube")

        assert records == ["gc"]
        assert said == [
            "Not offering the BIOS records under gamecube on gc's page until RomM's platform listing is read: IPL.bin"
        ]

    def test_nothing_held_back_says_nothing(self):
        _, said = _records(_uow_with("gc"), FakeListingRefiler(), "gc", "gamecube")

        assert said == []

    def test_once_a_listing_is_read_nothing_is_said(self):
        refiling = FakeListingRefiler(listed=frozenset({"gc", "ngc"}))

        _, said = _records(_uow_with("gamecube"), refiling, "gc", "gamecube")

        assert said == []

    def test_a_platform_whose_folder_is_its_slug_holds_nothing_back_and_says_nothing(self):
        records, said = _records(_uow_with("snes"), FakeListingRefiler(), "snes", "snes")

        assert records == ["snes"]
        assert said == []
