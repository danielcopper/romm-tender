"""What Tender already knows about the library, read back out of the local database.

The sync run needs two kinds of knowledge before it can decide anything: what
RomM currently holds, and what this device recorded last time. The first is
:class:`~services.library.fetcher.LibraryFetcher`'s — it reads outward, over
HTTP. This module is its inward pair: every method here reads Tender's own
SQLite, and nothing here talks to RomM. That split is the reason the two exist
separately, so a method that would have to ask the server does not belong here
however well it fits the sentence below.

What the local side knows is the ``Rom`` rows this device keeps and their Steam
shortcut bindings, the per-platform and per-collection completion stamps its
runs left behind, the sibling group keys it persisted, and the last run that
finished — shaped here
into the projections a run's decisions are made against, and into the reachable
set the collections listing counts against. The decisions themselves live in
``domain/`` and the moves in
:class:`~services.library.sync_preview.SyncPreview`,
:class:`~services.library.unit_apply.UnitApplier` and
:class:`~services.library.sync_orchestrator.SyncOrchestrator`; the reads are all
this module does. It has no phase of its own — the baseline reads open a
preview, the stale scan closes a run, and the reachable set rides the collections
listing, outside any run — so being local, not being early, is what holds it
together.

**The module is declared read-only**, and that is checked:
``scripts/check_read_only_module.py`` fails on any repository call here that is
not a read. Its reach stops at what this file itself calls, and at calls rather
than bound-method references, and it reads a method's *name* rather than what it
does — so a write named like a read (``get_or_create``) would pass. The
declaration is worth having anyway: every method here opens its own short Unit
of Work and is offloaded through its caller's executor — the preview's, the
unit applier's or the orchestrator's, or the fetcher's for the reachable set —
at points chosen for cheapness, so a write among them would land at a moment
nobody picked.

Two neighbours live elsewhere instead, deliberately, though both touch the
same tables through the same repositories:

* :class:`~services.library.unit_apply.UnitApplier`'s
  ``_clear_platform_stamp_io`` **writes**. That alone rules it out of a
  read-only module, and it belongs with the apply pipeline that performs it
  rather than with the projections a run reasons about.
  :meth:`LocalLibraryReader.do_count_platforms_to_restamp` reads the same table and
  answers a question the caller weighs; the delete is a step the pipeline takes.
  *When* it is taken — after the fetch, after the artwork, after the cancel
  guard, before the first chunk, so a crash in between leaves no stamp
  (ADR-0023 / #1025) — is a property of its call site, and the argument for that
  ordering lives there in full.
* ``services/library/_unit_roms.py::stamp_component_group_keys`` does no I/O at
  all — it calls a pure domain function and writes the result back onto the
  caller's own list.

The projections' *shapes* are a contract with :mod:`domain.sync_diff`: an entry
here is what ``classify_roms`` / ``collapse_sibling_groups`` read, and
``services/artwork/service.py``'s invalidation pass scans the same dicts for
``cover_source``. Changing a key is changing that contract, not this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.platform_names import PLATFORM_NAMES_KEY, decode_platform_names
from domain.platform_skip import restamp_due
from domain.platform_sync_state import stamp_for_skip
from domain.sibling_resolution import reachable_rom_ids
from domain.sync_diff import StaleRow, select_stale_removals

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from domain.work_unit import WorkUnit
    from services.protocols import UnitOfWorkFactory


@dataclass(frozen=True)
class LocalLibraryReaderConfig:
    """Frozen wiring bundle handed to ``LocalLibraryReader.__init__``.

    Holds the SQLite Unit-of-Work factory, and only that. It is the whole
    dependency surface on purpose: everything this module answers comes from the
    local database, so a second dependency here would mean it had started
    reading somewhere else.
    """

    uow_factory: UnitOfWorkFactory


class LocalLibraryReader:
    """Reads this device's own record of the library out of SQLite.

    The inward half of the pair :class:`~services.library.fetcher.LibraryFetcher`
    completes: what was recorded here, never what RomM currently holds.
    """

    def __init__(self, *, config: LocalLibraryReaderConfig) -> None:
        self._uow_factory = config.uow_factory

    def do_read_preview_baseline(
        self, slug_to_name: dict[str, str]
    ) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
        """Read the classify baseline from SQLite in one short read UoW.

        Returns ``(registry, last_synced_platforms, last_synced_collections)``
        where ``registry`` is the ``classify_roms``-shaped dict (keyed by
        ``str(rom_id)``) reconstructed from the bound ``roms`` rows, with
        the platform display name resolved from *slug_to_name* (the live
        work-queue) and falling back to the slug. Each entry also carries the
        persisted ``cover_source`` fingerprint so the preview's cover-work
        count (#1386) compares in memory against the same projection — no
        second DB pass. The last-synced platform/collection lists come from
        the newest completed ``SyncRun``.
        """
        with self._uow_factory() as uow:
            registry: dict[str, dict[str, Any]] = {}
            for rom in uow.roms.iter_all():
                if rom.shortcut_app_id is None:
                    continue
                registry[str(rom.rom_id)] = {
                    "app_id": rom.shortcut_app_id,
                    "name": rom.name,
                    "fs_name": rom.fs_name,
                    "platform_name": slug_to_name.get(rom.platform_slug, rom.platform_slug),
                    "platform_slug": rom.platform_slug,
                    "sibling_group_key": rom.sibling_group_key,
                    "applied_launch_options": rom.applied_launch_options,
                    "cover_source": rom.cover_source,
                    "delivered_through": rom.delivered_through,
                }
            latest = uow.sync_runs.get_latest_completed()
            last_platforms = list(latest.platforms_completed or []) if latest is not None else []
            last_collections = list(latest.collections_completed or []) if latest is not None else []
        return registry, last_platforms, last_collections

    def do_count_platforms_to_restamp(
        self, platform_slugs: set[str], full_fetched: Mapping[str, Sequence[Mapping[str, Any]]]
    ) -> int:
        """Count enabled platform slugs whose wholesale skip only a fresh ``PlatformSyncState`` stamp can restore.

        A platform lacking a completion stamp, or holding one whose skip was
        revoked (``stamp_for_skip``), has no wholesale-skip authority. One the
        preview fetched in full (*full_fetched*, slug → the ROMs that fetch
        returned) counts too when its stamp does not match that fetch
        (``domain/platform_skip.restamp_due``): no checksum or fetch start, other
        ids, or a ROM updated after its reference. Either way
        ``LibraryFetcher._try_unit_incremental_skip`` full-fetches it on every
        sync until an apply re-stamps it, so its apply runs even at a zero
        shortcut delta and the empty final chunk re-writes the stamp. Surfaced as
        the preview's ``restamp_platform_count`` so the frontend still offers
        Apply on an otherwise-empty delta (#1416, #743). One short read UoW.
        """
        with self._uow_factory() as uow:
            return sum(
                1
                for slug in platform_slugs
                if (stamp := stamp_for_skip(uow.platform_sync_state.get(slug))) is None
                or (slug in full_fetched and restamp_due(stamp, full_fetched[slug]))
            )

    def do_read_apply_registry(self, unit: WorkUnit) -> dict[str, dict[str, Any]]:
        """Read the bound-row registry the per-unit group collapse diffs against.

        Platform units scope to their own platform's rows (a sibling group is
        per-platform, so a vanished bound sibling shares the platform);
        collection units read the whole registry since their ROMs span
        platforms. A platform unit's fetch is therefore a COMPLETE view, every
        member of a group its platform listing returns (the collapse may
        rebind); a bound version hidden in RomM is not among them and is
        collapsed as one gone from RomM. A collection unit's fetch is a PARTIAL
        view — the whole registry surfaces bindings for groups the unit only
        partly fetched, so the collapse must not treat those as vanished (it
        passes ``complete_group_view=False`` and only grandfathers). Only bound
        rows (a live ``shortcut_app_id``) are returned — an unbound sibling is
        not a shortcut the collapse can grandfather or rebind. Each entry also
        carries the persisted ``cover_source`` fingerprint, so the cover-cache
        invalidation pass (#1386) scans against this same projection instead of
        per-ROM DB lookups. The apply path did not read the registry before
        group-aware sync (ADR-0021).
        """
        with self._uow_factory() as uow:
            rows = (
                uow.roms.iter_by_platform(unit.slug) if unit.type == "platform" and unit.slug else uow.roms.iter_all()
            )
            return {
                str(rom.rom_id): {
                    "app_id": rom.shortcut_app_id,
                    "name": rom.name,
                    "fs_name": rom.fs_name,
                    "platform_slug": rom.platform_slug,
                    "sibling_group_key": rom.sibling_group_key,
                    "applied_launch_options": rom.applied_launch_options,
                    "cover_source": rom.cover_source,
                }
                for rom in rows
                if rom.shortcut_app_id is not None
            }

    def do_read_installed_rom_ids(self, rom_ids: Collection[int]) -> set[int]:
        """The ids among *rom_ids* holding an install record, by point lookups in one short read UoW.

        The apply reads the installs of the ROMs it fetched with their launch
        paths; this answers the bound rows its fetch left out, which the
        convergence (``domain.sync_diff.converge_bound_groups``) ranks by their
        install as well.
        """
        with self._uow_factory() as uow:
            return {rom_id for rom_id in rom_ids if uow.rom_installs.get(rom_id) is not None}

    def do_read_resident_group_keys(self) -> dict[int, str]:
        """Read every persisted non-null ``sibling_group_key`` (``rom_id → key``).

        The preview builds one shortcut set over every enabled platform, so it
        needs the DB's canonical summaries for a fresh member that edges into a
        sibling on a skipped (incremental) platform, which the preview reconstructs
        only its bound rows of. The apply keys every platform unit it fetches
        and every collection unit's delivery with the same summaries, so its
        groups are the preview's. The stale removal looks up here the group keys
        of the ROMs the run synced. One short read UoW.
        """
        with self._uow_factory() as uow:
            return {
                rom.rom_id: rom.sibling_group_key for rom in uow.roms.iter_all() if rom.sibling_group_key is not None
            }

    def do_read_reachable_rom_ids(self) -> set[int]:
        """Every ``rom_id`` the sync's collection filing resolves to a shortcut.

        :func:`domain.sibling_resolution.reachable_rom_ids` over every row — the
        same set ``SyncReporter._member_app_id`` answers, member by member. One
        short read UoW.
        """
        with self._uow_factory() as uow:
            return reachable_rom_ids(list(uow.roms.iter_all()))

    def do_read_collection_listed_rom_ids(self) -> frozenset[int]:
        """Every ``rom_id`` a collection held at its last completed apply.

        The union of every ``CollectionSyncState`` stamp's ``member_rom_ids``. A
        collection unit rewrites its stamp at its last chunk, so a run reads this
        before its first unit to learn what the collections held before it. One
        short read UoW.
        """
        with self._uow_factory() as uow:
            return frozenset(
                rom_id for stamp in uow.collection_sync_state.iter_all() for rom_id in stamp.member_rom_ids
            )

    def do_read_kept_platform_names(self, platform_slugs: Collection[str]) -> dict[str, str]:
        """The name the names cache holds for each of *platform_slugs* that has one.

        Every sync's finalize writes the cache, a stopped run's included.

        One short read UoW.
        """
        with self._uow_factory() as uow:
            kept = decode_platform_names(uow.kv_config.get(PLATFORM_NAMES_KEY))
        return {slug: kept[slug] for slug in platform_slugs if slug in kept}

    def do_scan_stale_roms(self, synced_rom_ids: set[int], synced_app_ids: set[int]) -> list[StaleRow]:
        """Return a :class:`StaleRow` for every bound ROM not synced this run.

        Unbound (stale) rows are skipped — they were already cleared on a
        prior run and carry no Steam shortcut to remove. The ``app_id`` is
        the still-live ``shortcut_app_id`` captured here, before the
        reporter's finalize unbinds the row; the orchestrator threads it
        into the ``sync_stale`` payload so the frontend removes the Steam
        shortcut without re-resolving rom_id→app_id after the unbind.

        Any candidate whose ``app_id`` is in *synced_app_ids* — an appId this
        run bound to a freshly-synced ROM — is excluded by
        :func:`select_stale_removals`: a new server-issued ``rom_id`` can reuse
        an old appId (unchanged ``exe + name``), so the old colliding row looks
        stale but its appId now belongs to the new row. Removing it would wipe
        the shortcut the run just created/updated (#1036).
        """
        with self._uow_factory() as uow:
            candidates = {
                rom.rom_id: rom
                for rom in uow.roms.iter_all()
                if rom.shortcut_app_id is not None and rom.rom_id not in synced_rom_ids
            }
        kept = select_stale_removals(
            [(rom_id, rom.shortcut_app_id) for rom_id, rom in candidates.items() if rom.shortcut_app_id is not None],
            synced_app_ids,
        )
        return [
            StaleRow(
                rom_id,
                app_id,
                candidates[rom_id].platform_slug,
                candidates[rom_id].delivered_through,
                candidates[rom_id].sibling_group_key,
            )
            for rom_id, app_id in kept
        ]
