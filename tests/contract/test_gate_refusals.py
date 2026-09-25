"""What a gated endpoint answers while the condition it is gated on holds, and which condition answers first.

Four conditions refuse an endpoint before it does anything: a pending RetroDECK
migration (``blocked_by_migration``), a library sync in flight
(``sync_active``), a removed-game cleanup holding its run claim
(``prune_active``), and — for ``start_prune`` alone — another local-data
operation still running or holding a lease (``operation_active``). Where more
than one applies, they are asked in the order exclusive start, migration, sync,
prune active.

Every test here but the five at the bottom drives ``harness.plugin.<endpoint>``
with frontend-shaped arguments and reads the answer, so it holds wherever the
rules are enforced. Four of those five, the ``test_*_names_every_endpoint_*``
tests, read the gate decorators instead, to keep each list from falling behind
an endpoint that gains or loses its gate: when a rule moves off its decorator,
its test is rewritten to read the rule where it went, and the lists and the
tests driving them do not change. The fifth,
``test_every_gated_endpoint_has_its_arguments``, reads only the lists. Outside
this module, ``tests/test_plugin.py``'s ``TestMigrationBlockedDecoratorCoverage``
reads ``@migration_blocked`` as well.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any

import pytest

from domain.sync_state import SyncState

from ._harness import hold_migration_pending, hold_prune_active, hold_sync_in_flight, release_prune_active
from ._seed import seed_rom

_MIGRATION_MESSAGE = "Pending RetroDECK migration. Open the plugin QAM to migrate or dismiss."

_PRUNE_PREVIEW_REQUEST = {"scope": "bulk", "rom_id": None, "preview_id": None, "offset": 0, "limit": 50}
_START_PRUNE_REQUEST = {
    "preview_id": "no-such-preview",
    "confirmed": True,
    "repoint_shortcuts": True,
    "remove_rows": True,
    "remove_fully_vanished": True,
    "create_recovery_bundle": False,
    "installed_selection_id": None,
}
_SAVE_SYNC_SETTINGS = {
    "save_sync_enabled": True,
    "sync_before_launch": True,
    "sync_after_exit": True,
    "default_slot": "default",
    "autocleanup_limit": 10,
}
_APP_ID = 0x80000001

# Every gated endpoint's arguments, positional and typed the way
# ``frontend/src/api/backend.ts`` declares them.
_ARGS: dict[str, tuple[Any, ...]] = {
    "adopt_existing_rom": (41, None, None),
    "apply_sgdb_game_id": (41, 7),
    "apply_steam_input_setting": (),
    "cleanup_orphaned_grid_images": ([], False),
    "clear_game_core": (41,),
    "clear_sync_cache": (),
    "confirm_slot_choice": (41, "default", False, None, False),
    "connect_with_credentials": ("https://server.example", "user", "pass", False),
    "connect_with_pairing_code": ("https://server.example", "code", False),
    "connect_with_token": ("https://server.example", "token", False),
    "copy_save_to_slot": (41, 7, "default"),
    "delete_bios_file": ("n64", "pifdata.bin"),
    "delete_bios_folder": ("n64", "bios"),
    "delete_local_saves": (41,),
    "delete_platform_bios": ("n64",),
    "delete_platform_saves": ("n64",),
    "delete_slot": (41, "default"),
    "download_all_firmware": ("n64",),
    "download_platform_firmware_file": ("n64", "pifdata.bin"),
    "download_required_firmware": ("n64",),
    "evaluate_launch": (_APP_ID,),
    "fetch_cover_base64": (41,),
    "finalize_game_session": (41,),
    "get_installed_relaunch_options": (),
    "get_prune_preview": (_PRUNE_PREVIEW_REQUEST,),
    "get_rom_relaunch_options": (41,),
    "get_save_slots": (41,),
    "get_save_status": (41,),
    "get_sgdb_artwork_base64": (41, 0),
    "get_sgdb_resolution": (41,),
    "migrate_retrodeck_files": (None,),
    "pre_launch_sync": (41,),
    "reconcile_playtime": (41,),
    "reconcile_shortcuts": ([],),
    "record_session_start": (41,),
    "refresh_cover_artwork": (41,),
    "refresh_save_status": (41,),
    "remove_all_shortcuts": (),
    "remove_platform_shortcuts": ("n64",),
    "remove_rom": (41,),
    "report_removal_results": ([], None),
    "report_unit_results": ({}, "run", "unit", 0),
    "resolve_sync_conflict": (41, "Game.srm", 7, "keep_local"),
    "resume_download": (41,),
    "save_collection_sync": ("7", "standard", True),
    "save_collections_sync": (["7"], "standard", True),
    "save_custom_headers": ([],),
    "save_platform_sync": (7, True),
    "save_server_url": ("https://server.example", False),
    "save_shortcut_icon": (_APP_ID, ""),
    "saves_rollback_to_version": (41, "default", 7),
    "select_disc": (41, None),
    "set_all_platforms_sync": (True,),
    "set_game_core": (41, "core"),
    "set_system_core": ("n64", ""),
    "sign_out": (),
    "start_download": (41, False, None, None, False),
    "start_prune": (_START_PRUNE_REQUEST,),
    "start_sync": (),
    "switch_slot": (41, "default"),
    "switch_version": (_APP_ID, 41, False),
    "sync_all_saves": (),
    "sync_apply_delta": ("preview",),
    "sync_preview": (),
    "sync_rom_saves": (41,),
    "test_connection": (),
    "uninstall_all_roms": (),
    "update_save_sync_settings": (_SAVE_SYNC_SETTINGS,),
}

PRUNE_ACTIVE = (
    "adopt_existing_rom",
    "apply_sgdb_game_id",
    "apply_steam_input_setting",
    "cleanup_orphaned_grid_images",
    "clear_game_core",
    "clear_sync_cache",
    "confirm_slot_choice",
    "connect_with_credentials",
    "connect_with_pairing_code",
    "connect_with_token",
    "copy_save_to_slot",
    "delete_local_saves",
    "delete_platform_saves",
    "delete_slot",
    "evaluate_launch",
    "fetch_cover_base64",
    "finalize_game_session",
    "get_installed_relaunch_options",
    "get_rom_relaunch_options",
    "get_save_slots",
    "get_save_status",
    "get_sgdb_artwork_base64",
    "get_sgdb_resolution",
    "migrate_retrodeck_files",
    "pre_launch_sync",
    "reconcile_playtime",
    "reconcile_shortcuts",
    "record_session_start",
    "refresh_cover_artwork",
    "refresh_save_status",
    "remove_all_shortcuts",
    "remove_platform_shortcuts",
    "remove_rom",
    "report_removal_results",
    "report_unit_results",
    "resolve_sync_conflict",
    "resume_download",
    "save_custom_headers",
    "save_server_url",
    "save_shortcut_icon",
    "saves_rollback_to_version",
    "select_disc",
    "set_game_core",
    "set_system_core",
    "sign_out",
    "start_download",
    "start_sync",
    "switch_slot",
    "switch_version",
    "sync_all_saves",
    "sync_apply_delta",
    "sync_preview",
    "sync_rom_saves",
    "test_connection",
    "uninstall_all_roms",
)

MIGRATION = (
    "adopt_existing_rom",
    "cleanup_orphaned_grid_images",
    "clear_game_core",
    "clear_sync_cache",
    "confirm_slot_choice",
    "copy_save_to_slot",
    "delete_bios_file",
    "delete_bios_folder",
    "delete_local_saves",
    "delete_platform_bios",
    "delete_platform_saves",
    "delete_slot",
    "download_all_firmware",
    "download_platform_firmware_file",
    "download_required_firmware",
    "get_prune_preview",
    "pre_launch_sync",
    "refresh_cover_artwork",
    "remove_all_shortcuts",
    "remove_platform_shortcuts",
    "remove_rom",
    "resolve_sync_conflict",
    "resume_download",
    "save_collection_sync",
    "save_collections_sync",
    "save_platform_sync",
    "saves_rollback_to_version",
    "select_disc",
    "set_all_platforms_sync",
    "set_game_core",
    "set_system_core",
    "start_download",
    "start_prune",
    "start_sync",
    "switch_slot",
    "switch_version",
    "sync_all_saves",
    "sync_apply_delta",
    "sync_preview",
    "sync_rom_saves",
    "uninstall_all_roms",
    "update_save_sync_settings",
)

SYNC_ACTIVE = (
    "cleanup_orphaned_grid_images",
    "get_prune_preview",
    "remove_all_shortcuts",
    "remove_platform_shortcuts",
    "start_prune",
    "uninstall_all_roms",
)

EXCLUSIVE_START = ("start_prune",)

_IN_FLIGHT = [SyncState.RUNNING, SyncState.CANCELLING]


async def _call(harness, endpoint: str) -> dict[str, Any]:
    return await getattr(harness.plugin, endpoint)(*_ARGS[endpoint])


def _assert_refused(result: dict[str, Any], reason: str) -> None:
    assert result["success"] is False
    assert result["reason"] == reason
    assert isinstance(result["message"], str)
    assert result["message"]


def _assert_migration_refusal(result: dict[str, Any]) -> None:
    assert result["success"] is False
    assert result["reason"] == "blocked_by_migration"
    assert result["message"] == _MIGRATION_MESSAGE
    assert "blocked_by_migration" not in result


async def _until_entered(entered: asyncio.Event, running: asyncio.Task[Any]) -> None:
    """Wait for ``entered``, failing fast when ``running`` ends or stalls before it is set."""
    waiting = asyncio.create_task(entered.wait())
    await asyncio.wait({waiting, running}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
    waiting.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await waiting
    if entered.is_set():
        return
    if running.done():
        pytest.fail(f"the endpoint answered before it reached the held call: {running.result()!r}")
    running.cancel()
    pytest.fail("the endpoint neither reached the held call nor answered within 10 s")


def _endpoints_marked(marker: str) -> set[str]:
    from host.dispatch import reachable_methods
    from main import Plugin

    return {name for name, method in reachable_methods(Plugin()).items() if getattr(method, marker, False)}


# ── The four refusals ────────────────────────────────────────────────────────


@pytest.mark.parametrize("endpoint", PRUNE_ACTIVE)
async def test_a_held_cleanup_refuses_the_endpoint(harness, endpoint):
    hold_prune_active(harness)

    _assert_refused(await _call(harness, endpoint), "prune_active")

    # The refusal registered nothing: once the cleanup lets go, the exclusive
    # start finds no conflicting operation.
    release_prune_active(harness)
    assert (await harness.plugin.start_prune(_START_PRUNE_REQUEST))["reason"] == "stale_preview"


@pytest.mark.parametrize("endpoint", MIGRATION)
async def test_a_pending_migration_refuses_the_endpoint(harness, endpoint):
    hold_migration_pending(harness)

    _assert_migration_refusal(await _call(harness, endpoint))


@pytest.mark.parametrize("state", _IN_FLIGHT)
@pytest.mark.parametrize("endpoint", SYNC_ACTIVE)
async def test_a_sync_in_flight_refuses_the_endpoint(harness, endpoint, state):
    hold_sync_in_flight(harness, state)

    _assert_refused(await _call(harness, endpoint), "sync_active")


async def test_the_migration_refusal_is_the_canonical_failure_shape(harness):
    hold_migration_pending(harness)

    assert await harness.plugin.save_platform_sync(*_ARGS["save_platform_sync"]) == {
        "success": False,
        "reason": "blocked_by_migration",
        "message": _MIGRATION_MESSAGE,
    }


async def test_start_prune_is_refused_while_a_lease_is_held_and_gets_past_the_gate_once_it_is_released(harness):
    seed_rom(harness, 41)
    removed = await harness.plugin.remove_all_shortcuts()
    token = removed["prune_lease_token"]

    _assert_refused(await harness.plugin.start_prune(_START_PRUNE_REQUEST), "operation_active")

    await harness.plugin.release_prune_conflict_lease(token)
    assert (await harness.plugin.start_prune(_START_PRUNE_REQUEST))["reason"] == "stale_preview"


async def test_start_prune_is_refused_while_a_conflicting_endpoint_is_running(harness, monkeypatch):
    harness.plugin.settings["romm_url"] = "https://server.example"
    harness.plugin.settings["romm_api_token"] = "token"
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    answer_heartbeat = harness.romm.heartbeat

    def held_heartbeat():
        loop.call_soon_threadsafe(entered.set)
        release.wait(timeout=10)
        return answer_heartbeat()

    monkeypatch.setattr(harness.romm, "heartbeat", held_heartbeat)
    running = asyncio.create_task(harness.plugin.test_connection())
    await _until_entered(entered, running)

    try:
        _assert_refused(await harness.plugin.start_prune(_START_PRUNE_REQUEST), "operation_active")
    finally:
        release.set()
        await running

    assert (await harness.plugin.start_prune(_START_PRUNE_REQUEST))["reason"] == "stale_preview"


# ── Which condition answers first ────────────────────────────────────────────


@pytest.mark.parametrize("endpoint", sorted(set(MIGRATION) & set(PRUNE_ACTIVE)))
async def test_a_pending_migration_answers_before_a_held_cleanup(harness, endpoint):
    hold_migration_pending(harness)
    hold_prune_active(harness)

    _assert_migration_refusal(await _call(harness, endpoint))
    # Nothing the refused call registered stays behind: once the cleanup lets
    # go, the exclusive start, asked first, finds no conflicting operation and
    # the migration answers.
    release_prune_active(harness)
    _assert_migration_refusal(await harness.plugin.start_prune(_START_PRUNE_REQUEST))


@pytest.mark.parametrize("endpoint", sorted(set(MIGRATION) & set(SYNC_ACTIVE)))
async def test_a_pending_migration_answers_before_a_sync_in_flight(harness, endpoint):
    hold_migration_pending(harness)
    hold_sync_in_flight(harness)

    _assert_migration_refusal(await _call(harness, endpoint))


@pytest.mark.parametrize("endpoint", sorted(set(SYNC_ACTIVE) & set(PRUNE_ACTIVE)))
async def test_a_sync_in_flight_answers_before_a_held_cleanup(harness, endpoint):
    hold_sync_in_flight(harness)
    hold_prune_active(harness)

    _assert_refused(await _call(harness, endpoint), "sync_active")


@pytest.mark.parametrize("endpoint", sorted(set(MIGRATION) & set(SYNC_ACTIVE) & set(PRUNE_ACTIVE)))
async def test_a_pending_migration_answers_before_both_other_conditions(harness, endpoint):
    hold_migration_pending(harness)
    hold_sync_in_flight(harness)
    hold_prune_active(harness)

    _assert_migration_refusal(await _call(harness, endpoint))


async def test_a_held_lease_answers_start_prune_before_a_pending_migration(harness):
    seed_rom(harness, 41)
    assert (await harness.plugin.remove_all_shortcuts())["prune_lease_token"]
    hold_migration_pending(harness)

    _assert_refused(await harness.plugin.start_prune(_START_PRUNE_REQUEST), "operation_active")


async def test_a_held_lease_answers_start_prune_before_a_sync_in_flight(harness):
    seed_rom(harness, 41)
    assert (await harness.plugin.remove_all_shortcuts())["prune_lease_token"]
    hold_sync_in_flight(harness)

    _assert_refused(await harness.plugin.start_prune(_START_PRUNE_REQUEST), "operation_active")


async def test_start_prune_refused_for_a_pending_migration_leaves_no_cleanup_claim_behind(harness):
    hold_migration_pending(harness)

    _assert_migration_refusal(await harness.plugin.start_prune(_START_PRUNE_REQUEST))

    assert (await harness.plugin.test_connection())["reason"] == "config_error"


async def test_start_prune_refused_for_a_sync_in_flight_leaves_no_cleanup_claim_behind(harness):
    hold_sync_in_flight(harness)

    _assert_refused(await harness.plugin.start_prune(_START_PRUNE_REQUEST), "sync_active")

    assert (await harness.plugin.test_connection())["reason"] == "config_error"


# ── The lists above against the gates they stand for ─────────────────────────


def test_the_prune_active_matrix_names_every_endpoint_the_gate_covers():
    """Holds ``PRUNE_ACTIVE`` equal to the endpoints ``@prune_active_blocked`` marks.

    Reads the decorator, not behaviour.
    """
    assert set(PRUNE_ACTIVE) == _endpoints_marked("_prune_active_blocked")


def test_the_migration_matrix_names_every_endpoint_the_gate_covers():
    """Holds ``MIGRATION`` equal to the endpoints ``@migration_blocked`` marks.

    Reads the decorator, not behaviour.
    """
    assert set(MIGRATION) == _endpoints_marked("_migration_blocked")


def test_the_sync_active_matrix_names_every_endpoint_the_gate_covers():
    """Holds ``SYNC_ACTIVE`` equal to the endpoints ``@sync_active_blocked`` marks.

    Reads the decorator, not behaviour.
    """
    assert set(SYNC_ACTIVE) == _endpoints_marked("_sync_active_blocked")


def test_the_exclusive_start_names_every_endpoint_the_gate_covers():
    """Holds ``EXCLUSIVE_START`` equal to the endpoints ``@prune_exclusive_start`` marks.

    Reads the decorator, not behaviour.
    """
    assert set(EXCLUSIVE_START) == _endpoints_marked("_prune_exclusive_start")


def test_every_gated_endpoint_has_its_arguments():
    assert set(_ARGS) == set(PRUNE_ACTIVE) | set(MIGRATION) | set(SYNC_ACTIVE) | set(EXCLUSIVE_START)
