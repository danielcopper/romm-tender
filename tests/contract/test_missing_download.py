"""Contract tests for a download whose file is missing (#2188 D23, D28, D29).

Driven frontend-shaped: ``forgetDownload = endpoint<[number], ForgetDownloadResult>``
and ``startDownload`` over the real ``bootstrap()`` + SQLite and real file
stores under ``tmp_path``; only the RomM transport is the fake.

"Forget this download" is an uninstall without the deletion: the record goes
through the uninstall's writer of ``applied_launch_options``, no file is
touched, and it answers to the same conflict rules as ``remove_rom``.
"Download again" is an ordinary download, which leaves the old record in place
until the new one completes.
"""

from __future__ import annotations

import asyncio
import os
import threading

import pytest
from _conflict_rules import endpoints_with_rule

from domain.rom_install import RomInstall

from ._harness import hold_migration_pending, hold_prune_active, hold_update_in_progress
from ._seed import seed_install, seed_rom

_ROM_ID = 7
_STALE_LAUNCH = "flatpak run net.retrodeck.retrodeck /gone/game.gba"


def _seed_missing_download(harness) -> str:
    """An install record whose file is not on disk, bound and with a recorded launch command."""
    file_path = seed_install(harness, _ROM_ID)
    with harness.uow_factory() as uow:
        uow.roms.set_applied_launch_options(_ROM_ID, _STALE_LAUNCH)
    return file_path


def _write(path: str, data: bytes = b"rom") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _tree(root: str) -> set[str]:
    return {os.path.join(dirpath, name) for dirpath, _dirs, names in os.walk(root) for name in names}


# ── Forget this download ─────────────────────────────────────────────────────


async def test_forget_drops_the_record_through_the_uninstall_writer(harness):
    _seed_missing_download(harness)

    result = await harness.endpoints.forget_download(_ROM_ID)

    assert result["success"] is True
    assert result["prune_lease_token"].startswith("rom_uninstall:")
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(_ROM_ID) is None
        rom = uow.roms.get(_ROM_ID)
    assert rom is not None
    assert rom.applied_launch_options == ""


async def test_forget_deletes_no_file(harness):
    file_path = _seed_missing_download(harness)
    roms_root = harness.retrodeck_paths.roms_path()
    bystander = os.path.join(os.path.dirname(file_path), "other.gba")
    _write(bystander)
    before = _tree(roms_root)

    await harness.endpoints.forget_download(_ROM_ID)

    assert _tree(roms_root) == before
    assert os.path.isdir(os.path.dirname(file_path))


async def test_forget_is_refused_while_the_file_is_there(harness):
    file_path = _seed_missing_download(harness)
    _write(file_path)

    result = await harness.endpoints.forget_download(_ROM_ID)

    assert result == {
        "success": False,
        "reason": "file_present",
        "message": f"The recorded download exists: {file_path}",
        "path": file_path,
    }
    assert os.path.exists(file_path)
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(_ROM_ID) is not None
        rom = uow.roms.get(_ROM_ID)
    assert rom is not None
    assert rom.applied_launch_options == _STALE_LAUNCH


async def test_forget_of_a_rom_with_no_record_is_not_installed(harness):
    seed_rom(harness, _ROM_ID)

    result = await harness.endpoints.forget_download(_ROM_ID)

    assert result == {"success": False, "reason": "not_installed", "message": "ROM not installed"}


def test_forget_declares_the_rules_remove_rom_declares():
    for rule in ("update", "migration", "sync", "prune"):
        holders = endpoints_with_rule(rule)
        assert ("forget_download" in holders) == ("remove_rom" in holders), rule


@pytest.mark.parametrize(
    ("hold", "reason"),
    [
        (hold_update_in_progress, "blocked_by_update"),
        (hold_migration_pending, "blocked_by_migration"),
        (hold_prune_active, "prune_active"),
    ],
)
async def test_forget_is_refused_where_remove_rom_is(harness, hold, reason):
    _seed_missing_download(harness)
    hold(harness)

    forgotten = await harness.endpoints.forget_download(_ROM_ID)
    removed = await harness.endpoints.remove_rom(_ROM_ID)

    assert forgotten["reason"] == reason
    assert forgotten == removed
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(_ROM_ID) is not None


# ── Download again ───────────────────────────────────────────────────────────


async def _drain_background_tasks() -> None:
    """Await the fire-and-forget download task(s) ``start_download`` spawned."""
    for _ in range(6):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _seed_moved_download(harness) -> RomInstall:
    """A download recorded outside today's ROM folder, as after a moved folder, served by RomM."""
    seed_rom(harness, _ROM_ID)
    old = RomInstall.mark_installed(
        rom_id=_ROM_ID,
        file_path=str(harness.tmp_path / "moved-away" / "gba" / "game.gba"),
        rom_dir=None,
        platform_slug="gba",
        system="gba",
        installed_at="2026-01-01T00:00:00",
    )
    with harness.uow_factory() as uow:
        uow.rom_installs.save(old)
    harness.romm.roms[_ROM_ID] = {
        "id": _ROM_ID,
        "name": "Game",
        "fs_name": "game.gba",
        "fs_size_bytes": 8,
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "platform_name": "GBA",
    }
    harness.romm.download_payloads[f"rom:{_ROM_ID}:game.gba"] = b"NEWBYTES"
    return old


def _recorded(harness) -> RomInstall | None:
    with harness.uow_factory() as uow:
        return uow.rom_installs.get(_ROM_ID)


async def test_download_again_keeps_the_old_record_until_it_completes(harness):
    old = _seed_moved_download(harness)

    result = await harness.endpoints.start_download(_ROM_ID, False, None, None, False)

    assert result["success"] is True
    assert _recorded(harness) == old
    await _drain_background_tasks()
    new = _recorded(harness)
    assert new is not None
    assert new.file_path == os.path.join(harness.retrodeck_paths.roms_path(), "gba", "game.gba")
    detail = await harness.endpoints.get_cached_game_detail(_ROM_ID)
    assert detail["file_missing_at"] is None


async def test_a_cancelled_download_again_leaves_the_file_missing(harness):
    """Cancelled mid-transfer, so the download's own cancel handling runs."""
    old = _seed_moved_download(harness)
    transferring = threading.Event()
    release = threading.Event()
    transfer = harness.romm.download_rom_content

    def held_transfer(*args, **kwargs):
        transferring.set()
        release.wait(5)
        return transfer(*args, **kwargs)

    harness.romm.download_rom_content = held_transfer
    await harness.endpoints.start_download(_ROM_ID, False, None, None, False)
    while not transferring.is_set():
        await asyncio.sleep(0.01)
    harness.endpoints.cancel_download(_ROM_ID)
    release.set()
    await _drain_background_tasks()

    frames = [call.args[1] for call in harness.emit.await_args_list if call.args[0] == "download_progress"]
    assert frames[-1]["status"] == "cancelled"
    assert _recorded(harness) == old
    detail = await harness.endpoints.get_cached_game_detail(_ROM_ID)
    assert detail["file_missing_at"] == old.file_path


async def test_a_failed_download_again_leaves_the_file_missing(harness):
    old = _seed_moved_download(harness)
    harness.romm.download_rom_content_side_effect = RuntimeError("connection reset")

    await harness.endpoints.start_download(_ROM_ID, False, None, None, False)
    await _drain_background_tasks()

    assert [call.args[0] for call in harness.emit.await_args_list].count("download_failed") == 1
    assert _recorded(harness) == old
    detail = await harness.endpoints.get_cached_game_detail(_ROM_ID)
    assert detail["file_missing_at"] == old.file_path
