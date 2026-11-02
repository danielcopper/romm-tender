"""Contract test for the ``stop_running_game`` endpoint over the real ``Endpoints``.

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``stopRunningGame = endpoint<[number], StopGameResult>`` — the rom id, passed
positionally.

Pins both response shapes across the whole wire (real bootstrap → real
``wire_services`` → real ``GameProcessService`` over real SQLite), with only the
process table itself faked:

* nothing running under the ROM's shortcut — another game's tree alive or not —
  → the canonical ``{success: False, reason: "not_running", message}`` failure,
  never the forbidden ``error`` / ``error_code`` keys, with nothing signalled;
* the tree Steam started for this ROM's shortcut → ``{success: True, stopped,
  force_killed}``.

The shortcut appId each tree is looked up by comes from the ROM's real
``roms.shortcut_app_id`` row, so this tier also pins that the service reads the
binding the sync recorded.
"""

from __future__ import annotations

from ._seed import seed_install, seed_rom

_OTHER_GAMES_APP_ID = 9001


def _add_tree(harness, app_id: int, pids: list[int]) -> None:
    """Put the live tree Steam started for shortcut *app_id* on the table."""
    harness.game_process.add_tree(app_id, pids)


async def test_stop_running_game_with_nothing_running_returns_the_canonical_failure(harness):
    seed_install(harness, 42)

    result = await harness.endpoints.stop_running_game(42)

    assert result == {
        "success": False,
        "reason": "not_running",
        "message": result["message"],
    }
    assert isinstance(result["message"], str)
    assert result["message"]
    assert "error" not in result
    assert "error_code" not in result


async def test_stop_running_game_stops_only_the_instance_running_this_rom(harness):
    seed_install(harness, 42, file_name="ours.gba")
    _add_tree(harness, _OTHER_GAMES_APP_ID, [4201, 4202])
    _add_tree(harness, 42, [4101, 4102])

    result = await harness.endpoints.stop_running_game(42)

    assert result == {"success": True, "stopped": 2, "force_killed": 0}
    assert harness.game_process.stop_calls == [4101, 4102]
    assert harness.game_process.kill_calls == []
    # The other live game was never signalled on either rung.
    assert 4201 not in harness.game_process.stop_calls
    assert 4202 not in harness.game_process.stop_calls


async def test_stop_running_game_refuses_when_no_instance_runs_this_rom(harness):
    seed_install(harness, 42, file_name="ours.gba")
    _add_tree(harness, _OTHER_GAMES_APP_ID, [4201])

    result = await harness.endpoints.stop_running_game(42)

    assert result == {
        "success": False,
        "reason": "not_running",
        "message": result["message"],
    }
    assert isinstance(result["message"], str)
    assert result["message"]
    assert "error" not in result
    assert "error_code" not in result
    # Refusing means refusing: the other game's process was left alone.
    assert harness.game_process.stop_calls == []
    assert harness.game_process.kill_calls == []


async def test_stop_running_game_refuses_for_a_rom_bound_to_no_shortcut(harness):
    # Nothing Steam runs belongs to an unbound ROM — not even a tree under the
    # appId its row would have carried — and "attribute it to whatever is
    # running" is exactly the behaviour this endpoint's rom_id exists to prevent.
    seed_rom(harness, 42, shortcut_app_id=None)
    _add_tree(harness, 42, [4201])

    result = await harness.endpoints.stop_running_game(42)

    assert result["success"] is False
    assert result["reason"] == "not_running"
    assert harness.game_process.stop_calls == []


async def test_stop_running_game_forces_a_process_that_ignores_the_request(harness):
    seed_install(harness, 42, file_name="ours.gba")
    _add_tree(harness, 42, [4101])
    harness.game_process.survive_stop = {4101}

    result = await harness.endpoints.stop_running_game(42)

    assert result == {"success": True, "stopped": 1, "force_killed": 1}
    # Exactly one stop request even though the process stayed alive throughout —
    # the save-safety invariant, asserted end-to-end.
    assert harness.game_process.stop_calls == [4101]
    assert harness.game_process.kill_calls == [4101]


async def test_stop_running_game_looks_up_the_roms_shortcut_app_id(harness):
    seed_rom(harness, 42, shortcut_app_id=7007)

    await harness.endpoints.stop_running_game(42)

    assert harness.game_process.find_calls == [7007]
