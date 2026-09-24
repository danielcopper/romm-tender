"""Tests for LocalLibraryReader — this device's own record of the library, read back out.

Driven through the shared ``plugin`` fixture so every read runs against the same
``FakeUnitOfWork`` the rest of the library suite seeds, rather than a mock of the
factory: what these methods return is only interesting relative to rows someone
actually persisted.

The projections are also exercised end-to-end where their consumers live — the
preview's cover-refresh and restamp counts in
``tests/services/library/test_sync_orchestrator.py``, the stale scan's
collision exclusion in the same file's late-ack reconciliation — so the tests
here pin the projection shapes those flows read through.
"""

from domain.work_unit import WorkUnit
from tests.services.library._helpers import _seed_rom_row


class TestRegistryProjections:
    """The two bound-row projections the classify/collapse passes diff against.

    Both carry the persisted ``cover_source`` fingerprint, so the cover-cache
    invalidation pass (#1386) scans the read the group collapse already made
    instead of opening per-ROM lookups.
    """

    _OLD = "/cover/big.png?ts=2026-01-01 00:00:00"

    def test_apply_registry_projection_carries_cover_source(self, plugin):
        # Round-trip: the bound-row projection the apply scan (and its group
        # collapse) reads must surface the persisted fingerprint.
        _seed_rom_row(
            plugin, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64", cover_source=self._OLD
        )
        _seed_rom_row(plugin, 11, app_id=1011, platform_slug="n64", name="Null", fs_name="null.z64", cover_source=None)

        unit = WorkUnit(type="platform", id=1, name="N64", slug="n64", rom_count=2)
        registry = plugin._sync_service._local_library_reader.do_read_apply_registry(unit)

        assert registry["10"]["cover_source"] == self._OLD
        assert registry["11"]["cover_source"] is None

    def test_preview_baseline_projection_carries_cover_source(self, plugin):
        _seed_rom_row(
            plugin, 10, app_id=1010, platform_slug="n64", name="Keep", fs_name="keep.z64", cover_source=self._OLD
        )

        registry, _platforms, _collections = plugin._sync_service._local_library_reader.do_read_preview_baseline(
            {"n64": "N64"}
        )

        assert registry["10"]["cover_source"] == self._OLD


class TestResidentGroupKeys:
    """The DB's canonical sibling-group summaries the component keying seeds from (#1368)."""

    def test_read_resident_group_keys_filters_null_keys(self, plugin):
        _seed_rom_row(plugin, 1, app_id=100, platform_slug="n64", sibling_group_key="igdb:5:1")
        _seed_rom_row(plugin, 2, app_id=None, platform_slug="n64", sibling_group_key=None)

        keys = plugin._sync_service._local_library_reader.do_read_resident_group_keys()

        assert keys == {1: "igdb:5:1"}


class TestReachableRomIds:
    """Which ROMs the sync's collection filing resolves to a shortcut — CONTEXT.md → Reachable."""

    def test_a_bound_row_is_reachable(self, plugin):
        _seed_rom_row(plugin, 1, app_id=100, platform_slug="n64", sibling_group_key=None)

        assert plugin._sync_service._local_library_reader.do_read_reachable_rom_ids() == {1}

    def test_an_unbound_row_is_reachable_through_its_groups_binding(self, plugin):
        _seed_rom_row(plugin, 1, app_id=100, platform_slug="n64", sibling_group_key="igdb:5:1")
        _seed_rom_row(plugin, 2, app_id=None, platform_slug="n64", sibling_group_key="igdb:5:1")

        assert plugin._sync_service._local_library_reader.do_read_reachable_rom_ids() == {1, 2}

    def test_a_group_with_no_binding_reaches_nothing(self, plugin):
        _seed_rom_row(plugin, 1, app_id=None, platform_slug="n64", sibling_group_key="igdb:5:1")
        _seed_rom_row(plugin, 2, app_id=None, platform_slug="n64", sibling_group_key="igdb:5:1")

        assert plugin._sync_service._local_library_reader.do_read_reachable_rom_ids() == set()

    def test_null_keyed_unbound_rows_are_not_folded_into_a_bound_null_keyed_row(self, plugin):
        """A NULL key relates a row to nothing, so a binding on another NULL-keyed row does not reach it."""
        _seed_rom_row(plugin, 1, app_id=100, platform_slug="n64", sibling_group_key=None)
        _seed_rom_row(plugin, 2, app_id=None, platform_slug="n64", sibling_group_key=None)

        assert plugin._sync_service._local_library_reader.do_read_reachable_rom_ids() == {1}

    def test_no_rows_reach_nothing(self, plugin):
        assert plugin._sync_service._local_library_reader.do_read_reachable_rom_ids() == set()

    def test_agrees_with_the_syncs_collection_filing(self, plugin):
        """The set is exactly the rom_ids ``SyncReporter._member_app_id`` resolves to a shortcut.

        Two copies of one rule: the reporter resolves each collection member at
        filing time, this reader answers the whole set for the collections
        listing. One mixed table holds every shape either could get wrong.
        """
        from services.library.reporter import SyncReporter

        _seed_rom_row(plugin, 1, app_id=100, platform_slug="n64", sibling_group_key=None)
        _seed_rom_row(plugin, 2, app_id=None, platform_slug="n64", sibling_group_key=None)
        _seed_rom_row(plugin, 3, app_id=300, platform_slug="n64", sibling_group_key="igdb:3:n64")
        _seed_rom_row(plugin, 4, app_id=None, platform_slug="n64", sibling_group_key="igdb:3:n64")
        _seed_rom_row(plugin, 5, app_id=None, platform_slug="n64", sibling_group_key="igdb:5:n64")
        _seed_rom_row(plugin, 6, app_id=None, platform_slug="n64", sibling_group_key="igdb:5:n64")
        _seed_rom_row(plugin, 7, app_id=700, platform_slug="n64", sibling_group_key="igdb:7:n64")
        _seed_rom_row(plugin, 8, app_id=800, platform_slug="n64", sibling_group_key="igdb:7:n64")
        missing = 99

        reachable = plugin._sync_service._local_library_reader.do_read_reachable_rom_ids()

        reporter = plugin._sync_service._reporter
        with plugin._uow as uow:
            _platform_app_ids, group_bound = reporter._scan_bound_rows(uow, None, {})
            filed = {
                rid
                for rid in (1, 2, 3, 4, 5, 6, 7, 8, missing)
                if SyncReporter._member_app_id(uow, rid, group_bound) is not None
            }
        assert reachable == filed
        assert reachable == {1, 3, 4, 7, 8}
