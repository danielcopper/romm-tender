"""Tests for SyncReporter — post-apply roms upserts, finalisation, registry queries."""

import json
import os

import pytest
from fakes.fake_cover_art_file_store import FakeCoverArtFileStore

from domain.rom import Rom
from domain.sync_diff import BIND_ROM_ID_KEY
from services.library._state import CollectionMembership


def _seed_rom(
    uow, rom_id, *, app_id, platform_slug, name="Game", cover_path=None, sgdb_id=None, igdb_id=None, group_key=None
):
    """Insert a bound (or unbound when app_id is None) ROM into the shared fake UoW."""
    rom = Rom(
        rom_id=rom_id,
        platform_slug=platform_slug,
        name=name,
        fs_name=f"{name}.z64",
        shortcut_app_id=app_id,
        last_synced_at="2025-01-01T00:00:00",
        cover_path=cover_path,
        sgdb_id=sgdb_id,
        igdb_id=igdb_id,
        sibling_group_key=group_key,
    )
    with uow:
        uow.roms.save(rom)


def _stamp_fetch(uow, slug: str, *, rom_count: int, fetch_id: str | None, seen: list[int]) -> None:
    """Record a completed fetch of *slug* and mark which of its rows it returned.

    ``seen`` is the rom_ids that fetch came back with; every other row of the
    platform keeps whatever generation it had, which is how a dropped id is told
    apart from a current one without deleting anything.
    """
    from domain.platform_sync_state import PlatformSyncState

    with uow:
        for rom_id in seen:
            rom = uow.roms.get(rom_id)
            if fetch_id is not None:
                rom.record_fetch_generation(fetch_id)
            uow.roms.save(rom)
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(
                platform_slug=slug, at="2026-01-01T00:00:00", rom_count=rom_count, fetch_id=fetch_id
            )
        )


def _seed_platform_names(uow, names: dict[str, str]) -> None:
    """Seed the offline ``platform_slug → display_name`` cache."""
    with uow:
        uow.kv_config.set("platform_names", json.dumps(names))


def _stage(box, rom_id, entry, *, emitted=True):
    """Stage a fetched ROM's built entry for the group-aware per-unit commit.

    ``pending_all_roms`` is the identity + version source for EVERY fetched ROM;
    ``pending_sync`` holds the emitted representatives (cover-path + bind_rom_id
    marker). A bound representative appears in both; a non-representative sibling
    is staged only in ``pending_all_roms`` (``emitted=False``) so the commit
    persists it unbound.
    """
    box.pending_all_roms[rom_id] = entry
    if emitted:
        box.pending_sync[rom_id] = entry


class TestGetSyncStats:
    @pytest.mark.asyncio
    async def test_computes_from_registry(self, library):
        from domain.sync_run import SyncRun

        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64", name="Game A")
        _seed_rom(uow, 20, app_id=1002, platform_slug="n64", name="Game B")
        _seed_rom(uow, 30, app_id=1003, platform_slug="snes", name="Game C")
        run = SyncRun.start(id="run-1", at="2025-01-01T00:00:00", platforms_planned=2, roms_planned=3)
        run.complete("2025-01-01T00:00:00", ["N64", "SNES"], [])
        with uow:
            uow.sync_runs.save(run)
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        library.settings["enabled_collections"] = {
            "standard": {"3": True},
            "smart": {"5": True},
            "virtual": {"abc": False},  # disabled — not counted
        }

        stats = library.sync.get_sync_stats()
        assert stats["platforms"] == 2
        # 3 enabled across two buckets (user["3"], smart["5"]); virtual["abc"] is False.
        assert stats["collections"] == 2
        assert stats["roms"] == 3
        assert stats["total_shortcuts"] == 3
        assert stats["last_sync"] == "2025-01-01T00:00:00"
        # Only a completed run exists — no separate attempt to surface.
        assert stats["last_attempt"] is None

    @pytest.mark.asyncio
    async def test_empty_registry(self, library):
        stats = library.sync.get_sync_stats()
        assert stats["platforms"] == 0
        assert stats["roms"] == 0
        assert stats["total_shortcuts"] == 0
        assert stats["last_sync"] is None
        assert stats["last_attempt"] is None

    @pytest.mark.asyncio
    async def test_excludes_unbound_roms_from_count(self, library):
        """Stats count only bound ROMs — unbound (stale) rows do not inflate the total."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64", name="Game A")
        _seed_rom(uow, 20, app_id=None, platform_slug="snes", name="Game B (stale)")

        stats = library.sync.get_sync_stats()
        assert stats["roms"] == 1
        assert stats["total_shortcuts"] == 1

    @pytest.mark.asyncio
    async def test_report_removal_unbinds_roms_so_stats_drop(self, library):
        """report_removal_results unbinds the ROMs; derived get_sync_stats then counts zero."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64", name="Game A")
        _seed_rom(uow, 20, app_id=1002, platform_slug="snes", name="Game B")

        await library.shortcut_removal.report_removal_results([10, 20], None)

        stats = library.sync.get_sync_stats()
        assert stats["roms"] == 0
        assert stats["total_shortcuts"] == 0
        # Rows survive (ADR-0007): they're unbound, not deleted.
        with uow:
            assert uow.roms.get(10).shortcut_app_id is None
            assert uow.roms.get(20).shortcut_app_id is None


class TestGetSyncStatsLastAttempt:
    """last_attempt — surface a cancelled/crashed run so 'Last sync' isn't 'Never' (#1367-class)."""

    @staticmethod
    def _cancelled(uow, *, id, started, finished, reason="Sync cancelled"):
        from domain.sync_run import SyncRun

        run = SyncRun.start(id=id, at=started, platforms_planned=1, roms_planned=1)
        run.mark_cancelled(finished, reason)
        with uow:
            uow.sync_runs.save(run)

    @staticmethod
    def _completed(uow, *, id, started, finished):
        from domain.sync_run import SyncRun

        run = SyncRun.start(id=id, at=started, platforms_planned=1, roms_planned=1)
        run.complete(finished, ["N64"], [])
        with uow:
            uow.sync_runs.save(run)

    @staticmethod
    def _interrupted(uow, *, id, started, finished, reason="Sync interrupted (Steam UI stopped responding)"):
        from domain.sync_run import SyncRun

        run = SyncRun.start(id=id, at=started, platforms_planned=1, roms_planned=1)
        run.mark_interrupted(finished, reason)
        with uow:
            uow.sync_runs.save(run)

    @staticmethod
    def _paused(uow, *, id, started, finished, reason="Sync paused: Steam's memory is nearly full."):
        from domain.sync_run import SyncRun

        run = SyncRun.start(id=id, at=started, platforms_planned=1, roms_planned=1)
        run.mark_paused(finished, reason)
        with uow:
            uow.sync_runs.save(run)

    @pytest.mark.asyncio
    async def test_no_runs_reports_no_attempt(self, library):
        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] is None
        assert stats["last_attempt"] is None

    @pytest.mark.asyncio
    async def test_only_cancelled_run_surfaces_attempt(self, library):
        """A cancelled run with no completed run ever → last_sync None, last_attempt set."""
        self._cancelled(library.uow, id="run-c", started="2025-06-01T17:00:00", finished="2025-06-01T17:48:00")

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] is None
        assert stats["last_attempt"] == {"finished_at": "2025-06-01T17:48:00", "status": "cancelled"}

    @pytest.mark.asyncio
    async def test_errored_run_surfaces_attempt_with_errored_status(self, library):
        run_uow = library.uow
        from domain.sync_run import SyncRun

        run = SyncRun.start(id="run-e", at="2025-06-01T10:00:00", platforms_planned=1, roms_planned=1)
        run.mark_errored("2025-06-01T10:05:00", "boom")
        with run_uow:
            run_uow.sync_runs.save(run)

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] is None
        assert stats["last_attempt"] == {"finished_at": "2025-06-01T10:05:00", "status": "errored"}

    @pytest.mark.asyncio
    async def test_cancelled_newer_than_completed_surfaces_attempt(self, library):
        """A cancelled run newer than the last completed one → both surface."""
        self._completed(library.uow, id="run-ok", started="2025-06-01T09:00:00", finished="2025-06-01T09:30:00")
        self._cancelled(library.uow, id="run-c", started="2025-06-02T08:00:00", finished="2025-06-02T08:20:00")

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] == "2025-06-01T09:30:00"
        assert stats["last_attempt"] == {"finished_at": "2025-06-02T08:20:00", "status": "cancelled"}

    @pytest.mark.asyncio
    async def test_interrupted_newer_than_completed_surfaces_attempt(self, library):
        """An interrupted run (external death) newer than the last completed one →
        last_attempt carries the 'interrupted' status (get_latest_terminal must
        include interrupted, or this run would be invisible to the hint)."""
        self._completed(library.uow, id="run-ok", started="2025-06-01T09:00:00", finished="2025-06-01T09:30:00")
        self._interrupted(library.uow, id="run-i", started="2025-06-02T08:00:00", finished="2025-06-02T08:20:00")

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] == "2025-06-01T09:30:00"
        assert stats["last_attempt"] == {"finished_at": "2025-06-02T08:20:00", "status": "interrupted"}

    @pytest.mark.asyncio
    async def test_completed_newer_than_cancelled_hides_attempt(self, library):
        """A clean run after a cancelled one → last_sync only, no stale attempt line."""
        self._cancelled(library.uow, id="run-c", started="2025-06-01T08:00:00", finished="2025-06-01T08:20:00")
        self._completed(library.uow, id="run-ok", started="2025-06-02T09:00:00", finished="2025-06-02T09:30:00")

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] == "2025-06-02T09:30:00"
        assert stats["last_attempt"] is None

    @pytest.mark.asyncio
    async def test_paused_newer_than_completed_surfaces_resumable_attempt(self, library):
        """A session-budget 'paused' run newer than the last completed one → last_attempt
        carries the 'paused' status (get_latest_terminal must include paused, or the
        run — and the Resume Sync affordance it drives — would be invisible, #1383)."""
        self._completed(library.uow, id="run-ok", started="2025-07-11T09:00:00", finished="2025-07-11T09:30:00")
        self._paused(library.uow, id="run-p", started="2025-07-11T10:00:00", finished="2025-07-11T10:20:00")

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] == "2025-07-11T09:30:00"
        assert stats["last_attempt"] == {"finished_at": "2025-07-11T10:20:00", "status": "paused"}


