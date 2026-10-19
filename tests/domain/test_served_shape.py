"""Tests for ``domain/served_shape`` — which single zips are unpacked, and what a download becomes on disk."""

from __future__ import annotations

from typing import Any

import pytest

from domain.rom_adoption import ServerFile, server_manifest
from domain.rom_candidates import DIR, FILE
from domain.served_shape import (
    holds_disc_image,
    is_zip_name,
    listed_unpacked_bytes,
    replace_covers,
    served_shapes,
    unpacked_manifest,
    wrapper_prefix,
)


class TestHoldsDiscImage:
    @pytest.mark.parametrize(
        "names",
        [
            ["Game.cue", "Game (Track 1).bin", "Game (Track 2).bin"],
            ["Game.gdi", "track01.bin", "track02.raw"],
            ["Game.rvz"],
            ["Game.wbfs"],
            ["Game.chd"],
            ["Game (Disc 1).iso", "Game (Disc 2).iso", "readme.txt"],
            ["Game.img", "Game.ccd", "Game.sub"],
            ["Game.m3u", "Game (Disc 1).chd"],
            ["Game.iso", "extras/manual.iso"],
            ["Game/Game.cue", "Game/Game (Track 1).bin"],
            ["Game/Game.rvz"],
            ["Game\\Game.cue", "Game\\Game.bin"],
        ],
        ids=[
            "cue set",
            "gdi set",
            "rvz",
            "wbfs",
            "chd",
            "isos",
            "img with ccd",
            "chd beside an m3u",
            "top and nested",
            "cue set in a wrapper folder",
            "rvz in a wrapper folder",
            "wrapper folder named with a backslash",
        ],
    )
    def test_a_disc_set_is_unpacked(self, names):
        assert holds_disc_image(names) is True

    @pytest.mark.parametrize(
        "names",
        [
            ["Game.gba"],
            ["mslug.rom", "mslug.c1", "mslug.p1"],
            ["Game.bin"],
            ["Game.img"],
            ["Game.img", "Game.sub"],
            ["Game.m3u"],
            ["Game.m3u", "Game (Disk 1).d64", "Game (Disk 2).d64"],
            ["GAME.EXE", "cd/game.cue", "cd/game.bin"],
            ["Game/disc/Game.cue", "Game/disc/Game.bin"],
            ["GAME.EXE", "cd\\game.cue", "cd\\game.bin"],
            ["Game/GAME.EXE", "Game/cd/game.cue", "Game/cd/game.bin"],
            ["Game/Game.gba"],
            ["Game/Game.cue", "Other/Other.cue"],
            [],
        ],
        ids=[
            "cartridge",
            "arcade romset",
            "bin alone",
            "img alone",
            "img without ccd",
            "m3u alone",
            "floppy m3u",
            "dos game with its cd",
            "cue two folders deep",
            "a folder named with a backslash",
            "dos game with its cd in a wrapper folder",
            "cartridge in a wrapper folder",
            "two folders are no wrapper",
            "empty",
        ],
    )
    def test_anything_else_is_kept(self, names):
        assert holds_disc_image(names) is False

    def test_an_upper_case_extension_counts(self):
        assert holds_disc_image(["GAME.CUE", "GAME.BIN"]) is True


class TestWrapperPrefix:
    @pytest.mark.parametrize(
        ("names", "prefix"),
        [
            (["Game/Game.cue", "Game/Game.bin"], "Game/"),
            (["Game/", "Game/Game.cue"], "Game/"),
            (["Game\\Game.cue"], "Game\\"),
            (["Game/Game.cue", "readme.txt"], ""),
            (["Game/Game.cue", "Other/Other.cue"], ""),
            (["Game.rvz"], ""),
            ([], ""),
        ],
        ids=["one folder", "its own entry", "backslash", "a file beside it", "two folders", "one file", "empty"],
    )
    def test_the_one_folder_around_everything(self, names, prefix):
        assert wrapper_prefix(names) == prefix


