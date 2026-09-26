"""RomMetadata — cached RomM game metadata with a staleness signal.

The descriptive metadata the plugin caches per ROM (summary, genres, companies,
ratings, derived Steam categories) plus ``cached_at``, the Unix time it was
cached, which a reader measures its age against. Regenerated independently of
library sync — staleness, not a schedule, prompts a refresh. References its Rom
by id.
"""

from __future__ import annotations

from domain._aggregate import cosmic_aggregate


@cosmic_aggregate
class RomMetadata:
    """Cached ROM metadata plus the Unix time it was cached (``cached_at``)."""

    summary: str
    genres: tuple[str, ...]
    companies: tuple[str, ...]
    first_release_date: int | None
    average_rating: float | None
    game_modes: tuple[str, ...]
    player_count: str
    cached_at: float
    steam_categories: tuple[int, ...] = ()

    @classmethod
    def cached(
        cls,
        *,
        summary: str,
        genres: tuple[str, ...],
        companies: tuple[str, ...],
        first_release_date: int | None,
        average_rating: float | None,
        game_modes: tuple[str, ...],
        player_count: str,
        cached_at: float,
        steam_categories: tuple[int, ...] = (),
    ) -> RomMetadata:
        """Build a metadata record cached at Unix time ``cached_at``."""
        return cls(
            summary=summary,
            genres=genres,
            companies=companies,
            first_release_date=first_release_date,
            average_rating=average_rating,
            game_modes=game_modes,
            player_count=player_count,
            cached_at=cached_at,
            steam_categories=steam_categories,
        )
