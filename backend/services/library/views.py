"""The library façade's reads of this device's record of the library, answered from SQLite alone.

Every repository call here is a read (``scripts/check_read_only_module.py``); a write, Force Full Sync's reset
included, belongs to the part that performs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.fetch_generation import gone_from_romm_ids, rows_with_their_stamp
from domain.platform_names import PLATFORM_NAMES_KEY, platform_display_name
from domain.sibling_resolution import reachable_rom_ids

if TYPE_CHECKING:
    from domain.rom import Rom
    from domain.sync_run import SyncRun
    from services.protocols import UnitOfWorkFactory


# How many of the newest sync runs ``get_sync_runs`` answers with — the run
# list is a recent-history panel, not an archive, and its repository offers no
# delete, so ``sync_runs`` only grows.
SYNC_RUN_HISTORY_LIMIT = 10


@dataclass(frozen=True)
class LibraryViewsConfig:
    """Frozen wiring bundle handed to ``LibraryViews.__init__``.

    Holds the live settings dict (the enabled platform and collection counts
    ``get_sync_stats`` answers) and the SQLite Unit-of-Work factory every read
    opens its UoW through.
    """

    settings: dict[str, Any]
    uow_factory: UnitOfWorkFactory


@dataclass(frozen=True)
class _SyncStatsReading:
    """One read of everything ``get_sync_stats`` takes from SQLite."""

    last_sync: str | None
    last_attempt: dict[str, str] | None
    rom_count: int
    resumable_games: int
    has_completion_stamp: bool


class LibraryViews:
    """The ``roms``-derived queries and the run-history reads behind the façade."""

    def __init__(self, *, config: LibraryViewsConfig) -> None:
        self._settings = config.settings
        self._uow_factory = config.uow_factory

    # ── ``roms``-derived queries ─────────────────────────────────

    def get_registry_platforms(self):
        """Return synced platforms from ``uow.roms`` (works offline, no RomM API call).

        One entry per slug, its counts summed over every ``fs_slug`` its rows
        carry and over the rows with none recorded, until the Platforms tab
        knows a platform by its ``fs_slug`` (#2452 D3). Two counts per
        platform, and they answer different questions.
        ``count`` is bound ROMs — how many Steam shortcuts exist, which is
        what the Remove group acts on. ``reachable_count`` is how many of
        the platform's ROMs are reachable through one of those shortcuts,
        less the ones RomM no longer serves (:func:`_reachable_row_count`),
        which is what the header line states beside RomM's own total.
        The display name is the one the ``platform_names`` cache holds for
        the ``fs_slug`` that is the slug, else for the first of the slug's
        ``fs_slug`` values, else the slug.

        A platform with no bound row is omitted entirely, as it always has
        been. Nothing is lost with it: no group there holds a binding, so
        its ``reachable_count`` is 0 by construction, and the frontend
        reads an absent platform as zero on both counts.
        """
        return self._read_registry_platforms_io()

    def _read_registry_platforms_io(self):
        with self._uow_factory() as uow:
            raw_names = uow.kv_config.get(PLATFORM_NAMES_KEY)
            all_rows = list(uow.roms.iter_all())
            dropped = {
                rom_id
                for platform_rows, stamp in rows_with_their_stamp(all_rows, uow.platform_sync_state.get)
                for rom_id in gone_from_romm_ids(platform_rows, stamp)
            }
        by_slug: dict[str, list[Rom]] = {}
        for rom in all_rows:
            by_slug.setdefault(rom.platform_slug, []).append(rom)
        entries = []
        for slug, slug_rows in by_slug.items():
            count = sum(1 for rom in slug_rows if rom.shortcut_app_id is not None)
            if not count:
                continue
            fs_slugs = sorted({rom.platform_fs_slug for rom in slug_rows if rom.platform_fs_slug is not None})
            named_by = slug if slug in fs_slugs else next(iter(fs_slugs), None)
            entries.append(
                {
                    "name": platform_display_name(raw_names, named_by, slug),
                    "slug": slug,
                    "count": count,
                    "reachable_count": _reachable_row_count(slug_rows, dropped),
                }
            )
        return {"platforms": sorted(entries, key=lambda entry: (entry["name"], entry["slug"]))}

    # ── Stats / run history ──────────────────────────────────────

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
        whole platform or collection at fetch time; an **applied shortcut**
        (``roms.shortcut_applied``) makes it pass over one game at apply time. Both survive a stopped run and
        both are cleared together by Force Full Sync, which is the entire #1789
        defect: the offer used to be derived from the run history, which Force
        Full Sync deliberately preserves (#1318), so it outlived the progress it
        promised to continue.

        Neither fact subsumes the other, so the offer reads both. A run cancelled
        inside its very first platform unit has written shortcuts and recorded
        them applied but reached no final chunk, so it holds applied games and
        no stamp — and it is a genuine resume, because the next run really does
        less work. In the other direction, a row the sync has not applied since
        Force Full Sync or since it was created carries no record while its
        platform's stamp may survive, so an install can hold stamps with zero
        applied games and still skip those platforms wholesale. Counting only one kind declares one of
        these two a fresh start when it is not.

        Everything here rides in the read UoW that already scans ``roms``: the
        applied-game count is one more condition inside that same loop (``iter_all``
        already selects ``shortcut_applied``), and each stamp probe is a
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
                # Bound AND applied, never applied alone. ``classify_roms``
                # (``domain/sync_diff.py``) sends an unbound row down the NEW branch
                # — ``if not reg or not reg.get("app_id")`` — before it ever reads
                # the record, so an applied row with no shortcut is not skip
                # authority: the next run has to mint the shortcut regardless.
                # Requiring the binding is therefore what makes this count fall to
                # zero after Data Management's remove-all by construction: unbinding
                # keeps the row and its record on purpose (ADR-0007), so
                # a count over all rows would keep offering a resume of shortcuts
                # that no longer exist. Do not "simplify" this to every row.
                if rom.shortcut_applied:
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
