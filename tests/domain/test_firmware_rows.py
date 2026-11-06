"""Unit tests for which library file fills a BIOS row (:func:`domain.firmware_rows.library_match`)."""

from __future__ import annotations

from domain.firmware_rows import LibraryFirmware, library_match
from domain.firmware_wants import IDENTITY_FILE, FirmwarePlacement, FirmwareWant

_IPL_MD5 = "a860e8c0b6d573d191e4ec7db1b1e4f6"


def _placement() -> FirmwarePlacement:
    return FirmwarePlacement(
        file_name="IPL.bin",
        relative_path="IPL.bin",
        description="IPL",
        wants=(FirmwareWant(emulator="dolphin", required=True),),
        identity_md5=_IPL_MD5,
        identity_kind=IDENTITY_FILE,
    )


def _id_of(library: list[LibraryFirmware], folder: str) -> int | None:
    match = library_match(_placement(), library, folder)
    assert match is not None
    return match["firmware_id"]


def _file(firmware_id: int, name: str, folder: str) -> LibraryFirmware:
    return LibraryFirmware(
        file_name=name, md5=_IPL_MD5, firmware_id=firmware_id, platform_folder=folder, platform_slug="ngc"
    )


class TestThePlatformsOwnFileFirst:
    def test_its_own_file_fills_the_row_before_another_platforms_whatever_the_names(self):
        library = [_file(10, "a_gc_ipl.bin", "gc"), _file(20, "IPL.bin", "gamecube")]

        assert _id_of(library, "gamecube") == 20
        assert _id_of(library, "gc") == 10

    def test_another_platforms_file_fills_it_where_none_of_its_own_does(self):
        library = [_file(10, "a_gc_ipl.bin", "gc")]

        assert _id_of(library, "gamecube") == 10
