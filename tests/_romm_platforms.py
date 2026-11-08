"""RomM platforms as RomM's ``/api/platforms`` sends them, shared by the tests that read a listing whole.

Each is RomM 5.4.1's ``PlatformSchema`` (``backend/endpoints/responses/platform.py``
at that tag) less its firmware, which a test adds where it needs one.
"""

from __future__ import annotations

from typing import Any

LISTED_GAMECUBE: dict[str, Any] = {
    "id": 12,
    "slug": "ngc",
    "fs_slug": "gc",
    "rom_count": 40,
    "name": "Nintendo GameCube",
    "igdb_slug": "ngc",
    "moby_slug": "gamecube",
    "hltb_slug": None,
    "libretro_slug": "Nintendo - GameCube",
    "custom_name": "GameCube",
    "description": None,
    "igdb_id": 21,
    "sgdb_id": None,
    "moby_id": 14,
    "launchbox_id": 30,
    "ss_id": 13,
    "ra_id": 16,
    "hasheous_id": None,
    "tgdb_id": 2,
    "flashpoint_id": None,
    "category": "Console",
    "generation": 6,
    "family_name": "Nintendo",
    "family_slug": "nintendo",
    "url": None,
    "url_logo": None,
    "firmware": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "fs_size_bytes": 0,
    "is_unidentified": False,
    "is_identified": True,
    "missing_from_fs": False,
    "display_name": "GameCube",
    "abbreviation": "NGC",
    "alternative_names": [],
    "firmware_count": 0,
}
