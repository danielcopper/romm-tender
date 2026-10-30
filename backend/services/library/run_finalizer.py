"""The end of a library sync run: the one write Unit of Work that closes the run's record of the library, the
Steam-collection maps built inside it, and the terminal signals sent once the run's ``SyncRun`` is persisted.

Each chunk's commit belongs to :class:`SyncReporter`; deciding what a run does belongs to the orchestrator, the
preview and the unit applier.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.collection_label import steam_collection_key
from domain.collection_name import fold_collection_name
from domain.platform_names import PLATFORM_NAMES_KEY, decode_platform_names
from domain.sync_diff import platform_collection_name
from domain.sync_stage import SyncStage

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable, Collection, Mapping
    from collections.abc import Set as AbstractSet

    from domain.rom import GoneEvidence, Rom
    from services.library._state import CollectionMembership, LibrarySyncStateBox
    from services.protocols import ConflictRules, EventEmitter, UnitOfWork, UnitOfWorkFactory

    EmitProgressFn = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class RunFinalizerConfig:
    """Frozen wiring bundle handed to ``RunFinalizer.__init__``.

    Holds the live settings dict (the Steam-collection grouping and naming
    settings), the loop the write UoW runs on, the event emitter, the SQLite
    Unit-of-Work factory, the shared ``LibrarySyncStateBox`` (the finalizer
    reads the run's interrupted flag and planned total from it; the
    run-lifecycle reset is owned by the orchestrator's terminal ``finally``,
    not here), an ``SyncProgress.emit_progress`` callback for the terminal
    frame, and the ``ConflictRules`` the ``sync_complete`` event's lease is
    taken through.
    """

    settings: dict[str, Any]
    loop: asyncio.AbstractEventLoop
    emit: EventEmitter
    uow_factory: UnitOfWorkFactory
    sync_state_box: LibrarySyncStateBox
    emit_progress: EmitProgressFn
    conflict_rules: ConflictRules


class RunFinalizer:
    """The run's closing registry write with its Steam-collection maps, and its terminal signals."""

    def __init__(self, *, config: RunFinalizerConfig) -> None:
        self._settings = config.settings
        self._loop = config.loop
        self._emit = config.emit
        self._uow_factory = config.uow_factory
        self._sync_state = config.sync_state_box
        self._emit_progress = config.emit_progress
        self._rules = config.conflict_rules

    # ── Steam-collection maps ────────────────────────────────────

    def _build_collection_app_ids(
        self,
        uow: UnitOfWork,
        pending_platform_rom_ids: set[int] | None,
        pending_collection_memberships: dict[tuple[str, str], CollectionMembership],
        platform_names: dict[str, str],
        removed_rom_ids: AbstractSet[int] = frozenset(),
    ) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        """Build platform_app_ids and romm_collection_app_ids from ``uow.roms``.

        Platform collections are grouped from the full ``roms`` table
        (every bound ROM, including ones an incremental sync skipped —
        they remain rows), keyed by the platform's display name
        resolved from *platform_names*, falling back to the slug when
        absent.

        RomM collections keep the per-run membership accumulator (keyed by a
        collision-free ``(collection_kind, collection_id)`` identity, name in the
        value) and resolve each member ``rom_id`` to a Steam appId with a
        **sibling-group fallback** (ADR-0021): a bound member uses its own
        binding; an unbound member maps to its group's bound sibling's appId, so
        favouriting / collecting ANY version of a game puts the game's single
        shortcut into the Steam collection. Per-collection appIds are
        de-duplicated — several siblings of one group collapse onto the one
        shortcut — and collections sharing a resolved key UNION into the one Steam
        collection (the key is name-only under ``merge`` (#1503) or name + type
        label under ``by_label`` (#1539), a standard collection's name alone; see
        :meth:`_resolve_collection_memberships`). The platform loop still excludes
        rows whose ``shortcut_app_id`` is ``None``.

        A row in *removed_rom_ids* counts as unbound.
        """
        platform_app_ids, group_bound_app_id = self._scan_bound_rows(
            uow, pending_platform_rom_ids, platform_names, removed_rom_ids
        )
        romm_collection_app_ids = self._resolve_collection_memberships(
            uow, pending_collection_memberships, group_bound_app_id, removed_rom_ids
        )
        return platform_app_ids, romm_collection_app_ids

    def _scan_bound_rows(
        self,
        uow: UnitOfWork,
        pending_platform_rom_ids: set[int] | None,
        platform_names: dict[str, str],
        removed_rom_ids: AbstractSet[int] = frozenset(),
    ) -> tuple[dict[str, list[int]], dict[str, int]]:
        """One pass over the bound rows: platform buckets + each group's bound appId.

        When a group still carries several bound rows (a game the convergence
        has not reached yet, or never reaches) the smallest rom_id's binding
        wins, so the filing stays the same from run to run.

        Platform display names are grouped **case-insensitively** (keyed by
        :func:`domain.collection_name.fold_collection_name`, first-seen original
        casing kept for display): Steam collapses collection names by a
        case-insensitive identity, so two display names differing only in case
        must land in one Steam collection or the second create overwrites the
        first. Two distinct slugs colliding this way is rare, but the union keeps
        it lossless and matches the collection-map rule
        (:meth:`_resolve_collection_memberships`).
        """
        create_groups = self._settings.get("collection_create_platform_groups", False)
        display_by_fold: dict[str, str] = {}
        platform_app_ids_by_fold: dict[str, list[int]] = {}
        group_bound: dict[str, tuple[int, int]] = {}
        for rom in uow.roms.iter_all():
            if rom.shortcut_app_id is None or rom.rom_id in removed_rom_ids:
                continue
            display = platform_collection_name(
                rom.rom_id, rom.platform_slug, pending_platform_rom_ids, platform_names, create_groups
            )
            if display is not None:
                fold = fold_collection_name(display)
                display_by_fold.setdefault(fold, display)
                platform_app_ids_by_fold.setdefault(fold, []).append(rom.shortcut_app_id)
            self._note_group_binding(group_bound, rom)
        platform_app_ids = {display_by_fold[fold]: app_ids for fold, app_ids in platform_app_ids_by_fold.items()}
        return platform_app_ids, {key: app_id for key, (_rid, app_id) in group_bound.items()}

    @staticmethod
    def _note_group_binding(group_bound: dict[str, tuple[int, int]], rom: Rom) -> None:
        """Record the group's winning binding: smallest bound rom_id wins."""
        group_key = rom.sibling_group_key
        if group_key is None or rom.shortcut_app_id is None:
            return
        current = group_bound.get(group_key)
        if current is None or rom.rom_id < current[0]:
            group_bound[group_key] = (rom.rom_id, rom.shortcut_app_id)

    def _resolve_collection_memberships(
        self,
        uow: UnitOfWork,
        pending_collection_memberships: dict[tuple[str, str], CollectionMembership],
        group_bound_app_id: dict[str, int],
        removed_rom_ids: AbstractSet[int],
    ) -> dict[str, list[int]]:
        """Resolve each collection's member rom_ids to de-duplicated appIds, UNION by key.

        A bound member uses its own binding; an unbound member falls back to its
        sibling group's bound appId (ADR-0021). Steam's collection namespace is
        by-name, so collections that share the resolved key merge into one entry:
        their resolved appIds are UNIONed, order-preserving and de-duplicated
        ACROSS collections (each collection's own resolution already dedups
        within). Names that resolve to no appId are omitted.

        The key is the Steam-collection name-part built from the
        ``collection_naming_mode`` setting
        (:func:`domain.collection_label.steam_collection_key`): under
        ``merge`` (default) it is the bare display name, so same-named
        collections of any kind union into one ``RomM: [<name>]`` collection
        (#1503) — byte-for-byte the pre-mode output. Under ``by_label`` every
        kind but standard gets its type label appended (``"<name> (Franchise)"``),
        and collections union exactly where those keys match
        (``docs/architecture/steam-non-steam-shortcuts.md`` § Collection naming
        mode). Injecting the label here (not in the frontend) keeps the
        create-name and the reconcile ``activeNames`` derived from this one key.

        Grouping is **case-insensitive** (keyed by
        :func:`domain.collection_name.fold_collection_name`, first-seen original
        casing kept for display): Steam collapses collection names by a
        case-insensitive identity, so two keys differing only in case ("7 up" vs
        "7 Up") must union or the second Steam create overwrites the first and its
        games are lost (#1569). Under ``by_label`` this merges case variants of
        one key, and keeps apart variants whose labels differ (the folded keys
        differ).
        """
        naming_mode = self._settings.get("collection_naming_mode", "merge")
        display_key_by_fold: dict[str, str] = {}
        app_ids_by_fold: dict[str, list[int]] = {}
        seen_by_fold: dict[str, set[int]] = {}
        for membership in pending_collection_memberships.values():
            key = steam_collection_key(membership.name, membership.kind, membership.virtual_type, naming_mode)
            fold = fold_collection_name(key)
            display_key_by_fold.setdefault(fold, key)
            app_ids = app_ids_by_fold.setdefault(fold, [])
            seen = seen_by_fold.setdefault(fold, set())
            for rid in membership.rom_ids:
                app_id = self._member_app_id(uow, rid, group_bound_app_id, removed_rom_ids)
                if app_id is not None and app_id not in seen:
                    seen.add(app_id)
                    app_ids.append(app_id)
        return {display_key_by_fold[fold]: app_ids for fold, app_ids in app_ids_by_fold.items() if app_ids}

    @staticmethod
    def _member_app_id(
        uow: UnitOfWork, rid: int, group_bound_app_id: dict[str, int], removed_rom_ids: AbstractSet[int] = frozenset()
    ) -> int | None:
        """A member's appId: its own binding, else its sibling group's bound appId."""
        rom = uow.roms.get(rid)
        if rom is None:
            return None
        if rom.shortcut_app_id is not None and rid not in removed_rom_ids:
            return rom.shortcut_app_id
        if rom.sibling_group_key is None:
            return None
        return group_bound_app_id.get(rom.sibling_group_key)

    # ── Finalise per-unit run ────────────────────────────────────

    def _finalize_per_unit_run_io(
        self,
        pending_collection_memberships: dict[tuple[str, str], CollectionMembership],
        pending_platform_rom_ids: set[int] | None,
        platform_names: dict[str, str],
        stale_rom_ids: list[int] | None,
        processed_platform_slugs: frozenset[str],
        gone_marks: Mapping[int, GoneEvidence | None],
    ) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        """Settle the removed ROMs' stamps, refresh the name cache, and build collection maps.

        By the time this runs, every per-unit ``commit_unit_results``
        has already upserted its ROMs into ``uow.roms``, so we only need
        to: (1) revoke the skip of the platforms :meth:`finalize_per_unit_run`
        says to and delete the stamp of every collection whose member set holds
        a ROM whose shortcut the run removes, whatever its binding now — and (2)
        and (3) treat such a row as unbound — (2) refresh the offline
        ``platform_slug → name`` cache — the run's listed names plus
        each bound platform's kept name
        (:meth:`_names_with_bound_platforms_kept`) — and
        (3) build the cross-unit collection mappings. Before (2) it writes
        *gone_marks* onto their rows: a mark where one is given, a clear for
        ``None``. The last-sync timestamp
        and the synced platform/collection lists live on the ``SyncRun``
        record :class:`SyncRunRecorder` writes — they are not persisted here.

        Everything happens inside one write UoW so the revocations, the stamp
        deletions, the cache refresh and the reads commit atomically.
        """
        with self._uow_factory() as uow:
            removed_rom_ids: set[int] = set()
            unprocessed_slugs_removed: set[str] = set()
            for rid in stale_rom_ids or []:
                rom = uow.roms.get(rid)
                if rom is None:
                    continue
                removed_rom_ids.add(rom.rom_id)
                if rom.platform_slug not in processed_platform_slugs:
                    unprocessed_slugs_removed.add(rom.platform_slug)
            for slug in unprocessed_slugs_removed:
                uow.platform_sync_state.revoke_skip(slug)
            uow.collection_sync_state.delete_intersecting(removed_rom_ids)
            self._write_gone_marks(uow, gone_marks)

            names = self._names_with_bound_platforms_kept(uow, platform_names, removed_rom_ids)
            uow.kv_config.set(PLATFORM_NAMES_KEY, json.dumps(names))

            return self._build_collection_app_ids(
                uow,
                pending_platform_rom_ids,
                pending_collection_memberships,
                names,
                removed_rom_ids,
            )

    @staticmethod
    def _write_gone_marks(uow: UnitOfWork, gone_marks: Mapping[int, GoneEvidence | None]) -> None:
        """Record on each row what the run established about RomM still serving it."""
        for rom_id, evidence in gone_marks.items():
            rom = uow.roms.get(rom_id)
            if rom is None or rom.gone_from_romm == evidence:
                continue
            if evidence is None:
                rom.clear_gone_from_romm()
            else:
                rom.mark_gone_from_romm(evidence)
            uow.roms.save(rom)

    @staticmethod
    def _names_with_bound_platforms_kept(
        uow: UnitOfWork, platform_names: dict[str, str], removed_rom_ids: AbstractSet[int] = frozenset()
    ) -> dict[str, str]:
        """*platform_names*, plus the cached name of every platform outside it that a bound row is still on.

        A row in *removed_rom_ids* counts as unbound.

        A platform RomM no longer lists keeps its games, and its Steam collection
        is found by this name: written over with the listed names alone, the
        next run would key that collection by the slug, and the frontend would
        remove the old one. This runs on every finalize, a stopped run's included,
        since each of them rewrites the cache.
        """
        kept = decode_platform_names(uow.kv_config.get(PLATFORM_NAMES_KEY))
        outside = {
            rom.platform_slug
            for rom in uow.roms.iter_all()
            if rom.shortcut_app_id is not None
            and rom.rom_id not in removed_rom_ids
            and rom.platform_slug not in platform_names
        }
        return {**{slug: kept[slug] for slug in outside if slug in kept}, **platform_names}

    async def finalize_per_unit_run(
        self,
        pending_collection_memberships: dict[tuple[str, str], CollectionMembership],
        pending_platform_rom_ids: set[int] | None,
        platform_names: dict[str, str] | None = None,
        stale_rom_ids: list[int] | None = None,
        *,
        processed_platform_slugs: Collection[str],
        gone_marks: Mapping[int, GoneEvidence | None] | None = None,
    ):
        """Settle the removed ROMs' stamps, rebuild Steam-collection maps, and emit ``sync_collections``.

        Stale-removal is emitted separately by the orchestrator via
        ``sync_stale`` so the frontend can apply removals before
        collections are recomputed. ``stale_rom_ids`` (default ``None`` =
        none) are the rows whose shortcut the run removes, stale and converged
        alike. Each keeps its binding until the frontend reports the shortcut
        gone (``ShortcutRemovalService.report_sync_removals``;
        docs/architecture/steam-non-steam-shortcuts.md, "A removal the panel did
        not finish"), and the maps and the name cache built here leave it out as
        though it were unbound. A report can land before this runs, so the stamps
        are settled for every row handed in that still exists.
        ``platform_names`` is the ``platform_slug → name`` map of every
        platform the run's listing named, synced or not, cached for the offline
        ``roms``-derived queries.

        ``processed_platform_slugs`` names the platform units this run
        processed, skipped and fetched alike. A stale removal on any other
        platform — one whose sync is turned off, or one RomM no longer lists —
        revokes that platform's skip; a processed platform keeps its skip. A
        stale removal also deletes the stamp of every collection whose member
        set holds the removed ROM, with no processed set to consult. Why, and why a platform's
        stamp itself stays, is in docs/architecture/backend-architecture.md,
        "Incremental skip".

        ``gone_marks`` is the stale removal's verdict per row on whether RomM
        still serves it (:meth:`domain.sync_diff.StaleSort.gone_from_romm_marks`), written
        in the same UoW. Who may pass it is docs/architecture/backend-architecture.md,
        "The stale removal keeps a game gone from RomM".

        Returns the ``(platform_app_ids, romm_collection_app_ids)`` maps the caller
        needs for the completed-run ``SyncRun`` write and the terminal emit. The
        terminal ``sync_complete`` + progress frame is deliberately NOT emitted here
        — it is the orchestrator's separate :meth:`emit_sync_complete` call made
        AFTER the terminal ``SyncRun`` status is persisted, so a frontend stats
        refetch triggered by those terminal signals reads the fresh run status
        instead of racing the DB write (#39).
        """
        names = platform_names or {}
        platform_app_ids, romm_collection_app_ids = await self._loop.run_in_executor(
            None,
            self._finalize_per_unit_run_io,
            pending_collection_memberships,
            pending_platform_rom_ids,
            names,
            stale_rom_ids,
            frozenset(processed_platform_slugs),
            gone_marks or {},
        )

        await self._emit(
            "sync_collections",
            {
                "platform_app_ids": platform_app_ids,
                "romm_collection_app_ids": romm_collection_app_ids,
            },
        )

        return platform_app_ids, romm_collection_app_ids

    async def emit_sync_complete(
        self,
        *,
        platform_app_ids: dict[str, list[int]],
        romm_collection_app_ids: dict[str, list[int]],
        removed_rom_ids: AbstractSet[int] = frozenset(),
        total_games: int,
        cancelled: bool,
        interrupt_reason: str | None,
        restart_recommended: bool,
    ) -> None:
        """Emit the terminal ``sync_complete`` event + the terminal progress frame.

        Called by the orchestrator AFTER the terminal ``SyncRun`` status is
        persisted — this is the "emit last" ordering that closes the emit-before-
        persist race (#39): a frontend stats refetch triggered by these terminal
        signals now reads the freshly-written run status, not the prior run's.

        Session-budget surfacing (#1383): ``interrupt_reason`` (present only when a
        run was paused by the budget gate) rides the ``sync_complete`` payload and
        becomes the terminal progress message, so the UI shows the resume-friendly
        pause guidance distinctly instead of the generic cancelled/interrupted
        wording. ``restart_recommended`` (only on a clean run whose post-run RSS is
        high) sets an additive payload flag the UI turns into a "restart Steam" nudge.

        Heartbeat-timeout surfacing (#1384): an interrupted run (an external death —
        frontend crash/reload — flagged by the box's ``run_interrupted``) routes
        through the same cancelled finalize, so the payload carries an additive
        ``interrupted: True`` alongside ``cancelled`` and the completion toast can
        say "interrupted" instead of blaming a Cancel the user never pressed. A
        budget pause sets ``interrupt_reason`` (not ``run_interrupted``) and never
        carries the flag.
        """
        complete_payload: dict[str, Any] = {
            "platform_app_ids": platform_app_ids,
            "romm_collection_app_ids": romm_collection_app_ids,
            "total_games": total_games,
        }
        if cancelled:
            complete_payload["cancelled"] = True
            if self._sync_state.run_interrupted:
                complete_payload["interrupted"] = True
            if interrupt_reason:
                complete_payload["interrupt_reason"] = interrupt_reason
        elif restart_recommended:
            complete_payload["restart_recommended"] = True
        await self._rules.emit_under_lease(
            "sync_complete",
            lambda token: self._emit("sync_complete", {**complete_payload, "prune_lease_token": token}),
        )

        if cancelled:
            await self._emit_cancelled_frame(total_games=total_games, interrupt_reason=interrupt_reason)
        else:
            total = await self._loop.run_in_executor(None, self._count_bound_roms, removed_rom_ids)
            await self._emit_progress(
                SyncStage.DONE,
                current=total,
                total=total,
                message=f"Sync complete: {total} games from {len(platform_app_ids)} platforms",
                running=False,
            )

    async def _emit_cancelled_frame(self, *, total_games: int, interrupt_reason: str | None) -> None:
        """Emit the terminal progress frame for a cancelled/interrupted run.

        A budget pause carries its own full-sentence guidance — used as the
        terminal message verbatim so the QAM status reads the resume-friendly
        reason. Otherwise a heartbeat-timeout run routes through this same
        cancelled finalize, so the leading word keys on the box's
        ``run_interrupted`` flag — the frame then reads "interrupted" instead of
        blaming the user's Cancel button (stage stays CANCELLED; last_attempt
        already reads "interrupted"). The frame's denominator is the run's
        PLANNED total (the sync_plan count stamped in the box) so "N of M games
        processed" compares against the plan, not the bound-ROM registry count
        (which equals N on a first sync); the registry count is only the
        fallback when the box was wiped before plan time (backend restart).
        """
        planned_total = self._sync_state.run_total_items
        total = (
            planned_total
            if planned_total is not None
            else await self._loop.run_in_executor(None, self._count_bound_roms)
        )
        if interrupt_reason:
            message = interrupt_reason
        else:
            lead = "Sync interrupted" if self._sync_state.run_interrupted else "Sync cancelled"
            message = f"{lead}: {total_games} of {total} games processed"
        await self._emit_progress(
            SyncStage.CANCELLED,
            current=total_games,
            total=total,
            message=message,
            running=False,
        )

    def _count_bound_roms(self, removed_rom_ids: AbstractSet[int] = frozenset()) -> int:
        """Count ROMs that carry a Steam-shortcut binding; a row in *removed_rom_ids* counts as unbound."""
        with self._uow_factory() as uow:
            pending = sum(
                1 for rid in removed_rom_ids if (rom := uow.roms.get(rid)) and rom.shortcut_app_id is not None
            )
            return uow.roms.count_bound() - pending
