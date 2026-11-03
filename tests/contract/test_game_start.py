"""Contract test for ``prepare_game_start`` over the real wiring (#2221 D7, D15, D2).

The endpoint resolves the entry in effect, asks the real reading module
(``adapters/atlas_launch.py``) for the resolver's command over the RetroDECK
tree the harness seeds, and hands it over — or refuses. A refusal is the wire's
failure shape with the game's ``start`` beside it, which the panel words as the
game page does. The harness has no runtime directory, so no hand-over is wired:
what reaches the launcher is pinned in ``tests/services/test_game_start.py`` and
``tests/adapters/test_start_handover.py``.
"""

from __future__ import annotations

from ._seed import seed_es_systems, seed_install, seed_rom


async def test_a_game_tender_does_not_track_is_refused_not_found(harness):
    answer = await harness.endpoints.prepare_game_start(999)

    assert (answer["success"], answer["reason"]) == (False, "not_found")


async def test_a_game_no_emulator_starts_is_refused_with_its_start(harness):
    """No catalogue answers for its system: nothing starts it, and RetroDECK is not opened instead (#2221 D20)."""
    seed_install(harness, 42, system="gba", platform_slug="gba", file_name="pokemon.gba")

    answer = await harness.endpoints.prepare_game_start(42)

    assert (answer["success"], answer["reason"]) == (False, "cannot_start")
    assert answer["start"] == {"state": "no_entry", "reason": None, "entry": None}


async def test_a_game_that_is_not_downloaded_is_refused_not_installed(harness):
    seed_rom(harness, 7, platform_slug="gba", shortcut_app_id=7)

    answer = await harness.endpoints.prepare_game_start(7)

    assert (answer["success"], answer["reason"]) == (False, "not_installed")


async def test_a_game_whose_command_the_resolver_does_not_complete_is_refused_with_its_reason(harness):
    """The seeded deploy states no ES-DE build, so how ES-DE would start the game is not established (D2)."""
    seed_es_systems(harness)
    seed_install(harness, 42, system="gba", platform_slug="gba", file_name="pokemon.gba")

    answer = await harness.endpoints.prepare_game_start(42)

    assert answer["success"] is False
    assert answer["reason"] == "cannot_start"
    assert answer["start"] == {"state": "cannot_start", "reason": "frontend_build_unread", "entry": "mGBA"}
