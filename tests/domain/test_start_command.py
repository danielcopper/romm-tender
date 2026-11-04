"""Tests for domain/start_command — a start refused for the source that answers the game (#2221 D21)."""

from __future__ import annotations

from domain.start_command import SOURCE_STARTS_NO_GAMES, StartRefused, source_refusal, start_verdict


class TestSourceRefusal:
    def test_a_source_tender_starts_no_game_through_refuses_and_is_named(self):
        assert source_refusal({"kind": "emudeck", "starts_games": False}) == StartRefused(
            SOURCE_STARTS_NO_GAMES, source="emudeck"
        )

    def test_the_source_games_start_through_refuses_nothing(self):
        assert source_refusal({"kind": "retrodeck", "starts_games": True}) is None

    def test_no_answering_source_refuses_nothing(self):
        assert source_refusal(None) is None

    def test_the_verdict_names_the_source(self):
        refusal = source_refusal({"kind": "emudeck", "starts_games": False})
        assert refusal is not None
        assert start_verdict(refusal) == {
            "starts": False,
            "reason": SOURCE_STARTS_NO_GAMES,
            "remarks": [],
            "source": "emudeck",
        }
