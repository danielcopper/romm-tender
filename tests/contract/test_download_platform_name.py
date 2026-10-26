"""Contract test: the download queue names a platform as the platform's system does (#2573).

RomM's ROM answer carries no ``platform_name``: its ``platform_custom_name`` is the
custom name a user set, and its ``platform_display_name`` that custom name where
one is set, else the platform's ``name``. Tender never shows a custom name
(#2564 D2). The name is the one the platform answer the download fetches at
admission carries (#2573 D1): the cached name, else the listing name kept with the
platform's ids, else the slug.

Drives the real ``DownloadService`` over the real ``PlatformSystemService`` and
real SQLite under ``tmp_path``; RomM's transport is the fake. The ROM is served
as an archive, so the download reports an extracting phase as well. Where a
download must be paused, cancelled or kept waiting, the fake's transfer is held
on its worker thread until the test lets it go.
"""

from __future__ import annotations

import asyncio
import io
import threading
import zipfile
from typing import TYPE_CHECKING, Any

import pytest

from domain.platform_system import FOUND, PLATFORM_IDS_KEY, PlatformIds, encode_platform_ids

from ._seed import seed_rom

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

# RomM's own slug for a platform the catalogue calls ``gba``.
_SLUG = "game-boy-advance"
_ROM_ID = 5


def _archive(stem: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{stem}.gba", b"GBAROM!!")
        archive.writestr(f"{stem}.sav", b"SAVE")
    return buffer.getvalue()


def _seed_server(harness, rom_id: int = _ROM_ID) -> None:
    stem = f"golden-sun-{rom_id}"
    payload = _archive(stem)
    harness.romm.roms[rom_id] = {
        "id": rom_id,
        "name": f"Golden Sun {rom_id}",
        "fs_name": stem,
        "fs_name_no_ext": stem,
        "fs_size_bytes": len(payload),
        "platform_slug": _SLUG,
        "platform_custom_name": "GBA Custom",
        "platform_display_name": "GBA Custom",
        "has_multiple_files": True,
        "files": [
            {"file_name": f"{stem}.gba", "file_size_bytes": 8},
            {"file_name": f"{stem}.sav", "file_size_bytes": 4},
        ],
    }
    harness.romm.download_payloads[f"rom:{rom_id}:{stem}"] = payload
    harness.platform_systems.answers[_SLUG] = (FOUND, "gba")
    seed_rom(harness, rom_id, platform_slug=_SLUG)


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


def _named_with_status(harness, status: str) -> list[str]:
    return [frame["platform_name"] for frame in _sent(harness, "download_progress") if frame["status"] == status]


async def _until(holds: Callable[[], bool]) -> None:
    for _ in range(500):
        if holds():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never held")


class _HeldTransfer:
    """Holds every ROM transfer of the fake on its worker thread until :meth:`release`.

    Once released, a transfer carries on into the fake's own only when
    *then_transfer* is set; otherwise it ends having written nothing.
    """

    def __init__(self, harness, monkeypatch, *, then_transfer: bool) -> None:
        self._transfer = harness.romm.download_rom_content
        self._then_transfer = then_transfer
        self._released = threading.Event()
        self._lock = threading.Lock()
        self.held = 0
        self.ended = 0
        monkeypatch.setattr(harness.romm, "download_rom_content", self._hold)

    def _hold(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.held += 1
        try:
            if not self._released.wait(timeout=10):
                raise TimeoutError("the held transfer was never released")
            if self._then_transfer:
                self._transfer(*args, **kwargs)
        finally:
            with self._lock:
                self.ended += 1

    def release(self) -> None:
        self._released.set()


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


@pytest.mark.parametrize(
    ("press", "status"),
    [
        pytest.param("pause_download", "paused", id="paused"),
        pytest.param("cancel_download", "cancelled", id="cancelled"),
    ],
)
async def test_a_paused_or_cancelled_download_s_frame_names_the_platform(harness, monkeypatch, press, status):
    _seed_server(harness)
    _keep(harness, cached="Game Boy Advance", kept=None)
    held = _HeldTransfer(harness, monkeypatch, then_transfer=False)

    assert (await harness.endpoints.start_download(_ROM_ID))["success"] is True
    await _until(lambda: held.held == 1)
    assert getattr(harness.endpoints, press)(_ROM_ID)["success"] is True
    await _drain_background_tasks()
    held.release()
    await _until(lambda: held.ended == 1)

    assert _named_with_status(harness, status) == ["Game Boy Advance"]


async def test_a_download_waiting_for_a_transfer_slot_names_the_platform(harness, monkeypatch):
    for rom_id in (_ROM_ID, 6, 7):
        _seed_server(harness, rom_id)
    _keep(harness, cached="Game Boy Advance", kept=None)
    held = _HeldTransfer(harness, monkeypatch, then_transfer=True)

    for rom_id in (_ROM_ID, 6):
        assert (await harness.endpoints.start_download(rom_id))["success"] is True
    await _until(lambda: held.held == 2)
    assert (await harness.endpoints.start_download(7))["success"] is True
    await _until(lambda: bool(_named_with_status(harness, "queued")))
    held.release()
    await _until(lambda: held.ended == 3)
    await _drain_background_tasks()

    assert _named_with_status(harness, "queued") == ["Game Boy Advance"]
    assert [event["platform_name"] for event in _sent(harness, "download_complete")] == ["Game Boy Advance"] * 3
