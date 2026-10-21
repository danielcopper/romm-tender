"""Tests for domain/tender_folder.py — the stored place, typed places, the ROM-folder rule and reachability."""

from __future__ import annotations

import pytest

from domain.refusal import DomainRefused
from domain.tender_folder import (
    DRIVE_NOT_MOUNTED,
    FOLDER_MISSING,
    FOLDER_NOT_WRITABLE,
    SourceRomFolders,
    TenderPlace,
    current_folder_refusal,
    drive_below,
    place_asks,
    place_drive,
    place_setting,
    read_place,
    recorded_elsewhere,
    rom_folder_refusal,
    shown_folder,
    shown_place,
    tender_parent,
    typed_place,
    unreachable_message,
    unreachable_state,
)

_HOME = "/home/deck"


class TestTheStoredPlace:
    def test_no_choice_is_the_default_inside_the_home_folder(self):
        place = read_place(None, _HOME)

        assert place == TenderPlace(place=_HOME, mount_point=None, default=True)
        assert place.folder == "/home/deck/Tender"

    def test_a_choice_holds_its_place_and_the_mount_point_it_lay_on(self):
        place = read_place({"place": "/run/media/deck/SD", "mount_point": "/run/media/deck/SD"}, _HOME)

        assert place == TenderPlace(place="/run/media/deck/SD", mount_point="/run/media/deck/SD", default=False)
        assert place.folder == "/run/media/deck/SD/Tender"

    @pytest.mark.parametrize(
        "value",
        [
            "/run/media/deck/SD",
            {"place": "/run/media/deck/SD"},
            {"place": "relative", "mount_point": "/"},
            {"place": "/x", "mount_point": "relative"},
            {"place": 3, "mount_point": "/"},
            [],
        ],
    )
    def test_a_value_a_hand_edit_broke_is_the_default(self, value):
        assert read_place(value, _HOME).default is True

    def test_the_default_place_is_stored_as_no_choice_so_a_reset_restores_it(self):
        assert place_setting(_HOME, "/home", _HOME) is None
        assert place_setting("/home/deck/", "/home", _HOME) is None

    def test_any_other_place_is_stored_with_its_mount_point(self):
        assert place_setting("/run/media/deck/SD/", "/run/media/deck/SD", _HOME) == {
            "place": "/run/media/deck/SD",
            "mount_point": "/run/media/deck/SD",
        }


class TestATypedPlace:
    @pytest.mark.parametrize(
        ("text", "place"),
        [
            ("~", "/home/deck"),
            ("~/Games", "/home/deck/Games"),
            ("  ~/Games/  ", "/home/deck/Games"),
            ("~//Tender", "/home/deck/Tender"),
            ("~///Games//x", "/home/deck/Games/x"),
            ("/mnt/games/../library", "/mnt/library"),
            ("/", "/"),
        ],
    )
    def test_tilde_lies_under_the_home_folder_and_any_other_path_starts_with_a_slash(self, text, place):
        assert typed_place(text, _HOME) == place

    @pytest.mark.parametrize(
        ("text", "place", "folder"),
        [("~/Games", "~/Games", "~/Games/Tender"), (" /mnt/x/ ", "/mnt/x", "/mnt/x/Tender"), ("/", "/", "/Tender")],
    )
    def test_a_sentence_names_the_place_as_typed_and_tenders_folder_with_tender_after_it(self, text, place, folder):
        assert shown_place(text) == place
        assert shown_folder(text) == folder

    @pytest.mark.parametrize("text", ["", "   ", "Games", "~deck/Games", "./Games"])
    def test_anything_else_is_refused(self, text):
        with pytest.raises(DomainRefused) as refused:
            typed_place(text, _HOME)

        assert refused.value.reason == "place_not_absolute"
        assert refused.value.message == "Enter a full path starting with / or ~."

    @pytest.mark.parametrize("last", ["Tender", "tender", "TENDER"])
    def test_a_place_whose_last_folder_is_named_tender_in_any_case_offers_its_parent(self, last):
        assert tender_parent(f"/run/media/deck/SD/{last}") == "/run/media/deck/SD"

    def test_any_other_place_offers_nothing_instead(self):
        assert tender_parent("/run/media/deck/SD/Tenders") is None

    def test_the_part_still_missing_below_the_deepest_existing_folder_is_asked_about(self):
        asks = place_asks("/home/deck/Games/Library", "/home/deck")

        assert asks.existing == "/home/deck"
        assert asks.missing == "Games/Library"
        assert asks.tender_parent is None

    def test_a_place_that_exists_asks_nothing_about_folders(self):
        assert place_asks("/home/deck/Games", "/home/deck/Games").missing is None

    def test_the_drive_a_place_lies_on_is_the_folder_directly_under_the_drives_root(self):
        assert drive_below("/run/media/deck/SD/Games/x", "/run/media/deck") == "SD"
        assert drive_below("/run/media/deck/SD", "/run/media/deck") == "SD"

    def test_a_place_outside_the_drives_root_or_the_root_itself_lies_on_no_drive(self):
        assert drive_below("/home/deck/Games", "/run/media/deck") is None
        assert drive_below("/run/media/deck", "/run/media/deck") is None
        assert drive_below("/run/media/deckard/SD", "/run/media/deck") is None