class TestGetRegistryPlatforms:
    @pytest.mark.asyncio
    async def test_returns_platforms_from_registry(self, library):
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64", name="Mario 64")
        _seed_rom(uow, 20, app_id=1002, platform_slug="n64", name="Zelda OOT")
        _seed_rom(uow, 30, app_id=1003, platform_slug="snes", name="DKC")
        # Live name cache resolves slugs → display names.
        _seed_platform_names(uow, {"n64": "Nintendo 64", "snes": "Super Nintendo"})

        result = library.sync.get_registry_platforms()
        assert len(result["platforms"]) == 2
        # Sorted by display name
        assert result["platforms"][0]["name"] == "Nintendo 64"
        assert result["platforms"][0]["slug"] == "n64"
        assert result["platforms"][0]["count"] == 2
        assert result["platforms"][1]["name"] == "Super Nintendo"
        assert result["platforms"][1]["slug"] == "snes"
        assert result["platforms"][1]["count"] == 1

    @pytest.mark.asyncio
    async def test_empty_registry(self, library):
        result = library.sync.get_registry_platforms()
        assert result["platforms"] == []

    @pytest.mark.asyncio
    async def test_excludes_unbound_roms(self, library):
        """Unbound (stale) rows are not surfaced as registry platforms."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64", name="Bound")
        _seed_rom(uow, 20, app_id=None, platform_slug="snes", name="Unbound")
        _seed_platform_names(uow, {"n64": "Nintendo 64", "snes": "Super Nintendo"})

        result = library.sync.get_registry_platforms()
        assert len(result["platforms"]) == 1
        assert result["platforms"][0]["slug"] == "n64"

    @pytest.mark.asyncio
    async def test_degrades_to_slug_when_name_cache_absent(self, library):
        """Offline / no cache → the display name degrades to the slug."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64", name="Mario 64")

        result = library.sync.get_registry_platforms()
        assert len(result["platforms"]) == 1
        assert result["platforms"][0]["name"] == "n64"
        assert result["platforms"][0]["slug"] == "n64"
        assert result["platforms"][0]["count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("blob", ["not json at all {", '"a json string, not a dict"', "[1, 2, 3]"])
    async def test_degrades_to_slug_when_name_cache_corrupt(self, library, blob):
        """A corrupt / non-dict ``platform_names`` blob decodes to ``{}`` so the
        display name degrades to the slug (bad-path for the decode guard)."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64", name="Mario 64")
        with uow:
            uow.kv_config.set("platform_names", blob)

        result = library.sync.get_registry_platforms()
        assert len(result["platforms"]) == 1
        assert result["platforms"][0]["name"] == "n64"
        assert result["platforms"][0]["slug"] == "n64"


class TestRegistryPlatformsReachableCount:
    """``reachable_count`` counts ROMs a shortcut can reach, never shortcuts."""

    @pytest.mark.asyncio
    async def test_a_groups_unbound_versions_count_with_its_binding(self, library):
        """One game, three versions, one shortcut — all three are reachable.

        The two the binding did not go to are reached by switching version on
        the one it did, so counting bindings here would report them as absent
        from Steam.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="sms", group_key="igdb:1:2")
        _seed_rom(uow, 11, app_id=None, platform_slug="sms", group_key="igdb:1:2")
        _seed_rom(uow, 12, app_id=None, platform_slug="sms", group_key="igdb:1:2")

        entry = library.sync.get_registry_platforms()["platforms"][0]
        assert entry["count"] == 1
        assert entry["reachable_count"] == 3

    @pytest.mark.asyncio
    async def test_a_group_with_no_binding_reaches_nothing(self, library):
        """A partly-applied platform: the two numbers differ and the gap is real.

        The second game was fetched and never applied, so no shortcut anywhere
        leads to either of its versions.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="gba", group_key="igdb:1:2")
        _seed_rom(uow, 11, app_id=None, platform_slug="gba", group_key="igdb:1:2")
        _seed_rom(uow, 20, app_id=None, platform_slug="gba", group_key="igdb:9:2")
        _seed_rom(uow, 21, app_id=None, platform_slug="gba", group_key="igdb:9:2")

        entry = library.sync.get_registry_platforms()["platforms"][0]
        assert entry["count"] == 1
        assert entry["reachable_count"] == 2

    @pytest.mark.asyncio
    async def test_a_null_group_key_is_its_own_group(self, library):
        """A NULL key relates no rows, so one binding speaks only for its own row.

        The key was never computed for either row. Folding them together on
        that shared absence would let the bound one carry the unbound one into
        the count, which is the whole platform reading reachable off one
        applied game.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="nes", group_key=None)
        _seed_rom(uow, 11, app_id=None, platform_slug="nes", group_key=None)

        entry = library.sync.get_registry_platforms()["platforms"][0]
        assert entry["count"] == 1
        assert entry["reachable_count"] == 1

    @pytest.mark.asyncio
    async def test_a_version_the_last_fetch_did_not_return_is_not_counted(self, library):
        """RomM dropped a version; its row stays reachable and stops counting.

        Nothing deletes it — ADR-0007 keeps the row as an identity anchor and
        only the cleanup flow removes one — and the group's shortcut still
        reaches it, but RomM no longer serves it. Counting it was the header
        claiming a version is in Steam that RomM has stopped serving.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="dc", group_key="igdb:1:2")
        _seed_rom(uow, 11, app_id=None, platform_slug="dc", group_key="igdb:1:2")
        _stamp_fetch(uow, "dc", rom_count=2, fetch_id="fetch-1", seen=[10, 11])
        _stamp_fetch(uow, "dc", rom_count=1, fetch_id="fetch-2", seen=[10])

        entry = library.sync.get_registry_platforms()["platforms"][0]
        assert entry["count"] == 1
        assert entry["reachable_count"] == 1

    @pytest.mark.asyncio
    async def test_a_revoked_stamp_still_rules_out_what_its_fetch_did_not_return(self, library):
        """A revoked skip says nothing about what RomM served, so the exclusion stands."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="dc", group_key="igdb:1:2")
        _seed_rom(uow, 11, app_id=None, platform_slug="dc", group_key="igdb:1:2")
        _stamp_fetch(uow, "dc", rom_count=2, fetch_id="fetch-1", seen=[10, 11])
        _stamp_fetch(uow, "dc", rom_count=1, fetch_id="fetch-2", seen=[10])
        with uow:
            uow.platform_sync_state.revoke_skip("dc")

        entry = library.sync.get_registry_platforms()["platforms"][0]
        assert entry["reachable_count"] == 1

    @pytest.mark.asyncio
    async def test_a_group_still_reaches_its_survivors_when_the_binding_vanished(self, library):
        """The exclusion is from the COUNT, never from the grouping.

        The shortcut exists whether or not the version it binds is still on the
        server, so the group's other versions are still reached through it.
        Filtering the dropped row out before grouping would leave a group with
        no binding at all and report nothing reachable, which is a shortcut the
        reader can plainly see.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="dc", group_key="igdb:1:2")
        _seed_rom(uow, 11, app_id=None, platform_slug="dc", group_key="igdb:1:2")
        # The BOUND row is the one the fetch did not return.
        _stamp_fetch(uow, "dc", rom_count=1, fetch_id="fetch-2", seen=[11])

        entry = library.sync.get_registry_platforms()["platforms"][0]
        assert entry["count"] == 1
        assert entry["reachable_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stamp_args",
        [
            None,
            {"rom_count": 1, "fetch_id": None},
            {"rom_count": 0, "fetch_id": "fetch-2"},
        ],
        ids=["no stamp", "no generation", "empty fetch"],
    )
    async def test_a_platform_with_no_usable_stamp_keeps_every_row(self, library, stamp_args):
        """Discovery only, never deletion authority — and never a silent drop.

        A stamp that is missing, carries no generation, or recorded an empty
        fetch cannot establish what the server returned, so nothing is ruled out
        and the count is what it was before the exclusion existed. That fallback
        is what makes the exclusion safe: its worst case is the number already
        printed.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="dc", group_key="igdb:1:2")
        _seed_rom(uow, 11, app_id=None, platform_slug="dc", group_key="igdb:1:2")
        if stamp_args is not None:
            _stamp_fetch(uow, "dc", seen=[10], **stamp_args)

        entry = library.sync.get_registry_platforms()["platforms"][0]
        assert entry["reachable_count"] == 2


class TestGetRomBySteamAppId:
    @pytest.mark.asyncio
    async def test_finds_rom_by_app_id_installed(self, library):
        from domain.rom_install import RomInstall

        uow = library.uow
        _seed_rom(uow, 42, app_id=100001, platform_slug="n64", name="Zelda")
        _seed_platform_names(uow, {"n64": "Nintendo 64"})
        with uow:
            uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=42,
                    file_path="/roms/n64/zelda.z64",
                    rom_dir=None,
                    platform_slug="n64",
                    system="n64",
                    installed_at="2025-01-01T00:00:00",
                )
            )
        result = library.sync.get_rom_by_steam_app_id(100001)
        assert result is not None
        assert result["rom_id"] == 42
        assert result["name"] == "Zelda"
        assert result["platform_name"] == "Nintendo 64"
        assert result["platform_slug"] == "n64"
        assert result["installed"] is True

    @pytest.mark.asyncio
    async def test_finds_rom_by_app_id_not_installed(self, library):
        """A bound ROM with no install record reports ``installed`` False."""
        uow = library.uow
        _seed_rom(uow, 42, app_id=100001, platform_slug="n64", name="Zelda")
        _seed_platform_names(uow, {"n64": "Nintendo 64"})

        result = library.sync.get_rom_by_steam_app_id(100001)
        assert result is not None
        assert result["installed"] is False

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown(self, library):
        result = library.sync.get_rom_by_steam_app_id(999999)
        assert result is None


class TestFinalizeCoverPath:
    """Tests for _finalize_cover_path() — publishes the cache cover onto the grid."""

    def test_copies_cache_to_final_and_persists_cache_path(self, library, tmp_path):
        grid_path = tmp_path / "grid"
        grid_path.mkdir()
        grid = str(grid_path)
        cache = tmp_path / "covers" / "1.png"
        cache.parent.mkdir(parents=True)
        cache.write_text("cover data")

        result = library.sync._reporter._finalize_cover_path(grid, str(cache), 100001, "1")
        expected_final = os.path.join(grid, "100001p.png")
        # The persisted path is the CACHE path; the grid gets a copy (cache survives).
        assert result == str(cache)
        assert cache.exists()
        assert os.path.exists(expected_final)

    def test_returns_existing_final_when_cover_missing(self, library, tmp_path):
        grid = str(tmp_path)
        final = tmp_path / "100001p.png"
        final.write_text("final data")

        result = library.sync._reporter._finalize_cover_path(grid, "/nonexistent/path.png", 100001, "1")
        assert result == str(final)

    def test_returns_cover_path_when_no_grid(self, library):
        result = library.sync._reporter._finalize_cover_path(None, "/some/path.png", 100001, "1")
        assert result == "/some/path.png"

    def test_returns_cover_path_when_empty(self, library, tmp_path):
        result = library.sync._reporter._finalize_cover_path(str(tmp_path), "", 100001, "1")
        assert result == ""

    def test_handles_copy_os_error(self, library, tmp_path):
        grid = str(tmp_path / "grid")
        cache_path = os.path.join(str(tmp_path / "covers"), "1.png")

        # Inject OSError on copy_file through the CoverArtFileStore Protocol —
        # mirrors the Wave 3 fake-adapter failure-injection pattern instead
        # of patching ``shutil.copyfile`` globally.
        fake_store = FakeCoverArtFileStore(files={cache_path: b"data"})
        fake_store.copy_failures.add(cache_path)
        library.artwork._cover_art_file_store = fake_store

        result = library.sync._reporter._finalize_cover_path(grid, cache_path, 100001, "1")
        # A failed copy still returns the cache path (persist unchanged).
        assert result == cache_path


