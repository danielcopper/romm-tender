"""In-memory ``PlatformSystems`` implementation for service tests.

Answers a platform's system from a mapping, so a service test exercises what
its caller does with a system — or with none — without the kept ids, RomM's
listing or the resolver behind the real ``PlatformSystemService``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.emulator_sources import ArrangedSource
from domain.platform_system import FOUND, SEVERAL, PlatformKeys, PlatformSystem, SystemPair

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain.emulator_sources import SourcesReading
    from domain.platform_system import PlatformState, SystemArgument
    from domain.rom_install import RomInstall

RETRODECK_SOURCE = ArrangedSource(kind="retrodeck", enabled=True, starts_games=True)


def platform_answer(
    state: PlatformState,
    keys: PlatformKeys | str,
    system: SystemArgument | None = None,
    *,
    name: str | None = None,
    source: ArrangedSource | None = RETRODECK_SOURCE,
    unasked: str | None = None,
) -> PlatformSystem:
    """A platform's answer as a test spells it: a state and the system it names — one key, one pair, or several pairs.

    One key stands for a system a source names by its key alone; several pairs
    are the systems the answer named, asked with as they are.
    """
    keys = PlatformKeys(keys) if isinstance(keys, str) else keys
    platform_name = name if name is not None else keys.kept_as
    if system is None:
        return PlatformSystem(state, keys, platform_name, source=source, unasked=unasked)
    if isinstance(system, SystemPair):
        return PlatformSystem(state, keys, platform_name, source=source, named=(system,), unasked=unasked)
    if isinstance(system, tuple):
        asked = system if state == SEVERAL else ()
        return PlatformSystem(state, keys, platform_name, source=source, named=system, asked=asked, unasked=unasked)
    return PlatformSystem(state, keys, platform_name, source=source, recorded=system, unasked=unasked)


class FakePlatformSystems:
    """A slug in ``answers`` answers that value; one in ``mapping`` that system; any other slug itself.

    The tests' platforms are mostly spelled like the systems they stand for,
    which is why a slug nothing names answers itself; several pairs answer
    :data:`~domain.platform_system.SEVERAL`. ``calls`` records each
    ``(platform_slug, source)`` asked, through either method, and ``asked``
    each platform's keys; an installed game
    is answered from its record. ``listed`` is the platforms whose ids are kept —
    nothing by default, and ``None`` where no listing was ever kept. ``kept`` is
    each slug's kept ``fs_slug`` values, and ``kept_asked`` the slugs asked.
    """

    def __init__(
        self, mapping: Mapping[str, SystemArgument] | None = None, *, answers: dict[str, PlatformSystem] | None = None
    ) -> None:
        self.mapping: dict[str, SystemArgument] = dict(mapping) if mapping is not None else {}
        self.answers: dict[str, PlatformSystem] = answers if answers is not None else {}
        self.listed: frozenset[PlatformKeys] | None = frozenset()
        self.kept: dict[str, frozenset[str]] = {}
        self.kept_asked: list[str] = []
        self.calls: list[tuple[str, str | None]] = []
        self.asked: list[PlatformKeys] = []

    def platform_system(
        self,
        keys: PlatformKeys,
        *,
        source: str | None = None,
        reading: SourcesReading | None = None,
        ask_romm: bool = True,
    ) -> PlatformSystem:
        self.calls.append((keys.slug, source))
        self.asked.append(keys)
        if keys.slug in self.answers:
            return self.answers[keys.slug]
        system = self.mapping.get(keys.slug, keys.slug)
        several = isinstance(system, tuple) and not isinstance(system, SystemPair)
        return platform_answer(SEVERAL if several else FOUND, keys, system)

    def rom_system(
        self, keys: PlatformKeys, install: RomInstall | None, *, reading: SourcesReading | None = None
    ) -> PlatformSystem:
        if install is None:
            return self.platform_system(keys, reading=reading)
        self.calls.append((keys.slug, None))
        self.asked.append(keys)
        return PlatformSystem(FOUND, keys, keys.kept_as, recorded=install.system)

    def listed_platforms(self) -> frozenset[PlatformKeys] | None:
        return self.listed

    def kept_folders(self, platform_slug: str) -> frozenset[str]:
        self.kept_asked.append(platform_slug)
        return self.kept.get(platform_slug, frozenset())
