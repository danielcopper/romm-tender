"""SyncPreview — the read-only preview of a sync, its staged snapshot and its cancel.

The preview reads every unit into one union, collapses and classifies it as the
apply's units would, and stages the answer in the state box for
:meth:`SyncOrchestrator.sync_apply_delta` to admit an apply against. It claims
and releases the run slot itself, through the box's verbs, so a preview and a
run are never in flight at once. It writes nothing to the database: what it
knows comes from the fetchers, the :class:`ShortcutLaunchResolver` and
:class:`LocalLibraryReader`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.collection_label import steam_collection_key
from domain.collection_skip import apply_stamps
from domain.shortcut_data import build_shortcuts_data
from domain.sibling_resolution import AUTO_REGION
from domain.sync_diff import (
    classify_roms,
    collapse_sibling_groups,
    compute_collection_diff,
    compute_platform_collection_diff,
    count_disabled_platform_removals,
    platform_breakdown,
    rebuilt_rows_filed_as_kept,
)
from domain.sync_run_kind import SyncRunKind
from domain.sync_stage import SyncStage
from domain.sync_state import SyncCancelled
from lib.errors import Refused, classify_error
from services.library._preview_union import collapse_counting_platform_rebinds
from services.library._stale_rows import preview_group_keys, settle_stale_rows, stale_rows_of, with_kept_platform_names
from services.library._state import SYNC_CANCELLED, SYNC_IN_PROGRESS
from services.library._unit_roms import (
    collection_membership_key,
    record_collection_membership,
    stamp_component_group_keys,
)

if TYPE_CHECKING:
    import asyncio
    import logging

    from domain.work_unit import WorkUnit
    from services.library._state import CollectionMembership, LibrarySyncStateBox
    from services.library.collection_fetcher import CollectionFetcher
    from services.library.cover_preparer import CoverPreparer
    from services.library.fetcher import LibraryFetcher
    from services.library.local_library_reader import LocalLibraryReader
    from services.library.session_budget import SessionBudgetMonitor
    from services.library.shortcut_launch_resolver import ShortcutLaunchResolver
    from services.library.sync_progress import SyncProgress
    from services.protocols import Clock, ShortcutArtworkJob, UuidGen


@dataclass(frozen=True)
class SyncPreviewConfig:
    """Frozen wiring bundle handed to ``SyncPreview.__init__``.

    Holds the live settings, the loop and logger, the launcher path the
    preview's shortcuts are built with, the Clock/UuidGen seams the run, the
    snapshot and its deadline are stamped by, the shared
    :class:`LibrarySyncStateBox`, the :class:`LibraryFetcher` and
    :class:`CollectionFetcher` peers every unit is fetched through, the
    :class:`ShortcutLaunchResolver` that answers each bake's installed path and
    emulator, the :class:`LocalLibraryReader` every question about this
    device's own record of the library goes to, the :class:`SessionBudgetMonitor`
    the post-preview prognosis is asked of, the :class:`CoverPreparer` that
    counts the cover-only work, the :class:`SyncProgress` the preview's frames go
    out through, and the ``artwork_job``, asked for a run when the preview ends:
    it stops while a run is in flight.
    """

    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    launcher_exe: str
    clock: Clock
    uuid_gen: UuidGen
    sync_state_box: LibrarySyncStateBox
    fetcher: LibraryFetcher
    collection_fetcher: CollectionFetcher
    shortcut_launch_resolver: ShortcutLaunchResolver
    local_library_reader: LocalLibraryReader
    session_budget: SessionBudgetMonitor
    cover_preparer: CoverPreparer
    progress: SyncProgress
    artwork_job: ShortcutArtworkJob


class SyncPreview:
    def __init__(self, *, config: SyncPreviewConfig) -> None:
        self._settings = config.settings
        self._loop = config.loop
        self._logger = config.logger
        self._launcher_exe = config.launcher_exe
        self._clock = config.clock
        self._uuid_gen = config.uuid_gen
        self._sync_state = config.sync_state_box
        self._fetcher = config.fetcher
        self._collection_fetcher = config.collection_fetcher
        self._shortcut_launch_resolver = config.shortcut_launch_resolver
        self._local_library_reader = config.local_library_reader
        self._session_budget = config.session_budget
        self._cover_preparer = config.cover_preparer
        self._progress = config.progress
        self._artwork_job = config.artwork_job

    async def sync_preview(self):
        """Read-only preview: paginate every unit, classify, return the summary.

        Does NOT persist ``rom_metadata`` — the metadata stamp happens in
        the reporter's per-unit commit, after the frontend acknowledges
        shortcuts for that unit. Stamping during preview would persist the
        registry-reconstructed thin ROMs from the per-unit incremental-skip
        path, which carry no ``metadatum`` (#738).

        Refuses with ``sync_in_progress`` while another run holds the slot, and
        with ``cancelled`` when the user cancels. Any other failure drops any
        staged preview, so none stays appliable, ends the progress with an error
        frame, and reaches the caller.
        """
        box = self._sync_state
        run_id = self._uuid_gen.uuid4()
        if not box.try_begin_run(run_id, kind=SyncRunKind.PREVIEW):
            raise Refused("sync_in_progress", SYNC_IN_PROGRESS)
        box.sync_last_heartbeat = self._clock.monotonic()
        try:
            await self._progress.emit_progress(SyncStage.DISCOVERING, message="Fetching platforms...")
            work_queue = await self._fetcher.build_work_queue()

            all_roms: list[dict[str, Any]] = []
            platform_rom_ids: set[int] = set()
            collection_memberships: dict[tuple[str, str], CollectionMembership] = {}
            synced_rom_ids: set[int] = set()
            skipped_rom_ids: set[int] = set()
            full_fetched_platforms: dict[str, list[dict[str, Any]]] = {}
            restamp_collections: set[tuple[str, str]] = set()

            total_units = len(work_queue)
            for unit_index, unit in enumerate(work_queue, 1):
                if box.is_cancelling():
                    raise SyncCancelled(SYNC_CANCELLED)
                await self._progress.emit_progress(
                    SyncStage.FETCHING,
                    current=len(all_roms),
                    message=f"Fetching {unit.name}... ({unit_index}/{total_units})",
                    step=unit_index,
                    total_steps=total_units,
                )
                await self._fetch_preview_unit(
                    unit,
                    all_roms,
                    platform_rom_ids,
                    synced_rom_ids,
                    collection_memberships,
                    skipped_rom_ids=skipped_rom_ids,
                    full_fetched_platforms=full_fetched_platforms,
                    restamp_collections=restamp_collections,
                    progress_step=unit_index,
                    progress_total_steps=total_units,
                )

            sources = await self._loop.run_in_executor(None, self._shortcut_launch_resolver.do_read_sources)
            installed_paths = await self._loop.run_in_executor(
                None, self._shortcut_launch_resolver.do_scan_installed_paths, sources
            )
            core_overrides = await self._loop.run_in_executor(
                None, self._shortcut_launch_resolver.do_build_core_overrides, all_roms, sources
            )
            # Stamp each fresh ROM's component sibling-group key before the build so
            # the collapse below groups games, not dumps. The preview union is a
            # complete view of every enabled platform's groups; the DB's persisted
            # keys seed a member edging into a resident sibling on a skipped platform.
            resident_keys = await self._loop.run_in_executor(
                None, self._local_library_reader.do_read_resident_group_keys
            )
            stamp_component_group_keys(all_roms, resident_keys)
            shortcuts_data = build_shortcuts_data(all_roms, self._launcher_exe, installed_paths, core_overrides)
            platform_name_set = {u.name for u in work_queue if u.type == "platform"}
            slug_to_name = {u.slug: u.name for u in work_queue if u.type == "platform" and u.slug}
            registry, last_synced_platforms, last_synced_collections = await self._loop.run_in_executor(
                None, self._local_library_reader.do_read_preview_baseline, slug_to_name
            )
            # Enabled platforms whose skip only a fresh stamp can restore: no
            # stamp (#1416 — a late-ack-recovered platform is complete but
            # unstamped), or a full fetch here whose answer the stamp does not
            # match (#743). Without an apply the gate full-fetches them forever.
            # Counted here (side-effect-free read) so the preview can still offer
            # Apply on an otherwise-empty delta — the apply's 0-delta empty final
            # chunk re-writes the stamp and records a fresh SyncRun.
            restamp_platform_count = await self._loop.run_in_executor(
                None,
                self._local_library_reader.do_count_platforms_to_restamp,
                set(slug_to_name),
                full_fetched_platforms,
            )
            # Collapse to one entry per sibling group (ADR-0021) so the preview
            # counts games, not individual dumps: a multi-version game becomes one
            # shortcut and its unbound siblings stop reading as perpetual "new".
            # The view is chosen per group as the apply's units have it, so the
            # rebinds the view decides are counted as the apply makes them (the
            # #1292 bug class). A group is per-platform: one a platform unit
            # delivers a member of is complete, the view that unit collapses with
            # (a unit skipped as unchanged collapses nothing, and its groups stay
            # complete here), and one only collections reach is the partial view a
            # collection unit has (#2536). A row a collection skip rebuilt in a
            # group a platform unit delivers is collapsed as that unit has it.
            whole_groups = {sd.get("sibling_group_key") for sd in shortcuts_data if sd["rom_id"] in platform_rom_ids}
            emitted, classify_skipped = collapse_counting_platform_rebinds(
                shortcuts_data,
                lambda entries: collapse_sibling_groups(
                    entries,
                    registry,
                    set(installed_paths),
                    complete_group_view=whole_groups,
                    preferred_region=self._settings.get("preferred_region", AUTO_REGION),
                ),
                skipped_rom_ids=skipped_rom_ids,
                platform_rom_ids=platform_rom_ids,
            )
            new, changed, unchanged_ids, stale, _disabled_count = classify_roms(
                emitted,
                registry,
                platform_name_set,
                skipped_rom_ids=classify_skipped,
            )
            collection_listed = await self._loop.run_in_executor(
                None, self._local_library_reader.do_read_collection_listed_rom_ids
            )
            # The marks are discarded: docs/architecture/backend-architecture.md,
            # "The stale removal keeps a game gone from RomM".
            removals, kept, _marks = await settle_stale_rows(
                stale_rows_of(stale, registry),
                ask_romm=self._fetcher.ask_romm_for_roms,
                logger=self._logger,
                read_platform_slugs=set(slug_to_name),
                listed_platform_slugs=box.run_listed_platform_slugs,
                collection_listed_rom_ids=collection_listed,
                synced_group_keys=preview_group_keys(synced_rom_ids, resident_keys, all_roms),
            )
            removed_ids = {row.rom_id for row in removals}
            stale = [rid for rid in stale if rid in removed_ids]
            rebuilt = {rom["id"]: rom["platform_slug"] for rom in all_roms if rom["id"] in classify_skipped}
            kept += stale_rows_of(rebuilt_rows_filed_as_kept(rebuilt, set(slug_to_name), platform_rom_ids), registry)
            platform_names = await with_kept_platform_names(
                {row.platform_slug for row in [*kept, *removals]} | {sd["platform_slug"] for sd in emitted},
                box.run_listed_platform_names,
                read_kept_names=lambda slugs: self._loop.run_in_executor(
                    None, self._local_library_reader.do_read_kept_platform_names, slugs
                ),
            )
            disabled_count = count_disabled_platform_removals(stale, registry, platform_name_set)
            # Cover-only work (#1386): count the bound fetched ROMs whose server
            # cover fingerprint changed and whose cover is not set in Steam, by
            # the SAME rule the apply-path invalidation pass refreshes by (what
            # it reads: ArtworkService.count_cover_refreshes). Runs over the
            # raw union (platform + collection units alike), not the collapsed
            # delta: covers are per-ROM, cover-blind classify_roms (ADR-0025)
            # stays untouched, and without this an empty shortcut delta would
            # short-circuit the apply and strand a changed cover forever.
            cover_refresh_count = await self._cover_preparer.count_cover_refreshes(all_roms, registry)

            # Post-preview session-budget prognosis (#1383): warn up front when the
            # planned work would walk the renderer heap past the budget ceiling. The
            # gate is fail-open, so an unreadable renderer simply yields no warning.
            #
            # Taken here rather than after the DONE frame so that nothing awaits
            # between the cancel checkpoint below and the staging that follows it.
            pause_likely = await self._session_budget.predict_pause_likely(
                new_items=len(new), changed_items=len(changed)
            )

            # Final cancel checkpoint: a cancel can land after the unit loop's
            # last per-unit check but before the preview is staged. Re-check
            # here so a late cancel routes into the SyncCancelled branch (which
            # leaves ``pending_delta`` None) instead of staging a delta the
            # user already cancelled (#1202).
            if box.is_cancelling():
                raise SyncCancelled(SYNC_CANCELLED)

            preview_id = self._uuid_gen.uuid4()
            created_at = self._clock.time()
            platforms_count = sum(1 for u in work_queue if u.type == "platform")
            collections_count = sum(1 for u in work_queue if u.type == "collection")
            naming_mode = self._settings.get("collection_naming_mode", "merge")

            answer = {
                "success": True,
                "pause_likely": pause_likely,
                "summary": {
                    "new_count": len(new),
                    "changed_count": len(changed),
                    "unchanged_count": len(unchanged_ids),
                    "remove_count": len(stale),
                    "disabled_platform_remove_count": disabled_count,
                    # Bound ROMs whose server-side cover changed (#1386) — work the
                    # apply run performs even when the shortcut delta is empty, so
                    # the frontend must offer Apply on a cover-only preview instead
                    # of short-circuiting on "no changes". Additive: old consumers
                    # ignore it.
                    "cover_refresh_count": cover_refresh_count,
                    # Enabled platforms whose skip only a fresh stamp can restore
                    # (#1416, #743), and the collections the gate refused for a
                    # reason a fresh stamp settles whose apply will stamp them
                    # (#2477 D3). Each one's apply is a 0-delta empty final chunk
                    # that re-writes the stamp and records a fresh SyncRun, so a
                    # restamp-only preview must still offer Apply instead of
                    # short-circuiting on "no changes". Additive: old consumers
                    # ignore them.
                    "restamp_platform_count": restamp_platform_count,
                    "restamp_collection_count": len(restamp_collections),
                    # Scope of the run (#29): how many platforms / collections this
                    # sync spans, shown as an always-on informational line
                    # independent of the change diffs.
                    "sync_platform_count": platforms_count,
                    "sync_collection_count": collections_count,
                    "collection_diff": compute_collection_diff(
                        {
                            steam_collection_key(m.name, m.kind, m.virtual_type, naming_mode)
                            for m in collection_memberships.values()
                        },
                        last_synced_collections,
                    ),
                    # Per-platform rows behind the library-wide counts above,
                    # regrouped from the same three buckets and the same
                    # registry — no further read, so the preview stays
                    # side-effect-free — and named as the diff below files.
                    "platform_breakdown": platform_breakdown(
                        new, changed, stale, registry, slug_to_name, platform_names
                    ),
                    "platform_collection_diff": compute_platform_collection_diff(
                        emitted,
                        platform_rom_ids,
                        last_synced_platforms,
                        self._settings.get("collection_create_platform_groups", False),
                        platform_names,
                        kept_platform_slugs={row.platform_slug for row in kept},
                    ),
                },
                "new_names": [s["name"] for s in new[:10]],
                "changed_names": [s["name"] for s in changed[:10]],
                "preview_id": preview_id,
                # Absolute wall-clock deadline (epoch seconds) the backend stops
                # accepting this snapshot at. Absolute rather than a remaining-
                # seconds count because the panel runs on the same machine and the
                # same clock, and a deadline survives the Deck suspending where a
                # locally counted-down number does not.
                "expires_at": box.preview_deadline(created_at),
            }
            box.stage_preview(
                preview_id=preview_id,
                created_at=created_at,
                answer=answer,
            )

            await self._progress.emit_progress(SyncStage.DONE, message="Preview ready", running=False)

            return answer
        except SyncCancelled as e:
            # A user's cancel is the use case's own outcome, so it leaves as the
            # ``cancelled`` refusal: ``SyncCancelled`` itself would reach the
            # panel as a transport error. The clause order is what routes it
            # here — SyncCancelled is an Exception, so it must stay above the
            # generic ``except Exception`` below.
            box.discard_preview()
            await self._progress.emit_cancelled(SYNC_CANCELLED)
            raise Refused("cancelled", SYNC_CANCELLED) from e
        except Exception as e:
            box.discard_preview()
            _, message = classify_error(e)
            await self._progress.emit_progress(SyncStage.ERROR, message=message, running=False)
            raise
        finally:
            box.finish_run(run_id)
            self._artwork_job.request_run()

    async def _fetch_preview_unit(
        self,
        unit: WorkUnit,
        all_roms: list[dict[str, Any]],
        platform_rom_ids: set[int],
        synced_rom_ids: set[int],
        collection_memberships: dict[tuple[str, str], CollectionMembership],
        *,
        skipped_rom_ids: set[int],
        full_fetched_platforms: dict[str, list[dict[str, Any]]],
        restamp_collections: set[tuple[str, str]],
        progress_step: int = 0,
        progress_total_steps: int = 0,
    ) -> None:
        """Fetch one work unit's ROMs and fold them into the preview accumulators.

        Platform units add every ROM to ``platform_rom_ids`` and
        ``synced_rom_ids``; collection units record their full membership
        under a collision-free ``(collection_kind, collection_id)`` key (with the
        name in the value), so same-named collections never overwrite each other
        (#1503). ``all_roms`` is extended in both cases, and ``skipped_rom_ids``
        too with the ROMs of a unit the fetcher reports as skipped, platform or
        collection alike — the units the apply will skip. A platform unit the
        fetcher did not skip lands in ``full_fetched_platforms`` under its slug,
        so the restamp count can judge its stamp against what the fetch returned.
        A collection unit lands in ``restamp_collections`` under its membership
        key when the fetcher reports its skip refused for a reason a fresh stamp
        settles and the apply will stamp it (``domain/collection_skip.apply_stamps``,
        #2477 D3). Mutates the passed-in accumulators in place. ``progress_step`` /
        ``progress_total_steps`` thread the unit's coarse position into the
        fetcher's per-page ``fetching`` frames (on top of the per-unit frame
        the preview loop already emits).
        """
        if unit.type == "platform":
            unit_roms, skipped = await self._fetcher.fetch_platform_unit(
                unit, progress_step=progress_step, progress_total_steps=progress_total_steps
            )
            for rom in unit_roms:
                platform_rom_ids.add(rom["id"])
                synced_rom_ids.add(rom["id"])
            if not skipped and unit.slug:
                full_fetched_platforms[unit.slug] = unit_roms
        else:
            unit_roms, all_collection_rom_ids, skipped, settles = await self._collection_fetcher.fetch_collection_unit(
                unit, synced_rom_ids, progress_step=progress_step, progress_total_steps=progress_total_steps
            )
            record_collection_membership(collection_memberships, unit, all_collection_rom_ids)
            if settles and apply_stamps(unit.collection_kind, unit.collection_updated_at, all_collection_rom_ids):
                restamp_collections.add(collection_membership_key(unit))
        all_roms.extend(unit_roms)
        if skipped:
            skipped_rom_ids.update(rom["id"] for rom in unit_roms)

    def sync_cancel_preview(self):
        self._sync_state.discard_preview()
        return {"success": True}

    def get_pending_preview(self):
        """Hand back the staged preview answer, so a remounted panel can show its card again.

        ``preview: None`` — nothing staged, a snapshot the read aged out, or a
        run in flight (which withholds it rather than discarding it) — is a
        normal answer, never the failure shape. Starts and cancels no run.
        """
        delta = self._sync_state.read_restorable_preview(self._clock.time())
        return {"success": True, "preview": dict(delta.answer) if delta else None}