class TestCommitUnitResults:
    """Tests for _commit_unit_results_io — per-unit ``roms`` upsert via ``Rom.synced``."""

    def test_commit_upserts_rom_from_pending(self, library):
        """A unit's acked ROM is upserted into ``uow.roms`` from its pending entry."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_name": "Game Boy",
                "platform_slug": "gb",
                "cover_path": "",
                "igdb_id": 555,
                "sgdb_id": 999,
                "ra_id": 777,
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        assert uow.committed is True
        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.shortcut_app_id == 100001
        assert rom.name == "Game"
        assert rom.fs_name == "game.z64"
        assert rom.platform_slug == "gb"
        assert rom.igdb_id == 555
        assert rom.sgdb_id == 999
        assert rom.ra_id == 777

    def test_commit_records_applied_launch_options_for_binding_target(self, library):
        """A binding target this cycle records the launch command the frontend wrote
        onto the shortcut, so the next sync skips the now-correct shortcut (#1383)."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "n64",
                "cover_path": "",
                "launch_options": "flatpak run net.retrodeck.retrodeck /game.z64",
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.applied_launch_options == "flatpak run net.retrodeck.retrodeck /game.z64"

    def test_commit_preserves_applied_for_unacked_row(self, library):
        """A row committed this chunk but NOT acked (a skipped-unchanged item riding
        chunk 0's leftover) keeps its binding AND its recorded applied state — save()
        excludes applied_launch_options, so the un-re-acked value is never wiped."""
        uow = library.uow
        _seed_rom(uow, 43, app_id=100043, platform_slug="n64", name="Keep")
        with uow:
            uow.roms.set_applied_launch_options(43, "flatpak run … /keep.z64")
        _stage(
            library.sync._box,
            43,
            {"name": "Keep", "fs_name": "keep.z64", "platform_slug": "n64", "cover_path": "", "launch_options": ""},
            emitted=False,
        )

        # Empty ack — rom 43 is not a binding target this cycle, but its row commits.
        library.sync._reporter._commit_unit_results_io({}, [{"id": 43}])

        with uow:
            rom = uow.roms.get(43)
        assert rom is not None
        assert rom.shortcut_app_id == 100043
        assert rom.applied_launch_options == "flatpak run … /keep.z64"

    def test_commit_stamps_confirmed_cover_source(self, library):
        """A fingerprint the artwork layer confirmed for this unit (staged in
        ``pending_cover_sources``) is persisted on the upserted row (#1386)."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )
        library.sync._box.pending_cover_sources = {42: "/cover/big.png?ts=2026-07-11 12:00:00"}

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.cover_source == "/cover/big.png?ts=2026-07-11 12:00:00"

    def test_commit_preserves_existing_cover_source_when_unconfirmed(self, library):
        """A row whose cover was NOT confirmed this unit (failed download, or a
        sibling the download never touched) keeps its persisted fingerprint —
        the fresh fetch string is never blindly stamped, so the change is
        retried next sync (#1386)."""
        uow = library.uow
        _seed_rom(uow, 42, app_id=100001, platform_slug="n64", name="Game")
        with uow:
            rom = uow.roms.get(42)
            rom.adopt_cover_source("/cover/big.png?ts=2026-01-01 00:00:00")
            uow.roms.save(rom)
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )
        assert library.sync._box.pending_cover_sources == {}

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.cover_source == "/cover/big.png?ts=2026-01-01 00:00:00"

    def test_commit_stamps_confirmed_source_for_unacked_row(self, library):
        """The fingerprint records the CACHE state, not applied frontend state:
        a confirmed download whose shortcut was never acked still persists
        (the cache file was written regardless of the ack)."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )
        library.sync._box.pending_cover_sources = {42: "/cover/big.png?ts=2026-07-11 12:00:00"}

        # Empty ack — rom 42's shortcut never landed, its row commits unbound.
        library.sync._reporter._commit_unit_results_io({}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.shortcut_app_id is None
        assert rom.cover_source == "/cover/big.png?ts=2026-07-11 12:00:00"

    def test_commit_persists_platform_stamp_atomically(self, library):
        """A passed ``platform_stamp`` lands in the SAME committed UoW as the rom
        upsert — the per-platform completion stamp is atomic with the chunk (ADR-0023)."""
        from domain.platform_sync_state import PlatformSyncState

        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )
        stamp = PlatformSyncState.stamp(platform_slug="n64", at="2026-01-01T00:00:00+00:00", rom_count=7)

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}], stamp)

        assert uow.committed is True
        with uow:
            assert uow.roms.get(42) is not None  # chunk rom upserted
            loaded = uow.platform_sync_state.get("n64")
        assert loaded is not None
        assert loaded.rom_count == 7
        assert loaded.completed_at == "2026-01-01T00:00:00+00:00"

    def test_commit_without_stamp_writes_no_platform_state(self, library):
        """A commit with the default ``platform_stamp=None`` (non-final chunk, or a
        collection/late-ack path) leaves ``platform_sync_state`` untouched."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            assert uow.platform_sync_state.get("n64") is None

    def test_commit_marks_every_upserted_row_with_the_fetch_generation(self, library):
        """A platform unit's commit stamps the generation onto EVERY row it upserts,
        so the stamp can later count exactly the rows its fetch returned (#1504)."""
        uow = library.uow
        for rom_id in (10, 11):
            _stage(
                library.sync._box,
                rom_id,
                {"name": f"G{rom_id}", "fs_name": f"g{rom_id}.z64", "platform_slug": "n64", "cover_path": ""},
            )

        library.sync._reporter._commit_unit_results_io({"10": 9001}, [{"id": 10}, {"id": 11}], None, None, "run-new")

        with uow:
            bound = uow.roms.get(10)
            unbound = uow.roms.get(11)
        assert bound is not None
        assert unbound is not None
        # The unbound sibling carries it too — it counts toward the platform total.
        assert bound.last_fetch_id == "run-new"
        assert unbound.last_fetch_id == "run-new"

    def test_commit_writes_the_same_generation_the_stamp_records(self, library):
        """Row generation and stamp generation come from one value, so the skip's
        two sides always agree (#1504)."""
        from domain.platform_sync_state import PlatformSyncState

        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )
        stamp = PlatformSyncState.stamp(
            platform_slug="n64", at="2026-01-01T00:00:00+00:00", rom_count=1, fetch_id="run-new"
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}], stamp, None, "run-new")

        with uow:
            rom = uow.roms.get(42)
            loaded = uow.platform_sync_state.get("n64")
        assert rom is not None
        assert loaded is not None
        assert rom.last_fetch_id == loaded.fetch_id == "run-new"

    def test_collection_commit_preserves_a_foreign_platforms_generation(self, library):
        """A collection spans platforms, so its commit must NOT re-mark a member's
        row: re-stamping would drop that row from its own platform's counted rows
        and suppress that platform's skip (#1504)."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )
        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}], None, None, "run-new")

        # A later collection unit re-commits the same ROM with no generation.
        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}], None, None, None)

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.last_fetch_id == "run-new"

    def test_commit_persists_collection_stamp_atomically(self, library):
        """A passed ``collection_stamp`` lands in the SAME committed UoW as the rom
        upsert — the per-collection completion stamp is atomic with the chunk (#742)."""
        from domain.collection_sync_state import CollectionSyncState

        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )
        stamp = CollectionSyncState.stamp(
            collection_id="7",
            collection_kind="standard",
            updated_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:05:00+00:00",
            rom_count=1,
            member_rom_ids=(42,),
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}], None, stamp)

        assert uow.committed is True
        with uow:
            assert uow.roms.get(42) is not None  # chunk rom upserted
            loaded = uow.collection_sync_state.get("7", "standard")
        assert loaded is not None
        assert loaded.rom_count == 1
        assert loaded.member_rom_ids == (42,)
        assert loaded.completed_at == "2026-01-01T00:05:00+00:00"

    def test_commit_without_collection_stamp_writes_no_collection_state(self, library):
        """The default ``collection_stamp=None`` (platform / non-final / late-ack path)
        leaves ``collection_sync_state`` untouched."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "n64", "cover_path": ""},
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            assert uow.collection_sync_state.get("7", "standard") is None

    def test_commit_persists_version_metadata_from_pending(self, library):
        """The sibling-group key + version dimensions ride the pending entry onto
        the upserted ``Rom`` (#1295)."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "gb",
                "cover_path": "",
                "sibling_group_key": "igdb:3404:57",
                "regions": ["USA", "Europe"],
                "languages": ["En"],
                "revision": "1",
                "tags": ["Demo"],
                "is_main_sibling": True,
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.sibling_group_key == "igdb:3404:57"
        assert rom.regions == ("USA", "Europe")
        assert rom.languages == ("En",)
        assert rom.revision == "1"
        assert rom.tags == ("Demo",)
        assert rom.is_main_sibling is True

    def test_commit_defaults_version_metadata_when_pending_omits_it(self, library):
        """A pending entry with no version fields upserts a Rom carrying the
        aggregate defaults — never raises on the missing keys."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "gb",
                "cover_path": "",
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.sibling_group_key is None
        assert rom.regions == ()
        assert rom.revision == ""
        assert rom.is_main_sibling is False

    def test_commit_persists_fs_size_bytes_from_pending(self, library):
        """The server-reported ROM size (#1395) rides the pending entry onto the
        upserted ``Rom``, refreshed every sync like the version dimensions."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "gb",
                "cover_path": "",
                "fs_size_bytes": 3_145_728,
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.fs_size_bytes == 3_145_728

    def test_commit_defaults_fs_size_bytes_when_pending_omits_it(self, library):
        """A pending entry with no size upserts a Rom carrying NULL (size unknown)."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "gb",
                "cover_path": "",
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        assert rom is not None
        assert rom.fs_size_bytes is None

    def test_commit_stamps_cover_path_when_present(self, library):
        """A finalized cover path is recorded on the upserted ROM row."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "gb",
                "cover_path": "/covers/staging.png",
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        # No grid dir in the test stub → finalize returns the path unchanged.
        assert rom.cover_path == "/covers/staging.png"

    def test_commit_skips_invalid_rom_keeps_rest(self, library):
        """An invariant ValueError (missing platform_slug) skips one ROM; the rest still commit."""
        uow = library.uow
        _stage(
            library.sync._box,
            10,
            {
                "name": "Bad",
                "fs_name": "bad.z64",
                "platform_slug": "",  # invalid — Rom.synced raises ValueError
                "cover_path": "",
            },
        )
        _stage(
            library.sync._box,
            20,
            {
                "name": "Good",
                "fs_name": "good.z64",
                "platform_slug": "gb",
                "cover_path": "",
            },
        )

        library.sync._reporter._commit_unit_results_io({"10": 1010, "20": 1020}, [{"id": 10}, {"id": 20}])

        assert uow.committed is True
        with uow:
            assert uow.roms.get(10) is None
            assert uow.roms.get(20) is not None

    def test_commit_preserves_out_of_band_sgdb_id_on_resync(self, library):
        """An sgdb_id resolved out-of-band (e.g. IGDB cross-ref) survives a re-sync
        whose pending entry has sgdb_id=None — the live RomM fetch never carries it.

        Regression of #746's _merge_optional_id contract: a blind upsert would
        NULL the resolved id and revert SGDB artwork to "needs pick"."""
        uow = library.uow
        # Existing row carries a backend-resolved sgdb_id + ra_id + cover_path.
        _seed_rom(
            uow,
            42,
            app_id=100001,
            platform_slug="gb",
            name="Game",
            sgdb_id=4242,
            cover_path="/covers/42p.png",
        )
        with uow:
            existing = uow.roms.get(42)
            existing.assign_ra_id(7777)
            uow.roms.save(existing)

        # The re-sync's built entry (live RomM fetch) lacks sgdb_id / ra_id /
        # cover_path entirely.
        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "gb",
                "cover_path": "",
                "igdb_id": 555,
                "sgdb_id": None,
                "ra_id": None,
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            rom = uow.roms.get(42)
        # Out-of-band ids + cover preserved; RomM-native igdb_id overwritten.
        assert rom.sgdb_id == 4242
        assert rom.ra_id == 7777
        assert rom.cover_path == "/covers/42p.png"
        assert rom.igdb_id == 555

    def test_commit_new_value_overwrites_existing_id(self, library):
        """A fresh non-None sgdb_id in pending wins over the existing row's value."""
        uow = library.uow
        _seed_rom(uow, 42, app_id=100001, platform_slug="gb", name="Game", sgdb_id=4242)

        _stage(
            library.sync._box,
            42,
            {
                "name": "Game",
                "fs_name": "game.z64",
                "platform_slug": "gb",
                "cover_path": "",
                "sgdb_id": 9999,
            },
        )

        library.sync._reporter._commit_unit_results_io({"42": 100001}, [{"id": 42}])

        with uow:
            assert uow.roms.get(42).sgdb_id == 9999


class TestGroupAwareCommit:
    """Group-aware per-unit commit (ADR-0021): persist every fetched sibling,
    bind only representatives, and move a binding on a rebind."""

    def test_persists_non_representative_sibling_unbound(self, library):
        """A fetched sibling that is not the emitted representative lands a ``roms``
        row for its identity + version, but carries no shortcut binding."""
        uow = library.uow
        box = library.sync._box
        rep = {
            "name": "Game (USA)",
            "fs_name": "usa.z64",
            "platform_slug": "n64",
            "cover_path": "",
            "sibling_group_key": "g",
        }
        sibling = {
            "name": "Game (JP)",
            "fs_name": "jp.z64",
            "platform_slug": "n64",
            "cover_path": "",
            "sibling_group_key": "g",
        }
        box.pending_sync = {10: rep}  # only rom 10 is emitted
        box.pending_all_roms = {10: rep, 11: sibling}

        library.sync._reporter._commit_unit_results_io({"10": 9001}, [{"id": 10}, {"id": 11}])

        with uow:
            rep_row = uow.roms.get(10)
            sibling_row = uow.roms.get(11)
        assert rep_row is not None and rep_row.shortcut_app_id == 9001
        assert sibling_row is not None
        assert sibling_row.shortcut_app_id is None
        assert sibling_row.name == "Game (JP)"
        assert sibling_row.sibling_group_key == "g"

    def test_non_acked_bound_sibling_keeps_its_existing_binding(self, library):
        """A bound sibling NOT acked this cycle keeps its existing binding — the
        `_persist_synced_rom` fallback (`existing.shortcut_app_id`) is load-bearing.

        Regression guard for the single most dangerous line in #1296: a
        grandfathered group has rom 11 already bound (app 7777) while rom 10 is the
        emitted representative bound this cycle (app 9001). Only rom 10 is acked, so
        the commit must PRESERVE rom 11's 7777 from the existing row — the broken
        `binding.get(rom_id)` variant (no existing fallback) would silently unbind
        it, orphaning a live shortcut. ``test_persists_non_representative_sibling_unbound``
        seeds no prior row for rom 11, so it passes with or without the fallback;
        this one seeds the prior binding and only passes with it.
        """
        uow = library.uow
        # rom 11 already carries a shortcut (grandfathered duplicate of group "g").
        _seed_rom(uow, 11, app_id=7777, platform_slug="n64", name="Game (JP)", group_key="g")
        box = library.sync._box
        rep = {
            "name": "Game (USA)",
            "fs_name": "usa.z64",
            "platform_slug": "n64",
            "cover_path": "",
            "sibling_group_key": "g",
        }
        sibling = {
            "name": "Game (JP)",
            "fs_name": "jp.z64",
            "platform_slug": "n64",
            "cover_path": "",
            "sibling_group_key": "g",
        }
        box.pending_sync = {10: rep}  # only rom 10 is emitted / acked this cycle
        box.pending_all_roms = {10: rep, 11: sibling}

        library.sync._reporter._commit_unit_results_io({"10": 9001}, [{"id": 10}, {"id": 11}])

        with uow:
            rep_row = uow.roms.get(10)
            sibling_row = uow.roms.get(11)
        # rom 10 binds the acked appId; rom 11 KEEPS its pre-existing binding.
        assert rep_row is not None and rep_row.shortcut_app_id == 9001
        assert sibling_row is not None and sibling_row.shortcut_app_id == 7777

    def test_rebind_moves_binding_to_representative(self, library):
        """A rebind entry (keyed to the vanished bound sibling, carrying
        ``bind_rom_id``) moves the DB binding onto the surviving representative:
        the appId survives, the old sibling is unbound (ADR-0021 §2)."""
        uow = library.uow
        _seed_rom(uow, 1, app_id=5000, platform_slug="n64", name="Game (USA)", group_key="g")
        box = library.sync._box
        # The emitted rebind entry is keyed to the vanished bound sibling (rom 1)
        # and names the representative (rom 2) in bind_rom_id.
        box.pending_sync = {1: {"name": "Game (USA)", "cover_path": "", BIND_ROM_ID_KEY: 2}}
        box.pending_all_roms = {
            2: {
                "name": "Game (JP)",
                "fs_name": "jp.z64",
                "platform_slug": "n64",
                "cover_path": "",
                "sibling_group_key": "g",
            },
        }

        # The frontend reused the old shortcut's appId (5000) under rom_id 1.
        library.sync._reporter._commit_unit_results_io({"1": 5000}, [{"id": 2}])

        with uow:
            rep = uow.roms.get(2)
            old = uow.roms.get(1)
        # The binding moved onto the representative, which keeps its own real name.
        assert rep is not None
        assert rep.shortcut_app_id == 5000
        assert rep.name == "Game (JP)"
        assert rep.sibling_group_key == "g"
        # The vanished sibling is unbound by the collision-safe save (row survives).
        assert old is not None
        assert old.shortcut_app_id is None


class TestCommitUnitMetadataStamp:
    """The metadata stamp folded into the per-unit ``roms`` write UoW.

    The reporter saves each acked ROM's cached ``rom_metadata`` in the same
    write UoW as the ``roms`` upsert (Rom row first, metadata second — the
    FK is satisfied at commit), so a ROM and its metadata land atomically.
    """

    def test_stamps_metadata_alongside_rom(self, library):
        """An acked ROM carrying a ``metadatum`` lands both a ``roms`` row and
        a ``rom_metadata`` row in the same commit, with fields mapped + ms→s +
        steam_categories computed."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "gb", "cover_path": ""},
        )
        acked = [
            {
                "id": 42,
                "summary": "A classic",
                "metadatum": {
                    "genres": ["Action", "Puzzle"],
                    "companies": ["Nintendo"],
                    "first_release_date": 946684800000,  # ms
                    "average_rating": 88.5,
                    "game_modes": ["Single player"],
                    "player_count": "1",
                },
            },
        ]

        library.sync._reporter._commit_unit_results_io({"42": 100001}, acked)

        assert uow.committed is True
        with uow:
            rom = uow.roms.get(42)
            meta = uow.rom_metadata.get(42)
        # Rom row committed.
        assert rom is not None
        assert rom.shortcut_app_id == 100001
        # Metadata row committed, fields mapped.
        assert meta is not None
        assert meta.summary == "A classic"
        assert meta.genres == ("Action", "Puzzle")
        assert meta.companies == ("Nintendo",)
        assert meta.first_release_date == 946684800  # ms → s
        assert meta.average_rating == 88.5
        assert meta.game_modes == ("Single player",)
        # Steam categories derived from genres + modes (28 = full controller).
        assert 28 in meta.steam_categories
        assert 21 in meta.steam_categories  # Action
        assert 4 in meta.steam_categories  # Puzzle
        assert 2 in meta.steam_categories  # Single player

    def test_malformed_metadatum_skips_metadata_keeps_rom(self, library, caplog):
        """A malformed ``metadatum`` (non-numeric release date) skips only that
        ROM's metadata — the Rom row still commits and a warning is logged."""
        import logging

        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "gb", "cover_path": ""},
        )
        # first_release_date is non-numeric → int(...) raises ValueError in the
        # mapping, caught per-rom.
        acked = [{"id": 42, "summary": "Bad", "metadatum": {"first_release_date": "not-a-number"}}]

        with caplog.at_level(logging.WARNING):
            library.sync._reporter._commit_unit_results_io({"42": 100001}, acked)

        assert uow.committed is True
        with uow:
            # Rom survives; metadata was skipped.
            assert uow.roms.get(42) is not None
            assert uow.rom_metadata.get(42) is None
        assert any("malformed metadatum" in r.message.lower() for r in caplog.records)

    def test_no_metadatum_writes_no_metadata_row(self, library):
        """An acked ROM without a ``metadatum`` field commits the Rom but no
        ``rom_metadata`` row (defensive guard against thin-ROM cache erasure)."""
        uow = library.uow
        _stage(
            library.sync._box,
            42,
            {"name": "Game", "fs_name": "game.z64", "platform_slug": "gb", "cover_path": ""},
        )
        acked = [{"id": 42, "name": "Thin"}]  # no metadatum

        library.sync._reporter._commit_unit_results_io({"42": 100001}, acked)

        assert uow.committed is True
        with uow:
            assert uow.roms.get(42) is not None
            assert uow.rom_metadata.get(42) is None

    def test_falsy_metadatum_writes_no_metadata_row(self, library):
        """``metadatum: None`` and ``metadatum: {}`` both skip the metadata stamp."""
        uow = library.uow
        _stage(
            library.sync._box,
            10,
            {"name": "A", "fs_name": "a.z64", "platform_slug": "gb", "cover_path": ""},
        )
        _stage(
            library.sync._box,
            20,
            {"name": "B", "fs_name": "b.z64", "platform_slug": "gb", "cover_path": ""},
        )
        acked = [{"id": 10, "metadatum": None}, {"id": 20, "metadatum": {}}]

        library.sync._reporter._commit_unit_results_io({"10": 1010, "20": 1020}, acked)

        with uow:
            assert uow.rom_metadata.get(10) is None
            assert uow.rom_metadata.get(20) is None

    def test_empty_unit_commits_nothing_extra(self, library):
        """An empty unit (no acked ROMs) commits cleanly with no metadata rows."""
        uow = library.uow

        library.sync._reporter._commit_unit_results_io({}, [])

        assert uow.committed is True
        with uow:
            assert list(uow.rom_metadata.iter_all()) == []


