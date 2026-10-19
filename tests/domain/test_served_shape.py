"""Tests for ``domain/served_shape`` — which single zips are unpacked, and what a download becomes on disk."""

from __future__ import annotations

from typing import Any

import pytest

from domain.rom_candidates import DIR, FILE
from domain.served_shape import holds_disc_image, is_zip_name, listed_unpacked_bytes, served_shapes


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
            ["Game.m3u"],
            ["nested/Game.cue", "nested/Game.bin"],
        ],
        ids=["cue set", "gdi set", "rvz", "wbfs", "chd", "isos", "img with ccd", "m3u and chd", "m3u", "nested cue"],
    )
    def test_a_disc_set_or_a_playlist_is_unpacked(self, names):
        assert holds_disc_image(names) is True

    @pytest.mark.parametrize(
        "names",
        [
            ["Game.gba"],
            ["mslug.rom", "mslug.c1", "mslug.p1"],
            ["Game.bin"],
            ["Game.img"],
            ["Game.img", "Game.sub"],
            [],
        ],
        ids=["cartridge", "arcade romset", "bin alone", "img alone", "img without ccd", "empty"],
    )
    def test_anything_else_is_kept(self, names):
        assert holds_disc_image(names) is False

    def test_an_upper_case_extension_counts(self):
        assert holds_disc_image(["GAME.CUE", "GAME.BIN"]) is True


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
