"""Tests for domain.main_sibling — the main-version flag a RomM answer states."""

from __future__ import annotations

import pytest

from domain.main_sibling import main_sibling_flag


class TestMainSiblingFlag:
    @pytest.mark.parametrize("flag", [True, False])
    def test_a_rom_user_states_its_flag(self, flag):
        assert main_sibling_flag({"id": 7, "is_main_sibling": flag}) is flag

    @pytest.mark.parametrize("flag", [True, False])
    def test_a_sibling_stub_states_its_flag(self, flag):
        assert main_sibling_flag({"id": 5, "fs_name_no_ext": "Game (Japan)", "is_main_sibling": flag}) is flag

    @pytest.mark.parametrize("entry", [None, [], "rom_user"])
    def test_an_entry_that_is_no_mapping_states_nothing(self, entry):
        assert main_sibling_flag(entry) is None

    def test_an_entry_without_the_key_states_nothing(self):
        assert main_sibling_flag({"id": 5}) is None

    @pytest.mark.parametrize("value", [None, 1, 0, "true"])
    def test_a_value_that_is_no_boolean_states_nothing(self, value):
        assert main_sibling_flag({"is_main_sibling": value}) is None
