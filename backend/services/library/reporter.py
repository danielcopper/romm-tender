"""Sync result reporter and registry-query sub-service.

Owns the post-apply path: the ``report_unit_results`` ack the frontend
sends (event signal only) and the chunk-dispatcher-driven
``commit_unit_results`` that finalises artwork file names and upserts
each acked ROM into the ``roms`` aggregate, stamping its cached
``rom_metadata`` in the same write UoW (Rom row first, then metadata —
FK-safe). Also owns the registry- and
run-history-derived query methods (``get_registry_platforms``,
``get_sync_stats``, ``get_sync_runs``) and the ``clear_sync_cache``
reset. The end of a run — its closing registry write, the collection maps and
the terminal signals — belongs in :class:`RunFinalizer`; anything that decides
"what should this sync do?" belongs in the orchestrator, the preview or the
unit applier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_outcome import binding_forgets_cover_record
from domain.fetch_generation import gone_from_romm_ids
from domain.platform_names import PLATFORM_NAMES_KEY, decode_platform_names
from domain.rom import DeliveryRoute, Rom
from domain.rom_file import has_file_on_romm
from domain.rom_metadata_mapping import build_rom_metadata
from domain.sibling_resolution import reachable_rom_ids
from domain.sync_diff import BIND_ROM_ID_KEY
from domain.version_metadata import VersionMetadata
from services.library._followed_keys import record_followed_keys

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.collection_sync_state import CollectionSyncState
    from domain.platform_sync_state import PlatformSyncState
    from domain.sync_run import SyncRun
    from services.library._state import LibrarySyncStateBox
    from services.protocols import ArtworkManager, Clock, SteamConfigStore, UnitOfWorkFactory


# How many of the newest sync runs ``get_sync_runs`` answers with — the run
# list is a recent-history panel, not an archive, and its repository offers no
# delete, so ``sync_runs`` only grows.
SYNC_RUN_HISTORY_LIMIT = 10


@dataclass(frozen=True)
class SyncReporterConfig:
    """Frozen wiring bundle handed to ``SyncReporter.__init__``.

    Holds the Protocol-typed Steam-config adapter (used for grid-dir
    lookup and Steam-Input mode application), the live settings dict,
    runtime infrastructure (loop, logger), clock,
    the SQLite Unit-of-Work factory (the transactional seam over the
    ``roms`` / ``rom_installs`` / ``sync_runs`` / ``kv_config``
    repositories), the shared ``LibrarySyncStateBox`` (the reporter reads
    the pending-sync dicts staged by :class:`ChunkDispatcher`; the run-lifecycle
    reset is owned by the orchestrator's terminal ``finally``, not here), and
    the ``ArtworkManager`` peer used for cover-path finalisation.
    """

    steam_config: SteamConfigStore
    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    clock: Clock
    uow_factory: UnitOfWorkFactory
    sync_state_box: LibrarySyncStateBox
    artwork: ArtworkManager


@dataclass(frozen=True)
class _SyncStatsReading:
    """One read of everything ``get_sync_stats`` takes from SQLite."""

    last_sync: str | None
    last_attempt: dict[str, str] | None
    rom_count: int
    resumable_games: int
    has_completion_stamp: bool


class SyncReporter:
    """Post-apply reporter + the ``roms``-derived queries + cache reset."""

    def __init__(self, *, config: SyncReporterConfig) -> None:
        self._steam_config = config.steam_config
        self._settings = config.settings
        self._loop = config.loop
        self._logger = config.logger
        self._clock = config.clock
        self._uow_factory = config.uow_factory
        self._sync_state = config.sync_state_box
        self._artwork = config.artwork

    # ── Report sync results (frontend callback) ──────────────────

    def _finalize_cover_path(self, grid, cover_path, app_id, rom_id_str):
        """Delegate to ArtworkService for the final ``{app_id}p.png`` cover-path.

        The frontend already applies a created shortcut's cover through Steam's
        artwork API during apply, and Steam writes the same ``{app_id}p.png`` grid
        file itself. This commit-time copy is the durability net: it lands the grid
        file even if the per-item API call failed, and costs no renderer heap.
        """
        return self._artwork.finalize_cover_path(grid, cover_path, app_id, rom_id_str)

    # ── Report unit results (per-unit pipeline) ──────────────────

    def _commit_unit_results_io(
        self,
        rom_id_to_app_id,
        unit_roms,
        platform_stamp=None,
        collection_stamp=None,
        fetch_id=None,
        delivered_through: DeliveryRoute | None = None,
    ):
        """Finalise artwork names, then persist EVERY fetched ROM of the chunk.

        Group-aware commit (ADR-0021): this chunk's slice of the live RomM fetch
        (*unit_roms*) is upserted — one ``roms`` row per sibling for its
        identity + version metadata — while only the acked representatives
        carry a Steam-shortcut binding. A non-representative sibling keeps
        whatever binding it already had (usually none); a bound row not
        re-acked this cycle is never silently unbound.

        The frontend ack is **translated through any rebind** first: a rebind
        entry is keyed by the bound sibling it moves off (so the frontend reused
        its shortcut), but the binding — and the finalised cover — move onto the
        same dump named in ``bind_rom_id``.

        ADR-0006 two-pass: cover-file RENAME is filesystem I/O so it runs FIRST
        (outside any UoW); the final paths are collected, then one short write
        UoW upserts every ROM (Rom row first, cached metadata second — FK-safe),
        so a ROM and its metadata land atomically. The same UoW writes the key
        each row the unit's listing left out follows (``pending_followed_keys``).

        ``platform_stamp`` (set by :class:`ChunkDispatcher` on the final chunk of
        a platform unit, ADR-0023) is saved inside that same write UoW, so the
        per-platform completion stamp commits atomically with the chunk's rom
        upserts — the platform is stamped complete iff its last chunk is durable.
        ``collection_stamp`` is the collection sibling (#742), set on the final
        chunk of a collection unit; it rides the same UoW so a
        collection is stamped complete iff its last chunk is durable. Exactly one
        of the two is ever set per commit (a unit is a platform or a collection).

        ``fetch_id`` is the fetch generation stamped onto every row this commit
        upserts (#1504). It is set on EVERY chunk of a platform unit — not just
        the final one — so the whole unit's rows share the generation its
        completion stamp records, and left ``None`` by collection units and the
        late-ack path (see :meth:`Rom.record_fetch_generation`).
        """
        grid = self._steam_config.grid_dir()
        box = self._sync_state

        # Translate the ack onto binding targets (a rebind moves the binding off
        # the sibling it is keyed by onto the same dump) and finalise the staged
        # cover to ``{app_id}p.png``, keyed by the target rom_id.
        binding: dict[int, int] = {}
        finalized: dict[int, str] = {}
        for rom_id_str, app_id in rom_id_to_app_id.items():
            entry = box.pending_sync.get(int(rom_id_str), {})
            target = int(entry.get(BIND_ROM_ID_KEY, int(rom_id_str)))
            binding[target] = int(app_id)
            finalized[target] = self._finalize_cover_path(grid, entry.get("cover_path", ""), int(app_id), str(target))

        # ``unit_roms`` is the live RomM fetch for the whole unit — the source of
        # each ROM's ``metadatum``. Keyed by rom_id so the persist loop can stamp
        # metadata in the same iteration as the upsert.
        roms_by_id = {int(r["id"]): r for r in unit_roms if "id" in r}

        with self._uow_factory() as uow:
            for raw in unit_roms:
                if "id" in raw:
                    self._persist_synced_rom(
                        uow, int(raw["id"]), binding, finalized, roms_by_id, fetch_id, delivered_through
                    )
            record_followed_keys(uow, box.pending_followed_keys)
            if platform_stamp is not None:
                uow.platform_sync_state.save(platform_stamp)
            if collection_stamp is not None:
                uow.collection_sync_state.save(collection_stamp)

        steam_input_mode = self._settings.get("steam_input_mode", "default")
        if steam_input_mode != "default" and binding:
            try:
                self._steam_config.set_steam_input_config([int(aid) for aid in binding.values()], mode=steam_input_mode)
            except Exception as e:
                self._logger.error(f"Failed to set Steam Input config: {e}")

    def _persist_synced_rom(
        self, uow, rom_id, binding, finalized, roms_by_id, fetch_id=None, delivered_through=None
    ) -> None:
        """Upsert one fetched ROM + its cached metadata into the open write UoW.

        Reads the built identity + version fields from ``pending_all_roms`` (the
        whole unit's shortcut-shaped build), binds the ROM only when it is a
        binding target this cycle (else preserves its existing binding — a
        non-representative sibling stays unbound, a bound row not re-acked keeps
        its shortcut), read-merges the locally resolved ids
        (``sgdb_id`` / ``ra_id`` / ``cover_path`` / ``cover_source`` follow
        "confirmed new wins, else preserve existing, else None"), saves the Rom,
        then stamps its cached metadata. Saving the Rom before its metadata satisfies the
        ``rom_metadata.rom_id → roms(rom_id)`` FK at commit. ``Rom.synced``
        validates untrusted RomM fields; a ``ValueError`` is caught so one bad
        row is skipped while the rest of the unit still commits.
        """
        built = self._sync_state.pending_all_roms.get(rom_id, {})
        existing = uow.roms.get(rom_id)
        # Bind a binding target this cycle; otherwise preserve any existing
        # binding (a non-representative sibling stays unbound, a bound row not
        # re-acked keeps its shortcut).
        app_id = binding.get(rom_id, existing.shortcut_app_id if existing is not None else None)
        try:
            rom = Rom.synced(
                rom_id=rom_id,
                platform_slug=built.get("platform_slug", ""),
                name=built.get("name", ""),
                fs_name=built.get("fs_name", ""),
                shortcut_app_id=app_id,
                synced_at=self._clock.now().isoformat(),
                igdb_id=built.get("igdb_id"),
                version=VersionMetadata.from_mapping(built),
                fs_size_bytes=built.get("fs_size_bytes"),
                has_file_on_disk=has_file_on_romm(built),
            )
        except ValueError as e:
            self._logger.warning(f"Skipping invalid ROM {rom_id} during commit: {e}")
            return
        self._merge_locally_resolved_fields(rom, rom_id, built, finalized, existing)
        self._merge_fetch_generation(rom, fetch_id, existing)
        route = delivered_through or (existing.delivered_through if existing is not None else None)
        if route:
            rom.record_delivery(route)
        uow.roms.save(rom)

        # Record the applied launch command for a binding TARGET this cycle (the
        # value the frontend just wrote onto the shortcut), so the next sync skips
        # this now-correct shortcut (delta-restricted apply, #1383). A skipped or
        # non-representative row is absent from ``binding`` and keeps its recorded
        # value — the pin-only ``set_applied_launch_options`` write never wipes it,
        # unlike ``save()``. This is the first of the six recorded-state writer
        # sites (the others: download-complete, adopt-complete, uninstall, home
        # migration, version switch).
        if rom_id in binding:
            rom.record_applied_launch_options(built.get("launch_options", ""))
            uow.roms.set_applied_launch_options(rom_id, rom.applied_launch_options)
            self._forget_cover_record_on_bind(uow, rom_id, binding[rom_id], existing)

        self._stamp_rom_metadata(uow, rom_id, roms_by_id.get(rom_id))

    @staticmethod
    def _forget_cover_record_on_bind(uow, rom_id: int, app_id: int, existing: Rom | None) -> None:
        """Forget the cover record of a version this commit binds anew or moves a shortcut onto (#2381 D28).

        :func:`domain.artwork_slot_outcome.binding_forgets_cover_record` decides;
        a rebind, a recreated shortcut and an adopted orphan all bind here.
        """
        previous_app_id = existing.shortcut_app_id if existing is not None else None
        cover_chosen = uow.artwork_slot_choices.get(rom_id, ArtworkSlot.COVER) is not None
        if binding_forgets_cover_record(previous_app_id=previous_app_id, app_id=app_id, cover_chosen=cover_chosen):
            uow.artwork_slot_outcomes.delete_slot(rom_id, ArtworkSlot.COVER)
            uow.artwork_slot_handovers.delete_slot(rom_id, ArtworkSlot.COVER)

    def _merge_locally_resolved_fields(self, rom: Rom, rom_id: int, built, finalized, existing) -> None:
        """Read-merge the locally resolved fields onto the freshly built Rom.

        Each field follows "confirmed new wins, else preserve existing, else
        None": ``cover_path`` from this unit's finalized grid copies,
        ``cover_source`` from the box's confirmed ``pending_cover_sources``
        (never blindly the fetch's fresh string — a failed download keeps the
        old fingerprint so the change is retried next sync), and the
        ``sgdb_id`` / ``ra_id`` resolver results.
        """
        cover_path = finalized.get(rom_id) or (existing.cover_path if existing is not None else None)
        if cover_path:
            rom.update_cover_path(cover_path)
        cover_source = self._sync_state.pending_cover_sources.get(rom_id) or (
            existing.cover_source if existing is not None else None
        )
        if cover_source:
            rom.adopt_cover_source(cover_source)
        sgdb_id = self._merge_optional_id(built.get("sgdb_id"), existing.sgdb_id if existing else None)
        if sgdb_id is not None:
            rom.assign_sgdb_id(sgdb_id)
        ra_id = self._merge_optional_id(built.get("ra_id"), existing.ra_id if existing else None)
        if ra_id is not None:
            rom.assign_ra_id(ra_id)

    @staticmethod
    def _merge_fetch_generation(rom: Rom, fetch_id: str | None, existing) -> None:
        """Advance the row's fetch generation, or carry the existing one forward.

        Follows the same "confirmed new wins, else preserve existing, else None"
        merge as the locally resolved fields: a **platform** unit's commit supplies
        the generation and advances every row it upserts, while a **collection**
        unit (which supplies none) must leave a foreign platform's row on its own
        generation — re-marking it would drop it from that platform's counted
        rows and suppress that platform's skip (#1504). The column rides the sync
        UPSERT, so preserving here is what keeps the value across a re-save.
        """
        carried = fetch_id or (existing.last_fetch_id if existing is not None else None)
        if carried:
            rom.record_fetch_generation(carried)

    def _stamp_rom_metadata(self, uow, rom_id: int, rom: dict[str, Any] | None) -> None:
        """Stamp the ROM's cached metadata into ``uow.rom_metadata`` for this commit.

        No-op when the acked ROM carries no ``metadatum`` (defensive: thin
        registry-reconstructed ROMs from the incremental-skip path are
        already gated out upstream, but this guard prevents accidental
        cache erasure). The Rom row was saved just before this call in the
        same UoW, so the ``rom_metadata.rom_id`` FK is satisfied at commit.
        A malformed ``metadatum`` raises ``ValueError`` / ``TypeError`` in
        the mapping — caught here so only this ROM's metadata is skipped
        while its Rom row still commits.
        """
        if not rom or not rom.get("metadatum"):
            return
        try:
            meta = build_rom_metadata(rom, self._clock.time())
        except (ValueError, TypeError) as e:
            self._logger.warning(f"Skipping metadata for ROM {rom_id} — malformed metadatum: {e}")
            return
        uow.rom_metadata.save(rom_id, meta)

    @staticmethod
    def _merge_optional_id(new_value, existing_value) -> int | None:
        """Resolve a locally resolved id: non-None new wins, else preserve existing, else None."""
        if new_value is not None:
            return int(new_value)
        if existing_value is not None:
            return int(existing_value)
        return None

    async def report_unit_results(self, rom_id_to_app_id, run_id, unit_id, chunk_index):
        """The frontend's ack that this apply chunk's shortcuts are applied.

        Routes the ack in three cases, by run/unit/chunk identity:

        * **Active chunk** — the ack matches the dispatched
          ``current_sync_id`` / ``active_unit_id`` / ``active_chunk_index``
          (#1041). Record the rom_id→app_id mapping and, if the dispatcher is
          still waiting (``unit_complete_event`` live), signal the event so it
          drives the per-chunk commit. The happy path. A duplicate ack whose
          event was already consumed (identity still matches, event ``None``)
          simply records the mapping and no-ops — nothing is double-committed.
        * **Abandoned chunk** — the active identity no longer matches (the run
          wound down and ``finish_run`` nulled ``current_sync_id``), but the ack
          matches an ``abandoned_chunk`` stash left by a heartbeat timeout. The
          frontend already created the Steam shortcuts, so commit the delivered
          bindings here rather than discard them (#1052 / #1367). Popping the
          stash (``take_abandoned_chunk``) hands back the timed-out chunk's
          fetched rows; ``commit_unit_results`` upserts every fetched sibling
          (identity + metadata, the ``metadatum`` source) and binds only the
          acked representatives, reading the whole-unit staging still live on the
          box. Never passes a ``platform_stamp`` — a timed-out platform is
          incomplete and must not be stamped. A duplicate late ack finds the
          stash already cleared and falls through to *ignored*.
        * **Neither** — a stray/superseded ack (a late ack from a cancelled run,
          a different unit, or a stale chunk with no stash match). Ignored:
          neither recorded, signalled, nor committed, so it can never be
          credited to the wrong unit/run/chunk. Logged at debug, returns
          ``ignored: True`` with ``count: 0``.
        """
        box = self._sync_state
        if self._ack_matches_active_unit(run_id, unit_id, chunk_index):
            box.last_unit_results = dict(rom_id_to_app_id)
            if box.unit_complete_event is not None:
                box.unit_complete_event.set()
            self._logger.info(f"Unit results acknowledged: {len(rom_id_to_app_id)} shortcuts")
            return {"success": True, "count": len(rom_id_to_app_id)}

        stash = box.take_abandoned_chunk(run_id, unit_id, chunk_index)
        if stash is not None:
            await self.commit_unit_results(dict(rom_id_to_app_id), stash.chunk_rows)
            self._logger.info(f"Late ack recovered abandoned chunk: {len(rom_id_to_app_id)} shortcuts committed")
            return {"success": True, "count": len(rom_id_to_app_id)}

        self._logger.debug(
            f"Ignoring unit ack for run={run_id!r} unit={unit_id!r} chunk={chunk_index!r}: "
            f"active run={box.current_sync_id!r} unit={box.active_unit_id!r} chunk={box.active_chunk_index!r}, "
            f"no abandoned-chunk stash match"
        )
        return {"success": True, "count": 0, "ignored": True}

    def _ack_matches_active_unit(self, run_id, unit_id, chunk_index) -> bool:
        """True when the ack's run/unit/chunk identity matches the dispatched chunk.

        The frontend echoes back the ``run_id`` + ``unit_id`` + ``chunk_index``
        carried in the ``sync_apply_unit`` event. ``run_id`` and ``unit_id`` are
        compared by string value: the run id is a UUID string, and the unit id is
        JSON-shaped (a number for a platform, a string for a collection) so
        ``str()`` coercion on both sides is robust to int-vs-str drift on the
        wire; ``chunk_index`` is compared as an int. An ack is rejected when there
        is no active unit/chunk (``active_unit_id`` / ``active_chunk_index`` is
        ``None`` — the unit was cancelled or already committed), so a stray late
        ack from a cancelled run no-ops instead of being credited to a fresh run
        (#1041).
        """
        box = self._sync_state
        if box.active_unit_id is None or box.active_chunk_index is None:
            return False
        return (
            str(run_id) == str(box.current_sync_id)
            and str(unit_id) == str(box.active_unit_id)
            and int(chunk_index) == box.active_chunk_index
        )

    async def commit_unit_results(
        self,
        rom_id_to_app_id,
        unit_roms,
        platform_stamp: PlatformSyncState | None = None,
        collection_stamp: CollectionSyncState | None = None,
        fetch_id: str | None = None,
        delivered_through: DeliveryRoute | None = None,
    ):
        """Per-chunk commit: cover-path finalize then atomic ``roms`` + metadata upsert.

        Called once the frontend has acked an apply chunk's shortcuts — by
        :class:`ChunkDispatcher` on the happy path, or by
        :meth:`report_unit_results` itself on the heartbeat-timeout late-ack
        path (#1052). ``unit_roms`` is
        this chunk's slice of the live RomM fetch: a ``roms`` row is upserted for
        EVERY sibling in the slice (identity + version metadata, ADR-0021), but
        only the acked representatives carry a binding. The upsert and the
        cached-metadata stamp land in one write UoW (Rom row first, then
        ``rom_metadata`` — FK-safe), so a ROM and its metadata are always
        consistent across a crash, and each committed chunk is durable on its own.

        ``platform_stamp`` / ``collection_stamp`` are passed only by
        :class:`ChunkDispatcher` on the **final chunk of a platform /
        user-smart-collection unit** (ADR-0023, #742); the relevant one rides the same write UoW so the
        completion stamp is atomic with the chunk's rom upserts. The
        heartbeat-timeout late-ack path never sets either — a timed-out unit is
        incomplete and must not be stamped.

        ``fetch_id`` rides EVERY chunk of a platform unit (not only the stamped
        final one), marking each upserted row with the generation that fetch
        stamp records (#1504). Collection units and the late-ack path pass
        ``None``, leaving each row's existing generation untouched.

        ``delivered_through`` names the kind of unit committing the chunk, which
        each upserted row records (:meth:`Rom.record_delivery`). The late-ack
        path passes ``None`` and leaves each row's existing route untouched.

        Records every bound appId in the shared box so the stale-removal scan
        excludes appIds this run committed, whichever path drove the commit —
        a new rom_id reusing an old appId must not look stale (#1036).
        """
        await self._loop.run_in_executor(
            None,
            self._commit_unit_results_io,
            rom_id_to_app_id,
            unit_roms,
            platform_stamp,
            collection_stamp,
            fetch_id,
            delivered_through,
        )
        self._sync_state.committed_app_ids.update(int(aid) for aid in rom_id_to_app_id.values())

    # ── ``roms``-derived queries ─────────────────────────────────

    def _read_platform_name_cache(self, uow) -> dict[str, str]:
        """Decode the ``platform_slug → name`` cache, ``{}`` when absent/corrupt."""
        return decode_platform_names(uow.kv_config.get(PLATFORM_NAMES_KEY))

    def get_registry_platforms(self):
        """Return synced platforms from ``uow.roms`` (works offline, no RomM API call).

        Two counts per platform, and they answer different questions.
        ``count`` is bound ROMs — how many Steam shortcuts exist, which is
        what the Remove group acts on. ``reachable_count`` is how many of
        the platform's ROMs are reachable through one of those shortcuts,
        less the ones RomM no longer serves (:func:`_reachable_row_count`),
        which is what the header line states beside RomM's own total.
        Display names come from the ``platform_names`` cache refreshed each
        sync, degrading to the slug when a name is absent (RomM never seen
        for that slug).

        A platform with no bound row is omitted entirely, as it always has
        been. Nothing is lost with it: no group there holds a binding, so
        its ``reachable_count`` is 0 by construction, and the frontend
        reads an absent platform as zero on both counts.
        """
        return self._read_registry_platforms_io()

    def _read_registry_platforms_io(self):
        with self._uow_factory() as uow:
            names = self._read_platform_name_cache(uow)
            platforms: dict[str, dict[str, Any]] = {}
            rows: dict[str, list[Rom]] = {}
            by_slug: dict[str, list[Rom]] = {}
            for rom in uow.roms.iter_all():
                slug = rom.platform_slug
                display = names.get(slug, slug)
                rows.setdefault(display, []).append(rom)
                by_slug.setdefault(slug, []).append(rom)
                if rom.shortcut_app_id is None:
                    continue
                platforms.setdefault(display, {"count": 0, "slug": slug})
                platforms[display]["count"] += 1
            dropped = {
                rom_id
                for slug, slug_rows in by_slug.items()
                for rom_id in gone_from_romm_ids(slug_rows, uow.platform_sync_state.get(slug))
            }
        return {
            "platforms": [
                {
                    "name": k,
                    "slug": v["slug"],
                    "count": v["count"],
                    "reachable_count": _reachable_row_count(rows[k], dropped),
                }
                for k, v in sorted(platforms.items())
            ],
        }

    # ── Cache / stats ────────────────────────────────────────────

    def clear_sync_cache(self):
        """Force a full re-fetch AND a full re-apply on the next sync.

        The incremental-skip gate (fetcher) is stamp-based: its sole skip
        authority is the per-platform ``PlatformSyncState`` completion stamp
        (ADR-0023) and the per-collection ``CollectionSyncState`` stamp (#742),
        each of which is its own ``effective_last_sync``. So the full re-fetch is
        armed by clearing every stamp — no platform or collection then holds skip
        authority and each full-fetches next time. The recorded
        ``applied_launch_options`` are reset to NULL in the same write UoW, so
        the delta apply (ADR-0025) skips nothing on that run: "force" also means
        force past the per-item skip — the one repair path for Steam-side drift
        the recorded value cannot see (a manually edited or corrupted shortcut).
        The re-apply re-records every value.

        The ``SyncRun`` history is deliberately **preserved**: it feeds no skip
        gate (the fetcher never reads it), and it is the source of the "Last
        sync" display — the newest completed run's ``last_sync`` plus the newest
        terminal run's last-attempt hint, both read by ``get_sync_stats``.
        Deleting it forced nothing and only blanked the display to "Never" right
        after a reset (#1318). Keeping it means a post-force preview shows
        collections/platforms as "unchanged" (they exist in Steam; membership
        did not change), which is correct.

        What the preserved history does **not** carry is a resumable run. This
        clear takes away BOTH kinds of skip authority — every completion stamp and
        every recorded launch command — and the panel's resume offer reads those,
        never the history, so there is nothing left to continue here. That the two
        are cleared together is what makes the offer's rule hold; see
        :meth:`_read_sync_stats_io` (#1789).
        """
        with self._uow_factory() as uow:
            uow.platform_sync_state.clear()
            uow.collection_sync_state.clear()
            uow.roms.clear_all_applied_launch_options()
        self._logger.info("Sync cache cleared — next sync will fully re-fetch and re-apply")
        return {"success": True, "message": "Next sync will fully re-fetch and re-apply"}

    def get_sync_stats(self):
        enabled_platforms = self._settings.get("enabled_platforms", {})
        enabled_platform_count = sum(1 for v in enabled_platforms.values() if v)
        enabled_collections = self._settings.get("enabled_collections", {})
        if isinstance(enabled_collections, dict):
            enabled_collection_count = sum(
                1 for bucket in enabled_collections.values() if isinstance(bucket, dict) for v in bucket.values() if v
            )
        else:
            enabled_collection_count = 0
        stats = self._read_sync_stats_io()
        return {
            "last_sync": stats.last_sync,
            "last_attempt": stats.last_attempt,
            "platforms": enabled_platform_count,
            "collections": enabled_collection_count,
            "roms": stats.rom_count,
            "total_shortcuts": stats.rom_count,
            "resumable_games": stats.resumable_games,
            "has_completion_stamp": stats.has_completion_stamp,
        }

    def _read_sync_stats_io(self) -> _SyncStatsReading:
        """Read the run history, the bound-ROM count and the surviving skip authority from SQLite.

        ``last_sync`` is the ``finished_at`` of the latest completed ``SyncRun``;
        ``last_attempt`` surfaces the newest cancelled/interrupted/paused/errored run when
        it is newer than that (see :meth:`_last_attempt`); the ROM count is the
        bound-shortcut count in ``roms``.

        The last two are what makes the panel's resume offer honest, and
        they are two facts rather than one because Tender keeps **two** kinds
        of durable progress. A **completion stamp** makes the next run pass over a
        whole platform or collection at fetch time; a **recorded launch command**
        makes it pass over one game at apply time. Both survive a stopped run and
        both are cleared together by Force Full Sync, which is the entire #1789
        defect: the offer used to be derived from the run history, which Force
        Full Sync deliberately preserves (#1318), so it outlived the progress it
        promised to continue.

        Neither fact subsumes the other, so the offer reads both. A run cancelled
        inside its very first platform unit has written shortcuts and recorded
        their launch commands but reached no final chunk, so it holds recorded
        games and no stamp — and it is a genuine resume, because the next run
        really does less work. In the other direction, a row predating migration
        015 carries a NULL recorded value while its platform's stamp survives, so
        an upgraded install can hold stamps with zero recorded games and still
        skip those platforms wholesale. Counting only one kind declares one of
        these two a fresh start when it is not.

        Everything here rides in the read UoW that already scans ``roms``: the
        recorded-game count is one more condition inside that same loop (``iter_all``
        already selects ``applied_launch_options``), and each stamp probe is a
        ``SELECT 1 … LIMIT 1`` over a leaf table. The panel mount pays nothing
        measurable for any of it.
        """
        with self._uow_factory() as uow:
            completed = uow.sync_runs.get_latest_completed()
            terminal = uow.sync_runs.get_latest_terminal()
            rom_count = 0
            resumable_games = 0
            for rom in uow.roms.iter_all():
                if rom.shortcut_app_id is None:
                    continue
                rom_count += 1
                # Bound AND recorded, never recorded alone. ``classify_roms``
                # (``domain/sync_diff.py``) sends an unbound row down the NEW branch
                # — ``if not reg or not reg.get("app_id")`` — before it ever reads
                # the recorded value, so a recorded command with no shortcut is not
                # skip authority: the next run has to mint the shortcut regardless.
                # Requiring the binding is therefore what makes this count fall to
                # zero after Data Management's remove-all by construction: unbinding
                # keeps the row and its recorded command on purpose (ADR-0007), so
                # a count over all rows would keep offering a resume of shortcuts
                # that no longer exist. Do not "simplify" this to every row.
                if rom.applied_launch_options is not None:
                    resumable_games += 1
            has_completion_stamp = uow.platform_sync_state.has_any() or uow.collection_sync_state.has_any()
        return _SyncStatsReading(
            last_sync=completed.finished_at if completed is not None else None,
            last_attempt=self._last_attempt(completed, terminal),
            rom_count=rom_count,
            resumable_games=resumable_games,
            has_completion_stamp=has_completion_stamp,
        )

    @staticmethod
    def _last_attempt(completed: SyncRun | None, terminal: SyncRun | None) -> dict[str, str] | None:
        """The newest cancelled/interrupted/paused/errored run, but only when it is newer than the last completed one.

        A run that ended without completing (cancelled, interrupted, paused, or errored)
        still applied shortcuts; without this the last-completed-only ``last_sync``
        read reports "Never" even after thousands of games synced. Returns ``None``
        when the newest terminal run completed cleanly (``last_sync`` already covers
        it) or when a completed run is at least as recent as the attempt.
        ``finished_at`` is guaranteed set on a terminal run
        (mark_cancelled/mark_interrupted/mark_errored stamp it).
        """
        if terminal is None or terminal.status == "completed":
            return None
        if completed is not None and (terminal.finished_at or "") <= (completed.finished_at or ""):
            return None
        return {"finished_at": terminal.finished_at or "", "status": terminal.status}

    def get_sync_runs(self) -> dict[str, Any]:
        """Return the newest recorded sync runs, newest first.

        At most :data:`SYNC_RUN_HISTORY_LIMIT` runs of any status, each carrying
        its plan, its outcome and its timestamps verbatim from the ``SyncRun``
        aggregate. A field the run never recorded stays ``None`` — a run that
        did not complete has no completed platform/collection lists, and its
        status is what says so, so an empty list would claim it finished having
        synced nothing.
        """
        return {"success": True, "runs": self._read_sync_runs_io()}

    def _read_sync_runs_io(self) -> list[dict[str, Any]]:
        with self._uow_factory() as uow:
            runs = uow.sync_runs.iter_recent(SYNC_RUN_HISTORY_LIMIT)
        return [
            {
                "id": run.id,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status,
                "platforms_planned": run.platforms_planned,
                "roms_planned": run.roms_planned,
                "platforms_completed": run.platforms_completed,
                "collections_completed": run.collections_completed,
                "error": run.error,
            }
            for run in runs
        ]


def _reachable_row_count(rows: list[Rom], dropped: set[int]) -> int:
    """How many of *rows* the platform count states: the reachable ones, less *dropped*.

    What a binding reaches is :func:`domain.sibling_resolution.reachable_rom_ids`.
    *dropped* is the rows the last completed fetch of their platform did not
    return, and the rows carrying a gone-from-RomM mark
    (:func:`domain.fetch_generation.gone_from_romm_ids`) — the same set the
    removed-game cleanup lists. They stay
    reachable and are left out because RomM no longer serves them — the rule and
    its edges are ``docs/architecture/qam-panel.md``'s. They are excluded from
    the count and not from the GROUPING, because the group's membership and its
    binding are facts about every row: a group whose binding sits on a dropped
    row still reaches its surviving versions through that shortcut.
    """
    return len(reachable_rom_ids(rows) - dropped)
