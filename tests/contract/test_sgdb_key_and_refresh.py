"""Contract tests for removing the SteamGridDB API key and for Refresh Artwork's fresh download.

Drives ``remove_sgdb_api_key`` and ``refresh_sgdb_artwork`` over the real wired
backend, positionally and typed as ``frontend/src/api/backend.ts`` declares
them: a removed key reads as no key and leaves the artwork cache alone, and the
fresh download replaces a cached image only with one that arrived, or answers
the failure shape when SteamGridDB refuses the key.
"""

from __future__ import annotations

import os

from lib.errors import SgdbApiError

_ROM_ID = 41


def _artwork_dir(harness) -> str:
    path = os.path.join(harness.cache_dir, "artwork")
    os.makedirs(path, exist_ok=True)
    return path


def _cache_old_artwork(harness) -> dict[str, str]:
    art_dir = _artwork_dir(harness)
    paths = {
        "hero": os.path.join(art_dir, f"{_ROM_ID}_hero.png"),
        "logo": os.path.join(art_dir, f"{_ROM_ID}_logo_11.png"),
        "grid": os.path.join(art_dir, f"{_ROM_ID}_grid_12.png"),
        "icon": os.path.join(art_dir, f"{_ROM_ID}_icon.png"),
    }
    for path in paths.values():
        with open(path, "wb") as f:
            f.write(b"old")
    return paths


def _held(harness) -> dict[str, bytes]:
    art_dir = _artwork_dir(harness)
    held = {}
    for name in sorted(os.listdir(art_dir)):
        with open(os.path.join(art_dir, name), "rb") as f:
            held[name] = f.read()
    return held


async def test_a_removed_key_reads_as_none_and_the_cache_stays(harness):
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    _cache_old_artwork(harness)
    before = _held(harness)

    answer = harness.endpoints.remove_sgdb_api_key()

    assert answer == {"success": True, "message": "SteamGridDB API key removed"}
    assert harness.endpoints.get_settings()["sgdb_api_key_masked"] == ""
    assert _held(harness) == before


async def test_refresh_replaces_each_cached_image_with_the_new_download(harness):
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    _cache_old_artwork(harness)
    for asset_type, image_id in (("hero", 801), ("logo", 802), ("grid", 803), ("icon", 804)):
        url = f"https://cdn.example/{image_id}.png"
        harness.sgdb.seed_artwork(7, asset_type, url, image_id)
        harness.sgdb.seed_image_bytes(url, f"new {asset_type}".encode())

    answer = await harness.endpoints.refresh_sgdb_artwork(_ROM_ID, 7)

    assert answer == {"success": True}
    assert _held(harness) == {
        f"{_ROM_ID}_grid_803.png": b"new grid",
        f"{_ROM_ID}_hero.png": b"new hero",
        f"{_ROM_ID}_icon.png": b"new icon",
        f"{_ROM_ID}_logo_802.png": b"new logo",
    }


async def test_a_refused_key_answers_the_failure_shape_and_keeps_the_cache(harness):
    harness.settings["steamgriddb_api_key"] = "fake-sgdb-key"
    _cache_old_artwork(harness)
    before = _held(harness)
    harness.sgdb.request_side_effect = SgdbApiError(status_code=401, message="Unauthorized")

    answer = await harness.endpoints.refresh_sgdb_artwork(_ROM_ID, 7)

    assert answer == {
        "success": False,
        "reason": "auth_failed",
        "message": "SteamGridDB refused the API key — check it in Settings",
    }
    assert _held(harness) == before