class TestAckMatchesActiveUnit:
    """The reporter's ack identity guard now spans run + unit + chunk (#1025).

    A chunked apply dispatches one chunk at a time; an ack must echo back the
    active chunk index or it is rejected, so a crash-late ack for a superseded
    chunk can never be credited to the chunk in flight.
    """

    def test_matches_when_run_unit_and_chunk_all_agree(self, library):
        box = library.sync._box
        box.current_sync_id = "run-1"
        box.active_unit_id = 5
        box.active_chunk_index = 2
        assert library.sync._reporter._ack_matches_active_unit("run-1", 5, 2) is True

    def test_rejects_wrong_chunk_index(self, library):
        box = library.sync._box
        box.current_sync_id = "run-1"
        box.active_unit_id = 5
        box.active_chunk_index = 2
        # Run + unit agree, but the ack is for a stale chunk.
        assert library.sync._reporter._ack_matches_active_unit("run-1", 5, 1) is False

    def test_rejects_when_no_active_chunk(self, library):
        box = library.sync._box
        box.current_sync_id = "run-1"
        box.active_unit_id = 5
        box.active_chunk_index = None  # no chunk in flight (cancelled / committed)
        assert library.sync._reporter._ack_matches_active_unit("run-1", 5, 0) is False


class TestClearSyncCache:
    """Tests for clear_sync_cache() — Force Full Sync clears the per-platform stamps
    and the recorded launch options but PRESERVES the run history, so the Last-sync
    display stays truthful (#1318)."""

    async def test_preserves_completed_run_so_last_sync_survives(self, library):
        """After clear, the completed run remains → get_latest_completed is set and last_sync still reads its time."""
        from domain.sync_run import SyncRun

        uow = library.uow
        run = SyncRun.start(id="run-1", at="2025-01-01T00:00:00", platforms_planned=1, roms_planned=1)
        run.complete("2025-01-01T00:10:00", ["N64"], [])
        with uow:
            uow.sync_runs.save(run)

        result = await library.sync.clear_sync_cache()

        assert result["success"] is True
        with uow:
            assert uow.sync_runs.get_latest_completed() is not None
        # The derived last_sync read still surfaces the completed run — no reset to "Never".
        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] == "2025-01-01T00:10:00"

    async def test_leaves_run_history_untouched(self, library):
        """Force Full Sync deletes no runs — a completed run AND a running run both
        survive the reset (it clears stamps + recorded launch options only)."""
        from domain.sync_run import SyncRun

        uow = library.uow
        completed = SyncRun.start(id="run-done", at="2025-01-01T00:00:00", platforms_planned=1, roms_planned=1)
        completed.complete("2025-01-01T00:10:00", ["N64"], [])
        running = SyncRun.start(id="run-live", at="2025-02-01T00:00:00", platforms_planned=1, roms_planned=1)
        with uow:
            uow.sync_runs.save(completed)
            uow.sync_runs.save(running)

        await library.sync.clear_sync_cache()

        with uow:
            assert uow.sync_runs.get("run-done") is not None
            assert uow.sync_runs.get_running() is not None

    async def test_resets_recorded_launch_options_so_the_next_apply_skips_nothing(self, library):
        """Force Full Sync must force past the per-item delta skip (ADR-0025).

        The recorded launch command is the skip's evidence; resetting it to NULL
        (never matches a target) makes the next apply re-touch every shortcut —
        the repair path for Steam-side drift the recorded value cannot see.
        """
        uow = library.uow
        _seed_rom(uow, 7, app_id=111, platform_slug="n64")
        with uow:
            uow.roms.set_applied_launch_options(7, "flatpak run app 'x.zip'")

        await library.sync.clear_sync_cache()

        with uow:
            assert uow.roms.get(7).applied_launch_options is None

    async def test_preserves_last_sync_and_a_newer_cancelled_attempt(self, library):
        """After Force Full Sync, BOTH a completed run's last_sync AND a newer
        cancelled run's last-attempt hint survive (#1318).

        The old behaviour deleted every terminal run so the display blanked to
        "Never" right after a reset. Preserving history keeps the honest
        "17:48 (cancelled)"-style display; the force still re-fetches (stamps
        cleared) without touching what the panel shows.
        """
        from domain.sync_run import SyncRun

        uow = library.uow
        completed = SyncRun.start(id="run-ok", at="2025-01-01T00:00:00", platforms_planned=1, roms_planned=1)
        completed.complete("2025-01-01T00:10:00", ["N64"], [])
        cancelled = SyncRun.start(id="run-x", at="2025-01-01T01:00:00", platforms_planned=1, roms_planned=1)
        cancelled.mark_cancelled("2025-01-01T01:05:00", reason="user")
        with uow:
            uow.sync_runs.save(completed)
            uow.sync_runs.save(cancelled)

        await library.sync.clear_sync_cache()

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] == "2025-01-01T00:10:00"
        assert stats["last_attempt"] == {"finished_at": "2025-01-01T01:05:00", "status": "cancelled"}

    async def test_preserves_a_lone_failed_attempt_across_the_reset(self, library):
        """The #1318 core case: with only a non-completed run (a resume situation),
        Force Full Sync no longer blanks the display to "Never" — the interrupted
        attempt survives so last_attempt still surfaces it.
        """
        from domain.sync_run import SyncRun

        uow = library.uow
        interrupted = SyncRun.start(id="run-i", at="2025-01-01T00:00:00", platforms_planned=1, roms_planned=1)
        interrupted.mark_interrupted("2025-01-01T00:05:00", reason="external death")
        with uow:
            uow.sync_runs.save(interrupted)

        await library.sync.clear_sync_cache()

        stats = library.sync.get_sync_stats()
        assert stats["last_sync"] is None
        assert stats["last_attempt"] == {"finished_at": "2025-01-01T00:05:00", "status": "interrupted"}

    async def test_clears_platform_completion_stamps(self, library):
        """Force Full Sync also drops the per-platform completion stamps (ADR-0023).

        Each stamp is its own effective ``last_sync``; leaving them would let an
        unchanged platform still skip after the user asked for a full re-fetch.
        """
        from domain.platform_sync_state import PlatformSyncState

        uow = library.uow
        with uow:
            uow.platform_sync_state.save(
                PlatformSyncState.stamp(platform_slug="n64", at="2025-01-01T00:00:00", rom_count=100)
            )
            uow.platform_sync_state.save(
                PlatformSyncState.stamp(platform_slug="snes", at="2025-01-01T00:00:00", rom_count=200)
            )

        await library.sync.clear_sync_cache()

        with uow:
            assert uow.platform_sync_state.get("n64") is None
            assert uow.platform_sync_state.get("snes") is None

    async def test_clears_collection_completion_stamps(self, library):
        """Force Full Sync also drops the per-collection completion stamps (#742).

        Each collection stamp is its own effective ``last_sync``; leaving them would
        let an unchanged collection still skip after the user asked for a full
        re-fetch.
        """
        from domain.collection_sync_state import CollectionSyncState

        uow = library.uow
        with uow:
            uow.collection_sync_state.save(
                CollectionSyncState.stamp(
                    collection_id="7",
                    collection_kind="standard",
                    updated_at="2025-01-01T00:00:00",
                    completed_at="2025-01-01T00:05:00",
                    rom_count=2,
                    member_rom_ids=(1, 2),
                )
            )
            uow.collection_sync_state.save(
                CollectionSyncState.stamp(
                    collection_id="9",
                    collection_kind="smart",
                    updated_at="2025-01-01T00:00:00",
                    completed_at="2025-01-01T00:05:00",
                    rom_count=1,
                    member_rom_ids=(3,),
                )
            )

        await library.sync.clear_sync_cache()

        with uow:
            assert uow.collection_sync_state.get("7", "standard") is None
            assert uow.collection_sync_state.get("9", "smart") is None


