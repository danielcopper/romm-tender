"""Contract test: the download queue names a platform as the rest of Tender does (#2573).

RomM's ROM answer carries no ``platform_name``; its ``platform_custom_name`` and
``platform_display_name`` are the custom name a user set, which Tender never
shows (#2564 D2). The name is the one the platform answer the download fetches
at admission carries (D1): the cached name, else the listing name kept with the
platform's ids, else the slug.

Drives the real ``DownloadService`` over the real ``PlatformSystemService`` and
real SQLite under ``tmp_path``; RomM's transport is the fake. The ROM is served
as an archive, so the download reports an extracting phase as well.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any

import pytest

from domain.platform_system import FOUND, PLATFORM_IDS_KEY, PlatformIds, encode_platform_ids

from ._seed import seed_rom

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

# RomM's own slug for a platform the catalogue calls ``gba``.
_SLUG = "game-boy-advance"
_ROM_ID = 5


def _archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("golden-sun.gba", b"GBAROM!!")
        archive.writestr("golden-sun.sav", b"SAVE")
    return buffer.getvalue()


def _seed_server(harness) -> None:
    payload = _archive()
    harness.romm.roms[_ROM_ID] = {
        "id": _ROM_ID,
        "name": "Golden Sun",
        "fs_name": "golden-sun",
        "fs_name_no_ext": "golden-sun",
        "fs_size_bytes": len(payload),
        "platform_slug": _SLUG,
        "platform_custom_name": "GBA Custom",
        "platform_display_name": "GBA Custom",
        "has_multiple_files": True,
        "files": [
            {"file_name": "golden-sun.gba", "file_size_bytes": 8},
            {"file_name": "golden-sun.sav", "file_size_bytes": 4},
        ],
    }
    harness.romm.download_payloads[f"rom:{_ROM_ID}:golden-sun"] = payload
    harness.platform_systems.answers[_SLUG] = (FOUND, "gba")
    seed_rom(harness, _ROM_ID, platform_slug=_SLUG)


def _keep(harness, *, cached: str | None, kept: str | None) -> None:
    """Keep *cached* in the platform-name cache and *kept* with the platform's ids; ``None`` keeps nothing."""
    with harness.uow_factory() as uow:
        if cached is not None:
            uow.kv_config.set("platform_names", f'{{"{_SLUG}": "{cached}"}}')
        if kept is not None:
            uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids({_SLUG: PlatformIds(name=kept)}))


async def _drain_background_tasks() -> None:
    for _ in range(6):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _sent(harness, event: str) -> list[dict[str, Any]]:
    return [call.args[1] for call in harness.emit.call_args_list if call.args[0] == event]


@pytest.mark.parametrize(
    ("cached", "kept", "shown"),
    [
        pytest.param("Game Boy Advance", "GBA (listing)", "Game Boy Advance", id="the cached name"),
        pytest.param(None, "Game Boy Advance", "Game Boy Advance", id="the name kept with the ids"),
        pytest.param(None, None, _SLUG, id="the slug where neither is kept"),
    ],
)
async def test_the_queue_its_frames_and_its_completion_name_the_platform(harness, cached, kept, shown):
    _seed_server(harness)
    _keep(harness, cached=cached, kept=kept)

    started = await harness.endpoints.start_download(_ROM_ID)
    queued = harness.endpoints.get_download_queue()["downloads"]
    await _drain_background_tasks()

    assert started["success"] is True
    assert [entry["platform_name"] for entry in queued] == [shown]
    frames = _sent(harness, "download_progress")
    assert {frame["status"] for frame in frames} >= {"downloading", "extracting"}
    assert {frame["platform_name"] for frame in frames} == {shown}
    assert [event["platform_name"] for event in _sent(harness, "download_complete")] == [shown]
