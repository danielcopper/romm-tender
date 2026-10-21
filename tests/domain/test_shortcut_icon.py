"""Tests for domain/shortcut_icon.py.

When a shortcut's icon slot is filled, and the two icons that are Tender's own.
"""

from __future__ import annotations

import pytest
from _vendor import png

from domain.shortcut_icon import (
    LOGO_ICON_NAME,
    PLACEHOLDER_ICON_NAME,
    PLACEHOLDER_ICON_PNG,
    is_icon_filled,
    is_icon_set_in_steam,
)

_GRID = "/home/deck/.steam/steam/userdata/111/config/grid"
_OTHER_GRID = "/home/deck/.steam/steam/userdata/222/config/grid"
_PLACEHOLDER = f"{_GRID}/{PLACEHOLDER_ICON_NAME}"
_LOGO = f"{_GRID}/{LOGO_ICON_NAME}"


class TestThePlaceholder:
    def test_is_one_fully_transparent_pixel(self):
        width, height, rows, _info = png.Reader(bytes=PLACEHOLDER_ICON_PNG).asRGBA8()
        assert (width, height) == (1, 1)
        assert [list(row) for row in rows] == [[0, 0, 0, 0]]

    def test_is_68_bytes(self):
        assert len(PLACEHOLDER_ICON_PNG) == 68


class TestWhetherTheIconFillsItsSlot:
    @pytest.mark.parametrize("icon", ["", _PLACEHOLDER, _LOGO], ids=["empty", "placeholder", "logo-icon"])
    def test_an_empty_path_the_placeholder_and_the_logo_icon_are_stand_ins(self, icon):
        assert not is_icon_filled(icon, _GRID, frozenset({PLACEHOLDER_ICON_NAME, LOGO_ICON_NAME}))

    @pytest.mark.parametrize("name", [PLACEHOLDER_ICON_NAME, LOGO_ICON_NAME])
    def test_a_stand_in_in_another_steam_accounts_grid_directory_is_one_too(self, name):
        assert not is_icon_filled(f"/home/deck/.steam/steam/userdata/222/config/grid/{name}", _GRID, frozenset())

    @pytest.mark.parametrize("name", [f"my-{PLACEHOLDER_ICON_NAME}", f"my-{LOGO_ICON_NAME}"])
    def test_a_file_whose_name_only_ends_like_a_stand_in_fills_it(self, name):
        assert is_icon_filled(f"/home/deck/{name}", _GRID, frozenset())

    def test_a_file_in_the_grid_directory_fills_it_while_it_is_there(self):
        assert is_icon_filled(f"{_GRID}/100_icon.png", _GRID, frozenset({"100_icon.png"}))

    def test_a_file_in_the_grid_directory_that_is_gone_leaves_it_empty(self):
        assert not is_icon_filled(f"{_GRID}/100_icon.png", _GRID, frozenset({"100.png"}))

    def test_the_grid_directory_is_matched_by_its_path_not_its_spelling(self):
        assert not is_icon_filled(f"{_GRID}//./100_icon.png", f"{_GRID}/", frozenset())

    def test_a_file_in_the_grid_directory_is_left_alone_where_the_directory_could_not_be_listed(self):
        assert is_icon_filled(f"{_GRID}/100_icon.png", _GRID, None)

    def test_a_stand_in_still_waits_where_the_directory_could_not_be_listed(self):
        assert not is_icon_filled(_PLACEHOLDER, _GRID, None)

    @pytest.mark.parametrize(
        "icon",
        ["/home/deck/Downloads/mine.png", "/run/media/sd/icons/game.ico", f"{_OTHER_GRID}/100_icon.png"],
        ids=["by-hand", "unmounted-drive", "another-steam-accounts-grid"],
    )
    def test_any_other_path_fills_it_whether_its_file_is_there_or_not(self, icon):
        assert is_icon_filled(icon, _GRID, frozenset())


class TestWhetherTheIconIsSetInSteam:
    _APP_ID = 3345678901

    @pytest.mark.parametrize(
        "icon",
        [
            "/home/deck/Pictures/mine.png",
            f"{_GRID}/{_APP_ID}_icon.jpg",
            f"{_GRID}/4000000000_icon.png",
            f"/home/deck/.steam/steam/userdata/222/config/grid/{_APP_ID}_icon.png",
        ],
        ids=["elsewhere", "another-extension-in-the-grid", "another-shortcuts-icon", "another-accounts-grid"],
    )
    def test_any_path_but_tenders_files(self, icon):
        assert is_icon_set_in_steam(icon, self._APP_ID, _GRID)

    @pytest.mark.parametrize(
        "icon",
        [
            "",
            f"{_GRID}/{PLACEHOLDER_ICON_NAME}",
            f"{_GRID}/{LOGO_ICON_NAME}",
            f"/elsewhere/{LOGO_ICON_NAME}",
            f"{_GRID}/{_APP_ID}_icon.png",
            f"{_GRID}/./{_APP_ID}_icon.png",
        ],
        ids=["empty", "placeholder", "logo-icon", "logo-icon-elsewhere", "tenders-icon", "tenders-icon-unnormalised"],
    )
    def test_not_tenders_own_icons(self, icon):
        assert not is_icon_set_in_steam(icon, self._APP_ID, _GRID)
