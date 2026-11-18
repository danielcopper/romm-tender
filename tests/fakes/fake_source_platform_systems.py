"""In-memory ``SourcePlatformSystems`` implementation for tests above the resolver.

Answers a platform's system without a catalogue to ask, so a test wired over
the real ``PlatformSystemService`` exercises the kept platforms and the caller
while the platform answers what it is told to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.emulator_sources import ArrangedSource
from domain.platform_system import FOUND
from fakes.fake_platform_systems import platform_answer

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading
    from domain.platform_system import PlatformKeys, PlatformState, PlatformSystem, SystemArgument

RETRODECK_SOURCE = ArrangedSource(kind="retrodeck", enabled=True, starts_games=True)


class FakeSourcePlatformSystems:
    """A folder or a slug in ``answers`` answers ``(state, system)`` there; any other slug is its own system, found.

    ``asked`` records each ``(keys, source)`` put to it.
    """

    def __init__(self, answers: dict[str, tuple[PlatformState, SystemArgument | None]] | None = None) -> None:
        self.answers: dict[str, tuple[PlatformState, SystemArgument | None]] = answers if answers is not None else {}
        self.asked: list[tuple[PlatformKeys, str | None]] = []

    def platform_system(
        self,
        keys: PlatformKeys,
        *,
        platform_name: str,
        source: str | None = None,
        reading: SourcesReading | None = None,
    ) -> PlatformSystem:
        self.asked.append((keys, source))
        state, system = self.answers.get(keys.folder or "", None) or self.answers.get(keys.slug, (FOUND, keys.slug))
        return platform_answer(state, keys, system, name=platform_name)

    def asked_source(self, *, source: str | None = None, reading: SourcesReading | None = None) -> ArrangedSource:
        return RETRODECK_SOURCE
