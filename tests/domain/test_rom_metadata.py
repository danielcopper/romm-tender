"""Unit tests for the ``RomMetadata`` aggregate."""

from __future__ import annotations

from domain.rom_metadata import RomMetadata


def _make_metadata(*, cached_at: float, steam_categories: tuple[int, ...] | None = None) -> RomMetadata:
    kwargs = {
        "summary": "A great game.",
        "genres": ("Action", "Adventure"),
        "companies": ("Nintendo",),
        "first_release_date": 1994,
        "average_rating": 9.5,
        "game_modes": ("Single player",),
        "player_count": "1",
        "cached_at": cached_at,
    }
    if steam_categories is not None:
        kwargs["steam_categories"] = steam_categories
    return RomMetadata.cached(**kwargs)


class TestCached:
    def test_sets_all_fields(self):
        meta = RomMetadata.cached(
            summary="A great game.",
            genres=("Action",),
            companies=("Nintendo",),
            first_release_date=1994,
            average_rating=9.5,
            game_modes=("Single player",),
            player_count="1",
            cached_at=1000.0,
            steam_categories=(2, 22),
        )
        assert meta.summary == "A great game."
        assert meta.genres == ("Action",)
        assert meta.companies == ("Nintendo",)
        assert meta.first_release_date == 1994
        assert meta.average_rating == 9.5
        assert meta.game_modes == ("Single player",)
        assert meta.player_count == "1"
        assert meta.cached_at == 1000.0
        assert meta.steam_categories == (2, 22)

    def test_steam_categories_defaults_to_empty_tuple(self):
        meta = _make_metadata(cached_at=1000.0)
        assert meta.steam_categories == ()
