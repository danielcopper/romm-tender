"""In-memory ``PlatformSystems`` implementation for service tests.

Answers a platform's system from a mapping, so a service test exercises what
its caller does with a system — or with none — without the kept ids, RomM's
listing or the resolver behind the real ``PlatformSystemService``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.emulator_sources import ArrangedSource
from domain.platform_system import FOUND, PlatformSystem

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading
    from domain.rom_install import RomInstall

RETRODECK_SOURCE = ArrangedSource(kind="retrodeck", enabled=True, starts_games=True)


class FakePlatformSystems:
    """A slug in ``answers`` answers that value; one in ``mapping`` that system; any other slug itself.

    The tests' platforms are mostly spelled like the systems they stand for,
    which is why a slug nothing names answers itself. ``calls`` records each
    ``(platform_slug, source)`` asked, through either method; an installed game
    is answered from its record.
    """

    def __init__(
        self, mapping: dict[str, str] | None = None, *, answers: dict[str, PlatformSystem] | None = None
    ) -> None:
        self.mapping: dict[str, str] = mapping if mapping is not None else {}
        self.answers: dict[str, PlatformSystem] = answers if answers is not None else {}
        self.calls: list[tuple[str, str | None]] = []

    def platform_system(
        self,
        platform_slug: str,
        *,
        source: str | None = None,
        reading: SourcesReading | None = None,
        ask_romm: bool = True,
    ) -> PlatformSystem:
        self.calls.append((platform_slug, source))
        if platform_slug in self.answers:
            return self.answers[platform_slug]
        system = self.mapping.get(platform_slug, platform_slug)
        return PlatformSystem(FOUND, platform_slug, platform_slug, system=system, source=RETRODECK_SOURCE)

    def rom_system(
        self, platform_slug: str, install: RomInstall | None, *, reading: SourcesReading | None = None
    ) -> PlatformSystem:
        if install is None:
            return self.platform_system(platform_slug, reading=reading)
        self.calls.append((platform_slug, None))
        return PlatformSystem(FOUND, platform_slug, platform_slug, system=install.system)
