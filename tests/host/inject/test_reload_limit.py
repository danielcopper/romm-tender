"""The record that caps how often this machine's backends take Steam's interface down."""

from __future__ import annotations

import json

from host.inject.reload_limit import RELOAD_LIMIT, RELOAD_WINDOW_SECONDS, ReloadLimit

NOW = 1_000_000.0


def limit_at(path, *, now: float = NOW) -> ReloadLimit:
    return ReloadLimit(str(path), clock=lambda: now)


def written(path, *times: float) -> None:
    path.write_text(json.dumps({"takedowns": list(times)}), encoding="utf-8")


class TestTheLimit:
    def test_nothing_recorded_allows_one(self, tmp_path):
        assert limit_at(tmp_path / "reload-guard.json").allows()

    def test_one_under_the_limit_allows_one_more(self, tmp_path):
        path = tmp_path / "reload-guard.json"
        written(path, NOW - 60)
        assert limit_at(path).allows()

    def test_at_the_limit_it_refuses(self, tmp_path):
        path = tmp_path / "reload-guard.json"
        written(path, NOW - 300, NOW - 60)
        assert RELOAD_LIMIT == 2
        assert not limit_at(path).allows()

    def test_takedowns_older_than_the_window_are_forgotten(self, tmp_path):
        path = tmp_path / "reload-guard.json"
        written(path, NOW - RELOAD_WINDOW_SECONDS - 1, NOW - RELOAD_WINDOW_SECONDS)
        assert limit_at(path).allows()

    def test_a_time_after_now_is_a_clock_set_back_and_not_counted(self, tmp_path):
        path = tmp_path / "reload-guard.json"
        written(path, NOW + 60, NOW + 120)
        assert limit_at(path).allows()

    def test_a_record_it_cannot_read_allows(self, tmp_path):
        path = tmp_path / "reload-guard.json"
        path.write_text("not json", encoding="utf-8")
        assert limit_at(path).allows()
        path.write_text(json.dumps({"takedowns": ["soon", True, None]}), encoding="utf-8")
        assert limit_at(path).allows()

    def test_a_time_no_float_can_hold_is_skipped_like_any_unreadable_entry(self, tmp_path):
        path = tmp_path / "reload-guard.json"
        path.write_text(f'{{"takedowns": [{10**400}, {NOW - 60}]}}', encoding="utf-8")

        assert limit_at(path).allows()
        limit_at(path).record()
        assert json.loads(path.read_text(encoding="utf-8")) == {"takedowns": [NOW - 60, NOW]}


class TestRecording:
    def test_it_adds_now_and_drops_what_the_window_has_passed(self, tmp_path):
        path = tmp_path / "state" / "reload-guard.json"
        path.parent.mkdir()
        written(path, NOW - RELOAD_WINDOW_SECONDS - 5, NOW - 30)

        limit_at(path).record()

        assert json.loads(path.read_text(encoding="utf-8")) == {"takedowns": [NOW - 30, NOW]}
        assert not limit_at(path).allows()

    def test_a_directory_it_cannot_write_is_no_failure(self, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.write_text("a file where a directory should be", encoding="utf-8")
        limit_at(blocked / "reload-guard.json").record()
