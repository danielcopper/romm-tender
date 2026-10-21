"""Tests for services/prune/planning.py — what a group would do, and why not."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pytest
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.rom import Rom
from domain.version_metadata import VersionMetadata
from services.prune._models import PruneOptions
from services.prune.planning import GroupPlan, GroupPlanner, GroupPlannerConfig
from services.prune.registry import PruneRegistry, PruneRegistryConfig
from services.prune.results import MutationLedger, PruneResultReporter, PruneResultReporterConfig


class _FakeLiveness:
    """Hands out a fixed verdict per rom id, as the real prober would."""

    def __init__(self, statuses: dict[int, str]) -> None:
        self._statuses = statuses
        self.probed: list[set[int]] = []

    async def probe_many(self, rom_ids: set[int]) -> dict[int, dict[str, str]]:
        self.probed.append(set(rom_ids))
        return {
            rom_id: {
                "status": self._statuses.get(rom_id, "uncertain"),
                "reason": {"vanished": "not_found", "live": "live"}.get(
                    self._statuses.get(rom_id, "uncertain"), "server_unreachable"
                ),
                "message": f"verdict for {rom_id}",
            }
            for rom_id in rom_ids
        }


def _rom(rom_id: int, *, group: str | None = "g", app_id: int | None = None, main: bool = False) -> Rom:
    return Rom.synced(
        rom_id=rom_id,
        platform_slug="gba",
        name=f"Game {rom_id}",
        fs_name=f"Game {rom_id}.gba",
        shortcut_app_id=app_id,
        synced_at="now",
        version=VersionMetadata(sibling_group_key=group, is_main_sibling=main),
    )


def _options(**overrides: Any) -> PruneOptions:
    values: dict[str, Any] = {
        "remove_rows": True,
        "remove_fully_vanished": True,
        "create_recovery_bundle": True,
        "include_installed_rom_ids": frozenset(),
    }
    values.update(overrides)
    return PruneOptions(**values)


def _planner(
    rows: list[Rom],
    statuses: dict[int, str],
    *,
    active_downloads: set[int] | None = None,
    drifted: bool = False,
    drift_answer: dict[str, Any] | None = None,
) -> tuple[GroupPlanner, _FakeLiveness]:
    uow = FakeUnitOfWork()
    with uow:
        for row in rows:
            uow.roms.save(row)
    liveness = _FakeLiveness(statuses)

    async def drift_probe(rom_id: int) -> dict[str, Any]:
        if drift_answer is not None:
            return {"rom_id": rom_id, **drift_answer}
        return {
            "rom_id": rom_id,
            "drifted": drifted,
            "only_here": False,
            "stranding": "unsynced" if drifted else "none",
        }

    planner = GroupPlanner(
        config=GroupPlannerConfig(
            loop=asyncio.get_running_loop(),
            logger=logging.getLogger("test"),
            results=PruneResultReporter(config=PruneResultReporterConfig(emit=_noop_emit)),
            registry=PruneRegistry(config=PruneRegistryConfig(uow_factory=FakeUnitOfWorkFactory(uow))),
            liveness=cast("Any", liveness),
            active_downloads=lambda: active_downloads or set(),
            drift_probe=drift_probe,
        )
    )
    return planner, liveness


async def _noop_emit(*_args: Any, **_kwargs: Any) -> bool:
    return True


class _StubProbe:
    """A liveness prober whose probe_many the test supplies outright."""

    def __init__(self, probe) -> None:
        self.probe_many = probe


async def _plan(rows: list[Rom], statuses: dict[int, str], *, candidates: set[int] | None = None, **kwargs: Any):
    """Plan *rows* as a preview offering *candidates* would have it planned — every row unless named."""
    options = kwargs.pop("options", None) or _options()
    planner, _ = _planner(rows, statuses, **kwargs)
    ledger = MutationLedger(rows)
    offered = {row.rom_id for row in rows} if candidates is None else candidates
    return await planner.plan("run-1", rows, offered, options, 1, 1, ledger), ledger


class TestRefusals:
    async def test_a_group_whose_rows_vanished_locally_is_skipped(self):
        row = _rom(1)
        planner, _ = _planner([], {})
        ledger = MutationLedger([row])

        result = await planner.plan("run-1", [row], {1}, _options(), 1, 1, ledger)

        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "local_state_changed"

    async def test_two_shortcuts_in_one_group_are_refused(self):
        rows = [_rom(1, app_id=0x8001), _rom(2, app_id=0x8002)]
        result, _ = await _plan(rows, {1: "vanished", 2: "vanished"})
        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "multiple_bindings"

    async def test_an_active_download_refuses_the_group_before_any_probe(self):
        rows = [_rom(1)]
        planner, liveness = _planner(rows, {1: "vanished"}, active_downloads={1})
        result = await planner.plan("run-1", rows, {1}, _options(), 1, 1, MutationLedger(rows))
        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "download_in_progress"
        assert liveness.probed == [], "a refused group must not be probed"

    async def test_no_live_and_some_unproven_is_liveness_uncertain(self):
        rows = [_rom(1), _rom(2)]
        result, _ = await _plan(rows, {1: "vanished", 2: "uncertain"})
        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "liveness_uncertain"

    async def test_a_namespace_change_is_named_as_itself(self, monkeypatch):
        rows = [_rom(1)]
        planner, _ = _planner(rows, {})

        async def probe(rom_ids):
            return {
                rom_id: {"status": "uncertain", "reason": "server_namespace_changed", "message": "changed"}
                for rom_id in rom_ids
            }

        monkeypatch.setattr(planner, "_liveness", _StubProbe(probe))
        result = await planner.plan("run-1", rows, {1}, _options(), 1, 1, MutationLedger(rows))
        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "server_namespace_changed"

    async def test_options_that_exclude_everything_say_so(self):
        rows = [_rom(1)]
        result, _ = await _plan(rows, {1: "vanished"}, options=_options(remove_rows=False, remove_fully_vanished=False))
        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "options_excluded"

    async def test_an_unproven_member_outranks_the_options_explanation(self):
        """Fiddling with a toggle cannot fix a ROM RomM never answered for."""
        rows = [_rom(1, app_id=0x8001), _rom(2)]
        result, _ = await _plan(
            rows,
            {1: "live", 2: "uncertain"},
            candidates={2},
            options=_options(remove_rows=False, remove_fully_vanished=False),
        )
        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "liveness_uncertain"

    async def test_unsynced_saves_without_a_bundle_refuse_the_group(self):
        rows = [_rom(1, app_id=0x8001)]
        result, _ = await _plan(rows, {1: "vanished"}, drifted=True, options=_options(create_recovery_bundle=False))
        assert not isinstance(result, GroupPlan)
        assert result["reason"] == "unsynced_saves"

    async def test_a_save_with_no_recorded_sync_refuses_the_group_without_a_bundle(self):
        rows = [_rom(1, app_id=0x8001)]
        result, _ = await _plan(
            rows,
            {1: "vanished"},
            drift_answer={"drifted": False, "only_here": True, "stranding": "unsynced"},
            options=_options(create_recovery_bundle=False),
        )
        assert not isinstance(result, GroupPlan)
        assert (result["reason"], result["message"]) == (
            "unsynced_saves",
            "Unsynced saves require a sealed recovery bundle before changing this shortcut.",
        )

    async def test_a_check_that_could_not_read_refuses_the_group_saying_so(self):
        rows = [_rom(1, app_id=0x8001)]
        result, _ = await _plan(
            rows,
            {1: "vanished"},
            drift_answer={"drifted": False, "only_here": False, "stranding": "unchecked"},
            options=_options(create_recovery_bundle=False),
        )
        assert not isinstance(result, GroupPlan)
        assert (result["reason"], result["message"]) == (
            "unsynced_saves",
            "Tender couldn't check whether this game's saves have been synced to RomM; "
            "a sealed recovery bundle is required before changing this shortcut.",
        )

    async def test_a_save_with_no_recorded_sync_plans_once_a_bundle_is_chosen(self):
        rows = [_rom(1, app_id=0x8001)]
        plan, _ = await _plan(
            rows,
            {1: "vanished"},
            drift_answer={"drifted": False, "only_here": True, "stranding": "unsynced"},
        )
        assert isinstance(plan, GroupPlan)

    async def test_a_drifted_save_with_save_sync_off_leaves_the_group_free(self):
        rows = [_rom(1, app_id=0x8001)]
        plan, _ = await _plan(
            rows,
            {1: "vanished"},
            drift_answer={"drifted": True, "only_here": False, "stranding": "none"},
            options=_options(create_recovery_bundle=False),
        )
        assert isinstance(plan, GroupPlan)


class TestAGameShownAsGoneEntirely:
    """A game whose every version the preview offered is removed whole or not at all."""

    @pytest.mark.parametrize("remove_fully_vanished", [True, False])
    async def test_a_version_back_on_romm_leaves_the_whole_game(self, remove_fully_vanished):
        rows = [_rom(1), _rom(2), _rom(3)]
        result, _ = await _plan(
            rows,
            {1: "vanished", 2: "live", 3: "vanished"},
            options=_options(remove_fully_vanished=remove_fully_vanished),
        )

        assert not isinstance(result, GroupPlan)
        assert (result["status"], result["reason"], result["message"]) == (
            "skipped",
            "version_back_on_romm",
            "A version of this game is back on RomM. Scan again to review it.",
        )

    async def test_a_game_the_preview_offered_in_part_still_loses_its_gone_versions(self):
        rows = [_rom(1), _rom(2), _rom(3)]
        plan, _ = await _plan(rows, {1: "vanished", 2: "live", 3: "vanished"}, candidates={1, 3})

        assert isinstance(plan, GroupPlan)
        assert plan.delete_ids == {1, 3}


class TestPlans:
    async def test_a_fully_vanished_bound_group_plans_the_whole_game_action(self):
        rows = [_rom(1, app_id=0x8001)]
        plan, _ = await _plan(rows, {1: "vanished"})

        assert isinstance(plan, GroupPlan)
        assert plan.delete_ids == {1}
        assert plan.fully_dead is True
        assert plan.whole_game_action is True
        assert plan.app_id == 0x8001
        assert plan.bound_row is not None
        assert plan.bound_row.rom_id == 1

    async def test_a_vanished_bound_row_with_a_live_sibling_is_kept_where_it_is(self):
        """The shortcut is the game while it has a live version; only the user moves it."""
        rows = [_rom(1, app_id=0x8001), _rom(2, main=True), _rom(3)]
        plan, _ = await _plan(rows, {1: "vanished", 2: "live", 3: "vanished"}, candidates={1, 3})

        assert isinstance(plan, GroupPlan)
        assert plan.delete_ids == {3}, "the bound row is spared, the other confirmed-gone row is not"
        assert plan.whole_game_action is False

    async def test_sparing_the_bound_row_can_leave_nothing_to_do(self):
        rows = [_rom(1, app_id=0x8001), _rom(2, main=True)]
        result, _ = await _plan(rows, {1: "vanished", 2: "live"}, candidates={1})

        assert not isinstance(result, GroupPlan)
        assert (result["reason"], result["message"]) == (
            "shortcut_version_kept",
            "The version this game's shortcut is on is kept while another version is on RomM; switch versions first.",
        )

    async def test_an_unbound_vanished_row_plans_a_plain_row_removal(self):
        rows = [_rom(1)]
        plan, _ = await _plan(rows, {1: "vanished"})

        assert isinstance(plan, GroupPlan)
        assert plan.app_id is None
        assert plan.bound_row is None
        assert plan.delete_ids == {1}

    async def test_the_ledger_carries_the_freshly_read_rows(self):
        rows = [_rom(1, app_id=0x8001), _rom(2)]
        _, ledger = await _plan(rows, {1: "vanished", 2: "vanished"})
        assert sorted(row.rom_id for row in ledger.rows) == [1, 2]


@pytest.mark.parametrize("status", ["live", "uncertain"])
async def test_only_a_404_ever_reaches_delete_ids(status):
    rows = [_rom(1, app_id=0x8001), _rom(2)]
    result, _ = await _plan(rows, {1: status, 2: "live"})
    if isinstance(result, GroupPlan):
        assert result.delete_ids == set()