class TestIsZipName:
    @pytest.mark.parametrize("name", ["Game.zip", "Game.ZIP", "/roms/saturn/Game (USA).Zip"])
    def test_a_zip_by_its_extension(self, name):
        assert is_zip_name(name) is True

    @pytest.mark.parametrize("name", ["Game.7z", "Game.zip.tmp", "Game.cue", "zip"])
    def test_anything_else(self, name):
        assert is_zip_name(name) is False


def _single_zip(**extra: Any) -> dict[str, Any]:
    return {"id": 1, "fs_name": "Game.zip", "fs_name_no_ext": "Game", **extra}


def _listed(*members: tuple[str, int]) -> list[dict[str, Any]]:
    return [{"file_name": "Game.zip", "archive_members": [{"name": name, "size": size} for name, size in members]}]


class TestServedShapes:
    def test_a_multi_file_rom_is_a_folder(self):
        detail = {"fs_name": "Game.zip", "files": [{"file_name": "a.cue"}, {"file_name": "a.bin"}]}

        assert served_shapes(detail) == frozenset({DIR})

    def test_a_single_file_that_is_no_zip_is_a_file(self):
        assert served_shapes({"fs_name": "Game.chd", "files": [{"file_name": "Game.chd"}]}) == frozenset({FILE})

    def test_a_single_zip_without_a_listing_is_both(self):
        assert served_shapes(_single_zip(files=[{"file_name": "Game.zip"}])) == frozenset({FILE, DIR})

    def test_a_single_zip_listed_as_a_disc_set_is_both(self):
        detail = _single_zip(files=_listed(("Game.cue", 100), ("Game.bin", 9000)))

        assert served_shapes(detail) == frozenset({FILE, DIR})

    def test_a_single_zip_listed_as_a_cartridge_is_a_file(self):
        assert served_shapes(_single_zip(files=_listed(("Game.gba", 9000)))) == frozenset({FILE})

    def test_a_nested_single_zip_is_read_by_its_file_name(self):
        # RomM names the folder in fs_name for a nested single file; the file's own
        # name is what arrives on disk.
        detail = {
            "fs_name": "Game",
            "has_nested_single_file": True,
            "files": [{"file_name": "Game.zip", "archive_members": [{"name": "Game.rvz", "size": 1}]}],
        }

        assert served_shapes(detail) == frozenset({FILE, DIR})

    def test_a_zip_listed_with_its_disc_only_in_a_subfolder_is_a_file(self):
        detail = _single_zip(files=_listed(("GAME.EXE", 100), ("cd/game.cue", 80), ("cd/game.bin", 9000)))

        assert served_shapes(detail) == frozenset({FILE})


class TestListedUnpackedBytes:
    def test_a_listed_disc_set_counts_its_members(self):
        detail = _single_zip(files=_listed(("Game.cue", 100), ("Game.bin", 9000)))

        assert listed_unpacked_bytes(detail) == 9100

    def test_a_listed_cartridge_counts_nothing(self):
        assert listed_unpacked_bytes(_single_zip(files=_listed(("Game.gba", 9000)))) == 0

    def test_no_listing_counts_nothing(self):
        assert listed_unpacked_bytes(_single_zip(files=[{"file_name": "Game.zip"}])) == 0

    def test_a_multi_file_rom_counts_nothing_here(self):
        detail = {"fs_name": "Game.zip", "files": [{"file_name": "a.cue"}, {"file_name": "a.bin"}]}

        assert listed_unpacked_bytes(detail) == 0

    def test_a_disc_only_in_a_subfolder_counts_nothing(self):
        detail = _single_zip(files=_listed(("GAME.EXE", 100), ("cd/game.cue", 80), ("cd/game.bin", 9000)))

        assert listed_unpacked_bytes(detail) == 0


_ZIP = "/roms/saturn/Game.zip"
_FOLDER = "/roms/saturn/Game"


