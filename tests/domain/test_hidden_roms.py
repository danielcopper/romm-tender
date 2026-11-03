"""Tests for domain.hidden_roms — reading RomM's answer to "which ROMs did this user hide"."""

import pytest

from domain.hidden_roms import hidden_rom_ids


class TestHiddenRomIds:
    def test_reads_every_id_of_the_index(self):
        assert hidden_rom_ids({"items": [{"id": 4}], "total": 2, "rom_id_index": [4, 9]}) == frozenset({4, 9})

    def test_an_empty_index_is_no_rom_hidden(self):
        assert hidden_rom_ids({"items": [], "total": 0, "rom_id_index": []}) == frozenset()

    @pytest.mark.parametrize(
        "answer",
        [
            None,
            [],
            "hidden",
            {},
            {"items": [], "total": 0},
            {"rom_id_index": None},
            {"rom_id_index": "4,9"},
            {"rom_id_index": [4, "9"]},
            {"rom_id_index": [4, True]},
            {"rom_id_index": [4, 9.0]},
        ],
        ids=[
            "none",
            "a-list",
            "a-string",
            "an-empty-mapping",
            "no-index",
            "a-null-index",
            "an-index-that-is-no-list",
            "a-string-id",
            "a-bool-id",
            "a-float-id",
        ],
    )
    def test_an_answer_it_cannot_read_establishes_nothing(self, answer):
        assert hidden_rom_ids(answer) is None
