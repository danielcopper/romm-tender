"""Contract tests: the per-platform sync toggles are kept by each platform's ``fs_slug`` (#2452 D8, #2798).

Drives the real ``Endpoints`` over the real ``bootstrap()``, from a settings
file an earlier release wrote where the test is about the update, and reads the
real ``settings.json`` back. ``save_platform_sync`` is called as the frontend
declares it: ``(platform_folder: string, enabled: boolean)``.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from tests.contract._harness import ContractHarness, build_contract_harness

if TYPE_CHECKING:
    from pathlib import Path

# Two RomM platforms of one console, each in a folder of its own.
_SHARING_A_SLUG: list[dict[str, Any]] = [
    {"id": 1, "name": "Super Nintendo", "slug": "snes", "fs_slug": "snes", "rom_count": 3},
    {"id": 2, "name": "Super Nintendo", "slug": "snes", "fs_slug": "snes-hacks", "rom_count": 1},
]


def _updated_from(tmp_path: Path, settings: dict[str, Any]) -> ContractHarness:
    """The harness over a ``settings.json`` an earlier release wrote, listing the two platforms."""
    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    harness = build_contract_harness(tmp_path)
    harness.romm.platforms = [dict(p) for p in _SHARING_A_SLUG]
    return harness


def _on_disk(harness: ContractHarness) -> dict[str, Any]:
    with open(os.path.join(harness.settings_dir, "settings.json"), encoding="utf-8") as f:
        return json.load(f)


def _shown(result: dict[str, Any]) -> dict[str, bool]:
    return {p["fs_slug"]: p["sync_enabled"] for p in result["platforms"]}


async def test_a_toggle_saved_by_rom_m_s_platform_id_is_kept_by_its_fs_slug_after_the_next_listing_read(tmp_path):
    """Done when: a switch saved by RomM's platform id is kept by its platform's fs_slug after the next listing read."""
    harness = _updated_from(tmp_path, {"version": 14, "enabled_platforms": {"1": False, "2": True}})

    result = await harness.endpoints.get_platforms()

    assert _shown(result) == {"snes": False, "snes-hacks": True}
    on_disk = _on_disk(harness)
    assert on_disk["version"] == 15
    assert on_disk["enabled_platforms"] == {"snes": False, "snes-hacks": True}
    assert on_disk["enabled_platform_ids"] is None


async def test_the_first_sync_after_the_update_reads_the_platforms_by_their_moved_toggles(tmp_path):
    harness = _updated_from(tmp_path, {"version": 14, "enabled_platforms": {"1": False, "2": True}})

    synced, left_off = await harness.app.services.sync_service._fetcher._fetch_enabled_platforms()

    assert [p["fs_slug"] for p in synced] == ["snes-hacks"]
    assert left_off == (1,)


async def test_an_installation_whose_toggles_were_never_touched_keeps_every_platform_on(tmp_path):
    """#2452 D24: every platform was on before the update, and stays on."""
    harness = _updated_from(tmp_path, {"version": 14, "enabled_platforms": {}})

    result = await harness.endpoints.get_platforms()

    assert _shown(result) == {"snes": True, "snes-hacks": True}
    assert _on_disk(harness)["enabled_platforms"] == {"snes": True, "snes-hacks": True}


async def test_a_fresh_setup_starts_with_every_platform_off(tmp_path):
    """#2452 D24: nothing is switched on when the Platforms tab first opens, and a sync reads no platform."""
    harness = build_contract_harness(tmp_path)
    harness.romm.platforms = [dict(p) for p in _SHARING_A_SLUG]

    result = await harness.endpoints.get_platforms()
    synced, _left_off = await harness.app.services.sync_service._fetcher._fetch_enabled_platforms()

    assert _shown(result) == {"snes": False, "snes-hacks": False}
    assert synced == []
    on_disk = _on_disk(harness)
    assert on_disk["enabled_platforms"] == {}
    assert "enabled_platform_ids" not in on_disk


async def test_of_two_platforms_sharing_a_slug_switching_one_off_syncs_only_the_other(tmp_path):
    """Done when: of two platforms that share a slug, switching one off syncs only the other."""
    harness = build_contract_harness(tmp_path)
    harness.romm.platforms = [dict(p) for p in _SHARING_A_SLUG]
    assert (await harness.endpoints.set_all_platforms_sync(True)) == {"success": True}

    assert (await harness.endpoints.save_platform_sync("snes-hacks", False)) == {"success": True}
    synced, _left_off = await harness.app.services.sync_service._fetcher._fetch_enabled_platforms()

    assert [p["fs_slug"] for p in synced] == ["snes"]
    assert _on_disk(harness)["enabled_platforms"] == {"snes": True, "snes-hacks": False}


async def test_a_toggle_asked_by_rom_m_s_platform_id_is_refused(tmp_path):
    harness = build_contract_harness(tmp_path)

    answer = await harness.endpoints.save_platform_sync(2, False)

    assert answer == {"success": False, "reason": "invalid_value", "message": "2 is not a RomM platform folder"}
