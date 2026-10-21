"""What the installed emulators want — the one reading every platform-scoped answer is built from.

The demand side of the subsystem: the resolver's verified reading of one
platform's emulators, which the status surfaces and the download entry points
both ask. It holds no server vocabulary at all — RomM contributes a download and
nothing to any question here, which is why readiness survives an unreachable
server.

The demand is read per query and never stored: it changes with every RetroDECK
update, and a stored answer would drift silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.firmware_wants import FirmwareCatalogue

if TYPE_CHECKING:
    from services.protocols import FirmwarePlatformResolver


@dataclass(frozen=True)
class FirmwareDemandConfig:
    """Frozen wiring bundle handed to ``FirmwareDemand.__init__``: the per-platform resolver seam."""

    platform_firmware_resolver: FirmwarePlatformResolver


class FirmwareDemand:
    """The machine's firmware demand: what is wanted, where, and what the resolver found there."""

    def __init__(self, *, config: FirmwareDemandConfig) -> None:
        self._platform_firmware_resolver = config.platform_firmware_resolver

    def platform_catalogue(self, system: str | None) -> FirmwareCatalogue:
        """What *system*'s emulators want in the source that answers for it, read fresh and verified. Blocking.

        The question every platform-scoped answer asks, and the only one that
        covers a standalone emulator or settles a folder declaration. A platform
        with no system (``None``) has nothing to ask, and its reading did not
        happen.
        """
        if system is None:
            return FirmwareCatalogue(placements=(), unread_emulators=frozenset(), resolved=False)
        return self._platform_firmware_resolver(system)
