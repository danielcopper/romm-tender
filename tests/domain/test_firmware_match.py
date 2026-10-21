"""Unit tests for matching firmware files to emulator requirements by content."""

from __future__ import annotations

from domain.firmware_match import (
    MATCHED_ARCHIVE_NAME,
    MATCHED_CONTENT,
    MATCHED_NAME,
    FirmwareFile,
    filling,
    fills,
    fills_any,
    first_filling,
)
from domain.firmware_wants import (
    DECLARED_DIRECTORY,
    IDENTITY_ARCHIVE,
    IDENTITY_FILE,
    FirmwarePlacement,
    FirmwareWant,
)

_SATURN_MD5 = "af5828fdff51384f99b3c4926be27762"


def _placement(file_name: str, *, md5: str | None = None, kind: str | None = None, **extra) -> FirmwarePlacement:
    return FirmwarePlacement(
        file_name=file_name,
        relative_path=file_name,
        description=file_name,
        wants=(FirmwareWant(emulator="yabasanshiro_libretro.so", required=True),),
        identity_md5=md5,
        identity_kind=kind,
        **extra,
    )


class TestByContent:
    def test_a_file_under_an_alias_fills_the_requirement_its_bytes_are(self):
        saturn = _placement("saturn_bios.bin", md5=_SATURN_MD5, kind=IDENTITY_FILE)

        assert fills(saturn, FirmwareFile("sega_100.bin", _SATURN_MD5)) == MATCHED_CONTENT

    def test_a_name_in_other_letters_fills_the_requirement_its_bytes_are(self):
        psp = _placement("psxonpsp660.bin", md5="c53ca5908936d412331790f4426c6c33", kind=IDENTITY_FILE)

        assert fills(psp, FirmwareFile("PSXONPSP660.BIN", "C53CA5908936D412331790F4426C6C33")) == MATCHED_CONTENT

    def test_the_declared_name_with_other_bytes_fills_nothing(self):
        saturn = _placement("saturn_bios.bin", md5=_SATURN_MD5, kind=IDENTITY_FILE)

        assert fills(saturn, FirmwareFile("saturn_bios.bin", "0" * 32)) is None

    def test_a_file_without_a_checksum_fills_no_checksummed_requirement(self):
        saturn = _placement("saturn_bios.bin", md5=_SATURN_MD5, kind=IDENTITY_FILE)

        assert fills(saturn, FirmwareFile("saturn_bios.bin", "")) is None


class TestByName:
    def test_an_archive_is_filled_by_its_declared_name_in_any_letter_case(self):
        neogeo = _placement("neogeo.zip", md5="dffb72f116d36d025068b23970a4f6df", kind=IDENTITY_ARCHIVE)

        assert fills(neogeo, FirmwareFile("NeoGeo.zip", "1" * 32)) == MATCHED_ARCHIVE_NAME
        assert fills(neogeo, FirmwareFile("neogeo-repack.zip", "dffb72f116d36d025068b23970a4f6df")) is None

    def test_a_requirement_with_no_known_checksum_is_filled_by_its_name(self):
        keys = _placement("keys.txt")

        assert fills(keys, FirmwareFile("Keys.TXT", "2" * 32)) == MATCHED_NAME
        assert fills(keys, FirmwareFile("otp.bin", "2" * 32)) is None


class TestAFolderDeclaration:
    def test_is_filled_by_each_file_the_table_files_under_it(self):
        folder = _placement(
            "bios",
            declared_kind=DECLARED_DIRECTORY,
            folder_md5s=frozenset({"3" * 32, "4" * 32}),
        )

        assert fills(folder, FirmwareFile("SCPH-39001.bin", "3" * 32)) == MATCHED_CONTENT
        assert fills(folder, FirmwareFile("bios", "5" * 32)) is None


class TestSeveralFiles:
    def test_the_alphabetically_first_of_two_with_the_same_bytes_is_the_one_linked(self):
        saturn = _placement("saturn_bios.bin", md5=_SATURN_MD5, kind=IDENTITY_FILE)
        files = [FirmwareFile("sega_101.bin", _SATURN_MD5), FirmwareFile("Sega_100.bin", _SATURN_MD5)]

        assert [f.file_name for f in filling(saturn, files)] == ["Sega_100.bin", "sega_101.bin"]
        assert first_filling(saturn, files) == FirmwareFile("Sega_100.bin", _SATURN_MD5)

    def test_none_where_no_file_fills(self):
        saturn = _placement("saturn_bios.bin", md5=_SATURN_MD5, kind=IDENTITY_FILE)

        assert first_filling(saturn, [FirmwareFile("x.bin", "6" * 32)]) is None

    def test_any_of_several_requirements(self):
        saturn = _placement("saturn_bios.bin", md5=_SATURN_MD5, kind=IDENTITY_FILE)
        keys = _placement("keys.txt")

        assert fills_any([saturn, keys], FirmwareFile("keys.txt", "")) is True
        assert fills_any([saturn, keys], FirmwareFile("unknown.bin", "7" * 32)) is False
