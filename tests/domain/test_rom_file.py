"""Tests for domain.rom_file — whether RomM holds a file for a ROM it lists."""

from __future__ import annotations

import pytest

from domain.rom_file import NoFileOnRomm, has_file_on_romm


class TestHasFileOnRomm:
    def test_a_rom_whose_file_left_the_disk_has_none(self):
        assert has_file_on_romm({"has_file_on_disk": False, "missing_from_fs": True}) is False

    def test_a_physical_rom_has_none(self):
        assert has_file_on_romm({"has_file_on_disk": False, "is_physical": True, "missing_from_fs": False}) is False

    def test_a_rom_with_a_file_has_one(self):
        assert has_file_on_romm({"has_file_on_disk": True}) is True

    def test_missing_from_fs_is_not_read_on_its_own(self):
        assert has_file_on_romm({"has_file_on_disk": True, "missing_from_fs": True}) is True

    @pytest.mark.parametrize("payload", [{}, {"has_file_on_disk": None}])
    def test_an_absent_answer_reads_as_having_a_file(self, payload):
        assert has_file_on_romm(payload) is True


def test_the_refusal_carries_its_reason():
    refusal = NoFileOnRomm("No file on RomM")
    assert refusal.reason == "no_file_on_romm"
    assert refusal.message == "No file on RomM"
