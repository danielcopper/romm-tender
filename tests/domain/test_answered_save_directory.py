"""Unit tests for the ``AnsweredSaveDirectory`` aggregate."""

from __future__ import annotations

import pytest

from domain.answered_save_directory import AnsweredSaveDirectory


class TestRecord:
    def test_carries_the_rom_and_the_directory(self):
        record = AnsweredSaveDirectory.record(rom_id=7, directory="/saves/gba/mGBA")

        assert record == AnsweredSaveDirectory(rom_id=7, directory="/saves/gba/mGBA")

    def test_an_empty_directory_is_refused(self):
        with pytest.raises(ValueError, match="directory is required"):
            AnsweredSaveDirectory.record(rom_id=7, directory="")
