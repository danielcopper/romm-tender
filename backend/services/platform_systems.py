"""PlatformSystemService — the system a RomM platform is in an emulator source, and the one an installed game keeps.

Owns the platform's kept ids — the ``kv_config`` value written at every sync
beside the platform names — and their one live read from RomM where none are
kept for a platform yet. Which system the ids give is asked of one source at a
time (``SourcePlatformSystems``); an installed game keeps the system its
install record holds, because only a new download follows the resolver's
current answer.

Every method blocks: it reads the database, and may read RomM and the resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.platform_names import decode_platform_names
from domain.platform_system import (
    FOUND,
    PLATFORM_IDS_KEY,
    PlatformIds,
    PlatformSystem,
    decode_platform_ids,
    encode_platform_ids,
    platform_ids_by_slug,
)

if TYPE_CHECKING:
    from domain.emulator_sources import SourcesReading
    from domain.rom_install import RomInstall
    from services.protocols import DebugLogger, RommPlatformReader, SourcePlatformSystems, UnitOfWorkFactory

_PLATFORM_NAMES_KEY = "platform_names"


@dataclass(frozen=True)
class PlatformSystemServiceConfig:
    """Frozen wiring bundle handed to ``PlatformSystemService.__init__``.

    Carries the Unit-of-Work factory the kept ids and names are read and
    written through, the RomM platform listing they are read from where none
    are kept, the seam that asks a source, and the debug logger.
    """

    uow_factory: UnitOfWorkFactory
    romm_api: RommPlatformReader
    source_platform_systems: SourcePlatformSystems
    log_debug: DebugLogger


class PlatformSystemService:
    """Answers the system a platform is in a source (``PlatformSystems``)."""

    def __init__(self, *, config: PlatformSystemServiceConfig) -> None:
        self._uow_factory = config.uow_factory
        self._romm_api = config.romm_api
        self._source_platform_systems = config.source_platform_systems
        self._log_debug = config.log_debug

    def platform_system(
        self,
        platform_slug: str,
        *,
        source: str | None = None,
        reading: SourcesReading | None = None,
        ask_romm: bool = True,
    ) -> PlatformSystem:
        """The system *platform_slug* is in *source*, the answering source where it is ``None``.

        Where no ids are kept for the platform they are read from RomM's
        listing and kept, and a failed read raises; with *ask_romm* false they
        count as none instead.
        """
        ids, name = self._ids_and_name(platform_slug, ask_romm=ask_romm)
        return self._source_platform_systems.platform_system(
            ids, platform_slug=platform_slug, platform_name=name, source=source, reading=reading
        )

    def rom_system(
        self, platform_slug: str, install: RomInstall | None, *, reading: SourcesReading | None = None
    ) -> PlatformSystem:
        """The install record's system for an installed game, else :meth:`platform_system` in the answering source."""
        if install is not None:
            return PlatformSystem(FOUND, platform_slug, platform_slug, system=install.system)
        return self.platform_system(platform_slug, reading=reading)

    def _ids_and_name(self, platform_slug: str, *, ask_romm: bool) -> tuple[PlatformIds, str]:
        with self._uow_factory() as uow:
            kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
            names = decode_platform_names(uow.kv_config.get(_PLATFORM_NAMES_KEY))
        if kept is not None and platform_slug in kept:
            ids = kept[platform_slug]
        elif ask_romm:
            ids = self._read_and_keep(platform_slug)
        else:
            ids = PlatformIds()
        return ids, names.get(platform_slug) or ids.name or platform_slug

    def _read_and_keep(self, platform_slug: str) -> PlatformIds:
        """*platform_slug*'s ids from RomM's listing, kept with every listed platform's beside those kept before."""
        listed = platform_ids_by_slug(self._romm_api.list_platforms())
        ids = listed.get(platform_slug, PlatformIds())
        self._log_debug(f"[platforms] no ids kept for {platform_slug}; read {len(listed)} from RomM")
        with self._uow_factory() as uow:
            current = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY)) or {}
            uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids({**current, **listed, platform_slug: ids}))
        return ids
