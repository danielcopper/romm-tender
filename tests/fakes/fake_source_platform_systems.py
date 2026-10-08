"""In-memory ``SourcePlatformSystems`` implementation for tests above the resolver.

Answers a platform's system without a catalogue to ask, so a test wired over
the real ``PlatformSystemService`` exercises the kept ids and the caller while
the platform answers what it is told to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.emulator_sources import ArrangedSource
from domain.platform_system import FOUND, PlatformSystem

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading
    from domain.platform_system import PlatformIds

RETRODECK_SOURCE = ArrangedSource(kind="retrodeck", enabled=True, starts_games=True)


class FakeSourcePlatformSystems:
    """A slug in ``answers`` answers ``(state, system)`` there; any other slug is its own system, found.

    ``asked`` records each ``(ids, platform_slug, source)`` put to it.
    """

    def __init__(self, answers: dict[str, tuple[str, str | None]] | None = None) -> None:
        self.answers: dict[str, tuple[str, str | None]] = answers if answers is not None else {}
        self.asked: list[tuple[PlatformIds, str, str | None]] = []

    def platform_system(
        self,
        ids: PlatformIds,
        *,
        platform_slug: str,
        platform_name: str,
        source: str | None = None,
        reading: SourcesReading | None = None,
    ) -> PlatformSystem:
        self.asked.append((ids, platform_slug, source))
        state, system = self.answers.get(platform_slug, (FOUND, platform_slug))
        return PlatformSystem(state, platform_slug, platform_name, system=system, source=RETRODECK_SOURCE)
