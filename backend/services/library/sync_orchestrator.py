"""The sync run's lifecycle and control, its loop over the work units, and its finalize.

Owns the paths that start an apply run or stop one from outside — starting a
sync, applying a staged preview, the user's Cancel, the heartbeat route,
shutdown — and the apply run itself: the plan, the loop that hands each unit to :class:`UnitApplier`, the stale removal
and the Steam collections at its end, and its terminal status. It is the one
part of the sync that starts a task. Of the heartbeat clock this module holds
the stamp taken when a run is admitted (as :class:`SyncPreview` does for a
preview) and the frontend's ``sync_heartbeat`` route. The stamp that opens a
chunk's 60-second window, and the wait that measures against it, belong to
:class:`ChunkDispatcher`, so the window a timeout is judged by starts at a
chunk's emit rather than at run admission. One unit's apply belongs to
:class:`UnitApplier`; the preview, its staged snapshot and its cancel to
:class:`SyncPreview`; the ``sync_progress`` frames and the status read to
:class:`SyncProgress`. Anything that fetches ROMs belongs in
:class:`LibraryFetcher`; anything that commits an applied chunk belongs in
:class:`SyncReporter`, and the run's closing registry write and terminal signals
in :class:`RunFinalizer`; Steam's renderer memory belongs in
:class:`SessionBudgetMonitor`; the run's own ``SyncRun`` row — opened at the
plan, closed once by the status that ended the run — belongs in
:class:`SyncRunRecorder`; reading the registry and the completion stamps into
the projections these decisions are made against belongs in
:class:`LocalLibraryReader`. **Which** terminal status a stopped run is
recorded with belongs here: the priority a pause, a heartbeat timeout and the
user's Cancel are read in follows from what this pipeline observed while it
ran. The recorder writes that answer, never picks it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.session_budget import post_run_advisory, session_memory_delta
from domain.sync_diff import (
    StaleRow,
    group_keys_of,
    rebuilt_rows_filed_as_kept,
    select_stale_removals,
    without_running_groups,
)
from domain.sync_run_kind import SyncRunKind
from domain.sync_stage import SyncStage
from domain.sync_state import SyncCancelled
from lib.background_tasks import BackgroundTasks
from lib.errors import Refused, classify_error
from services.library._stale_rows import settle_stale_rows
from services.library._state import SYNC_CANCELLED, SYNC_IN_PROGRESS
from services.library._unit_roms import collection_membership_key
from services.library.session_budget import SYNC_PAUSED_BUDGET, SessionBudgetMonitor

if TYPE_CHECKING:
    import logging

    from domain.work_unit import WorkUnit
    from services.library._state import CollectionMembership, LibrarySyncStateBox
    from services.library.collection_fetcher import CollectionDelivery
    from services.library.fetcher import LibraryFetcher
    from services.library.local_library_reader import LocalLibraryReader
    from services.library.run_finalizer import RunFinalizer
    from services.library.shortcut_launch_resolver import ShortcutLaunchResolver
    from services.library.sync_progress import SyncProgress
    from services.library.sync_run_recorder import SyncRunRecorder
    from services.library.unit_apply import UnitApplier
    from services.protocols import (
        Clock,
        ConflictRules,
        EventEmitter,
        RunningRomIdsFn,
        ShortcutArtworkJob,
        UuidGen,
    )


# Terminal reason when the run died externally (heartbeat timeout — the
# frontend crashed or reloaded) rather than by the user's Cancel. Stored in
# ``sync_runs.error`` via ``mark_interrupted``; the status split lets the UI
# report "(interrupted)" instead of "(cancelled)" for a crash.
_SYNC_INTERRUPTED = "Sync interrupted (Steam UI stopped responding)"


@dataclass(frozen=True)
class SyncOrchestratorConfig:
    """Frozen wiring bundle handed to ``SyncOrchestrator.__init__``.

    Holds runtime infrastructure (loop, logger), the event emitter the
    ``sync_plan`` and ``sync_stale`` events go out through, the Clock/UuidGen
    test seams, the shared :class:`LibrarySyncStateBox`, the
    :class:`LibraryFetcher` peer the orchestrator takes the work queue from and
    asks RomM about a stale row through, and the :class:`UnitApplier` each unit
    is applied by. The ``run_finalizer`` finalises the run's collections and emits
    ``sync_complete``. The ``shortcut_launch_resolver`` peer is asked for the one
    reading of the emulator sources a run takes. The ``local_library_reader``
    peer answers what the run asks of this device's own record of the library —
    the members the collections listed at their last completed apply, the
    resident sibling-group keys and the stale-row scan — each in its own short
    read Unit of Work. The ``session_budget`` seam stamps the run-start baseline
    and takes the terminal reading the memory delta is drawn from. The
    ``sync_run_recorder`` peer writes the run's ``SyncRun`` row: this module
    decides which terminal status a stopped run earns, and hands that decision
    over as a method call. The ``conflict_rules`` are what a ``sync_stale``
    event that removes shortcuts takes its lease through. The ``artwork_job`` is
    asked for a run at every run's end: it stops while a run is in flight. The
    ``progress`` part emits the run's ``sync_progress`` frames. The
    ``running_rom_ids`` reading is taken once more before the finalize removes
    a converged shortcut.
    """

    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    emit: EventEmitter
    clock: Clock
    uuid_gen: UuidGen
    sync_state_box: LibrarySyncStateBox
    fetcher: LibraryFetcher
    unit_applier: UnitApplier
    run_finalizer: RunFinalizer
    shortcut_launch_resolver: ShortcutLaunchResolver
    local_library_reader: LocalLibraryReader
    session_budget: SessionBudgetMonitor
    sync_run_recorder: SyncRunRecorder
    conflict_rules: ConflictRules
    artwork_job: ShortcutArtworkJob
    progress: SyncProgress
    running_rom_ids: RunningRomIdsFn


@dataclass(frozen=True)
class FinalizeOutcome:
    """What ``_finalize_per_unit`` produces for the run's terminal phase.

    Carries the run finalizer's collection maps (the completed-run ``SyncRun`` write
    needs their keys), the rows whose shortcut the run removes (the terminal
    frame counts them as gone, though they stay bound until the frontend reports
    the removal) and the two session-budget surfacing values computed at
    finalize (``interrupt_reason`` / ``restart_recommended``), so the orchestrator
    can persist the terminal ``SyncRun`` status FIRST and only THEN emit
    ``sync_complete`` from these — the "emit last" ordering that closes the
    emit-before-persist race (#39).
    """

    platform_app_ids: dict[str, list[int]]
    romm_collection_app_ids: dict[str, list[int]]
    removed_rom_ids: frozenset[int]
    interrupt_reason: str | None
    restart_recommended: bool


class SyncOrchestrator:
    def __init__(self, *, config: SyncOrchestratorConfig) -> None:
        self._loop = config.loop
        self._logger = config.logger
        self._emit = config.emit
        self._clock = config.clock
        self._uuid_gen = config.uuid_gen
        self._sync_state = config.sync_state_box
        self._fetcher = config.fetcher
        self._unit_applier = config.unit_applier
        self._run_finalizer = config.run_finalizer
        self._shortcut_launch_resolver = config.shortcut_launch_resolver
        self._local_library_reader = config.local_library_reader
        self._session_budget = config.session_budget
        self._sync_run_recorder = config.sync_run_recorder
        self._rules = config.conflict_rules
        self._artwork_job = config.artwork_job
        self._progress = config.progress
        self._running_rom_ids = config.running_rom_ids
        self._tasks = BackgroundTasks(logger=config.logger)

    # ── Sync control ─────────────────────────────────────────────

    def start_sync(self):
        box = self._sync_state
        run_id = self._uuid_gen.uuid4()
        if not box.try_begin_run(run_id, kind=SyncRunKind.APPLY):
            raise Refused("sync_in_progress", SYNC_IN_PROGRESS)
        box.sync_last_heartbeat = self._clock.monotonic()
        self._tasks.spawn(self._do_sync_per_unit(), "library sync run")
        return {"success": True, "message": "Sync started"}

    def cancel_sync(self, run_id=None):
        """Request cancellation of the active sync, scoped to *run_id*.

        A truthy *run_id* must match the active run's ``current_sync_id`` for the
        cancel to take effect — a cancel click meant for run N can land after run
        N finalized to IDLE and run N+1 started fresh, and an unscoped cancel
        would wrongly abort run N+1; the ack path's ``_ack_matches_active_unit``
        checks the run the same way. A falsy/``None`` *run_id* cancels
        **unconditionally** — legacy callers that pass no id, and the "no active
        run id yet" safety case — so cancel is never made less reliable.
        """
        box = self._sync_state
        self._logger.info(
            f"cancel_sync: run_id={run_id!r} active run={box.current_sync_id!r} state={box.sync_state.value}"
        )
        outcome = box.request_cancel(run_id)
        if outcome == "no_sync":
            return {"success": True, "message": "No sync in progress"}
        if outcome == "stale":
            self._logger.info(f"Ignoring stale cancel for run={run_id!r}; active run={box.current_sync_id!r}")
            return {"success": True, "message": "Cancel ignored (stale run)"}
        return {"success": True, "message": "Sync cancelling..."}

    def sync_heartbeat(self):
        """Called by frontend during shortcut application to refresh the per-unit heartbeat clock."""
        self._sync_state.sync_last_heartbeat = self._clock.monotonic()
        return {"success": True}

    async def shutdown(self) -> None:
        """Set the run's cancel flag, then cancel the run and wait until it has ended, with no grace wait between."""
        self._sync_state.request_cancel()
        await self._tasks.shutdown()

    # ── Apply ────────────────────────────────────────────────────

    async def sync_apply_delta(self, preview_id):
        box = self._sync_state
        if not box.matches_preview(preview_id):
            raise Refused("stale_preview", "Preview expired, please re-sync")
        # The read drops an over-age snapshot on its way out, so a repeat apply
        # can't pick it up.
        if box.read_fresh_preview(self._clock.time()) is None:
            raise Refused("stale_preview", "Preview is older than 30 minutes, please re-run sync")
        # Admission guard: a rapid second apply (or an apply landing while a
        # sync is already in flight) must be rejected without consuming the
        # staged delta, so the still-valid preview survives for the legitimate
        # apply (#1202). Claim the run slot BEFORE discarding the preview.
        run_id = self._uuid_gen.uuid4()
        if not box.try_begin_run(run_id, kind=SyncRunKind.APPLY):
            raise Refused("sync_in_progress", SYNC_IN_PROGRESS)
        box.discard_preview()
        box.sync_last_heartbeat = self._clock.monotonic()

        self._tasks.spawn(self._do_sync_per_unit(), "library sync run")

        return {"success": True, "message": "Applying changes"}

    # ── Per-unit pipeline ────────────────────────────────────────

    async def _do_sync_per_unit(self):
        """Per-unit sync pipeline (Phase 0 + per-unit dispatch + finalize).

        Builds a work queue, opens a ``SyncRun`` for the planned counts,
        processes each platform/collection unit to completion (fetch ->
        shortcuts -> artwork -> apply -> per-unit ``roms`` + ``rom_metadata``
        commit) before moving on, then emits stale-removal + Steam-
        collection mappings + ``sync_complete`` at the end and writes the
        ``SyncRun``'s terminal status. Each completed unit is a crash-safe
        checkpoint: the reporter's per-unit commit writes the ``roms`` row
        and its cached metadata in one write UoW (Rom first, metadata
        second — FK-safe), so a ROM and its metadata are always consistent
        across a crash.
        """
        box = self._sync_state
        # Cross-unit accumulators — built up unit-by-unit, consumed by the
        # final phase. ``synced_rom_ids`` is shared with collection units
        # for dedup. ``collection_memberships`` and ``platform_rom_ids``
        # feed the run finalizer's ``_build_collection_app_ids`` once every
        # unit has been applied. ``total_games_applied`` is the run's
        # processed-ROM count — wholesale skips, delta-skips, and committed
        # applies alike — surfaced as ``total_games`` (the terminal frame's
        # "N of M games processed" numerator).
        synced_rom_ids: set[int] = set()
        collection_memberships: dict[tuple[str, str], CollectionMembership] = {}
        platform_rom_ids: set[int] = set()
        total_games_applied = 0
        cancelled = False
        # Reset the per-run set of appIds the reporter binds (across both the
        # happy-path and late-ack commit paths). The stale scan excludes it so
        # a new rom_id reusing an old appId is never wrongly removed (#1036).
        box.committed_app_ids = set()
        box.run_interrupted = False
        # Session-budget gate run-scoped state (#1383): the paused flag, the
        # emitted-chunk counter (first chunk exempt → guaranteed forward progress),
        # and the distinct pause reason. The gate's own once-per-run measurement
        # latch is re-armed by the baseline stamp below, in the module that sets it.
        box.run_paused = False
        box.chunks_emitted_this_run = 0
        box.interrupt_reason = None
        # Run-scoped progress counters for the paused banner's "X of Y games done"
        # (#1383). The total is stamped at plan time below; the done count grows with
        # the skipped + committed work as the run proceeds.
        box.run_total_items = None
        box.run_done_items = 0
        # Re-arm the gate's measurement latch and stamp the run-start RSS baseline
        # for the last-run memory delta (#1383) — UNCONDITIONALLY, before any chunk is
        # applied, so even a fully-incremental-skip run reports an honest delta.
        await self._session_budget.record_run_start_baseline()
        # Capture the run id up front so the terminal SyncRun writes and the
        # ``finally`` reset below operate on a stable id for the lifetime of
        # this run. Every terminal IDLE/None reset for this run is collapsed
        # into the single ``finally: box.finish_run(run_id)`` — a run-scoped
        # compare-and-reset that no-ops if a fresher run already owns the slot,
        # so a rapid Sync/Cancel can't leave a half-reset run id (#1202).
        # The capture is what every write below reads instead of the box: the
        # error path runs before that ``finally``, but a clear moving ahead of
        # it would leave the live id null there, and a terminal write handed a
        # null id no-ops — recording nothing and leaving the run ``running``
        # for good. Reading the box at each write would make that a one-line
        # change away; reading the capture makes it impossible.
        run_id = box.current_sync_id

        try:
            try:
                work_queue = await self._fetcher.build_work_queue()
            except asyncio.CancelledError:
                await self._progress.emit_cancelled(SYNC_CANCELLED)
                raise
            except Exception as e:
                self._logger.error(f"Failed to build work queue: {e}")
                _code, _msg = classify_error(e)
                await self._progress.emit_progress(SyncStage.ERROR, message=_msg, running=False)
                return

            total_units = len(work_queue)
            total_roms_planned = sum(u.rom_count for u in work_queue)
            # Skip-aware estimate total (#1382): predicted-skip units weigh 0,
            # the rest their persisted collapsed count (raw ``rom_count``
            # fallback). Estimate-only — it prices the frontend's seeds and
            # never feeds the actual skip decision (ADR-0023); ``total_roms``
            # below stays the raw planned total for backward compatibility.
            total_estimated_items = sum(u.estimated_items() for u in work_queue)
            platforms_planned = sum(1 for u in work_queue if u.type == "platform")
            processed_platform_slugs = frozenset(u.slug for u in work_queue if u.type == "platform" and u.slug)
            # The run's denominator for the paused banner's "X of Y games done" — the
            # same planned total the ``sync_plan`` event carries (#1383).
            box.run_total_items = total_roms_planned
            self._logger.info(f"Per-unit pipeline: {total_units} units planned, {total_roms_planned} ROMs total")
            await self._emit(
                "sync_plan",
                {
                    "run_id": str(run_id or ""),
                    "units": [u.to_event_payload() for u in work_queue],
                    "total_units": total_units,
                    "total_roms": total_roms_planned,
                    "total_estimated_items": total_estimated_items,
                },
            )

            if total_units == 0:
                # A zero-unit sync MUST NOT open or complete a ``SyncRun``:
                # an empty completed run would become ``get_latest_completed``
                # and reset the preview baseline (every platform would then
                # report as 'added') and the ``last_sync`` timestamp. Leaving
                # the prior completed run as the baseline matches the JSON era.
                await self._progress.emit_progress(SyncStage.DONE, message="Nothing to sync", running=False)
                return

            # SyncRun.start — short write UoW for the planned counts.
            await self._loop.run_in_executor(
                None, self._sync_run_recorder.do_open_run, run_id, platforms_planned, total_roms_planned
            )
            # Read before the first unit: a collection unit rewrites its stamp's
            # members at its last chunk, and the stale removal asks what the
            # collections held before this run.
            collection_listed = await self._loop.run_in_executor(
                None, self._local_library_reader.do_read_collection_listed_rom_ids
            )

            # One reading of the emulator sources for the whole run: every unit
            # resolves its ROMs' emulators through it, and a change to the
            # sources during the run takes effect from the next one.
            sources = await self._loop.run_in_executor(None, self._shortcut_launch_resolver.do_read_sources)
            deliveries: dict[tuple[str, str], CollectionDelivery] = {}
            try:
                for unit_index, unit in enumerate(work_queue):
                    if box.is_cancelling():
                        cancelled = True
                        break

                    delivery = await self._delivery_for(
                        work_queue, unit_index, deliveries, synced_rom_ids=synced_rom_ids
                    )
                    applied = await self._unit_applier.apply_unit(
                        unit,
                        sources=sources,
                        unit_index=unit_index,
                        total_units=total_units,
                        synced_rom_ids=synced_rom_ids,
                        collection_memberships=collection_memberships,
                        platform_rom_ids=platform_rom_ids,
                        delivery=delivery,
                    )
                    total_games_applied += applied

                    if box.is_cancelling():
                        cancelled = True
                        break
            except SyncCancelled:
                # A cooperative cancel delivered mid-fetch (a fetcher raised
                # SyncCancelled inside UnitApplier.apply_unit or
                # UnitApplier.fetch_collection_units) bypasses the
                # is_cancelling() checkpoints. Route it into the same graceful
                # finalize the checkpoint break uses, so the SyncRun is marked
                # cancelled and sync_state is restored to IDLE instead of wedging
                # until the backend restarts (#1035). This clause names
                # SyncCancelled alone, and a REAL asyncio.CancelledError raised
                # mid-fetch is a BaseException outside it, so that one is NOT
                # caught here — it propagates out, never swallowed into the
                # finalize.
                cancelled = True

            # Final phase: stale cleanup + Steam collections + sync_complete.
            # Surface a non-terminal finalizing snapshot before the terminal
            # done/cancelled emit so the bar stays full while the run finalizer
            # commits collections. Cancelled runs skip it — their next emit
            # is the terminal cancelled snapshot from the run finalizer.
            if not cancelled:
                await self._progress.emit_progress(
                    SyncStage.FINALIZING,
                    message="Finalizing…",
                    step=total_units,
                    total_steps=total_units,
                )
            outcome = await self._finalize_per_unit(
                synced_rom_ids=synced_rom_ids,
                collection_memberships=collection_memberships,
                platform_rom_ids=platform_rom_ids,
                processed_platform_slugs=processed_platform_slugs,
                collection_listed_rom_ids=collection_listed,
                cancelled=cancelled,
            )

            await self._record_terminal_status(run_id, cancelled=cancelled, outcome=outcome)

            # Emit the terminal signals LAST — only now that the terminal SyncRun
            # status is persisted, so a frontend stats refetch triggered by
            # ``sync_complete`` / the terminal progress frame reads the fresh run
            # status instead of racing the DB write (#39). The error path below never
            # reaches here, so it never double-emits sync_complete.
            await self._run_finalizer.emit_sync_complete(
                platform_app_ids=outcome.platform_app_ids,
                romm_collection_app_ids=outcome.romm_collection_app_ids,
                removed_rom_ids=outcome.removed_rom_ids,
                total_games=total_games_applied,
                cancelled=cancelled,
                interrupt_reason=outcome.interrupt_reason,
                restart_recommended=outcome.restart_recommended,
            )
        except Exception as e:
            import traceback

            self._logger.error(f"Per-unit sync failed: {e}\n{traceback.format_exc()}")
            _code, _msg = classify_error(e)
            self._tasks.spawn(self._progress.write_error_frame(f"Sync failed — {_msg}"), "sync_progress error frame")
            # The captured ``run_id``, for the reason given where it is taken.
            # This path is reached by every failure inside the run, including
            # the ones before ``do_open_run`` ever wrote a row; those carry a
            # perfectly good id and no row, which ``_terminate_run`` no-ops on
            # when its load comes back empty.
            await self._loop.run_in_executor(None, self._sync_run_recorder.do_mark_errored, run_id, _msg)
        finally:
            # Single run-scoped termination point for every exit path (success,
            # cancel, error, zero-unit) — resets to IDLE only if ``run_id``
            # still owns the slot (#1202).
            box.finish_run(run_id)
            # ``settle``: the shortcuts this run minted reach shortcuts.vdf a
            # little after it, and the artwork job's worklist is read from there.
            self._artwork_job.request_run(settle=True)

    async def _delivery_for(
        self,
        work_queue: list[WorkUnit],
        unit_index: int,
        deliveries: dict[tuple[str, str], CollectionDelivery],
        *,
        synced_rom_ids: set[int],
    ) -> CollectionDelivery | None:
        """The delivery the unit at *unit_index* applies from, taken out of *deliveries*; ``None`` for a platform unit.

        The first collection unit fills *deliveries* with every collection unit
        left in *work_queue*, fetched by :meth:`UnitApplier.fetch_collection_units`
        before any of them applies: the first one delivering a member of a group
        no binding holds mints it over every member the run delivers.
        """
        unit = work_queue[unit_index]
        if unit.type != "collection":
            return None
        key = collection_membership_key(unit)
        if key not in deliveries:
            deliveries.update(
                await self._unit_applier.fetch_collection_units(
                    [u for u in work_queue[unit_index:] if u.type == "collection"],
                    synced_rom_ids,
                    unit_index=unit_index,
                    total_units=len(work_queue),
                )
            )
        return deliveries.pop(key)

    async def _record_terminal_status(self, run_id: str | None, *, cancelled: bool, outcome: FinalizeOutcome) -> None:
        """Write the run's terminal ``SyncRun`` status: the one a stopped run earns, or completed."""
        box = self._sync_state
        # SyncRun terminal status — short write UoW. Clean runs complete;
        # a stopped run records WHY, in priority order: a deliberate
        # session-budget ``paused`` (#1383) wins, then a heartbeat-timeout
        # ``interrupted`` (an external death — frontend crash/reload), else the
        # user's own ``cancelled``. The split keeps the UI from blaming a
        # self-imposed pause or a crash on the Cancel button.
        if cancelled:
            if box.run_paused:
                await self._loop.run_in_executor(
                    None, self._sync_run_recorder.do_mark_paused, run_id, box.interrupt_reason or SYNC_PAUSED_BUDGET
                )
            elif box.run_interrupted:
                reason = box.interrupt_reason or _SYNC_INTERRUPTED
                await self._loop.run_in_executor(None, self._sync_run_recorder.do_mark_interrupted, run_id, reason)
            else:
                await self._loop.run_in_executor(
                    None, self._sync_run_recorder.do_mark_cancelled, run_id, SYNC_CANCELLED
                )
        else:
            await self._loop.run_in_executor(
                None,
                self._sync_run_recorder.do_complete_run,
                run_id,
                list(outcome.platform_app_ids.keys()),
                list(outcome.romm_collection_app_ids.keys()),
            )

    async def _finalize_per_unit(
        self,
        *,
        synced_rom_ids: set[int],
        collection_memberships: dict[tuple[str, str], CollectionMembership],
        platform_rom_ids: set[int],
        processed_platform_slugs: frozenset[str],
        cancelled: bool,
        collection_listed_rom_ids: frozenset[int] = frozenset(),
    ) -> FinalizeOutcome:
        """Emit stale-removal + collection mappings; measure the run's memory delta.

        Does NOT emit the terminal ``sync_complete`` — that is deferred to the
        caller's :meth:`RunFinalizer.emit_sync_complete` after the terminal SyncRun
        write (#39). Returns a :class:`FinalizeOutcome` carrying the collection maps
        (the completed-run write needs their keys) plus the ``interrupt_reason`` /
        ``restart_recommended`` surfacing values the deferred emit needs.
        """
        # Stale ROMs: any bound ROM in the registry whose rom_id wasn't
        # seen by any processed unit. Only meaningful on a non-cancelled run
        # — a partial run can't tell "stale" from "didn't get to it yet".
        # Each entry carries the ROM's ``shortcut_app_id``; the row keeps it
        # until the frontend reports the shortcut gone (#2653), so a removal
        # never reported is emitted again by the next run, or unbound by that
        # run's start reconcile where the shortcut is gone.
        # ``committed_app_ids`` (every appId this run bound, across both commit
        # paths) is excluded so a new rom_id reusing an old appId is never
        # wrongly removed (#1036). Of the rest, only a game outside what the
        # run syncs is removed (``sort_stale_rows``); a version gone from RomM
        # stays bound and in its platform's Steam collection, and so does one a
        # collection skip rebuilt (``rebuilt_rows_filed_as_kept``), which no
        # unit left stale. A stopped run files neither in its platform's Steam
        # collection. A row a platform unit converged is no stale row, so the
        # stale sort never keeps it: it goes as a removal of its own that names
        # the shortcut its game keeps, so the panel carries its state over — on
        # a run that was not stopped, and never over an appId this run bound.
        candidates: list[StaleRow] = []
        group_keys: dict[int, str] = {}
        converged = [] if cancelled else list(self._sync_state.run_converged.values())
        if not cancelled:
            candidates = await self._loop.run_in_executor(
                None,
                self._local_library_reader.do_scan_stale_roms,
                synced_rom_ids,
                set(self._sync_state.committed_app_ids),
            )
            candidates = [row for row in candidates if row.rom_id not in self._sync_state.run_converged]
        if candidates:
            group_keys = await self._loop.run_in_executor(None, self._local_library_reader.do_read_resident_group_keys)
        stale, kept, gone_marks = await settle_stale_rows(
            candidates,
            ask_romm=self._fetcher.ask_romm_for_roms,
            logger=self._logger,
            read_platform_slugs=processed_platform_slugs,
            listed_platform_slugs=self._sync_state.run_listed_platform_slugs,
            collection_listed_rom_ids=collection_listed_rom_ids,
            synced_group_keys=group_keys_of(synced_rom_ids, group_keys),
        )
        platform_rom_ids.update(row.rom_id for row in kept)
        rebuilt = {} if cancelled else self._sync_state.run_skip_rebuilt_rows
        platform_rom_ids.update(rebuilt_rows_filed_as_kept(rebuilt, processed_platform_slugs, platform_rom_ids))
        removable = set(
            select_stale_removals(
                [(binding.rom_id, binding.app_id) for binding in converged], set(self._sync_state.committed_app_ids)
            )
        )
        converged = [binding for binding in converged if (binding.rom_id, binding.app_id) in removable]
        if converged:
            # A platform unit's own reading may be minutes old by now: a group
            # one of whose shortcuts runs at this point keeps them all (#2645 D6).
            left = without_running_groups(converged, await self._running_rom_ids())
            if len(left) < len(converged):
                kept_for_now = len(converged) - len(left)
                self._logger.info(f"Convergence: {kept_for_now} shortcut(s) of a running game stay until a later sync")
            converged = left
        remove = [{"rom_id": row.rom_id, "app_id": row.app_id} for row in stale]
        remove += [binding._asdict() for binding in converged]
        if remove:
            await self._rules.emit_under_lease(
                "sync_stale",
                lambda token: self._emit("sync_stale", {"remove": remove, "prune_lease_token": token}),
            )
        else:
            await self._emit("sync_stale", {"remove": remove})

        # Session-budget surfacing (#1383): a budget pause carries its distinct
        # reason into the terminal payload; a CLEAN run recommends a Steam restart
        # when its post-run RSS is high (the next large operation would likely
        # pause/crash). The memory delta is measured at every terminal this
        # finalize reaches — completed/paused/cancelled/interrupted; an errored
        # run aborts before this path and keeps the prior delta (#36). It is the
        # GC-settled terminal RSS differenced against this run's raw run-start
        # baseline, so a paused/cancelled/interrupted run reports ITS OWN
        # consumption-so-far ("last run: +X GB") instead of leaving a prior clean
        # run's number in place. The restart advisory stays clean-run-only. The delta
        # is retained IN THE BOX only, surfaced to the QAM via
        # get_session_budget_status (not the sync_complete wire; the UI reads it from
        # the endpoint). Fail-open — an unavailable reading or any seam error
        # recommends nothing and leaves the delta unmeasurable (never a stale number).
        interrupt_reason = self._sync_state.interrupt_reason if cancelled else None
        restart_recommended = False
        memory_delta_kb: int | None = None
        try:
            rss_kb = await self._session_budget.measure_rss()
            memory_delta_kb = session_memory_delta(self._sync_state.run_start_rss_kb, rss_kb)
            if not cancelled:
                restart_recommended = rss_kb is not None and post_run_advisory(rss_kb)
        except Exception as e:  # fail-open: the advisory/delta must never fail finalize
            self._logger.debug(f"Session-budget terminal advisory/delta skipped: {e}")
        self._sync_state.last_run_delta_kb = memory_delta_kb

        removed_rom_ids = [row.rom_id for row in stale] + [binding.rom_id for binding in converged]
        platform_app_ids, romm_collection_app_ids = await self._run_finalizer.finalize_per_unit_run(
            pending_collection_memberships=collection_memberships,
            pending_platform_rom_ids=platform_rom_ids,
            platform_names=dict(self._sync_state.run_listed_platform_names),
            processed_platform_slugs=processed_platform_slugs,
            stale_rom_ids=removed_rom_ids,
            gone_marks=gone_marks,
        )
        return FinalizeOutcome(
            platform_app_ids=platform_app_ids,
            romm_collection_app_ids=romm_collection_app_ids,
            removed_rom_ids=frozenset(removed_rom_ids),
            interrupt_reason=interrupt_reason,
            restart_recommended=restart_recommended,
        )
