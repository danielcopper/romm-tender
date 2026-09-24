"""Contract test — a save the home migration's ``skip`` kept stays kept at the next sync.

Drives the real ``Plugin`` through the real ``bootstrap()`` + real SQLite. A game's
save exists under both the old RetroDECK home and the new one; the user migrates
with ``skip``, which keeps the new home's copy and leaves the old one where it
is. The recorded save directory still names the old home until the migration
records the new one, and a sync that met that stale record would follow the
left-behind copy over the kept one.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, cast

from fakes.fake_retrodeck_paths import FakeRetroDeckPaths

from domain.answered_save_directory import AnsweredSaveDirectory
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.save_answer import SaveAnswer, SaveComponent

if TYPE_CHECKING:
    from fakes.fake_save_location_reader import FakeSaveLocationReader

_ROM = 1


def _answer(directory: str) -> SaveAnswer:
    return SaveAnswer(
        state="per_game_files",
        unestablished=None,
        emulator="mGBA",
        directory=directory,
        backing_directory=None,
        granularity="per-game-file",
        needs=(),
        components=(SaveComponent(name="pokemon.srm", directory=directory, role="battery", granularity=None),),
        caveats=(),
        content_installed=True,
        root_kind="savefile_directory",
    )


def _write(path: str, data: bytes, mtime: float) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _detect_at(harness, home: str) -> None:
    harness.plugin._migration_service._retrodeck_paths = FakeRetroDeckPaths(
        home=home,
        saves=os.path.join(home, "saves"),
        roms=os.path.join(home, "roms"),
        bios=os.path.join(home, "bios"),
    )
    harness.plugin._migration_service.detect_retrodeck_path_change()
    await asyncio.sleep(0)


async def test_a_save_kept_by_skip_is_neither_replaced_nor_backed_up(harness):
    old_home = str(harness.tmp_path / "old")
    new_home = str(harness.tmp_path / "new")
    old_saves = os.path.join(old_home, "saves", "gba")
    new_saves = os.path.join(new_home, "saves", "gba")
    # The left-behind copy is the NEWER one: a follow's collision rule would
    # otherwise put it over the copy the user kept.
    _write(os.path.join(old_home, "roms", "gba", "pokemon.gba"), b"rom", 1_000)
    _write(os.path.join(old_saves, "pokemon.srm"), b"left behind", 2_000)
    _write(os.path.join(new_saves, "pokemon.srm"), b"kept", 1_000)
    with harness.uow_factory() as uow:
        uow.roms.save(
            Rom.synced(
                rom_id=_ROM,
                platform_slug="gba",
                name="Pokemon",
                fs_name="pokemon.gba",
                shortcut_app_id=4242,
                synced_at="2026-01-01T00:00:00",
            )
        )
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=_ROM,
                file_path=os.path.join(old_home, "roms", "gba", "pokemon.gba"),
                rom_dir=None,
                platform_slug="gba",
                system="gba",
                installed_at="2026-01-01T00:00:00",
            )
        )
        uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=_ROM, directory=old_saves))
    save_locations = cast("FakeSaveLocationReader", harness.plugin._save_sync_service._rom_info._save_locations)
    save_locations.answer_with("gba", _answer(new_saves))
    await _detect_at(harness, old_home)
    await _detect_at(harness, new_home)

    migrated = await harness.plugin.migrate_retrodeck_files("skip")
    # The save existed at both ends, so ``skip`` left both copies where they were.
    assert sorted(os.listdir(old_saves)) == ["pokemon.srm"]
    harness.plugin.settings["save_sync_enabled"] = True
    await harness.plugin.sync_rom_saves(_ROM)

    assert migrated["success"] is True
    assert _read(os.path.join(new_saves, "pokemon.srm")) == b"kept"
    assert _read(os.path.join(old_saves, "pokemon.srm")) == b"left behind"
    assert not os.path.exists(os.path.join(new_saves, ".romm-backup"))
    assert not os.path.exists(os.path.join(old_saves, ".romm-backup"))
    with harness.uow_factory() as uow:
        record = uow.answered_save_directories.get(_ROM)
    assert record == AnsweredSaveDirectory(rom_id=_ROM, directory=new_saves)