def _stamp_platform(uow, slug: str) -> None:
    from domain.platform_sync_state import PlatformSyncState

    with uow:
        uow.platform_sync_state.save(
            PlatformSyncState.stamp(platform_slug=slug, at="2025-01-01T00:00:00", rom_count=10)
        )


def _stamp_collection(uow, collection_id: str, kind: str = "standard") -> None:
    from domain.collection_sync_state import CollectionSyncState

    with uow:
        uow.collection_sync_state.save(
            CollectionSyncState.stamp(
                collection_id=collection_id,
                collection_kind=kind,
                updated_at="2025-01-01T00:00:00",
                completed_at="2025-01-01T00:05:00",
                rom_count=1,
                member_rom_ids=(1,),
            )
        )


def _record_launch_options(uow, rom_id: int, launch_options: str = "flatpak run app 'game.zip'") -> None:
    """Record the launch command a sync ack-commit would have written for this ROM."""
    with uow:
        rom = uow.roms.get(rom_id)
        rom.record_applied_launch_options(launch_options)
        uow.roms.set_applied_launch_options(rom_id, rom.applied_launch_options)


class TestGetSyncStatsResumeInputs:
    """``resumable_games`` / ``has_completion_stamp`` — the two kinds of durable
    progress the panel's "Resume Sync" offer reads (#1789).

    A completion stamp makes the next run pass over a whole platform or collection
    at fetch time; a recorded launch command makes it pass over one game at apply
    time. Both survive a stopped run and both are cleared together by Force Full
    Sync — which is why the run history, deliberately preserved by that clear
    (#1318), cannot carry the offer."""

    @pytest.mark.asyncio
    async def test_pristine_install_has_neither(self, library):
        stats = library.sync.get_sync_stats()
        assert stats["resumable_games"] == 0
        assert stats["has_completion_stamp"] is False

    @pytest.mark.asyncio
    async def test_counts_bound_roms_carrying_a_recorded_launch_command(self, library):
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")
        _seed_rom(uow, 20, app_id=1002, platform_slug="n64")
        _record_launch_options(uow, 10)
        _record_launch_options(uow, 20)

        stats = library.sync.get_sync_stats()
        assert stats["resumable_games"] == 2

    @pytest.mark.asyncio
    async def test_a_bound_rom_with_no_recorded_command_is_not_resumable(self, library):
        """A NULL recorded value never matches a target, so that row is always re-applied."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")

        stats = library.sync.get_sync_stats()
        assert stats["roms"] == 1
        assert stats["resumable_games"] == 0

    @pytest.mark.asyncio
    async def test_the_uninstalled_placeholder_still_counts(self, library):
        """An empty string is a recorded value (the uninstall placeholder, #1146), not a missing one.

        The shortcut it describes carries an empty launch command and is correct as
        it stands, so the next run skips it exactly as it skips a full command.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")
        _record_launch_options(uow, 10, "")

        stats = library.sync.get_sync_stats()
        assert stats["resumable_games"] == 1

    @pytest.mark.asyncio
    async def test_an_unbound_rom_is_not_resumable_however_it_was_recorded(self, library):
        """Removing every shortcut must drop the offer, and unbinding KEEPS the row.

        ``Rom.unbind_shortcut`` clears only ``shortcut_app_id`` (ADR-0007), so the
        recorded command survives Data Management's remove-all. It is not skip authority
        there: ``classify_roms`` (``domain/sync_diff.py``) sends an unbound row down
        the NEW branch before it reads the recorded value, because the next run has
        to mint the shortcut regardless. A count over every row would keep offering to resume
        shortcuts that no longer exist.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")
        _record_launch_options(uow, 10)
        assert library.sync.get_sync_stats()["resumable_games"] == 1

        await library.shortcut_removal.report_removal_results([10], None)

        stats = library.sync.get_sync_stats()
        assert stats["roms"] == 0
        assert stats["resumable_games"] == 0
        with uow:
            assert uow.roms.get(10).applied_launch_options is not None

    @pytest.mark.asyncio
    async def test_a_cancel_inside_the_first_platform_is_still_a_resume(self, library):
        """The case the stamp-only rule got wrong.

        A run cancelled before any unit reached its final chunk leaves no stamp,
        but its committed chunks wrote shortcuts and recorded their launch
        commands. The next run genuinely does less work, so this is a resume.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")
        _record_launch_options(uow, 10)

        stats = library.sync.get_sync_stats()
        assert stats["resumable_games"] == 1
        assert stats["has_completion_stamp"] is False

    @pytest.mark.asyncio
    async def test_a_platform_stamp_alone_is_still_a_resume(self, library):
        """The mirror case: stamps with no recorded game.

        A row predating migration 015 carries a NULL recorded value while its
        platform's stamp survives, so an upgraded install can hold stamps and no
        recorded games — and those platforms still skip wholesale at fetch time.
        """
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")
        _stamp_platform(uow, "n64")

        stats = library.sync.get_sync_stats()
        assert stats["resumable_games"] == 0
        assert stats["has_completion_stamp"] is True

    @pytest.mark.asyncio
    async def test_a_collection_stamp_alone_answers_the_same_way(self, library):
        """A library synced only through collections holds no platform stamp at all."""
        uow = library.uow
        _stamp_collection(uow, "7", "smart")

        stats = library.sync.get_sync_stats()
        assert stats["has_completion_stamp"] is True

    @pytest.mark.asyncio
    async def test_a_platform_stamp_whose_skip_was_revoked_is_no_resume(self, library):
        """The next run full-fetches that platform, so the stamp saves it nothing."""
        uow = library.uow
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")
        _stamp_platform(uow, "n64")
        with uow:
            uow.platform_sync_state.revoke_skip("n64")

        stats = library.sync.get_sync_stats()
        assert stats["roms"] == 1
        assert stats["has_completion_stamp"] is False

    @pytest.mark.asyncio
    async def test_the_stamp_flag_follows_a_dropped_stamp(self, library):
        """Read live, not cached — an apply-start clear shows up at once."""
        uow = library.uow
        _stamp_platform(uow, "n64")
        assert library.sync.get_sync_stats()["has_completion_stamp"] is True
        with uow:
            uow.platform_sync_state.delete("n64")

        assert library.sync.get_sync_stats()["has_completion_stamp"] is False

    @pytest.mark.asyncio
    async def test_force_full_sync_takes_both_while_the_attempt_survives(self, library):
        """The #1789 case end to end: the clear takes both skip authorities, not the history.

        Before the clear the panel holds an incomplete attempt, recorded games AND
        a stamp — a genuine resume. Afterwards the attempt is still there (it feeds
        the "Last sync" display, #1318) while both authorities are gone, which is
        what takes the resume offer away.
        """
        from domain.sync_run import SyncRun

        uow = library.uow
        interrupted = SyncRun.start(id="run-i", at="2025-01-01T00:00:00", platforms_planned=1, roms_planned=1)
        interrupted.mark_interrupted("2025-01-01T00:05:00", reason="external death")
        with uow:
            uow.sync_runs.save(interrupted)
        _seed_rom(uow, 10, app_id=1001, platform_slug="n64")
        _record_launch_options(uow, 10)
        _stamp_platform(uow, "n64")
        _stamp_collection(uow, "7")

        before = library.sync.get_sync_stats()
        assert before["resumable_games"] == 1
        assert before["has_completion_stamp"] is True

        await library.sync.clear_sync_cache()

        after = library.sync.get_sync_stats()
        assert after["resumable_games"] == 0
        assert after["has_completion_stamp"] is False
        # The shortcuts themselves are untouched — only the skip authority went.
        assert after["roms"] == 1
        assert after["last_attempt"] == {"finished_at": "2025-01-01T00:05:00", "status": "interrupted"}


class TestFinalizePerUnitRun:
    """SyncReporter.finalize_per_unit_run (stale unbind + sync_collections) and the
    separate emit_sync_complete (terminal sync_complete + progress frame, emitted
    LAST by the orchestrator after the SyncRun write, #39)."""

    @pytest.mark.asyncio
    async def test_builds_platform_collections_from_roms(self, library, emit):
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")
        library.settings["collection_create_platform_groups"] = True

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        collections_events = [c for c in emit.call_args_list if c[0][0] == "sync_collections"]
        assert len(collections_events) == 1
        payload = collections_events[0][0][1]
        # Keyed by live display names; the kv_config cache was refreshed.
        assert set(payload["platform_app_ids"].keys()) == {"Nintendo 64", "Super Nintendo"}
        with uow:
            assert json.loads(uow.kv_config.get("platform_names")) == {
                "n64": "Nintendo 64",
                "snes": "Super Nintendo",
            }

    @pytest.mark.asyncio
    async def test_builds_romm_collection_app_ids_excluding_unbound(self, library, emit):
        """RomM collections resolve rom_id→app_id via uow.roms and skip unbound rows."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=None, platform_slug="snes", name="B (unbound)")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="Faves", rom_ids=[1, 2], kind="standard")
            },
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
        )

        collections_events = [c for c in emit.call_args_list if c[0][0] == "sync_collections"]
        payload = collections_events[0][0][1]
        # rom 2 is unbound AND has no sibling group → excluded; only rom 1 appears.
        assert payload["romm_collection_app_ids"] == {"Faves": [1001]}

    @pytest.mark.asyncio
    async def test_romm_collection_group_fallback_maps_unbound_sibling(self, library, emit):
        """A collection membership on an UNBOUND sibling maps to its group's bound
        sibling's appId (ADR-0021) — collecting any version collects the game."""

        uow = library.uow
        # A bound representative + an unbound sibling in the SAME group.
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="Game (USA)", group_key="g")
        _seed_rom(uow, 2, app_id=None, platform_slug="n64", name="Game (JP)", group_key="g")

        await library.sync._reporter.finalize_per_unit_run(
            # the UNBOUND sibling is collected
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="Faves", rom_ids=[2], kind="standard")
            },
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        # rom 2 is unbound, but its group's bound sibling (rom 1 → 1001) stands in.
        assert payload["romm_collection_app_ids"] == {"Faves": [1001]}

    @pytest.mark.asyncio
    async def test_romm_collection_dedups_group_members_onto_one_shortcut(self, library, emit):
        """A collection holding several siblings of one group yields the group's
        single shortcut appId once, not duplicated."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="Game (USA)", group_key="g")
        _seed_rom(uow, 2, app_id=None, platform_slug="n64", name="Game (JP)", group_key="g")

        await library.sync._reporter.finalize_per_unit_run(
            # BOTH siblings collected
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="Faves", rom_ids=[1, 2], kind="standard")
            },
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {"Faves": [1001]}

    @pytest.mark.asyncio
    async def test_same_named_collections_union_their_members(self, library, emit):
        """Two same-named DIFFERENT-kind collections UNION into one Steam collection (#1503).

        RomM permits same-named collections across kinds/users. Steam's collection
        namespace is by-name, so both must map to the single ``RomM: [X]`` tag with
        the UNION of their members. Load-bearing: against the pre-#1503 name-keyed
        overwrite, only the last-synced collection's member would survive.
        """

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="X", rom_ids=[1], kind="standard"),
                ("smart", "9"): CollectionMembership(name="X", rom_ids=[2], kind="smart"),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        # UNION of both collections' resolved appIds, order-preserving, deduped.
        assert payload["romm_collection_app_ids"] == {"X": [1001, 1002]}

    @pytest.mark.asyncio
    async def test_same_named_union_dedups_shared_member_across_collections(self, library, emit):
        """A member shared by two same-named collections contributes its appId once."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="X", rom_ids=[1, 2], kind="standard"),
                ("smart", "9"): CollectionMembership(name="X", rom_ids=[1], kind="smart"),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        # app 1001 is shared by both collections but appears once; 1002 follows it.
        # (Also non-vacuous vs the old overwrite: last-write-wins would drop 1002.)
        assert payload["romm_collection_app_ids"] == {"X": [1001, 1002]}

    @pytest.mark.asyncio
    async def test_distinct_named_collections_unchanged_common_case(self, library, emit):
        """The all-distinct-names common case is byte-for-byte the pre-#1503 output.

        Each name unions a set of one, so distinct collections keep their own
        member sets — no merge, no reordering.
        """

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="Alpha", rom_ids=[1], kind="standard"),
                ("smart", "9"): CollectionMembership(name="Beta", rom_ids=[2], kind="smart"),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {"Alpha": [1001], "Beta": [1002]}

    @pytest.mark.asyncio
    async def test_by_label_keys_a_single_collection_with_its_type_label(self, library, emit):
        """``by_label`` mode appends the type label to the reporter key.

        The key becomes ``"<name> (<Label>)"`` so the frontend builds
        ``RomM: [<name> (Label)]``. Default ``merge`` would key by the bare name.
        """

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("virtual", "vc-1"): CollectionMembership(
                    name="coll-a", rom_ids=[1], kind="virtual", virtual_type="franchise"
                )
            },
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {"coll-a (Franchise)": [1001]}

    @pytest.mark.asyncio
    async def test_by_label_keeps_same_name_different_label_separate(self, library, emit):
        """A standard and a virtual collection sharing a name stay SEPARATE under by_label.

        The load-bearing behavior: the standard collection keeps its bare name and
        the IGDB collection gets its label, so the two keys differ → two distinct
        Steam collections, not a union (which is what ``merge`` does).
        """

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="shared-name", rom_ids=[1], kind="standard"),
                ("virtual", "vc-9"): CollectionMembership(
                    name="shared-name", rom_ids=[2], kind="virtual", virtual_type="collection"
                ),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {
            "shared-name": [1001],
            "shared-name (IGDB Collection)": [1002],
        }

    @pytest.mark.asyncio
    async def test_by_label_franchise_vs_igdb_collection_same_name_two_keys(self, library, emit):
        """Two virtual collections of the same name but different virtual_type split apart."""

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("virtual", "vc-f"): CollectionMembership(
                    name="shared-name", rom_ids=[1], kind="virtual", virtual_type="franchise"
                ),
                ("virtual", "vc-c"): CollectionMembership(
                    name="shared-name", rom_ids=[2], kind="virtual", virtual_type="collection"
                ),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {
            "shared-name (Franchise)": [1001],
            "shared-name (IGDB Collection)": [1002],
        }

    @pytest.mark.asyncio
    async def test_by_label_same_name_same_label_still_unions(self, library, emit):
        """Two collections of the SAME name AND same kind still UNION under by_label.

        Same-name-within-one-kind is accepted as merged (two standard "Faves"),
        so the bare key ``"Faves"`` unions both member sets.
        """

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="Faves", rom_ids=[1], kind="standard"),
                ("standard", "8"): CollectionMembership(name="Faves", rom_ids=[2], kind="standard"),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {"Faves": [1001, 1002]}

    @pytest.mark.asyncio
    async def test_by_label_gives_a_smart_collection_its_label_and_a_standard_one_none(self, library, emit):
        """A same-named standard and smart collection: the standard key is bare, the smart one ``(Smart)``."""

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="Kids", rom_ids=[1], kind="standard"),
                ("smart", "9"): CollectionMembership(name="Kids", rom_ids=[2], kind="smart"),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {"Kids": [1001], "Kids (Smart)": [1002]}

    @pytest.mark.asyncio
    async def test_by_label_unions_a_standard_named_like_another_kinds_key(self, library, emit):
        """A standard collection whose name spells another kind's key unions with it.

        Deliberate: a standard collection carries no label, so one named
        ``"Foo (Smart)"`` has the same key as a smart collection named ``"Foo"``,
        and the two land in one Steam collection. It takes a name that spells out
        another kind's label to get there.
        """

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="Foo (Smart)", rom_ids=[1], kind="standard"),
                ("smart", "9"): CollectionMembership(name="Foo", rom_ids=[2], kind="smart"),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {"Foo (Smart)": [1001, 1002]}

    @pytest.mark.asyncio
    async def test_merge_mode_unions_same_name_across_kinds(self, library, emit):
        """Default ``merge`` still unions the same-named standard + virtual pair by bare name.

        The complement to ``test_by_label_keeps_same_name_different_label_separate``:
        with the default mode the exact same input collapses onto one bare-name key.
        """

        library.settings["collection_naming_mode"] = "merge"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="shared-name", rom_ids=[1], kind="standard"),
                ("virtual", "vc-9"): CollectionMembership(
                    name="shared-name", rom_ids=[2], kind="virtual", virtual_type="collection"
                ),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {"shared-name": [1001, 1002]}

    @pytest.mark.asyncio
    async def test_merge_unions_case_differing_names(self, library, emit):
        """Two names differing ONLY by case union into ONE key under merge (#1569).

        The data-loss guard: Steam collapses "7 up" and "7 Up" onto one
        case-insensitive collection, so the reporter must emit a single key with
        BOTH member sets — otherwise the second Steam create overwrites the first
        and its games are lost. First-seen casing wins for display.
        """

        library.settings["collection_naming_mode"] = "merge"
        uow = library.uow
        # collection A ("7 up") → 2 apps; collection B ("7 Up") → 5 apps.
        for rid, aid in [(1, 1001), (2, 1002), (3, 1003), (4, 1004), (5, 1005), (6, 1006), (7, 1007)]:
            _seed_rom(uow, rid, app_id=aid, platform_slug="n64", name=f"g{rid}")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="7 up", rom_ids=[1, 2], kind="standard"),
                ("virtual", "vc-1"): CollectionMembership(
                    name="7 Up", rom_ids=[3, 4, 5, 6, 7], kind="virtual", virtual_type="franchise"
                ),
            },
            pending_platform_rom_ids=set(range(1, 8)),
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        # ONE key (first-seen casing "7 up"), 2 + 5 = 7 apps unioned, neither set dropped.
        assert payload["romm_collection_app_ids"] == {"7 up": [1001, 1002, 1003, 1004, 1005, 1006, 1007]}

    @pytest.mark.asyncio
    async def test_by_label_merges_same_type_case_variants(self, library, emit):
        """Under by_label, same-TYPE case variants merge (both bare standard names → folded keys match)."""

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="abc", rom_ids=[1], kind="standard"),
                ("standard", "8"): CollectionMembership(name="ABC", rom_ids=[2], kind="standard"),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        # One key ("abc", first-seen casing), both apps unioned.
        assert payload["romm_collection_app_ids"] == {"abc": [1001, 1002]}

    @pytest.mark.asyncio
    async def test_by_label_keeps_different_type_case_variants_separate(self, library, emit):
        """Under by_label, a standard and an IGDB-collection case variant stay separate (only one is labelled)."""

        library.settings["collection_naming_mode"] = "by_label"
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={
                ("standard", "7"): CollectionMembership(name="abc", rom_ids=[1], kind="standard"),
                ("virtual", "vc-9"): CollectionMembership(
                    name="ABC", rom_ids=[2], kind="virtual", virtual_type="collection"
                ),
            },
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        assert payload["romm_collection_app_ids"] == {
            "abc": [1001],
            "ABC (IGDB Collection)": [1002],
        }

    @pytest.mark.asyncio
    async def test_platform_names_union_case_insensitively(self, library, emit):
        """Two platform display names differing only by case union into one bucket (#1569)."""

        uow = library.uow
        # Two distinct slugs whose live display names collide only by case.
        _seed_rom(uow, 1, app_id=1001, platform_slug="a", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="b", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1, 2},
            platform_names={"a": "Retro", "b": "retro"},
            processed_platform_slugs={"a", "b"},
        )

        payload = next(c for c in emit.call_args_list if c[0][0] == "sync_collections")[0][1]
        platform_map = payload["platform_app_ids"]
        # ONE bucket, both appIds present, keyed by a case-variant of "retro".
        assert len(platform_map) == 1
        (display, app_ids) = next(iter(platform_map.items()))
        assert display.casefold() == "retro"
        assert set(app_ids) == {1001, 1002}

    @pytest.mark.asyncio
    async def test_emit_sync_complete_terminal(self, library, emit):
        await library.sync._reporter.emit_sync_complete(
            platform_app_ids={},
            romm_collection_app_ids={},
            total_games=0,
            cancelled=False,
            interrupt_reason=None,
            restart_recommended=False,
        )

        complete_events = [c for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert len(complete_events) == 1
        assert "cancelled" not in complete_events[0][0][1]
        assert "interrupted" not in complete_events[0][0][1]
        # The last-run memory delta is retained in the box and read via
        # get_session_budget_status, NOT ridden on the sync_complete wire (#1383 LOW-3).
        assert "memory_delta_kb" not in complete_events[0][0][1]

    @pytest.mark.asyncio
    async def test_emit_sync_complete_carries_restart_recommended_on_clean_run(self, library, emit):
        await library.sync._reporter.emit_sync_complete(
            platform_app_ids={},
            romm_collection_app_ids={},
            total_games=0,
            cancelled=False,
            interrupt_reason=None,
            restart_recommended=True,
        )

        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert complete and complete[-1]["restart_recommended"] is True

    @pytest.mark.asyncio
    async def test_emit_sync_complete_cancelled_frame_says_cancelled_when_not_interrupted(self, library, emit):
        """A user cancel (box.run_interrupted False) → the terminal CANCELLED frame
        leads with 'Sync cancelled:' and the payload carries no ``interrupted``."""

        library.sync._box.run_interrupted = False

        await library.sync._reporter.emit_sync_complete(
            platform_app_ids={},
            romm_collection_app_ids={},
            total_games=3,
            cancelled=True,
            interrupt_reason=None,
            restart_recommended=False,
        )

        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert "interrupted" not in complete[-1]
        progress = library.sync._sync_progress
        assert progress["stage"] == "cancelled"
        assert progress["message"].startswith("Sync cancelled: ")

    @pytest.mark.asyncio
    async def test_emit_sync_complete_frame_says_interrupted_when_run_interrupted(self, library, emit):
        """A heartbeat-timeout run routes through the same cancelled emit; with
        box.run_interrupted set the payload carries ``interrupted: True`` and the
        terminal frame leads with 'Sync interrupted:' (stage stays CANCELLED — no
        new SyncStage). The frame's denominator is the PLANNED total from the
        box, not the bound-ROM count (#1384)."""

        library.sync._box.run_interrupted = True
        library.sync._box.run_total_items = 10

        await library.sync._reporter.emit_sync_complete(
            platform_app_ids={},
            romm_collection_app_ids={},
            total_games=3,
            cancelled=True,
            interrupt_reason=None,
            restart_recommended=False,
        )

        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert complete[-1]["interrupted"] is True
        progress = library.sync._sync_progress
        assert progress["stage"] == "cancelled"
        assert progress["message"] == "Sync interrupted: 3 of 10 games processed"
        assert progress["total"] == 10

    @pytest.mark.asyncio
    async def test_emit_sync_complete_uses_interrupt_reason_verbatim(self, library, emit):
        """A budget-pause interrupt_reason rides the payload AND becomes the terminal
        frame message verbatim (resume-friendly guidance, #1383)."""

        await library.sync._reporter.emit_sync_complete(
            platform_app_ids={},
            romm_collection_app_ids={},
            total_games=2,
            cancelled=True,
            interrupt_reason="Sync paused: restart Steam, then sync again.",
            restart_recommended=False,
        )

        complete = [c[0][1] for c in emit.call_args_list if c[0][0] == "sync_complete"]
        assert complete[-1]["interrupt_reason"] == "Sync paused: restart Steam, then sync again."
        # A budget pause sets run_paused + interrupt_reason, never run_interrupted —
        # the payload must not read as a heartbeat interrupt (#1384).
        assert "interrupted" not in complete[-1]
        assert library.sync._sync_progress["message"] == "Sync paused: restart Steam, then sync again."

    @pytest.mark.asyncio
    async def test_emit_sync_complete_frame_total_falls_back_to_bound_count(self, library, emit):
        """With no planned total in the box (``run_total_items`` None — pre-plan or
        the box wiped by a backend restart), the terminal frame's denominator falls
        back to the bound-ROM registry count (#1384)."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="Game A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="n64", name="Game B")
        box = library.sync._box
        box.run_interrupted = True
        assert box.run_total_items is None

        await library.sync._reporter.emit_sync_complete(
            platform_app_ids={},
            romm_collection_app_ids={},
            total_games=1,
            cancelled=True,
            interrupt_reason=None,
            restart_recommended=False,
        )

        progress = library.sync._sync_progress
        assert progress["message"] == "Sync interrupted: 1 of 2 games processed"
        assert progress["total"] == 2

    @pytest.mark.asyncio
    async def test_emit_sync_complete_counts_only_bound_roms(self, library, emit):
        """A completed run's terminal frame counts the ROMs that carry a shortcut;
        a row whose shortcut was unbound stays out of the total."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="Game A")
        _seed_rom(uow, 2, app_id=None, platform_slug="n64", name="Game B")
        _seed_rom(uow, 3, app_id=1003, platform_slug="n64", name="Game C")

        await library.sync._reporter.emit_sync_complete(
            platform_app_ids={"n64": [1001, 1003]},
            romm_collection_app_ids={},
            total_games=2,
            cancelled=False,
            interrupt_reason=None,
            restart_recommended=False,
        )

        progress = library.sync._sync_progress
        assert progress["message"] == "Sync complete: 2 games from 1 platforms"
        assert progress["total"] == 2

    @pytest.mark.asyncio
    async def test_finalize_does_not_reset_run_lifecycle(self, library, emit):
        """finalize_per_unit_run + emit_sync_complete never touch the run lifecycle (#1202).

        The IDLE/None reset lives in the orchestrator's single run-scoped
        ``finally: box.finish_run(run_id)``; the reporter only unbinds/collects and
        emits, leaving ``sync_state`` / ``current_sync_id`` untouched.
        """

        from domain.sync_state import SyncState

        library.sync._box.sync_state = SyncState.RUNNING
        library.sync._box.current_sync_id = "sync-xyz"

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids=set(),
            platform_names={},
            processed_platform_slugs=set(),
        )

        assert library.sync._sync_state == SyncState.RUNNING
        assert library.sync._current_sync_id == "sync-xyz"

    @pytest.mark.asyncio
    async def test_unbinds_stale_rom_ids_keeping_rows(self, library, emit):
        """stale_rom_ids are UNBOUND (NULL app_id) — the rows survive (ADR-0007), never deleted."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")
        _seed_rom(uow, 3, app_id=1003, platform_slug="gba", name="C")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
            stale_rom_ids=[2, 3],
        )

        assert uow.committed is True
        with uow:
            # Rows survive but their shortcut binding is cleared.
            assert uow.roms.get(2).shortcut_app_id is None
            assert uow.roms.get(3).shortcut_app_id is None
            assert uow.roms.get(1).shortcut_app_id == 1001
            assert {r.rom_id for r in uow.roms.iter_all()} == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_stale_unbind_excludes_them_from_collections(self, library, emit):
        """Collections built from uow.roms must skip NULL-app_id (just-unbound) rows."""

        library.settings["collection_create_platform_groups"] = True
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1, 2},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64", "snes"},
            stale_rom_ids=[2],
        )

        collections_events = [c for c in emit.call_args_list if c[0][0] == "sync_collections"]
        payload = collections_events[0][0][1]
        assert set(payload["platform_app_ids"].keys()) == {"Nintendo 64"}

    @pytest.mark.asyncio
    async def test_stale_unbind_skips_missing_and_already_unbound(self, library, emit):
        """A stale_rom_id with no row (missing) or already-unbound row is skipped
        without error; the genuinely-bound stale rows still unbind."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="Kept")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="Stale bound")
        _seed_rom(uow, 5, app_id=None, platform_slug="gba", name="Already unbound")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
            stale_rom_ids=[2, 5, 99],  # 2 bound, 5 already unbound, 99 missing
        )

        assert uow.committed is True
        with uow:
            # rom 2 was genuinely stale → unbound; rom 5 stays unbound (skipped,
            # no error); rom 99 has no row (skipped); rom 1 stays bound.
            assert uow.roms.get(2).shortcut_app_id is None
            assert uow.roms.get(5).shortcut_app_id is None
            assert uow.roms.get(1).shortcut_app_id == 1001
            assert uow.roms.get(99) is None
            assert {r.rom_id for r in uow.roms.iter_all()} == {1, 2, 5}

    @pytest.mark.asyncio
    async def test_no_unbind_when_stale_rom_ids_default(self, library, emit):
        """Default stale_rom_ids=None unbinds nothing — every bound row stays bound."""

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1, 2},
            platform_names={},
            processed_platform_slugs=set(),
        )

        with uow:
            assert uow.roms.get(1).shortcut_app_id == 1001
            assert uow.roms.get(2).shortcut_app_id == 1002

    @pytest.mark.asyncio
    async def test_stale_unbind_revokes_the_skip_of_a_platform_the_run_did_not_process(self, library, emit):
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")
        _stamp_fetch(uow, "n64", rom_count=1, fetch_id="run-1", seen=[1])
        _stamp_fetch(uow, "snes", rom_count=1, fetch_id="run-0", seen=[2])

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
            stale_rom_ids=[2],
        )

        with uow:
            assert uow.roms.get(2).shortcut_app_id is None
            snes = uow.platform_sync_state.get("snes")
            n64 = uow.platform_sync_state.get("n64")
        assert snes is not None
        assert (snes.fetch_id, snes.skip_revoked) == ("run-0", True)
        assert n64 is not None
        assert n64.skip_revoked is False

    @pytest.mark.asyncio
    async def test_stale_unbind_leaves_the_skip_of_a_platform_the_run_processed(self, library, emit):
        """A row RomM dropped goes stale on a platform the run fetched and stamped."""
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="n64", name="Dropped")
        _stamp_fetch(uow, "n64", rom_count=1, fetch_id="run-1", seen=[1])

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
            stale_rom_ids=[2],
        )

        with uow:
            assert uow.roms.get(2).shortcut_app_id is None
            stamp = uow.platform_sync_state.get("n64")
        assert stamp is not None
        assert (stamp.fetch_id, stamp.skip_revoked) == ("run-1", False)

    @pytest.mark.asyncio
    async def test_a_platform_the_run_did_not_process_keeps_its_skip_when_nothing_on_it_is_unbound(self, library, emit):
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 5, app_id=None, platform_slug="snes", name="Already unbound")
        _seed_rom(uow, 6, app_id=1006, platform_slug="gba", name="Not stale")
        _stamp_fetch(uow, "n64", rom_count=1, fetch_id="run-1", seen=[1])
        _stamp_fetch(uow, "snes", rom_count=1, fetch_id="run-0", seen=[5])
        _stamp_fetch(uow, "gba", rom_count=1, fetch_id="run-0", seen=[6])

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
            stale_rom_ids=[5],
        )

        with uow:
            snes = uow.platform_sync_state.get("snes")
            gba = uow.platform_sync_state.get("gba")
            assert uow.roms.get(6).shortcut_app_id == 1006
        assert snes is not None
        assert snes.skip_revoked is False
        assert gba is not None
        assert gba.skip_revoked is False

    @pytest.mark.asyncio
    async def test_the_processed_platforms_are_the_ones_named_as_processed(self, library, emit):
        """The display-name cache may name a platform the run did not process; only the processed set decides."""
        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")
        _stamp_fetch(uow, "n64", rom_count=1, fetch_id="run-1", seen=[1])
        _stamp_fetch(uow, "snes", rom_count=1, fetch_id="run-0", seen=[2])

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64", "snes": "Super Nintendo"},
            processed_platform_slugs={"n64"},
            stale_rom_ids=[2],
        )

        with uow:
            snes = uow.platform_sync_state.get("snes")
        assert snes is not None
        assert snes.skip_revoked is True

    @pytest.mark.asyncio
    async def test_get_sync_stats_reflects_unbound_count(self, library, emit):
        """After a normal finalize unbinds stale rows, get_sync_stats counts only bound ones."""

        from domain.sync_run import SyncRun

        uow = library.uow
        _seed_rom(uow, 1, app_id=1001, platform_slug="n64", name="A")
        _seed_rom(uow, 2, app_id=1002, platform_slug="snes", name="B")
        _seed_rom(uow, 3, app_id=1003, platform_slug="gba", name="C")
        run = SyncRun.start(id="run-1", at="2025-01-01T00:00:00", platforms_planned=1, roms_planned=1)
        run.complete("2025-01-01T00:00:00", ["Nintendo 64"], [])
        with uow:
            uow.sync_runs.save(run)

        await library.sync._reporter.finalize_per_unit_run(
            pending_collection_memberships={},
            pending_platform_rom_ids={1},
            platform_names={"n64": "Nintendo 64"},
            processed_platform_slugs={"n64"},
            stale_rom_ids=[2, 3],
        )

        stats = library.sync.get_sync_stats()
        assert stats["roms"] == 1
        assert stats["total_shortcuts"] == 1


