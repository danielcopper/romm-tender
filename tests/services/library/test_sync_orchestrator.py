"""Tests for SyncOrchestrator — preview/apply/full-sync lifecycle and safety heartbeat.

The migrated layout drives the orchestrator end-to-end through
``FakeRommApi``: tests seed in-memory platforms/ROMs/collections on the
fake, then exercise the public use cases (``sync_preview``,
``sync_apply_delta``, ``_do_sync_per_unit``, etc.) and assert on the
**observable outputs** — ``emit`` calls, state mutations, persister
counts.

Two production seams remain mockable per test:

* ``ChunkDispatcher._wait_for_unit_complete`` — waits on a frontend
  ``report_unit_results`` callback that no test exercises. Replaced with a
  ``fake_wait`` helper.
* ``CoverPreparer._download_artwork`` — delegates to the SteamGridDB
  pipeline; the orchestrator tests do not exercise artwork I/O. Replaced
  with an ``AsyncMock``.

``emit_progress`` is intentionally **not** mocked when the test asserts on
``emit.call_args_list`` — driving real emissions keeps the
assertions honest. The fetchers' runtime methods (``build_work_queue``,
``fetch_platform_unit``, ``fetch_collection_unit``) are reached through
the real fetchers against the seeded fake — that is the whole point of the
migration.
"""

import asyncio
import json
import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _factories import _make_application, _make_services_bundle
from _spawns import record_spawns
from fakes.fake_source_platform_systems import FakeSourcePlatformSystems
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory

from domain.platform_system import PLATFORM_IDS_KEY, decode_platform_ids
from domain.playtime import Playtime
from domain.rom_install import RomInstall
from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from domain.sync_diff import BIND_ROM_ID_KEY, ConvergedBinding
from domain.sync_run_kind import SyncRunKind
from domain.sync_stage import SyncStage
from domain.sync_state import SyncState
from domain.work_unit import WorkUnit
from lib.errors import Refused, RommConnectionError, RommNotFoundError, classify_error
from lib.romm_paging import LIST_PAGE_SIZE
from services.library._unit_roms import stamp_component_group_keys
from services.library.collection_fetcher import CollectionDelivery
from services.platform_systems import PlatformSystemService, PlatformSystemServiceConfig
from tests.services.library._helpers import (
    _fake_wait_set_event,
    _seed_collection_stamp,
    _seed_install,
    _seed_persisted_rom,
    _seed_platform,
    _seed_rom_row,
)

# ── Test helpers ─────────────────────────────────────────────────


def _seed_collection(
    fake_romm_api,
    *,
    collection_id,
    name,
    rom_ids,
    is_favorite=False,
    is_virtual=False,
    virtual_category=None,
):
    """Seed a (real or virtual) collection plus the ``collection_ids`` /
    ``virtual_collection_ids`` lookup arrays on each member ROM."""
    entry = {
        "id": collection_id,
        "name": name,
        "rom_count": len(rom_ids),
        "rom_ids": list(rom_ids),
        "is_favorite": is_favorite,
        "is_virtual": is_virtual,
    }
    if is_virtual:
        assert virtual_category is not None, "virtual collections need a category"
        fake_romm_api.virtual_collections.setdefault(virtual_category, []).append(entry)
        for rid in rom_ids:
            rom = fake_romm_api.roms.setdefault(rid, {"id": rid})
            rom.setdefault("virtual_collection_ids", []).append(collection_id)
    else:
        fake_romm_api.collections.append(entry)
        for rid in rom_ids:
            rom = fake_romm_api.roms.setdefault(rid, {"id": rid})
            rom.setdefault("collection_ids", []).append(collection_id)


def _seed_completed_run(library, *, at, platforms=None, collections=None, run_id="run-prev"):
    """Insert a completed ``SyncRun`` so ``last_sync`` / ``last_synced_*`` reads resolve."""
    from domain.sync_run import SyncRun

    run = SyncRun.start(id=run_id, at=at, platforms_planned=1, roms_planned=1)
    run.complete(at, platforms or [], collections or [])
    with library.uow:
        library.uow.sync_runs.save(run)


def _seed_platform_stamp(library, slug, *, at, rom_count, fetch_id=None, rom_ids=(), fetch_started_at=None):
    """Persist a per-platform completion stamp (ADR-0023) into the shared UoW.

    ``fetch_id`` is the generation the stamp's fetch marked its rows with (#1504);
    ``None`` lets a test's rows carry none, since every row then counts. That
    pairing is a test convenience: production writes a generation on every stamp
    with a checksum, and a stamp without one predates the checksum. ``rom_ids``
    are the ids the stamp's fetch returned, whose checksum the skip compares
    RomM's id set with — none by default, which is what the fake serves for a
    platform it holds no ROMs for. ``fetch_started_at`` defaults to ``at``.
    """
    from domain.platform_skip import rom_id_checksum
    from domain.platform_sync_state import PlatformSyncState

    with library.uow:
        library.uow.platform_sync_state.save(
            PlatformSyncState.stamp(
                platform_slug=slug,
                at=at,
                rom_count=rom_count,
                fetch_id=fetch_id,
                fetch_started_at=fetch_started_at or at,
                rom_id_checksum=rom_id_checksum(rom_ids),
            )
        )


# Before every seeded stamp's reference (fetch start 2025-01-01 less ten minutes)
# and the harness clock's: a ROM RomM last updated then leaves the skip standing.
_OLD_UPDATED_AT = "2024-06-01T00:00:00+00:00"


class _ClockAdvancingSleeper:
    """A ``Sleeper`` that advances a ``FakeClock`` on each sleep, so the real
    heartbeat-clocked ``ChunkDispatcher._wait_for_unit_complete`` times out
    deterministically without any wall-clock wait (#1367)."""

    def __init__(self, clock, step: float) -> None:
        self._clock = clock
        self._step = step
        self.calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(self._step)


def _stash_abandoned_and_wind_down(library, *, run_id, unit_id, chunk_index, pending, chunk_rows):
    """Drive the box into the production post-heartbeat-timeout state via the
    real box verbs, then wind the run down — no hand-forced internals (#1367).

    Mirrors exactly what ``ChunkDispatcher._abandon_active_chunk`` (stash the
    chunk: null the
    event, clear the dispatch identity) followed by the run's terminal
    ``finally: finish_run(run_id)`` (null ``current_sync_id``) leave behind, so a
    late ``report_unit_results`` arriving now must recover the binding through
    the ``abandoned_chunk`` stash rather than the (dead) active-unit path.
    """
    box = library.sync._box
    box.try_begin_run(run_id, kind=SyncRunKind.APPLY)
    box.active_unit_id = unit_id
    box.active_chunk_index = chunk_index
    box.pending_sync = dict(pending)
    box.pending_all_roms = dict(pending)
    box.stash_abandoned_chunk(list(chunk_rows))
    box.finish_run(run_id)
    return box


class TestShortcutDataFormat:
    """Validate the shortcut data format produced by the backend.

    The backend prepares shortcut data that the frontend uses to create
    Steam shortcuts. These tests ensure the data is well-formed.
    """

    def test_exe_path_is_the_launcher_it_was_handed(self, library):
        """The exe is the launcher path handed in, verbatim.

        Where that path comes from is the composition root's answer (the bin
        root, ``tests/test_bootstrap.py``) — this builder composes none of it,
        so that a launcher living outside the code root needs no second
        spelling here.
        """
        from domain.shortcut_data import build_shortcuts_data

        launcher = "/home/deck/.local/bin/tender-rom-launcher"

        result = build_shortcuts_data([{"id": 1, "name": "Game"}], launcher, {}, {})

        assert result[0]["exe"] == launcher

    def test_installed_rom_gets_launch_command(self, library):
        """An installed ROM's launch_options is the full RetroDECK launch command."""
        from domain.shortcut_data import build_shortcuts_data

        result = build_shortcuts_data(
            [{"id": 42, "name": "Game"}],
            "/data/bin/tender-rom-launcher",
            {42: "/roms/n64/game.z64"},
            {},
        )
        assert (
            result[0]["launch_options"] == "flatpak run --nosocket=wayland net.retrodeck.retrodeck '/roms/n64/game.z64'"
        )

    def test_start_dir_is_parent_of_exe(self, library):
        """Start dir must be the directory containing the launcher."""
        from domain.shortcut_data import build_shortcuts_data

        launcher = "/home/deck/.local/share/romm-tender/bin/tender-rom-launcher"

        result = build_shortcuts_data([{"id": 1, "name": "Game"}], launcher, {}, {})

        assert result[0]["start_dir"] == os.path.dirname(result[0]["exe"])


class TestSyncPreview:
    """Tests for sync_preview().

    Preview is read-only — it paginates every unit, classifies the
    result, and returns the summary. It does NOT mutate the metadata
    cache (that happens per applied unit in the apply phase) and does
    NOT cache the prefetched ROMs (apply re-fetches; this is the
    fix for #738)."""

    @pytest.mark.asyncio
    async def test_returns_correct_summary(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 1, "name": "Game A", "fs_name": "a.z64"},
                {"id": 2, "name": "Game B", "fs_name": "b.z64"},
                {"id": 3, "name": "Game C", "fs_name": "c.z64"},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}

        # Baseline in roms: rom 1 unchanged, rom 2 changed name. The
        # display name resolves from the live work-queue (slug n64 → "N64").
        _seed_rom_row(library, 1, app_id=1001, platform_slug="n64", name="Game A", fs_name="a.z64")
        _seed_rom_row(library, 2, app_id=1002, platform_slug="n64", name="Old B", fs_name="b.z64")

        result = await library.sync.sync_preview()
        assert result["success"] is True
        summary = result["summary"]
        assert summary["new_count"] == 1  # rom 3 is new
        assert summary["changed_count"] == 1  # rom 2 name changed
        assert summary["unchanged_count"] == 1  # rom 1 unchanged
        assert summary["remove_count"] == 0
        assert "preview_id" in result
        # The same counts, per platform: the run's own display name, the
        # buckets in the order the domain function takes them.
        assert summary["platform_breakdown"] == [
            {
                "slug": "n64",
                "name": "N64",
                "synced": True,
                "new_count": 1,
                "changed_count": 1,
                "remove_count": 0,
            }
        ]

    @pytest.mark.asyncio
    async def test_terminal_frame_is_emitted_before_the_snapshot_goes_idle(self, make_library, fake_romm_api, emit):
        """The run's ending reaches the panel as an EVENT; what it leaves behind is idle.

        Ordering, not merely outcome: the terminal frame is emitted while the run
        still owns the slot, and only the ``finally: finish_run`` that follows it
        puts the snapshot back to the idle default. A panel remounting after this
        run must not be handed its terminal frame as the state of whatever run it
        is watching now — that frame's message and run id were what turned a live
        preview into a green "Preview ready" over its own progress rows.
        """

        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        result = await library.sync.sync_preview()
        assert result["success"] is True

        frames = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_progress"]
        assert frames[-1]["stage"] == "done"
        assert frames[-1]["message"] == "Preview ready"
        assert frames[-1]["running"] is False
        # The lifecycle flag rides the get_sync_status answer alone — an event
        # carrying it would make the two look interchangeable, which they are not.
        assert "inFlight" not in frames[-1]
        # ...and the run id it named is gone from what the next mount reads.
        assert frames[-1]["runId"] != ""
        status = library.sync.get_sync_status()
        assert status["running"] is False
        assert status["stage"] == ""
        assert status["message"] == ""
        assert status["runId"] == ""

    @pytest.mark.asyncio
    async def test_populates_pending_delta(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        result = await library.sync.sync_preview()
        assert library.sync._pending_delta is not None
        assert library.sync._pending_delta.preview_id == result["preview_id"]
        assert library.sync._pending_delta.created_at == library.sync._orchestrator._clock.time()

    @pytest.mark.asyncio
    async def test_does_not_write_metadata(self, make_library, fake_romm_api, emit):
        """Preview MUST NOT persist ``rom_metadata`` (#738 regression).

        The bug: preview wrote metadata as a side-effect, and the per-unit
        incremental-skip path produced thin registry ROMs without
        ``metadatum``. Those overwrote populated entries with empty ones,
        corrupting the cache on every delta sync.

        The fix: preview is read-only. The metadata stamp happens in the
        reporter's per-unit commit during apply, not at preview time.
        """

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64", "metadatum": {"genres": ["RPG"]}}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        await library.sync.sync_preview()

        # Preview never commits — no metadata row was persisted.
        with library.uow as uow:
            assert uow.rom_metadata.get(1) is None

    @pytest.mark.asyncio
    async def test_excludes_unbound_rows_from_baseline(self, make_library, fake_romm_api, emit):
        """An unbound (NULL ``shortcut_app_id``) row must NOT enter the
        classify baseline, so it cannot inflate ``remove_count`` (R1xR3).

        Setup: rom 1 is bound and still present on the server (unchanged),
        rom 99 is an unbound leftover that is absent from the live fetch.
        If ``do_read_preview_baseline`` leaked rom 99 into the registry, it
        would be classified as stale (not in the current fetch) and reported
        as a removal. The NULL-exclusion guard keeps ``remove_count`` at 0.
        """

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        # rom 1 bound + present on the server (unchanged); rom 99 unbound
        # leftover on a now-absent platform (would look stale if leaked).
        _seed_rom_row(library, 1, app_id=1001, platform_slug="n64", name="Game A", fs_name="a.z64")
        _seed_rom_row(library, 99, app_id=None, platform_slug="gba", name="Old Z", fs_name="z.gba")

        result = await library.sync.sync_preview()
        assert result["success"] is True
        summary = result["summary"]
        # The unbound row is excluded from the baseline → not counted as stale.
        assert summary["remove_count"] == 0
        assert summary["unchanged_count"] == 1
        assert summary["new_count"] == 0

    @pytest.mark.asyncio
    async def test_refuses_when_sync_running(self, library):
        library.sync._box.sync_state = SyncState.RUNNING
        call = library.sync.sync_preview()

        with pytest.raises(Refused) as excinfo:
            await call

        assert excinfo.value.reason == "sync_in_progress"
        assert "already in progress" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_resets_sync_running_on_completion(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        await library.sync.sync_preview()
        assert library.sync._sync_state == SyncState.IDLE


class TestPreviewCollectionDiff:
    """The preview's collection diffs compare the Steam collections' name-parts on both sides.

    The last completed run records the keys the reporter built, so the preview's
    current set has to be built the same way — under ``by_label`` a smart or
    virtual collection's key carries its type label — and folded the same way.
    """

    @staticmethod
    def _seed_three_kinds(library, fake_romm_api):
        """Enable a standard, a smart and a franchise collection, one game each."""
        for rid in (20, 21, 22):
            fake_romm_api.roms[rid] = {
                "id": rid,
                "name": f"Game {rid}",
                "fs_name": f"g{rid}.gba",
                "platform_id": 2,
                "platform_name": "GBA",
                "platform_slug": "gba",
            }
        _seed_collection(fake_romm_api, collection_id=7, name="Favorites", rom_ids=[20], is_favorite=True)
        fake_romm_api.smart_collections = [{"id": 5, "name": "Filter", "rom_count": 1}]
        fake_romm_api.roms[21]["smart_collection_ids"] = [5]
        _seed_collection(
            fake_romm_api,
            collection_id="9",
            name="Metroid",
            rom_ids=[22],
            is_virtual=True,
            virtual_category="franchise",
        )
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {
            "standard": {"7": True},
            "smart": {"5": True},
            "virtual": {"9": True},
        }

    @pytest.mark.asyncio
    async def test_by_label_unchanged_collections_show_no_diff(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._seed_three_kinds(library, fake_romm_api)
        library.settings["collection_naming_mode"] = "by_label"
        _seed_completed_run(
            library, at="2026-01-01T00:00:00", collections=["Favorites", "Filter (Smart)", "Metroid (Franchise)"]
        )

        result = await library.sync.sync_preview()

        assert result["success"] is True
        diff = result["summary"]["collection_diff"]
        assert diff["added"] == []
        assert diff["removed"] == []

    @pytest.mark.asyncio
    async def test_merge_unchanged_collections_show_no_diff(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._seed_three_kinds(library, fake_romm_api)
        library.settings["collection_naming_mode"] = "merge"
        _seed_completed_run(library, at="2026-01-01T00:00:00", collections=["Favorites", "Filter", "Metroid"])

        result = await library.sync.sync_preview()

        diff = result["summary"]["collection_diff"]
        assert diff["added"] == []
        assert diff["removed"] == []

    @pytest.mark.asyncio
    async def test_by_label_a_real_change_shows_under_the_steam_names(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._seed_three_kinds(library, fake_romm_api)
        library.settings["collection_naming_mode"] = "by_label"
        _seed_completed_run(
            library, at="2026-01-01T00:00:00", collections=["Favorites", "Metroid (Franchise)", "Gone (Smart)"]
        )

        result = await library.sync.sync_preview()

        diff = result["summary"]["collection_diff"]
        assert diff["added"] == ["Filter (Smart)"]
        assert diff["removed"] == ["Gone (Smart)"]

    @pytest.mark.asyncio
    async def test_switching_the_naming_mode_shows_the_renamed_collections(self, make_library, fake_romm_api):
        """A mode switch renames the labelled kinds' Steam collections, so they read as added and removed.

        A standard collection's Steam name is the same in both modes and stays out of the diff.
        """
        library = make_library(romm_api=fake_romm_api)
        self._seed_three_kinds(library, fake_romm_api)
        library.settings["collection_naming_mode"] = "by_label"
        _seed_completed_run(library, at="2026-01-01T00:00:00", collections=["Favorites", "Filter", "Metroid"])

        result = await library.sync.sync_preview()

        diff = result["summary"]["collection_diff"]
        assert diff["added"] == ["Filter (Smart)", "Metroid (Franchise)"]
        assert diff["removed"] == ["Filter", "Metroid"]

    @pytest.mark.asyncio
    async def test_collections_differing_only_in_case_match_their_one_recorded_name(self, make_library, fake_romm_api):
        """The reporter merges the two into one Steam collection and records one spelling of it."""
        library = make_library(romm_api=fake_romm_api)
        for rid in (20, 21):
            fake_romm_api.roms[rid] = {
                "id": rid,
                "name": f"Game {rid}",
                "fs_name": f"g{rid}.gba",
                "platform_id": 2,
                "platform_name": "GBA",
                "platform_slug": "gba",
            }
        _seed_collection(fake_romm_api, collection_id=7, name="7 up", rom_ids=[20])
        _seed_collection(fake_romm_api, collection_id=8, name="7 Up", rom_ids=[21])
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {"standard": {"7": True, "8": True}}
        _seed_completed_run(library, at="2026-01-01T00:00:00", collections=["7 up"])

        result = await library.sync.sync_preview()

        diff = result["summary"]["collection_diff"]
        assert diff["added"] == []
        assert diff["removed"] == []

    @pytest.mark.asyncio
    async def test_a_platform_name_whose_case_changed_is_no_platform_collection_change(
        self, make_library, fake_romm_api
    ):
        """Steam's collection identity ignores case, so the platform's Steam collection stays the same one."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="Nintendo 64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 1, app_id=1001, platform_slug="n64", name="Game A", fs_name="a.z64")
        _seed_completed_run(library, at="2026-01-01T00:00:00", platforms=["NINTENDO 64"])

        result = await library.sync.sync_preview()

        diff = result["summary"]["platform_collection_diff"]
        assert diff == {"has_changes": False, "added_count": 0, "removed_count": 0}


class TestPreviewCoverRefreshCount:
    """The preview's cover-only work count (#1386 flow gap).

    A cover-only server change yields an empty shortcut delta, so the frontend
    would short-circuit on "no changes" and the apply-time invalidation pass
    would never run. The preview therefore counts fingerprint mismatches with
    the SAME kernel the apply pass refreshes by, over the registry projection
    it already reads — side-effect-free: no downloads, no DB writes.
    """

    _OLD = "/cover/big.png?ts=2026-01-01 00:00:00"
    _NEW = "/cover/big.png?ts=2026-07-11 12:00:00"

    @pytest.mark.asyncio
    async def test_platform_unit_mismatch_counted_without_downloads_or_writes(self, make_library, fake_romm_api):
        # A bound, content-unchanged platform ROM whose server cover changed:
        # the shortcut delta is empty but the cover count is 1 — and the
        # preview stays read-only (no cover download, fingerprint untouched).
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Keep", "fs_name": "keep.z64", "path_cover_large": self._NEW}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(
            library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64", cover_source=self._OLD
        )

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["cover_refresh_count"] == 1
        assert summary["new_count"] == 0
        assert summary["changed_count"] == 0
        assert summary["remove_count"] == 0
        # Side-effect-free: the fake saw no cover download and the persisted
        # fingerprint did not advance (the APPLY run owns both).
        assert all(name != "download_cover" for name, _a, _k in fake_romm_api.call_log)
        with library.uow as uow:
            assert uow.roms.get(10).cover_source == self._OLD

    @pytest.mark.asyncio
    async def test_a_cover_set_in_steam_is_not_counted(self, make_library, fake_romm_api, home):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Keep", "fs_name": "keep.z64", "path_cover_large": self._NEW}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(
            library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64", cover_source=self._OLD
        )
        grid = home / ".local" / "share" / "Steam" / "userdata" / "123" / "config" / "grid"
        grid.mkdir(parents=True)
        (grid / "1010p.jpg").write_bytes(b"set by hand")

        result = await library.sync.sync_preview()

        assert result["summary"]["cover_refresh_count"] == 0

    @pytest.mark.asyncio
    async def test_collection_unit_mismatch_is_counted(self, make_library, fake_romm_api):
        # The hardware repro was a collection-unit ROM: its platform is NOT
        # enabled, so the ROM only enters the preview union via the enabled
        # collection — the count must still see it.
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms[20] = {
            "id": 20,
            "name": "CGame",
            "fs_name": "cgame.gba",
            "platform_id": 2,
            "platform_name": "GBA",
            "platform_slug": "gba",
            "path_cover_large": self._NEW,
        }
        fake_romm_api.platforms.append({"id": 2, "name": "GBA", "slug": "gba", "rom_count": 1})
        _seed_collection(fake_romm_api, collection_id=7, name="Favorites", rom_ids=[20], is_favorite=True)
        library.settings["enabled_platforms"] = {"2": False}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        _seed_rom_row(
            library, 20, app_id=2020, platform_slug="gba", name="CGame", fs_name="cgame.gba", cover_source=self._OLD
        )

        result = await library.sync.sync_preview()

        assert result["success"] is True
        assert result["summary"]["cover_refresh_count"] == 1
        assert result["summary"]["new_count"] == 0
        assert result["summary"]["changed_count"] == 0

    @pytest.mark.asyncio
    async def test_unchanged_and_null_fingerprints_count_zero(self, make_library, fake_romm_api):
        # An unchanged fingerprint is no work; a NULL fingerprint is the silent
        # adopt path (or the apply path's own download) — neither is user-visible
        # cover work, so the pure no-changes preview keeps its shape.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 1, "name": "Same", "fs_name": "same.z64", "path_cover_large": self._NEW},
                {"id": 2, "name": "Null", "fs_name": "null.z64", "path_cover_large": self._NEW},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(
            library, 1, app_id=1001, platform_slug="n64", name="Same", fs_name="same.z64", cover_source=self._NEW
        )
        _seed_rom_row(library, 2, app_id=1002, platform_slug="n64", name="Null", fs_name="null.z64", cover_source=None)

        result = await library.sync.sync_preview()

        assert result["success"] is True
        assert result["summary"]["cover_refresh_count"] == 0
        assert result["summary"]["new_count"] == 0
        assert result["summary"]["changed_count"] == 0


class TestPreviewRestampPlatformCount:
    """The preview's unstamped-platform re-run count (#1416).

    A late-ack-recovered platform is complete but carries no ``PlatformSyncState``
    stamp, so its shortcut delta is empty yet its apply must still run once to
    re-stamp it. The preview counts enabled platforms without a completion stamp
    (``restamp_platform_count``) so the frontend offers Apply on an otherwise-empty
    delta instead of short-circuiting — a fully-stamped library keeps counting 0
    and short-circuits exactly as before.
    """

    @pytest.mark.asyncio
    async def test_unstamped_platform_counted_with_empty_delta(self, make_library, fake_romm_api):
        # A bound, content-unchanged platform ROM with NO completion stamp: the
        # shortcut delta is empty (0 new / changed / removed) but the platform
        # needs a re-stamp run, so restamp_platform_count is 1.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Keep", "fs_name": "keep.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["restamp_platform_count"] == 1
        assert summary["new_count"] == 0
        assert summary["changed_count"] == 0
        assert summary["remove_count"] == 0
        assert summary["cover_refresh_count"] == 0

    @pytest.mark.asyncio
    async def test_stamped_platform_not_counted(self, make_library, fake_romm_api):
        # The same unchanged platform WITH a matching completion stamp: no
        # re-stamp is owed, so the count stays 0 and the "up to date"
        # short-circuit is preserved.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Keep", "fs_name": "keep.z64", "updated_at": _OLD_UPDATED_AT}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00", rom_count=1, rom_ids=(10,))

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["restamp_platform_count"] == 0
        assert summary["new_count"] == 0
        assert summary["changed_count"] == 0

    @pytest.mark.asyncio
    async def test_counts_only_unstamped_across_mixed_platforms(self, make_library, fake_romm_api):
        # Two enabled platforms, one stamped and one not: only the unstamped one
        # is counted (the count is not a whole-library boolean).
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A", "fs_name": "a.z64", "updated_at": _OLD_UPDATED_AT}],
        )
        _seed_platform(
            fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 20, "name": "B", "fs_name": "b.gba"}]
        )
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")
        _seed_rom_row(library, 20, app_id=2020, platform_slug="gba", name="B", fs_name="b.gba")
        # Only n64 carries a stamp; gba is the unstamped late-ack survivor.
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00", rom_count=1, rom_ids=(10,))

        result = await library.sync.sync_preview()

        assert result["success"] is True
        assert result["summary"]["restamp_platform_count"] == 1

    @pytest.mark.asyncio
    async def test_a_platform_whose_skip_was_revoked_is_counted(self, make_library, fake_romm_api):
        # The stamp is still there, but a revoked one authorises no skip, so the
        # platform full-fetches and only an apply can give it a stamp that does.
        # Were it not counted, an empty delta would offer no Apply and the
        # platform would full-fetch on every sync.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Keep", "fs_name": "keep.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00", rom_count=1)
        with library.uow as uow:
            uow.platform_sync_state.revoke_skip("n64")

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["restamp_platform_count"] == 1
        assert summary["new_count"] == 0
        assert summary["changed_count"] == 0


class TestPreviewOverASkippedUnit:
    """The preview counts what the apply will do, and the apply leaves a skipped unit alone.

    An installed game whose built launch command differs from the one recorded on
    its row — here, the command before RetroDECK started without its Wayland
    socket — is a change only where the apply rewrites shortcuts. On a unit the
    fetcher skips it never does, so counting it there showed the same "updated"
    games on every preview.
    """

    OLD_CMD = 'flatpak run net.retrodeck.retrodeck "/roms/n64/a.z64"'

    @staticmethod
    def _installed_game(library, *, applied_launch_options):
        _seed_install(library, 10, file_path="/roms/n64/a.z64", platform_slug="n64")
        _seed_rom_row(
            library,
            10,
            app_id=1010,
            platform_slug="n64",
            name="A",
            fs_name="a.z64",
            applied_launch_options=applied_launch_options,
        )

    @pytest.mark.asyncio
    async def test_a_launch_command_only_difference_on_a_skipped_platform_is_unchanged(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        # The platform lists no ROMs, so a full fetch would read the game as
        # removed: only the skip keeps it in the preview.
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        library.settings["enabled_platforms"] = {"1": True}
        self._installed_game(library, applied_launch_options=self.OLD_CMD)
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["new_count"] == 0
        assert summary["changed_count"] == 0
        assert summary["unchanged_count"] == 1
        assert summary["remove_count"] == 0
        assert result["changed_names"] == []
        assert summary["platform_breakdown"] == []

    @pytest.mark.asyncio
    async def test_a_launch_command_only_difference_on_a_skipped_collection_is_unchanged(
        self, make_library, fake_romm_api
    ):
        from domain.collection_sync_state import CollectionSyncState
        from domain.platform_skip import rom_id_checksum

        library = make_library(romm_api=fake_romm_api)
        # The game's platform is off, so only the collection brings it in.
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A"}])
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[10])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00+00:00"
        library.settings["enabled_platforms"] = {"1": False}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        self._installed_game(library, applied_launch_options=self.OLD_CMD)
        with library.uow as uow:
            uow.collection_sync_state.save(
                CollectionSyncState.stamp(
                    collection_id="7",
                    collection_kind="standard",
                    updated_at="2025-01-01T00:00:00+00:00",
                    completed_at="2025-06-01T00:00:00",
                    rom_count=1,
                    member_rom_ids=(10,),
                    fetch_started_at="2025-06-01T00:00:00+00:00",
                    listing_rom_id_checksum=rom_id_checksum((10,)),
                )
            )

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["changed_count"] == 0
        assert summary["unchanged_count"] == 1
        assert summary["remove_count"] == 0
        assert result["changed_names"] == []
        assert summary["platform_breakdown"] == []

    @pytest.mark.asyncio
    async def test_a_name_difference_on_a_skipped_platform_is_still_changed(
        self, make_library, fake_romm_api, monkeypatch
    ):
        # A skip rebuilds its rows from the database, so their identity cannot
        # drift there today; the fetch is stubbed to show that one which did would
        # still be counted.
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        library.settings["enabled_platforms"] = {"1": True}
        self._installed_game(library, applied_launch_options=self.OLD_CMD)
        rebuilt = {
            "id": 10,
            "name": "A (Renamed)",
            "fs_name": "a.z64",
            "platform_name": "N64",
            "platform_slug": "n64",
            "sibling_group_key": "romm:seed:1",
        }
        monkeypatch.setattr(library.sync._fetcher, "fetch_platform_unit", AsyncMock(return_value=([rebuilt], True)))

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["changed_count"] == 1
        assert summary["unchanged_count"] == 0
        assert result["changed_names"] == ["A (Renamed)"]
        assert [row["changed_count"] for row in summary["platform_breakdown"]] == [1]

    @pytest.mark.asyncio
    async def test_a_launch_command_only_difference_on_a_fetched_platform_is_changed(self, make_library, fake_romm_api):
        # No stamp, so the platform is fetched and the apply rewrites the command.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A", "fs_name": "a.z64"}]
        )
        library.settings["enabled_platforms"] = {"1": True}
        self._installed_game(library, applied_launch_options=self.OLD_CMD)

        result = await library.sync.sync_preview()

        assert result["success"] is True
        summary = result["summary"]
        assert summary["changed_count"] == 1
        assert summary["unchanged_count"] == 0
        assert result["changed_names"] == ["A"]
        assert [row["changed_count"] for row in summary["platform_breakdown"]] == [1]

    @pytest.mark.asyncio
    async def test_a_skipped_collection_member_on_a_fetched_platform_is_changed(
        self, make_library, fake_romm_api, monkeypatch
    ):
        from domain.collection_sync_state import CollectionSyncState
        from domain.platform_skip import rom_id_checksum

        # The platform has no stamp and is fetched; the collection holding the
        # same game is stamped and skipped. The game belongs to the platform unit,
        # whose apply rewrites the command.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A", "fs_name": "a.z64"}]
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[10])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00+00:00"
        library.settings["enabled_platforms"] = {"1": True}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        self._installed_game(library, applied_launch_options=self.OLD_CMD)
        with library.uow as uow:
            uow.collection_sync_state.save(
                CollectionSyncState.stamp(
                    collection_id="7",
                    collection_kind="standard",
                    updated_at="2025-01-01T00:00:00+00:00",
                    completed_at="2025-06-01T00:00:00",
                    rom_count=1,
                    member_rom_ids=(10,),
                    fetch_started_at="2025-06-01T00:00:00+00:00",
                    listing_rom_id_checksum=rom_id_checksum((10,)),
                )
            )
        fetcher = library.collection_fetcher
        fetch_collection_unit = fetcher.fetch_collection_unit
        collection_skips: list[bool] = []

        async def recording_fetch_collection_unit(*args, **kwargs):
            answer = await fetch_collection_unit(*args, **kwargs)
            collection_skips.append(answer[2])
            return answer

        monkeypatch.setattr(fetcher, "fetch_collection_unit", recording_fetch_collection_unit)

        result = await library.sync.sync_preview()

        assert collection_skips == [True]
        assert result["success"] is True
        summary = result["summary"]
        assert summary["changed_count"] == 1
        assert summary["unchanged_count"] == 0
        assert result["changed_names"] == ["A"]
        assert [row["changed_count"] for row in summary["platform_breakdown"]] == [1]


class TestSyncApplyDelta:
    """Tests for sync_apply_delta().

    Apply dispatches the per-unit pipeline against a live fetch (no
    preview-time prefetch cache — that's the #738 fix). The preview_id
    and 30-min age gate still validate stale apply attempts.
    """

    def _setup_pending_delta(self, library, preview_id="test-preview-123"):
        """Helper to stage a valid pending delta through the box's own verb."""
        library.sync._box.stage_preview(
            preview_id=preview_id,
            created_at=library.sync._orchestrator._clock.time(),
            answer={"success": True, "preview_id": preview_id},
        )

    @pytest.mark.asyncio
    async def test_rejects_wrong_preview_id(self, library):
        self._setup_pending_delta(library, "correct-id")
        call = library.sync.sync_apply_delta("wrong-id")

        with pytest.raises(Refused) as excinfo:
            await call

        assert excinfo.value.reason == "stale_preview"

    @pytest.mark.asyncio
    async def test_rejects_when_no_pending_delta(self, library):
        assert library.sync._pending_delta is None
        call = library.sync.sync_apply_delta("any-id")

        with pytest.raises(Refused) as excinfo:
            await call

        assert excinfo.value.reason == "stale_preview"

    @pytest.mark.asyncio
    async def test_rejected_when_run_in_flight_preserves_delta(self, library):
        """RC-OVERLAP (#1202): an apply landing while a run is already in flight
        is rejected by the admission guard WITHOUT consuming the staged delta or
        disturbing the active run id.
        """
        self._setup_pending_delta(library, "pv-1")
        box = library.sync._box
        assert box.try_begin_run("active-run", kind=SyncRunKind.APPLY) is True
        call = library.sync.sync_apply_delta("pv-1")

        with pytest.raises(Refused) as excinfo:
            await call

        assert (excinfo.value.reason, excinfo.value.message) == ("sync_in_progress", "Sync already in progress")
        # The active run is untouched and the staged delta survives for the
        # legitimate apply.
        assert box.current_sync_id == "active-run"
        assert box.pending_delta is not None
        assert box.pending_delta.preview_id == "pv-1"

    @pytest.mark.asyncio
    async def test_rejects_when_preview_older_than_max_age(self, library):
        """Preview snapshots older than 30 minutes are stale.

        Regression for #345: sync_apply_delta previously only validated
        preview_id, so a user could leave the preview open for hours and
        apply a stale RomM snapshot — silent data corruption.
        """
        self._setup_pending_delta(library, "preview-abc")
        # Advance the clock past the 30-minute max age.
        library.sync._orchestrator._clock.advance(1801)
        call = library.sync.sync_apply_delta("preview-abc")

        with pytest.raises(Refused) as excinfo:
            await call

        assert excinfo.value.reason == "stale_preview"
        assert "30 minutes" in excinfo.value.message
        # Stale delta is cleared so a repeat apply can't pick it up.
        assert library.sync._pending_delta is None

    @pytest.mark.asyncio
    async def test_accepts_when_preview_just_under_max_age(self, library, emit):
        """Snapshots within the TTL window apply normally."""

        self._setup_pending_delta(library, "preview-xyz")
        # Apply runs the per-unit pipeline as a fire-and-forget task; stub
        # it out so the test can assert dispatch without driving the full
        # pipeline (the per-unit driver is covered in TestDoSyncPerUnit).
        library.sync._orchestrator._do_sync_per_unit = AsyncMock()
        # Just under the 30-minute window.
        library.sync._orchestrator._clock.advance(1799)

        result = await library.sync.sync_apply_delta("preview-xyz")

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_dispatches_per_unit_without_cached_queue(self, library, emit):
        """Apply dispatches ``_do_sync_per_unit`` with no prefetched cache (always live fetch)."""

        self._setup_pending_delta(library)
        do_sync = AsyncMock()
        library.sync._orchestrator._do_sync_per_unit = do_sync

        result = await library.sync.sync_apply_delta("test-preview-123")
        # Drain the spawned dispatch.
        for _ in range(3):
            await asyncio.sleep(0)

        assert result["success"] is True
        # Per-unit dispatch was kicked off without any prefetched cache (live fetch).
        do_sync.assert_called_once()
        # The new signature takes no positional/keyword args.
        assert do_sync.call_args.args == ()
        assert do_sync.call_args.kwargs == {}

    @pytest.mark.asyncio
    async def test_apply_dispatches_per_unit_task(self, library, emit):
        """Apply transitions to RUNNING and dispatches the per-unit pipeline.

        The planned platform/rom counts are no longer written to a JSON
        ``sync_stats`` scalar — they land on the ``SyncRun`` record opened
        inside ``_do_sync_per_unit`` (covered in TestDoSyncPerUnit)."""

        self._setup_pending_delta(library)
        library.sync._orchestrator._do_sync_per_unit = AsyncMock()

        result = await library.sync.sync_apply_delta("test-preview-123")

        assert result["success"] is True
        assert library.sync._sync_state == SyncState.RUNNING

    @pytest.mark.asyncio
    async def test_clears_pending_delta(self, library, emit):
        self._setup_pending_delta(library)
        library.sync._orchestrator._do_sync_per_unit = AsyncMock()

        await library.sync.sync_apply_delta("test-preview-123")
        assert library.sync._pending_delta is None


class TestSyncCancelPreview:
    """Tests for sync_cancel_preview()."""

    @pytest.mark.asyncio
    async def test_clears_pending_delta(self, library):
        library.sync._box.stage_preview(
            preview_id="some-id",
            created_at=library.sync._orchestrator._clock.time(),
            answer={"success": True, "preview_id": "some-id"},
        )
        result = library.sync.sync_cancel_preview()
        assert library.sync._pending_delta is None
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_returns_success(self, library):
        result = library.sync.sync_cancel_preview()
        assert result == {"success": True}


class TestGetPendingPreview:
    """Tests for get_pending_preview().

    The staged snapshot is what a panel that was navigated away from asks
    for on its next mount, so the read hands back the preview answer
    verbatim, answers "nothing pending" as a success, and drops a snapshot
    the apply would refuse anyway.
    """

    @staticmethod
    def _preview_setup(library, fake_romm_api):
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_pending(self, library):
        assert library.sync._pending_delta is None
        assert library.sync.get_pending_preview() == {"success": True, "preview": None}

    @pytest.mark.asyncio
    async def test_withholds_the_snapshot_while_a_run_is_in_flight(self, make_library, fake_romm_api):
        """A panel that remounts mid-run must not be handed a card to render over
        the run's own progress rows. The snapshot is withheld, not discarded —
        the frontend's own store guard can be wrong about a live run exactly
        when it matters (an apply refused with ``sync_in_progress`` retracts the
        optimistic running flag while the backend keeps the delta), so the
        backend is the one that has to be right.
        """
        library = make_library(romm_api=fake_romm_api)
        self._preview_setup(library, fake_romm_api)
        await library.sync.sync_preview()
        box = library.sync._box
        assert box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True

        assert library.sync.get_pending_preview() == {"success": True, "preview": None}
        assert box.pending_delta is not None

    @pytest.mark.asyncio
    async def test_hands_the_snapshot_back_once_the_run_is_over(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._preview_setup(library, fake_romm_api)
        fresh = await library.sync.sync_preview()
        box = library.sync._box
        box.try_begin_run("run-1", kind=SyncRunKind.APPLY)
        assert library.sync.get_pending_preview() == {"success": True, "preview": None}

        box.finish_run("run-1")

        assert library.sync.get_pending_preview() == {"success": True, "preview": fresh}

    @pytest.mark.asyncio
    async def test_hands_back_the_exact_answer_sync_preview_returned(self, make_library, fake_romm_api):
        """The restored card must be what the user was shown — the same payload,
        not a second assembly of it that can drift from the first."""
        library = make_library(romm_api=fake_romm_api)
        self._preview_setup(library, fake_romm_api)

        fresh = await library.sync.sync_preview()
        restored = library.sync.get_pending_preview()

        assert fresh["success"] is True
        assert restored == {"success": True, "preview": fresh}

    @pytest.mark.asyncio
    async def test_answer_carries_the_absolute_expiry_deadline(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._preview_setup(library, fake_romm_api)
        clock = library.sync._orchestrator._clock

        fresh = await library.sync.sync_preview()

        assert fresh["expires_at"] == clock.time() + 1800

    @pytest.mark.asyncio
    async def test_over_age_snapshot_is_dropped_and_answered_as_none(self, make_library, fake_romm_api):
        """Past the TTL the read answers "nothing pending" AND clears the box —
        the apply refuses the same snapshot, so leaving it staged would only
        offer the user an Apply that cannot succeed."""
        library = make_library(romm_api=fake_romm_api)
        self._preview_setup(library, fake_romm_api)
        await library.sync.sync_preview()
        assert library.sync._pending_delta is not None
        library.sync._orchestrator._clock.advance(1801)

        assert library.sync.get_pending_preview() == {"success": True, "preview": None}
        assert library.sync._pending_delta is None

    @pytest.mark.asyncio
    async def test_just_under_the_ttl_is_still_handed_back(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._preview_setup(library, fake_romm_api)
        fresh = await library.sync.sync_preview()
        library.sync._orchestrator._clock.advance(1799)

        assert library.sync.get_pending_preview() == {"success": True, "preview": fresh}

    @pytest.mark.asyncio
    async def test_cancelled_preview_leaves_nothing_to_hand_back(self, make_library, fake_romm_api):
        """A cancel landing after the unit loop stages no delta (#1202), so the
        read has nothing to restore — the panel comes back to an idle page."""
        library = make_library(romm_api=fake_romm_api)
        self._preview_setup(library, fake_romm_api)
        preview = library.sync._preview
        box = library.sync._box
        orig_fetch = preview._fetch_preview_unit

        async def fetch_then_cancel(unit, *args, **kwargs):
            await orig_fetch(unit, *args, **kwargs)
            box.request_cancel()

        preview._fetch_preview_unit = fetch_then_cancel
        call = library.sync.sync_preview()

        with pytest.raises(Refused) as excinfo:
            await call

        assert excinfo.value.reason == "cancelled"
        assert library.sync.get_pending_preview() == {"success": True, "preview": None}


# ── Tests for uncovered helper methods in library_sync.py ──────────


class TestSyncControl:
    """Tests for start_sync, cancel_sync, sync_heartbeat."""

    async def test_start_sync_when_idle(self, library):
        result = await library.sync.start_sync()
        assert result["success"] is True
        assert library.sync._sync_state == SyncState.RUNNING

    async def test_start_sync_rejects_when_running(self, library):
        library.sync._box.sync_state = SyncState.RUNNING
        call = library.sync.start_sync()

        with pytest.raises(Refused) as excinfo:
            await call

        assert excinfo.value.reason == "sync_in_progress"
        assert "already in progress" in excinfo.value.message

    def test_cancel_sync_when_running(self, library):
        library.sync._box.sync_state = SyncState.RUNNING
        result = library.sync.cancel_sync()
        assert result["success"] is True
        assert library.sync._sync_state == SyncState.CANCELLING

    def test_cancel_sync_when_idle(self, library):
        result = library.sync.cancel_sync()
        assert result["success"] is True
        assert "No sync" in result["message"]

    def test_cancel_sync_with_matching_run_id_sets_cancelling(self, library):
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-B"
        result = library.sync.cancel_sync("run-B")
        assert result["success"] is True
        assert library.sync._sync_state == SyncState.CANCELLING

    def test_cancel_sync_with_stale_run_id_is_noop(self, library):
        """A cancel meant for run-A must NOT abort the fresh run-B (#1198).

        The regression case: run-A finalized to IDLE and run-B started fresh,
        then run-A's Cancel click lands. The argument-less cancel would flip
        run-B to CANCELLING; the run-scoped cancel ignores the stale id.
        """
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-B"
        result = library.sync.cancel_sync("run-A")
        assert result["success"] is True
        assert "stale" in result["message"].lower()
        assert library.sync._sync_state == SyncState.RUNNING

    def test_cancel_sync_with_none_run_id_cancels_unconditionally(self, library):
        """A falsy run_id (legacy caller / no id captured yet) always cancels."""
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-B"
        result = library.sync.cancel_sync(None)
        assert result["success"] is True
        assert library.sync._sync_state == SyncState.CANCELLING

    def test_cancel_sync_with_empty_run_id_cancels_unconditionally(self, library):
        """An empty-string run_id (the frontend's no-id-yet fallback) cancels."""
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-B"
        result = library.sync.cancel_sync("")
        assert result["success"] is True
        assert library.sync._sync_state == SyncState.CANCELLING

    def test_cancel_sync_stale_run_id_when_idle_is_no_op(self, library):
        """Idle short-circuits before the run-id check — no sync, plain no-op."""
        library.sync._box.current_sync_id = "run-B"
        result = library.sync.cancel_sync("run-A")
        assert result["success"] is True
        assert "No sync" in result["message"]

    def test_sync_heartbeat(self, library):
        old = library.sync._sync_last_heartbeat
        # Advance the injected FakeClock so monotonic moves forward.
        library.sync._orchestrator._clock.advance(0.01)
        result = library.sync.sync_heartbeat()
        assert result["success"] is True
        assert library.sync._sync_last_heartbeat > old


class TestEmitCancelled:
    """Tests for SyncProgress.emit_cancelled().

    ``emit_cancelled`` emits the terminal CANCELLED progress snapshot only; the
    IDLE/None reset of the run-lifecycle pair is owned by the caller's
    ``finally: box.finish_run(run_id)`` (#1202), so this method leaves
    ``sync_state`` / ``current_sync_id`` untouched.
    """

    @pytest.mark.asyncio
    async def test_emits_cancelled_progress_snapshot(self, library, emit):
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._sync_progress = {"running": True, "current": 5, "total": 10}

        await library.sync._progress.emit_cancelled("Sync cancelled")

        assert library.sync._sync_progress["running"] is False
        assert library.sync._sync_progress["stage"] == "cancelled"
        assert library.sync._sync_progress["message"] == "Sync cancelled"

    @pytest.mark.asyncio
    async def test_does_not_reset_run_lifecycle(self, library):
        """emit_cancelled only emits — the terminal ``finally`` owns the reset.

        Leaving ``current_sync_id`` set here is what lets the run-scoped
        ``finish_run(run_id)`` later decide whether this run still owns the slot.
        """
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._sync_progress = {"running": True}
        library.sync._box.current_sync_id = "sync-abc"

        await library.sync._progress.emit_cancelled("Sync cancelled")

        assert library.sync._sync_state == SyncState.RUNNING
        assert library.sync._current_sync_id == "sync-abc"


class TestGetSyncStatus:
    """Backend-authoritative sync status query.

    ``get_sync_status`` returns the persisted progress snapshot so a
    freshly remounted QAM can recover in-flight state without waiting on
    a live ``sync_progress`` event.
    """

    def test_returns_idle_default_when_no_sync(self, library):
        status = library.sync.get_sync_status()
        assert status["running"] is False
        assert status["stage"] == ""

    def test_returns_live_snapshot_mid_sync(self, library):
        snapshot = {
            "running": True,
            "stage": "applying",
            "current": 3,
            "total": 10,
            "message": "N64 (1/2)",
            "step": 1,
            "totalSteps": 2,
        }
        library.sync._sync_progress = snapshot
        library.sync._box.try_begin_run("run-live", kind=SyncRunKind.APPLY)

        status = library.sync.get_sync_status()

        assert status == {**snapshot, "inFlight": True}
        assert status["running"] is True
        assert status["stage"] == "applying"

    def test_in_flight_is_the_lifecycle_not_a_reading_of_the_frame(self, library):
        """The two answer different questions and are allowed to disagree.

        A cancel drain is the standing example: ``emit_cancelled`` writes the
        CANCELLED frame — ``running: False`` — while the run still owns the slot,
        so a frontend inferring "no run" from the frame would retract a run that
        is still winding down. It reads ``inFlight`` instead.
        """
        box = library.sync._box
        box.try_begin_run("run-1", kind=SyncRunKind.APPLY)
        box.request_cancel("run-1")
        library.sync._sync_progress = {
            "running": False,
            "stage": "cancelled",
            "message": "Sync cancelled",
            "runId": "run-1",
        }

        status = library.sync.get_sync_status()

        assert status["running"] is False
        assert status["inFlight"] is True

    def test_in_flight_is_false_once_the_run_is_finished(self, library):
        """And the panel's licence to retract: the run is over, said plainly.

        Without this the reset above makes a finished run and one that never
        started the same answer, so a lost terminal frame would leave every later
        mount believing a run is live with nothing on the page to escape it.
        """
        box = library.sync._box
        box.try_begin_run("run-1", kind=SyncRunKind.APPLY)
        box.finish_run("run-1")

        status = library.sync.get_sync_status()

        assert status["inFlight"] is False
        assert status["running"] is False

    @pytest.mark.asyncio
    async def test_emit_progress_sub_stage_rides_event_and_status(self, library, emit):
        """The ``sub_stage`` kwarg rides the payload as the camelCase ``subStage``
        key (matching ``totalSteps`` / ``runId``) on BOTH the emitted
        ``sync_progress`` event and the persisted snapshot that
        ``get_sync_status`` re-seeds a remounted QAM from (#1407)."""

        await library.sync._progress.emit_progress(
            SyncStage.FETCHING,
            current=2,
            total=7,
            message="Fetching GBA (page 2/7)",
            step=3,
            total_steps=8,
            sub_stage="fetch",
        )

        event_payloads = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_progress"]
        assert event_payloads, "emit_progress must emit a sync_progress event"
        assert event_payloads[-1]["subStage"] == "fetch"
        # Same value re-seeds a remounted QAM through get_sync_status.
        assert library.sync.get_sync_status()["subStage"] == "fetch"

    @pytest.mark.asyncio
    async def test_emit_progress_defaults_sub_stage_empty(self, library, emit):
        """A frame that names no phase carries an empty ``subStage`` — the bar
        reads it as "rest at the unit floor", never a stale phase (#1407)."""

        await library.sync._progress.emit_progress(SyncStage.FETCHING, message="Fetching GBA", step=3, total_steps=8)

        assert library.sync.get_sync_status()["subStage"] == ""


class TestRunKindOnTheWire:
    """The run's KIND is stated on every frame, never inferred from the stream.

    A preview run and an apply run emit the same stages over the same work
    queue, so nothing in the stream tells them apart and no absence is evidence
    either — a plan's absence conflates "this is a preview" with "nobody has
    said yet". The kind is therefore claimed with the run slot and read off the
    box wherever a frame is built: ``emit_progress``, the CANCELLED terminal and
    the per-unit ERROR terminal.
    """

    def _frames(self, emit):
        return [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_progress"]

    async def test_start_sync_claims_the_slot_as_an_apply_run(self, library):
        library.sync._orchestrator._do_sync_per_unit = AsyncMock()

        assert (await library.sync.start_sync())["success"] is True

        assert library.sync._box.run_kind is SyncRunKind.APPLY

    @pytest.mark.asyncio
    async def test_apply_delta_claims_the_slot_as_an_apply_run(self, library):
        library.sync._orchestrator._do_sync_per_unit = AsyncMock()
        library.sync._box.stage_preview(
            preview_id="p1",
            created_at=library.sync._orchestrator._clock.time(),
            answer={"success": True, "preview_id": "p1"},
        )

        assert (await library.sync.sync_apply_delta("p1"))["success"] is True

        assert library.sync._box.run_kind is SyncRunKind.APPLY

    @pytest.mark.asyncio
    async def test_every_preview_frame_says_preview_including_the_terminal(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        assert (await library.sync.sync_preview())["success"] is True

        frames = self._frames(emit)
        assert len(frames) > 1, "a preview emits a discovering frame and one per unit"
        assert {f["runKind"] for f in frames} == {"preview"}
        # The terminal frame carries it too — it is the one a panel that arrived
        # late is most likely to be reading.
        assert frames[-1]["stage"] == "done"
        assert frames[-1]["runKind"] == "preview"

    @pytest.mark.asyncio
    async def test_every_apply_frame_says_apply(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        assert library.sync._box.try_begin_run("run-apply", kind=SyncRunKind.APPLY) is True

        await library.sync._orchestrator._do_sync_per_unit()

        frames = self._frames(emit)
        assert frames, "an apply run narrates its units"
        assert {f["runKind"] for f in frames} == {"apply"}

    @pytest.mark.asyncio
    async def test_the_snapshot_a_remounted_qam_re_seeds_from_carries_it(self, library):
        """The case an inference cannot reach: the frontend reloaded mid-run.

        Its store starts empty, so the only thing that can tell it what the run
        it is watching is doing is the snapshot ``get_sync_status`` hands back.
        """
        assert library.sync._box.try_begin_run("run-live", kind=SyncRunKind.PREVIEW) is True
        await library.sync._progress.emit_progress(SyncStage.FETCHING, message="Fetching N64", step=1, total_steps=2)

        assert library.sync.get_sync_status()["runKind"] == "preview"

    @pytest.mark.asyncio
    async def test_the_cancelled_terminal_carries_it(self, library, emit):
        assert library.sync._box.try_begin_run("run-cancel", kind=SyncRunKind.PREVIEW) is True

        await library.sync._progress.emit_cancelled("Sync cancelled")

        frames = self._frames(emit)
        assert frames[-1]["stage"] == "cancelled"
        assert frames[-1]["runKind"] == "preview"

    @pytest.mark.asyncio
    async def test_the_per_unit_error_terminal_carries_it(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        # Blow up inside the unit loop, after the plan — that path's ERROR frame
        # comes from ``SyncProgress.write_error_frame``, not ``emit_progress``.
        library.unit_applier.apply_unit = AsyncMock(side_effect=RuntimeError("boom"))
        assert library.sync._box.try_begin_run("run-err", kind=SyncRunKind.APPLY) is True

        await library.sync._orchestrator._do_sync_per_unit()

        frames = self._frames(emit)
        assert frames[-1]["stage"] == "error"
        assert frames[-1]["runKind"] == "apply"

    @pytest.mark.asyncio
    async def test_a_finished_run_leaves_no_kind_behind(self, make_library, fake_romm_api):
        """The idle snapshot states no kind, so a later mount reads "not
        established" rather than the previous run's answer."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        assert (await library.sync.sync_preview())["success"] is True

        assert library.sync._box.run_kind is None
        assert library.sync.get_sync_status()["runKind"] == ""


class TestSyncPreviewErrorHandling:
    """Tests for sync_preview error paths."""

    @pytest.mark.asyncio
    async def test_general_exception_reaches_the_caller(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        # Cause the platforms listing to blow up — exception bubbles up
        # through build_work_queue into sync_preview exactly like a
        # mid-paginate RomM failure would in production.
        failure = RuntimeError("Something broke")
        fake_romm_api.list_platforms_side_effect = failure
        library.settings["enabled_platforms"] = {"1": True}
        call = library.sync.sync_preview()

        with pytest.raises(RuntimeError) as excinfo:
            await call

        assert excinfo.value is failure
        assert library.sync._sync_state == SyncState.IDLE
        # Error path evicts any pending delta.
        assert library.sync._pending_delta is None

    @pytest.mark.parametrize(
        ("kind", "failing_listing"),
        [
            ("standard", "list_collections_side_effect"),
            ("smart", "list_smart_collections_side_effect"),
            ("virtual", "list_virtual_collections_side_effect"),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_failed_collection_listing_fails_the_preview(
        self, make_library, fake_romm_api, emit, kind, failing_listing
    ):
        """A collection listing that fails fails the preview as a failed platform listing does (#2112)."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.settings["enabled_collections"] = {kind: {"7": True}}
        setattr(fake_romm_api, failing_listing, RommConnectionError("Connection refused"))
        call = library.sync.sync_preview()

        with pytest.raises(RommConnectionError) as excinfo:
            await call

        message = "Server unreachable — check your URL and ensure RomM is running"
        assert classify_error(excinfo.value) == ("server_unreachable", message)
        progress = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_progress"]
        assert (progress[-1]["stage"], progress[-1]["message"], progress[-1]["running"]) == ("error", message, False)
        assert library.sync._pending_delta is None

    @pytest.mark.asyncio
    async def test_cancelled_error_refuses_with_cancelled(self, make_library, fake_romm_api, emit):
        """A cooperative cancel during sync_preview refuses with ``cancelled`` rather
        than letting ``SyncCancelled`` out of the use case (#1035).

        A cancel is the use case's own outcome, not a transport failure:
        ``SyncCancelled`` itself would reach the panel as a ``backend_exception``
        error (``host/dispatch.py``). The cooperative cancel — the dedicated
        ``SyncCancelled``, matching the production signal raised by
        ``fetcher._check_cancelling`` and the per-unit checkpoint — must surface as
        the ``cancelled`` refusal, with sync_state IDLE and no pending delta.
        ``SyncCancelled`` is an ``Exception``; the clause order routes it into
        ``except SyncCancelled``, which sits above the generic ``except Exception``.
        """

        from domain.sync_state import SyncCancelled

        library = make_library(romm_api=fake_romm_api)

        fake_romm_api.list_platforms = MagicMock(side_effect=SyncCancelled("Sync cancelled"))
        library.settings["enabled_platforms"] = {"1": True}

        # sync_preview only runs from IDLE — guard against a leaked non-IDLE state.
        assert library.sync._sync_state == SyncState.IDLE
        call = library.sync.sync_preview()

        with pytest.raises(Refused) as excinfo:
            await call

        assert (excinfo.value.reason, excinfo.value.message) == ("cancelled", "Sync cancelled")
        assert library.sync._sync_state == SyncState.IDLE
        assert library.sync._pending_delta is None
        # The cooperative signal genuinely originated from the fetch.
        fake_romm_api.list_platforms.assert_called()

    @staticmethod
    def _stage_an_earlier_preview(library):
        library.sync._box.stage_preview(
            preview_id="earlier",
            created_at=library.sync._orchestrator._clock.time(),
            answer={"success": True, "preview_id": "earlier"},
        )

    @staticmethod
    def _progress_frames(emit):
        return [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_progress"]

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(RommConnectionError("Connection refused"), id="romm-error"),
            pytest.param(RuntimeError("Something broke"), id="other-error"),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_failed_preview_discards_the_staged_preview_and_ends_with_an_error_frame(
        self, make_library, fake_romm_api, emit, failure
    ):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.list_platforms_side_effect = failure
        library.settings["enabled_platforms"] = {"1": True}
        self._stage_an_earlier_preview(library)
        call = library.sync.sync_preview()

        with pytest.raises(type(failure)) as excinfo:
            await call

        assert excinfo.value is failure
        assert library.sync._pending_delta is None
        last = self._progress_frames(emit)[-1]
        assert (last["stage"], last["message"], last["running"]) == ("error", classify_error(failure)[1], False)
        assert library.sync._sync_state == SyncState.IDLE

    @pytest.mark.asyncio
    async def test_a_cancelled_preview_discards_the_staged_preview_and_ends_with_a_cancelled_frame(
        self, make_library, fake_romm_api, emit
    ):
        from domain.sync_state import SyncCancelled

        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.list_platforms = MagicMock(side_effect=SyncCancelled("Sync cancelled"))
        library.settings["enabled_platforms"] = {"1": True}
        self._stage_an_earlier_preview(library)
        call = library.sync.sync_preview()

        with pytest.raises(Refused) as excinfo:
            await call

        assert (excinfo.value.reason, excinfo.value.message) == ("cancelled", "Sync cancelled")
        assert library.sync._pending_delta is None
        last = self._progress_frames(emit)[-1]
        assert (last["stage"], last["message"], last["running"]) == ("cancelled", "Sync cancelled", False)


# ──────────────────────────────────────────────────────────────
# Per-unit pipeline tests
# ──────────────────────────────────────────────────────────────


class TestBuildWorkQueue:
    """Phase 0 of the per-unit pipeline: enumerate platforms + collections without fetching ROMs."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_nothing_enabled(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {}

        units = await library.sync._fetcher.build_work_queue()
        assert units == []

    @pytest.mark.asyncio
    async def test_includes_enabled_platforms(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.platforms = [
            {"id": 1, "name": "N64", "slug": "n64", "rom_count": 12},
            {"id": 2, "name": "SNES", "slug": "snes", "rom_count": 99},
            {"id": 3, "name": "GBA", "slug": "gba", "rom_count": 5},
        ]
        library.settings["enabled_platforms"] = {"1": True, "2": False, "3": True}
        library.settings["enabled_collections"] = {}

        units = await library.sync._fetcher.build_work_queue()
        assert [u.name for u in units] == ["N64", "GBA"]
        assert all(u.type == "platform" for u in units)
        assert units[0].rom_count == 12

    @pytest.mark.asyncio
    async def test_includes_enabled_collections_after_platforms(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 4}]
        fake_romm_api.collections = [{"id": 7, "name": "Favorites", "rom_count": 3, "is_favorite": True}]
        fake_romm_api.smart_collections = [{"id": 5, "name": "Filter", "rom_count": 2}]
        fake_romm_api.virtual_collections["franchise"] = [{"id": 9, "name": "Metroid", "rom_count": 8}]
        library.settings["enabled_platforms"] = {"1": True}
        library.settings["enabled_collections"] = {
            "standard": {"7": True},
            "smart": {"5": True},
            "virtual": {"9": True},
        }

        units = await library.sync._fetcher.build_work_queue()
        assert [(u.type, u.name) for u in units] == [
            ("platform", "N64"),
            ("collection", "Favorites"),
            ("collection", "Filter"),
            ("collection", "Metroid"),
        ]
        assert units[1].collection_kind == "standard"
        assert units[2].collection_kind == "smart"
        assert units[3].collection_kind == "virtual"


class TestFetchPlatformUnit:
    """Per-unit platform ROM fetch with incremental-skip path."""

    @pytest.mark.asyncio
    async def test_full_fetch_when_no_registry(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}, {"id": 11, "name": "B"}],
        )

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        roms, skipped = await library.sync._fetcher.fetch_platform_unit(unit)
        assert skipped is False
        assert [r["id"] for r in roms] == [10, 11]
        assert roms[0]["platform_name"] == "N64"

    @pytest.mark.asyncio
    async def test_skips_when_registry_matches_count(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        # No ROMs seeded on the fake; the platform's listing reports zero
        # updates after the completion stamp so the incremental-skip path fires.
        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=2)
        _seed_rom_row(
            library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64", sibling_group_key="romm:10:1"
        )
        _seed_rom_row(
            library, 11, app_id=1011, platform_slug="n64", name="B", fs_name="b.z64", sibling_group_key="romm:11:1"
        )

        roms, skipped = await library.sync._fetcher.fetch_platform_unit(unit)
        assert skipped is True
        assert {r["id"] for r in roms} == {10, 11}

    @pytest.mark.asyncio
    async def test_null_group_key_forces_full_fetch_for_backfill(self, make_library, fake_romm_api):
        # A pre-#1295 registry row (NULL sibling_group_key) must NOT skip even
        # when count matches + zero server updates — the platform is re-fetched so
        # the commit backfills its version metadata. One bound row un-backfilled
        # is enough to force the whole platform's fetch.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}, {"id": 11, "name": "B"}],
        )
        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        _seed_completed_run(library, at="2025-01-01T00:00:00Z")
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64", sibling_group_key=None)
        _seed_rom_row(
            library, 11, app_id=1011, platform_slug="n64", name="B", fs_name="b.z64", sibling_group_key="igdb:5:1"
        )

        roms, skipped = await library.sync._fetcher.fetch_platform_unit(unit)
        assert skipped is False
        assert {r["id"] for r in roms} == {10, 11}

    @pytest.mark.asyncio
    async def test_full_fetch_when_count_mismatch(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        # roms says 1 ROM but the unit reports 3 → incremental-skip
        # check still says zero updated (no updated_at > last_sync), but
        # count mismatch forces a full fetch.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}, {"id": 11, "name": "B"}, {"id": 12, "name": "C"}],
        )
        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=3)
        _seed_completed_run(library, at="2025-01-01T00:00:00Z")
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A")

        roms, skipped = await library.sync._fetcher.fetch_platform_unit(unit)
        assert skipped is False
        assert len(roms) == 3


class TestFetchCollectionUnit:
    """Per-unit collection ROM fetch with cross-unit deduplication."""

    @pytest.mark.asyncio
    async def test_returns_new_roms_and_member_ids(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {
            1: {"id": 1, "platform_id": 1, "platform_name": "N64", "collection_ids": [7]},
            2: {"id": 2, "platform_id": 2, "platform_name": "SNES", "collection_ids": [7]},
            3: {"id": 3, "platform_id": 3, "platform_name": "GBA", "collection_ids": [7]},
        }
        unit = WorkUnit(
            type="collection",
            id="7",
            name="Faves",
            slug="",
            rom_count=3,
            collection_kind="standard",
            member_rom_ids=(1, 2, 3),
            unwalked_platform_ids=(1, 2, 3),
        )
        synced: set[int] = set()
        new_roms, ids, _skipped, _restamp_settles = await library.collection_fetcher.fetch_collection_unit(unit, synced)
        assert [r["id"] for r in new_roms] == [1, 2, 3]
        assert ids == [1, 2, 3]
        assert synced == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_dedups_against_already_synced(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.roms = {
            1: {"id": 1, "platform_id": 1, "platform_name": "N64", "virtual_collection_ids": ["9"]},
            2: {"id": 2, "platform_id": 2, "platform_name": "SNES", "virtual_collection_ids": ["9"]},
        }
        unit = WorkUnit(
            type="collection",
            id="9",
            name="Metroid",
            slug="",
            rom_count=2,
            collection_kind="virtual",
            member_rom_ids=(1, 2),
            unwalked_platform_ids=(2,),
        )

        # rom_id=1 was already fetched via a platform unit
        synced: set[int] = {1}
        new_roms, ids, _skipped, _restamp_settles = await library.collection_fetcher.fetch_collection_unit(unit, synced)
        assert [r["id"] for r in new_roms] == [2]
        # All collection rom_ids reported back even if not in new_roms
        assert ids == [1, 2]


class TestDoSyncPerUnit:
    """End-to-end orchestration of the per-unit pipeline."""

    @pytest.mark.asyncio
    async def test_empty_queue_terminates_cleanly(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        # No platforms enabled → empty work queue.
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {}
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        assert library.sync._sync_state == SyncState.IDLE
        # Sync plan was emitted with empty units
        plan_events = [c for c in emit.call_args_list if c[0][0] == "sync_plan"]
        assert len(plan_events) == 1
        assert plan_events[0][0][1]["total_units"] == 0

    @pytest.mark.asyncio
    async def test_emits_sync_plan_with_queue(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)

        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 2}]
        library.settings["enabled_platforms"] = {"1": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-plan"

        await library.sync._orchestrator._do_sync_per_unit()

        plan_events = [c for c in emit.call_args_list if c[0][0] == "sync_plan"]
        assert len(plan_events) == 1
        payload = plan_events[0][0][1]
        assert payload["total_units"] == 1
        assert payload["units"][0]["name"] == "N64"
        # The frontend captures this run_id to scope a later Cancel click (#1198).
        assert payload["run_id"] == "run-plan"

    @pytest.mark.asyncio
    async def test_sync_plan_carries_skip_aware_estimate_fields(self, make_library, fake_romm_api, emit):
        """#1382: platform units ride predicted_skip / collapsed_count, the raw
        ``total_roms`` stays untouched (backward compat), and the additive
        ``total_estimated_items`` zero-weights predicted skips and prices the
        rest at their collapsed count (raw fallback)."""

        library = make_library(romm_api=fake_romm_api)

        # N64: skip-eligible — stamp matches the server count, 2 persisted rows
        # in one sibling group with a bound representative → predicted skip,
        # collapsed count 1. GBA: never synced → no skip, raw-count fallback.
        fake_romm_api.platforms = [
            {"id": 1, "name": "N64", "slug": "n64", "rom_count": 2},
            {"id": 2, "name": "GBA", "slug": "gba", "rom_count": 5},
        ]
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00", rom_count=2)
        _seed_rom_row(library, 10, app_id=1001, platform_slug="n64", sibling_group_key="igdb:100:1")
        _seed_rom_row(library, 11, app_id=None, platform_slug="n64", sibling_group_key="igdb:100:1")
        # A collection unit, left UNSTAMPED here: its membership is knowable only
        # from a completion stamp, so it carries no estimate field at all and
        # weighs its raw rom_count. (A stamped collection does ride bound_count,
        # #1511 — covered in test_fetcher.)
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20, 21, 22])
        library.settings["enabled_collections"] = {"standard": {"7": True}, "smart": {}, "virtual": {}}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-est"

        await library.sync._orchestrator._do_sync_per_unit()

        plan_events = [c for c in emit.call_args_list if c[0][0] == "sync_plan"]
        assert len(plan_events) == 1
        payload = plan_events[0][0][1]
        # Raw planned total is untouched: 2 + 5 + 3.
        assert payload["total_roms"] == 10
        # Skip-aware: N64 predicted-skip → 0, GBA raw 5, collection raw 3.
        assert payload["total_estimated_items"] == 8
        units = {u["name"]: u for u in payload["units"]}
        assert units["N64"]["predicted_skip"] is True
        assert units["N64"]["collapsed_count"] == 1
        assert units["GBA"]["predicted_skip"] is False
        assert "collapsed_count" not in units["GBA"]
        assert "predicted_skip" not in units["Faves"]
        assert "collapsed_count" not in units["Faves"]
        assert "bound_count" not in units["Faves"]

    @pytest.mark.asyncio
    async def test_unstamped_zero_delta_platform_restamps_and_records_run(self, make_library, fake_romm_api, emit):
        """#1416: an unstamped platform with a 0 shortcut delta still runs the apply.

        A late-ack recovery leaves the platform complete but unstamped. The next
        apply must NOT wholesale-skip it (no stamp): its delta-restricted apply
        emits a single empty final chunk whose commit re-writes the completion
        stamp, and the run records a fresh completed ``SyncRun`` — so the platform
        stops full-fetching forever and the "interrupted" status heals.
        """

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Keep", "fs_name": "keep.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        # Bound + content-unchanged → empty delta. NO completion stamp: the
        # complete-but-unstamped residue a heartbeat-timeout's late ack leaves.
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")
        with library.uow as uow:
            assert uow.platform_sync_state.get("n64") is None

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-restamp"

        await library.sync._orchestrator._do_sync_per_unit()

        # The apply ran despite the empty delta: a single empty final chunk fired.
        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(unit_events) == 1
        assert unit_events[0]["shortcuts"] == []
        assert unit_events[0]["chunk_count"] == 1

        # The empty chunk's commit re-wrote the platform stamp, and the run
        # recorded a fresh completed SyncRun.
        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
            assert stamp is not None
            assert stamp.rom_count == 1
            completed = uow.sync_runs.get_latest_completed()
            assert completed is not None
            assert completed.id == "run-restamp"

    @pytest.mark.asyncio
    async def test_processes_each_unit_in_order(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)

        # Live-fetch platforms (no last_sync, empty registry) so both
        # units reach the apply branch and emit ``sync_apply_unit``.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[{"id": 20, "name": "B"}],
        )
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_unit, event):
            event.set()
            return {str(_unit.id * 10): 9000 + int(_unit.id)}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(unit_events) == 2
        assert unit_events[0]["unit_name"] == "N64"
        assert unit_events[1]["unit_name"] == "GBA"
        assert unit_events[0]["unit_index"] == 0
        assert unit_events[1]["unit_index"] == 1

    @pytest.mark.asyncio
    async def test_emitted_unit_carries_run_id(self, make_library, fake_romm_api, emit):
        """Each ``sync_apply_unit`` payload carries the run's ``current_sync_id``.

        The frontend keys its once-per-run existing-shortcut scan cache off
        ``run_id``, so every unit emitted within a run must carry the same id.
        """

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[{"id": 20, "name": "B"}],
        )
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._reporter.commit_unit_results = AsyncMock()  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-abc"

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(unit_events) == 2
        assert all(e["run_id"] == "run-abc" for e in unit_events)

    @pytest.mark.asyncio
    async def test_emitted_shortcuts_carry_install_launch_options(self, make_library, fake_romm_api, emit):
        """Installed ROMs get the full launch command; uninstalled ROMs get ``""``.

        The orchestrator builds the ``{rom_id: file_path}`` map from
        ``rom_installs`` and passes it to ``build_shortcuts_data`` so the
        emitted ``sync_apply_unit`` shortcuts carry per-ROM launch options.
        """

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Installed"}, {"id": 11, "name": "NotInstalled"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        # rom 10 has an install record; rom 11 does not.
        _seed_install(library, 10, file_path="/roms/n64/installed.z64", platform_slug="n64")

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._reporter.commit_unit_results = AsyncMock()  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(unit_events) == 1
        by_rom = {s["rom_id"]: s for s in unit_events[0]["shortcuts"]}
        assert (
            by_rom[10]["launch_options"]
            == "flatpak run --nosocket=wayland net.retrodeck.retrodeck '/roms/n64/installed.z64'"
        )
        assert by_rom[11]["launch_options"] == ""

    @pytest.mark.asyncio
    async def test_apply_bakes_emulator_override_into_launch_options(self, make_library, fake_romm_api, emit):
        """A pinned ``emulator_override`` bakes the ``-e`` form; a NULL pin stays plain (R6).

        Two installed ROMs on the same platform: rom 10 carries a resolvable
        override (``-e`` baked), rom 11 has none (plain launch). Proves the
        sync-apply ``core_overrides`` map drives ``build_shortcuts_data`` per-ROM.
        """

        library = make_library(romm_api=fake_romm_api)
        library.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="PSX",
            slug="psx",
            roms=[{"id": 10, "name": "Pinned"}, {"id": 11, "name": "Plain"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_install(library, 10, file_path="/roms/psx/pinned.chd", platform_slug="psx")
        _seed_install(library, 11, file_path="/roms/psx/plain.chd", platform_slug="psx")
        with library.uow:
            library.uow.roms.set_emulator_override(10, "PCSX ReARMed")

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._reporter.commit_unit_results = AsyncMock()  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        by_rom = {s["rom_id"]: s for s in unit_events[0]["shortcuts"]}
        assert by_rom[10]["launch_options"] == (
            "flatpak run --nosocket=wayland net.retrodeck.retrodeck "
            '-e "%EMULATOR_RETROARCH% -L /var/config/retroarch/cores/pcsx_rearmed_libretro.so %ROM%" '
            "'/roms/psx/pinned.chd'"
        )
        assert (
            by_rom[11]["launch_options"]
            == "flatpak run --nosocket=wayland net.retrodeck.retrodeck '/roms/psx/plain.chd'"
        )
        assert "-e" not in by_rom[11]["launch_options"]

    @pytest.mark.asyncio
    async def test_apply_stale_override_bakes_plain_with_warning(self, make_library, fake_romm_api, caplog, emit):
        """A stale override LABEL (no longer in available_cores) bakes PLAIN + WARNs (B4)."""

        library = make_library(romm_api=fake_romm_api)
        # The options no longer carry the pinned label → label_to_invocation → None.
        library.core_info.available_cores = [
            {"core_so": "pcsx_rearmed_libretro", "label": "PCSX ReARMed", "is_default": True},
        ]

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="PSX",
            slug="psx",
            roms=[{"id": 10, "name": "Stale"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_install(library, 10, file_path="/roms/psx/stale.chd", platform_slug="psx")
        with library.uow:
            library.uow.roms.set_emulator_override(10, "Removed Core")

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._reporter.commit_unit_results = AsyncMock()  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING

        import logging

        with caplog.at_level(logging.WARNING):
            await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        by_rom = {s["rom_id"]: s for s in unit_events[0]["shortcuts"]}
        # Stale → PLAIN launch, never -e with a bogus core.
        assert (
            by_rom[10]["launch_options"]
            == "flatpak run --nosocket=wayland net.retrodeck.retrodeck '/roms/psx/stale.chd'"
        )
        assert "-e" not in by_rom[10]["launch_options"]
        assert "Removed Core" in caplog.text
        assert "no longer resolves" in caplog.text

    @pytest.mark.asyncio
    async def test_skipped_unit_short_circuits_apply(self, make_library, fake_romm_api, emit):
        """``skipped=True`` from the fetcher short-circuits the whole apply+commit branch.

        For a unit whose registry already matches the server-side ROM
        count and has no updates since ``last_sync``, none of these run:
        artwork download, ``_wait_for_unit_complete``, the
        ``sync_apply_unit`` emit, or the reporter's ``commit_unit_results``.
        The unit's reconstructed ROMs still join ``synced_rom_ids`` so
        the final stale-cleanup pass doesn't mistakenly remove them.
        """

        library = make_library(romm_api=fake_romm_api)

        # roms matches platform count + zero updates → incremental skip.
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        library.settings["enabled_platforms"] = {"1": True}

        download_artwork = AsyncMock(return_value={})
        library.cover_preparer._download_artwork = download_artwork
        wait_mock = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = wait_mock
        commit_mock = AsyncMock()
        library.sync._reporter.commit_unit_results = commit_mock  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        # Nothing on the apply branch ran.
        download_artwork.assert_not_called()
        wait_mock.assert_not_called()
        apply_events = [c for c in emit.call_args_list if c.args and c.args[0] == "sync_apply_unit"]
        assert apply_events == [], f"sync_apply_unit must not be emitted for a skipped unit, got: {apply_events}"
        commit_mock.assert_not_called()

        # Stale-cleanup still emits with an empty remove list — the
        # skipped unit's reconstructed ROMs joined synced_rom_ids so
        # rom_id 10 is not classified as stale.
        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert len(stale_events) == 1
        assert stale_events[0] == {"remove": []}

        # Blueprint invariant #1: a delta sync must NOT shrink platform
        # collections. The skipped platform's unchanged ROM (app_id 1010)
        # must still appear in the rebuilt ``platform_app_ids`` — the
        # collection is rebuilt from the full ``roms`` table, so a skipped
        # unit's rows survive and are re-emitted under their live name.
        collection_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_collections"]
        assert len(collection_events) == 1
        assert collection_events[0]["platform_app_ids"] == {"N64": [1010]}

    @pytest.mark.asyncio
    async def test_stale_entries_unbound_but_rows_kept_after_finalize(self, make_library, fake_romm_api, emit):
        """End-to-end: a stale ROM (disabled platform) is unbound during finalize —
        its ``shortcut_app_id`` is NULLed while the row survives (ADR-0007), not just
        dropped from the frontend via ``sync_stale``.

        Regression for the inflated ``get_sync_stats`` count: the orchestrator emits
        ``sync_stale`` so the frontend drops the shortcut, and the reporter unbinds the
        same rom_ids in ``uow.roms`` (NULL ``shortcut_app_id``, keep the row) so the
        bound-shortcut count matches the still-synced ROMs.
        """

        library = make_library(romm_api=fake_romm_api)

        # rom_id 10 is the live N64 ROM (synced this run). rom_id 99 is a leftover
        # from a now-disabled platform — present in roms but in no enabled unit.
        _seed_completed_run(library, at="2025-01-01T00:00:00Z")
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)
        _seed_rom_row(library, 10, app_id=1000, platform_slug="n64", name="A", fs_name="a.z64")
        _seed_rom_row(
            library, 99, app_id=9900, platform_slug="gba", name="Z", fs_name="z.gba", sibling_group_key="romm:seed:99"
        )
        # GBA is listed by RomM and turned off in Tender.
        fake_romm_api.platforms = [
            {"id": 1, "name": "N64", "slug": "n64", "rom_count": 1},
            {"id": 2, "name": "GBA", "slug": "gba", "rom_count": 0},
        ]
        library.settings["enabled_platforms"] = {"1": True, "2": False}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = AsyncMock(return_value={})
        library.sync._reporter.commit_unit_results = AsyncMock()  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        # Frontend was told to remove rom_id 99, carrying its bound app_id
        # captured before the finalize unbind NULLed the binding.
        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": [{"rom_id": 99, "app_id": 9900}], "prune_lease_token": "sync_stale:1"}]

        # rom 99 was unbound (NULL app_id) but its row survives; only the
        # synced ROM is still bound.
        with library.uow as uow:
            assert uow.roms.get(99).shortcut_app_id is None
            assert uow.roms.get(10).shortcut_app_id == 1000
            assert {r.rom_id for r in uow.roms.iter_all()} == {10, 99}

        # get_sync_stats reflects the bound count, not the pre-sync inflated count.
        stats = library.sync.get_sync_stats()
        assert stats["roms"] == 1
        assert stats["total_shortcuts"] == 1

    @pytest.mark.asyncio
    async def test_sync_stale_excludes_unbound_roms(self, make_library, fake_romm_api, emit):
        """An already-unbound stale ROM (NULL ``shortcut_app_id``) is excluded
        from the ``sync_stale`` payload — it has no Steam shortcut to remove.

        rom 10 is the live synced ROM, rom 99 is a bound stale ROM (carries its
        app_id), and rom 77 is an unbound leftover (cleared on a prior run). Only
        the bound stale ROM appears in ``remove``, each entry carrying its app_id.
        """

        library = make_library(romm_api=fake_romm_api)

        _seed_completed_run(library, at="2025-01-01T00:00:00Z")
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)
        _seed_rom_row(library, 10, app_id=1000, platform_slug="n64", name="A", fs_name="a.z64")
        _seed_rom_row(
            library, 99, app_id=9900, platform_slug="gba", name="Z", fs_name="z.gba", sibling_group_key="romm:seed:99"
        )
        _seed_rom_row(library, 77, app_id=None, platform_slug="snes", name="Y", fs_name="y.sfc")
        # GBA is listed by RomM and turned off in Tender.
        fake_romm_api.platforms = [
            {"id": 1, "name": "N64", "slug": "n64", "rom_count": 1},
            {"id": 2, "name": "GBA", "slug": "gba", "rom_count": 0},
        ]
        library.settings["enabled_platforms"] = {"1": True, "2": False}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = AsyncMock(return_value={})
        library.sync._reporter.commit_unit_results = AsyncMock()  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        # Only the bound stale ROM (99) is emitted; the unbound leftover (77) is excluded.
        assert stale_events == [{"remove": [{"rom_id": 99, "app_id": 9900}], "prune_lease_token": "sync_stale:1"}]

    @pytest.mark.asyncio
    async def test_appid_reuse_collision_excluded_from_sync_stale(self, make_library, fake_romm_api, emit):
        """A new server-issued rom_id reusing an old appId must NOT be wiped (#1036).

        Old row (rom 1, app 5000) survives a server switch / re-import; the new
        ROM (rom 2) for the same game produces the SAME appId (unchanged
        exe+name). The frontend re-acks app 5000 for rom 2; the real commit
        binds rom 2 and records app 5000 in ``committed_app_ids``. The stale
        scan flags old rom 1 — but ``select_stale_removals`` excludes app 5000
        (bound this run), so ``sync_stale`` carries NO removal and the live
        shortcut survives."""

        library = make_library(romm_api=fake_romm_api)

        # Old colliding row from before the reassignment: rom 1 bound to app 5000.
        # No completed run is seeded so the platform full-fetches (no incremental
        # skip), exercising the real commit path for the new rom_id.
        _seed_rom_row(library, 1, app_id=5000, platform_slug="n64", name="A", fs_name="a.z64")
        # The server now serves the same game under a NEW rom_id (2).
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 2, "name": "A"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        # The frontend re-uses the same appId (CRC32 of unchanged exe+name) and
        # acks it for the new rom_id. The REAL commit runs so committed_app_ids
        # is populated and the repo unbinds the colliding sibling.
        async def ack_same_appid(_unit, event):
            event.set()
            return {"2": 5000}

        library.chunk_dispatcher._wait_for_unit_complete = ack_same_appid
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        # The load-bearing assertion: app 5000 is NOT emitted for removal.
        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": []}], (
            f"appId-reuse collision leaked a removal that would wipe the live shortcut: {stale_events}"
        )
        # The new row holds the binding; the old row is unbound (ADR-0007 — kept).
        with library.uow as uow:
            assert uow.roms.get(2).shortcut_app_id == 5000
            assert uow.roms.get(1).shortcut_app_id is None
            assert {r.rom_id for r in uow.roms.iter_all()} == {1, 2}

    @pytest.mark.asyncio
    async def test_genuinely_stale_still_removed_alongside_collision(self, make_library, fake_romm_api, emit):
        """A genuinely-stale ROM (its appId NOT bound this run) is still removed,
        even while a colliding appId is excluded — the fix narrows removals, it
        does not disable the stale path (#1036)."""

        library = make_library(romm_api=fake_romm_api)

        # rom 1 collides (app 5000 re-bound to rom 2 this run); rom 99 is a
        # genuinely-removed ROM on a now-disabled platform (app 9900, not re-bound).
        # No completed run seeded → full fetch (no skip) so the real commit runs.
        _seed_rom_row(library, 1, app_id=5000, platform_slug="n64", name="A", fs_name="a.z64")
        _seed_rom_row(library, 99, app_id=9900, platform_slug="gba", name="Z", fs_name="z.gba")
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 2, "name": "A"}],
        )
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[])
        library.settings["enabled_platforms"] = {"1": True, "2": False}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def ack_same_appid(_unit, event):
            event.set()
            return {"2": 5000}

        library.chunk_dispatcher._wait_for_unit_complete = ack_same_appid
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        # rom 99 (app 9900) is removed; the colliding app 5000 is excluded.
        assert stale_events == [{"remove": [{"rom_id": 99, "app_id": 9900}], "prune_lease_token": "sync_stale:1"}]

    @pytest.mark.asyncio
    async def test_group_emits_one_shortcut_per_sibling_group(self, make_library, fake_romm_api, emit):
        """A platform with a 3-version sibling group emits ONE shortcut for the
        game (ADR-0021); the non-representative dumps are persisted unbound."""

        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                # Three dumps of one game (shared igdb_id) + one unrelated game.
                {
                    "id": 10,
                    "name": "Zelda (USA)",
                    "igdb_id": 100,
                    "fs_name_no_ext": "zelda_usa",
                    "rom_user": {"is_main_sibling": True},
                },
                {"id": 11, "name": "Zelda (JP)", "igdb_id": 100, "fs_name_no_ext": "zelda_jp"},
                {"id": 12, "name": "Zelda (EU)", "igdb_id": 100, "fs_name_no_ext": "zelda_eu"},
                {"id": 20, "name": "Mario", "igdb_id": 200, "fs_name_no_ext": "mario"},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def ack_reps(_unit, event):
            event.set()
            return {str(rid): 9000 + rid for rid in library.sync._box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = ack_reps
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(unit_events) == 1
        emitted_ids = {sd["rom_id"] for sd in unit_events[0]["shortcuts"]}
        # ONE shortcut for the Zelda group (rep = the RomM default, rom 10) + Mario.
        assert emitted_ids == {10, 20}
        with library.uow as uow:
            # All four siblings are persisted; only the representatives bind.
            assert uow.roms.get(10).shortcut_app_id == 9010
            assert uow.roms.get(20).shortcut_app_id == 9020
            assert uow.roms.get(11).shortcut_app_id is None
            assert uow.roms.get(12).shortcut_app_id is None
            assert uow.roms.get(11).sibling_group_key == "igdb:100:1"

    async def _apply_group_and_get_shortcuts(self, library, fake_romm_api, emit, roms):
        """Seed a single-platform sibling group, run one apply unit, return the
        emitted ``sync_apply_unit`` shortcut dicts. Shared by the region tests."""
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=roms)
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def ack_reps(_unit, event):
            event.set()
            return {str(rid): 9000 + rid for rid in library.sync._box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = ack_reps
        library.sync._box.sync_state = SyncState.RUNNING
        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(unit_events) == 1
        return unit_events[0]["shortcuts"]

    @pytest.mark.asyncio
    async def test_region_priority_picks_and_names_representative(self, make_library, fake_romm_api, emit):
        """No default/installed/bound: region priority binds the USA dump AND
        names the shortcut after it, even though Japan sorts first alphabetically
        (ADR-0021 §3 region leg + canonical naming)."""
        library = make_library(romm_api=fake_romm_api)
        shortcuts = await self._apply_group_and_get_shortcuts(
            library,
            fake_romm_api,
            emit,
            [
                {"id": 10, "name": "Zelda (JP)", "igdb_id": 100, "fs_name_no_ext": "zelda_jp", "regions": ["Japan"]},
                {"id": 11, "name": "Zelda (USA)", "igdb_id": 100, "fs_name_no_ext": "zelda_usa", "regions": ["USA"]},
            ],
        )
        assert len(shortcuts) == 1
        assert shortcuts[0]["rom_id"] == 11
        assert shortcuts[0]["name"] == "Zelda (USA)"

    @pytest.mark.asyncio
    async def test_preferred_region_setting_threads_into_apply_collapse(self, make_library, fake_romm_api, emit):
        """Setting ``preferred_region`` re-heads the ranking: with Japan preferred,
        the Japanese dump becomes the representative + shortcut name — proving the
        setting is threaded into the apply collapse call site."""
        library = make_library(romm_api=fake_romm_api)
        library.settings["preferred_region"] = "Japan"
        shortcuts = await self._apply_group_and_get_shortcuts(
            library,
            fake_romm_api,
            emit,
            [
                {"id": 10, "name": "Zelda (JP)", "igdb_id": 100, "fs_name_no_ext": "zelda_jp", "regions": ["Japan"]},
                {"id": 11, "name": "Zelda (USA)", "igdb_id": 100, "fs_name_no_ext": "zelda_usa", "regions": ["USA"]},
            ],
        )
        assert len(shortcuts) == 1
        assert shortcuts[0]["rom_id"] == 10
        assert shortcuts[0]["name"] == "Zelda (JP)"

    @pytest.mark.asyncio
    async def test_preview_new_names_follow_region_canonical(self, make_library, fake_romm_api, emit):
        """The preview collapse call site also applies region priority: the new
        game's reported name is the region-canonical (USA) name."""

        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 10, "name": "Zelda (JP)", "igdb_id": 100, "fs_name_no_ext": "zelda_jp", "regions": ["Japan"]},
                {"id": 11, "name": "Zelda (USA)", "igdb_id": 100, "fs_name_no_ext": "zelda_usa", "regions": ["USA"]},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}

        result = await library.sync.sync_preview()
        assert result["success"] is True
        assert result["summary"]["new_count"] == 1  # one game, not two dumps
        assert result["new_names"] == ["Zelda (USA)"]

    def _seed_rescanned_saturn_game(self, library, fake_romm_api):
        """A bound "Game (USA).zip" (app 5000) RomM keeps without its file, beside the folder a rescan made of it."""
        _seed_rom_row(
            library,
            1,
            app_id=5000,
            platform_slug="saturn",
            name="Game (USA)",
            fs_name="Game (USA).zip",
            sibling_group_key="igdb:100:1",
        )
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="Saturn",
            slug="saturn",
            roms=[
                {
                    "id": 1,
                    "name": "Game (USA)",
                    "igdb_id": 100,
                    "fs_name": "Game (USA).zip",
                    "fs_name_no_ext": "Game (USA)",
                    "has_file_on_disk": False,
                    "missing_from_fs": True,
                },
                {
                    "id": 2,
                    "name": "Game (USA)",
                    "igdb_id": 100,
                    "fs_name": "Game (USA)",
                    "fs_name_no_ext": "Game (USA)",
                    "has_file_on_disk": True,
                },
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}

    @pytest.mark.asyncio
    async def test_a_bound_rom_without_a_file_rebinds_onto_its_rescanned_folder(
        self, make_library, fake_romm_api, emit
    ):
        """The shortcut of a ROM RomM holds no file for moves onto the same dump in its
        new shape: one shortcut, keyed by the old row so the appId is reused (#2196)."""
        library = make_library(romm_api=fake_romm_api)
        self._seed_rescanned_saturn_game(library, fake_romm_api)
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def ack_reuse(_unit, event):
            event.set()
            return {str(rid): 5000 for rid in library.sync._box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = ack_reuse
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert [[e["rom_id"] for e in u["shortcuts"]] for u in unit_events] == [[1]]
        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": []}]
        with library.uow as uow:
            assert uow.roms.get(2).shortcut_app_id == 5000
            assert uow.roms.get(1).shortcut_app_id is None
            assert uow.roms.get(1).has_file_on_disk is False
            assert uow.roms.get(2).has_file_on_disk is True

    @pytest.mark.asyncio
    async def test_the_preview_offers_apply_for_a_rebind_alone(self, make_library, fake_romm_api, emit):
        """A rebind whose name and launch command stay the same is one changed game."""
        library = make_library(romm_api=fake_romm_api)
        self._seed_rescanned_saturn_game(library, fake_romm_api)

        result = await library.sync.sync_preview()

        summary = result["summary"]
        assert (summary["new_count"], summary["changed_count"], summary["remove_count"]) == (0, 1, 0)

    @staticmethod
    def _seed_zelda_whose_bound_usa_dump_is_gone(library, fake_romm_api, *, server_usa_id=None):
        """Rom 1, the bound USA dump (app 5000), is gone from RomM; the JP and EU dumps remain.

        With *server_usa_id*, RomM also serves the USA dump again under that id.
        """
        _seed_rom_row(
            library,
            1,
            app_id=5000,
            platform_slug="n64",
            name="Zelda (USA)",
            fs_name="zelda_usa.z64",
            sibling_group_key="igdb:100:1",
            applied_launch_options="run /zelda_usa.z64",
        )
        served = [
            {"id": 2, "name": "Zelda (JP)", "igdb_id": 100, "fs_name_no_ext": "zelda_jp"},
            {"id": 3, "name": "Zelda (EU)", "igdb_id": 100, "fs_name_no_ext": "zelda_eu"},
        ]
        if server_usa_id is not None:
            served.append({"id": server_usa_id, "name": "Zelda (USA)", "igdb_id": 100, "fs_name_no_ext": "zelda_usa"})
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=served)
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def ack_reuse(_unit, event):
            event.set()
            return {str(rid): 5000 for rid in library.sync._box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = ack_reuse
        library.sync._box.sync_state = SyncState.RUNNING

    @pytest.mark.asyncio
    async def test_a_vanished_bound_version_keeps_its_binding_beside_live_siblings_under_other_names(
        self, make_library, fake_romm_api, emit
    ):
        """The binding, the appId and the launch command stay; no shortcut is removed (D1)."""
        library = make_library(romm_api=fake_romm_api)
        self._seed_zelda_whose_bound_usa_dump_is_gone(library, fake_romm_api)

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert [e["rom_id"] for event in unit_events for e in event["shortcuts"]] == []
        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": []}]
        with library.uow as uow:
            assert uow.roms.get(1).shortcut_app_id == 5000
            assert uow.roms.get(1).applied_launch_options == "run /zelda_usa.z64"
            assert uow.roms.get(2).shortcut_app_id is None
            assert uow.roms.get(3).shortcut_app_id is None

    @pytest.mark.asyncio
    async def test_a_vanished_bound_version_moves_onto_the_same_dump_under_a_new_id(
        self, make_library, fake_romm_api, emit
    ):
        """A re-import reissued the USA dump's id: the shortcut follows it (D2)."""
        library = make_library(romm_api=fake_romm_api)
        self._seed_zelda_whose_bound_usa_dump_is_gone(library, fake_romm_api, server_usa_id=4)

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert [e["rom_id"] for event in unit_events for e in event["shortcuts"]] == [1]
        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": []}]
        with library.uow as uow:
            assert uow.roms.get(4).shortcut_app_id == 5000
            assert uow.roms.get(1).shortcut_app_id is None

    @pytest.mark.asyncio
    async def test_a_game_whose_every_version_is_gone_keeps_its_shortcut(self, make_library, fake_romm_api, emit):
        """D3: the platform was read whole, and none of the game's versions came back."""
        library = make_library(romm_api=fake_romm_api)
        _seed_rom_row(library, 1, app_id=5000, platform_slug="n64", name="Zelda (USA)", sibling_group_key="igdb:100:1")
        _seed_rom_row(library, 9, app_id=5009, platform_slug="n64", name="Mario", sibling_group_key="igdb:200:1")
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 9, "name": "Mario"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.settings["collection_create_platform_groups"] = False
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": []}]
        with library.uow as uow:
            assert uow.roms.get(1).shortcut_app_id == 5000
        # The kept shortcut stays in its platform's Steam collection.
        (collections,) = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_collections"]
        assert sorted(collections["platform_app_ids"]["N64"]) == [5000, 5009]

    @pytest.mark.asyncio
    async def test_skipped_platform_collection_unbound_sibling_never_rebinds(self, make_library, fake_romm_api, emit):
        """#1296 CRITICAL: a skipped platform + a collection holding an UNBOUND
        sibling of a group must NEVER rebind the live installed game.

        The platform incremental-SKIPS, so only its BOUND rows enter
        ``synced_rom_ids``. The collection makes no request here: its one
        member, the group's unbound sibling, is not in ``synced_rom_ids``, but
        the sync leaves no platform off, so no walk happens and the sibling is
        left out of ``new_roms`` — as the collection skip already leaves
        unbound members out. The installed representative (rom
        10) must stay bound to its shortcut, not be read as "vanished" and
        rebound onto the uninstalled sibling (which would blank the launch
        options and orphan the installed ROM).
        """

        library = make_library(romm_api=fake_romm_api)

        # A 2-version Zelda group on N64: rom 10 bound + installed (active
        # version), rom 11 the unbound JP sibling. Both persisted + backfilled so
        # the platform incremental-skips this run.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {
                    "id": 10,
                    "name": "Zelda (USA)",
                    "igdb_id": 100,
                    "fs_name_no_ext": "zelda_usa",
                    "updated_at": _OLD_UPDATED_AT,
                },
                {
                    "id": 11,
                    "name": "Zelda (JP)",
                    "igdb_id": 100,
                    "fs_name_no_ext": "zelda_jp",
                    "updated_at": _OLD_UPDATED_AT,
                },
            ],
        )
        # The collection holds ONLY the unbound sibling.
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[11], is_favorite=True)
        library.settings["enabled_platforms"] = {"1": True}
        library.settings["enabled_collections"] = {"standard": {"7": True}}

        _seed_completed_run(library, at="2025-01-01T00:00:00Z")
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=2, rom_ids=(10, 11))
        # Install first so the binding survives the _seed_rom_row overwrite.
        _seed_install(library, 10, file_path="/roms/n64/zelda_usa.z64", platform_slug="n64")
        _seed_rom_row(
            library,
            10,
            app_id=5000,
            platform_slug="n64",
            name="Zelda (USA)",
            fs_name="zelda_usa.z64",
            sibling_group_key="igdb:100:1",
        )
        _seed_rom_row(
            library,
            11,
            app_id=None,
            platform_slug="n64",
            name="Zelda (JP)",
            fs_name="zelda_jp.z64",
            sibling_group_key="igdb:100:1",
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        # The platform incremental-SKIPPED (never paginated ``list_roms``), and the
        # collection fetched no page.
        call_names = [c[0] for c in fake_romm_api.call_log]
        assert "list_roms" not in call_names
        assert "list_roms_by_collection" not in call_names

        # NO shortcut entry for the sibling, and above all NO rebind entry.
        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        all_emitted = [sd for e in unit_events for sd in e["shortcuts"]]
        assert all(BIND_ROM_ID_KEY not in sd for sd in all_emitted)
        assert all(sd["rom_id"] != 11 for sd in all_emitted)

        # No stale removal of the live binding.
        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": []}]

        # Binding + version untouched: rom 10 still bound to app 5000, rom 11 stays
        # an unbound tracked sibling.
        with library.uow as uow:
            assert uow.roms.get(10).shortcut_app_id == 5000
            assert uow.roms.get(11).shortcut_app_id is None

    @pytest.mark.asyncio
    async def test_disabled_platform_bound_row_kept_while_a_collection_delivers_its_sibling_and_not_rebound(
        self, make_library, fake_romm_api, emit
    ):
        """Disabled-platform variant of #1296: the bound row keeps its shortcut, and
        the collection does NOT rebind it.

        The group's platform is disabled, so no platform unit returns rom 10. An
        enabled collection delivers the group's unbound sibling (rom 11), so the
        run synced the game and the stale removal leaves rom 10's shortcut alone.
        As a partial view the collection grandfathers the binding rather than
        moving it onto rom 11.
        """

        library = make_library(romm_api=fake_romm_api)

        # rom 11 (the unbound sibling) lives on N64 + in the collection; rom 10 is
        # a bound DB row on the (now disabled) platform.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 11, "name": "Zelda (JP)", "igdb_id": 100, "fs_name_no_ext": "zelda_jp"}],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[11], is_favorite=True)
        library.settings["enabled_platforms"] = {"1": False}
        library.settings["enabled_collections"] = {"standard": {"7": True}}

        _seed_rom_row(
            library,
            10,
            app_id=5000,
            platform_slug="n64",
            name="Zelda (USA)",
            fs_name="zelda_usa.z64",
            sibling_group_key="igdb:100:1",
        )
        _seed_rom_row(
            library,
            11,
            app_id=None,
            platform_slug="n64",
            name="Zelda (JP)",
            fs_name="zelda_jp.z64",
            sibling_group_key="igdb:100:1",
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        stale_events = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [{"remove": []}]

        # The collection did NOT rebind — no rebind entry, no shortcut for the
        # unbound sibling.
        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        all_emitted = [sd for e in unit_events for sd in e["shortcuts"]]
        assert all(BIND_ROM_ID_KEY not in sd for sd in all_emitted)
        assert all(sd["rom_id"] != 11 for sd in all_emitted)

        # rom 10 keeps its binding; rom 11 stays unbound (grandfathered).
        with library.uow as uow:
            assert uow.roms.get(10).shortcut_app_id == 5000
            assert uow.roms.get(11).shortcut_app_id is None

    @pytest.mark.asyncio
    async def test_downloads_artwork_when_not_skipped(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)

        # No prior sync → full fetch path → skipped=False → artwork pipeline runs.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        download_artwork = AsyncMock(return_value={10: "/grid/a.png"})
        library.cover_preparer._download_artwork = download_artwork
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()
        download_artwork.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_between_units_stops_processing(self, make_library, fake_romm_api, emit):
        """Cancel flipped during the first unit's ack stops the queue mid-flight.

        Both platforms take the live-fetch path (no ``last_sync``) so
        each fully traverses ``apply_unit`` rather than short-
        circuiting. The cancel observed between units must produce
        exactly one ``sync_apply_unit`` and a ``cancelled=True``
        ``sync_complete``.
        """

        library = make_library(romm_api=fake_romm_api)

        # Two live-fetch platforms (no last_sync, empty registry).
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[{"id": 20, "name": "B"}],
        )
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            # Flip to CANCELLING after first unit completes
            library.sync._box.sync_state = SyncState.CANCELLING
            return {}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        unit_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(unit_events) == 1  # cancel observed between units
        complete_events = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert len(complete_events) == 1
        assert complete_events[0].get("cancelled") is True

    @pytest.mark.asyncio
    async def test_normal_completion_emits_finalizing_running(self, make_library, fake_romm_api, emit):
        """A normal-completion run emits a non-terminal finalizing snapshot
        after the unit loop, before the reporter's terminal done emit."""

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        finalizing = [
            c.args[1]
            for c in emit.call_args_list
            if c.args and c.args[0] == "sync_progress" and c.args[1].get("stage") == "finalizing"
        ]
        assert len(finalizing) == 1
        assert finalizing[0]["running"] is True
        # The terminal done snapshot still follows it (running:false).
        done = [
            c.args[1]
            for c in emit.call_args_list
            if c.args and c.args[0] == "sync_progress" and c.args[1].get("stage") == "done"
        ]
        assert len(done) == 1
        assert done[0]["running"] is False

    @pytest.mark.asyncio
    async def test_cancelled_run_does_not_emit_finalizing(self, make_library, fake_romm_api, emit):
        """A cancelled run skips the finalizing snapshot — its terminal emit
        is the reporter's cancelled snapshot."""

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[{"id": 20, "name": "B"}],
        )
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            library.sync._box.sync_state = SyncState.CANCELLING
            return {}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        finalizing = [
            c.args[1]
            for c in emit.call_args_list
            if c.args and c.args[0] == "sync_progress" and c.args[1].get("stage") == "finalizing"
        ]
        assert finalizing == []


class TestSyncRunLifecycle:
    """The SyncRun record persisted by ``_do_sync_per_unit`` across its outcomes.

    What is under test here is the branch, not the write. Each test drives a
    real run to its ending, seeds ``box.current_sync_id``, and asserts the
    persisted ``uow.sync_runs`` row, so a branch that picked the wrong terminal
    — blaming a frontend crash on the user's Cancel, say — fails here. Four
    terminals are covered: ``completed``, ``cancelled``, ``interrupted`` and
    ``errored``, plus the zero-unit run that must write no row at all. The
    fifth, ``paused``, is NOT here — it is the highest-priority arm of that
    ladder and it is covered where its trigger lives, in
    :class:`TestSessionBudgetGate`.

    :class:`SyncRunRecorder` performs the write and is covered on its own in
    ``tests/services/library/test_sync_run_recorder.py``.
    """

    @pytest.mark.asyncio
    async def test_clean_run_persists_completed_with_platforms(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"10": 9001}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-clean"

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            run = uow.sync_runs.get("run-clean")
        assert run is not None
        assert run.status == "completed"
        assert run.platforms_planned == 1
        assert run.roms_planned == 1
        assert run.finished_at is not None
        assert run.platforms_completed == ["N64"]

    @pytest.mark.asyncio
    async def test_empty_queue_preserves_prior_baseline(self, make_library, fake_romm_api):
        """A zero-unit sync must NOT open or complete a SyncRun — an empty
        completed run would reset the preview baseline (next preview would
        report every platform as 'added'). The prior completed run stays the
        baseline source, matching the JSON era's return-early behaviour."""
        library = make_library(romm_api=fake_romm_api)

        # A prior real sync completed with N64 synced — this is the baseline
        # the next preview must keep reading.
        _seed_completed_run(library, at="2025-01-01T00:00:00Z", platforms=["Nintendo 64"], run_id="run-prior")

        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {}
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-empty"

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            # No empty run was persisted.
            assert uow.sync_runs.get("run-empty") is None
            # The prior completed run is still the latest completed → baseline
            # platforms preserved (not reset to []).
            latest = uow.sync_runs.get_latest_completed()
            assert latest is not None
            assert latest.id == "run-prior"
            assert latest.platforms_completed == ["Nintendo 64"]

    @pytest.mark.asyncio
    async def test_cancelled_run_persists_cancelled(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A"}])
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 20, "name": "B"}])
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            library.sync._box.sync_state = SyncState.CANCELLING
            return {}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-cancel"

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            run = uow.sync_runs.get("run-cancel")
        assert run is not None
        assert run.status == "cancelled"
        assert run.finished_at is not None
        assert run.error == "Sync cancelled"

    @pytest.mark.asyncio
    async def test_heartbeat_timeout_run_persists_interrupted(self, make_library, fake_romm_api):
        """A heartbeat timeout (the frontend stopped responding, not a user cancel)
        ends the run as ``interrupted`` — the terminal write branches on
        ``box.run_interrupted`` so a crash is never blamed on the user's Cancel."""
        from services.library import sync_orchestrator

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        # Heartbeat timeout: the wait gives up (None) while the box is still
        # RUNNING — the chunk dispatcher flags run_interrupted and requests the cancel.
        async def wait_timeout(_u, _event):
            return

        library.chunk_dispatcher._wait_for_unit_complete = wait_timeout
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-interrupted"

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            run = uow.sync_runs.get("run-interrupted")
        assert run is not None
        assert run.status == "interrupted"
        assert run.finished_at is not None
        assert run.error == sync_orchestrator._SYNC_INTERRUPTED

    @pytest.mark.asyncio
    async def test_exception_in_unit_loop_persists_errored(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)

        # build_work_queue succeeds, then list_roms raises during the unit fetch.
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        fake_romm_api.list_roms_side_effect = RuntimeError("boom")
        library.settings["enabled_platforms"] = {"1": True}
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-error"

        await library.sync._orchestrator._do_sync_per_unit()
        for _ in range(3):
            await asyncio.sleep(0)

        with library.uow as uow:
            run = uow.sync_runs.get("run-error")
        assert run is not None
        assert run.status == "errored"
        assert run.finished_at is not None
        assert run.error  # carries a human-readable detail

    @pytest.mark.asyncio
    async def test_terminal_write_failure_after_finalize_persists_errored(self, make_library, fake_romm_api):
        """A terminal write that raises AFTER finalize must still mark the run
        ``errored`` — not leave it stuck ``running``.

        What this pins is that the error path records the run at all: raise
        inside the terminal write and the row must still end ``errored``.

        It does NOT exercise the captured run id any more, and the docstring
        used to claim it did. ``finalize_per_unit_run`` nulled
        ``box.current_sync_id`` before the terminal write when that claim was
        written; #1202 moved the nulling into ``finish_run``, which runs in the
        ``finally`` AFTER this path, so ``run_id`` and ``box.current_sync_id``
        are the same value here and swapping one for the other changes nothing.
        The capture stays because it makes the error path independent of where
        the nulling lives — put a null back before the terminal write and it is
        what keeps this test true.
        """
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"10": 9001}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait

        # The terminal completed-write raises (e.g. a SQLite lock during the
        # short write UoW), which is what routes the run into the error path.
        def boom(*_args, **_kwargs):
            raise RuntimeError("terminal write boom")

        library.sync_run_recorder.do_complete_run = boom  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-terminal-fail"

        await library.sync._orchestrator._do_sync_per_unit()
        for _ in range(3):
            await asyncio.sleep(0)

        # The run id is back to None — ``finish_run``, in the terminating
        # ``finally`` — and the run is still recorded errored.
        assert library.sync._current_sync_id is None
        with library.uow as uow:
            run = uow.sync_runs.get("run-terminal-fail")
        assert run is not None
        assert run.status == "errored"
        assert run.finished_at is not None
        assert run.error


class TestReportUnitResults:
    """Per-unit ack signal — frontend callback that signals the orchestrator's wait event.

    The actual ``roms`` + ``rom_metadata`` upsert is driven by the
    orchestrator via ``commit_unit_results`` after this ack returns.
    """

    @pytest.mark.asyncio
    async def test_signals_unit_complete_event(self, library):
        library.sync._pending_sync = {}
        box = library.sync._box
        box.current_sync_id = "run-1"
        box.active_unit_id = 1
        box.active_chunk_index = 0
        event = asyncio.Event()
        box.unit_complete_event = event
        assert not event.is_set()

        await library.sync.report_unit_results({}, "run-1", 1, 0)

        assert event.is_set()
        assert box.last_unit_results == {}

    @pytest.mark.asyncio
    async def test_records_last_unit_results(self, library):
        library.sync._pending_sync = {}
        box = library.sync._box
        box.current_sync_id = "run-1"
        box.active_unit_id = 1
        box.active_chunk_index = 0
        box.unit_complete_event = asyncio.Event()

        result = await library.sync.report_unit_results({"10": 9001, "11": 9002}, "run-1", 1, 0)

        assert result["success"] is True
        assert result["count"] == 2
        assert box.last_unit_results == {"10": 9001, "11": 9002}

    @pytest.mark.asyncio
    async def test_stale_run_id_ack_is_ignored_not_credited_to_new_run(self, library):
        """A late ack from a cancelled run A, arriving while run B is in flight,
        is ignored — not signalled, not recorded, not committed (#1041).

        Run B's wait event must stay UNSET and its registry untouched: the late
        ack carries run A's id, which no longer matches ``current_sync_id``."""
        library.sync._pending_sync = {}
        box = library.sync._box
        # Run B is the active run, waiting on its own unit's event.
        box.current_sync_id = "run-B"
        box.active_unit_id = 7
        box.active_chunk_index = 0
        event = asyncio.Event()
        box.unit_complete_event = event

        # Late ack from the cancelled run A (stale run id) for the old unit.
        result = await library.sync.report_unit_results({"10": 9001}, "run-A", 1, 0)

        assert result == {"success": True, "count": 0, "ignored": True}
        # Run B is untouched: its event stays unset and no result was recorded.
        assert not event.is_set()
        assert box.last_unit_results is None
        # Nothing committed to run B's registry.
        assert library.uow.committed is False
        with library.uow as uow:
            assert uow.roms.get(10) is None

    @pytest.mark.asyncio
    async def test_mismatched_unit_id_same_run_is_ignored(self, library):
        """An ack for a different unit within the SAME run is ignored — the
        unit_id guard rejects it even though the run id matches (#1041)."""
        library.sync._pending_sync = {}
        box = library.sync._box
        box.current_sync_id = "run-1"
        box.active_unit_id = 5  # unit 5 is the active one
        box.active_chunk_index = 0
        event = asyncio.Event()
        box.unit_complete_event = event

        result = await library.sync.report_unit_results({"10": 9001}, "run-1", 99, 0)

        assert result == {"success": True, "count": 0, "ignored": True}
        assert not event.is_set()
        assert box.last_unit_results is None

    @pytest.mark.asyncio
    async def test_stale_chunk_index_same_unit_is_ignored(self, library):
        """An ack for a superseded chunk of the ACTIVE unit is ignored — the
        chunk-index guard rejects it even though run + unit match. A crash-late
        ack for chunk 0 must never be credited to chunk 1 in flight."""
        library.sync._pending_sync = {}
        box = library.sync._box
        box.current_sync_id = "run-1"
        box.active_unit_id = 1
        box.active_chunk_index = 1  # chunk 1 is the active one
        event = asyncio.Event()
        box.unit_complete_event = event

        result = await library.sync.report_unit_results({"10": 9001}, "run-1", 1, 0)

        assert result == {"success": True, "count": 0, "ignored": True}
        assert not event.is_set()
        assert box.last_unit_results is None

    @pytest.mark.asyncio
    async def test_late_ack_after_abandon_commits_binding(self, library):
        """A late ack for a heartbeat-timed-out chunk commits the delivered
        bindings itself instead of discarding them (#1052 / #1367).

        The box is driven into the real production post-timeout state (chunk
        stashed via ``stash_abandoned_chunk``, run wound down via ``finish_run``
        so ``current_sync_id`` is None). The late ack — whose active-unit check
        can no longer match — recovers the ``abandoned_chunk`` stash, drives
        ``commit_unit_results`` directly, persists the ``roms`` binding +
        metadata, and clears the stash."""
        _entry = {"name": "Game", "fs_name": "game.z64", "platform_slug": "gb", "cover_path": ""}
        box = _stash_abandoned_and_wind_down(
            library,
            run_id="run-1",
            unit_id=1,
            chunk_index=0,
            pending={42: _entry},
            chunk_rows=[{"id": 42, "metadatum": {"genres": ["RPG"]}}],
        )
        # The run has wound down: the active-unit identity is gone.
        assert box.current_sync_id is None
        assert box.active_unit_id is None

        result = await library.sync.report_unit_results({"42": 100001}, "run-1", 1, 0)

        assert result == {"success": True, "count": 1}
        # The binding was committed (not discarded).
        with library.uow as uow:
            rom = uow.roms.get(42)
            meta = uow.rom_metadata.get(42)
        assert rom is not None
        assert rom.shortcut_app_id == 100001
        # Metadata stamped from the stashed chunk rows.
        assert meta is not None
        assert meta.genres == ("RPG",)
        # The stash is cleared so a duplicate late ack no longer recovers it.
        assert box.abandoned_chunk is None

    @pytest.mark.asyncio
    async def test_duplicate_late_ack_after_recovery_is_ignored(self, library):
        """The first late ack pops the stash and commits; a second identical late
        ack finds no stash and is ignored, so nothing is double-committed
        (#1367)."""
        _entry = {"name": "Game", "fs_name": "game.z64", "platform_slug": "gb", "cover_path": ""}
        box = _stash_abandoned_and_wind_down(
            library,
            run_id="run-1",
            unit_id=1,
            chunk_index=0,
            pending={42: _entry},
            chunk_rows=[{"id": 42}],
        )

        first = await library.sync.report_unit_results({"42": 100001}, "run-1", 1, 0)
        second = await library.sync.report_unit_results({"42": 100001}, "run-1", 1, 0)

        assert first == {"success": True, "count": 1}
        assert second == {"success": True, "count": 0, "ignored": True}
        assert box.abandoned_chunk is None

    @pytest.mark.asyncio
    async def test_late_ack_with_wrong_identity_is_ignored_and_stash_intact(self, library):
        """A late ack whose chunk index does not match the stash is ignored and
        leaves the stash untouched, so the genuine late ack can still recover it
        (#1367)."""
        _entry = {"name": "Game", "fs_name": "game.z64", "platform_slug": "gb", "cover_path": ""}
        box = _stash_abandoned_and_wind_down(
            library,
            run_id="run-1",
            unit_id=1,
            chunk_index=0,
            pending={42: _entry},
            chunk_rows=[{"id": 42}],
        )

        # Wrong chunk index → no stash match.
        result = await library.sync.report_unit_results({"42": 100001}, "run-1", 1, 9)

        assert result == {"success": True, "count": 0, "ignored": True}
        # Nothing committed and the stash survives for the real late ack.
        with library.uow as uow:
            assert uow.roms.get(42) is None
        assert box.abandoned_chunk is not None

    @pytest.mark.asyncio
    async def test_late_ack_binds_stashed_rom_without_metadatum_stamps_no_metadata(self, library):
        """A stashed ROM carrying no ``metadatum`` still binds via the late ack,
        but stamps no metadata — the binding is load-bearing, metadata is
        best-effort (#1052)."""
        _entry = {"name": "A", "fs_name": "a.z64", "platform_slug": "gb", "cover_path": ""}
        _stash_abandoned_and_wind_down(
            library,
            run_id="run-1",
            unit_id=1,
            chunk_index=0,
            pending={42: _entry},
            # The stash (the chunk fetch) carries rom 42 without a metadatum.
            chunk_rows=[{"id": 42}],
        )

        result = await library.sync.report_unit_results({"42": 100001}, "run-1", 1, 0)

        assert result == {"success": True, "count": 1}
        with library.uow as uow:
            rom = uow.roms.get(42)
            meta = uow.rom_metadata.get(42)
        # Binding still committed.
        assert rom is not None
        assert rom.shortcut_app_id == 100001
        # No metadata: rom 42 carried no metadatum.
        assert meta is None

    @pytest.mark.asyncio
    async def test_stray_ack_on_active_chunk_without_live_wait_is_noop(self, library):
        """An ack for the ACTIVE chunk whose event was already consumed (a stray
        duplicate) records the mapping but commits nothing — the active-unit
        branch signals no event and never double-commits (#1052)."""
        box = library.sync._box
        box.try_begin_run("run-1", kind=SyncRunKind.APPLY)
        box.active_unit_id = 1
        box.active_chunk_index = 0
        box.unit_complete_event = None
        box.pending_sync = {}

        result = await library.sync.report_unit_results({"42": 100001}, "run-1", 1, 0)

        assert result == {"success": True, "count": 1}
        # The mapping is still recorded, but NOTHING is committed.
        assert box.last_unit_results == {"42": 100001}
        assert library.uow.committed is False
        with library.uow as uow:
            assert uow.roms.get(42) is None

    @pytest.mark.asyncio
    async def test_late_ack_of_timed_out_chunk_commits_only_that_chunk(self, library):
        """A late ack for an abandoned CHUNK commits only that chunk's stashed rows
        (the chunk subset), validated by run + unit + chunk index (#1025/#1052).

        Models the state a chunk-1 heartbeat timeout leaves behind: the chunk's
        two rows stashed under identity (run-1, unit 1, chunk 1). The ack for
        chunk 1 recovers them while an ack for any other chunk would be
        ignored."""
        entries = {
            3: {"name": "C", "fs_name": "c.z64", "platform_slug": "n64", "cover_path": ""},
            4: {"name": "D", "fs_name": "d.z64", "platform_slug": "n64", "cover_path": ""},
        }
        box = _stash_abandoned_and_wind_down(
            library,
            run_id="run-1",
            unit_id=1,
            chunk_index=1,
            pending=entries,
            chunk_rows=[{"id": 3}, {"id": 4}],
        )

        result = await library.sync.report_unit_results({"3": 7003, "4": 7004}, "run-1", 1, 1)

        assert result == {"success": True, "count": 2}
        with library.uow as uow:
            assert uow.roms.get(3).shortcut_app_id == 7003
            assert uow.roms.get(4).shortcut_app_id == 7004
        assert box.abandoned_chunk is None


class TestLateAckReconciliationWithStaleScan:
    """#1052 ↔ #1036 reconciliation: a binding committed via the late-ack path
    must be excluded from a stale scan, exactly like a happy-path binding.

    ``committed_app_ids`` accumulates from EVERY commit — both the orchestrator's
    in-loop ack and the reporter's late-ack commit (#1052). If it only captured
    the happy path, a late-committed binding could still be wiped by a later
    stale scan, re-opening the #1036 data-loss bug."""

    @pytest.mark.asyncio
    async def test_late_ack_appid_excluded_from_subsequent_stale_scan(self, library):
        """A unit times out → its binding commits late via report_unit_results →
        a subsequent stale scan does NOT remove that appId.

        The late ack both binds the row (app 5000) AND records it in
        committed_app_ids; the stale scan then excludes app 5000 even though the
        old colliding row (rom 1) looks stale (#1036 collision via the #1052
        late-ack path)."""
        # Old colliding bound row (a prior server's rom_id for the same game).
        _seed_rom_row(library, 1, app_id=5000, platform_slug="n64", name="A", fs_name="a.z64")

        # The production post-timeout state for the NEW rom_id (2), which the
        # frontend acks with the SAME reused appId, run wound down. The fresh box
        # starts with an empty committed_app_ids (the real reset lives in
        # _do_sync_per_unit); the late-ack commit accumulates app 5000 into it.
        _entry = {"name": "A", "fs_name": "a.z64", "platform_slug": "n64", "cover_path": ""}
        box = _stash_abandoned_and_wind_down(
            library,
            run_id="run-1",
            unit_id=1,
            chunk_index=0,
            pending={2: _entry},
            chunk_rows=[{"id": 2}],
        )

        # Late ack: commits the binding AND records app 5000 in committed_app_ids.
        await library.sync.report_unit_results({"2": 5000}, "run-1", 1, 0)

        assert 5000 in box.committed_app_ids
        # rom 2 now holds app 5000; rom 1 was unbound by the collision-safe save.
        with library.uow as uow:
            assert uow.roms.get(2).shortcut_app_id == 5000
            assert uow.roms.get(1).shortcut_app_id is None

        # A subsequent stale scan (rom 1 not in synced_rom_ids) must NOT emit
        # app 5000 for removal — it's a freshly-committed binding.
        stale = await asyncio.get_running_loop().run_in_executor(
            None,
            library.local_library_reader.do_scan_stale_roms,
            set(),  # synced_rom_ids — neither rom counts as synced for this scan
            set(box.committed_app_ids),
        )
        # rom 1 is already unbound (Layer 2), so it's not even a candidate; and
        # if it were, app 5000 is in committed_app_ids (Layer 1) → excluded.
        assert all(app_id != 5000 for _rid, app_id in stale)
        assert stale == []


class TestRealOrchestratorLateAckRecovery:
    """The #1367 acceptance path end-to-end through the REAL orchestrator.

    Drives a real ``_do_sync_per_unit`` whose only frontend ack never arrives, so
    the real heartbeat-clocked wait times out, the real
    ``ChunkDispatcher._abandon_active_chunk`` stashes the chunk, and the run's terminal ``finally`` winds the run down
    (``finish_run`` nulls ``current_sync_id``). Then the real
    ``report_unit_results`` — carrying the identity captured from the emitted
    ``sync_apply_unit`` event — recovers the stash and commits the binding. No box
    state is hand-set; the commit is observed through the shared UoW."""

    @pytest.mark.asyncio
    async def test_heartbeat_timeout_then_late_ack_commits_binding(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        # The frontend never acks, so the REAL _wait_for_unit_complete runs; the
        # clock-advancing sleeper pushes it past the heartbeat timeout on the
        # second poll — a genuine timeout, no cancel, no stubbed wait.
        orch = library.sync._orchestrator
        dispatcher = library.chunk_dispatcher
        dispatcher._sleeper = _ClockAdvancingSleeper(dispatcher._clock, 999.0)

        # Real run start (claims the slot + stamps current_sync_id).
        assert library.sync._box.try_begin_run("run-headline", kind=SyncRunKind.APPLY) is True
        library.sync._box.sync_last_heartbeat = orch._clock.monotonic()

        await orch._do_sync_per_unit()

        box = library.sync._box
        # The run wound down: the slot is free and a chunk is stashed for recovery.
        assert box.current_sync_id is None
        assert box.abandoned_chunk is not None
        assert box.run_interrupted is True
        # The chunk timed out before its commit, so rom 1 has no row yet.
        with library.uow as uow:
            assert uow.roms.get(1) is None
        # The run was persisted as interrupted (not cancelled) by the real teardown.
        with library.uow as uow:
            terminal = uow.sync_runs.get_latest_terminal()
        assert terminal is not None
        assert terminal.status == "interrupted"

        # Capture the identity the frontend echoes back from the emitted event.
        apply_events = [c.args[1] for c in emit.call_args_list if c.args[0] == "sync_apply_unit"]
        assert len(apply_events) == 1
        ev = apply_events[0]

        # The late ack arrives AFTER the run wound down — the exact production
        # timing #1367 fixes. It recovers the stash and commits the binding.
        result = await library.sync.report_unit_results({"1": 90001}, ev["run_id"], ev["unit_id"], ev["chunk_index"])

        assert result == {"success": True, "count": 1}
        with library.uow as uow:
            rom = uow.roms.get(1)
        assert rom is not None
        assert rom.shortcut_app_id == 90001
        # The stash is consumed so a duplicate late ack can't double-commit.
        assert box.abandoned_chunk is None

    @pytest.mark.asyncio
    async def test_next_run_start_drops_an_unacked_stash(self, make_library, fake_romm_api, emit):
        """If the frontend crash never acks, the abandoned chunk is inert until the
        next run's ``try_begin_run`` drops it — bounded lifetime (#1367)."""

        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A", "fs_name": "a.z64"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        orch = library.sync._orchestrator
        dispatcher = library.chunk_dispatcher
        dispatcher._sleeper = _ClockAdvancingSleeper(dispatcher._clock, 999.0)

        assert library.sync._box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
        library.sync._box.sync_last_heartbeat = orch._clock.monotonic()
        await orch._do_sync_per_unit()

        box = library.sync._box
        assert box.abandoned_chunk is not None

        # A fresh run starts before any late ack — the stale stash is dropped.
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        assert box.abandoned_chunk is None

        # A late ack for the old run now finds nothing and is ignored.
        result = await library.sync.report_unit_results({"1": 90001}, "run-1", 1, 0)
        assert result == {"success": True, "count": 0, "ignored": True}
        with library.uow as uow:
            assert uow.roms.get(1) is None


class TestCommitUnitResults:
    """Orchestrator-driven per-unit commit: cover-path finalize + ``roms`` + ``rom_metadata`` upsert."""

    @pytest.mark.asyncio
    async def test_updates_registry_for_unit_roms(self, library):
        box = library.sync._box
        entries = {
            10: {"rom_id": 10, "name": "A", "platform_name": "N64", "platform_slug": "n64", "cover_path": ""},
            11: {"rom_id": 11, "name": "B", "platform_name": "N64", "platform_slug": "n64", "cover_path": ""},
        }
        box.pending_sync = entries
        box.pending_all_roms = entries

        await library.sync._reporter.commit_unit_results({"10": 9001, "11": 9002}, [{"id": 10}, {"id": 11}])

        with library.uow as uow:
            assert uow.roms.get(10).shortcut_app_id == 9001
            assert uow.roms.get(11).shortcut_app_id == 9002

    @pytest.mark.asyncio
    async def test_commits_roms_for_unit(self, library):
        """commit_unit_results lands the unit's ROM upserts in one committed UoW."""
        box = library.sync._box
        entries = {10: {"rom_id": 10, "name": "A", "platform_name": "N64", "platform_slug": "n64", "cover_path": ""}}
        box.pending_sync = entries
        box.pending_all_roms = entries

        await library.sync._reporter.commit_unit_results({"10": 9001}, [{"id": 10}])

        assert library.uow.committed is True
        with library.uow as uow:
            assert uow.roms.get(10) is not None

    @pytest.mark.asyncio
    async def test_persists_unbound_non_representative_siblings(self, library):
        """Group-aware persist (ADR-0021): every fetched ROM gets an identity row,
        but a sibling absent from the ack lands UNBOUND — only representatives
        carry a binding."""
        box = library.sync._box
        entries = {
            10: {"rom_id": 10, "name": "A (USA)", "platform_slug": "n64", "cover_path": "", "sibling_group_key": "g"},
            11: {"rom_id": 11, "name": "A (JP)", "platform_slug": "n64", "cover_path": "", "sibling_group_key": "g"},
        }
        # Only rom 10 is the representative (emitted + acked); rom 11 is a sibling.
        box.pending_sync = {10: entries[10]}
        box.pending_all_roms = entries

        await library.sync._reporter.commit_unit_results({"10": 9001}, [{"id": 10}, {"id": 11}])

        with library.uow as uow:
            rep = uow.roms.get(10)
            sibling = uow.roms.get(11)
        assert rep is not None and rep.shortcut_app_id == 9001
        # The non-representative sibling is persisted for its identity + version,
        # but carries no shortcut binding.
        assert sibling is not None
        assert sibling.shortcut_app_id is None
        assert sibling.sibling_group_key == "g"


class TestShutdown:
    """Tests for shutdown()'s first step: it flips a RUNNING sync into CANCELLING.

    It then cancels the run and waits for it, which ``TestTheRunIsHeldUntilItEnds`` pins.
    """

    async def test_shutdown_when_running_marks_cancelling(self, library):
        library.sync._box.sync_state = SyncState.RUNNING
        await library.sync.shutdown()
        assert library.sync._sync_state == SyncState.CANCELLING

    async def test_shutdown_when_idle_is_noop(self, library):
        library.sync._box.sync_state = SyncState.IDLE
        await library.sync.shutdown()
        assert library.sync._sync_state == SyncState.IDLE

    async def test_shutdown_when_cancelling_is_noop(self, library):
        library.sync._box.sync_state = SyncState.CANCELLING
        await library.sync.shutdown()
        assert library.sync._sync_state == SyncState.CANCELLING


class TestTheRunIsHeldUntilItEnds:
    """The run and the error path's frame start through ``BackgroundTasks``, which the shutdown cancels and awaits."""

    async def test_start_sync_starts_the_run_through_the_helper(self, library, monkeypatch):
        spawned = record_spawns(monkeypatch)
        library.sync._orchestrator._do_sync_per_unit = AsyncMock()

        await library.sync.start_sync()

        assert spawned == ["library sync run"]

    async def test_apply_delta_starts_the_run_through_the_helper(self, library, monkeypatch):
        spawned = record_spawns(monkeypatch)
        library.sync._orchestrator._do_sync_per_unit = AsyncMock()
        library.sync._box.stage_preview(
            preview_id="p1",
            created_at=library.sync._orchestrator._clock.time(),
            answer={"success": True, "preview_id": "p1"},
        )

        await library.sync.sync_apply_delta("p1")

        assert spawned == ["library sync run"]

    async def test_the_error_paths_frame_starts_through_the_helper(self, make_library, fake_romm_api, monkeypatch):
        spawned = record_spawns(monkeypatch)
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        fake_romm_api.list_roms_side_effect = RuntimeError("boom")
        library.settings["enabled_platforms"] = {"1": True}
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        assert spawned == ["sync_progress error frame"]
        await library.sync.shutdown()

    @staticmethod
    async def _start_a_run_that_waits(library) -> asyncio.Task[Any]:
        """Start a real run whose work-queue build waits until it is cancelled; answer its task."""
        building = asyncio.Event()

        async def build_work_queue():
            building.set()
            await asyncio.Event().wait()

        library.sync._orchestrator._fetcher.build_work_queue = build_work_queue
        await library.sync.start_sync()
        await asyncio.wait_for(building.wait(), 5)
        (run,) = tuple(library.sync._orchestrator._tasks)
        return run

    async def test_shutdown_cancels_the_running_sync_and_returns_once_it_has_ended(self, library):
        run = await self._start_a_run_that_waits(library)

        await asyncio.wait_for(library.sync.shutdown(), 5)

        assert run.cancelled()
        assert library.sync._sync_state == SyncState.IDLE

    async def test_a_running_sync_has_ended_before_the_services_after_it_shut_down(self, library):
        run = await self._start_a_run_that_waits(library)
        run_ended_when_the_next_service_stopped: list[bool] = []

        async def prune_shutdown() -> None:
            run_ended_when_the_next_service_stopped.append(run.done())

        app = _make_application(
            _make_services_bundle(
                sync_service=library.sync,
                shortcut_artwork_service=MagicMock(shutdown=AsyncMock()),
                update_install_service=MagicMock(shutdown=AsyncMock()),
                prune_service=MagicMock(shutdown=AsyncMock(side_effect=prune_shutdown)),
                download_service=MagicMock(shutdown=AsyncMock()),
                migration_service=MagicMock(shutdown=AsyncMock()),
                session_lifecycle_service=MagicMock(shutdown=AsyncMock()),
                playtime_service=MagicMock(shutdown=AsyncMock()),
            )
        )

        await asyncio.wait_for(app.shutdown(), 5)

        assert run_ended_when_the_next_service_stopped == [True]
        assert run.cancelled()


class TestDoSyncPerUnitErrors:
    """Tests for error/cancel paths inside _do_sync_per_unit."""

    @pytest.mark.asyncio
    async def test_build_work_queue_cancelled_error_finishes_sync(self, make_library, fake_romm_api, emit):
        """CancelledError during build_work_queue triggers emit_cancelled + re-raise."""

        library = make_library(romm_api=fake_romm_api)
        # ``list_platforms`` runs in the executor; the fake raises
        # CancelledError exactly like an asyncio cancel would propagate.
        fake_romm_api.list_platforms_side_effect = asyncio.CancelledError()
        library.settings["enabled_platforms"] = {"1": True}
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "sync-cancel-build"

        with pytest.raises(asyncio.CancelledError):
            await library.sync._orchestrator._do_sync_per_unit()

        # The run's ``finally: box.finish_run(run_id)`` transitioned to IDLE + cleared sync id.
        assert library.sync._sync_state == SyncState.IDLE
        assert library.sync._current_sync_id is None
        progress_stages = [
            c.args[1].get("stage") for c in emit.call_args_list if c.args and c.args[0] == "sync_progress"
        ]
        assert "cancelled" in progress_stages

    @pytest.mark.asyncio
    async def test_build_work_queue_general_exception_emits_error(self, make_library, fake_romm_api, emit):
        """A non-cancellation exception during build_work_queue is logged + surfaced."""

        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.list_platforms_side_effect = RuntimeError("RomM down")
        library.settings["enabled_platforms"] = {"1": True}
        library.sync._box.sync_state = SyncState.RUNNING

        # Should NOT raise — outer flow swallows the exception after emitting an error.
        await library.sync._orchestrator._do_sync_per_unit()

        # error phase was emitted via sync_progress.
        error_events = [
            c
            for c in emit.call_args_list
            if c.args and c.args[0] == "sync_progress" and c.args[1].get("stage") == "error"
        ]
        assert len(error_events) >= 1
        assert library.sync._sync_state == SyncState.IDLE

    @pytest.mark.asyncio
    async def test_outer_exception_handler_emits_error_progress(self, make_library, fake_romm_api, emit):
        """An exception raised after build_work_queue (e.g. during a unit) hits the outer except."""

        library = make_library(romm_api=fake_romm_api)

        # build_work_queue succeeds (platforms listing returns a unit), then
        # list_roms blows up when the unit is fetched.
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        fake_romm_api.list_roms_side_effect = RuntimeError("boom")
        library.settings["enabled_platforms"] = {"1": True}
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()
        # Drain any pending tasks the outer handler spawned.
        for _ in range(3):
            await asyncio.sleep(0)

        # sync_progress with phase=error was scheduled.
        error_events = [
            c
            for c in emit.call_args_list
            if c.args and c.args[0] == "sync_progress" and c.args[1].get("stage") == "error"
        ]
        assert len(error_events) >= 1
        assert "Sync failed" in error_events[0].args[1]["message"]
        assert library.sync._sync_state == SyncState.IDLE

    @pytest.mark.asyncio
    async def test_pagination_failure_does_not_emit_partial_stale_removal(self, make_library, fake_romm_api, emit):
        """#630 safety invariant: a fetch_platform_unit failure must NOT trigger
        the stale-cleanup pass with a partial ROM set.

        Before the fix, ``fetch_platform_unit`` swallowed pagination exceptions
        and returned ``([], False)``. The orchestrator then ran ``_finalize_per_unit``
        with ``synced_rom_ids == set()`` and the registry's full ROM list was
        emitted via ``sync_stale``, which the frontend turned into a wholesale
        Steam shortcut deletion.

        Now that the fetcher re-raises, the exception hits the outer ``except``
        in ``_do_sync_per_unit`` BEFORE ``_finalize_per_unit`` runs, so no
        ``sync_stale`` event is ever emitted.
        """

        library = make_library(romm_api=fake_romm_api)

        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 3}]
        # Mid-pagination failure — the bug scenario from #630.
        fake_romm_api.list_roms_side_effect = RuntimeError("HTTP 500 on page 2")
        library.settings["enabled_platforms"] = {"1": True}
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()
        # Drain any pending tasks scheduled by the outer handler.
        for _ in range(3):
            await asyncio.sleep(0)

        # The load-bearing assertion: sync_stale must never have been emitted.
        stale_events = [c for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale_events == [], (
            f"Pagination failure leaked a partial sync_stale event: {stale_events}. "
            "This is the #630 wipe-the-library bug."
        )
        # The error path was taken instead.
        error_events = [
            c
            for c in emit.call_args_list
            if c.args and c.args[0] == "sync_progress" and c.args[1].get("stage") == "error"
        ]
        assert len(error_events) >= 1
        assert library.sync._sync_state == SyncState.IDLE

    @pytest.mark.asyncio
    async def test_cancel_mid_unit_fetch_finalizes_gracefully(self, make_library, fake_romm_api):
        """A cooperative cancel delivered MID per-unit fetch recovers all state (#1035).

        The cancel arrives while ``apply_unit`` is fetching the unit's
        ROMs (``fetcher._check_cancelling`` raising ``SyncCancelled`` from
        inside ``list_roms``) — NOT at an ``is_cancelling()`` checkpoint and
        NOT during ``build_work_queue``. The fetcher's own ``except
        SyncCancelled`` around ``list_roms``, above its ``except Exception``,
        re-raises it untouched and unlogged, and it lands in
        ``_do_sync_per_unit``'s dedicated ``except SyncCancelled``. On the un-fixed code (raising
        ``asyncio.CancelledError`` and catching it) sonar's S7497 would flag the
        swallow; the refactor uses a distinct cooperative type so the swallow is
        scoped to the cooperative signal only.

        The handler routes that mid-fetch SyncCancelled into the same graceful
        finalize the checkpoint break uses. This asserts all three recovery
        post-conditions AND that ``_do_sync_per_unit`` does NOT propagate the
        cooperative cancel (contrast with the build_work_queue path, which
        re-raises a real asyncio cancel).
        """
        from domain.sync_state import SyncCancelled

        library = make_library(romm_api=fake_romm_api)

        # One live-fetch platform (no last_sync, empty registry) so the unit
        # takes the real per-unit fetch rather than the incremental-skip path.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        # The per-unit ROM fetch raises SyncCancelled mid-flight — exactly how
        # ``fetcher._check_cancelling`` now signals a cooperative cancel that
        # landed after the platform listing but before the unit ack. A tracked
        # MagicMock (not just ``list_roms_side_effect``) lets us pin that the
        # signal was raised FROM the per-unit fetch — not bypassed by an early
        # ``is_cancelling()`` checkpoint or the incremental-skip path, which would
        # finalize gracefully for the WRONG reason.
        fake_romm_api.list_roms = MagicMock(side_effect=SyncCancelled("Sync cancelled"))

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-mid-fetch-cancel"

        # Guard against cross-test state leakage: a stale CANCELLING at entry
        # would break the unit loop before the fetch and pass for the wrong reason.
        assert library.sync._sync_state == SyncState.RUNNING

        # Must NOT propagate the cooperative cancel — awaiting returns normally.
        await library.sync._orchestrator._do_sync_per_unit()

        # The cooperative signal genuinely originated from the per-unit fetch.
        fake_romm_api.list_roms.assert_called()

        # 1. sync_state restored to IDLE (not stuck CANCELLING).
        assert library.sync._sync_state == SyncState.IDLE
        # 2. The SyncRun row is marked cancelled (not left ``running``).
        with library.uow as uow:
            run = uow.sync_runs.get("run-mid-fetch-cancel")
        assert run is not None
        assert run.status == "cancelled"
        assert run.finished_at is not None
        # 3. The persisted progress snapshot is no longer running.
        assert library.sync.get_sync_status()["running"] is False

    @pytest.mark.asyncio
    async def test_real_asyncio_cancel_mid_fetch_is_not_swallowed(self, make_library, fake_romm_api):
        """A REAL ``asyncio.CancelledError`` mid per-unit fetch PROPAGATES (#1035).

        This is the key safety guard the SyncCancelled split buys: the
        cooperative cancel signal is now a DISTINCT type (``SyncCancelled``),
        so the unit-loop ``except SyncCancelled`` does NOT catch a genuine
        ``asyncio.CancelledError`` (e.g. the sync task being cancelled by the
        runtime). Were the handler still ``except asyncio.CancelledError`` (the
        S7497-flagged pre-refactor shape), this real cancel would be swallowed
        into the graceful finalize and the run wrongly marked ``cancelled`` —
        masking a real task cancellation.

        The real cancel is injected at the ``list_roms`` layer, unwinds through
        the fetcher's ``except Exception`` re-raise, skips the unit-loop
        ``except SyncCancelled`` AND the outer ``except Exception`` (both narrower
        than ``BaseException``), and propagates straight out of
        ``_do_sync_per_unit``. The SyncRun is left ``running`` — it is NOT marked
        cancelled by the cooperative swallow path.
        """
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}],
        )
        library.settings["enabled_platforms"] = {"1": True}

        # A genuine asyncio task cancellation lands mid-fetch — NOT the
        # cooperative SyncCancelled signal. A tracked MagicMock guarantees a
        # 'DID NOT RAISE' can never be a silent fetch bypass: list_roms.assert_called()
        # below pins that the real cancel originated from the per-unit fetch.
        fake_romm_api.list_roms = MagicMock(side_effect=asyncio.CancelledError("real task cancel"))

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-real-cancel"

        # Guard against cross-test state leakage (a stale CANCELLING would break
        # the loop before the fetch and the cancel would never fire).
        assert library.sync._sync_state == SyncState.RUNNING

        # The real cancel must PROPAGATE OUT — the cooperative handler does not
        # catch it.
        with pytest.raises(asyncio.CancelledError):
            await library.sync._orchestrator._do_sync_per_unit()

        # The cancel genuinely fired from the per-unit fetch (not a bypass).
        fake_romm_api.list_roms.assert_called()

        # The run is NOT marked cancelled by the swallow path — a real task
        # cancel leaves the SyncRun ``running`` (never routed through the
        # graceful cooperative finalize).
        with library.uow as uow:
            run = uow.sync_runs.get("run-real-cancel")
        assert run is not None
        assert run.status == "running"
        assert run.finished_at is None

    @pytest.mark.asyncio
    async def test_real_asyncio_cancel_mid_preview_is_not_swallowed(self, make_library, fake_romm_api):
        """A REAL ``asyncio.CancelledError`` mid sync_preview PROPAGATES (#1035).

        Symmetric to the per-unit guard: ``sync_preview``'s
        ``except SyncCancelled`` catches only the cooperative signal. A genuine
        ``asyncio.CancelledError`` injected at the fetch layer skips it (and the
        generic ``except Exception``) and propagates straight out of the
        use case — it is NOT mapped onto the canonical ``cancelled`` failure
        dict. The ``finally`` still restores sync_state to IDLE.
        """
        library = make_library(romm_api=fake_romm_api)

        fake_romm_api.list_platforms = MagicMock(side_effect=asyncio.CancelledError("real task cancel"))
        library.settings["enabled_platforms"] = {"1": True}

        # sync_preview only runs from IDLE — guard against a leaked non-IDLE state
        # that would short-circuit it to "sync_in_progress" before the fetch.
        assert library.sync._sync_state == SyncState.IDLE

        with pytest.raises(asyncio.CancelledError):
            await library.sync.sync_preview()

        # The cancel genuinely fired from the fetch, not a bypass.
        fake_romm_api.list_platforms.assert_called()

        # The ``finally`` block always restores IDLE, even on a propagated cancel.
        assert library.sync._sync_state == SyncState.IDLE

    @pytest.mark.asyncio
    async def test_cancelling_state_before_first_unit_skips_processing(self, make_library, fake_romm_api, emit):
        """If state is CANCELLING when the unit loop starts, no units run."""

        library = make_library(romm_api=fake_romm_api)

        # Two units in the queue; CANCELLING gates the loop before either fires.
        fake_romm_api.platforms = [
            {"id": 1, "name": "N64", "slug": "n64", "rom_count": 1},
            {"id": 2, "name": "GBA", "slug": "gba", "rom_count": 1},
        ]
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.sync._box.sync_state = SyncState.CANCELLING

        await library.sync._orchestrator._do_sync_per_unit()

        # No units were processed because the CANCELLING check fired before
        # the loop entered the per-unit body — sync_apply_unit is the
        # cleanest observable for "did the unit dispatch run?".
        apply_events = [c for c in emit.call_args_list if c.args and c.args[0] == "sync_apply_unit"]
        assert apply_events == []
        # _finalize_per_unit still ran; sync_complete is emitted with cancelled=True.
        complete = [c for c in emit.call_args_list if c.args and c.args[0] == "sync_complete"]
        assert len(complete) == 1
        assert complete[0].args[1].get("cancelled") is True


class TestApplyUnitCollectionAndCancel:
    """Tests for apply_unit branches: collection units + mid-unit cancel."""

    @pytest.mark.asyncio
    async def test_collection_unit_records_membership(self, make_library, fake_romm_api, emit):
        """A collection unit populates collection_memberships with its rom_ids."""

        library = make_library(romm_api=fake_romm_api)

        # Seed a real (non-virtual) collection with two ROMs.
        _seed_collection(
            fake_romm_api,
            collection_id=7,
            name="Faves",
            rom_ids=[1, 2],
            is_favorite=True,
        )
        fake_romm_api.roms[1]["name"] = "A"
        fake_romm_api.roms[1]["platform_name"] = "N64"
        fake_romm_api.roms[2]["name"] = "B"
        fake_romm_api.roms[2]["platform_name"] = "N64"
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {"standard": {"7": True}, "smart": {}, "virtual": {}}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        # sync_complete fired (collection_memberships flowed through to finalize).
        complete = [c for c in emit.call_args_list if c.args and c.args[0] == "sync_complete"]
        assert len(complete) == 1

    @pytest.mark.asyncio
    async def test_a_collection_unit_s_membership_threads_kind_and_virtual_type(self, library):
        """A collection unit's apply stamps the unit's kind + virtual_type onto the membership (#1539)."""
        applier = library.unit_applier
        unit = WorkUnit(
            type="collection",
            id="vc-1",
            name="coll-fr",
            slug="coll-fr",
            rom_count=2,
            collection_kind="virtual",
            virtual_type="franchise",
        )
        memberships: dict[tuple[str, str], Any] = {}

        await applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships=memberships,
            platform_rom_ids=set(),
            delivery=CollectionDelivery(unit, roms=[], member_rom_ids=[1, 2], skipped=True, fetch_started_at=""),
        )

        membership = memberships[("virtual", "vc-1")]
        assert membership.name == "coll-fr"
        assert membership.rom_ids == [1, 2]
        assert membership.kind == "virtual"
        assert membership.virtual_type == "franchise"

    @pytest.mark.asyncio
    async def test_a_collection_unit_without_its_delivery_is_refused(self, library):
        """A collection unit applies only from what the fetch of every collection unit delivered (#2543 D3)."""
        unit = WorkUnit(type="collection", id="7", name="Faves", slug="faves", rom_count=1, collection_kind="standard")

        sources = library.emulator_sources.read()
        with pytest.raises(ValueError, match="collection unit Faves applied without its delivery"):
            await library.unit_applier.apply_unit(
                unit,
                sources=sources,
                unit_index=0,
                total_units=1,
                synced_rom_ids=set(),
                collection_memberships={},
                platform_rom_ids=set(),
            )

    @pytest.mark.asyncio
    async def test_a_standard_collection_s_membership_has_no_virtual_type(self, library):
        """A standard collection membership carries kind='standard' and virtual_type=None (#1539)."""
        applier = library.unit_applier
        unit = WorkUnit(
            type="collection",
            id="7",
            name="Faves",
            slug="faves",
            rom_count=1,
            collection_kind="standard",
        )
        memberships: dict[tuple[str, str], Any] = {}

        await applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships=memberships,
            platform_rom_ids=set(),
            delivery=CollectionDelivery(unit, roms=[], member_rom_ids=[1], skipped=True, fetch_started_at=""),
        )

        membership = memberships[("standard", "7")]
        assert membership.kind == "standard"
        assert membership.virtual_type is None

    @pytest.mark.asyncio
    async def test_cancel_after_fetch_returns_zero_applied(self, make_library, fake_romm_api):
        """CANCELLING flipped after fetch_platform_unit → unit returns 0."""
        library = make_library(romm_api=fake_romm_api)

        # Real fetcher will be called for the unit. Wrap list_roms so the
        # post-fetch state is CANCELLING when ``apply_unit`` checks it.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}],
        )

        orig_list_roms = fake_romm_api.list_roms

        def list_roms_then_cancel(platform_id, limit=LIST_PAGE_SIZE, offset=0):
            page = orig_list_roms(platform_id, limit=limit, offset=offset)
            library.sync._box.sync_state = SyncState.CANCELLING
            return page

        fake_romm_api.list_roms = list_roms_then_cancel  # type: ignore[method-assign]

        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        applied = await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )
        assert applied == 0

    @pytest.mark.asyncio
    async def test_cancel_after_artwork_returns_zero_applied(self, make_library, fake_romm_api):
        """CANCELLING flipped after the artwork download → unit returns 0."""
        library = make_library(romm_api=fake_romm_api)

        # Real fetcher runs; artwork download is intercepted to flip state mid-flight.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}],
        )

        async def cancel_during_artwork(*_a, **_kw):
            # Trigger CANCELLING in between the post-fetch check and the post-artwork check.
            library.sync._box.sync_state = SyncState.CANCELLING
            return {}

        library.cover_preparer._download_artwork = cancel_during_artwork
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        applied = await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )
        assert applied == 0

    @pytest.mark.asyncio
    async def test_cancel_after_artwork_still_counts_delta_skips(self, make_library, fake_romm_api):
        """A mid-unit cancel AFTER the delta is computed returns the delta-skip
        count: those ROMs were verified already-correct in Steam, so they are
        processed — same as a wholesale-skipped unit's ROMs — and the terminal
        frame's "N of M games processed" numerator must not drop them."""
        library = make_library(romm_api=fake_romm_api)

        # rom 10 is content-unchanged (delta-skipped); rom 11 is brand-new, so
        # the unit reaches the artwork step where the cancel lands.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 10, "name": "Keep", "fs_name": "keep.z64"},
                {"id": 11, "name": "Fresh", "fs_name": "fresh.z64"},
            ],
        )
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")

        async def cancel_during_artwork(*_a, **_kw):
            library.sync._box.sync_state = SyncState.CANCELLING
            return {}

        library.cover_preparer._download_artwork = cancel_during_artwork
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        processed = await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )
        assert processed == 1, "the delta-skipped ROM is processed; the cancelled apply is not"

    @pytest.mark.asyncio
    async def test_cancel_after_cover_refresh_still_counts_delta_skips(self, make_library, fake_romm_api):
        """Same invariant at the sibling guard: a cancel landing during the
        cover-refresh pass — after the delta is computed, before the artwork
        download — still returns the delta-skip count."""
        library = make_library(romm_api=fake_romm_api)

        # rom 10 is content-unchanged (delta-skipped); the cancel lands inside
        # the cover-refresh pass, so the guard right after it fires.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Keep", "fs_name": "keep.z64"}],
        )
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")

        async def cancel_during_cover_refresh(*_a, **_kw):
            library.sync._box.sync_state = SyncState.CANCELLING
            return []

        library.cover_preparer.refresh_changed_covers = cancel_during_cover_refresh
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        processed = await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )
        assert processed == 1, "the delta-skipped ROM is processed even on a pre-apply cancel"

    @pytest.mark.asyncio
    async def test_user_cancel_clears_pending_and_drops_event(self, make_library, fake_romm_api):
        """A user cancel during the wait discards in-flight work: pending_sync
        cleared, unit event nulled, no abandoned-chunk stash.

        ``_wait_for_unit_complete`` returns None while the box is already
        CANCELLING (the cancel branch), so the unit's in-flight state is
        intentionally dropped and a stray late ack can't commit it (#1052)."""
        library = make_library(romm_api=fake_romm_api)

        # Live-fetch path so the unit reaches the apply branch where
        # ``_wait_for_unit_complete`` is called.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}],
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        # The wait observes a user cancel: flip CANCELLING, then give up (None).
        async def wait_user_cancel(_unit, _event):
            library.sync._box.sync_state = SyncState.CANCELLING
            return

        library.chunk_dispatcher._wait_for_unit_complete = wait_user_cancel
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        applied = await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )
        assert applied == 0
        # User cancel: pending_sync cleared, unit event dropped, state CANCELLING.
        assert library.sync._pending_sync == {}
        assert library.sync._box.unit_complete_event is None
        assert library.sync._sync_state == SyncState.CANCELLING
        # No abandoned-chunk stash — a cancel intentionally discards the work.
        assert library.sync._box.abandoned_chunk is None

    @pytest.mark.asyncio
    async def test_heartbeat_timeout_retains_pending_and_stashes_roms(self, make_library, fake_romm_api):
        """A heartbeat timeout (not a cancel) stashes the abandoned chunk so a
        late ``report_unit_results`` can still commit the delivered bindings.

        The wait returns None while the box is still RUNNING (the timeout
        branch): the chunk is moved into ``abandoned_chunk`` (its rows captured),
        the whole-unit ``pending_sync`` staging stays live for the late-ack
        commit to read, and the dispatch identity (``unit_complete_event`` +
        ``active_unit_id`` + ``active_chunk_index``) is cleared. The box flips
        CANCELLING so the outer loop stops (#1052 / #1367)."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}],
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        # Heartbeat timeout: the wait gives up (None) WITHOUT a user cancel.
        async def wait_timeout(_unit, _event):
            return

        library.chunk_dispatcher._wait_for_unit_complete = wait_timeout
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        applied = await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )
        assert applied == 0
        box = library.sync._box
        # Timeout: whole-unit staging RETAINED so the late-ack commit can read it,
        # but the dispatch identity is cleared (moved into the stash).
        assert library.sync._pending_sync != {}
        assert box.unit_complete_event is None
        assert box.active_unit_id is None
        assert box.active_chunk_index is None
        assert library.sync._sync_state == SyncState.CANCELLING
        assert box.run_interrupted is True
        # The chunk is stashed (identity + rows) for the late-ack commit.
        assert box.abandoned_chunk is not None
        assert (box.abandoned_chunk.unit_id, box.abandoned_chunk.chunk_index) == (1, 0)
        assert [r["id"] for r in box.abandoned_chunk.chunk_rows] == [1]


class TestPerUnitMetadataStamping:
    """Per-unit metadata stamping folded into the reporter's commit (#738/#784)."""

    @pytest.mark.asyncio
    async def test_acked_roms_threaded_to_commit(self, make_library, fake_romm_api):
        """The orchestrator threads the acked ROM dicts into ``commit_unit_results``
        so the reporter can stamp ``rom_metadata`` in the same write UoW as the
        ``roms`` upsert (atomic — no separate metadata hop)."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A", "metadatum": {"genres": ["RPG"]}}],
        )

        commit_calls: list[tuple[Any, Any]] = []
        original_commit = library.sync._reporter.commit_unit_results

        async def tracked_commit(
            rid_to_aid, acked_roms, platform_stamp=None, collection_stamp=None, fetch_id=None, delivered_through=None
        ):
            commit_calls.append((rid_to_aid, acked_roms))
            await original_commit(
                rid_to_aid, acked_roms, platform_stamp=platform_stamp, collection_stamp=collection_stamp
            )

        library.sync._reporter.commit_unit_results = tracked_commit  # type: ignore[method-assign]
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"10": 5001}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        # commit_unit_results received the acked ROM dict (carrying metadatum).
        assert len(commit_calls) == 1
        _rid_to_aid, acked = commit_calls[0]
        assert [r["id"] for r in acked] == [10]
        assert acked[0]["metadatum"] == {"genres": ["RPG"]}
        # The metadata row + Rom row landed atomically in the shared UoW.
        with library.uow as uow:
            assert uow.roms.get(10) is not None
            meta = uow.rom_metadata.get(10)
        assert meta is not None
        assert meta.genres == ("RPG",)

    @pytest.mark.asyncio
    async def test_skipped_unit_does_not_stamp_metadata(self, make_library, fake_romm_api):
        """Incremental-skip platforms must NOT reach ``commit_unit_results``.

        The skipped short-circuit returns from ``apply_unit`` before the
        per-unit commit, so no ``rom_metadata`` is written for a skipped unit
        (populated metadata from prior real fetches is preserved, #738).
        """
        library = make_library(romm_api=fake_romm_api)

        # roms matches platform rom_count + zero updates → incremental skip.
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")

        commit_mock = AsyncMock()
        library.sync._reporter.commit_unit_results = commit_mock  # type: ignore[method-assign]
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"10": 5001}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        commit_mock.assert_not_called()
        with library.uow as uow:
            assert uow.rom_metadata.get(10) is None

    @pytest.mark.asyncio
    async def test_all_fetched_roms_threaded_ack_subset_binds(self, make_library, fake_romm_api):
        """Group-aware persist (ADR-0021): the WHOLE unit fetch is threaded into
        ``commit_unit_results`` (every sibling gets an identity row), while the
        frontend ack — a subset — decides which representatives bind."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 1, "name": "A", "metadatum": {"genres": ["RPG"]}},
                {"id": 2, "name": "B", "metadatum": {"genres": ["Action"]}},
                {"id": 3, "name": "C", "metadatum": {"genres": ["Puzzle"]}},
                {"id": 4, "name": "D", "metadatum": {"genres": ["Sport"]}},
                {"id": 5, "name": "E", "metadatum": {"genres": ["Strategy"]}},
            ],
        )

        commit_calls: list[tuple[Any, Any]] = []

        async def capture_commit(
            rid_to_aid, unit_roms, platform_stamp=None, collection_stamp=None, fetch_id=None, delivered_through=None
        ):
            commit_calls.append((rid_to_aid, unit_roms))

        library.sync._reporter.commit_unit_results = capture_commit  # type: ignore[method-assign]
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        # Frontend ack's only 3 out of 5 ROMs.
        async def fake_wait(_u, event):
            event.set()
            return {"1": 5001, "3": 5003, "5": 5005}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=5)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        assert len(commit_calls) == 1
        rid_to_aid, unit_roms = commit_calls[0]
        # The whole fetch is threaded (all 5 siblings), the ack binds only 3.
        assert {r["id"] for r in unit_roms} == {1, 2, 3, 4, 5}
        assert set(rid_to_aid.keys()) == {"1", "3", "5"}


class TestPlatformCompletionStamp:
    """Per-platform completion stamp written on the final platform chunk (ADR-0023 / #1025).

    The stamp lets the next sync's incremental-skip gate skip a platform that fully
    synced inside a run the user later cancelled — the run never completes, so the
    library-wide ``last_sync`` never advances, but the per-platform stamp does. It
    is written ONLY when a platform unit's LAST chunk commits, never on a
    collection unit, a cancelled unit, or a heartbeat-timed-out unit.
    """

    @pytest.mark.asyncio
    async def test_stamp_written_after_final_platform_chunk(self, make_library, fake_romm_api):
        """A platform unit that completes stamps ``platform_sync_state`` with the
        server ROM count and the clock's completion timestamp (real commit)."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"1": 5001, "2": 5002}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        # rom_count is the unit's server count; completed_at is the injected clock.
        assert stamp.rom_count == 2
        assert stamp.completed_at == "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_stamp_only_on_final_chunk_carries_unit_rom_count(self, make_library, fake_romm_api, monkeypatch):
        """Across a multi-chunk platform, only the FINAL chunk's commit carries the
        stamp, and it records the unit's whole ``rom_count`` (not the chunk size)."""
        from services.library import chunk_dispatcher

        library = make_library(romm_api=fake_romm_api)
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 2)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": i, "name": f"G{i}"} for i in range(1, 6)],
        )

        stamps: list[Any] = []

        async def capture_commit(
            _rid_to_aid, _chunk_rows, platform_stamp=None, collection_stamp=None, fetch_id=None, delivered_through=None
        ):
            stamps.append(platform_stamp)

        library.sync._reporter.commit_unit_results = capture_commit  # type: ignore[method-assign]
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=5)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        # 5 singletons at chunk size 2 → 3 chunks; only the last carries the stamp.
        assert len(stamps) == 3
        assert stamps[0] is None
        assert stamps[1] is None
        assert stamps[2] is not None
        assert stamps[2].platform_slug == "n64"
        assert stamps[2].rom_count == 5

    @pytest.mark.asyncio
    async def test_no_stamp_on_user_cancel_mid_unit(self, make_library, fake_romm_api, monkeypatch):
        """A user cancel before a platform's last chunk leaves NO stamp — the platform
        is only partially applied, so the next run must re-fetch it."""
        from services.library import chunk_dispatcher

        library = make_library(romm_api=fake_romm_api)
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 2)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": i, "name": f"G{i}"} for i in range(1, 6)],
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def wait(_unit, event):
            if box.active_chunk_index == 0:
                event.set()
                return {}
            box.sync_state = SyncState.CANCELLING  # user cancel during chunk 1
            return None

        library.chunk_dispatcher._wait_for_unit_complete = wait
        box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=5)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        with library.uow as uow:
            assert uow.platform_sync_state.get("n64") is None

    @pytest.mark.asyncio
    async def test_no_stamp_on_heartbeat_timeout(self, make_library, fake_romm_api):
        """A heartbeat timeout (wait returns None while NOT cancelling) abandons the
        chunk without committing it — no stamp, even on a single-chunk platform."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def timeout_wait(_unit, _event):
            return None  # heartbeat timeout — box is NOT cancelling

        library.chunk_dispatcher._wait_for_unit_complete = timeout_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        with library.uow as uow:
            assert uow.platform_sync_state.get("n64") is None

    @pytest.mark.asyncio
    async def test_no_stamp_for_collection_unit(self, make_library, fake_romm_api):
        """A collection unit never carries a platform stamp — every chunk's commit
        carries ``platform_stamp=None``; its own stamp is the collection stamp."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Favs", rom_ids=[1, 2])

        stamps: list[Any] = []

        async def capture_commit(
            _rid_to_aid, _chunk_rows, platform_stamp=None, collection_stamp=None, fetch_id=None, delivered_through=None
        ):
            stamps.append(platform_stamp)

        library.sync._reporter.commit_unit_results = capture_commit  # type: ignore[method-assign]
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"1": 5001, "2": 5002}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="collection", id="7", name="Favs", slug="favs", rom_count=2, collection_kind="standard")
        deliveries = await library.unit_applier.fetch_collection_units([unit], set(), unit_index=0, total_units=1)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
            delivery=deliveries[("standard", "7")],
        )

        # The collection committed (non-vacuous) but never carried a platform stamp.
        assert stamps, "collection unit should have committed at least one chunk"
        assert all(s is None for s in stamps)

    @pytest.mark.asyncio
    async def test_platform_threads_one_generation_through_every_chunk(self, make_library, fake_romm_api, monkeypatch):
        """Every chunk of a platform unit carries the SAME generation, and the final
        chunk's stamp records that same value (#1504).

        A per-chunk value (e.g. a clock reading) would leave the earlier chunks'
        rows on a generation the stamp never names, so the next skip would count
        only the last chunk and wedge itself off permanently.
        """
        from services.library import chunk_dispatcher

        library = make_library(romm_api=fake_romm_api)
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 2)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": i, "name": f"G{i}"} for i in range(1, 6)],
        )

        seen: list[Any] = []
        routes: list[Any] = []

        async def capture_commit(
            _rid_to_aid, _chunk_rows, platform_stamp=None, collection_stamp=None, fetch_id=None, delivered_through=None
        ):
            seen.append((fetch_id, platform_stamp))
            routes.append(delivered_through)

        library.sync._reporter.commit_unit_results = capture_commit  # type: ignore[method-assign]
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-abc"

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=5)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        assert len(seen) == 3  # 5 singletons at chunk size 2
        assert [fetch_id for fetch_id, _ in seen] == ["run-abc"] * 3
        assert routes == ["platform"] * 3
        # The stamp names the generation its own unit's rows all carry.
        final_stamp = seen[-1][1]
        assert final_stamp is not None
        assert final_stamp.fetch_id == "run-abc"

    @pytest.mark.asyncio
    async def test_collection_unit_writes_no_generation(self, make_library, fake_romm_api):
        """A collection spans platforms, so its commits pass no generation — marking
        a foreign platform's row would drop it from that platform's counted rows
        and suppress that platform's skip (#1504)."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Favs", rom_ids=[1, 2])

        seen: list[Any] = []
        routes: list[Any] = []

        async def capture_commit(
            _rid_to_aid, _chunk_rows, platform_stamp=None, collection_stamp=None, fetch_id=None, delivered_through=None
        ):
            seen.append(fetch_id)
            routes.append(delivered_through)

        library.sync._reporter.commit_unit_results = capture_commit  # type: ignore[method-assign]
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"1": 5001, "2": 5002}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-abc"

        unit = WorkUnit(type="collection", id="7", name="Favs", slug="favs", rom_count=2, collection_kind="standard")
        deliveries = await library.unit_applier.fetch_collection_units([unit], set(), unit_index=0, total_units=1)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
            delivery=deliveries[("standard", "7")],
        )

        # Non-vacuous: the collection did commit, and no commit carried a generation.
        assert seen, "collection unit should have committed at least one chunk"
        assert all(fetch_id is None for fetch_id in seen)
        # Every chunk it commits records that a collection delivered its rows.
        assert routes == ["collection"] * len(seen)

    @pytest.mark.asyncio
    async def test_apply_start_clears_preexisting_stamp_on_cancel_mid_unit(
        self, make_library, fake_romm_api, monkeypatch
    ):
        """A pre-existing (stale) stamp is cleared when the apply begins, so a cancel
        before the final chunk leaves NO stamp — the #1025 silent-gap regression.

        Before the apply-start clear the stale stamp survived a mid-unit cancel and
        let the next sync skip the half-applied platform, dropping every game the
        cancelled run never re-bound.
        """
        from services.library import chunk_dispatcher

        library = make_library(romm_api=fake_romm_api)
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 2)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": i, "name": f"G{i}"} for i in range(1, 6)],
        )
        # A stale completion stamp left by a prior fully-synced run.
        _seed_platform_stamp(library, "n64", at="2020-01-01T00:00:00+00:00", rom_count=5)

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def wait(_unit, event):
            if box.active_chunk_index == 0:
                event.set()
                return {}
            box.sync_state = SyncState.CANCELLING  # user cancel during chunk 1
            return None

        library.chunk_dispatcher._wait_for_unit_complete = wait
        box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=5)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        with library.uow as uow:
            assert uow.platform_sync_state.get("n64") is None

    @pytest.mark.asyncio
    async def test_apply_start_clears_preexisting_stamp_on_heartbeat_timeout(self, make_library, fake_romm_api):
        """A heartbeat timeout abandons the chunk without committing it, and its late-ack
        commit (unreachable today, #1367) carries no stamp — but the apply-start clear
        already removed the pre-existing stamp, so none survives the interruption."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        )
        _seed_platform_stamp(library, "n64", at="2020-01-01T00:00:00+00:00", rom_count=2)

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def timeout_wait(_unit, _event):
            return None  # heartbeat timeout — box is NOT cancelling

        library.chunk_dispatcher._wait_for_unit_complete = timeout_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        with library.uow as uow:
            assert uow.platform_sync_state.get("n64") is None

    @pytest.mark.asyncio
    async def test_completing_reapply_refreshes_stale_stamp(self, make_library, fake_romm_api):
        """A platform that re-applies to completion replaces a stale stamp with a fresh
        one (current server count + the injected completion clock), not the old values."""
        library = make_library(romm_api=fake_romm_api)

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        )
        _seed_platform_stamp(library, "n64", at="2020-01-01T00:00:00+00:00", rom_count=99)

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"1": 5001, "2": 5002}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )

        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert stamp.completed_at == "2026-01-01T00:00:00+00:00"  # fresh clock, not the stale 2020 value
        assert stamp.rom_count == 2  # current server count, not the stale 99

    @pytest.mark.asyncio
    async def test_skipped_platform_keeps_its_stamp(self, make_library, fake_romm_api):
        """An incremental-skipped platform returns before the apply-start clear, so its
        completion stamp is preserved untouched (the skip is what the stamp exists for)."""
        library = make_library(romm_api=fake_romm_api)

        # Stamp + one matching bound row + unchanged server → the fetch incremental-skips.
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00", rom_count=1)
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.sync._reporter.commit_unit_results = AsyncMock()  # type: ignore[method-assign]
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=1)
        applied = await library.unit_applier.apply_unit(
            unit,
            sources=library.emulator_sources.read(),
            unit_index=0,
            total_units=1,
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
        )
        # Non-vacuous: the unit actually skipped (its ROM count flows back) rather than
        # applying — the commit was never driven.
        assert applied == 1
        library.sync._reporter.commit_unit_results.assert_not_called()

        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert stamp.rom_count == 1
        assert stamp.completed_at == "2025-01-01T00:00:00"  # untouched

    @pytest.mark.asyncio
    async def test_fetch_failure_before_chunk_loop_keeps_stamp(self, make_library, fake_romm_api):
        """A fetch that raises before the chunk loop is not an apply start, so the old
        stamp is preserved (fetch failure ≠ apply started)."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform_stamp(library, "n64", at="2020-01-01T00:00:00+00:00", rom_count=5)

        async def boom(*_args, **_kwargs):
            raise RuntimeError("fetch exploded")

        library.unit_applier._sync_platform_unit = boom
        library.sync._box.sync_state = SyncState.RUNNING

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=5)
        sources = library.emulator_sources.read()
        synced_rom_ids: set[int] = set()
        platform_rom_ids: set[int] = set()
        with pytest.raises(RuntimeError, match="fetch exploded"):
            await library.unit_applier.apply_unit(
                unit,
                sources=sources,
                unit_index=0,
                total_units=1,
                synced_rom_ids=synced_rom_ids,
                collection_memberships={},
                platform_rom_ids=platform_rom_ids,
            )

        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert stamp.rom_count == 5  # untouched — the apply never started


class TestCollectionTurnedOffAndBackOn:
    """A collection whose sync is turned off and back on gets its shortcuts back (#2106).

    Its games sit on a platform whose own sync is off, so while the collection is
    off the stale-removal scan unbinds them. Were the collection's completion stamp
    left able to skip, nothing the skip reads would have moved: RomM's
    ``updated_at`` and count are the same and no member changed since the stamp,
    and the skip's reconstruction passes over an unbound member.
    """

    @pytest.mark.asyncio
    async def test_the_games_unbound_while_it_was_off_are_bound_again(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        )
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 4, "name": "D"}])
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[1, 2])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00+00:00"
        library.settings["enabled_platforms"] = {"1": False, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_game(unit, event):
            event.set()
            return {"collection": {"1": 5001, "2": 5002}, "platform": {"4": 5004}}[unit.type]

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        def bindings():
            with library.uow as uow:
                return {rom_id: uow.roms.get(rom_id).shortcut_app_id for rom_id in (1, 2)}

        # Run 1: the collection is on and binds both of its games.
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        await complete_a_run("run-1")
        assert bindings() == {1: 5001, 2: 5002}
        with library.uow as uow:
            stamp = uow.collection_sync_state.get("7", "standard")
        assert stamp is not None
        assert (stamp.rom_count, stamp.member_rom_ids) == (2, (1, 2))

        # Run 2: the collection is off, so no unit covers its games any more.
        library.settings["enabled_collections"] = {"standard": {"7": False}}
        await complete_a_run("run-2")
        assert bindings() == {1: None, 2: None}
        with library.uow as uow:
            assert uow.collection_sync_state.get("7", "standard") is None

        # Run 3: the collection is back on and nothing changed on RomM.
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        await complete_a_run("run-3")
        assert bindings() == {1: 5001, 2: 5002}

    @pytest.mark.asyncio
    async def test_a_collection_the_run_processed_keeps_its_stamp_when_a_game_it_does_not_hold_is_unbound(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[1])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00+00:00"
        library.settings["enabled_collections"] = {"standard": {"7": True}}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_n64_game(unit, event):
            event.set()
            return {"1": 5001, "2": 5002, "3": 5003} if unit.slug == "n64" else {}

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_n64_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        def bindings():
            with library.uow as uow:
                return {rom_id: uow.roms.get(rom_id).shortcut_app_id for rom_id in (1, 2, 3)}

        # Run 1: N64 and the collection are on.
        library.settings["enabled_platforms"] = {"1": True}
        await complete_a_run("run-1")
        assert bindings() == {1: 5001, 2: 5002, 3: 5003}

        # Run 2: N64 is off; the stale removal unbinds the two games the collection does not hold.
        library.settings["enabled_platforms"] = {"1": False}
        await complete_a_run("run-2")
        assert bindings() == {1: 5001, 2: None, 3: None}
        with library.uow as uow:
            stamp = uow.collection_sync_state.get("7", "standard")
        assert stamp is not None
        assert stamp.member_rom_ids == (1,)

    @pytest.mark.asyncio
    async def test_a_collection_the_run_full_fetched_keeps_its_fresh_stamp_when_a_game_it_does_not_hold_is_unbound(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[1])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00+00:00"
        library.settings["enabled_collections"] = {"standard": {"7": True}}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_n64_game(unit, event):
            event.set()
            return {"1": 5001, "2": 5002, "3": 5003} if unit.slug == "n64" else {}

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_n64_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        def bindings():
            with library.uow as uow:
                return {rom_id: uow.roms.get(rom_id).shortcut_app_id for rom_id in (1, 2, 3)}

        # Run 1: N64 and the collection are on.
        library.settings["enabled_platforms"] = {"1": True}
        await complete_a_run("run-1")
        assert bindings() == {1: 5001, 2: 5002, 3: 5003}

        # Run 2: N64 is off, and RomM moved the collection's updated_at, so it full-fetches and is stamped again.
        library.settings["enabled_platforms"] = {"1": False}
        fake_romm_api.collections[0]["updated_at"] = "2025-02-01T00:00:00+00:00"
        await complete_a_run("run-2")
        assert bindings() == {1: 5001, 2: None, 3: None}
        with library.uow as uow:
            stamp = uow.collection_sync_state.get("7", "standard")
        assert stamp is not None
        assert (stamp.updated_at, stamp.member_rom_ids) == ("2025-02-01T00:00:00+00:00", (1,))


class TestFailedCollectionListing:
    """A collection listing that fails stops the run before it changes anything (#2112).

    The games sit on a platform whose own sync is off, so no platform unit covers
    them. When RomM cannot be reached for the listing, the run has no unit for the
    collection; that must not read as "the collection holds nothing", which the
    end-of-run stale removal would act on by unbinding its games and the frontend
    by deleting the Steam collection.
    """

    @pytest.mark.parametrize("kind", ["standard", "smart", "virtual"])
    @pytest.mark.asyncio
    async def test_the_run_fails_and_leaves_the_collection_as_it_was(self, make_library, fake_romm_api, emit, kind):
        from lib.errors import RommConnectionError

        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        )
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 4, "name": "D"}])
        collection_id = "fr-7" if kind == "virtual" else 7
        entry = {"id": collection_id, "name": "Faves", "rom_count": 2, "updated_at": "2025-01-01T00:00:00+00:00"}
        membership_key = {
            "standard": "collection_ids",
            "smart": "smart_collection_ids",
            "virtual": "virtual_collection_ids",
        }[kind]
        if kind == "standard":
            fake_romm_api.collections.append(entry)
        elif kind == "smart":
            fake_romm_api.smart_collections.append(entry)
        else:
            fake_romm_api.virtual_collections["franchise"] = [entry]
        for rom_id in (1, 2):
            fake_romm_api.roms[rom_id].setdefault(membership_key, []).append(collection_id)
        library.settings["enabled_platforms"] = {"1": False, "2": True}
        library.settings["enabled_collections"] = {kind: {str(collection_id): True}}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_game(unit, event):
            event.set()
            return {"collection": {"1": 5001, "2": 5002}, "platform": {"4": 5004}}[unit.type]

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_game

        async def run_to_its_end(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        def bindings():
            with library.uow as uow:
                return {rom_id: uow.roms.get(rom_id).shortcut_app_id for rom_id in (1, 2)}

        def stamp():
            with library.uow as uow:
                return uow.collection_sync_state.get(str(collection_id), kind)

        # Run 1: the listing answers and the collection binds both of its games.
        await run_to_its_end("run-1")
        assert bindings() == {1: 5001, 2: 5002}
        stamp_after_run_1 = stamp()
        # Every kind of collection is stamped.
        assert stamp_after_run_1 is not None

        # Run 2: RomM cannot be reached for the listing; nothing changed on RomM or in the settings.
        failure = RommConnectionError("Connection refused")
        if kind == "standard":
            fake_romm_api.list_collections_side_effect = failure
        elif kind == "smart":
            fake_romm_api.list_smart_collections_side_effect = failure
        else:
            fake_romm_api.list_virtual_collections_side_effect = failure
        emit.reset_mock()
        await run_to_its_end("run-2")

        progress = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_progress"]
        assert [(f["stage"], f["message"], f["running"]) for f in progress] == [
            ("error", "Server unreachable — check your URL and ensure RomM is running", False)
        ]
        # Nothing the frontend removes a shortcut or a Steam collection on.
        assert {c.args[0] for c in emit.call_args_list} == {"sync_progress"}
        with library.uow as uow:
            assert uow.sync_runs.get("run-2") is None
        assert bindings() == {1: 5001, 2: 5002}
        assert stamp() == stamp_after_run_1

    @pytest.mark.parametrize(
        ("kind", "path"),
        [
            ("standard", "/api/collections"),
            ("smart", "/api/collections/smart"),
            ("virtual", "/api/collections/virtual?type=franchise"),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_listing_that_answers_with_something_but_a_list_fails_the_run(self, make_library, emit, kind, path):
        from adapters.romm.romm_api import RommApiAdapter

        answers = {"/api/platforms": [], path: {"detail": "not a list"}}
        client = MagicMock()
        client.request.side_effect = lambda requested: answers.get(requested, [])
        library = make_library(romm_api=RommApiAdapter(client))
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {kind: {"7": True}}
        box = library.sync._box
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = "run-1"

        await library.sync._orchestrator._do_sync_per_unit()

        progress = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_progress"]
        assert [(f["stage"], f["message"], f["running"]) for f in progress] == [
            ("error", f"Unexpected response from {path}: dict", False)
        ]
        assert {c.args[0] for c in emit.call_args_list} == {"sync_progress"}
        with library.uow as uow:
            assert uow.sync_runs.get("run-1") is None


class TestPlatformListingThatIsNotAList:
    """A platform listing that answers with something but a list stops the run before it changes anything.

    Read as "no platforms" instead, a run with a collection enabled would still
    complete, and the end-of-run stale removal would unbind every game only a
    platform brings in.
    """

    @pytest.mark.asyncio
    async def test_the_run_fails_and_leaves_every_binding_as_it_was(self, make_library, fake_romm_api, emit):
        from adapters.romm.romm_api import RommApiAdapter

        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        )
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 4, "name": "D"}])
        fake_romm_api.collections.append(
            {"id": 7, "name": "Faves", "rom_count": 2, "updated_at": "2025-01-01T00:00:00+00:00"}
        )
        for rom_id in (1, 2):
            fake_romm_api.roms[rom_id].setdefault("collection_ids", []).append(7)
        library.settings["enabled_platforms"] = {"1": False, "2": True}
        library.settings["enabled_collections"] = {"standard": {"7": True}}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_game(unit, event):
            event.set()
            return {"collection": {"1": 5001, "2": 5002}, "platform": {"4": 5004}}[unit.type]

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_game

        async def run_to_its_end(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        def bindings():
            with library.uow as uow:
                return {rom_id: uow.roms.get(rom_id).shortcut_app_id for rom_id in (1, 2, 4)}

        # Run 1: both listings answer; the collection binds its games and the platform its own.
        await run_to_its_end("run-1")
        assert bindings() == {1: 5001, 2: 5002, 4: 5004}

        # Run 2: the platform listing answers with an object; the collection listing still answers.
        client = MagicMock()
        client.request.return_value = {"detail": "not a list"}
        fake_romm_api.list_platforms = RommApiAdapter(client).list_platforms  # type: ignore[method-assign]
        emit.reset_mock()
        await run_to_its_end("run-2")

        progress = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_progress"]
        assert [(f["stage"], f["message"], f["running"]) for f in progress] == [
            ("error", "Unexpected response from /api/platforms: dict", False)
        ]
        assert {c.args[0] for c in emit.call_args_list} == {"sync_progress"}
        with library.uow as uow:
            assert uow.sync_runs.get("run-2") is None
        assert bindings() == {1: 5001, 2: 5002, 4: 5004}


class TestStoppedRunLeavesNoHiddenStaleRow:
    """A bound version its stamping fetch did not return no longer holds its platform's skip (#743 D10).

    A run that commits a platform writes its completion stamp with the checksum of
    the ids its fetch returned. A bound version that fetch did not return stays
    bound — the stale removal keeps a version gone from a platform it read whole,
    and a stopped run removes nothing — and the checksum already records its
    absence, so the next run skips the unchanged platform, keeps the version bound
    and removes nothing. A reissued id changes the id set and forces the full fetch
    the same-dump move needs (``test_fetcher.py``, ``test_a_reissued_id_forces_a_full_fetch``).
    """

    @staticmethod
    async def _stop_after_applying_n64(library, fake_romm_api):
        """Run 1: apply N64 in full, whose fetch no longer returns the bound V, then cancel inside GBA."""

        # RomM serves A and B; V (rom 3) was deleted since the last completed fetch.
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 1, "name": "A", "updated_at": _OLD_UPDATED_AT},
                {"id": 2, "name": "B", "updated_at": _OLD_UPDATED_AT},
            ],
        )
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[{"id": 20, "name": "Z", "updated_at": _OLD_UPDATED_AT}],
        )
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00", rom_count=3, fetch_id="run-0")
        for rom_id, name in ((1, "A"), (2, "B"), (3, "V")):
            _seed_rom_row(library, rom_id, app_id=5000 + rom_id, platform_slug="n64", name=name)
            with library.uow as uow:
                row = uow.roms.get(rom_id)
                row.record_fetch_generation("run-0")
                uow.roms.save(row)

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def cancel_inside_gba(unit, event):
            if unit.slug == "gba":
                box.sync_state = SyncState.CANCELLING
                return None
            event.set()
            return {}

        library.chunk_dispatcher._wait_for_unit_complete = cancel_inside_gba
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = "run-1"
        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
            assert uow.roms.get(3).shortcut_app_id == 5003
        assert stamp is not None
        assert (stamp.rom_count, stamp.fetch_id) == (2, "run-1")

    @pytest.mark.asyncio
    async def test_next_completed_run_skips_the_platform_and_keeps_the_vanished_version(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._stop_after_applying_n64(library, fake_romm_api)
        box = library.sync._box

        # Run 2 completes.
        run_2_starts_at = len(emit.call_args_list)
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = "run-2"
        await library.sync._orchestrator._do_sync_per_unit()

        run_2 = emit.call_args_list[run_2_starts_at:]
        applied_units = {c.args[1]["unit_name"] for c in run_2 if c.args and c.args[0] == "sync_apply_unit"}
        assert "N64" not in applied_units
        stale_events = [c.args[1] for c in run_2 if c.args and c.args[0] == "sync_stale"]
        assert [event["remove"] for event in stale_events] == [[]]
        with library.uow as uow:
            assert uow.roms.get(3).shortcut_app_id == 5003
            assert uow.roms.get(1).shortcut_app_id == 5001

    @pytest.mark.asyncio
    async def test_preview_after_the_stop_skips_the_platform_and_counts_no_removal(self, make_library, fake_romm_api):
        """The preview skips N64 too, and a version gone from it is no removal."""
        library = make_library(romm_api=fake_romm_api)
        await self._stop_after_applying_n64(library, fake_romm_api)
        fake_romm_api.call_log.clear()

        result = await library.sync.sync_preview()

        assert result["success"] is True
        assert ("list_roms", (1,)) not in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]
        assert result["summary"]["remove_count"] == 0
        assert all(row["remove_count"] == 0 for row in result["summary"]["platform_breakdown"])


class TestOnlyAFreshStampRestoresTheSkip:
    """A full fetch whose stamp no longer matches RomM offers Apply, re-stamps, and skips next sync (#743).

    Each case is a round: the sync after the change fetches the platform in full,
    the preview offers Apply through ``restamp_platform_count`` even with no
    shortcut to change, the apply writes a stamp recording the fetch's start and
    id checksum, and the sync after that skips the platform.
    """

    @staticmethod
    def _seed_n64(library, fake_romm_api, *, roms):
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": rom_id, "name": f"G{rom_id}", "fs_name": f"g{rom_id}.z64", "updated_at": _OLD_UPDATED_AT}
                for rom_id in roms
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        for rom_id in roms:
            _seed_rom_row(
                library, rom_id, app_id=5000 + rom_id, platform_slug="n64", name=f"G{rom_id}", fs_name=f"g{rom_id}.z64"
            )
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

    @staticmethod
    async def _apply(library, run_id, wait=_fake_wait_set_event):
        box = library.sync._box
        library.chunk_dispatcher._wait_for_unit_complete = wait
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = run_id
        await library.sync._orchestrator._do_sync_per_unit()

    @staticmethod
    def _walked_n64(fake_romm_api) -> bool:
        return ("list_roms", (1,)) in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]

    @staticmethod
    def _stamp(library):
        with library.uow as uow:
            return uow.platform_sync_state.get("n64")

    @pytest.mark.asyncio
    async def test_a_hidden_rom_with_a_shortcut_is_fetched_in_full_once_then_skipped(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        from domain.platform_skip import rom_id_checksum

        self._seed_n64(library, fake_romm_api, roms=(1, 2))
        await self._apply(library, "run-1")
        assert self._stamp(library).rom_id_checksum == rom_id_checksum([1, 2])

        # Hidden in RomM: gone from the list and the id index, no updated_at moved.
        fake_romm_api.hidden_rom_ids = {2}
        fake_romm_api.call_log.clear()
        preview = await library.sync.sync_preview()

        assert self._walked_n64(fake_romm_api)
        summary = preview["summary"]
        assert summary["restamp_platform_count"] == 1
        assert (summary["new_count"], summary["changed_count"], summary["remove_count"]) == (0, 0, 0)

        apply_starts_at = len(emit.call_args_list)
        await self._apply(library, "run-2")

        applied = {
            c.args[1]["unit_name"] for c in emit.call_args_list[apply_starts_at:] if c.args[0] == "sync_apply_unit"
        }
        assert applied == {"N64"}
        stamp = self._stamp(library)
        assert stamp.rom_id_checksum == rom_id_checksum([1])
        with library.uow as uow:
            assert uow.roms.get(2).shortcut_app_id == 5002

        fake_romm_api.call_log.clear()
        preview = await library.sync.sync_preview()

        assert not self._walked_n64(fake_romm_api)
        assert preview["summary"]["restamp_platform_count"] == 0

        next_starts_at = len(emit.call_args_list)
        await self._apply(library, "run-3")
        assert not [c for c in emit.call_args_list[next_starts_at:] if c.args[0] == "sync_apply_unit"]
        assert not self._walked_n64(fake_romm_api)

    @pytest.mark.asyncio
    async def test_a_stamp_without_a_checksum_is_fetched_in_full_once_then_skipped(
        self, make_library, fake_romm_api, emit
    ):
        """Every stamp after the upgrade to migration 031: one full fetch and one Apply, then the skip again."""
        library = make_library(romm_api=fake_romm_api)
        from domain.platform_sync_state import PlatformSyncState

        self._seed_n64(library, fake_romm_api, roms=(1, 2))
        with library.uow as uow:
            uow.platform_sync_state.save(
                PlatformSyncState.stamp(
                    platform_slug="n64", at="2025-01-01T00:00:00+00:00", rom_count=2, fetch_id="run-0"
                )
            )

        preview = await library.sync.sync_preview()

        assert self._walked_n64(fake_romm_api)
        summary = preview["summary"]
        assert summary["restamp_platform_count"] == 1
        assert (summary["new_count"], summary["changed_count"], summary["remove_count"]) == (0, 0, 0)

        await self._apply(library, "run-1")

        stamp = self._stamp(library)
        assert stamp.rom_id_checksum is not None
        assert stamp.fetch_started_at == "2026-01-01T00:00:00+00:00"

        fake_romm_api.call_log.clear()
        preview = await library.sync.sync_preview()

        assert not self._walked_n64(fake_romm_api)
        assert preview["summary"]["restamp_platform_count"] == 0

    @pytest.mark.asyncio
    async def test_a_rom_edited_while_the_run_applied_is_fetched_in_full_next_sync(self, make_library, fake_romm_api):
        """#743 Done when: the stamp's reference is its fetch's start, not the run's end.

        The apply takes half an hour on the clock; RomM records an edit twenty
        minutes in, after N64's fetch started and before its final chunk
        committed. The next sync fetches N64 in full.
        """
        library = make_library(romm_api=fake_romm_api)
        self._seed_n64(library, fake_romm_api, roms=(1, 2))
        clock = library.sync._orchestrator._clock

        async def an_edit_lands_during_the_apply(_unit, event):
            clock.advance(1200)
            fake_romm_api.roms[1]["updated_at"] = clock.now().isoformat()
            clock.advance(600)
            event.set()
            return {}

        await self._apply(library, "run-1", wait=an_edit_lands_during_the_apply)
        stamp = self._stamp(library)
        assert stamp.fetch_started_at == "2026-01-01T00:00:00+00:00"
        assert stamp.completed_at == "2026-01-01T00:30:00+00:00"

        fake_romm_api.call_log.clear()
        await self._apply(library, "run-2")

        assert self._walked_n64(fake_romm_api)


class TestOnlyAFreshCollectionStampRestoresTheSkip:
    """A collection follows the platform skip's rules: fetch start, listing checksum, re-stamp (#2477).

    The collection's games are on N64, whose sync is off, so only the collection
    delivers them and only its own skip decides whether they are walked. Each
    case is a round like the platform's: a sync that fetches the collection in
    full offers Apply through ``restamp_collection_count`` even with no shortcut
    to change, the apply writes a stamp recording the fetch's start and the
    listing's checksum, and the sync after that skips the collection.
    """

    _LISTED_AT = "2025-01-01T00:00:00+00:00"
    _WALKS = ("list_roms_by_collection", "list_roms_by_smart_collection")

    def _seed_faves(self, library, fake_romm_api, *, kind="standard", listed=(1, 2), members=(1, 2)):
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": rom_id, "name": f"G{rom_id}", "fs_name": f"g{rom_id}.z64", "updated_at": _OLD_UPDATED_AT}
                for rom_id in members
            ],
        )
        entry = {
            "id": 7,
            "name": "Faves",
            "rom_count": len(listed),
            "rom_ids": list(listed),
            "updated_at": self._LISTED_AT,
        }
        if kind == "standard":
            fake_romm_api.collections.append(entry)
        else:
            fake_romm_api.smart_collections.append(entry)
        membership_key = "collection_ids" if kind == "standard" else "smart_collection_ids"
        for rom_id in members:
            fake_romm_api.roms[rom_id].setdefault(membership_key, []).append(7)
            _seed_rom_row(
                library, rom_id, app_id=5000 + rom_id, platform_slug="n64", name=f"G{rom_id}", fs_name=f"g{rom_id}.z64"
            )
        library.settings["enabled_platforms"] = {"1": False}
        library.settings["enabled_collections"] = {kind: {"7": True}}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})

    @staticmethod
    async def _apply(library, run_id, wait=_fake_wait_set_event):
        box = library.sync._box
        library.chunk_dispatcher._wait_for_unit_complete = wait
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = run_id
        await library.sync._orchestrator._do_sync_per_unit()

    def _walked(self, fake_romm_api) -> bool:
        return any(name in self._WALKS for name, _args, _kwargs in fake_romm_api.call_log)

    @staticmethod
    def _probes(fake_romm_api):
        return [args for name, args, _kwargs in fake_romm_api.call_log if name == "list_collection_roms_updated_after"]

    @staticmethod
    def _stamp(library, kind="standard"):
        with library.uow as uow:
            return uow.collection_sync_state.get("7", kind)

    @pytest.mark.asyncio
    async def test_a_stamp_without_a_fetch_start_is_fetched_in_full_once_then_skipped(
        self, make_library, fake_romm_api
    ):
        """Every stamp after the upgrade to migration 032: one full fetch and one Apply, then the skip again."""
        library = make_library(romm_api=fake_romm_api)
        from domain.platform_skip import rom_id_checksum

        self._seed_faves(library, fake_romm_api)
        _seed_collection_stamp(
            library.uow,
            "7",
            "standard",
            updated_at=self._LISTED_AT,
            completed_at="2025-06-01T00:00:00+00:00",
            rom_count=2,
            member_rom_ids=(1, 2),
        )

        preview = await library.sync.sync_preview()

        assert self._walked(fake_romm_api)
        assert self._probes(fake_romm_api) == []
        summary = preview["summary"]
        assert summary["restamp_collection_count"] == 1
        assert summary["restamp_platform_count"] == 0
        assert (summary["new_count"], summary["changed_count"], summary["remove_count"]) == (0, 0, 0)

        await self._apply(library, "run-1")

        stamp = self._stamp(library)
        assert stamp.fetch_started_at == "2026-01-01T00:00:00+00:00"
        assert stamp.listing_rom_id_checksum == rom_id_checksum([1, 2])

        fake_romm_api.call_log.clear()
        preview = await library.sync.sync_preview()

        assert not self._walked(fake_romm_api)
        assert self._probes(fake_romm_api) == [(7, "standard", "2025-12-31T23:50:00+00:00")]
        assert preview["summary"]["restamp_collection_count"] == 0

    @pytest.mark.asyncio
    async def test_a_member_edited_while_the_run_applied_is_fetched_in_full_next_sync(
        self, make_library, fake_romm_api
    ):
        """#2477 Done when: the stamp's reference is its fetch's start, not the run's end.

        The apply takes half an hour on the clock; RomM records an edit to a
        member twenty minutes in, after the collection's fetch started and before
        its final chunk committed. The next sync fetches the collection in full
        and offers Apply for it.
        """
        library = make_library(romm_api=fake_romm_api)
        self._seed_faves(library, fake_romm_api)
        clock = library.sync._orchestrator._clock

        async def an_edit_lands_during_the_apply(_unit, event):
            clock.advance(1200)
            fake_romm_api.roms[1]["updated_at"] = clock.now().isoformat()
            clock.advance(600)
            event.set()
            return {}

        await self._apply(library, "run-1", wait=an_edit_lands_during_the_apply)
        stamp = self._stamp(library)
        assert stamp.fetch_started_at == "2026-01-01T00:00:00+00:00"
        assert stamp.completed_at == "2026-01-01T00:30:00+00:00"

        fake_romm_api.call_log.clear()
        preview = await library.sync.sync_preview()

        assert self._walked(fake_romm_api)
        assert preview["summary"]["restamp_collection_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["raised", "unreadable"])
    async def test_a_collection_whose_probe_failed_is_fetched_in_full_and_not_counted(
        self, make_library, fake_romm_api, failure
    ):
        """#2477 D3: a fresh stamp settles nothing there, and a failing probe would offer Apply on every sync."""
        library = make_library(romm_api=fake_romm_api)
        self._seed_faves(library, fake_romm_api)
        await self._apply(library, "run-1")
        assert self._stamp(library).listing_rom_id_checksum is not None
        if failure == "raised":
            fake_romm_api.list_collection_roms_updated_after_side_effect = RuntimeError("probe boom")
        else:
            fake_romm_api.list_collection_roms_updated_after_answer = ["not", "an", "object"]

        fake_romm_api.call_log.clear()
        preview = await library.sync.sync_preview()

        assert len(self._probes(fake_romm_api)) == 1
        assert self._walked(fake_romm_api)
        assert preview["summary"]["restamp_collection_count"] == 0

    @pytest.mark.asyncio
    async def test_an_empty_collection_and_one_without_updated_at_are_not_counted(self, make_library, fake_romm_api):
        """The apply writes neither of them a stamp, so a re-stamp Apply would change nothing for them (#2477 D3)."""
        library = make_library(romm_api=fake_romm_api)
        self._seed_faves(library, fake_romm_api)
        fake_romm_api.collections += [
            {"id": 8, "name": "Empty", "rom_count": 0, "rom_ids": [], "updated_at": self._LISTED_AT},
            {"id": 9, "name": "Undated", "rom_count": 1, "rom_ids": [1]},
        ]
        fake_romm_api.roms[1]["collection_ids"].append(9)
        library.settings["enabled_collections"] = {"standard": {"7": True, "8": True, "9": True}}

        preview = await library.sync.sync_preview()

        # Only Faves, which has members, an updated_at and no stamp yet.
        assert preview["summary"]["restamp_collection_count"] == 1

    @pytest.mark.asyncio
    async def test_an_own_smart_collection_whose_walk_returns_more_than_its_listing_is_skipped_once_stamped(
        self, make_library, fake_romm_api
    ):
        """Its stamp replays the walk's extra member, while the skip compares only the listing's ids."""
        library = make_library(romm_api=fake_romm_api)
        self._seed_faves(library, fake_romm_api, kind="smart", listed=(1,), members=(1, 2))

        await self._apply(library, "run-1")
        assert self._stamp(library, "smart").member_rom_ids == (1, 2)

        fake_romm_api.call_log.clear()
        preview = await library.sync.sync_preview()

        assert not self._walked(fake_romm_api)
        assert preview["summary"]["restamp_collection_count"] == 0


class TestPlatformTurnedOffAndBackOn:
    """A platform whose sync is turned off and back on gets all its shortcuts back (#2094).

    While the platform is off, the stale-removal scan unbinds its games except the
    ones an enabled collection lists. Were its completion stamp left able to skip,
    the game the collection held would stay bound, which keeps the zero-bound guard
    quiet, and the unbound ones would still carry the stamp's generation, so every
    count the skip reads would match. The stamp itself stays, because removed-game
    discovery reads its generation.
    """

    @pytest.mark.asyncio
    async def test_the_games_unbound_while_it_was_off_are_bound_again(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[1])
        library.settings["enabled_collections"] = {"standard": {"7": True}}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_n64_game(unit, event):
            event.set()
            return {"1": 5001, "2": 5002, "3": 5003} if unit.slug == "n64" else {}

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_n64_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        def bindings():
            with library.uow as uow:
                return {rom_id: uow.roms.get(rom_id).shortcut_app_id for rom_id in (1, 2, 3)}

        # Run 1: N64 and the collection are on.
        library.settings["enabled_platforms"] = {"1": True}
        await complete_a_run("run-1")
        assert bindings() == {1: 5001, 2: 5002, 3: 5003}
        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert (stamp.rom_count, stamp.fetch_id) == (3, "run-1")

        # Run 2: N64 is off; the collection keeps its one game bound.
        library.settings["enabled_platforms"] = {"1": False}
        await complete_a_run("run-2")
        assert bindings() == {1: 5001, 2: None, 3: None}
        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert (stamp.fetch_id, stamp.skip_revoked) == ("run-1", True)

        # Run 3: N64 is back on and nothing changed on RomM.
        library.settings["enabled_platforms"] = {"1": True}
        await complete_a_run("run-3")
        assert bindings() == {1: 5001, 2: 5002, 3: 5003}
        with library.uow as uow:
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert (stamp.fetch_id, stamp.skip_revoked) == ("run-3", False)

    @pytest.mark.asyncio
    async def test_a_platform_the_run_processed_keeps_its_skip_when_romm_drops_a_game(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        )
        library.settings["enabled_platforms"] = {"1": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        app_ids = {1: 5001, 2: 5002}

        async def bind_what_romm_serves(unit, event):
            event.set()
            served = {rom_id: app_id for rom_id, app_id in app_ids.items() if rom_id in fake_romm_api.roms}
            return {str(rom_id): app_id for rom_id, app_id in served.items()} if unit.slug == "n64" else {}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_romm_serves

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        await complete_a_run("run-1")

        # RomM drops game 2; N64 stays on, so the run fetches it, and the stale
        # removal keeps the version gone from RomM bound.
        del fake_romm_api.roms[2]
        fake_romm_api.platforms[0]["rom_count"] = 1
        await complete_a_run("run-2")

        with library.uow as uow:
            assert uow.roms.get(2).shortcut_app_id == 5002
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert (stamp.fetch_id, stamp.skip_revoked) == ("run-2", False)

    @pytest.mark.asyncio
    async def test_a_platform_still_on_that_romm_no_longer_lists_keeps_its_shortcuts(
        self, make_library, fake_romm_api, emit
    ):
        """D15: RomM's platform list is a complete answer, so its games are gone from RomM, not turned off."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}])
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 4, "name": "D"}])
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_game(unit, event):
            event.set()
            return {"n64": {"1": 5001}, "gba": {"4": 5004}}.get(unit.slug, {})

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        await complete_a_run("run-1")

        # N64 is still turned on, but RomM no longer lists it.
        fake_romm_api.platforms = [p for p in fake_romm_api.platforms if p["slug"] != "n64"]
        fake_romm_api.call_log.clear()
        run_2_starts_at = len(emit.call_args_list)
        await complete_a_run("run-2")

        stale = [c.args[1] for c in emit.call_args_list[run_2_starts_at:] if c.args and c.args[0] == "sync_stale"]
        assert [event["remove"] for event in stale] == [[]]
        assert ("get_rom_once", (1,)) not in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]
        with library.uow as uow:
            assert uow.roms.get(1).shortcut_app_id == 5001
            n64 = uow.platform_sync_state.get("n64")
            gba = uow.platform_sync_state.get("gba")
        assert n64 is not None
        assert (n64.fetch_id, n64.skip_revoked) == ("run-1", False)
        assert gba is not None
        assert gba.skip_revoked is False

    @pytest.mark.asyncio
    async def test_a_platform_romm_no_longer_lists_keeps_its_steam_collection(self, make_library, fake_romm_api, emit):
        """D15 rows stay in their platform's collection under its last known name, as D1's kept rows do."""
        library = make_library(romm_api=fake_romm_api)
        # A name that does not fold equal to its slug, so a collection keyed by the slug reads as a change.
        _seed_platform(fake_romm_api, platform_id=1, name="Nintendo 64", slug="n64", roms=[{"id": 1, "name": "A"}])
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 4, "name": "D"}])
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_game(unit, event):
            event.set()
            return {"n64": {"1": 5001}, "gba": {"4": 5004}}.get(unit.slug, {})

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        await complete_a_run("run-1")

        fake_romm_api.platforms = [p for p in fake_romm_api.platforms if p["slug"] != "n64"]
        for run_id in ("run-2", "run-3"):
            run_starts_at = len(emit.call_args_list)
            await complete_a_run(run_id)

            complete = [c.args[1] for c in emit.call_args_list[run_starts_at:] if c.args[0] == "sync_complete"]
            assert [event["platform_app_ids"] for event in complete] == [{"Nintendo 64": [5001], "GBA": [5004]}]

        preview = await library.sync.sync_preview()
        assert preview["summary"]["remove_count"] == 0
        assert preview["summary"]["platform_collection_diff"]["has_changes"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stop", ["cancelled", "paused", "interrupted"])
    async def test_a_stopped_run_in_between_keeps_the_collection_s_name(self, make_library, fake_romm_api, emit, stop):
        """A stopped run still writes the names cache; the delisted platform's name has to survive it."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}])
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 4, "name": "D"}])
        library.settings["enabled_platforms"] = {"1": True, "2": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_game(unit, event):
            event.set()
            return {"n64": {"1": 5001}, "gba": {"4": 5004}}.get(unit.slug, {})

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        await complete_a_run("run-1")
        fake_romm_api.platforms = [p for p in fake_romm_api.platforms if p["slug"] != "n64"]

        # Run 2 stops right after its work queue is built.
        build_work_queue = library.sync._fetcher.build_work_queue

        async def build_then_stop():
            queue = await build_work_queue()
            box.run_paused = stop == "paused"
            box.run_interrupted = stop == "interrupted"
            box.request_cancel()
            return queue

        library.sync._fetcher.build_work_queue = build_then_stop
        await complete_a_run("run-2")
        library.sync._fetcher.build_work_queue = build_work_queue

        run_3_starts_at = len(emit.call_args_list)
        await complete_a_run("run-3")

        complete = [c.args[1] for c in emit.call_args_list[run_3_starts_at:] if c.args[0] == "sync_complete"]
        assert [event["platform_app_ids"] for event in complete] == [{"N64": [5001], "GBA": [5004]}]

    @pytest.mark.asyncio
    async def test_a_platform_read_mid_run_still_counts_as_unlisted(self, make_library, fake_romm_api, emit):
        """A game page asking for N64's system mid-run keeps ids for it again; the run's own listing still decides."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}])
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=[{"id": 4, "name": "D"}])
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        platform_systems = PlatformSystemService(
            config=PlatformSystemServiceConfig(
                uow_factory=FakeUnitOfWorkFactory(uow=library.uow),
                romm_api=fake_romm_api,
                source_platform_systems=FakeSourcePlatformSystems(),
                logger=logging.getLogger("test_sync_orchestrator"),
                log_debug=MagicMock(),
            )
        )

        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_every_game(unit, event):
            if unit.slug == "gba":
                platform_systems.platform_system("n64")
            event.set()
            return {"n64": {"1": 5001}, "gba": {"4": 5004, "5": 5005}}.get(unit.slug, {})

        library.chunk_dispatcher._wait_for_unit_complete = bind_every_game

        async def complete_a_run(run_id):
            box.sync_state = SyncState.RUNNING
            box.current_sync_id = run_id
            await library.sync._orchestrator._do_sync_per_unit()

        await complete_a_run("run-1")

        # RomM drops N64 from its list, and GBA gains a game, so run 2 applies GBA.
        fake_romm_api.platforms = [p for p in fake_romm_api.platforms if p["slug"] != "n64"]
        fake_romm_api.roms[5] = {**fake_romm_api.roms[4], "id": 5, "name": "E"}
        fake_romm_api.platforms[0]["rom_count"] = 2
        run_2_starts_at = len(emit.call_args_list)
        await complete_a_run("run-2")

        with library.uow as uow:
            assert "n64" in (decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY)) or {})
            assert uow.roms.get(1).shortcut_app_id == 5001
        stale = [c.args[1] for c in emit.call_args_list[run_2_starts_at:] if c.args and c.args[0] == "sync_stale"]
        assert [event["remove"] for event in stale] == [[]]


class TestAVersionReachedOnlyThroughACollection:
    """A version a collection held, missing from every collection now, is asked for once (D4, D11, D12).

    GBA's sync is off; the Faves collection reached games 20 and 21 there and
    left a completion stamp listing both. Then 21 leaves Faves.
    """

    @staticmethod
    async def _bind_both_then_drop_21(library, fake_romm_api):
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[{"id": 20, "name": "A"}, {"id": 21, "name": "B"}],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20, 21])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00"
        library.settings["enabled_platforms"] = {"2": False}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_collection_holds(unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_collection_holds
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = "run-1"
        await library.sync._orchestrator._do_sync_per_unit()
        with library.uow as uow:
            assert (uow.roms.get(20).shortcut_app_id, uow.roms.get(21).shortcut_app_id) == (6020, 6021)
            assert {rid for stamp in uow.collection_sync_state.iter_all() for rid in stamp.member_rom_ids} == {20, 21}

        faves = fake_romm_api.collections[0]
        faves["rom_ids"] = [20]
        faves["rom_count"] = 1
        faves["updated_at"] = "2030-01-01T00:00:00"
        fake_romm_api.roms[21]["collection_ids"] = []

    @classmethod
    async def _complete_run_2(cls, library, emit):
        return await cls._complete_run(library, emit, "run-2")

    @staticmethod
    async def _complete_run(library, emit, run_id):
        box = library.sync._box
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = run_id
        run_2_starts_at = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        stale = [c.args[1] for c in emit.call_args_list[run_2_starts_at:] if c.args and c.args[0] == "sync_stale"]
        with library.uow as uow:
            binding = uow.roms.get(21).shortcut_app_id
        return [event["remove"] for event in stale], binding

    @pytest.mark.asyncio
    async def test_a_404_keeps_the_shortcut(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)
        fake_romm_api.get_rom_once_side_effect_by_id[21] = RommNotFoundError("Rom with id 21 not found")

        assert await self._complete_run_2(library, emit) == ([[]], 6021)
        assert ("get_rom_once", (21,)) in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]

    @pytest.mark.asyncio
    async def test_a_rom_romm_still_returns_is_removed(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)

        assert await self._complete_run_2(library, emit) == ([[{"rom_id": 21, "app_id": 6021}]], None)

    @pytest.mark.asyncio
    async def test_an_answer_that_establishes_nothing_keeps_the_shortcut(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)
        fake_romm_api.get_rom_once_side_effect_by_id[21] = RommConnectionError("connection refused")

        assert await self._complete_run_2(library, emit) == ([[]], 6021)

    @pytest.mark.asyncio
    async def test_a_404_keeps_asking_on_every_later_run(self, make_library, fake_romm_api, emit):
        """The run that kept the row rewrote Faves' record without it; the route still says collection."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)
        fake_romm_api.get_rom_once_side_effect_by_id[21] = RommNotFoundError("Rom with id 21 not found")
        assert await self._complete_run_2(library, emit) == ([[]], 6021)
        with library.uow as uow:
            assert {rid for stamp in uow.collection_sync_state.iter_all() for rid in stamp.member_rom_ids} == {20}
        fake_romm_api.call_log.clear()

        assert await self._complete_run(library, emit, "run-3") == ([[]], 6021)
        assert ("get_rom_once", (21,)) in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]

    @pytest.mark.asyncio
    async def test_a_stopped_run_leaves_the_row_asked_at_the_next_one(self, make_library, fake_romm_api, emit):
        """A run that committed Faves without 21, then stopped before its stale removal, changes nothing for 21."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)
        fake_romm_api.get_rom_once_side_effect_by_id[21] = RommNotFoundError("Rom with id 21 not found")
        box = library.sync._box
        cancel_after_the_collection = library.chunk_dispatcher._wait_for_unit_complete

        async def commit_then_stop(unit, event):
            applied = await cancel_after_the_collection(unit, event)
            box.sync_state = SyncState.CANCELLING
            return applied

        library.chunk_dispatcher._wait_for_unit_complete = commit_then_stop
        fake_romm_api.call_log.clear()
        # A stopped run removes nothing and asks nothing.
        assert await self._complete_run(library, emit, "run-2") == ([[]], 6021)
        assert ("get_rom_once", (21,)) not in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]
        with library.uow as uow:
            assert {rid for stamp in uow.collection_sync_state.iter_all() for rid in stamp.member_rom_ids} == {20}
        library.chunk_dispatcher._wait_for_unit_complete = cancel_after_the_collection
        fake_romm_api.call_log.clear()

        assert await self._complete_run(library, emit, "run-3") == ([[]], 6021)
        assert ("get_rom_once", (21,)) in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]

    @pytest.mark.asyncio
    async def test_a_row_last_delivered_through_its_platform_is_removed_unasked(
        self, make_library, fake_romm_api, emit
    ):
        """The contrast: GBA was on when 21 was committed, and is turned off now."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)
        with library.uow as uow:
            row = uow.roms.get(21)
            row.record_delivery("platform")
            uow.roms.save(row)
        fake_romm_api.get_rom_once_side_effect_by_id[21] = RommNotFoundError("Rom with id 21 not found")
        fake_romm_api.call_log.clear()

        assert await self._complete_run_2(library, emit) == ([[{"rom_id": 21, "app_id": 6021}]], None)
        assert ("get_rom_once", (21,)) not in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("listed", "remove"), [(True, []), (False, [{"rom_id": 21, "app_id": 6021}])])
    async def test_a_row_no_unit_delivered_since_the_route_was_recorded_follows_the_collection_records(
        self, make_library, fake_romm_api, emit, listed, remove
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)
        _seed_rom_row(library, 21, app_id=6021, platform_slug="gba", name="B")
        fake_romm_api.get_rom_once_side_effect_by_id[21] = RommNotFoundError("Rom with id 21 not found")
        if not listed:
            with library.uow as uow:
                uow.collection_sync_state.clear()

        stale, _binding = await self._complete_run_2(library, emit)

        assert stale == [remove]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("answer", "remove_count"),
        [(RommNotFoundError("Rom with id 21 not found"), 0), (None, 1), (RommConnectionError("refused"), 0)],
    )
    async def test_the_preview_gives_the_same_answers(self, make_library, fake_romm_api, answer, remove_count):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_both_then_drop_21(library, fake_romm_api)
        if answer is not None:
            fake_romm_api.get_rom_once_side_effect_by_id[21] = answer

        result = await library.sync.sync_preview()

        assert result["summary"]["remove_count"] == remove_count
        assert result["summary"]["disabled_platform_remove_count"] == remove_count


class TestAVersionSwitchedOffTheCollectionsMember:
    """A game only a collection delivers keeps its shortcut after a switch to a version the collection does not list.

    GBA's sync is off and RomM lists it. Faves holds Golf (USA), 20, which the
    first run binds; a version switch then moves that shortcut onto Golf
    (Europe), 22, which no collection lists, and records no route on it.
    """

    @staticmethod
    async def _bind_20_then_switch_to_22(library, fake_romm_api, *, key_of_22=None):
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[
                {"id": 20, "name": "Golf (USA)", "igdb_id": 300, "rom_user": {"is_main_sibling": True}},
                {"id": 22, "name": "Golf (Europe)", "igdb_id": 300},
                {"id": 23, "name": "Golf (Japan)", "igdb_id": 300},
            ],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00"
        library.settings["enabled_platforms"] = {"2": False}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_collection_holds(unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_collection_holds
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = "run-1"
        await library.sync._orchestrator._do_sync_per_unit()
        with library.uow as uow:
            key = uow.roms.get(20).sibling_group_key
            assert (uow.roms.get(20).shortcut_app_id, key) == (6020, "igdb:300:2")
        _seed_rom_row(
            library,
            22,
            app_id=6020,
            platform_slug="gba",
            name="Golf (Europe)",
            sibling_group_key=key_of_22 or key,
        )
        with library.uow as uow:
            assert uow.roms.get(20).shortcut_app_id is None
            assert uow.roms.get(22).delivered_through is None

    @staticmethod
    async def _run_2(library, emit):
        box = library.sync._box
        box.sync_state = SyncState.RUNNING
        box.current_sync_id = "run-2"
        run_2_starts_at = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        stale = [c.args[1] for c in emit.call_args_list[run_2_starts_at:] if c.args and c.args[0] == "sync_stale"]
        with library.uow as uow:
            binding = uow.roms.get(22).shortcut_app_id
        return [event["remove"] for event in stale], binding

    @staticmethod
    def _fetch_faves_again(fake_romm_api):
        fake_romm_api.collections[0]["updated_at"] = "2030-01-01T00:00:00"

    @staticmethod
    def _faves_was_fetched(fake_romm_api):
        return any(name == "list_roms_by_collection" for name, _args, _kwargs in fake_romm_api.call_log)

    @pytest.mark.asyncio
    async def test_a_collection_skipped_as_unchanged_keeps_the_switched_version(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        fake_romm_api.call_log.clear()

        assert await self._run_2(library, emit) == ([[]], 6020)
        assert not self._faves_was_fetched(fake_romm_api)
        assert ("get_rom_once", (22,)) not in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]

    @pytest.mark.asyncio
    async def test_a_collection_fetched_again_keeps_the_switched_version(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        self._fetch_faves_again(fake_romm_api)
        fake_romm_api.call_log.clear()

        assert await self._run_2(library, emit) == ([[]], 6020)
        assert self._faves_was_fetched(fake_romm_api)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fetched", [False, True], ids=["skipped", "fetched"])
    async def test_the_preview_counts_no_removal(self, make_library, fake_romm_api, fetched):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        if fetched:
            self._fetch_faves_again(fake_romm_api)

        result = await library.sync.sync_preview()

        assert result["summary"]["remove_count"] == 0

    @staticmethod
    def _faves_now_holds_only_23(fake_romm_api):
        """Golf (Japan), never delivered before and so without a row of its own, replaces 20 in Faves."""
        faves = fake_romm_api.collections[0]
        faves["rom_ids"] = [23]
        faves["updated_at"] = "2030-01-01T00:00:00"
        fake_romm_api.roms[20]["collection_ids"] = []
        fake_romm_api.roms[23]["collection_ids"] = [7]

    @pytest.mark.asyncio
    async def test_a_version_the_collection_delivers_for_the_first_time_keeps_the_switched_one(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        self._faves_now_holds_only_23(fake_romm_api)
        with library.uow as uow:
            assert uow.roms.get(23) is None

        assert await self._run_2(library, emit) == ([[]], 6020)

    @pytest.mark.asyncio
    async def test_the_preview_counts_no_removal_for_a_version_delivered_for_the_first_time(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        self._faves_now_holds_only_23(fake_romm_api)

        result = await library.sync.sync_preview()

        assert result["summary"]["remove_count"] == 0

    @staticmethod
    def _record_on_22(library, *, route, mark=None):
        with library.uow as uow:
            row = uow.roms.get(22)
            row.record_delivery(route)
            if mark is not None:
                row.mark_gone_from_romm(mark)
            uow.roms.save(row)

    @staticmethod
    def _gone_mark_of_22(library):
        with library.uow as uow:
            return uow.roms.get(22).gone_from_romm

    @pytest.mark.asyncio
    async def test_a_switched_version_a_collection_last_delivered_is_asked_and_marked_on_a_404(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        self._record_on_22(library, route="collection")
        fake_romm_api.get_rom_once_side_effect_by_id[22] = RommNotFoundError("Rom with id 22 not found")
        fake_romm_api.call_log.clear()

        assert await self._run_2(library, emit) == ([[]], 6020)
        assert ("get_rom_once", (22,)) in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]
        assert self._gone_mark_of_22(library) == "not_found"

    @pytest.mark.asyncio
    async def test_a_switched_version_romm_still_returns_stays_bound_and_has_its_mark_cleared(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        self._record_on_22(library, route="collection", mark="not_found")

        assert await self._run_2(library, emit) == ([[]], 6020)
        assert self._gone_mark_of_22(library) is None

    @pytest.mark.asyncio
    async def test_the_preview_counts_no_removal_for_a_switched_version_romm_still_returns(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api)
        self._record_on_22(library, route="collection")

        result = await library.sync.sync_preview()

        assert result["summary"]["remove_count"] == 0

    @staticmethod
    def _rematch_left_22s_key_behind(fake_romm_api):
        """22's stored key no longer matches its game's; RomM names 22 as 20's sibling, and Faves is fetched again."""
        fake_romm_api.roms[20]["sibling_roms"] = [{"id": 22}]
        fake_romm_api.collections[0]["updated_at"] = "2030-01-01T00:00:00"

    @pytest.mark.asyncio
    async def test_a_switched_version_whose_stored_key_a_rematch_left_behind_is_judged_by_that_key(
        self, make_library, fake_romm_api, emit
    ):
        """The gap #2533 D10 accepts: 22 is removed, and the collection mints a new shortcut for 20."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api, key_of_22="romm:22:2")
        self._rematch_left_22s_key_behind(fake_romm_api)
        box = library.sync._box

        async def bind_new_shortcuts_under_new_app_ids(unit, event):
            event.set()
            return {str(rid): 7000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_new_shortcuts_under_new_app_ids

        assert await self._run_2(library, emit) == ([[{"rom_id": 22, "app_id": 6020}]], None)
        with library.uow as uow:
            assert uow.roms.get(20).shortcut_app_id == 7020

    @pytest.mark.asyncio
    async def test_the_preview_counts_the_same_removal_and_new_shortcut_for_a_key_a_rematch_left_behind(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api, key_of_22="romm:22:2")
        self._rematch_left_22s_key_behind(fake_romm_api)

        result = await library.sync.sync_preview()

        assert (result["summary"]["remove_count"], result["summary"]["new_count"]) == (1, 1)

    @pytest.mark.asyncio
    async def test_a_bound_version_of_another_game_is_still_removed(self, make_library, fake_romm_api, emit):
        """The contrast: 22 is a game of its own that no unit delivered, on a platform RomM lists."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api, key_of_22="igdb:301:2")

        assert await self._run_2(library, emit) == ([[{"rom_id": 22, "app_id": 6020}]], None)

    @pytest.mark.asyncio
    async def test_the_preview_counts_the_other_game_s_removal(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_then_switch_to_22(library, fake_romm_api, key_of_22="igdb:301:2")

        result = await library.sync.sync_preview()

        assert result["summary"]["remove_count"] == 1


class TestAGameOnlyACollectionReachesIsCountedAsItsApplyCollapsesIt:
    """The preview collapses a game only collections reach with the partial view its apply uses (#2536 D2).

    RomM lists GBA. Faves holds Golf (USA), 20, which the first run binds;
    Golf (Europe), 22, and Golf (Japan), 23, are its other versions on GBA.
    Each case runs the preview, then the apply it would start, so the two
    answer over the same library.
    """

    @staticmethod
    def _golf(rom_id, region, *, fs_name=None, **extra):
        """A version of Golf on GBA, as RomM lists it."""
        stem = f"Golf ({region})"
        return {
            "id": rom_id,
            "name": stem,
            "fs_name": fs_name or f"{stem}.zip",
            "fs_name_no_ext": stem,
            "igdb_id": 300,
            "platform_id": 2,
            "platform_name": "GBA",
            "platform_slug": "gba",
            **extra,
        }

    @classmethod
    async def _bind_20(cls, library, fake_romm_api, *, gba_synced=False):
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[
                cls._golf(20, "USA", rom_user={"is_main_sibling": True}),
                cls._golf(22, "Europe"),
                cls._golf(23, "Japan"),
            ],
        )
        library.settings["enabled_platforms"] = {"2": gba_synced}
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00"
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits
        assert box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
        await library.sync._orchestrator._do_sync_per_unit()
        assert cls._bindings(library) == {20: 6020}

    @staticmethod
    def _bindings(library):
        with library.uow as uow:
            return {rom.rom_id: rom.shortcut_app_id for rom in uow.roms.iter_all() if rom.shortcut_app_id}

    @staticmethod
    def _fetch_faves_again(fake_romm_api, rom_ids):
        faves = fake_romm_api.collections[0]
        for rom in fake_romm_api.roms.values():
            rom["collection_ids"] = [cid for cid in rom.get("collection_ids") or [] if cid != 7]
        for rom_id in rom_ids:
            fake_romm_api.roms[rom_id].setdefault("collection_ids", []).append(7)
        faves["rom_ids"] = list(rom_ids)
        faves["rom_count"] = len(rom_ids)
        faves["updated_at"] = "2030-01-01T00:00:00"

    @classmethod
    async def _preview_then_apply(cls, library, emit):
        """The preview's ``(new, changed, removed)``, then its apply's ``(shortcuts sent, removals, bindings)``."""
        summary = (await library.sync.sync_preview())["summary"]
        counted = (summary["new_count"], summary["changed_count"], summary["remove_count"])
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        run_2_starts_at = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        frames = [c.args for c in emit.call_args_list[run_2_starts_at:] if c.args]
        sent = [
            shortcut["rom_id"]
            for name, *payload in frames
            if name == "sync_apply_unit"
            for shortcut in payload[0]["shortcuts"]
        ]
        removed = [rom["rom_id"] for name, *payload in frames if name == "sync_stale" for rom in payload[0]["remove"]]
        return counted, (sent, removed, cls._bindings(library))

    @pytest.mark.asyncio
    async def test_a_version_gone_from_romm_whose_dump_the_collection_lists_under_a_new_id(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        del fake_romm_api.roms[20]
        fake_romm_api.get_rom_once_side_effect_by_id[20] = RommNotFoundError("Rom with id 20 not found")
        fake_romm_api.roms[24] = self._golf(24, "USA")
        self._fetch_faves_again(fake_romm_api, [24])

        assert await self._preview_then_apply(library, emit) == ((0, 0, 0), ([], [], {20: 6020}))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("installed", [False, True], ids=["not-installed", "installed"])
    async def test_the_collection_listing_a_same_stem_copy_of_a_live_version(
        self, make_library, fake_romm_api, emit, installed
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        if installed:
            with library.uow as uow:
                uow.rom_installs.save(
                    RomInstall.mark_installed(
                        rom_id=20,
                        file_path="/roms/gba/Golf (USA).zip",
                        rom_dir=None,
                        platform_slug="gba",
                        system="gba",
                        installed_at="2025-01-01T00:00:00",
                    )
                )
        fake_romm_api.roms[25] = self._golf(25, "USA", fs_name="Golf (USA).7z")
        self._fetch_faves_again(fake_romm_api, [25])

        assert await self._preview_then_apply(library, emit) == ((0, 0, 0), ([], [], {20: 6020}))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("same_collection", [True, False], ids=["same-collection", "another-collection"])
    async def test_a_version_without_a_file_beside_a_same_stem_version_with_one(
        self, make_library, fake_romm_api, emit, same_collection
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        fake_romm_api.roms[20]["has_file_on_disk"] = False
        fake_romm_api.roms[21] = self._golf(21, "USA", fs_name="Golf (USA)")
        if same_collection:
            self._fetch_faves_again(fake_romm_api, [20, 21])
        else:
            self._fetch_faves_again(fake_romm_api, [20])
            _seed_collection(fake_romm_api, collection_id=8, name="Mine", rom_ids=[21])
            library.settings["enabled_collections"] = {"standard": {"7": True, "8": True}}

        assert await self._preview_then_apply(library, emit) == ((0, 0, 0), ([], [], {20: 6020}))

    @pytest.mark.asyncio
    async def test_a_game_its_platform_delivers_still_moves_onto_the_dump_under_a_new_id(
        self, make_library, fake_romm_api, emit
    ):
        """The contrast: GBA is synced, so its unit holds the whole game and the move is counted and made."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api, gba_synced=True)
        del fake_romm_api.roms[20]
        fake_romm_api.roms[24] = self._golf(24, "USA")

        assert await self._preview_then_apply(library, emit) == ((0, 1, 0), ([20], [], {24: 6020}))

    @pytest.mark.asyncio
    async def test_a_game_its_platform_delivers_still_moves_off_a_version_without_a_file(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api, gba_synced=True)
        fake_romm_api.roms[20]["has_file_on_disk"] = False
        fake_romm_api.roms[21] = self._golf(21, "USA", fs_name="Golf (USA)")

        assert await self._preview_then_apply(library, emit) == ((0, 1, 0), ([20], [], {21: 6020}))


class TestAMemberASkippedCollectionStillListsIsCountedAsItsPlatformUnitRebindsIt:
    """The preview counts the same-dump rebind a platform unit makes for a member a skipped collection still lists.

    GBA is synced and holds Golf (USA), 20, which the first run binds, beside
    Golf (Europe), 22, and Golf (Japan), 23. Faves lists 20 and is skipped as
    unchanged on the second run, so the skip hands the preview 20's stored row
    while GBA's fetch no longer returns 20. Each case runs the preview, then the
    apply it would start, so the two answer over the same library.
    """

    _golf = staticmethod(TestAGameOnlyACollectionReachesIsCountedAsItsApplyCollapsesIt._golf)
    _bindings = staticmethod(TestAGameOnlyACollectionReachesIsCountedAsItsApplyCollapsesIt._bindings)

    @classmethod
    async def _bind_20(cls, library, fake_romm_api):
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[
                cls._golf(20, "USA", rom_user={"is_main_sibling": True}),
                cls._golf(22, "Europe"),
                cls._golf(23, "Japan"),
            ],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00"
        library.settings["enabled_platforms"] = {"2": True}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits
        assert box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
        await library.sync._orchestrator._do_sync_per_unit()
        assert cls._bindings(library) == {20: 6020}

    @classmethod
    async def _preview_then_apply(cls, library, emit, monkeypatch):
        """The preview's summary and the ids it classified as skipped, then its apply's outcome.

        The outcome is ``(sent, removals, bindings)`` and the platform
        collections the apply sends. Asserts that the preview took Faves as
        skipped.
        """
        import services.library.sync_preview as preview_module

        fetcher = library.collection_fetcher
        fetch_collection_unit = fetcher.fetch_collection_unit
        collection_skips: list[bool] = []

        async def recording_fetch_collection_unit(*args, **kwargs):
            answer = await fetch_collection_unit(*args, **kwargs)
            collection_skips.append(answer[2])
            return answer

        classify_roms = preview_module.classify_roms
        classified_as_skipped: list[frozenset[int]] = []

        def recording_classify_roms(*args, skipped_rom_ids, **kwargs):
            classified_as_skipped.append(skipped_rom_ids)
            return classify_roms(*args, skipped_rom_ids=skipped_rom_ids, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(fetcher, "fetch_collection_unit", recording_fetch_collection_unit)
            patch.setattr(preview_module, "classify_roms", recording_classify_roms)
            summary = (await library.sync.sync_preview())["summary"]
        assert collection_skips == [True]
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        run_2_starts_at = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        frames = [c.args for c in emit.call_args_list[run_2_starts_at:] if c.args]
        sent = [
            shortcut["rom_id"]
            for name, *payload in frames
            if name == "sync_apply_unit"
            for shortcut in payload[0]["shortcuts"]
        ]
        removed = [rom["rom_id"] for name, *payload in frames if name == "sync_stale" for rom in payload[0]["remove"]]
        (platform_collections,) = [
            set(payload[0]["platform_app_ids"]) for name, *payload in frames if name == "sync_collections"
        ]
        return summary, classified_as_skipped, (sent, removed, cls._bindings(library)), platform_collections

    @staticmethod
    def _counted(summary):
        return summary["new_count"], summary["changed_count"], summary["remove_count"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform_groups", [False, True], ids=["platform-groups-off", "platform-groups-on"])
    async def test_a_hidden_member_beside_a_same_stem_copy_on_its_platform(
        self, make_library, fake_romm_api, emit, monkeypatch, platform_groups
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        library.settings["collection_create_platform_groups"] = platform_groups
        fake_romm_api.hidden_rom_ids = {20}
        fake_romm_api.roms[25] = self._golf(25, "USA", fs_name="Golf (USA).7z")

        summary, classified_as_skipped, applied, platform_collections = await self._preview_then_apply(
            library, emit, monkeypatch
        )

        assert (self._counted(summary), applied) == ((0, 1, 0), ([20], [], {25: 6020}))
        assert classified_as_skipped == [frozenset()]
        assert (summary["platform_collection_diff"]["has_changes"], platform_collections) == (False, {"GBA"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform_groups", [False, True], ids=["platform-groups-off", "platform-groups-on"])
    async def test_a_member_gone_from_romm_whose_dump_returned_on_its_platform_under_a_new_id(
        self, make_library, fake_romm_api, emit, monkeypatch, platform_groups
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        library.settings["collection_create_platform_groups"] = platform_groups
        del fake_romm_api.roms[20]
        fake_romm_api.get_rom_once_side_effect_by_id[20] = RommNotFoundError("Rom with id 20 not found")
        fake_romm_api.roms[24] = self._golf(24, "USA")
        assert fake_romm_api.list_collections()[0]["rom_ids"] == [20]

        summary, classified_as_skipped, applied, platform_collections = await self._preview_then_apply(
            library, emit, monkeypatch
        )

        assert (self._counted(summary), applied) == ((0, 1, 0), ([20], [], {24: 6020}))
        assert classified_as_skipped == [frozenset()]
        assert (summary["platform_collection_diff"]["has_changes"], platform_collections) == (False, {"GBA"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform_groups", [False, True], ids=["platform-groups-off", "platform-groups-on"])
    async def test_a_hidden_member_without_a_same_stem_copy_keeps_its_binding(
        self, make_library, fake_romm_api, emit, monkeypatch, platform_groups
    ):
        """The binding stays, and 20 keeps its place in the preview's union, so both collection diffs are the apply's.

        GBA's fetch no longer returns 20, which Faves' skip rebuilds, so GBA's
        collection files it as a kept row whatever the setting.
        """
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        library.settings["collection_create_platform_groups"] = platform_groups
        fake_romm_api.hidden_rom_ids = {20}

        summary, classified_as_skipped, applied, platform_collections = await self._preview_then_apply(
            library, emit, monkeypatch
        )

        assert (self._counted(summary), applied) == ((0, 0, 0), ([], [], {20: 6020}))
        assert platform_collections == {"GBA"}
        assert summary["platform_collection_diff"] == {"has_changes": False, "added_count": 0, "removed_count": 0}
        assert summary["collection_diff"] == {"has_changes": True, "added": [], "removed": []}
        assert classified_as_skipped == [frozenset({20})]


class TestAGameASkippedCollectionStillListsConvergesAsThePreviewCounts:
    """A version hidden on its synced platform that a skipped collection still lists converges as unlisted (#2645).

    The setup of the class above; then 22 is bound as well, a duplicate from
    before one shortcut per game, and 20 is hidden on GBA while Faves, skipped
    as unchanged, still hands the preview 20's stored row.
    """

    _bind_20 = TestAMemberASkippedCollectionStillListsIsCountedAsItsPlatformUnitRebindsIt._bind_20
    _preview_then_apply = TestAMemberASkippedCollectionStillListsIsCountedAsItsPlatformUnitRebindsIt._preview_then_apply
    _counted = staticmethod(TestAMemberASkippedCollectionStillListsIsCountedAsItsPlatformUnitRebindsIt._counted)

    @staticmethod
    def _bind_22_too(library, *, applied_launch_options=""):
        with library.uow as uow:
            rom = uow.roms.get(22)
            rom.bind_shortcut(6022)
            uow.roms.save(rom)
            uow.roms.set_applied_launch_options(22, applied_launch_options)

    @pytest.mark.asyncio
    async def test_the_listed_version_keeps_the_shortcut_in_the_preview_as_in_the_apply(
        self, make_library, fake_romm_api, emit, monkeypatch
    ):
        # 22 is a prerelease, which the ranking would put behind 20 were 20
        # counted as listed. 22's recorded launch command is stale, so its entry
        # is a change only where 22 is the version that keeps the shortcut.
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        self._bind_22_too(library, applied_launch_options="stale")
        fake_romm_api.roms[22]["tags"] = ["Beta"]
        fake_romm_api.hidden_rom_ids = {20}

        summary, _skipped, applied, _collections = await self._preview_then_apply(library, emit, monkeypatch)

        assert self._counted(summary) == (0, 1, 1)
        assert applied == ([22], [20], {22: 6022})


class TestAHiddenVersionKeepsItsPlatformCollectionWhetherItsCollectionIsSkippedOrFetched:
    """A hidden bound version a collection lists gets the same platform collections either way (#2549 D1).

    GBA is synced and holds Golf (USA), 20, which the first run binds, beside
    Golf (Europe), 22, and Golf (Japan), 23. The user then hides 20 in RomM, so
    GBA's fetch no longer returns it, while Faves still lists it. Faves is
    skipped as unchanged, or fetched because its listing changed for another
    reason. Each case runs the preview, then the apply it would start.
    """

    _bind_20 = TestAMemberASkippedCollectionStillListsIsCountedAsItsPlatformUnitRebindsIt._bind_20

    @staticmethod
    async def _preview_then_apply(library, emit, monkeypatch):
        """``(faves_skips, platform_collection_diff, platform_app_ids)`` over one preview and the apply it starts."""
        fetcher = library.collection_fetcher
        fetch_collection_unit = fetcher.fetch_collection_unit
        faves_skips: list[bool] = []

        async def recording_fetch_collection_unit(*args, **kwargs):
            answer = await fetch_collection_unit(*args, **kwargs)
            faves_skips.append(answer[2])
            return answer

        monkeypatch.setattr(fetcher, "fetch_collection_unit", recording_fetch_collection_unit)
        summary = (await library.sync.sync_preview())["summary"]
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        run_2_starts_at = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        frames = [c.args for c in emit.call_args_list[run_2_starts_at:] if c.args]
        (platform_app_ids,) = [
            payload[0]["platform_app_ids"] for name, *payload in frames if name == "sync_collections"
        ]
        return faves_skips, summary["platform_collection_diff"], platform_app_ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize("faves_skipped", [True, False], ids=["faves-skipped", "faves-fetched"])
    @pytest.mark.parametrize("platform_groups", [False, True], ids=["platform-groups-off", "platform-groups-on"])
    async def test_the_game_stays_in_its_platform_s_collection(
        self, make_library, fake_romm_api, emit, monkeypatch, faves_skipped, platform_groups
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        library.settings["collection_create_platform_groups"] = platform_groups
        fake_romm_api.hidden_rom_ids = {20}
        if not faves_skipped:
            fake_romm_api.collections[0]["updated_at"] = "2030-01-01T00:00:00"

        faves_skips, platform_collection_diff, platform_app_ids = await self._preview_then_apply(
            library, emit, monkeypatch
        )

        assert faves_skips == [faves_skipped, faves_skipped]
        assert platform_collection_diff == {"has_changes": False, "added_count": 0, "removed_count": 0}
        assert platform_app_ids == {"GBA": [6020]}

    @pytest.mark.asyncio
    async def test_a_game_on_a_platform_the_run_does_not_read_stays_out_of_its_collection(
        self, make_library, fake_romm_api, emit, monkeypatch
    ):
        """With GBA off and platform groups off, the game Faves' skip rebuilds is filed under no platform."""
        library = make_library(romm_api=fake_romm_api)
        await TestAGameOnlyACollectionReachesIsCountedAsItsApplyCollapsesIt._bind_20(library, fake_romm_api)
        library.settings["collection_create_platform_groups"] = False

        faves_skips, platform_collection_diff, platform_app_ids = await self._preview_then_apply(
            library, emit, monkeypatch
        )

        assert faves_skips == [True, True]
        assert platform_collection_diff == {"has_changes": False, "added_count": 0, "removed_count": 0}
        assert platform_app_ids == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("faves_skipped", [True, False], ids=["faves-skipped", "faves-fetched"])
    async def test_a_stopped_apply_files_it_by_the_setting_alone(
        self, make_library, fake_romm_api, emit, monkeypatch, faves_skipped
    ):
        """A stopped run keeps no stale row, so with platform groups off it files neither case under GBA."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api)
        library.settings["collection_create_platform_groups"] = False
        fake_romm_api.hidden_rom_ids = {20}
        if not faves_skipped:
            fake_romm_api.collections[0]["updated_at"] = "2030-01-01T00:00:00"
        orchestrator = library.sync._orchestrator
        apply_unit = library.unit_applier.apply_unit
        box = library.sync._box

        async def stop_after_faves(unit, **kwargs):
            processed = await apply_unit(unit, **kwargs)
            if unit.type == "collection":
                box.request_cancel()
            return processed

        monkeypatch.setattr(library.unit_applier, "apply_unit", stop_after_faves)
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        run_2_starts_at = len(emit.call_args_list)
        await orchestrator._do_sync_per_unit()

        frames = [c.args for c in emit.call_args_list[run_2_starts_at:] if c.args]
        (platform_app_ids,) = [
            payload[0]["platform_app_ids"] for name, *payload in frames if name == "sync_collections"
        ]
        assert platform_app_ids == {}


class TestThePreviewNamesAPlatformCollectionAsTheApplyDoes:
    """The preview names each platform collection the way the apply files it (#2558 D1).

    N64's slug, ``n64``, and its name, ``Nintendo 64``, differ. N64 holds Golf
    (USA), 20, beside Golf (Europe), 22, and Golf (Japan), 23; Faves lists 20,
    which the first run binds. Platform groups are on. Each case runs the
    preview, then the apply it would start, and holds the preview's
    platform-collection diff against the platform collections that apply sends
    and the ones the run before it sent.
    """

    @staticmethod
    def _golf(rom_id, region, **extra):
        stem = f"Golf ({region})"
        return {"id": rom_id, "name": stem, "fs_name": f"{stem}.zip", "fs_name_no_ext": stem, "igdb_id": 300, **extra}

    @classmethod
    async def _bind_20(cls, library, fake_romm_api, *, n64_synced):
        _seed_platform(
            fake_romm_api,
            platform_id=4,
            name="Nintendo 64",
            slug="n64",
            roms=[
                cls._golf(20, "USA", rom_user={"is_main_sibling": True}),
                cls._golf(22, "Europe"),
                cls._golf(23, "Japan"),
            ],
        )
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00"
        library.settings["enabled_platforms"] = {"4": n64_synced}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        library.settings["collection_create_platform_groups"] = True
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits
        assert box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
        await library.sync._orchestrator._do_sync_per_unit()
        with library.uow as uow:
            assert uow.roms.get(20).shortcut_app_id == 6020

    @staticmethod
    async def _preview_then_apply(library, emit, monkeypatch):
        """``(faves_skips, platform_collection_diff, the diff the apply made, platform_app_ids)``.

        The diff the apply made compares the platform collections it sends with
        the ones the run before it sent, by the rule the preview counts with.
        """
        fetcher = library.collection_fetcher
        fetch_collection_unit = fetcher.fetch_collection_unit
        faves_skips: list[bool] = []

        async def recording_fetch_collection_unit(*args, **kwargs):
            answer = await fetch_collection_unit(*args, **kwargs)
            faves_skips.append(answer[2])
            return answer

        monkeypatch.setattr(fetcher, "fetch_collection_unit", recording_fetch_collection_unit)
        with library.uow as uow:
            sent_before = set(uow.sync_runs.get_latest_completed().platforms_completed)
        summary = (await library.sync.sync_preview())["summary"]
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        run_2_starts_at = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        frames = [c.args for c in emit.call_args_list[run_2_starts_at:] if c.args]
        (platform_app_ids,) = [
            payload[0]["platform_app_ids"] for name, *payload in frames if name == "sync_collections"
        ]
        added, removed = len(platform_app_ids.keys() - sent_before), len(sent_before - platform_app_ids.keys())
        applied = {"has_changes": bool(added or removed), "added_count": added, "removed_count": removed}
        return faves_skips, summary["platform_collection_diff"], applied, platform_app_ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize("faves_skipped", [True, False], ids=["faves-skipped", "faves-fetched"])
    async def test_a_member_a_collection_rebuilt_is_named_as_the_apply_names_it(
        self, make_library, fake_romm_api, emit, monkeypatch, faves_skipped
    ):
        """N64 is synced and 20 hidden in RomM, so N64's fetch no longer returns it while Faves still lists it."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api, n64_synced=True)
        fake_romm_api.hidden_rom_ids = {20}
        if not faves_skipped:
            fake_romm_api.collections[0]["updated_at"] = "2030-01-01T00:00:00"

        faves_skips, platform_collection_diff, applied, platform_app_ids = await self._preview_then_apply(
            library, emit, monkeypatch
        )

        assert faves_skips == [faves_skipped, faves_skipped]
        assert platform_app_ids == {"Nintendo 64": [6020]}
        assert platform_collection_diff == applied

    @pytest.mark.asyncio
    @pytest.mark.parametrize("faves_skipped", [True, False], ids=["faves-skipped", "faves-fetched"])
    @pytest.mark.parametrize("name_cached", [False, True], ids=["no-name-cached", "name-cached-then-renamed-in-romm"])
    async def test_a_platform_only_collections_reach_is_named_as_the_apply_names_it(
        self, make_library, fake_romm_api, emit, monkeypatch, faves_skipped, name_cached
    ):
        """N64's sync is off for the second run; to cache a name, the first run syncs N64, which RomM renames after."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20(library, fake_romm_api, n64_synced=name_cached)
        library.settings["enabled_platforms"] = {"4": False}
        if name_cached:
            for rom_id in (20, 22, 23):
                fake_romm_api.roms[rom_id]["platform_name"] = "Nintendo 64 (N64)"
        if not faves_skipped:
            fake_romm_api.collections[0]["updated_at"] = "2030-01-01T00:00:00"

        faves_skips, platform_collection_diff, applied, platform_app_ids = await self._preview_then_apply(
            library, emit, monkeypatch
        )

        assert faves_skips == [faves_skipped, faves_skipped]
        assert platform_app_ids == {"Nintendo 64": [6020]}
        assert platform_collection_diff == applied


class TestThePreviewFilesAGameRommDidNotConfirmAsTheApplyDoes:
    """The preview counts the platform collection of a game RomM did not confirm as the apply files it (#2490 D3).

    GBA's and SNES's sync is off. Faves lists A, 20, on GBA and S, 30, on
    SNES, and the first run binds both. Then 20 leaves Faves, so the second
    run asks RomM for it by its exact id: a 404, or an answer that establishes
    nothing, keeps its shortcut. Each case runs the preview, then the apply it
    would start, and holds the preview's platform-collection diff against the
    platform collections that apply sends and the ones the run before it sent.
    """

    @staticmethod
    async def _bind_20_and_30(library, fake_romm_api, *, platform_groups):
        _seed_platform(
            fake_romm_api, platform_id=2, name="Game Boy Advance", slug="gba", roms=[{"id": 20, "name": "A"}]
        )
        _seed_platform(fake_romm_api, platform_id=3, name="Super Nintendo", slug="snes", roms=[{"id": 30, "name": "S"}])
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20, 30])
        fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00"
        library.settings["enabled_platforms"] = {"2": False, "3": False}
        library.settings["enabled_collections"] = {"standard": {"7": True}}
        library.settings["collection_create_platform_groups"] = platform_groups
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits
        assert box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
        await library.sync._orchestrator._do_sync_per_unit()
        with library.uow as uow:
            assert (uow.roms.get(20).shortcut_app_id, uow.roms.get(30).shortcut_app_id) == (6020, 6030)
        faves = fake_romm_api.collections[0]
        faves.update(rom_ids=[30], rom_count=1, updated_at="2030-01-01T00:00:00")
        fake_romm_api.roms[20]["collection_ids"] = []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answer",
        [RommNotFoundError("Rom with id 20 not found"), RommConnectionError("connection refused")],
        ids=["404", "nothing-established"],
    )
    @pytest.mark.parametrize("gba_listed", [True, False], ids=["gba-listed", "gba-no-longer-listed"])
    @pytest.mark.parametrize("platform_groups", [True, False], ids=["platform-groups-on", "platform-groups-off"])
    async def test_the_preview_counts_what_the_apply_writes(
        self, make_library, fake_romm_api, emit, monkeypatch, answer, gba_listed, platform_groups
    ):
        """The preview and the apply agree on the platform collections.

        With platform groups on and GBA no longer listed, its collection is
        named from the name the first run cached.
        """
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_and_30(library, fake_romm_api, platform_groups=platform_groups)
        fake_romm_api.get_rom_once_side_effect_by_id[20] = answer
        if not gba_listed:
            fake_romm_api.platforms = [p for p in fake_romm_api.platforms if p["slug"] != "gba"]

        (
            _faves_skips,
            platform_collection_diff,
            applied,
            platform_app_ids,
        ) = await TestThePreviewNamesAPlatformCollectionAsTheApplyDoes._preview_then_apply(library, emit, monkeypatch)

        if platform_groups:
            assert platform_app_ids == {"Game Boy Advance": [6020], "Super Nintendo": [6030]}
        else:
            assert platform_app_ids == {}
        assert platform_collection_diff == {"has_changes": False, "added_count": 0, "removed_count": 0}
        assert platform_collection_diff == applied
        with library.uow as uow:
            assert uow.roms.get(20).shortcut_app_id == 6020


class TestThePreviewFilesAGameTheRunSyncedAsTheApplyDoes:
    """The preview counts the platform collection of a row whose game the run synced as the apply files it (#2490 D3).

    GBA's sync is off. Faves holds Golf (USA), 20, which the first run binds;
    a version switch then moves that shortcut onto Golf (Europe), 22, which no
    collection lists. The second run delivers 20 through Faves and leaves 22
    bound, since its game was synced, whether or not RomM is asked about 22.
    No entry the preview emits is on GBA, so 22 is the only row that keeps
    GBA's collection. Each case runs the preview, then the apply it would start.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("faves_fetched", [False, True], ids=["faves-skipped", "faves-fetched"])
    @pytest.mark.parametrize("route", [None, "collection"], ids=["not-asked", "asked"])
    @pytest.mark.parametrize("platform_groups", [True, False], ids=["platform-groups-on", "platform-groups-off"])
    async def test_the_preview_counts_what_the_apply_writes(
        self, make_library, fake_romm_api, emit, monkeypatch, faves_fetched, route, platform_groups
    ):
        """A route of ``collection`` on 22 has RomM asked about it; without one it is not asked."""
        switched = TestAVersionSwitchedOffTheCollectionsMember
        library = make_library(romm_api=fake_romm_api)
        library.settings["collection_create_platform_groups"] = platform_groups
        await switched._bind_20_then_switch_to_22(library, fake_romm_api)
        if route is not None:
            switched._record_on_22(library, route=route)
        if faves_fetched:
            switched._fetch_faves_again(fake_romm_api)
        fake_romm_api.call_log.clear()

        (
            faves_skips,
            platform_collection_diff,
            applied,
            platform_app_ids,
        ) = await TestThePreviewNamesAPlatformCollectionAsTheApplyDoes._preview_then_apply(library, emit, monkeypatch)

        assert faves_skips == [not faves_fetched, not faves_fetched]
        asked = [args for name, args, _kwargs in fake_romm_api.call_log if name == "get_rom_once"]
        assert ((22,) in asked) is (route is not None)
        assert platform_app_ids == ({"GBA": [6020]} if platform_groups else {})
        assert platform_collection_diff == {"has_changes": False, "added_count": 0, "removed_count": 0}
        assert platform_collection_diff == applied
        with library.uow as uow:
            assert uow.roms.get(22).shortcut_app_id == 6020


class TestThePerPlatformTableLabelsAPlatformAsTheApplyNamesIt:
    """The preview's per-platform table labels a platform with the name the apply files it under (#2564).

    N64's slug, ``n64``, and its listed name, ``Nintendo 64``, differ, and its
    ROMs carry ``N64 Custom``, the custom name set on it in RomM. The first run
    binds Golf, 20, through Faves and Pong, 30, through Old, syncing N64 too
    where the case says so, and caches N64's listed name either way. The second
    run syncs neither N64 nor Old, so 30 is removed: N64's row in the table
    carries that removal, and the apply still files 20 under N64 through Faves.
    """

    @staticmethod
    async def _bind_20_and_30(library, fake_romm_api, *, n64_synced):
        golf = TestThePreviewNamesAPlatformCollectionAsTheApplyDoes._golf
        _seed_platform(
            fake_romm_api,
            platform_id=4,
            name="Nintendo 64",
            slug="n64",
            roms=[
                golf(20, "USA", rom_user={"is_main_sibling": True}),
                golf(22, "Europe"),
                {"id": 30, "name": "Pong", "fs_name": "Pong.zip", "fs_name_no_ext": "Pong", "igdb_id": 301},
            ],
        )
        for rom in fake_romm_api.roms.values():
            rom["platform_name"] = "N64 Custom"
        _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=[20])
        _seed_collection(fake_romm_api, collection_id=8, name="Old", rom_ids=[30])
        for collection in fake_romm_api.collections:
            collection["updated_at"] = "2025-01-01T00:00:00"
        library.settings["enabled_platforms"] = {"4": n64_synced}
        library.settings["enabled_collections"] = {"standard": {"7": True, "8": True}}
        library.settings["collection_create_platform_groups"] = True
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits
        assert box.try_begin_run("run-1", kind=SyncRunKind.APPLY) is True
        await library.sync._orchestrator._do_sync_per_unit()
        with library.uow as uow:
            assert (uow.roms.get(20).shortcut_app_id, uow.roms.get(30).shortcut_app_id) == (6020, 6030)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("faves_skipped", [True, False], ids=["faves-skipped", "faves-fetched"])
    @pytest.mark.parametrize("n64_synced_first", [False, True], ids=["n64-off-in-run-1", "n64-synced-in-run-1"])
    async def test_a_platform_not_synced_is_labelled_as_the_apply_names_it(
        self, make_library, fake_romm_api, emit, faves_skipped, n64_synced_first
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_and_30(library, fake_romm_api, n64_synced=n64_synced_first)
        library.settings["enabled_platforms"] = {"4": False}
        library.settings["enabled_collections"] = {"standard": {"7": True, "8": False}}
        if not faves_skipped:
            fake_romm_api.collections[0]["updated_at"] = "2030-01-01T00:00:00"

        summary = (await library.sync.sync_preview())["summary"]
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        run_2_starts_at = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        frames = [c.args for c in emit.call_args_list[run_2_starts_at:] if c.args]
        (platform_app_ids,) = [
            payload[0]["platform_app_ids"] for name, *payload in frames if name == "sync_collections"
        ]

        assert platform_app_ids == {"Nintendo 64": [6020]}
        assert summary["platform_breakdown"] == [
            {
                "slug": "n64",
                "name": "Nintendo 64",
                "synced": False,
                "new_count": 0,
                "changed_count": 0,
                "remove_count": 1,
            }
        ]

    @pytest.mark.asyncio
    async def test_a_platform_romm_no_longer_lists_is_labelled_with_its_cached_name(self, make_library, fake_romm_api):
        """RomM stops listing N64 and both collections are off: its answer removes 20 and 30, and nothing names N64."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind_20_and_30(library, fake_romm_api, n64_synced=False)
        with library.uow as uow:
            assert json.loads(uow.kv_config.get("platform_names")) == {"n64": "Nintendo 64"}
        fake_romm_api.platforms = []
        library.settings["enabled_collections"] = {"standard": {"7": False, "8": False}}

        summary = (await library.sync.sync_preview())["summary"]

        assert summary["platform_breakdown"] == [
            {
                "slug": "n64",
                "name": "Nintendo 64",
                "synced": False,
                "new_count": 0,
                "changed_count": 0,
                "remove_count": 2,
            }
        ]


class TestRegression738CacheCorruption:
    """Regression for #738 — delta sync must not erase populated metadata.

    Before the fix, the per-unit incremental-skip path produced thin
    registry-reconstructed ROMs (no ``metadatum`` field). Those flowed
    through the metadata stamp and overwrote populated entries with empty
    ones. Symptom: 160 populated entries → 62 after one delta sync.
    Post-cutover the equivalent guard lives in the reporter's per-unit
    commit — a skipped unit never reaches it, so its ``rom_metadata`` rows
    survive untouched.
    """

    @pytest.mark.asyncio
    async def test_delta_sync_preserves_populated_metadata(self, make_library, fake_romm_api):
        """Populated ``rom_metadata`` rows survive a per-unit delta sync of unchanged platforms.

        Scenario: ``uow`` has 3 ROMs on platform N64 with populated
        metadata. Server reports zero updated after ``last_sync``, so
        ``fetch_platform_unit`` returns skipped=True. The orchestrator's
        skip-guard short-circuits before the per-unit commit, so the
        populated metadata rows are preserved untouched.
        """
        from domain.rom_metadata import RomMetadata

        library = make_library(romm_api=fake_romm_api)

        # Pre-existing populated metadata rows (the "160 entries" scenario
        # boiled down to 3 ROMs), each backed by a bound Rom row (FK parent).
        seeds = {
            1: RomMetadata(
                summary="Game 1 description",
                genres=("RPG",),
                companies=("Square",),
                first_release_date=946684800,
                average_rating=95.0,
                game_modes=("Single player",),
                player_count="1",
                cached_at=100.0,
                steam_categories=(2, 21),
            ),
            2: RomMetadata(
                summary="Game 2 description",
                genres=("Action",),
                companies=("Capcom",),
                first_release_date=1000000000,
                average_rating=88.0,
                game_modes=("Multiplayer",),
                player_count="1-4",
                cached_at=100.0,
                steam_categories=(1, 21),
            ),
            3: RomMetadata(
                summary="Game 3 description",
                genres=("Puzzle",),
                companies=("Nintendo",),
                first_release_date=1100000000,
                average_rating=92.0,
                game_modes=("Single player",),
                player_count="1",
                cached_at=100.0,
                steam_categories=(4,),
            ),
        }
        for rid, meta in seeds.items():
            _seed_rom_row(
                library, rid, app_id=1000 + rid, platform_slug="n64", name=f"Game {rid}", fs_name=f"g{rid}.z64"
            )
            with library.uow as uow:
                uow.rom_metadata.save(rid, meta)

        # A prior completed run + matching roms count drive the incremental skip.
        _seed_completed_run(library, at="2025-01-01T00:00:00Z")
        # Server reports the platform exists with 3 ROMs and ZERO updates.
        # No ROMs seeded on the fake → the probe answers no ROM and no id.
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 3}]
        library.settings["enabled_platforms"] = {"1": True}

        library.cover_preparer._download_artwork = AsyncMock(return_value={})

        async def fake_wait(_u, event):
            event.set()
            return {"1": 1001, "2": 1002, "3": 1003}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        # Post-flight: the 3 populated metadata rows MUST survive untouched.
        # Pre-fix, they would have been overwritten by empty ones.
        with library.uow as uow:
            for rid, meta in seeds.items():
                assert uow.rom_metadata.get(rid) == meta


class TestFetchNarrationInterplay:
    """Fetch-phase narration meets the chunk-apply phase (#1025).

    The per-unit prep (anchor + paginated fetch + cover download) narrates under
    the ``fetching`` stage; the chunk loop then hands progress to the frontend
    under ``applying``. The backend must not emit a ``fetching`` frame once a
    unit's chunks start, or the coarse label would flip back and forth.
    """

    @pytest.mark.asyncio
    async def test_unit_anchor_is_fetching_stage(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        progress_frames = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_progress"]
        # The unit's first progress frame anchors the coarse bar under FETCHING,
        # not the old APPLYING that read as a frozen "Applying shortcuts".
        first = progress_frames[0]
        assert first["stage"] == "fetching"
        assert first["message"] == "Fetching N64"
        assert first["step"] == 1
        assert first["totalSteps"] == 1

    @pytest.mark.asyncio
    async def test_no_fetching_frame_after_first_chunk_emit(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "A"}, {"id": 11, "name": "B"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        first_chunk_idx = next(i for i, c in enumerate(emit.call_args_list) if c[0][0] == "sync_apply_unit")
        fetching_after_chunk = [
            i
            for i, c in enumerate(emit.call_args_list)
            if c[0][0] == "sync_progress" and c[0][1].get("stage") == "fetching" and i > first_chunk_idx
        ]
        assert fetching_after_chunk == []
        # And the fetch actually narrated before the chunk (guards against a
        # vacuous pass where no fetching frame was ever emitted).
        fetching_before_chunk = [
            i
            for i, c in enumerate(emit.call_args_list)
            if c[0][0] == "sync_progress" and c[0][1].get("stage") == "fetching" and i < first_chunk_idx
        ]
        assert fetching_before_chunk


class TestComponentGroupKeyStamping:
    """The per-unit / preview stamp of component sibling-group keys (#1368).

    ``stamp_component_group_keys`` delegates to the pure kernel and writes the
    result back onto the raw ROM dicts before ``build_shortcuts_data`` so the whole
    sync pipeline (shortcut build, group collapse, commit) reads the component key
    rather than a per-ROM coalesce-first key.
    """

    @pytest.mark.asyncio
    async def test_platform_unit_stamps_shared_component_key(self, make_library, fake_romm_api):
        # Two dumps of one game with UNEVEN coverage — rom 10 igdb+ss, rom 11 ss
        # only, RomM listing them as siblings. Both must persist under the SAME
        # component key (igdb), so the ss-only dump does not split into its own
        # group (the #1368 bug). A platform unit is a complete view → recompute.
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 10, "name": "Game (USA)", "igdb_id": 1001, "ss_id": 2002, "sibling_roms": [{"id": 11}]},
                {"id": 11, "name": "Game (EU)", "ss_id": 2002, "sibling_roms": [{"id": 10}]},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow:
            assert library.uow.roms.get(10).sibling_group_key == "igdb:1001:1"
            assert library.uow.roms.get(11).sibling_group_key == "igdb:1001:1"

    def test_stamp_preserves_resident_and_keys_fresh_against_resident_summaries(self, library):
        # An in-unit resident (already keyed) is left untouched; a fresh member
        # edging into it adopts its canonical summary; a fresh member edging into a
        # DB-resident (fed via resident_keys) adopts THAT summary. The stamp mutates
        # the raw dicts in place.
        resident = {"id": 1, "platform_id": 57, "sibling_group_key": "igdb:100:57", "sibling_roms": [{"id": 2}]}
        fresh_in_unit = {"id": 2, "platform_id": 57, "ss_id": 22, "sibling_roms": [{"id": 1}]}
        fresh_vs_db = {"id": 3, "platform_id": 57, "moby_id": 9, "sibling_roms": [{"id": 99}]}
        roms = [resident, fresh_in_unit, fresh_vs_db]

        stamp_component_group_keys(roms, {99: "igdb:777:57"})

        assert resident["sibling_group_key"] == "igdb:100:57"
        assert fresh_in_unit["sibling_group_key"] == "igdb:100:57"
        assert fresh_vs_db["sibling_group_key"] == "igdb:777:57"


class TestAPlatformUnitIsKeyedAsThePreviewKeysIt:
    """A platform unit's ROMs are keyed against the resident keys the preview reads (#2617 D1, D2, D3).

    Golf's USA version (30) is bound; its European version (31) is an unbound
    sibling holding the group's key. RomM then hides 31, which leaves it out of
    the platform listing but not out of its siblings' ``sibling_roms``, and adds
    a Japanese version (32) whose only sibling edge goes to 31.
    """

    @staticmethod
    def _golf(rom_id, region, sibling_ids, **ids):
        stem = f"Golf ({region})"
        return {
            "id": rom_id,
            "name": stem,
            "fs_name": f"{stem}.zip",
            "fs_name_no_ext": stem,
            "sibling_roms": [{"id": sibling} for sibling in sibling_ids],
            **ids,
        }

    @classmethod
    async def _bind_usa_then_hide_europe(cls, library, fake_romm_api, emit):
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[
                cls._golf(30, "USA", [31], igdb_id=300, rom_user={"is_main_sibling": True}),
                cls._golf(31, "Europe", [30], igdb_id=300, ss_id=7),
            ],
        )
        library.settings["enabled_platforms"] = {"2": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(_unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits
        await cls._apply(library, emit, "run-1")
        assert cls._rows(library) == {30: (6030, "igdb:300:2"), 31: (None, "igdb:300:2")}

        fake_romm_api.hidden_rom_ids = {31}
        fake_romm_api.roms[31]["sibling_roms"] = [{"id": 30}, {"id": 32}]
        fake_romm_api.roms[32] = {
            **cls._golf(32, "Japan", [31], ss_id=7),
            "platform_id": 2,
            "platform_name": "GBA",
            "platform_slug": "gba",
        }

    @staticmethod
    async def _apply(library, emit, run_id):
        """Run an apply; answer the rom ids it sent and the ones its stale removal removed."""
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
        start = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        frames = [call.args for call in emit.call_args_list[start:] if call.args]
        sent = [
            shortcut["rom_id"]
            for name, *payload in frames
            if name == "sync_apply_unit"
            for shortcut in payload[0]["shortcuts"]
        ]
        removed = [row["rom_id"] for name, *payload in frames if name == "sync_stale" for row in payload[0]["remove"]]
        return sent, removed

    @staticmethod
    def _rows(library):
        with library.uow as uow:
            return {rom.rom_id: (rom.shortcut_app_id, rom.sibling_group_key) for rom in uow.roms.iter_all()}

    @pytest.mark.asyncio
    async def test_a_new_version_whose_only_sibling_is_hidden_joins_its_game_as_in_the_preview(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind_usa_then_hide_europe(library, fake_romm_api, emit)

        summary = (await library.sync.sync_preview())["summary"]
        sent, removed = await self._apply(library, emit, "run-2")

        assert (summary["new_count"], summary["changed_count"], summary["remove_count"]) == (0, 0, 0)
        assert (sent, removed) == ([], [])
        assert self._rows(library) == {
            30: (6030, "igdb:300:2"),
            31: (None, "igdb:300:2"),
            32: (None, "igdb:300:2"),
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("installed", [(), (32,), (30, 32)], ids=["none", "the-duplicate", "both"])
    async def test_a_duplicate_made_before_takes_its_game_s_key_and_converges(
        self, make_library, fake_romm_api, emit, tmp_path, installed
    ):
        """D2: 32 is bound under a key of its own, a duplicate an apply without this fix made.

        It takes the group's key and the group converges (#2645): the chain's pick keeps its shortcut, the
        other is removed and unbound, and the install records stay.
        """
        library = make_library(romm_api=fake_romm_api)
        await self._bind_usa_then_hide_europe(library, fake_romm_api, emit)
        _seed_rom_row(library, 32, app_id=6032, platform_slug="gba", name="Golf (Japan)", sibling_group_key="ss:7:2")
        for rom_id in installed:
            path = tmp_path / f"golf-{rom_id}.zip"
            path.write_bytes(b"rom")
            with library.uow:
                library.uow.rom_installs.save(
                    RomInstall.mark_installed(
                        rom_id=rom_id,
                        file_path=str(path),
                        rom_dir=None,
                        platform_slug="gba",
                        system="gba",
                        installed_at="2025-01-01T00:00:00",
                    )
                )

        _sent, removed = await self._apply(library, emit, "run-2")

        # With nothing installed the chain picks 30, RomM's default; an installed
        # version beats it, and of the two installed ones the ranking puts
        # "Golf (Japan)" first, since the regions are not sent here.
        kept, gone = (32, 30) if 32 in installed else (30, 32)
        assert removed == [gone]
        assert self._rows(library) == {
            kept: (6000 + kept, "igdb:300:2"),
            31: (None, "igdb:300:2"),
            gone: (None, "igdb:300:2"),
        }
        with library.uow as uow:
            installs = {rom_id: uow.rom_installs.get(rom_id) for rom_id in installed}
        assert {rom_id: install and install.file_path for rom_id, install in installs.items()} == {
            rom_id: str(tmp_path / f"golf-{rom_id}.zip") for rom_id in installed
        }


class TestAGameKeepsItsKeyWhenTheVersionCarryingItIsNotListed:
    """A game keeps its one shortcut when the version that carried its key is deleted or hidden in RomM (#2646).

    GBA holds Golf. The first run binds RomM's default version, 30, and keys
    every version by the id the game agrees on. Each case then deletes or hides
    a version in RomM, or changes an id, and runs a preview and the apply it
    would start, twice where the answer must hold on the next sync.
    """

    _apply = staticmethod(TestAPlatformUnitIsKeyedAsThePreviewKeysIt._apply)
    _rows = staticmethod(TestAPlatformUnitIsKeyedAsThePreviewKeysIt._rows)

    @staticmethod
    def _golf(rom_id, region, sibling_ids, **ids):
        stem = f"Golf ({region})"
        return {
            "id": rom_id,
            "name": stem,
            "fs_name": f"{stem}.zip",
            "fs_name_no_ext": stem,
            "updated_at": "2025-01-01T00:00:00+00:00",
            "sibling_roms": [{"id": sibling} for sibling in sibling_ids],
            **ids,
        }

    @classmethod
    async def _bind(cls, library, fake_romm_api, emit, tmp_path, roms, *, installed=(), faves=()):
        """Run the first apply over *roms*, the first RomM's default; install *installed*; *faves* in a collection."""
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=roms)
        library.settings["enabled_platforms"] = {"2": True}
        if faves:
            _seed_collection(fake_romm_api, collection_id=7, name="Faves", rom_ids=list(faves))
            fake_romm_api.collections[0]["updated_at"] = "2025-01-01T00:00:00"
            library.settings["enabled_collections"] = {"standard": {"7": True}}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        cls._bind_what_the_unit_emits(library)
        await cls._apply(library, emit, "run-1")
        for rom_id in installed:
            path = tmp_path / f"golf-{rom_id}.zip"
            path.write_bytes(b"rom")
            with library.uow:
                library.uow.rom_installs.save(
                    RomInstall.mark_installed(
                        rom_id=rom_id,
                        file_path=str(path),
                        rom_dir=None,
                        platform_slug="gba",
                        system="gba",
                        installed_at="2025-01-01T00:00:00",
                    )
                )

    @staticmethod
    def _bind_what_the_unit_emits(library):
        box = library.sync._box

        async def bind(_unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind

    @classmethod
    async def _sync(cls, library, emit, run_id):
        """A preview's ``(new, changed, remove)``, then its apply's ``(sent, removed)`` and the rows it leaves."""
        summary = (await library.sync.sync_preview())["summary"]
        sent, removed = await cls._apply(library, emit, run_id)
        counts = (summary["new_count"], summary["changed_count"], summary["remove_count"])
        return counts, (sent, removed), cls._rows(library)

    @staticmethod
    def _p1_roms(golf):
        return [
            golf(30, "USA", [31], igdb_id=300, ss_id=7, rom_user={"is_main_sibling": True}),
            golf(31, "Europe", [30], ss_id=7),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("installed", [(), (30,), (31,)], ids=["none", "deleted-installed", "survivor-installed"])
    async def test_the_deleted_version_follows_its_game_and_keeps_the_one_shortcut(
        self, make_library, fake_romm_api, emit, tmp_path, installed
    ):
        """D1: 30 alone carried the IGDB id and is deleted; 31 is keyed by its own ss id, and 30 follows it."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind(library, fake_romm_api, emit, tmp_path, self._p1_roms(self._golf), installed=installed)
        del fake_romm_api.roms[30]
        fake_romm_api.roms[31]["sibling_roms"] = []

        for run_id in ("run-2", "run-3"):
            assert await self._sync(library, emit, run_id) == (
                (0, 0, 0),
                ([], []),
                {30: (6030, "ss:7:2"), 31: (None, "ss:7:2")},
            )

    @pytest.mark.asyncio
    async def test_a_hidden_version_its_game_still_names_keeps_the_game_s_key(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind(library, fake_romm_api, emit, tmp_path, self._p1_roms(self._golf))
        fake_romm_api.hidden_rom_ids = {30}

        assert await self._sync(library, emit, "run-2") == (
            (0, 0, 0),
            ([], []),
            {30: (6030, "igdb:300:2"), 31: (None, "igdb:300:2")},
        )

    @staticmethod
    def _p5_roms(golf):
        return [
            golf(30, "USA", [31], ss_id=7, rom_user={"is_main_sibling": True}),
            golf(31, "Europe", [30], ss_id=7),
        ]

    @staticmethod
    def _hide_30_and_match_31(fake_romm_api):
        fake_romm_api.hidden_rom_ids = {30}
        fake_romm_api.roms[31].update(igdb_id=300, updated_at="2090-02-01T00:00:00+00:00")

    @pytest.mark.asyncio
    async def test_a_hidden_bound_version_takes_the_key_its_game_gains(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """30 is hidden and bound, 31 gains an IGDB id: 31's edge to 30 keys 30 with it (D1)."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind(library, fake_romm_api, emit, tmp_path, self._p5_roms(self._golf))
        self._hide_30_and_match_31(fake_romm_api)
        rows = {30: (6030, "igdb:300:2"), 31: (None, "igdb:300:2")}

        for run_id in ("run-2", "run-3"):
            assert await self._sync(library, emit, run_id) == ((0, 0, 0), ([], []), rows)
        fake_romm_api.hidden_rom_ids = set()
        fake_romm_api.roms[30]["updated_at"] = "2090-03-01T00:00:00+00:00"
        counts, (sent, removed), after = await self._sync(library, emit, "run-4")
        assert (counts[0], counts[2], 31 in sent, removed, after) == (0, 0, False, [], rows)
        assert counts[1] == len(sent)

    @staticmethod
    def _p4_roms(golf):
        return [
            golf(30, "USA", [31, 32], igdb_id=1, ss_id=5, rom_user={"is_main_sibling": True}),
            golf(31, "Europe", [30, 32], ss_id=5),
            golf(32, "Japan", [30, 31], ss_id=5),
        ]

    @staticmethod
    def _hide_32_and_rematch_30(fake_romm_api):
        fake_romm_api.hidden_rom_ids = {32}
        fake_romm_api.roms[30].update(igdb_id=2, updated_at="2090-02-01T00:00:00+00:00")

    @pytest.mark.asyncio
    async def test_a_hidden_version_s_stale_summary_does_not_split_its_game(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """D3: 32 is hidden and still holds igdb:1, the summary 30 and 31 held; RomM re-matched 30 to igdb 2."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind(library, fake_romm_api, emit, tmp_path, self._p4_roms(self._golf))
        self._hide_32_and_rematch_30(fake_romm_api)
        rows = {30: (6030, "igdb:2:2"), 31: (None, "igdb:2:2"), 32: (None, "igdb:2:2")}

        for run_id in ("run-2", "run-3"):
            assert await self._sync(library, emit, run_id) == ((0, 0, 0), ([], []), rows)
        fake_romm_api.hidden_rom_ids = set()
        fake_romm_api.roms[32]["updated_at"] = "2090-03-01T00:00:00+00:00"
        assert await self._sync(library, emit, "run-4") == ((0, 0, 0), ([], []), rows)

    @pytest.mark.asyncio
    async def test_a_version_separated_from_its_listed_game_gets_a_shortcut_of_its_own(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind(library, fake_romm_api, emit, tmp_path, self._p1_roms(self._golf))
        fake_romm_api.roms[30].update(ss_id=9, sibling_roms=[], updated_at="2090-02-01T00:00:00+00:00")
        fake_romm_api.roms[31]["sibling_roms"] = []

        assert await self._sync(library, emit, "run-2") == (
            (1, 0, 0),
            ([31], []),
            {30: (6030, "igdb:300:2"), 31: (6031, "ss:7:2")},
        )

    @pytest.mark.asyncio
    async def test_a_version_moved_into_another_game_leaves_the_deleted_version_its_key(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """31 held 30's key and joins 35's game, which held another: 30 follows neither."""
        library = make_library(romm_api=fake_romm_api)
        roms = [*self._p1_roms(self._golf), self._golf(35, "Japan", [], ss_id=8)]
        await self._bind(library, fake_romm_api, emit, tmp_path, roms)
        del fake_romm_api.roms[30]
        fake_romm_api.roms[31].update(ss_id=8, sibling_roms=[{"id": 35}], updated_at="2090-02-01T00:00:00+00:00")
        fake_romm_api.roms[35]["sibling_roms"] = [{"id": 31}]

        assert await self._sync(library, emit, "run-2") == (
            (0, 0, 0),
            ([], []),
            {30: (6030, "igdb:300:2"), 31: (None, "ss:8:2"), 35: (6035, "ss:8:2")},
        )

    @pytest.mark.asyncio
    async def test_a_game_split_in_two_leaves_the_deleted_version_its_key(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        roms = [
            self._golf(30, "USA", [31, 32], igdb_id=300, rom_user={"is_main_sibling": True}),
            self._golf(31, "Europe", [30, 32], ss_id=5),
            self._golf(32, "Japan", [30, 31], moby_id=4),
        ]
        await self._bind(library, fake_romm_api, emit, tmp_path, roms)
        del fake_romm_api.roms[30]
        fake_romm_api.roms[31]["sibling_roms"] = []
        fake_romm_api.roms[32]["sibling_roms"] = []

        assert await self._sync(library, emit, "run-2") == (
            (2, 0, 0),
            ([31, 32], []),
            {30: (6030, "igdb:300:2"), 31: (6031, "ss:5:2"), 32: (6032, "moby:4:2")},
        )

    @pytest.mark.asyncio
    async def test_a_duplicate_a_hidden_version_left_behind_converges(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """31 was minted beside hidden 30 by a sync without the rule; 30 now takes 31's key, and the two converge."""
        library = make_library(romm_api=fake_romm_api)
        await self._bind(library, fake_romm_api, emit, tmp_path, self._p5_roms(self._golf))
        self._hide_30_and_match_31(fake_romm_api)
        _seed_rom_row(
            library,
            31,
            app_id=6031,
            platform_slug="gba",
            name="Golf (Europe)",
            fs_name="Golf (Europe).zip",
            sibling_group_key="igdb:300:2",
        )

        assert await self._sync(library, emit, "run-2") == (
            (0, 0, 1),
            ([], [30]),
            {30: (None, "igdb:300:2"), 31: (6031, "igdb:300:2")},
        )
        assert await self._sync(library, emit, "run-3") == (
            (0, 0, 0),
            ([], []),
            {30: (None, "igdb:300:2"), 31: (6031, "igdb:300:2")},
        )

    @pytest.mark.asyncio
    async def test_a_late_ack_of_a_timed_out_first_chunk_writes_the_followed_key(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._bind(library, fake_romm_api, emit, tmp_path, self._p1_roms(self._golf))
        del fake_romm_api.roms[30]
        fake_romm_api.roms[31]["sibling_roms"] = []
        dispatcher = library.chunk_dispatcher
        del dispatcher._wait_for_unit_complete
        dispatcher._sleeper = _ClockAdvancingSleeper(dispatcher._clock, 999.0)
        box = library.sync._box
        assert box.try_begin_run("run-2", kind=SyncRunKind.APPLY) is True
        box.sync_last_heartbeat = library.sync._orchestrator._clock.monotonic()
        await library.sync._orchestrator._do_sync_per_unit()
        assert box.abandoned_chunk is not None
        assert self._rows(library) == {30: (6030, "igdb:300:2"), 31: (None, "igdb:300:2")}
        (frame,) = [call.args[1] for call in emit.call_args_list if call.args[0] == "sync_apply_unit"][-1:]

        await library.sync.report_unit_results({}, frame["run_id"], frame["unit_id"], frame["chunk_index"])

        assert self._rows(library) == {30: (6030, "ss:7:2"), 31: (None, "ss:7:2")}
        self._bind_what_the_unit_emits(library)
        assert await self._sync(library, emit, "run-3") == (
            (0, 0, 0),
            ([], []),
            {30: (6030, "ss:7:2"), 31: (None, "ss:7:2")},
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", ["hidden-bound", "stale-summary"])
    async def test_a_hidden_version_a_collection_still_lists_is_counted_as_the_apply_keys_it(
        self, make_library, fake_romm_api, emit, tmp_path, case
    ):
        """A hidden version stays in a standard collection's listing, so the preview's union may hold it.

        Faves is skipped as unchanged, and its skip hands the preview the stored
        row of a bound member: hidden-bound's 30. GBA's unit keys 30 as a stored
        row and commits before Faves rebuilds it; the preview keys it the same
        way, so it counts what the apply sends. A skip rebuilds no unbound row,
        so stale-summary's hidden 32 never reaches the union and that case
        holds the platform path's parity only.
        """
        library = make_library(romm_api=fake_romm_api)
        if case == "hidden-bound":
            roms, hidden, change = self._p5_roms(self._golf), 30, self._hide_30_and_match_31
            key = "igdb:300:2"
        else:
            roms, hidden, change = self._p4_roms(self._golf), 32, self._hide_32_and_rematch_30
            key = "igdb:2:2"
        await self._bind(library, fake_romm_api, emit, tmp_path, roms, faves=[hidden])
        change(fake_romm_api)

        for run_id in ("run-2", "run-3"):
            (new, changed, remove), (sent, removed), rows = await self._sync(library, emit, run_id)
            assert (new, changed + new, remove) == (0, len(sent), len(removed))
            assert (removed, {rom_id: row[0] for rom_id, row in rows.items()}) == (
                [],
                {rom["id"]: 6030 if rom["id"] == 30 else None for rom in roms},
            )
            assert {row[1] for row in rows.values()} == {key}


class TestAGameWithSeveralShortcutsConverges:
    """A sibling group holding more than one bound shortcut keeps one (#2645 D1-D3).

    GBA lists Golf (USA) as 30, RomM's default, and Golf (Europe) as 31. Run 1
    binds 30; a library from before one shortcut per game bound 31 as well.
    """

    @staticmethod
    def _golf(rom_id, region, sibling_ids, **ids):
        stem = f"Golf ({region})"
        return {
            "id": rom_id,
            "name": stem,
            "fs_name": f"{stem}.zip",
            "fs_name_no_ext": stem,
            "regions": [region],
            "updated_at": "2025-01-01T00:00:00+00:00",
            "sibling_roms": [{"id": sibling} for sibling in sibling_ids],
            **ids,
        }

    @staticmethod
    async def _apply(library, emit, run_id, *, cancel=False):
        """Run an apply; answer the rom ids it sent and its ``sync_stale`` entries."""
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
        if cancel:

            async def cancel_inside_the_unit(_unit, _event):
                box.sync_state = SyncState.CANCELLING

            library.chunk_dispatcher._wait_for_unit_complete = cancel_inside_the_unit
        start = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        frames = [call.args for call in emit.call_args_list[start:] if call.args]
        sent = [
            shortcut["rom_id"]
            for name, *payload in frames
            if name == "sync_apply_unit"
            for shortcut in payload[0]["shortcuts"]
        ]
        removed = [row for name, *payload in frames if name == "sync_stale" for row in payload[0]["remove"]]
        return sent, removed

    @staticmethod
    def _bindings(library):
        with library.uow as uow:
            return {rom.rom_id: rom.shortcut_app_id for rom in uow.roms.iter_all()}

    @classmethod
    async def _two_bound(cls, library, fake_romm_api, emit, tmp_path, *, installed=()):
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[
                cls._golf(30, "USA", [31], igdb_id=300, rom_user={"is_main_sibling": True}),
                cls._golf(31, "Europe", [30], igdb_id=300),
            ],
        )
        library.settings["enabled_platforms"] = {"2": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(_unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits
        await cls._apply(library, emit, "run-1")
        with library.uow as uow:
            rom = uow.roms.get(31)
            rom.bind_shortcut(6031)
            uow.roms.save(rom)
            uow.roms.set_applied_launch_options(31, "")
            for rom_id in (30, 31):
                uow.playtime.save(rom_id, Playtime(total_seconds=100 * rom_id, session_count=rom_id))
                uow.rom_save_sync_states.save(
                    rom_id,
                    RomSaveSyncState(system="gba", files={f"golf-{rom_id}.srm": FileSyncState(last_sync_hash="h")}),
                )
        for rom_id in installed:
            path = tmp_path / f"golf-{rom_id}.zip"
            path.write_bytes(b"rom")
            with library.uow:
                library.uow.rom_installs.save(
                    RomInstall.mark_installed(
                        rom_id=rom_id,
                        file_path=str(path),
                        rom_dir=None,
                        platform_slug="gba",
                        system="gba",
                        installed_at="2025-01-01T00:00:00",
                    )
                )
        return bind_what_the_unit_emits

    @staticmethod
    def _per_rom_data(library):
        with library.uow as uow:
            return {
                rom_id: (
                    uow.playtime.get(rom_id),
                    uow.rom_save_sync_states.get(rom_id),
                    install.file_path if (install := uow.rom_installs.get(rom_id)) else None,
                )
                for rom_id in (30, 31)
            }

    @staticmethod
    def _list_calls(fake_romm_api):
        return [args for name, args, _kwargs in fake_romm_api.call_log if name == "list_roms"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("installed", "kept"), [((), 30), ((30,), 30), ((31,), 31), ((30, 31), 30)], ids=["none", "30", "31", "both"]
    )
    async def test_the_chain_s_pick_keeps_its_shortcut_as_the_preview_counted(
        self, make_library, fake_romm_api, emit, tmp_path, installed, kept
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path, installed=installed)
        gone = 31 if kept == 30 else 30
        before = self._per_rom_data(library)

        summary = (await library.sync.sync_preview())["summary"]
        _sent, removed = await self._apply(library, emit, "run-2")

        assert summary["remove_count"] == 1
        assert [(row["slug"], row["remove_count"]) for row in summary["platform_breakdown"]] == [("gba", 1)]
        assert removed == [{"rom_id": gone, "app_id": 6000 + gone, "kept_rom_id": kept, "kept_app_id": 6000 + kept}]
        assert self._bindings(library) == {kept: 6000 + kept, gone: None}
        assert self._per_rom_data(library) == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("installed", "kept"), [((), 30), ((31,), 31), ((30, 31), 30)], ids=["none", "the-hidden", "both"]
    )
    async def test_a_version_romm_no_longer_lists_converges_with_the_listed_one(
        self, make_library, fake_romm_api, emit, tmp_path, installed, kept
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path, installed=installed)
        fake_romm_api.hidden_rom_ids = {31}
        gone = 31 if kept == 30 else 30

        summary = (await library.sync.sync_preview())["summary"]
        _sent, removed = await self._apply(library, emit, "run-2")

        assert summary["remove_count"] == 1
        assert [row["rom_id"] for row in removed] == [gone]
        assert self._bindings(library) == {kept: 6000 + kept, gone: None}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("installed", "kept"), [((), 30), ((31,), 31)], ids=["none", "31"])
    async def test_a_game_romm_no_longer_lists_at_all_keeps_one_shortcut(
        self, make_library, fake_romm_api, emit, tmp_path, installed, kept
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path, installed=installed)
        fake_romm_api.roms[40] = {
            **self._golf(40, "Japan", [], igdb_id=400),
            "platform_id": 2,
            "platform_name": "GBA",
            "platform_slug": "gba",
        }
        fake_romm_api.hidden_rom_ids = {30, 31}
        gone = 31 if kept == 30 else 30

        summary = (await library.sync.sync_preview())["summary"]
        _sent, removed = await self._apply(library, emit, "run-2")

        assert summary["remove_count"] == 1
        assert [row["rom_id"] for row in removed] == [gone]
        assert (self._bindings(library)[kept], self._bindings(library)[gone]) == (6000 + kept, None)

    @pytest.mark.asyncio
    async def test_the_platform_is_fetched_in_full_until_it_has_converged_then_skipped(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path)

        fake_romm_api.call_log.clear()
        await self._apply(library, emit, "run-2")
        converging_run = self._list_calls(fake_romm_api)
        fake_romm_api.call_log.clear()
        await self._apply(library, emit, "run-3")

        assert converging_run, "the converging run fetches the platform in full"
        assert self._list_calls(fake_romm_api) == []
        assert self._bindings(library) == {30: 6030, 31: None}

    @pytest.mark.asyncio
    async def test_a_stopped_run_removes_nothing_and_the_next_one_converges(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        bind = await self._two_bound(library, fake_romm_api, emit, tmp_path)
        # 30's launch command changes, so the unit has a chunk the stop lands in.
        with library.uow as uow:
            uow.roms.set_applied_launch_options(30, "stale")

        _sent, stopped = await self._apply(library, emit, "run-2", cancel=True)
        bindings_after_the_stop = self._bindings(library)
        library.chunk_dispatcher._wait_for_unit_complete = bind
        _sent, removed = await self._apply(library, emit, "run-3")

        assert stopped == []
        assert bindings_after_the_stop == {30: 6030, 31: 6031}
        assert [row["rom_id"] for row in removed] == [31]
        assert self._bindings(library) == {30: 6030, 31: None}

    @pytest.mark.asyncio
    async def test_the_removal_names_the_row_the_kept_shortcut_moves_onto(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """30 is installed and RomM reissued it as 50, the same dump: 30's shortcut moves onto 50, 31's goes."""
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path, installed=(30,))
        del fake_romm_api.roms[30]
        fake_romm_api.roms[50] = {
            **self._golf(50, "USA", [31], igdb_id=300),
            "platform_id": 2,
            "platform_name": "GBA",
            "platform_slug": "gba",
        }
        fake_romm_api.roms[31]["sibling_roms"] = [{"id": 50}]

        _sent, removed = await self._apply(library, emit, "run-2")

        assert removed == [{"rom_id": 31, "app_id": 6031, "kept_rom_id": 50, "kept_app_id": 6030}]
        assert self._bindings(library) == {30: None, 31: None, 50: 6030}

    @pytest.mark.asyncio
    async def test_a_removed_version_romm_no_longer_lists_is_not_kept_by_the_stale_removal(
        self, make_library, fake_romm_api, emit, tmp_path, caplog
    ):
        """It goes as a converged row only; neither the preview's stale sort nor the apply's keeps it."""
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path)
        fake_romm_api.hidden_rom_ids = {31}

        with caplog.at_level(logging.INFO):
            await library.sync.sync_preview()
            _sent, removed = await self._apply(library, emit, "run-2")

        assert [row["rom_id"] for row in removed] == [31]
        assert "Stale removal: keeping" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("running", [30, 31], ids=["the-kept-one", "the-other"])
    async def test_a_game_that_is_running_keeps_both_shortcuts_in_the_preview_and_the_apply(
        self, make_library, fake_romm_api, emit, tmp_path, running
    ):
        """D5: one of the game's shortcuts is running, so neither the preview nor the apply converges it."""
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path)
        library.running_games.running = {running}

        summary = (await library.sync.sync_preview())["summary"]
        _sent, removed = await self._apply(library, emit, "run-2")

        assert summary["remove_count"] == 0
        assert removed == []
        assert self._bindings(library) == {30: 6030, 31: 6031}

    @pytest.mark.asyncio
    async def test_a_game_left_out_while_running_converges_at_the_next_sync_fetched_in_full(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        bind = await self._two_bound(library, fake_romm_api, emit, tmp_path)
        library.running_games.running = {31}
        _sent, while_running = await self._apply(library, emit, "run-2")
        library.running_games.running = set()
        library.chunk_dispatcher._wait_for_unit_complete = bind

        fake_romm_api.call_log.clear()
        _sent, removed = await self._apply(library, emit, "run-3")

        assert while_running == []
        assert self._list_calls(fake_romm_api), "the platform is fetched in full while the game has two shortcuts"
        assert [row["rom_id"] for row in removed] == [31]
        assert self._bindings(library) == {30: 6030, 31: None}

    @pytest.mark.asyncio
    async def test_the_preview_and_the_apply_each_read_the_running_games_at_their_own_time(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """The game runs while the preview counts and has quit when the apply runs."""
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path)
        library.running_games.running = {30}

        summary = (await library.sync.sync_preview())["summary"]
        library.running_games.running = set()
        _sent, removed = await self._apply(library, emit, "run-2")

        assert summary["remove_count"] == 0
        assert [row["rom_id"] for row in removed] == [31]

    @pytest.mark.asyncio
    async def test_steam_is_not_asked_what_runs_when_nothing_converges(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path)
        await self._apply(library, emit, "run-2")
        readings = library.running_games.readings

        await library.sync.sync_preview()
        await self._apply(library, emit, "run-3")

        assert readings == 2, "the converging run reads at its unit and again before its removal"
        assert library.running_games.readings == readings

    @pytest.mark.asyncio
    async def test_a_game_started_after_its_unit_applied_keeps_both_shortcuts_and_converges_next_sync(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """D6: nothing runs when the unit converges; 31 has started by the time the finalize would remove it."""
        library = make_library(romm_api=fake_romm_api)
        bind = await self._two_bound(library, fake_romm_api, emit, tmp_path)
        running = library.running_games

        def start_after_the_unit(reading):
            if reading == 2:
                running.running = {31}

        running.on_read = start_after_the_unit
        _sent, while_running = await self._apply(library, emit, "run-2")
        bindings_while_running = self._bindings(library)
        running.on_read = None
        running.running = set()
        library.chunk_dispatcher._wait_for_unit_complete = bind
        fake_romm_api.call_log.clear()
        _sent, removed = await self._apply(library, emit, "run-3")

        assert while_running == []
        assert bindings_while_running == {30: 6030, 31: 6031}
        assert self._list_calls(fake_romm_api), "the platform is fetched in full while the game has two shortcuts"
        assert [row["rom_id"] for row in removed] == [31]
        assert self._bindings(library) == {30: 6030, 31: None}

    @pytest.mark.asyncio
    async def test_with_nothing_running_the_finalize_reads_once_more_and_removes(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path)

        _sent, removed = await self._apply(library, emit, "run-2")

        assert library.running_games.readings == 2
        assert [row["rom_id"] for row in removed] == [31]

    @pytest.mark.asyncio
    async def test_a_removed_shortcut_gets_no_cover_refresh_and_the_kept_one_does(
        self, make_library, fake_romm_api, emit, tmp_path
    ):
        """Both versions' covers changed in RomM: only the shortcut that stays has its cover pushed again."""
        library = make_library(romm_api=fake_romm_api)
        await self._two_bound(library, fake_romm_api, emit, tmp_path)
        with library.uow as uow:
            for rom_id in (30, 31):
                rom = uow.roms.get(rom_id)
                rom.adopt_cover_source(f"/cover/{rom_id}.png?ts=old")
                uow.roms.save(rom)
                fake_romm_api.roms[rom_id]["path_cover_large"] = f"/cover/{rom_id}.png?ts=new"
                fake_romm_api.download_payloads[f"cover:/cover/{rom_id}.png?ts=new"] = b"new cover"
        start = len(emit.call_args_list)

        await self._apply(library, emit, "run-2")

        refreshes = [
            refresh
            for call in emit.call_args_list[start:]
            if call.args and call.args[0] == "sync_apply_unit"
            for refresh in call.args[1]["cover_refreshes"]
        ]
        assert refreshes == [{"rom_id": 30, "app_id": 6030}]

    @pytest.mark.asyncio
    async def test_a_converged_row_whose_app_id_this_run_bound_is_not_removed(self, make_library, emit):
        """The #1036 guard holds for a converged row too: an appId this run bound is never removed."""
        library = make_library()
        _seed_rom_row(library, 30, app_id=6030, platform_slug="gba", sibling_group_key="igdb:300:2")
        _seed_rom_row(library, 31, app_id=6031, platform_slug="gba", sibling_group_key="igdb:300:2")
        box = library.sync._box
        box.run_converged = {31: ConvergedBinding(31, 6031, 30, 6030)}
        box.committed_app_ids = {6031}

        await library.sync._orchestrator._finalize_per_unit(
            synced_rom_ids={30, 31},
            collection_memberships={},
            platform_rom_ids={30, 31},
            processed_platform_slugs=frozenset({"gba"}),
            cancelled=False,
        )

        stale = [c.args[1] for c in emit.call_args_list if c.args and c.args[0] == "sync_stale"]
        assert stale == [{"remove": []}]
        assert self._bindings(library) == {30: 6030, 31: 6031}


class TestDeltaRestrictedApply:
    """Delta-restricted apply (#1383): classify in the apply path, emit only new +
    changed, skip content-unchanged shortcuts while still committing every row."""

    @staticmethod
    def _apply_setup(library, fake_romm_api):
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-delta"

    @staticmethod
    def _apply_unit_events(emit):
        return [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_apply_unit"]

    @pytest.mark.asyncio
    async def test_unchanged_item_skipped_but_row_committed_and_stamped(self, make_library, fake_romm_api, emit):
        # rom 10 is content-unchanged (identity + recorded applied "" both match the
        # uninstalled built ""); rom 11 changed its name. Only rom 11 is emitted; both
        # rows still commit and the platform is stamped.
        library = make_library(romm_api=fake_romm_api)
        self._apply_setup(library, fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 10, "name": "Keep", "fs_name": "keep.z64"},
                {"id": 11, "name": "New Name", "fs_name": "changed.z64"},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")
        _seed_rom_row(library, 11, app_id=1011, platform_slug="n64", name="Old Name", fs_name="changed.z64")

        await library.sync._orchestrator._do_sync_per_unit()

        events = self._apply_unit_events(emit)
        assert len(events) == 1
        emitted_ids = [s["rom_id"] for s in events[0]["shortcuts"]]
        assert emitted_ids == [11], "only the changed item is emitted; the unchanged one is skipped"
        assert events[0]["unit_total"] == 1, "unit_total is the DELTA size, not the whole platform"

        with library.uow as uow:
            # Both rows committed: the changed name is persisted, the skipped row kept
            # its binding + recorded applied (never re-acked, so save() left it alone).
            assert uow.roms.get(11).name == "New Name"
            skipped = uow.roms.get(10)
            assert skipped.shortcut_app_id == 1010
            assert skipped.applied_launch_options == ""
            # The platform stamp rides the final chunk even though most items skipped.
            assert uow.platform_sync_state.get("n64") is not None

    @pytest.mark.asyncio
    async def test_empty_delta_platform_still_stamps_and_commits_all_rows(self, make_library, fake_romm_api, emit):
        # Every item content-unchanged → an empty delta. The pipeline still emits one
        # empty chunk (unit_total 0), commits every row, and writes the platform stamp.
        library = make_library(romm_api=fake_romm_api)
        self._apply_setup(library, fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 10, "name": "A", "fs_name": "a.z64"},
                {"id": 11, "name": "B", "fs_name": "b.z64"},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")
        _seed_rom_row(library, 11, app_id=1011, platform_slug="n64", name="B", fs_name="b.z64")

        await library.sync._orchestrator._do_sync_per_unit()

        events = self._apply_unit_events(emit)
        assert len(events) == 1, "the empty-delta platform still round-trips exactly one (empty) chunk"
        assert events[0]["shortcuts"] == []
        assert events[0]["unit_total"] == 0
        with library.uow as uow:
            assert uow.roms.get(10).shortcut_app_id == 1010
            assert uow.roms.get(11).shortcut_app_id == 1011
            assert uow.platform_sync_state.get("n64") is not None

    @pytest.mark.asyncio
    async def test_null_recorded_applied_forces_reapply(self, make_library, fake_romm_api, emit):
        # A bound row whose applied_launch_options is NULL (pre-migration-015 /
        # never recorded) is unknown → always re-applied, even with matching identity.
        library = make_library(romm_api=fake_romm_api)
        self._apply_setup(library, fake_romm_api)
        _seed_platform(
            fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A", "fs_name": "a.z64"}]
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(
            library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64", applied_launch_options=None
        )

        await library.sync._orchestrator._do_sync_per_unit()

        events = self._apply_unit_events(emit)
        assert len(events) == 1
        assert [s["rom_id"] for s in events[0]["shortcuts"]] == [10], "NULL recorded state forces a re-apply"


class TestSessionBudgetGate:
    """The session-budget gate as a real sync run drives it end-to-end (#1383)."""

    # ── End-to-end through the chunk loop ────────────────────────

    @staticmethod
    def _arm_two_chunk_apply(library, fake_romm_api, monkeypatch):
        """Seed a 2-ROM platform and force one shortcut per chunk (2 chunks)."""
        from services.library import chunk_dispatcher

        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Alpha"}, {"id": 11, "name": "Beta"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 1)

        async def fake_wait(_u, event):
            event.set()
            return {}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-budget"

    @pytest.mark.asyncio
    async def test_pause_at_second_chunk_persists_paused_with_budget_reason(
        self, make_library, fake_romm_api, monkeypatch, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        from services.library.session_budget import SYNC_PAUSED_BUDGET

        self._arm_two_chunk_apply(library, fake_romm_api, monkeypatch)
        library.renderer_gc.result = True
        # Just under the ceiling: the first chunk's predictive-vs-CLIFF check
        # (2.199 GB + one item's 2500 KB well below the 2.45 GB cliff) passes, but
        # the second chunk's predictive-vs-ceiling check (+2500 ≥ 2.2 GB) crosses.
        library.renderer_rss.rss_kb = 2_199_000

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            run = uow.sync_runs.get("run-budget")
        assert run is not None
        # A deliberate session-budget stop is 'paused', NOT 'interrupted' (which is
        # reserved for an external death — a crash/heartbeat timeout).
        assert run.status == "paused"
        assert run.error == SYNC_PAUSED_BUDGET
        # The first chunk's predictive-vs-cliff check passed (well below the cliff)
        # so it committed its ROM; the gate fired the GC before measuring on both
        # chunk boundaries.
        assert library.renderer_gc.calls >= 2
        # The distinct pause reason reaches the frontend via sync_complete so the
        # toast + QAM status read the resume-friendly guidance, not "cancelled".
        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert complete
        assert complete[-1].get("interrupt_reason") == SYNC_PAUSED_BUDGET
        assert complete[-1].get("cancelled") is True

    @staticmethod
    def _arm_single_chunk_apply(library, fake_romm_api, monkeypatch, *, run_id):
        """Seed a 1-ROM platform (→ one chunk = the run's first chunk)."""
        from services.library import chunk_dispatcher

        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "Solo"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 1)

        async def fake_wait(_u, event):
            event.set()
            return {}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = run_id

    @pytest.mark.asyncio
    async def test_first_chunk_proceeds_when_projection_stays_below_cliff(
        self, make_library, fake_romm_api, monkeypatch
    ):
        # One ROM → one chunk = the run's first. It is gated PREDICTIVELY against the
        # CLIFF (2.45 GB), not the ceiling: at 2.4 GB — ABOVE the 2.2 GB ceiling the
        # old absolute first-chunk check would have paused at — this light 1-item
        # chunk projects 2.4025 GB (create + cover), still below the cliff, so it
        # proceeds into the safety margin and the run completes. The gate DID run, so
        # the GC fired.
        library = make_library(romm_api=fake_romm_api)
        self._arm_single_chunk_apply(library, fake_romm_api, monkeypatch, run_id="run-solo")
        library.renderer_gc.result = True
        library.renderer_rss.rss_kb = 2_400_000  # above the 2.2M ceiling, below the 2.45M cliff

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            run = uow.sync_runs.get("run-solo")
        assert run is not None
        assert run.status == "completed"
        assert library.renderer_gc.calls >= 1  # the predictive-vs-cliff check GCs + measures

    @pytest.mark.asyncio
    async def test_first_chunk_repauses_when_projection_reaches_cliff(self, make_library, fake_romm_api, monkeypatch):
        library = make_library(romm_api=fake_romm_api)
        from services.library.session_budget import SYNC_PAUSED_BUDGET

        # A resume whose first chunk would be PROJECTED to reach the cliff (no Steam
        # restart) must re-pause on that very first chunk rather than drive it into
        # the crash line. At 2.449 GB even the light 1-item chunk projects ≥ the
        # 2.45 GB cliff, so the run re-pauses with zero forward progress — intended.
        self._arm_single_chunk_apply(library, fake_romm_api, monkeypatch, run_id="run-over")
        library.renderer_gc.result = True
        library.renderer_rss.rss_kb = 2_449_000  # +2500 (create + cover) for the one item ≥ the 2.45M cliff

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            run = uow.sync_runs.get("run-over")
        assert run is not None
        assert run.status == "paused"
        assert run.error == SYNC_PAUSED_BUDGET
        assert library.renderer_gc.calls >= 1

    @pytest.mark.asyncio
    async def test_fail_open_rss_none_completes_all_chunks(self, make_library, fake_romm_api, monkeypatch):
        library = make_library(romm_api=fake_romm_api)
        self._arm_two_chunk_apply(library, fake_romm_api, monkeypatch)
        library.renderer_gc.result = False
        library.renderer_rss.rss_kb = None  # measurement unavailable → gate skipped

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            run = uow.sync_runs.get("run-budget")
        assert run is not None
        assert run.status == "completed"  # both chunks applied, no pause
        assert library.sync._box.budget_measure_unavailable_logged is True
        # The raw-first measure detects the unavailable reading (raw is None) BEFORE
        # any GC, and the once-per-run flag short-circuits every later measure, so a
        # run whose renderer RSS is unreadable never GCs at all.
        assert library.renderer_gc.calls == 0

    # ── Preview prognosis + post-run advisory ────────────────────

    @pytest.mark.asyncio
    async def test_preview_flags_pause_likely_when_run_would_cross(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.renderer_rss.rss_kb = 2_199_000  # 1 planned create (+2500) crosses 2.2M ceiling

        result = await library.sync.sync_preview()

        assert result["success"] is True
        assert result["pause_likely"] is True

    @pytest.mark.asyncio
    async def test_preview_pause_likely_false_with_headroom(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.renderer_rss.rss_kb = 440_000  # fresh baseline

        result = await library.sync.sync_preview()

        assert result["pause_likely"] is False

    @pytest.mark.asyncio
    async def test_preview_pause_likely_false_when_rss_unavailable(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.renderer_rss.rss_kb = None  # fail-open → no warning

        result = await library.sync.sync_preview()

        assert result["pause_likely"] is False

    @pytest.mark.asyncio
    async def test_preview_large_unchanged_resync_does_not_warn(self, make_library, fake_romm_api, emit):
        # MEDIUM-1: unchanged items are not priced, so a fully-unchanged re-sync
        # near the ceiling does NOT warn — even though the OLD all-touches formula
        # (which priced unchanged at the create rate) would have crossed here.

        library = make_library(romm_api=fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 1, "name": "A", "fs_name": "a.z64"},
                {"id": 2, "name": "B", "fs_name": "b.z64"},
                {"id": 3, "name": "C", "fs_name": "c.z64"},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        # Matching bound baseline rows → all three classify as unchanged.
        _seed_rom_row(library, 1, app_id=1001, platform_slug="n64", name="A", fs_name="a.z64")
        _seed_rom_row(library, 2, app_id=1002, platform_slug="n64", name="B", fs_name="b.z64")
        _seed_rom_row(library, 3, app_id=1003, platform_slug="n64", name="C", fs_name="c.z64")
        # rss + 3*2500 would cross (if unchanged were priced as creates); rss + 0 new
        # + 0 changed does not.
        library.renderer_rss.rss_kb = 2_199_000

        result = await library.sync.sync_preview()

        assert result["success"] is True
        assert result["summary"]["unchanged_count"] == 3
        assert result["summary"]["new_count"] == 0
        assert result["summary"]["changed_count"] == 0
        assert result["pause_likely"] is False

    @pytest.mark.asyncio
    async def test_completed_run_recommends_restart_when_rss_high(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "A"}])
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.renderer_rss.rss_kb = 1_900_000  # > 1.8M post-run advisory floor

        async def fake_wait(_u, event):
            event.set()
            return {"10": 9001}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-restart"

        await library.sync._orchestrator._do_sync_per_unit()

        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert complete, "sync_complete must be emitted"
        assert complete[-1].get("restart_recommended") is True

    # ── Last-run memory delta (#32) ──────────────────────────────

    @pytest.mark.asyncio
    async def test_finalize_computes_and_retains_memory_delta(self, library):
        # The signed growth is retained in the box for QAM remounts
        # (get_session_budget_status). _finalize_per_unit itself no longer emits
        # sync_complete (that moved to the orchestrator's post-write emit_sync_complete,
        # #39), so this test just pins the retained box value.
        orch = library.sync._orchestrator
        box = library.sync._box
        box.committed_app_ids = set()
        box.run_start_rss_kb = 500_000  # raw baseline captured at run start
        library.renderer_rss.rss_kb = 1_300_000  # post-run reading (below floor → raw)

        await orch._finalize_per_unit(
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
            processed_platform_slugs=frozenset(),
            cancelled=False,
        )

        assert box.last_run_delta_kb == 800_000

    @pytest.mark.asyncio
    async def test_finalize_no_delta_when_run_start_unmeasured(self, library):
        # A run whose run-start baseline read was unavailable (None) leaves the delta
        # unmeasurable — no number retained (degrade gracefully, never a stale delta).
        orch = library.sync._orchestrator
        box = library.sync._box
        box.committed_app_ids = set()
        box.run_start_rss_kb = None
        library.renderer_rss.rss_kb = 1_300_000

        await orch._finalize_per_unit(
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
            processed_platform_slugs=frozenset(),
            cancelled=False,
        )

        assert box.last_run_delta_kb is None

    @pytest.mark.asyncio
    async def test_finalize_stores_delta_on_a_stopped_run(self, library):
        # #36: the delta is measured at EVERY terminal, not just clean completion —
        # so a paused/cancelled run overwrites a PRIOR run's delta with ITS OWN
        # consumption-so-far instead of leaving the stale number on the row.
        from services.library.session_budget import SYNC_PAUSED_BUDGET

        orch = library.sync._orchestrator
        box = library.sync._box
        box.committed_app_ids = set()
        box.run_start_rss_kb = 500_000  # this run's raw run-start baseline
        box.last_run_delta_kb = 700_000  # a PRIOR clean run's delta, must not linger
        box.run_paused = True
        box.interrupt_reason = SYNC_PAUSED_BUDGET
        library.renderer_rss.rss_kb = 2_100_000  # terminal reading — the run grew memory

        await orch._finalize_per_unit(
            synced_rom_ids=set(),
            collection_memberships={},
            platform_rom_ids=set(),
            processed_platform_slugs=frozenset(),
            cancelled=True,  # a stopped (paused) run
        )

        # 2_100_000 - 500_000 = 1_600_000 — this paused run's own growth, overwriting 700_000.
        assert box.last_run_delta_kb == 1_600_000

    @pytest.mark.asyncio
    async def test_baseline_captured_at_run_start_so_skip_only_run_reports_zero_delta(
        self, make_library, fake_romm_api
    ):
        # LOW-1: the baseline is captured at RUN START, not at the first chunk gate,
        # so a fully-incremental-skip run (nothing applied, no gate ever fires) still
        # records a baseline and reports an honest ≈ +0.0 GB delta instead of wiping
        # last_run_delta_kb to None on every no-op re-sync.
        library = make_library(romm_api=fake_romm_api)
        # roms matches platform count + zero updates → the platform incremental-skips.
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        library.settings["enabled_platforms"] = {"1": True}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.renderer_rss.rss_kb = 1_200_000  # below the GC-skip floor → raw both ends
        # A stale delta from a prior run — the fresh no-op run must overwrite it, not
        # leave it and not wipe it to None.
        library.sync._box.last_run_delta_kb = 500_000
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-skip"

        await library.sync._orchestrator._do_sync_per_unit()

        box = library.sync._box
        assert box.run_start_rss_kb == 1_200_000  # captured at run start despite no chunk
        assert box.last_run_delta_kb == 0  # honest zero, not None

    # ── Terminal emit ordering (#39): sync_complete AFTER the SyncRun write ──

    @staticmethod
    async def _capture_run_status_at_sync_complete(library, emit, run_id: str) -> str | None:
        """Run the pipeline with an ``emit`` hook that reads the run's persisted
        status the instant ``sync_complete`` is emitted; return that status."""

        seen: dict[str, str | None] = {}

        async def _hook(event, payload=None):
            if event == "sync_complete" and "value" not in seen:
                with library.uow as uow:
                    run = uow.sync_runs.get(run_id)
                    seen["value"] = run.status if run is not None else None

        emit.side_effect = _hook
        await library.sync._orchestrator._do_sync_per_unit()
        return seen.get("value")

    @pytest.mark.asyncio
    async def test_sync_complete_emits_after_completed_syncrun_persisted(
        self, make_library, fake_romm_api, monkeypatch, emit
    ):
        # A clean run: when sync_complete fires, the SyncRun is ALREADY 'completed', so
        # a frontend stats refetch can't read the prior run's status (#39).
        library = make_library(romm_api=fake_romm_api)
        self._arm_single_chunk_apply(library, fake_romm_api, monkeypatch, run_id="run-order-done")
        library.renderer_rss.rss_kb = 440_000  # low → completes

        status = await self._capture_run_status_at_sync_complete(library, emit, "run-order-done")

        assert status == "completed"

    @pytest.mark.asyncio
    async def test_sync_complete_emits_after_paused_syncrun_persisted(
        self, make_library, fake_romm_api, monkeypatch, emit
    ):
        # A budget-paused run: when sync_complete fires, the SyncRun is ALREADY 'paused'
        # — the emit-last ordering that closes the emit-before-persist race (#39).
        library = make_library(romm_api=fake_romm_api)
        self._arm_two_chunk_apply(library, fake_romm_api, monkeypatch)  # run id "run-budget"
        library.renderer_gc.result = True
        library.renderer_rss.rss_kb = 2_199_000  # first chunk passes, second pauses

        status = await self._capture_run_status_at_sync_complete(library, emit, "run-budget")

        assert status == "paused"


class TestRunProgressCounters:
    """The run-scoped ``X of Y games done`` counters behind the paused banner (#1383).

    They live in the backend because the backend process survives the Steam restart the
    paused banner asks for — only the frontend reloads — and they ride the existing
    ``get_session_budget_status`` payload the QAM already polls while paused.
    """

    @staticmethod
    def _arm(library, fake_romm_api, *, run_id="run-progress"):
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = run_id

    @staticmethod
    def _acks(library, *maps):
        """Drive ``_wait_for_unit_complete`` to ack *maps* in order (one per chunk)."""
        pending = list(maps)

        async def fake_wait(_unit, event):
            event.set()
            return pending.pop(0) if pending else {}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait

    @pytest.mark.asyncio
    async def test_committed_chunks_count_toward_done(self, make_library, fake_romm_api, monkeypatch):
        library = make_library(romm_api=fake_romm_api)
        from services.library import chunk_dispatcher

        # Two brand-new ROMs, one shortcut per chunk → two chunks, both acked and
        # committed. The plan's ROM total is the denominator.
        self._arm(library, fake_romm_api)
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Alpha"}, {"id": 11, "name": "Beta"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 1)
        self._acks(library, {"10": 1010}, {"11": 1011})

        await library.sync._orchestrator._do_sync_per_unit()

        box = library.sync._box
        assert box.run_total_items == 2
        assert box.run_done_items == 2

    @pytest.mark.asyncio
    async def test_emitted_but_uncommitted_chunk_does_not_count(self, make_library, fake_romm_api, monkeypatch):
        library = make_library(romm_api=fake_romm_api)
        from services.library import chunk_dispatcher

        # The first chunk is emitted, then the user cancels mid-wait: the wait gives
        # up (None), the chunk never commits, and its item must NOT be reported done.
        self._arm(library, fake_romm_api, run_id="run-cancel")
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Alpha"}, {"id": 11, "name": "Beta"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 1)
        box = library.sync._box

        async def cancel_mid_wait(_unit, _event) -> dict[str, int] | None:
            # A None ack is the wait's "gave up" signal (user cancel / heartbeat timeout).
            box.request_cancel()
            return None

        library.chunk_dispatcher._wait_for_unit_complete = cancel_mid_wait

        await library.sync._orchestrator._do_sync_per_unit()

        assert box.run_total_items == 2
        assert box.run_done_items == 0, "an emitted chunk whose ack never landed is not done"

    @pytest.mark.asyncio
    async def test_paused_run_counts_only_the_committed_chunk(self, make_library, fake_romm_api, monkeypatch):
        library = make_library(romm_api=fake_romm_api)
        from services.library import chunk_dispatcher

        # The session-budget gate pauses at the second chunk's boundary — before its
        # emit — so exactly one chunk committed. This is the banner's own scenario.
        self._arm(library, fake_romm_api, run_id="run-paused")
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Alpha"}, {"id": 11, "name": "Beta"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 1)
        self._acks(library, {"10": 1010}, {"11": 1011})
        library.renderer_gc.result = True
        library.renderer_rss.rss_kb = 2_199_000  # first chunk passes (vs cliff), second pauses

        await library.sync._orchestrator._do_sync_per_unit()

        with library.uow as uow:
            assert uow.sync_runs.get("run-paused").status == "paused"
        box = library.sync._box
        assert box.run_total_items == 2
        assert box.run_done_items == 1

    @pytest.mark.asyncio
    async def test_delta_skipped_items_count_as_done(self, make_library, fake_romm_api):
        # rom 10 is content-unchanged (skipped by the delta-restricted apply — its
        # shortcut is already correct), rom 11 changed and is applied. Both are done.
        library = make_library(romm_api=fake_romm_api)
        self._arm(library, fake_romm_api, run_id="run-delta-progress")
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 10, "name": "Keep", "fs_name": "keep.z64"},
                {"id": 11, "name": "New Name", "fs_name": "changed.z64"},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")
        _seed_rom_row(library, 11, app_id=1011, platform_slug="n64", name="Old Name", fs_name="changed.z64")
        self._acks(library, {"11": 1011})

        await library.sync._orchestrator._do_sync_per_unit()

        box = library.sync._box
        assert box.run_total_items == 2
        assert box.run_done_items == 2, "1 skipped-unchanged + 1 acked in the single chunk"

    @pytest.mark.asyncio
    async def test_wholesale_skipped_unit_counts_its_roms(self, make_library, fake_romm_api):
        # A platform the incremental gate skips entirely (its stamp matches the
        # server) never reaches the apply — but its games ARE done. This is what a
        # resume sees for every platform it finished before the pause, so without
        # this the resumed run's banner would under-report badly.
        library = make_library(romm_api=fake_romm_api)
        self._arm(library, fake_romm_api, run_id="run-skip")
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")
        fake_romm_api.platforms = [{"id": 1, "name": "N64", "slug": "n64", "rom_count": 1}]
        library.settings["enabled_platforms"] = {"1": True}
        self._acks(library)

        await library.sync._orchestrator._do_sync_per_unit()

        box = library.sync._box
        assert box.run_total_items == 1
        assert box.run_done_items == 1

    @pytest.mark.asyncio
    async def test_counters_reset_at_run_start(self, make_library, fake_romm_api):
        # A stale run's counters must never leak into the next run's banner.
        library = make_library(romm_api=fake_romm_api)
        self._arm(library, fake_romm_api, run_id="run-reset")
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 10, "name": "Alpha"}])
        library.settings["enabled_platforms"] = {"1": True}
        self._acks(library, {"10": 1010})
        box = library.sync._box
        box.run_done_items = 5000
        box.run_total_items = 9999

        await library.sync._orchestrator._do_sync_per_unit()

        assert box.run_total_items == 1
        assert box.run_done_items == 1

    @pytest.mark.asyncio
    async def test_the_session_budget_status_surfaces_the_run_progress(self, make_library, fake_romm_api):
        # The pair rides the existing session-budget payload the QAM already polls.
        library = make_library(romm_api=fake_romm_api)
        self._arm(library, fake_romm_api, run_id="run-status")
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 10, "name": "Alpha"}, {"id": 11, "name": "Beta"}],
        )
        library.settings["enabled_platforms"] = {"1": True}
        self._acks(library, {"10": 1010, "11": 1011})

        await library.sync._orchestrator._do_sync_per_unit()
        result = await library.sync.get_session_budget_status()

        assert result["run_done_items"] == 2
        assert result["run_total_items"] == 2

    @pytest.mark.asyncio
    async def test_the_session_budget_status_reports_unknown_before_any_run(self, library):
        # A fresh backend process has no counters: the pair is None, and the
        # banner drops the sentence rather than showing zeros.
        result = await library.sync.get_session_budget_status()

        assert result["run_done_items"] is None
        assert result["run_total_items"] is None


class TestProcessedGamesNumerator:
    """``total_games`` — the terminal frame's "N of M games processed" numerator.

    It counts every PROCESSED ROM the same way ``run_done_items`` does: a
    wholesale-skipped unit's ROMs, a partial unit's delta-skipped entries, and
    the committed applies. A resumed run that is interrupted again must not
    understate the one platform it resumed into — a delta-skipped ROM is just
    as processed as a wholesale-skipped one.
    """

    @staticmethod
    def _arm(library, fake_romm_api, *, run_id):
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = run_id

    @pytest.mark.asyncio
    async def test_clean_run_payload_counts_delta_skips(self, make_library, fake_romm_api, emit):
        # rom 10 is content-unchanged (delta-skipped), rom 11 changed and is
        # acked — both are processed, so the sync_complete payload reports 2.
        library = make_library(romm_api=fake_romm_api)
        self._arm(library, fake_romm_api, run_id="run-numerator-clean")
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[
                {"id": 10, "name": "Keep", "fs_name": "keep.z64"},
                {"id": 11, "name": "New Name", "fs_name": "changed.z64"},
            ],
        )
        library.settings["enabled_platforms"] = {"1": True}
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64")
        _seed_rom_row(library, 11, app_id=1011, platform_slug="n64", name="Old Name", fs_name="changed.z64")

        async def fake_wait(_unit, event):
            event.set()
            return {"11": 1011}

        library.chunk_dispatcher._wait_for_unit_complete = fake_wait

        await library.sync._orchestrator._do_sync_per_unit()

        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert complete[-1]["total_games"] == 2, "1 delta-skipped + 1 applied are both processed"

    @pytest.mark.asyncio
    async def test_resumed_run_interrupt_frame_counts_delta_skips(self, make_library, fake_romm_api, monkeypatch, emit):
        """The resume-then-interrupt shape: a platform the prior run finished
        wholesale-skips, the partial platform delta-skips its already-applied
        ROM and commits one more chunk, then the heartbeat times out. The
        terminal frame's numerator counts all three kinds of processed ROM
        (and agrees with the paused banner's ``run_done_items``)."""

        library = make_library(romm_api=fake_romm_api)
        from services.library import chunk_dispatcher

        self._arm(library, fake_romm_api, run_id="run-resume-interrupt")

        # Unit 1 (N64): wholesale-skipped — its completion stamp matches the
        # server and its one bound row reconstructs.
        _seed_platform_stamp(library, "n64", at="2025-01-01T00:00:00Z", rom_count=1)
        _seed_rom_row(library, 10, app_id=1010, platform_slug="n64", name="A", fs_name="a.z64")
        fake_romm_api.platforms.append({"id": 1, "name": "N64", "slug": "n64", "rom_count": 1})

        # Unit 2 (GBA): the partial platform — rom 20 is content-unchanged
        # (delta-skipped), roms 21/22 changed. One item per chunk: chunk 0
        # acks and commits, chunk 1's wait gives up while the box is still
        # RUNNING (heartbeat timeout) → the run ends interrupted.
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="GBA",
            slug="gba",
            roms=[
                {"id": 20, "name": "Keep", "fs_name": "keep.gba"},
                {"id": 21, "name": "New Name", "fs_name": "changed.gba"},
                {"id": 22, "name": "Other New", "fs_name": "other.gba"},
            ],
        )
        _seed_rom_row(library, 20, app_id=1020, platform_slug="gba", name="Keep", fs_name="keep.gba")
        _seed_rom_row(library, 21, app_id=1021, platform_slug="gba", name="Old Name", fs_name="changed.gba")
        _seed_rom_row(library, 22, app_id=1022, platform_slug="gba", name="Old Other", fs_name="other.gba")
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        monkeypatch.setattr(chunk_dispatcher, "_APPLY_CHUNK_SIZE", 1)

        acks: list[dict[str, int] | None] = [{"21": 1021}, None]

        async def wait_ack_then_timeout(_unit, event):
            ack = acks.pop(0)
            if ack is not None:
                event.set()
                return ack
            return None  # heartbeat timeout — the box is still RUNNING

        library.chunk_dispatcher._wait_for_unit_complete = wait_ack_then_timeout

        await library.sync._orchestrator._do_sync_per_unit()

        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert complete[-1]["total_games"] == 3, "1 wholesale-skipped + 1 delta-skipped + 1 committed"
        assert complete[-1]["interrupted"] is True
        # The terminal frame as EMITTED — the snapshot the box holds is back at
        # the idle default by now, because the run has finished (``finish_run``).
        progress = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_progress"][-1]
        assert progress["stage"] == "cancelled"
        assert progress["message"] == "Sync interrupted: 3 of 4 games processed"
        assert progress["current"] == 3
        assert progress["total"] == 4
        # The frame's numerator and the paused banner's done counter agree.
        assert library.sync._box.run_done_items == 3


class TestOneReadingOfTheSourcesPerRun:
    """A run resolves every ROM's emulator through ONE reading of the sources.

    The reading keeps the run's answers until it ends, so a system asked about
    for many ROMs is asked once, and a switch or a move written during the run
    takes effect from the next one.
    """

    @staticmethod
    def _two_platforms(library, fake_romm_api) -> None:
        _seed_platform(
            fake_romm_api,
            platform_id=1,
            name="N64",
            slug="n64",
            roms=[{"id": 1, "name": "Game A", "fs_name": "a.z64"}, {"id": 2, "name": "Game B", "fs_name": "b.z64"}],
        )
        _seed_platform(
            fake_romm_api,
            platform_id=2,
            name="SNES",
            slug="snes",
            roms=[{"id": 3, "name": "Game C", "fs_name": "c.sfc"}],
        )
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        # A ROM the database knows is one the active-core resolver asks the
        # catalogue about; an unknown one resolves to nothing before that.
        _seed_rom_row(library, 1, app_id=1001, platform_slug="n64", name="Game A", fs_name="a.z64")
        _seed_rom_row(library, 2, app_id=1002, platform_slug="n64", name="Game B", fs_name="b.z64")
        _seed_rom_row(library, 3, app_id=1003, platform_slug="snes", name="Game C", fs_name="c.sfc")

    @pytest.mark.asyncio
    async def test_a_preview_resolves_every_rom_through_one_reading(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._two_platforms(library, fake_romm_api)

        assert (await library.sync.sync_preview())["success"] is True

        assert library.emulator_sources.reads == 1
        (reading,) = library.emulator_sources.taken
        assert len(library.core_info.readings) == 3
        assert all(handed is reading for handed in library.core_info.readings)
        assert len(library.core_info.default_readings) == 3
        assert all(handed is reading for handed in library.core_info.default_readings)

    @pytest.mark.asyncio
    async def test_two_previews_take_two_readings(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._two_platforms(library, fake_romm_api)

        await library.sync.sync_preview()
        await library.sync.sync_preview()

        first, second = library.emulator_sources.taken
        assert first is not second
        assert all(handed is first for handed in library.core_info.readings[:3])
        assert all(handed is second for handed in library.core_info.readings[3:])

    @pytest.mark.asyncio
    async def test_an_apply_hands_every_unit_the_same_reading(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._two_platforms(library, fake_romm_api)
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        library.chunk_dispatcher._wait_for_unit_complete = _fake_wait_set_event
        assert library.sync._box.try_begin_run("run-apply", kind=SyncRunKind.APPLY) is True

        await library.sync._orchestrator._do_sync_per_unit()

        assert library.emulator_sources.reads == 1
        (reading,) = library.emulator_sources.taken
        assert len(library.core_info.readings) == 3
        assert all(handed is reading for handed in library.core_info.readings)
        assert len(library.core_info.default_readings) == 3
        assert all(handed is reading for handed in library.core_info.default_readings)


class TestTheShortcutArtworkJobAfterARun:
    """Every run's end asks for the shortcut artwork job."""

    @pytest.mark.asyncio
    async def test_an_apply_run_asks_for_it_once_steam_has_had_time_to_write_its_file(
        self, make_library, fake_romm_api
    ):
        library = make_library(romm_api=fake_romm_api)
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {}
        library.sync._box.sync_state = SyncState.RUNNING

        await library.sync._orchestrator._do_sync_per_unit()

        assert library.artwork_job.requests == [True]

    @pytest.mark.asyncio
    async def test_an_apply_run_that_failed_asks_for_it_too(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        fake_romm_api.list_platforms_side_effect = RuntimeError("boom")
        library.settings["enabled_platforms"] = {"1": True}
        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "run-fails"

        await library.sync._orchestrator._do_sync_per_unit()

        assert library.artwork_job.requests == [True]

    @pytest.mark.asyncio
    async def test_a_preview_asks_for_it_without_waiting(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        library.settings["enabled_platforms"] = {}
        library.settings["enabled_collections"] = {}

        await library.sync.sync_preview()

        assert library.artwork_job.requests == [False]


class TestAGameSplitAcrossCollections:
    """A game only collections deliver is minted over every version they deliver (#2543 D1, D3, D4).

    Golf's Japanese (23) and European (22) versions sit on GBA, whose sync is
    off; each collection lists one of them. The version rules rank Europe over
    Japan, and the shortcut is named after the European version either way.
    """

    @staticmethod
    def _golf(rom_id, region, **extra):
        stem = f"Golf ({region})"
        return {
            "id": rom_id,
            "name": stem,
            "fs_name": f"{stem}.zip",
            "fs_name_no_ext": stem,
            "igdb_id": 300,
            "regions": [region],
            **extra,
        }

    @classmethod
    def _seed(cls, library, fake_romm_api, collections, *, enabled=None, main=None):
        """*collections* are ``(id, name, rom_ids)`` in queue order; *enabled* the ids synced (default all)."""
        golf = [cls._golf(22, "Europe"), cls._golf(23, "Japan")]
        for rom in golf:
            if rom["id"] == main:
                rom["rom_user"] = {"is_main_sibling": True}
        _seed_platform(fake_romm_api, platform_id=2, name="GBA", slug="gba", roms=golf)
        library.settings["enabled_platforms"] = {**library.settings.get("enabled_platforms", {}), "2": False}
        for cid, name, rom_ids in collections:
            _seed_collection(fake_romm_api, collection_id=cid, name=name, rom_ids=rom_ids)
            fake_romm_api.collections[-1]["updated_at"] = "2025-01-01T00:00:00"
        enabled = [cid for cid, _, _ in collections] if enabled is None else enabled
        library.settings["enabled_collections"] = {"standard": {str(cid): True for cid in enabled}}
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        box = library.sync._box

        async def bind_what_the_unit_emits(_unit, event):
            event.set()
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_what_the_unit_emits

    @staticmethod
    async def _apply(library, emit, run_id):
        """Run an apply; answer ``(unit_name, rom_id, name)`` for every shortcut it sent."""
        box = library.sync._box
        box.discard_preview()
        assert box.try_begin_run(run_id, kind=SyncRunKind.APPLY) is True
        start = len(emit.call_args_list)
        await library.sync._orchestrator._do_sync_per_unit()
        return [
            (call.args[1]["unit_name"], shortcut["rom_id"], shortcut["name"])
            for call in emit.call_args_list[start:]
            if call.args and call.args[0] == "sync_apply_unit"
            for shortcut in call.args[1]["shortcuts"]
        ]

    @staticmethod
    def _bound(library):
        with library.uow as uow:
            return {rom.rom_id: rom.shortcut_app_id for rom in uow.roms.iter_all() if rom.shortcut_app_id is not None}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "collections",
        [[(7, "First", [23]), (8, "Second", [22])], [(7, "First", [22]), (8, "Second", [23])]],
        ids=["japan-first", "europe-first"],
    )
    async def test_the_apply_binds_the_best_ranked_version_the_preview_names(
        self, make_library, fake_romm_api, emit, collections
    ):
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, collections)

        answer = await library.sync.sync_preview()
        sent = await self._apply(library, emit, "run-1")

        assert answer["new_names"] == ["Golf (Europe)"]
        assert [(rom_id, name) for _unit, rom_id, name in sent] == [(22, "Golf (Europe)")]
        assert self._bound(library) == {22: 6022}

    @pytest.mark.asyncio
    async def test_versions_only_romm_s_sibling_list_links_are_one_game_across_collections(
        self, make_library, fake_romm_api, emit
    ):
        """D4: the component keys are stamped over every collection's delivery at once, as the preview's union is."""
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, [(7, "First", [23]), (8, "Second", [22])])
        fake_romm_api.roms[22]["sibling_roms"] = [{"id": 23}]
        fake_romm_api.roms[23].update(igdb_id=None, sibling_roms=[{"id": 22}])

        answer = await library.sync.sync_preview()
        sent = await self._apply(library, emit, "run-1")

        assert answer["summary"]["new_count"] == 1
        assert [(rom_id, name) for _unit, rom_id, name in sent] == [(22, "Golf (Europe)")]
        assert self._bound(library) == {22: 6022}

    @pytest.mark.asyncio
    async def test_a_version_linked_to_an_unbound_row_joins_its_game_as_in_the_preview(
        self, make_library, fake_romm_api, emit
    ):
        """D4: the resident keys are the preview's, unbound rows' included."""
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, [(7, "First", [22]), (8, "Second", [23])])
        _seed_persisted_rom(library.uow, 21, app_id=None, group_key="igdb:300:2", platform_slug="gba")
        fake_romm_api.roms[23].update(igdb_id=None, sibling_roms=[{"id": 21}])

        answer = await library.sync.sync_preview()
        sent = await self._apply(library, emit, "run-1")

        assert answer["summary"]["new_count"] == 1
        assert [(rom_id, name) for _unit, rom_id, name in sent] == [(22, "Golf (Europe)")]
        assert self._bound(library) == {22: 6022}

    @pytest.mark.asyncio
    async def test_an_installed_version_a_later_collection_delivers_is_bound(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, [(7, "First", [22]), (8, "Second", [23])])
        _seed_install(library, 23, file_path="/roms/gba/Golf (Japan).zip", platform_slug="gba")

        answer = await library.sync.sync_preview()
        sent = await self._apply(library, emit, "run-1")

        assert answer["new_names"] == ["Golf (Europe)"]
        assert [(rom_id, name) for _unit, rom_id, name in sent] == [(23, "Golf (Europe)")]
        assert self._bound(library) == {23: 6023}

    @pytest.mark.asyncio
    async def test_romm_s_default_version_a_later_collection_delivers_is_bound(self, make_library, fake_romm_api, emit):
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, [(7, "First", [22]), (8, "Second", [23])], main=23)

        answer = await library.sync.sync_preview()
        sent = await self._apply(library, emit, "run-1")

        assert answer["new_names"] == ["Golf (Europe)"]
        assert [(rom_id, name) for _unit, rom_id, name in sent] == [(23, "Golf (Europe)")]
        assert self._bound(library) == {23: 6023}

    @pytest.mark.asyncio
    async def test_a_run_stopped_after_the_first_collection_has_bound_the_best_version(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, [(7, "First", [23]), (8, "Second", [22])])
        box = library.sync._box

        async def bind_then_cancel(_unit, event):
            event.set()
            box.request_cancel(None)
            return {str(rid): 6000 + rid for rid in box.pending_sync}

        library.chunk_dispatcher._wait_for_unit_complete = bind_then_cancel

        sent = await self._apply(library, emit, "run-1")

        assert [unit for unit, _rom_id, _name in sent] == ["First"]
        assert self._bound(library) == {22: 6022}

    @pytest.mark.asyncio
    async def test_a_binding_made_before_the_other_version_was_delivered_is_kept(
        self, make_library, fake_romm_api, emit
    ):
        """D2: the group already holds a binding, so neither its preview nor its apply moves it."""
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, [(7, "First", [23]), (8, "Second", [22])], enabled=[7])
        await self._apply(library, emit, "run-1")
        assert self._bound(library) == {23: 6023}
        library.settings["enabled_collections"] = {"standard": {"7": True, "8": True}}

        answer = await library.sync.sync_preview()
        sent = await self._apply(library, emit, "run-2")

        assert (answer["summary"]["new_count"], answer["summary"]["changed_count"]) == (0, 0)
        assert sent == []
        assert self._bound(library) == {23: 6023}

    @pytest.mark.asyncio
    async def test_a_cancel_while_the_collections_are_fetched_applies_no_collection(
        self, make_library, fake_romm_api, emit, monkeypatch
    ):
        """D3: cancelled during the second collection's fetch, no collection applies; the platform unit stays."""
        library = make_library(romm_api=fake_romm_api)
        _seed_platform(fake_romm_api, platform_id=1, name="N64", slug="n64", roms=[{"id": 1, "name": "Tennis"}])
        library.settings["enabled_platforms"] = {"1": True}
        self._seed(library, fake_romm_api, [(7, "First", [23]), (8, "Second", [22])])
        box = library.sync._box
        walk = fake_romm_api.list_roms_by_collection

        def cancel_once_second_walked(collection_id, *args, **kwargs):
            answer = walk(collection_id, *args, **kwargs)
            if collection_id == 8:
                box.sync_state = SyncState.CANCELLING
            return answer

        monkeypatch.setattr(fake_romm_api, "list_roms_by_collection", cancel_once_second_walked)

        sent = await self._apply(library, emit, "run-1")

        assert [unit for unit, _rom_id, _name in sent] == ["N64"]
        assert self._bound(library) == {1: 6001}
        complete = [call.args[1] for call in emit.call_args_list if call.args and call.args[0] == "sync_complete"]
        assert [payload.get("cancelled") for payload in complete] == [True]

    @pytest.mark.asyncio
    async def test_the_first_collection_s_row_runs_while_every_collection_is_fetched(
        self, make_library, fake_romm_api, emit, monkeypatch
    ):
        """D3: frames hold the first collection's step and never step back; it applies once both are fetched."""
        library = make_library(romm_api=fake_romm_api)
        self._seed(library, fake_romm_api, [(7, "First", [23]), (8, "Second", [22])])
        walk = fake_romm_api.list_roms_by_collection
        applies_at_walk: list[int] = []

        def count_applies_then_walk(*args, **kwargs):
            applies_at_walk.append(sum(1 for c in emit.call_args_list if c.args and c.args[0] == "sync_apply_unit"))
            return walk(*args, **kwargs)

        monkeypatch.setattr(fake_romm_api, "list_roms_by_collection", count_applies_then_walk)

        await self._apply(library, emit, "run-1")

        names = [c.args[0] if c.args else None for c in emit.call_args_list]
        first_apply = names.index("sync_apply_unit")
        before = [
            c.args[1]
            for c in emit.call_args_list[:first_apply]
            if c.args and c.args[0] == "sync_progress" and c.args[1].get("running")
        ]
        assert {frame["step"] for frame in before} == {1}
        fills = [self._row_fill(frame) for frame in before]
        assert fills == sorted(fills)
        assert applies_at_walk == [0, 0]

    @staticmethod
    def _row_fill(frame):
        """Where a frame puts its row's bar, in the order ``withinUnitFraction`` fills it.

        That order is the frontend's (``frontend/src/utils/syncProgress.ts``):
        the fetch slice, then the covers slice, then the apply slice; a
        ``fetching`` frame with no sub-stage rests at the row's start.
        """
        part = frame["current"] / frame["total"] if frame.get("current") and frame.get("total") else 0
        if frame["stage"] == "applying":
            return 2 + part
        if frame.get("subStage") == "covers":
            return 1 + part
        if frame.get("subStage") == "fetch":
            return part
        return 0