def _covers(
    occupied: list[str],
    *,
    replace: bool = True,
    resume: bool = False,
    folder_path: str | None = _FOLDER,
    confirmed: list[str] | None = None,
) -> frozenset[str]:
    return replace_covers(occupied, replace=replace, resume=resume, folder_path=folder_path, confirmed_paths=confirmed)


class TestReplaceCovers:
    """Which occupied paths a replace answer may remove."""

    def test_no_replace_covers_nothing(self):
        assert _covers([_ZIP], replace=False, confirmed=[_ZIP]) == frozenset()

    def test_one_claimed_path_is_covered_by_replace_alone(self):
        assert _covers([_ZIP], folder_path=None) == frozenset({_ZIP})

    def test_two_claimed_paths_cover_only_what_was_confirmed(self):
        assert _covers([_ZIP, _FOLDER], confirmed=[_ZIP]) == frozenset({_ZIP})

    def test_both_confirmed_cover_both(self):
        assert _covers([_ZIP, _FOLDER], confirmed=[_FOLDER, _ZIP]) == frozenset({_ZIP, _FOLDER})

    def test_an_answer_naming_no_path_covers_the_one_occupied(self):
        assert _covers([_FOLDER]) == frozenset({_FOLDER})

    def test_an_answer_naming_no_path_covers_neither_of_two(self):
        assert _covers([_ZIP, _FOLDER]) == frozenset()

    def test_a_resume_covers_the_zip_the_start_confirmed(self):
        assert _covers([_ZIP], resume=True, confirmed=[_ZIP, _FOLDER]) == frozenset({_ZIP})

    def test_a_resume_never_covers_the_folder(self):
        assert _covers([_FOLDER], resume=True, confirmed=[_FOLDER]) == frozenset()

    @pytest.mark.parametrize("confirmed", [[_FOLDER], None], ids=["only the folder confirmed", "nothing named"])
    def test_a_resume_does_not_cover_a_zip_the_start_never_confirmed(self, confirmed):
        assert _covers([_ZIP], resume=True, confirmed=confirmed) == frozenset()


class TestUnpackedManifest:
    """What a folder unpacked from a single zip is held to (#910 D5)."""

    def test_each_listed_member_becomes_a_loose_file_at_its_own_path(self):
        manifest = server_manifest(
            {
                "files": [
                    {
                        "file_name": "Game.zip",
                        "md5_hash": "zipcontent",
                        "archive_members": [
                            {"name": "Game.cue", "size": 80, "md5_hash": "AA", "crc_hash": "11"},
                            {"name": "tracks/Game.bin", "size": 9000, "md5_hash": "bb", "crc_hash": "22"},
                        ],
                    }
                ]
            }
        )

        assert unpacked_manifest(manifest) == (
            ServerFile(name="Game.cue", size_bytes=80, algorithm="md5", digest="aa", rel_path="Game.cue", crc32="11"),
            ServerFile(
                name="Game.bin",
                size_bytes=9000,
                algorithm="md5",
                digest="bb",
                rel_path="tracks/Game.bin",
                crc32="22",
            ),
        )

    def test_a_zip_without_a_listing_holds_the_folder_to_nothing(self):
        manifest = server_manifest({"files": [{"file_name": "Game.zip", "md5_hash": "zipcontent"}]})

        assert unpacked_manifest(manifest) == ()

    def test_a_wrapper_folder_is_left_out_of_every_path(self):
        manifest = server_manifest(
            {
                "files": [
                    {
                        "file_name": "Game.zip",
                        "archive_members": [
                            {"name": "Game/", "size": 0},
                            {"name": "Game/Game.cue", "size": 80, "md5_hash": "aa"},
                            {"name": "Game/tracks/Game.bin", "size": 9000, "md5_hash": "bb"},
                        ],
                    }
                ]
            }
        )

        assert [(f.name, f.rel_path) for f in unpacked_manifest(manifest)] == [
            ("Game.cue", "Game.cue"),
            ("Game.bin", "tracks/Game.bin"),
        ]