class TestGetSyncRuns:
    """get_sync_runs — the newest recorded runs, serialised verbatim."""

    @staticmethod
    def _save(uow, run):
        with uow:
            uow.sync_runs.save(run)

    @staticmethod
    def _start(run_id: str, started: str):
        from domain.sync_run import SyncRun

        return SyncRun.start(id=run_id, at=started, platforms_planned=2, roms_planned=7)

    @pytest.mark.asyncio
    async def test_no_runs_answers_an_empty_list(self, library):
        assert library.sync.get_sync_runs() == {"success": True, "runs": []}

    @pytest.mark.asyncio
    async def test_completed_run_serialised_field_for_field(self, library):
        run = self._start("run-1", "2026-01-01T09:00:00")
        run.complete("2026-01-01T09:30:00", ["N64", "SNES"], ["Favourites"])
        self._save(library.uow, run)

        result = library.sync.get_sync_runs()
        assert result["success"] is True
        assert result["runs"] == [
            {
                "id": "run-1",
                "started_at": "2026-01-01T09:00:00",
                "finished_at": "2026-01-01T09:30:00",
                "status": "completed",
                "platforms_planned": 2,
                "roms_planned": 7,
                "platforms_completed": ["N64", "SNES"],
                "collections_completed": ["Favourites"],
                "error": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_cancelled_run_keeps_null_lists_and_carries_its_reason(self, library):
        """A run that did not complete recorded no lists — they stay null, not empty."""
        run = self._start("run-c", "2026-01-02T09:00:00")
        run.mark_cancelled("2026-01-02T09:05:00", "Sync cancelled")
        self._save(library.uow, run)

        record = library.sync.get_sync_runs()["runs"][0]
        assert record["status"] == "cancelled"
        assert record["platforms_completed"] is None
        assert record["collections_completed"] is None
        assert record["error"] == "Sync cancelled"

    @pytest.mark.asyncio
    async def test_running_run_is_listed_with_null_terminal_fields(self, library):
        self._save(library.uow, self._start("run-live", "2026-01-03T09:00:00"))

        record = library.sync.get_sync_runs()["runs"][0]
        assert record["status"] == "running"
        assert record["finished_at"] is None
        assert record["error"] is None

    @pytest.mark.asyncio
    async def test_newest_first_and_capped_at_the_history_limit(self, library):
        from services.library.reporter import SYNC_RUN_HISTORY_LIMIT

        for minute in range(SYNC_RUN_HISTORY_LIMIT + 3):
            self._save(library.uow, self._start(f"run-{minute:02d}", f"2026-01-01T09:{minute:02d}:00"))

        runs = library.sync.get_sync_runs()["runs"]
        assert len(runs) == SYNC_RUN_HISTORY_LIMIT
        newest = SYNC_RUN_HISTORY_LIMIT + 2
        assert [run["id"] for run in runs] == [
            f"run-{n:02d}" for n in range(newest, newest - SYNC_RUN_HISTORY_LIMIT, -1)
        ]
