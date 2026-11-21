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
    from services.protocols import SourcePlatformSystems

RETRODECK_SOURCE = ArrangedSource(kind="retrodeck", enabled=True, starts_games=True)


class FakeSourcePlatformSystems:
    """A folder or a slug in ``answers`` answers ``(state, system)`` there; any other slug is its own system, found.

    ``asked`` records each ``(keys, source)`` put to it. With ``real`` set, every
    question goes to that adapter instead — the real resolver over the test's
    tree — and is still recorded.
    """

    def __init__(self, answers: dict[str, tuple[PlatformState, SystemArgument | None]] | None = None) -> None:
        self.answers: dict[str, tuple[PlatformState, SystemArgument | None]] = answers if answers is not None else {}
        self.asked: list[tuple[PlatformKeys, str | None]] = []
        self.real: SourcePlatformSystems | None = None

    def platform_system(
        self,
        keys: PlatformKeys,
        *,
        platform_name: str,
        source: str | None = None,
        reading: SourcesReading | None = None,
    ) -> PlatformSystem:
        self.asked.append((keys, source))
        if self.real is not None:
            return self.real.platform_system(keys, platform_name=platform_name, source=source, reading=reading)
        state, system = self.answers.get(keys.folder or "", None) or self.answers.get(keys.slug, (FOUND, keys.slug))
        return platform_answer(state, keys, system, name=platform_name)

    def asked_source(
        self, *, source: str | None = None, reading: SourcesReading | None = None
    ) -> ArrangedSource | None:
        if self.real is not None:
            return self.real.asked_source(source=source, reading=reading)
        return RETRODECK_SOURCE
