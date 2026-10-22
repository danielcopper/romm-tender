"""Tests for domain.artwork_slot — when Steam's grid directory already fills a slot."""

from __future__ import annotations

import pytest

from domain.artwork_slot import ArtworkSlot, is_set_in_steam, is_slot_filled

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


_GRID_FOLDER_SLOTS = [ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO, ArtworkSlot.HERO, ArtworkSlot.COVER]


def _stem(slot: ArtworkSlot) -> str:
    return {
        ArtworkSlot.WIDE_GRID: str(APP_ID),
        ArtworkSlot.LOGO: f"{APP_ID}_logo",
        ArtworkSlot.HERO: f"{APP_ID}_hero",
        ArtworkSlot.COVER: f"{APP_ID}p",
    }[slot]


class TestASlotSetInSteam:
    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    @pytest.mark.parametrize("extension", ["jpg", "jpeg", "webp", "PNG"])
    def test_a_file_under_another_extension_beside_tenders_png(self, slot, extension):
        names = [f"{_stem(slot)}.png", f"{_stem(slot)}.{extension}"]

        assert is_set_in_steam(names, APP_ID, slot, png_sha256="tender", tenders_sha256="tender")

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_file_under_another_extension_alone(self, slot):
        assert is_set_in_steam([f"{_stem(slot)}.jpg"], APP_ID, slot, png_sha256=None, tenders_sha256=None)

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_png_whose_hash_is_not_the_one_tender_set(self, slot):
        assert is_set_in_steam([f"{_stem(slot)}.png"], APP_ID, slot, png_sha256="theirs", tenders_sha256="tender")


class TestASlotThatIsTenders:
    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_png_with_the_hash_tender_set(self, slot):
        assert not is_set_in_steam([f"{_stem(slot)}.png"], APP_ID, slot, png_sha256="tender", tenders_sha256="tender")

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_png_with_nothing_to_compare_it_with(self, slot):
        assert not is_set_in_steam([f"{_stem(slot)}.png"], APP_ID, slot, png_sha256="theirs", tenders_sha256=None)

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_an_empty_slot(self, slot):
        assert not is_set_in_steam([], APP_ID, slot, png_sha256=None, tenders_sha256="tender")

    def test_the_logos_position_file_and_other_slots_files(self):
        names = [f"{APP_ID}_logo.json", f"{APP_ID}p.jpg", f"{APP_ID}_hero.jpg", f"{APP_ID}.jpg"]

        assert not is_set_in_steam(names, APP_ID, ArtworkSlot.LOGO, png_sha256=None, tenders_sha256=None)

    def test_the_icon_is_judged_by_its_path(self):
        with pytest.raises(ValueError, match="path"):
            is_set_in_steam([], APP_ID, ArtworkSlot.ICON, png_sha256=None, tenders_sha256=None)


class TestTheImageHandedToSteam:
    """D15: the image Tender handed over and the one Steam confirmed are both Tender's."""

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_png_with_the_handed_over_hash_is_tenders(self, slot):
        names = [f"{_stem(slot)}.png"]

        assert not is_set_in_steam(
            names, APP_ID, slot, png_sha256="handed", tenders_sha256="set", handed_sha256="handed"
        )

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_png_with_the_confirmed_hash_stays_tenders_beside_a_hand_over(self, slot):
        names = [f"{_stem(slot)}.png"]

        assert not is_set_in_steam(names, APP_ID, slot, png_sha256="set", tenders_sha256="set", handed_sha256="handed")

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_png_that_is_neither_is_set_in_steam(self, slot):
        names = [f"{_stem(slot)}.png"]

        assert is_set_in_steam(names, APP_ID, slot, png_sha256="theirs", tenders_sha256="set", handed_sha256="handed")

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_a_hand_over_alone_is_compared(self, slot):
        names = [f"{_stem(slot)}.png"]

        assert is_set_in_steam(names, APP_ID, slot, png_sha256="theirs", tenders_sha256=None, handed_sha256="handed")

    @pytest.mark.parametrize("slot", _GRID_FOLDER_SLOTS)
    def test_another_extension_is_set_in_steam_whatever_was_handed(self, slot):
        names = [f"{_stem(slot)}.png", f"{_stem(slot)}.jpg"]

        assert is_set_in_steam(names, APP_ID, slot, png_sha256="handed", tenders_sha256=None, handed_sha256="handed")
