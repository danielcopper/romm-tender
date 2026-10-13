"""Tests for domain.artwork_slot — when Steam's grid directory already fills a slot."""

from __future__ import annotations

import pytest

from domain.artwork_slot import ArtworkSlot, is_slot_filled

APP_ID = 3345678901
# The 64-bit game id other tools name a shortcut's files by: the app id in the
# upper half, 0x02000000 in the lower.
GAME_ID_64 = (APP_ID << 32) | 0x02000000


class TestAFileFillsItsSlot:
    @pytest.mark.parametrize(
        ("name", "slot"),
        [
            (f"{APP_ID}.png", ArtworkSlot.WIDE_GRID),
            (f"{APP_ID}_logo.png", ArtworkSlot.LOGO),
            (f"{APP_ID}_hero.png", ArtworkSlot.HERO),
        ],
    )
    def test_the_slots_name_plus_png(self, name, slot):
        assert is_slot_filled([name], APP_ID, slot)

    @pytest.mark.parametrize("extension", ["jpg", "jpeg", "webp", "PNG", "ico"])
    def test_any_one_extension_counts(self, extension):
        assert is_slot_filled([f"{APP_ID}_logo.{extension}"], APP_ID, ArtworkSlot.LOGO)

    def test_among_other_files(self):
        names = ["other.png", f"{APP_ID}p.png", f"{APP_ID}_hero.jpg"]

        assert is_slot_filled(names, APP_ID, ArtworkSlot.HERO)


class TestNothingElseFillsIt:
    def test_an_empty_directory(self):
        assert not is_slot_filled([], APP_ID, ArtworkSlot.WIDE_GRID)

    @pytest.mark.parametrize("extension", ["json", "JSON"])
    def test_the_logos_position_file(self, extension):
        assert not is_slot_filled([f"{APP_ID}_logo.{extension}"], APP_ID, ArtworkSlot.LOGO)

    def test_the_cover_is_not_the_wide_grid(self):
        assert not is_slot_filled([f"{APP_ID}p.png", f"{APP_ID}p.jpg"], APP_ID, ArtworkSlot.WIDE_GRID)

    @pytest.mark.parametrize("slot", list(ArtworkSlot))
    def test_a_file_named_by_the_64_bit_game_id(self, slot):
        stem = str(GAME_ID_64) if slot is ArtworkSlot.WIDE_GRID else f"{GAME_ID_64}_{slot.value}"

        assert not is_slot_filled([f"{stem}.png"], APP_ID, slot)

    def test_the_name_without_an_extension(self):
        assert not is_slot_filled([str(APP_ID), f"{APP_ID}."], APP_ID, ArtworkSlot.WIDE_GRID)

    def test_the_name_with_two_extensions(self):
        assert not is_slot_filled([f"{APP_ID}_hero.png.tmp"], APP_ID, ArtworkSlot.HERO)

    def test_another_slots_file(self):
        names = [f"{APP_ID}_logo.png", f"{APP_ID}_hero.png", f"{APP_ID}_icon.png"]

        assert not is_slot_filled(names, APP_ID, ArtworkSlot.WIDE_GRID)

    def test_another_shortcuts_file(self):
        assert not is_slot_filled([f"{APP_ID}1.png", f"1{APP_ID}_logo.png"], APP_ID, ArtworkSlot.WIDE_GRID)
        assert not is_slot_filled([f"1{APP_ID}_logo.png"], APP_ID, ArtworkSlot.LOGO)
