"""PlatformSystemService — the system a RomM platform is in an emulator source, and the one an installed game keeps.

Owns the kept platforms — the ``kv_config`` value replaced by every sync's
listing (``replace_listing``) beside the platform names, keyed by each
platform's folder in RomM, kept again from every listing the refiling is
handed (``keep_listing``) — and their one live read from RomM where none is
kept for a platform yet. Which system a platform is, asked by the slug and the
folder its caller names, is asked of one source at a time
(``SourcePlatformSystems``); the kept record gives the platform's name. An
installed game keeps the system its install record holds, because only a new
download follows the resolver's current answer.

Every method blocks: it reads the database, and may read RomM and the resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.platform_names import PLATFORM_NAMES_KEY, decode_platform_names
from domain.platform_system import (
    FOUND,
    PLATFORM_IDS_KEY,
    UNASKED,
    KeptPlatform,
    PlatformKeys,
    PlatformSystem,
    decode_platform_ids,
    encode_platform_ids,
    kept_entry,
    kept_folders_of_slug,
    kept_platforms,
)
from lib.errors import RommApiError, classify_error, romm_error_log_level

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping, Sequence

    from domain.emulator_sources import SourcesReading
    from domain.rom_install import RomInstall
    from services.protocols import (
        DebugLogger,
        ListingRefiler,
        RommPlatformReader,
        SourcePlatformSystems,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class PlatformSystemServiceConfig:
    """Frozen wiring bundle handed to ``PlatformSystemService.__init__``.

    Carries the Unit-of-Work factory the kept platforms and names are read and
    written through, the RomM platform listing they are read from where none
    are kept, the refiling that listing is handed to once read, the seam that
    asks a source, the logger a failed listing read is logged with, and the
    debug logger.
    """

    uow_factory: UnitOfWorkFactory
    romm_api: RommPlatformReader
    refiling: ListingRefiler
    source_platform_systems: SourcePlatformSystems
    logger: logging.Logger
    log_debug: DebugLogger


class PlatformSystemService:
    """Answers the system a platform is in a source (``PlatformSystems``)."""

    def __init__(self, *, config: PlatformSystemServiceConfig) -> None:
        self._uow_factory = config.uow_factory
        self._romm_api = config.romm_api
        self._refiling = config.refiling
        self._source_platform_systems = config.source_platform_systems
        self._logger = config.logger
        self._log_debug = config.log_debug

    def platform_system(
        self,
        keys: PlatformKeys,
        *,
        source: str | None = None,
        reading: SourcesReading | None = None,
        ask_romm: bool = True,
    ) -> PlatformSystem:
        """The system the platform *keys* names is in *source*, the answering source where it is ``None``.

        The platform is asked by the slug and the folder *keys* names
        (:class:`~domain.platform_system.PlatformKeys`); what is kept for it
        gives its name. Where nothing is kept for
        it, RomM's listing is read and kept, and the listing is handed to the
        refiling, so this read may move Tender's older entries; with *ask_romm*
        false nothing is read. A read that fails answers
        :data:`~domain.platform_system.UNASKED` with the reason every RomM read
        is classified by, the source that would have been asked, and — where
        there is none — why none would be (``unanswered``), and keeps nothing. It is logged here, at the level the
        entrypoint logs a RomM error at, because what it becomes reaches the
        entrypoint as a refusal, or as an answer that something is unavailable,
        and neither is logged.
        """
        try:
            name = self._name(keys, ask_romm=ask_romm)
        except RommApiError as exc:
            reason, message = classify_error(exc)
            self._logger.log(
                romm_error_log_level(exc),
                f"[platforms] no ids kept for {keys.slug}, and RomM's listing failed: {type(exc).__name__}: {exc}",
            )
            asked, why_none = self._source_platform_systems.asked_or_why(source=source, reading=reading)
            return PlatformSystem(
                UNASKED,
                keys,
                keys.kept_as,
                source=asked,
                unasked=reason,
                unasked_message=message,
                unanswered=why_none,
            )
        return self._source_platform_systems.platform_system(keys, platform_name=name, source=source, reading=reading)

    def rom_system(
        self, keys: PlatformKeys, install: RomInstall | None, *, reading: SourcesReading | None = None
    ) -> PlatformSystem:
        """The install record's system for an installed game, else :meth:`platform_system` in the answering source."""
        if install is not None:
            return PlatformSystem(FOUND, keys, keys.kept_as, recorded=install.system)
        return self.platform_system(keys, reading=reading)

    def listed_platforms(self) -> frozenset[PlatformKeys] | None:
        """The slug and folder of every kept platform; ``None`` where no listing was ever kept. Reads nothing from RomM.

        That is each platform RomM listed at the last sync, every one a listing
        read since named (each is handed to :meth:`keep_listing`), and a
        platform asked about since that the listing did not name.
        """
        with self._uow_factory() as uow:
            kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
        if kept is None:
            return None
        return frozenset(
            PlatformKeys.of(entry.slug, entry.fs_slug) for entry in kept.values() if entry.slug is not None
        )

    def kept_folders(self, platform_slug: str) -> frozenset[str]:
        """The ``fs_slug`` of every kept platform of *platform_slug*. Reads nothing from RomM."""
        with self._uow_factory() as uow:
            return kept_folders_of_slug(uow.kv_config.get(PLATFORM_IDS_KEY), platform_slug)

    def keep_listing(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        """Keep every platform *platforms* names, beside each kept one of a slug the listing does not name. Blocking."""
        self._keep(kept_platforms(platforms, self._romm_api.get_version()))

    def replace_listing(self, platforms: Sequence[Mapping[str, Any]]) -> None:
        """Keep exactly the platforms *platforms* names, in place of every one kept before. Blocking.

        A failed write is logged and leaves the caller alone: the platforms
        kept before still answer, and one with nothing kept is read from RomM
        when asked.
        """
        try:
            listed = kept_platforms(platforms, self._romm_api.get_version())
            with self._uow_factory() as uow:
                uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids(listed))
        except Exception as exc:
            self._logger.warning(f"Could not keep the platforms' ids: {exc}")

    def _keep(self, kept: Mapping[str, KeptPlatform]) -> None:
        """Keep *kept* in place of every platform kept before of the slugs it names."""
        slugs = {entry.slug for entry in kept.values()}
        with self._uow_factory() as uow:
            current = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY)) or {}
            before = {key: entry for key, entry in current.items() if entry.slug not in slugs}
            uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids({**before, **kept}))

    def _name(self, keys: PlatformKeys, *, ask_romm: bool) -> str:
        """The name the platform *keys* names is shown under; reads RomM's listing where nothing is kept for it."""
        with self._uow_factory() as uow:
            kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
            names = decode_platform_names(uow.kv_config.get(PLATFORM_NAMES_KEY))
        entry = kept_entry(kept or {}, keys)
        if entry is None and ask_romm:
            entry = self._read_and_keep(keys)
        # The names cache is keyed by fs_slug; an entry kept before it recorded
        # one was kept under its slug.
        named_by = keys.folder or (entry.fs_slug if entry is not None and entry.fs_slug else keys.slug)
        # A platform listed without a name is named by its fs_slug (#2761 D3).
        return names.get(named_by) or (entry.name if entry is not None else None) or named_by

    def _read_and_keep(self, keys: PlatformKeys) -> KeptPlatform | None:
        """The platform *keys* names, from RomM's listing; the listing is kept by the refiling's keep step.

        The listing is handed to the refiling, which has it kept
        (:meth:`keep_listing`); this adds only the placeholder for a platform the
        listing does not name. How the two are merged:
        ``docs/architecture/core-emulator-selection.md#a-platforms-system``.
        """
        platforms = self._romm_api.list_platforms()
        self._refiling.do_refile(platforms)
        listed = kept_platforms(platforms, self._romm_api.get_version())
        entry = kept_entry(listed, keys)
        self._log_debug(f"[platforms] no ids kept for {keys.slug}; read {len(listed)} from RomM")
        # The refiling has had the listing kept (keep_listing); what is left is
        # the platform the listing did not name, kept so it is not read again.
        if entry is None:
            self._keep_unlisted(keys)
        return entry

    def _keep_unlisted(self, keys: PlatformKeys) -> None:
        """Keep the platform *keys* names with nothing but its slug and folder, unless one is kept under that key.

        It is kept under its folder where one was asked with, else its slug.
        """
        key = keys.kept_as
        with self._uow_factory() as uow:
            current = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY)) or {}
            if key in current:
                return
            current[key] = KeptPlatform(slug=keys.slug, fs_slug=keys.folder)
            uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids(current))