_RETRODECK = SourceRomFolders(
    kind="retrodeck",
    rom_root="/run/media/deck/SD/retrodeck/roms",
    system_folders=(("psx", "/run/media/deck/SD/retrodeck/roms/psx"), ("ps2", "/run/media/deck/BIG/ps2")),
)


class TestTheRomFolderRule:
    def test_a_folder_inside_a_sources_rom_folder_is_refused_naming_it(self):
        refusal = rom_folder_refusal("/run/media/deck/SD/retrodeck/roms/Tender", (_RETRODECK,), "~/sd-roms/Tender")

        assert refusal is not None
        assert refusal.reason == "place_in_rom_folder"
        assert refusal.details == {
            "path": "~/sd-roms/Tender",
            "source": "retrodeck",
            "folder": "/run/media/deck/SD/retrodeck/roms",
            "system": None,
        }
        assert refusal.message == (
            "~/sd-roms/Tender lies inside retrodeck's ROM folder (/run/media/deck/SD/retrodeck/roms). "
            "Choose a place outside it."
        )

    def test_a_folder_that_is_the_rom_folder_is_refused(self):
        refusal = rom_folder_refusal("/run/media/deck/SD/retrodeck/roms", (_RETRODECK,), "/x/Tender")

        assert refusal is not None
        assert refusal.reason == "place_in_rom_folder"

    def test_a_folder_above_a_sources_rom_folder_is_refused(self):
        source = SourceRomFolders(kind="emudeck", rom_root="/home/deck/Tender/roms", system_folders=())

        refusal = rom_folder_refusal("/home/deck/Tender", (source,), "~/Tender")

        assert refusal is not None
        assert refusal.reason == "place_above_rom_folder"
        assert (
            refusal.message
            == "emudeck's ROM folder (/home/deck/Tender/roms) lies inside ~/Tender. Choose another place."
        )
        assert refusal.details["folder"] == "/home/deck/Tender/roms"

    def test_a_folder_inside_a_system_folder_linked_to_another_drive_is_refused_naming_the_system(self):
        refusal = rom_folder_refusal("/run/media/deck/BIG/ps2/Tender", (_RETRODECK,), "/big-link/Tender")

        assert refusal is not None
        assert refusal.reason == "place_in_rom_folder"
        assert refusal.details["system"] == "ps2"
        assert refusal.details["folder"] == "/run/media/deck/BIG/ps2"
        assert refusal.details["path"] == "/big-link/Tender"

    def test_a_folder_at_the_root_of_the_drive_the_rom_folder_lies_on_is_neither(self):
        assert rom_folder_refusal("/run/media/deck/SD/Tender", (_RETRODECK,), "/run/media/deck/SD/Tender") is None

    def test_a_sibling_whose_name_only_starts_like_the_rom_folder_is_neither(self):
        assert rom_folder_refusal("/run/media/deck/SD/retrodeck/roms2/Tender", (_RETRODECK,), "/x/Tender") is None

    def test_a_source_with_no_rom_folder_judges_by_its_system_folders_alone(self):
        source = SourceRomFolders(kind="bare_retroarch_native", rom_root=None, system_folders=())

        assert rom_folder_refusal("/anywhere/Tender", (source,), "/anywhere/Tender") is None


