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
    UNASKED,
    PlatformIds,
    PlatformSystem,
    decode_platform_ids,
    encode_platform_ids,
    platform_ids_by_slug,
)
from lib.errors import RommApiError, classify_error, romm_error_log_level

if TYPE_CHECKING:
    import logging

    from domain.emulator_sources import SourcesReading
    from domain.rom_install import RomInstall
    from services.protocols import DebugLogger, RommPlatformReader, SourcePlatformSystems, UnitOfWorkFactory

_PLATFORM_NAMES_KEY = "platform_names"


@dataclass(frozen=True)
class PlatformSystemServiceConfig:
    """Frozen wiring bundle handed to ``PlatformSystemService.__init__``.

    Carries the Unit-of-Work factory the kept ids and names are read and
    written through, the RomM platform listing they are read from where none
    are kept, the seam that asks a source, the logger a failed listing read is
    logged with, and the debug logger.
    """

    uow_factory: UnitOfWorkFactory
    romm_api: RommPlatformReader
    source_platform_systems: SourcePlatformSystems
    logger: logging.Logger
    log_debug: DebugLogger


class PlatformSystemService:
    """Answers the system a platform is in a source (``PlatformSystems``)."""

    def __init__(self, *, config: PlatformSystemServiceConfig) -> None:
        self._uow_factory = config.uow_factory
        self._romm_api = config.romm_api
        self._source_platform_systems = config.source_platform_systems
        self._logger = config.logger
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
        listing and kept; with *ask_romm* false they count as none instead. A
        read that fails answers :data:`~domain.platform_system.UNASKED` with the
        reason every RomM read is classified by, and keeps nothing. It is logged
        here, at the level the entrypoint logs a RomM error at, because what
        it becomes reaches the entrypoint as a refusal, or as an answer that
        something is unavailable, and neither is logged.
        """
        try:
            ids, name = self._ids_and_name(platform_slug, ask_romm=ask_romm)
        except RommApiError as exc:
            reason, message = classify_error(exc)
            self._logger.log(
                romm_error_log_level(exc),
                f"[platforms] no ids kept for {platform_slug}, and RomM's listing failed: {type(exc).__name__}: {exc}",
            )
            return PlatformSystem(
                UNASKED,
                platform_slug,
                platform_slug,
                source=self._source_platform_systems.asked_source(source=source, reading=reading),
                unasked=reason,
                unasked_message=message,
            )
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

    def listed_platforms(self) -> frozenset[str] | None:
        """Every platform whose ids are kept, or ``None`` where none are. Reads nothing from RomM.

        That is each platform RomM listed at the last sync, every one a listing
        read since named (:meth:`_read_and_keep` keeps the whole listing), and
        a platform asked about since that the listing did not name.
        """
        with self._uow_factory() as uow:
            kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
        return frozenset(kept) if kept is not None else None

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
