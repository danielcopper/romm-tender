"""Contract tests for the platform-toggle endpoints (#1007, #2452 D8, D24).

Drives the real ``Endpoints`` over the real ``bootstrap`` to pin the
end-to-end behavior: opening the Platforms page (``get_platforms``) switches
nothing on and writes no toggle, and switching exactly ONE platform
(``save_platform_sync``) leaves every other platform as it was — both on disk
and in the sync-time platform filter.

Each endpoint is driven exactly as the frontend declares it in
``frontend/src/api/backend.ts``: ``get_platforms()`` zero-arg,
``save_platform_sync`` with ``(platform_folder: string, enabled: boolean)``.
"""

from __future__ import annotations

import json
import os
from typing import Any


def _read_settings(harness) -> dict[str, Any]:
    """Read the real on-disk ``settings.json`` the bootstrap wrote under the user home."""
    path = os.path.join(harness.settings_dir, "settings.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def test_get_platforms_shows_every_platform_off_and_writes_no_toggle(harness):
    """A fresh setup: opening the Platforms page switches nothing on (#2452 D24)."""
    harness.romm.platforms = [
        {"id": 1, "name": "Super Nintendo", "slug": "snes", "rom_count": 3},
        {"id": 2, "name": "Nintendo 64", "slug": "n64", "rom_count": 4},
        {"id": 3, "name": "Empty", "slug": "empty", "rom_count": 0},  # filtered out
    ]

    result = await harness.endpoints.get_platforms()

    assert result["success"] is True
    assert [p["slug"] for p in result["platforms"]] == ["snes", "n64"]
    assert all(p["sync_enabled"] is False for p in result["platforms"])

    on_disk = _read_settings(harness)
    assert on_disk["enabled_platforms"] == {}


async def test_one_off_toggle_leaves_other_platforms_enabled(harness):
    """#1007: Enable all → save_platform_sync(one, False) → others stay enabled.

    Pins the guarantee across the real settings round-trip and the sync-time
    filter that drives stale-ROM removal: a single OFF write must never turn
    every other platform off, which would mark their bound ROMs stale.
    """
    harness.romm.platforms = [
        {"id": 1, "name": "Super Nintendo", "slug": "snes", "rom_count": 3},
        {"id": 2, "name": "Nintendo 64", "slug": "n64", "rom_count": 4},
        {"id": 3, "name": "Game Boy Advance", "slug": "gba", "rom_count": 5},
    ]

    # 1. Every platform switched on from the Platforms page.
    assert await harness.endpoints.set_all_platforms_sync(True) == {"success": True}

    # 2. Switch exactly one platform off (frontend: folder, bool enabled).
    toggle_result = await harness.endpoints.save_platform_sync("n64", False)
    assert toggle_result == {"success": True}

    # 3. The on-disk map is a true partial update — only n64 flipped.
    on_disk = _read_settings(harness)
    assert on_disk["enabled_platforms"] == {"snes": True, "n64": False, "gba": True}

    # 4. The sync-time filter keeps every OTHER platform.
    filtered, _unwalked = await harness.app.services.sync_service._fetcher._fetch_enabled_platforms()
    kept_slugs = {p["slug"] for p in filtered}
    assert kept_slugs == {"snes", "gba"}
    assert "n64" not in kept_slugs


async def test_get_platforms_after_one_toggle_on_a_fresh_setup_switches_nothing_more_on(harness):
    """Reopening the page after one toggle reads the map literally and writes nothing more (#2452 D24)."""
    harness.romm.platforms = [
        {"id": 1, "name": "Super Nintendo", "slug": "snes", "rom_count": 3},
        {"id": 2, "name": "Nintendo 64", "slug": "n64", "rom_count": 4},
    ]

    await harness.endpoints.get_platforms()
    await harness.endpoints.save_platform_sync("snes", False)

    result = await harness.endpoints.get_platforms()
    by_folder = {p["fs_slug"]: p["sync_enabled"] for p in result["platforms"]}
    assert by_folder == {"snes": False, "n64": False}

    on_disk = _read_settings(harness)
    assert on_disk["enabled_platforms"] == {"snes": False}
