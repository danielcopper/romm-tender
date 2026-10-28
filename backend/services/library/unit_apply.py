"""UnitApplier — one work unit's apply, from its fetch to its last chunk.

Which units run, and what the run makes of their outcomes, is
:class:`~services.library.sync_orchestrator.SyncOrchestrator`'s.

One write is this module's deliberately: the platform stamp's DELETE, a step of
the apply rather than a question a run weighs, which
:class:`LocalLibraryReader` therefore does not hold. Its ordering is argued at
its call site.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from domain.shortcut_data import build_shortcuts_data
from domain.sibling_resolution import AUTO_REGION
from domain.sync_diff import (
    BIND_ROM_ID_KEY,
    classify_roms,
    collapse_sibling_groups,
    gather_unbound_groups,
    with_kept_rows_rebound,
)
from domain.sync_stage import SyncStage
from services.library._convergence import converge_unless_running
from services.library._followed_keys import with_followed_keys
from services.library._unit_roms import (
    collection_membership_key,
    record_collection_membership,
    stamp_component_group_keys,
)

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Awaitable, Callable

    from domain.emulator_sources import SourcesReading
    from domain.sync_diff import ConvergedBinding
    from domain.work_unit import WorkUnit
    from services.library._state import CollectionMembership, LibrarySyncStateBox
    from services.library.chunk_dispatcher import ChunkDispatcher
    from services.library.collection_fetcher import CollectionDelivery, CollectionFetcher
    from services.library.cover_preparer import CoverPreparer
    from services.library.fetcher import LibraryFetcher
    from services.library.local_library_reader import LocalLibraryReader
    from services.library.shortcut_launch_resolver import ShortcutLaunchResolver
    from services.protocols import Clock, RunningRomIdsFn, UnitOfWorkFactory

    EmitProgressFn = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class UnitApplierConfig:
    """Frozen wiring bundle handed to ``UnitApplier.__init__``.

    Holds the live settings (the preferred region a collapse ranks by), the loop
    and logger, the launcher path every built shortcut's ``exe`` names, the
    Clock a platform unit's fetch start is read from, the SQLite Unit-of-Work factory
    (which opens exactly one transaction from this module: the platform stamp's
    DELETE at a unit's apply start), the shared :class:`LibrarySyncStateBox`, the
    :class:`LibraryFetcher` and :class:`CollectionFetcher` peers a platform and a
    collection unit are fetched through, the :class:`ShortcutLaunchResolver` that
    answers each bake's installed path and emulator, the
    :class:`LocalLibraryReader` the bound-row registry and the resident group
    keys are read through,
    the :class:`CoverPreparer` that holds the unit's covers, the
    :class:`ChunkDispatcher` that takes the built delta the rest of the way, the
    ``emit_progress`` callback the unit's coarse frame goes out through, and the
    ``running_rom_ids`` reading a platform unit's convergence leaves a running
    game out by.
    """

    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    launcher_exe: str
    clock: Clock
    uow_factory: UnitOfWorkFactory
    sync_state_box: LibrarySyncStateBox
    fetcher: LibraryFetcher
    collection_fetcher: CollectionFetcher
    shortcut_launch_resolver: ShortcutLaunchResolver
    local_library_reader: LocalLibraryReader
    cover_preparer: CoverPreparer
    chunk_dispatcher: ChunkDispatcher
    emit_progress: EmitProgressFn
    running_rom_ids: RunningRomIdsFn


class UnitApplier:
    def __init__(self, *, config: UnitApplierConfig) -> None:
        self._settings = config.settings
        self._loop = config.loop
        self._logger = config.logger
        self._launcher_exe = config.launcher_exe
        self._clock = config.clock
        self._uow_factory = config.uow_factory
        self._sync_state = config.sync_state_box
        self._fetcher = config.fetcher
        self._collection_fetcher = config.collection_fetcher
        self._shortcut_launch_resolver = config.shortcut_launch_resolver
        self._local_library_reader = config.local_library_reader
        self._cover_preparer = config.cover_preparer
        self._chunk_dispatcher = config.chunk_dispatcher
        self._emit_progress = config.emit_progress
        self._running_rom_ids = config.running_rom_ids

    def _clear_platform_stamp_io(self, platform_slug: str) -> None:
        """Delete *platform_slug*'s completion stamp in one short write UoW.

        Called at a platform unit's apply start (ADR-0023 / #1025): the stamp
        asserts "this platform's last apply completed", so a fresh apply must
        drop it up front and let the final chunk re-write it only on a clean
        finish. A no-op when no stamp exists.
        """
        with self._uow_factory() as uow:
            uow.platform_sync_state.delete(platform_slug)

    async def fetch_collection_units(
        self, units: list[WorkUnit], synced_rom_ids: set[int], *, unit_index: int, total_units: int
    ) -> dict[tuple[str, str], CollectionDelivery]:
        """Fetch the run's collection *units* before the first applies; answer each one's delivery by membership key.

        *units* are in queue order, the first at *unit_index*; they are fetched by
        :meth:`CollectionFetcher.fetch_collection_units` against *synced_rom_ids*.
        The component keys are then stamped once over every fetched delivery,
        with the resident keys the preview reads, so the apply's groups are the
        preview's; and :func:`gather_unbound_groups` moves every member of a
        group no bound row holds into the first fetched delivery holding one, so
        that unit mints it over every member the run delivers. A skipped
        delivery takes part in neither: it holds bound rows only, and its unit
        collapses nothing.
        """
        deliveries = await self._collection_fetcher.fetch_collection_units(
            units, synced_rom_ids, progress_step=unit_index + 1, progress_total_steps=total_units
        )
        fetched = [delivery for delivery in deliveries if not delivery.skipped]
        resident_keys = await self._loop.run_in_executor(None, self._local_library_reader.do_read_resident_group_keys)
        stamp_component_group_keys([rom for delivery in fetched for rom in delivery.roms], resident_keys)
        registry = await self._loop.run_in_executor(None, self._local_library_reader.do_read_apply_registry, units[0])
        gathered = gather_unbound_groups([delivery.roms for delivery in fetched], registry)
        by_key = {collection_membership_key(delivery.unit): delivery for delivery in deliveries}
        for delivery, roms in zip(fetched, gathered, strict=True):
            by_key[collection_membership_key(delivery.unit)] = replace(delivery, roms=roms)
        return by_key

    async def apply_unit(
        self,
        unit: WorkUnit,
        *,
        sources: SourcesReading,
        unit_index: int,
        total_units: int,
        synced_rom_ids: set[int],
        collection_memberships: dict[tuple[str, str], CollectionMembership],
        platform_rom_ids: set[int],
        delivery: CollectionDelivery | None = None,
    ) -> int:
        """Process one work unit start-to-finish; return its processed-ROM count.

        A platform unit's ROMs come from a live per-unit fetch; a collection
        unit's are its *delivery*, which :meth:`fetch_collection_units` fetched
        before the run applied any collection unit. After the
        frontend acks the unit's shortcuts (via ``report_unit_results``),
        the reporter commits the unit: it upserts each acked ROM into the
        ``roms`` aggregate and stamps the ROM's cached ``rom_metadata`` in
        the same write UoW (Rom row first, metadata second — FK-safe), so
        a ROM and its metadata land atomically.

        When the fetcher reports ``skipped=True`` (registry already
        matches the server-side platform state), the entire apply +
        commit branch is short-circuited: no frontend roundtrip, no
        registry write. The unit's ROMs still count toward the
        ``total_games_applied`` total returned to the user. Delta-skipped
        entries (content-unchanged ROMs the delta-restricted apply never
        re-emits) count the same way: the return value is the unit's
        processed ROMs — skips plus acked applies — not just the
        shortcuts written.
        """
        box = self._sync_state
        # Coarse anchor for the unit: FETCHING (not APPLYING) so the whole
        # per-unit prep phase — the paginated fetch + cover download below —
        # reads as "Fetching library". The label flips to "Applying shortcuts"
        # exactly once, when the chunk loop starts (frontend-driven), instead of
        # showing a frozen "Applying shortcuts" bar for the minutes-long fetch.
        # A delivered unit was fetched already, so its anchor fills its fetch
        # slice whole.
        await self._emit_progress(
            SyncStage.FETCHING,
            message=f"Fetching {unit.name}",
            step=unit_index + 1,
            total_steps=total_units,
            **({} if delivery is None else {"current": 1, "total": 1, "sub_stage": "fetch"}),
        )

        # Either kind may incremental-skip.
        if unit.type == "platform":
            # The unit's fetch start, read before the skip check so it is never
            # later than the walk's first page; the final chunk's stamp records
            # it as the next skip's reference (#743 D1, #2477 D2). The unit's
            # coarse step/total is threaded so the fetcher's per-page
            # ``fetching`` frames keep the bar's position.
            fetch_started_at = self._clock.now().isoformat()
            unit_roms, skipped = await self._sync_platform_unit(
                unit,
                synced_rom_ids=synced_rom_ids,
                platform_rom_ids=platform_rom_ids,
                progress_step=unit_index + 1,
                progress_total_steps=total_units,
            )
        elif delivery is None:
            raise ValueError(f"collection unit {unit.name} applied without its delivery")
        else:
            unit_roms, skipped, fetch_started_at = delivery.roms, delivery.skipped, delivery.fetch_started_at
            record_collection_membership(collection_memberships, unit, delivery.member_rom_ids)

        if box.is_cancelling():
            return 0

        # Per-unit incremental skip: registry already matches the
        # server-side state for this platform, so neither apply nor
        # commit have any work. Skip the frontend roundtrip and the
        # no-op two-phase commit. Force Full Sync clears ``last_sync``
        # upstream, so ``skipped`` is always False on forced runs.
        if skipped:
            self._logger.info(f"Per-unit apply skipped: {unit.name} ({len(unit_roms)} ROMs unchanged)")
            # A wholesale-skipped unit's ROMs are already correct in Steam, so they
            # count toward the run's done total — the resume of a paused run skips
            # every platform it finished before the pause, and those games ARE done
            # (#1383).
            box.run_done_items += len(unit_roms)
            box.run_skip_rebuilt_rows.update((rom["id"], rom["platform_slug"]) for rom in unit_roms)
            return len(unit_roms)

        # Build shortcut data for every fetched ROM. Installed ROMs carry the
        # full launch command; uninstalled ROMs get an empty placeholder until
        # they are downloaded.
        installed_paths = await self._loop.run_in_executor(
            None, self._shortcut_launch_resolver.do_read_installed_paths, {rom["id"] for rom in unit_roms}, sources
        )
        core_overrides = await self._loop.run_in_executor(
            None, self._shortcut_launch_resolver.do_build_core_overrides, unit_roms, sources
        )

        # Read the bound-row registry once, before the build: it drives the group
        # collapse below. The component keying reads the preview's resident keys,
        # unbound rows' included, not the registry's (why:
        # docs/architecture/backend-architecture.md, "Group-aware sync"). A
        # delivered unit's ROMs already carry the keys stamped over every
        # delivery, which the keying leaves alone.
        registry = await self._loop.run_in_executor(None, self._local_library_reader.do_read_apply_registry, unit)
        resident_keys = await self._loop.run_in_executor(None, self._local_library_reader.do_read_resident_group_keys)
        complete_view = unit.type == "platform" and bool(unit.slug)
        unlisted = {int(rid) for rid in registry} - {rom["id"] for rom in unit_roms}
        # A platform unit's listing is a complete view of its groups, so the rows
        # it left out follow their groups' keys, and the convergence and the
        # collapse below group them by the keys the commit records.
        followed_keys = (
            stamp_component_group_keys(
                unit_roms,
                resident_keys,
                complete_rom_ids={rom["id"] for rom in unit_roms},
                unfetched_bound_ids=unlisted,
            )
            if complete_view
            else stamp_component_group_keys(unit_roms, resident_keys)
        )
        registry = with_followed_keys(registry, followed_keys)
        shortcuts_data = build_shortcuts_data(unit_roms, self._launcher_exe, installed_paths, core_overrides)
        preferred_region = self._settings.get("preferred_region", AUTO_REGION)

        # A group holding more than one bound shortcut converges in a complete
        # view (#2645); the finalize of a run that was not stopped removes the
        # others.
        installed = set(installed_paths)
        converged: list[ConvergedBinding] = []
        if complete_view and unit.slug:
            installed |= await self._loop.run_in_executor(
                None, self._local_library_reader.do_read_installed_rom_ids, unlisted
            )
            converged = await converge_unless_running(
                shortcuts_data,
                registry,
                installed,
                platform_slugs={unit.slug},
                preferred_region=preferred_region,
                running_rom_ids=self._running_rom_ids,
                logger=self._logger,
            )
        converged_ids = {binding.rom_id for binding in converged}

        # Collapse to one Steam shortcut per sibling group (ADR-0021): only the
        # representative, or the group's bound siblings outside the converged
        # ones, is emitted; a rebinding group's entry is keyed to its vanished
        # sibling so the frontend reuses that shortcut. A PLATFORM unit holds
        # every member of a group its platform listing returns (a group is
        # per-platform), so it is a complete view and may rebind; a version
        # hidden in RomM is not among them, so a bound one is collapsed as one
        # gone from RomM. A COLLECTION unit is a partial view (it holds what it
        # delivered and every member of a group no binding holds, but not a
        # group's bound sibling on another/skipped platform) — the collapse then
        # only grandfathers, never rebinds a live binding onto an uninstalled
        # sibling (#1296).
        emitted = collapse_sibling_groups(
            shortcuts_data,
            registry,
            installed,
            complete_group_view=(unit.type == "platform"),
            preferred_region=preferred_region,
            converged=converged_ids,
        )
        # What follows reads the registry without the converged rows, so no
        # cover is pushed onto a shortcut the finalize removes.
        box.run_converged.update((binding.rom_id, binding) for binding in with_kept_rows_rebound(converged, emitted))
        registry = {rid: reg for rid, reg in registry.items() if int(rid) not in converged_ids}

        # Delta-restricted apply (#1383 / #1382-M3): split the collapsed entries
        # against the recorded applied state and emit ONLY new + changed. An
        # unchanged entry — identity matches AND its built target launch_options
        # matches the recorded ``applied_launch_options`` — is already correct on
        # the shortcut, so it never reaches the frontend (no Set* walk, no confirm
        # poll). Its ``roms`` row still commits: chunking routes the skipped
        # groups' rows to chunk 0's leftover (sync_chunking), so DB work is never
        # dropped and the platform stamp still rides the final chunk. A rebind
        # entry is never unchanged — classify counts it as changed, because it
        # must move its binding onto the same dump. ``new_ids`` prices each
        # chunk create-vs-update for the budget gate. Covers ride only the delta
        # (covers are creates-only, #1391).
        new, changed, unchanged_ids, _stale, _disabled = classify_roms(emitted, registry, set())
        rebind_ids = {e["rom_id"] for e in emitted if BIND_ROM_ID_KEY in e}
        skip_ids = set(unchanged_ids)
        new_ids = {e["rom_id"] for e in new}
        apply_emitted = [e for e in emitted if e["rom_id"] not in skip_ids]
        self._logger.info(
            f"Delta apply: {unit.name} — {len(new)} new + {len(changed)} changed "
            f"({len(rebind_ids)} of them rebinds) of {len(emitted)} collapsed ({len(skip_ids)} unchanged skipped)"
        )
        # A skipped entry is already correct on its Steam shortcut — no Set* walk is
        # owed — so it counts as done the moment the delta is computed (#1383). It is
        # just as processed as a wholesale-skipped unit's ROMs, so every return below
        # also adds ``len(skip_ids)`` to the unit's processed count — keeping the
        # terminal frame's "N of M games processed" numerator (``total_games``)
        # consistent with this counter on a resumed run.
        box.run_done_items += len(skip_ids)

        # Cover-cache invalidation pass (#1386): before any cover is reused, refresh
        # the cache + grid copy of every BOUND fetched ROM whose server cover source
        # changed (delta-skipped ROMs included — covers otherwise ride only the
        # delta) and persist the fresh fingerprint. Runs BEFORE the cover download
        # below so a changed apply-emitted ROM downloads once here and the apply
        # path then reuses the refreshed cache. The returned {rom_id, app_id} list
        # rides the unit's first apply chunk so the frontend re-applies each cover
        # to the EXISTING shortcut via SetCustomArtworkForApp — without that push
        # the Steam tile stays stale in-session until a client restart.
        cover_refreshes = await self._cover_preparer.refresh_changed_covers(
            unit_roms, registry, progress_step=unit_index + 1, progress_total_steps=total_units, label=unit.name
        )

        if box.is_cancelling():
            return len(skip_ids)

        # Download artwork for the shortcuts about to be applied and stamp each
        # delta entry's cover path in place (a no-op when the delta is empty).
        # Returns the confirmed cover fingerprints (rom_id → fresh source) the
        # per-unit commit persists onto the upserted rows (#1386).
        confirmed_cover_sources = await self._cover_preparer.attach_unit_cover_paths(
            unit, unit_roms, apply_emitted, unit_index=unit_index, total_units=total_units
        )

        if box.is_cancelling():
            return len(skip_ids)

        # Clear this platform's completion stamp now that its apply is actually
        # beginning — the fetch succeeded, artwork is done, and the cancel guard
        # above has passed, so the next line starts emitting chunks. The stamp's
        # contract is "this platform's last apply ran to completion", which a
        # fresh apply invalidates the instant it starts: a crash / cancel /
        # heartbeat-timeout before the final chunk must leave NO stamp, so the
        # skip gate can't honour a stale one over a half-applied platform (the
        # #1025 silent-gap regression). The final chunk re-writes the stamp on a
        # clean finish; the other half of the pair is
        # ``ChunkDispatcher._build_final_platform_stamp``, which rides the final
        # chunk's commit UoW. A fetch that failed raised before here (fetch failure ≠
        # apply started) and a cancel during fetch/artwork returned at a guard
        # above with the old stamp intact, so an unstarted apply keeps it. A
        # skipped platform returned before this point and keeps its stamp. A
        # collection's stamp is not cleared here: its final chunk replaces it,
        # and the flows that unbind or delete one of its members delete it
        # (docs/architecture/backend-architecture.md, "Incremental skip").
        # A dedicated short write UoW (not folded into the first chunk's commit)
        # so the clear is unconditional at apply start — even a first-chunk
        # heartbeat-timeout, whose late ack commits without the stamp, leaves no
        # stale stamp behind (ADR-0023 / #1025). A delete, not ``revoke_skip``:
        # the chunks re-mark rows with this run's generation, and a kept stamp
        # naming the old one would make removed-game discovery read every
        # re-marked row as gone from RomM.
        if unit.type == "platform" and unit.slug:
            await self._loop.run_in_executor(None, self._clear_platform_stamp_io, unit.slug)

        # A collection unit's final chunk stamps a CollectionSyncState over its
        # full membership (the accumulator just populated by the fetch, #742); a
        # platform unit passes None. The full set — not just the applied new_roms —
        # is what a future skip replays to rebuild membership. Read back by the
        # collision-free ``(collection_kind, collection_id)`` key, not the name, so
        # a same-named sibling collection can't shadow this unit's members (#1503).
        collection_member_ids = None
        if unit.type == "collection":
            membership = collection_memberships.get(collection_membership_key(unit))
            collection_member_ids = membership.rom_ids if membership is not None else None

        applied_count = await self._chunk_dispatcher.apply_unit_in_chunks(
            unit,
            unit_index=unit_index,
            total_units=total_units,
            emitted=apply_emitted,
            shortcuts_data=shortcuts_data,
            unit_roms=unit_roms,
            new_ids=new_ids,
            confirmed_cover_sources=confirmed_cover_sources,
            cover_refreshes=cover_refreshes,
            collection_member_ids=collection_member_ids,
            fetch_started_at=fetch_started_at,
            followed_keys=followed_keys,
        )
        return len(skip_ids) + applied_count

    async def _sync_platform_unit(
        self,
        unit: WorkUnit,
        *,
        synced_rom_ids: set[int],
        platform_rom_ids: set[int],
        progress_step: int = 0,
        progress_total_steps: int = 0,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Resolve ROMs for a platform unit and update cross-unit accumulators.

        Returns ``(unit_roms, skipped)`` for the caller's downstream
        shortcut + artwork + apply phases. ROMs come from a live per-unit
        fetch (no preview cache); the fetcher's incremental-skip path
        handles the "unchanged platform" optimisation internally.
        ``progress_step`` / ``progress_total_steps`` thread the unit's coarse
        position into the fetcher's per-page ``fetching`` frames.
        """
        unit_roms, skipped = await self._fetcher.fetch_platform_unit(
            unit, progress_step=progress_step, progress_total_steps=progress_total_steps
        )
        platform_rom_ids.update(r["id"] for r in unit_roms)
        synced_rom_ids.update(r["id"] for r in unit_roms)
        return unit_roms, skipped
