"""Which of a ROM's files a download fetches (``domain/game_files.py``, #2223 D27, D30)."""

from __future__ import annotations

from typing import Any

from domain.game_files import (
    MAX_REQUEST_LINE_BYTES,
    file_ids_query,
    game_files,
    request_line_fits,
)


def _file(file_id: int, path: str, category: str | None, size: int = 10) -> dict[str, Any]:
    folder, _, name = path.rpartition("/")
    return {
        "id": file_id,
        "file_name": name,
        "file_path": f"roms/gc/Game/{folder}".rstrip("/"),
        "file_size_bytes": size,
        "category": category,
    }


def _detail(*files: dict[str, Any]) -> dict[str, Any]:
    return {"full_path": "roms/gc/Game", "files": list(files)}


class TestWhatIsFetched:
    def test_a_soundtrack_stays_on_the_server(self):
        files = game_files(
            _detail(
                _file(1, "Game.rvz", "game", 900),
                _file(2, "soundtrack/01.flac", "soundtrack", 40),
                _file(3, "soundtrack/02.flac", "soundtrack", 40),
            )
        )

        assert [entry.file_id for entry in files.fetched] == [1]
        assert [entry.rel_path for entry in files.left_out] == ["soundtrack/01.flac", "soundtrack/02.flac"]

    def test_every_other_category_stays_on_the_server(self):
        others = [
            "dlc",
            "hack",
            "manual",
            "walkthrough",
            "patch",
            "update",
            "mod",
            "demo",
            "translation",
            "prototype",
            "cheat",
            "soundtrack",
            "screenshot",
        ]
        files = game_files(
            _detail(
                _file(1, "Game.iso", "game"),
                *(_file(10 + index, f"{name}/x.bin", name) for index, name in enumerate(others)),
            )
        )

        assert [entry.file_id for entry in files.fetched] == [1]
        assert sorted(entry.category or "" for entry in files.left_out) == sorted(others)

    def test_a_file_in_no_category_is_the_games_own(self):
        # A PS3 dump: nothing lies directly in the game's folder, so RomM files
        # every file under no category.
        files = game_files(
            _detail(
                _file(1, "PS3_GAME/USRDIR/EBOOT.BIN", None),
                _file(2, "PS3_GAME/PARAM.SFO", None),
                _file(3, "manual/manual.pdf", "manual"),
            )
        )

        assert [entry.file_id for entry in files.fetched] == [1, 2]
        assert [entry.file_id for entry in files.left_out] == [3]

    def test_where_nothing_is_the_games_every_file_is_fetched(self):
        files = game_files(_detail(_file(1, "manual/a.pdf", "manual"), _file(2, "soundtrack/b.flac", "soundtrack")))

        assert [entry.file_id for entry in files.fetched] == [1, 2]
        assert files.left_out == ()
        assert files.file_ids is None

    def test_the_fetched_size_is_the_sum_of_the_files_fetched(self):
        files = game_files(
            _detail(
                _file(1, "Game.cue", "game", 100), _file(2, "Game.bin", "game", 900), _file(3, "manual/m", "manual", 5)
            )
        )

        assert files.fetched_bytes == 1000

    def test_a_payload_without_files_fetches_nothing_named(self):
        files = game_files({"fs_name": "Game.gba"})

        assert files.fetched == ()
        assert files.fetched_bytes == 0
        assert files.file_ids is None


class TestTheIdsTheRequestNames:
    def test_none_where_nothing_is_left_out(self):
        files = game_files(_detail(_file(1, "Game.cue", "game"), _file(2, "Game.bin", "game")))

        assert files.file_ids is None

    def test_the_fetched_ids_where_something_is_left_out(self):
        files = game_files(_detail(_file(1, "Game.rvz", "game"), _file(2, "soundtrack/a.flac", "soundtrack")))

        assert files.file_ids == (1,)

    def test_none_where_a_fetched_file_has_no_id(self):
        entry = _file(1, "Game.rvz", "game")
        del entry["id"]
        files = game_files(_detail(entry, _file(2, "soundtrack/a.flac", "soundtrack")))

        assert files.file_ids is None
        assert [item.rel_path for item in files.left_out] == ["soundtrack/a.flac"]

    def test_the_query_lists_them_comma_separated(self):
        assert file_ids_query((4, 17, 230)) == "?file_ids=4,17,230"


class TestTheRequestLine:
    def test_a_line_at_the_limit_fits(self):
        overhead = len("GET  HTTP/1.1")

        assert request_line_fits("u" * (MAX_REQUEST_LINE_BYTES - overhead))

    def test_a_line_one_byte_over_does_not(self):
        overhead = len("GET  HTTP/1.1")

        assert not request_line_fits("u" * (MAX_REQUEST_LINE_BYTES - overhead + 1))

    def test_it_counts_bytes_rather_than_characters(self):
        overhead = len("GET  HTTP/1.1")

        assert not request_line_fits("é" * (MAX_REQUEST_LINE_BYTES - overhead))