class TestReachability:
    def test_with_something_recorded_under_the_folder_its_condition_is_the_reason(self):
        for condition in (DRIVE_NOT_MOUNTED, FOLDER_MISSING, FOLDER_NOT_WRITABLE):
            assert unreachable_state("/sd/Tender", {"/sd/Tender"}, condition) == condition

    def test_with_nothing_recorded_under_the_folder_it_is_never_unreachable(self):
        assert unreachable_state("/sd/Tender", set(), DRIVE_NOT_MOUNTED) is None
        assert unreachable_state("/sd/Tender", {"/home/deck/Tender"}, FOLDER_MISSING) is None

    def test_a_folder_that_can_be_used_is_reachable_whatever_is_recorded(self):
        assert unreachable_state("/sd/Tender", {"/sd/Tender"}, None) is None

    def test_the_other_folders_something_is_recorded_under_are_listed_sorted(self):
        recorded = {"/sd/Tender", "/home/deck/Tender", "/big/Tender"}

        assert recorded_elsewhere("/home/deck/Tender", recorded) == ("/big/Tender", "/sd/Tender")

    @pytest.mark.parametrize(
        ("state", "ending"),
        [
            (DRIVE_NOT_MOUNTED, "the drive is not connected."),
            (FOLDER_MISSING, "the folder is missing."),
            (FOLDER_NOT_WRITABLE, "the folder cannot be written to."),
        ],
    )
    def test_an_action_refused_names_the_folder_and_the_reason(self, state, ending):
        assert unreachable_message("/sd/Tender", state) == f"Tender's folder at /sd/Tender cannot be reached: {ending}"


class TestTheCurrentFolder:
    def test_the_current_folder_itself_is_already_tenders_folder(self):
        refusal = current_folder_refusal("/home/deck/Tender", "/home/deck/Tender", "~/Tender", "/home/deck/Tender")

        assert refusal is not None
        assert refusal.reason == "place_is_current_folder"
        assert refusal.message == "/home/deck/Tender is already Tender's folder."

    def test_a_folder_inside_the_current_folder_is_refused(self):
        refusal = current_folder_refusal(
            "/home/deck/Tender/Tender", "/home/deck/Tender", "~/Tender/Tender", "/home/deck/Tender"
        )

        assert refusal is not None
        assert refusal.reason == "place_in_current_folder"
        assert refusal.message == (
            "~/Tender/Tender lies inside Tender's current folder (/home/deck/Tender). Choose a place outside it."
        )

    def test_a_folder_holding_the_current_folder_is_refused(self):
        refusal = current_folder_refusal("/sd/Tender", "/sd/Tender/x/Tender", "/sd/Tender", "/sd/Tender/x/Tender")

        assert refusal is not None
        assert refusal.reason == "place_above_current_folder"
        assert (
            refusal.message
            == "Tender's current folder (/sd/Tender/x/Tender) lies inside /sd/Tender. Choose another place."
        )

    def test_a_folder_beside_the_current_folder_is_neither(self):
        assert current_folder_refusal("/sd/Tender", "/home/deck/Tender", "/sd/Tender", "/home/deck/Tender") is None
        assert current_folder_refusal("/home/deck/Tenderly/Tender", "/home/deck/Tender", "x", "y") is None


class TestTheDriveAPlaceLiesOn:
    def test_a_choice_keeps_the_kind_and_name_of_its_drive(self):
        stored = place_setting("/run/media/deck/SD", "/run/media/deck/SD", _HOME, ("sd_card", "Emulation"))

        assert stored == {
            "place": "/run/media/deck/SD",
            "mount_point": "/run/media/deck/SD",
            "drive_kind": "sd_card",
            "drive_name": "Emulation",
        }
        place = read_place(stored, _HOME)
        assert (place.drive_kind, place.drive_name) == ("sd_card", "Emulation")
        assert place_drive(place, "/run/media/deck") == ("sd_card", "Emulation")

    def test_a_choice_stored_without_them_on_a_drive_is_a_drive_named_after_its_mount(self):
        place = read_place({"place": "/run/media/deck/SD/Games", "mount_point": "/run/media/deck/SD"}, _HOME)

        assert place_drive(place, "/run/media/deck") == ("drive", "SD")

    @pytest.mark.parametrize("value", [{"drive_kind": "floppy", "drive_name": "A"}, {"drive_kind": "sd_card"}])
    def test_a_kind_or_name_a_hand_edit_broke_is_not_read(self, value):
        place = read_place({"place": "/mnt/x", "mount_point": "/mnt", **value}, _HOME)

        assert (place.drive_kind, place.drive_name) == (None, None)

    def test_a_place_on_no_drive_has_none(self):
        assert (
            place_drive(read_place({"place": "/home/deck/Games", "mount_point": "/home"}, _HOME), "/run/media/deck")
            is None
        )
        assert place_drive(read_place(None, _HOME), "/run/media/deck") is None
