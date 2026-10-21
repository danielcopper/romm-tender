"""Tests for ``SqliteFirmwareStoreRepository`` and ``SqliteFirmwareLinkRepository``."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from domain.firmware_store import FirmwareLink, StoredFirmware

if TYPE_CHECKING:
    from adapters.repositories.unit_of_work import SqliteUnitOfWork


def _stored(platform: str, file_name: str, *, folder: str = "/home/deck/Tender") -> StoredFirmware:
    return StoredFirmware.mark_downloaded(
        platform_slug=platform,
        file_name=file_name,
        folder=folder,
        md5="85ec9ca47d8f6807718151cbcca8b964",
        size=524288,
        downloaded_at="2026-10-21T20:00:00",
        firmware_id=7,
    )


def _link(place: str, platform: str = "saturn", file_name: str = "sega_101.bin") -> FirmwareLink:
    return FirmwareLink.mark_placed(
        place=place,
        target=f"/home/deck/Tender/bios/{platform}/{file_name}",
        platform_slug=platform,
        file_name=file_name,
        source="retrodeck",
        placed_at="2026-10-21T20:00:00",
    )


class TestTheStore:
    def test_round_trips_every_field(self, uow: SqliteUnitOfWork):
        stored = _stored("saturn", "sega_101.bin")
        uow.firmware_store.save(stored)

        assert uow.firmware_store.get("saturn", "sega_101.bin") == stored

    def test_one_name_on_two_platforms_is_two_files(self, uow: SqliteUnitOfWork):
        uow.firmware_store.save(_stored("gb", "gb_bios.bin"))
        uow.firmware_store.save(_stored("gbc", "gb_bios.bin"))

        assert sorted(f.platform_slug for f in uow.firmware_store.iter_all()) == ["gb", "gbc"]

    def test_delete_removes_one_and_is_idempotent(self, uow: SqliteUnitOfWork):
        uow.firmware_store.save(_stored("saturn", "sega_101.bin"))

        uow.firmware_store.delete("saturn", "sega_101.bin")
        uow.firmware_store.delete("saturn", "sega_101.bin")

        assert uow.firmware_store.get("saturn", "sega_101.bin") is None

    def test_names_every_folder_a_file_is_recorded_under_once(self, uow: SqliteUnitOfWork):
        uow.firmware_store.save(_stored("gb", "a.bin"))
        uow.firmware_store.save(_stored("gb", "b.bin"))
        uow.firmware_store.save(_stored("gba", "c.bin", folder="/run/media/deck/SD/Tender"))

        assert uow.firmware_store.get_folders() == frozenset({"/home/deck/Tender", "/run/media/deck/SD/Tender"})

    def test_refuses_a_file_without_a_checksum(self, uow: SqliteUnitOfWork):
        stored = _stored("saturn", "sega_101.bin")
        stored_without = StoredFirmware(
            platform_slug=stored.platform_slug,
            file_name=stored.file_name,
            folder=stored.folder,
            md5="",
            size=stored.size,
            downloaded_at=stored.downloaded_at,
        )

        with pytest.raises(sqlite3.IntegrityError):
            uow.firmware_store.save(stored_without)


class TestTheLinks:
    def test_round_trips_every_field(self, uow: SqliteUnitOfWork):
        link = _link("/home/deck/retrodeck/bios/saturn_bios.bin")
        uow.firmware_links.save(link)

        assert uow.firmware_links.get(link.place) == link

    def test_a_place_holds_one_record(self, uow: SqliteUnitOfWork):
        place = "/home/deck/retrodeck/bios/saturn_bios.bin"
        uow.firmware_links.save(_link(place, file_name="sega_100.bin"))
        uow.firmware_links.save(_link(place, file_name="sega_101.bin"))

        assert [link.file_name for link in uow.firmware_links.iter_all()] == ["sega_101.bin"]

    def test_outlives_its_store_file(self, uow: SqliteUnitOfWork):
        link = _link("/home/deck/retrodeck/bios/saturn_bios.bin")
        uow.firmware_store.save(_stored("saturn", "sega_101.bin"))
        uow.firmware_links.save(link)

        uow.firmware_store.delete("saturn", "sega_101.bin")

        assert uow.firmware_links.get(link.place) == link

    def test_delete_is_idempotent(self, uow: SqliteUnitOfWork):
        place = "/home/deck/retrodeck/bios/saturn_bios.bin"
        uow.firmware_links.save(_link(place))

        uow.firmware_links.delete(place)
        uow.firmware_links.delete(place)

        assert uow.firmware_links.get(place) is None
