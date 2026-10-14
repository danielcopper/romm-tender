"""Tests for domain/shortcut_icon.py.

Which shortcuts wait for an icon from the artwork job, and the two icons that are Tender's own.
"""

from __future__ import annotations

from _vendor import png

from domain.shortcut_icon import LOGO_ICON_NAME, PLACEHOLDER_ICON_NAME, PLACEHOLDER_ICON_PNG, icon_worklist

_PLACEHOLDER = f"/grid/{PLACEHOLDER_ICON_NAME}"
_LOGO = f"/grid/{LOGO_ICON_NAME}"


class TestThePlaceholder:
    def test_is_one_fully_transparent_pixel(self):
        width, height, rows, _info = png.Reader(bytes=PLACEHOLDER_ICON_PNG).asRGBA8()
        assert (width, height) == (1, 1)
        assert [list(row) for row in rows] == [[0, 0, 0, 0]]

    def test_is_68_bytes(self):
        assert len(PLACEHOLDER_ICON_PNG) == 68


class TestIconWorklist:
    def test_takes_a_shortcut_with_no_icon_and_one_with_the_placeholder(self):
        bound = {100: 1, 200: 2}
        icons = {100: "", 200: _PLACEHOLDER}
        assert icon_worklist(bound, icons) == [(1, 100), (2, 200)]

    def test_takes_the_placeholder_another_steam_accounts_grid_directory_holds(self):
        icons = {100: f"/home/deck/.steam/steam/userdata/222/config/grid/{PLACEHOLDER_ICON_NAME}"}
        assert icon_worklist({100: 1}, icons) == [(1, 100)]

    def test_leaves_a_file_whose_name_only_ends_like_the_placeholder(self):
        assert icon_worklist({100: 1}, {100: f"/home/deck/my-{PLACEHOLDER_ICON_NAME}"}) == []

    def test_leaves_the_logo_and_any_icon_set_by_hand(self):
        bound = {100: 1, 200: 2}
        icons = {100: _LOGO, 200: "/home/deck/Downloads/mine.png"}
        assert icon_worklist(bound, icons) == []

    def test_leaves_a_shortcut_the_file_does_not_hold_yet(self):
        assert icon_worklist({100: 1}, {}) == []

    def test_leaves_a_shortcut_tender_does_not_manage(self):
        assert icon_worklist({}, {100: ""}) == []

    def test_answers_in_rom_order(self):
        bound = {300: 9, 100: 4, 200: 6}
        icons = dict.fromkeys(bound, "")
        assert icon_worklist(bound, icons) == [(4, 100), (6, 200), (9, 300)]
