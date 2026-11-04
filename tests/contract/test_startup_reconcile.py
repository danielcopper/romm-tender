"""Contract tests for the launch-options reconcile read endpoint.

Driven frontend-shaped per ``frontend/src/api/backend.ts``:
``getBoundLaunchOptions = endpoint<[], {success, items: {app_id, launch_options}[], prune_lease_token}>``.

The frontend pulls this on mount (after backend reachability is proven) and
after every sync, and confirm-sets each listed shortcut's launch options to its
appId (#1043, #2221 D10). The contract is the list SHAPE: zero items when
nothing is bound, and a ``{app_id, launch_options}`` dict per bound ROM whose
launch options in Steam's shortcut file are not its appId — every bound ROM
here, since the harness's Steam holds no shortcut file.
"""

from __future__ import annotations

from ._seed import seed_install, seed_rom


async def test_nothing_bound_returns_empty_list(harness):
    """Nothing bound → empty list (the frontend no-ops on it)."""
    result = await harness.endpoints.get_bound_launch_options()
    assert result == {"success": True, "items": [], "prune_lease_token": None}


async def test_a_bound_rom_is_listed_with_its_app_id_as_its_launch_options(harness):
    """A bound ROM → one {app_id, launch_options} item whose launch options are its appId."""
    seed_install(harness, 42, system="gba", platform_slug="gba", file_name="pokemon.gba")
    result = await harness.endpoints.get_bound_launch_options()
    assert result["success"] is True
    assert result["prune_lease_token"].startswith("installed_reconcile:")
    # seed_rom defaults shortcut_app_id to rom_id when not bound otherwise.
    assert result["items"] == [{"app_id": 42, "launch_options": "42"}]


async def test_a_bound_rom_without_an_install_is_listed_too(harness):
    """Every bound shortcut carries its appId, installed or not."""
    seed_rom(harness, 7, platform_slug="gba", shortcut_app_id=7)
    result = await harness.endpoints.get_bound_launch_options()
    assert result["items"] == [{"app_id": 7, "launch_options": "7"}]


async def test_an_unbound_rom_is_not_listed(harness):
    seed_rom(harness, 8, platform_slug="gba", shortcut_app_id=None)
    result = await harness.endpoints.get_bound_launch_options()
    assert result == {"success": True, "items": [], "prune_lease_token": None}
