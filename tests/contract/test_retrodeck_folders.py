"""Contract: every folder Tender uses in RetroDECK is the real resolver's answer, over a seeded RetroDECK.

Driven through the real endpoints and the real ``RetroDeckFoldersAdapter`` over a
RetroDECK laid down under the harness home — marker, ES-DE's ROM folder setting,
deploy. The four findings that make RetroDECK's folders defaults are each
produced the way the resolver meets them, and under every one a press that would
download, delete or clean up there is refused and leaves the folders as they were
— as it is where detecting the sources, or asking the resolver about RetroDECK's
health or one of its roots, raised.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING, Any

import pytest
from _vendor.atlas.installations import RetroDeck

from domain.bios_file import BiosFile
from domain.rom_install import RomInstall

from ._seed import _retrodeck_marker_path, seed_es_systems, seed_install, seed_retrodeck_not_set_up, seed_rom

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_ROM_ID = 7


def _marker_missing(harness) -> None:
    # The marker is what detection found RetroDECK by, and it is gone by the
    # time the folders are asked: every answer is the handle's live reading.
    seed_es_systems(harness)
    sources = harness.app.services.download_service._retrodeck_folders._sources
    reading = sources.read()
    os.remove(_retrodeck_marker_path(harness))
    sources.read = lambda: reading


def _marker_unreadable(harness) -> None:
    seed_es_systems(harness)
    marker = _retrodeck_marker_path(harness)
    os.remove(marker)
    os.makedirs(marker)


def _marker_invalid(harness) -> None:
    seed_es_systems(harness)
    with open(_retrodeck_marker_path(harness), "w") as f:
        f.write("not json")


def _not_set_up(harness) -> None:
    seed_retrodeck_not_set_up(harness)


_FINDINGS = [
    ("marker-missing", _marker_missing),
    ("marker-unreadable", _marker_unreadable),
    ("marker-invalid", _marker_invalid),
    ("not-set-up", _not_set_up),
]


def _write(path: str, data: bytes = b"x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _assert_refused_for(result: dict[str, Any], code: str) -> None:
    assert result["success"] is False
    assert result["reason"] == "retrodeck_finding"
    assert result["finding"]["code"] == code


def _seed_what_a_press_would_touch(harness) -> list[str]:
    """An installed game, a leftover partial download, a BIOS file and its leftover, and a game and BIOS to download.

    Answers the paths of what lies on disk, which a refused press must leave.
    """
    installed = _write(seed_install(harness, _ROM_ID))
    leftover = _write(os.path.join(harness.roms_root, "gba", "half.gba.tmp"))
    bios = _write(os.path.join(harness.retrodeck_home, "bios", "dc", "dc_boot.bin"))
    bios_leftover = _write(os.path.join(harness.retrodeck_home, "bios", "dc", "dc_flash.bin.tmp"))
    with harness.uow_factory() as uow:
        uow.bios_files.save(
            BiosFile.mark_downloaded(
                platform_slug="dc",
                file_name="dc_boot.bin",
                file_path=bios,
                downloaded_at="2026-01-01T00:00:00",
                firmware_id=1,
            )
        )
    harness.romm.roms[8] = {
        "id": 8,
        "name": "Other",
        "fs_name": "other.gba",
        "fs_size_bytes": 1,
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "platform_name": "GBA",
    }
    harness.romm.firmware_files = [
        {"id": 1, "file_name": "dc_boot.bin", "file_path": "bios/dc/dc_boot.bin", "file_size_bytes": 1, "md5_hash": ""}
    ]
    return [installed, leftover, bios, bios_leftover]


_PRESSES: dict[str, Callable[[Any], Awaitable[dict[str, Any]]]] = {
    "start_download": lambda harness: harness.endpoints.start_download(8),
    "adopt_existing_rom": lambda harness: harness.endpoints.adopt_existing_rom(8, None, None),
    "download_platform_firmware_file": lambda harness: harness.endpoints.download_platform_firmware_file(
        "dc", "dc_boot.bin"
    ),
    "download_all_firmware": lambda harness: harness.endpoints.download_all_firmware("dc", None),
    "remove_rom": lambda harness: harness.endpoints.remove_rom(_ROM_ID),
    "uninstall_all_roms": lambda harness: harness.endpoints.uninstall_all_roms(),
    "delete_platform_bios": lambda harness: harness.endpoints.delete_platform_bios("dc"),
    "start_prune": lambda harness: harness.endpoints.start_prune({"confirmed": True}),
}


@pytest.mark.parametrize(("code", "make"), _FINDINGS)
async def test_under_the_finding_nothing_is_downloaded_deleted_or_cleaned_up_there(harness, code, make):
    # What would be touched lies where the resolver's defaults point, so a
    # refusal here is the rule's, not a missing folder's.
    on_disk = _seed_what_a_press_would_touch(harness)
    make(harness)
    before = sorted(os.walk(harness.retrodeck_home))

    presses = {name: await press(harness) for name, press in _PRESSES.items()}
    harness.app.services.leftover_tmp_cleanup_service.cleanup_leftover_tmp_files()

    for name, result in presses.items():
        assert result["success"] is False, name
        _assert_refused_for(result, code)
    assert sorted(os.walk(harness.retrodeck_home)) == before
    assert all(os.path.exists(path) for path in on_disk)


def _raises(*_args: object) -> None:
    raise RuntimeError("the resolver failed")


_UNANSWERED = "RetroDECK's folders could not be established, so Tender downloads into and removes from none of them."
# RetroDECK's health and its four roots, asked together up front: one that
# raises leaves none of its folders established.
_ROOT_QUESTIONS = ["health", "root", "roms_dir", "bios_dir", "saves_root"]


def _detection_raises(harness) -> None:
    harness.app.services.download_service._retrodeck_folders._sources._detect = _raises


@pytest.mark.parametrize("raising", _ROOT_QUESTIONS)
@pytest.mark.parametrize("press", list(_PRESSES))
async def test_where_a_question_about_retrodeck_s_roots_raises_the_press_is_refused(
    harness, monkeypatch, press, raising
):
    # A healthy RetroDECK whose question fails: nothing establishes its
    # folders, so every press is refused as under a finding.
    on_disk = _seed_what_a_press_would_touch(harness)
    seed_es_systems(harness)
    monkeypatch.setattr(RetroDeck, raising, _raises)
    before = sorted(os.walk(harness.retrodeck_home))

    result = await _PRESSES[press](harness)

    assert result["success"] is False
    assert result["reason"] == "retrodeck_unanswered"
    assert result["message"] == _UNANSWERED
    assert sorted(os.walk(harness.retrodeck_home)) == before
    assert all(os.path.exists(path) for path in on_disk)


@pytest.mark.parametrize("press", list(_PRESSES))
async def test_where_detecting_the_sources_raises_the_press_is_refused_never_as_not_installed(harness, press):
    on_disk = _seed_what_a_press_would_touch(harness)
    seed_es_systems(harness)
    _detection_raises(harness)
    before = sorted(os.walk(harness.retrodeck_home))

    result = await _PRESSES[press](harness)

    assert result["success"] is False
    assert result["reason"] == "retrodeck_unanswered"
    assert result["message"] == _UNANSWERED
    assert sorted(os.walk(harness.retrodeck_home)) == before
    assert all(os.path.exists(path) for path in on_disk)


@pytest.mark.parametrize("raising", [*_ROOT_QUESTIONS, "detection"])
async def test_switched_off_a_download_says_so_where_a_question_raises(harness, monkeypatch, raising):
    _seed_what_a_press_would_touch(harness)
    seed_es_systems(harness)
    harness.settings["emulator_sources_off"] = ["retrodeck"]
    if raising == "detection":
        _detection_raises(harness)
    else:
        monkeypatch.setattr(RetroDeck, raising, _raises)

    game = await harness.endpoints.start_download(8)
    bios = await harness.endpoints.download_platform_firmware_file("dc", "dc_boot.bin")
    removal = await harness.endpoints.remove_rom(_ROM_ID)

    assert game["reason"] == "retrodeck_switched_off"
    assert game["message"] == "Downloads need RetroDECK, which is switched off in Settings › Emulator sources."
    assert bios["reason"] == "retrodeck_switched_off"
    assert removal["message"] == _UNANSWERED


@pytest.mark.parametrize("raising", [*_ROOT_QUESTIONS, "rom_location"])
async def test_where_a_question_raises_check_against_server_says_so(harness, monkeypatch, raising):
    # Its answer is a status rather than a success flag, so it is not one of the presses above.
    _seed_what_a_press_would_touch(harness)
    seed_es_systems(harness)
    monkeypatch.setattr(RetroDeck, raising, _raises)

    result = await harness.endpoints.verify_existing_content(8)

    assert result["status"] == "error"
    assert result["reason"] == "retrodeck_unanswered"
    assert result["message"] == _UNANSWERED


@pytest.mark.parametrize("raising", [*_ROOT_QUESTIONS, "detection"])
async def test_where_a_question_about_retrodeck_s_roots_raises_no_leftover_is_removed(harness, monkeypatch, raising):
    on_disk = _seed_what_a_press_would_touch(harness)
    seed_es_systems(harness)
    if raising == "detection":
        _detection_raises(harness)
    else:
        monkeypatch.setattr(RetroDeck, raising, _raises)

    harness.app.services.leftover_tmp_cleanup_service.cleanup_leftover_tmp_files()

    assert all(os.path.exists(path) for path in on_disk)


async def test_where_a_system_s_folder_question_raises_only_the_presses_that_ask_it_are_refused(harness, monkeypatch):
    # A system's own ROM folder is not one of the roots asked up front: its
    # raise refuses the presses that need that folder — an uninstall among
    # them, since that folder bounds the removal — and nothing else.
    on_disk = _seed_what_a_press_would_touch(harness)
    seed_es_systems(harness)
    monkeypatch.setattr(RetroDeck, "rom_location", _raises)

    asking = [
        await harness.endpoints.start_download(8),
        await harness.endpoints.adopt_existing_rom(8, None, None),
        await harness.endpoints.remove_rom(_ROM_ID),
    ]

    for result in asking:
        assert result["success"] is False
        assert result["reason"] == "retrodeck_unanswered"
        assert result["message"] == _UNANSWERED
    assert os.path.exists(on_disk[0])


_TWO_SYSTEMS_XML = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>gba</name>
    <path>%ROMPATH%/gba</path>
    <command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>
  </system>
  <system>
    <name>snes</name>
    <path>%ROMPATH%/snes</path>
    <command label="Snes9x">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/snes9x_libretro.so %ROM%</command>
  </system>
</systemList>
"""


def _only_gba_raises(monkeypatch) -> None:
    asked = RetroDeck.rom_location

    def rom_location(self, system: str):
        if system == "gba":
            raise RuntimeError("the resolver failed")
        return asked(self, system)

    monkeypatch.setattr(RetroDeck, "rom_location", rom_location)


async def test_where_one_system_s_folder_question_raises_uninstall_all_goes_on_for_every_other_system(
    harness, monkeypatch
):
    seed_es_systems(harness, _TWO_SYSTEMS_XML)
    gba = _write(seed_install(harness, _ROM_ID))
    snes = _write(seed_install(harness, _ROM_ID + 1, system="snes", platform_slug="snes", file_name="game.sfc"))
    _only_gba_raises(monkeypatch)

    result = await harness.endpoints.uninstall_all_roms()

    assert result["success"] is False
    assert result["removed_count"] == 1
    assert result["errors"] == [{"rom_id": str(_ROM_ID), "error": _UNANSWERED}]
    assert os.path.exists(gba)
    assert not os.path.exists(snes)
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(_ROM_ID) is not None
        assert uow.rom_installs.get(_ROM_ID + 1) is None


@pytest.mark.usefixtures("seeded_retrodeck")
async def test_an_uninstall_deletes_inside_the_rom_folder_the_resolver_names(harness):
    installed = _write(seed_install(harness, _ROM_ID))

    result = await harness.endpoints.remove_rom(_ROM_ID)

    assert result["success"] is True
    assert not os.path.exists(installed)


@pytest.mark.usefixtures("seeded_retrodeck")
async def test_an_uninstall_of_a_path_outside_that_rom_folder_is_refused(harness):
    # The record points outside the folder ES-DE's settings name, so nothing
    # bounds the deletion: it is refused and the file stays.
    seed_rom(harness, _ROM_ID, platform_slug="gba")
    outside = _write(os.path.join(str(harness.tmp_path), "elsewhere", "gba", "game.gba"))
    with harness.uow_factory() as uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=_ROM_ID,
                file_path=outside,
                rom_dir=None,
                platform_slug="gba",
                system="gba",
                installed_at="2026-01-01T00:00:00",
            )
        )

    result = await harness.endpoints.remove_rom(_ROM_ID)

    assert result["success"] is False
    assert result["reason"] == "uninstall_failed"
    assert os.path.exists(outside)


async def test_without_retrodeck_an_uninstall_is_refused_and_says_why(harness):
    installed = _write(seed_install(harness, _ROM_ID))

    result = await harness.endpoints.remove_rom(_ROM_ID)

    assert result["success"] is False
    assert result["reason"] == "retrodeck_not_installed"
    assert result["message"] == "Uninstalling needs RetroDECK, which is not installed."
    assert os.path.exists(installed)


async def test_while_retrodeck_s_folder_is_missing_a_download_is_refused_for_that_finding(harness):
    # An SD card that is out: the download refuses with the finding the banner
    # shows, and creates nothing where RetroDECK's folder belongs.
    seed_es_systems(harness)
    shutil.rmtree(harness.retrodeck_home)
    harness.romm.roms[8] = {
        "id": 8,
        "name": "Other",
        "fs_name": "other.gba",
        "fs_size_bytes": 1,
        "platform_slug": "gba",
        "platform_fs_slug": "gba",
        "platform_name": "GBA",
    }

    result = await harness.endpoints.start_download(8)

    _assert_refused_for(result, "root-missing")
    assert not os.path.exists(harness.retrodeck_home)


def _point_retrodeck_at(harness, home: str) -> None:
    """Rewrite RetroDECK's settings as its own move tool leaves them: the home, and ES-DE's ROM folder under it."""
    os.makedirs(os.path.join(home, "roms"), exist_ok=True)
    with open(_retrodeck_marker_path(harness), "w") as f:
        json.dump({"paths": {"rd_home_path": home}}, f)
    settings = os.path.join(
        str(harness.tmp_path), "home", ".var", "app", "net.retrodeck.retrodeck", "config", "ES-DE", "settings"
    )
    with open(os.path.join(settings, "es_settings.xml"), "w") as f:
        f.write(f'<string name="ROMDirectory" value="{os.path.join(home, "roms")}" />\n')


async def test_after_retrodeck_s_home_moves_the_move_code_keeps_its_records_right(harness):
    seed_es_systems(harness)
    old_home = os.path.realpath(os.path.join(str(harness.tmp_path), "A", "retrodeck"))
    new_home = os.path.realpath(os.path.join(str(harness.tmp_path), "B", "retrodeck"))
    _point_retrodeck_at(harness, old_home)
    old_rom = _write(os.path.join(old_home, "roms", "gba", "game.gba"))
    seed_rom(harness, _ROM_ID, platform_slug="gba")
    with harness.uow_factory() as uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=_ROM_ID,
                file_path=old_rom,
                rom_dir=None,
                platform_slug="gba",
                system="gba",
                installed_at="2026-01-01T00:00:00",
            )
        )
    migration = harness.app.services.migration_service
    migration.detect_retrodeck_path_change()

    _point_retrodeck_at(harness, new_home)
    migration.detect_retrodeck_path_change()
    result = await harness.endpoints.migrate_retrodeck_files(None)

    new_rom = os.path.join(new_home, "roms", "gba", "game.gba")
    assert result["success"] is True
    assert result["roms_moved"] == 1
    assert os.path.exists(new_rom)
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(_ROM_ID).file_path == new_rom
        assert uow.kv_config.get("retrodeck_home_path") == new_home


@pytest.mark.parametrize("raising", [*_ROOT_QUESTIONS, "detection"])
async def test_where_a_question_about_retrodeck_s_roots_raises_no_move_is_seen(harness, monkeypatch, raising):
    seed_es_systems(harness)
    recorded = os.path.realpath(os.path.join(str(harness.tmp_path), "A", "retrodeck"))
    with harness.uow_factory() as uow:
        uow.kv_config.set("retrodeck_home_path", recorded)
    if raising == "detection":
        _detection_raises(harness)
    else:
        monkeypatch.setattr(RetroDeck, raising, _raises)

    harness.app.services.migration_service.detect_retrodeck_path_change()

    with harness.uow_factory() as uow:
        assert uow.kv_config.get("retrodeck_home_path") == recorded
        assert uow.kv_config.get("retrodeck_home_path_previous") is None


@pytest.mark.parametrize(("code", "make"), _FINDINGS)
async def test_under_the_finding_no_move_is_seen(harness, code, make):
    # The resolver's home is then its default, which says nothing about where
    # RetroDECK went.
    seed_es_systems(harness)
    recorded = os.path.realpath(os.path.join(str(harness.tmp_path), "A", "retrodeck"))
    with harness.uow_factory() as uow:
        uow.kv_config.set("retrodeck_home_path", recorded)
    os.makedirs(harness.retrodeck_home, exist_ok=True)
    make(harness)

    harness.app.services.migration_service.detect_retrodeck_path_change()

    with harness.uow_factory() as uow:
        assert uow.kv_config.get("retrodeck_home_path") == recorded
        assert uow.kv_config.get("retrodeck_home_path_previous") is None


def _seed_pending_move(harness) -> tuple[str, list[str]]:
    """A move RetroDECK made and nobody migrated yet: a game and a save left under the old home.

    Answers the old home, and the paths of what lies under it, which a refused press must leave.
    """
    old_home = os.path.realpath(os.path.join(str(harness.tmp_path), "A", "retrodeck"))
    old_rom = _write(os.path.join(old_home, "roms", "gba", "game.gba"))
    old_save = _write(os.path.join(old_home, "saves", "gba", "game.srm"))
    seed_rom(harness, _ROM_ID, platform_slug="gba")
    with harness.uow_factory() as uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=_ROM_ID,
                file_path=old_rom,
                rom_dir=None,
                platform_slug="gba",
                system="gba",
                installed_at="2026-01-01T00:00:00",
            )
        )
        uow.kv_config.set("retrodeck_home_path", harness.retrodeck_home)
        uow.kv_config.set("retrodeck_home_path_previous", old_home)
    return old_home, [old_rom, old_save]


def _assert_nothing_moved(harness, old_home: str, left: list[str]) -> None:
    assert all(os.path.exists(path) for path in left)
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(_ROM_ID).file_path == left[0]
        assert uow.kv_config.get("retrodeck_home_path_previous") == old_home


@pytest.mark.parametrize(("code", "make"), _FINDINGS)
async def test_under_the_finding_the_migrate_press_is_refused_before_anything_moves(harness, code, make):
    old_home, left = _seed_pending_move(harness)
    make(harness)

    result = await harness.endpoints.migrate_retrodeck_files(None)

    _assert_refused_for(result, code)
    _assert_nothing_moved(harness, old_home, left)


@pytest.mark.parametrize("raising", [*_ROOT_QUESTIONS, "detection"])
async def test_where_a_question_about_retrodeck_s_roots_raises_the_migrate_press_is_refused(
    harness, monkeypatch, raising
):
    old_home, left = _seed_pending_move(harness)
    seed_es_systems(harness)
    if raising == "detection":
        _detection_raises(harness)
    else:
        monkeypatch.setattr(RetroDeck, raising, _raises)

    result = await harness.endpoints.migrate_retrodeck_files(None)

    assert result["success"] is False
    assert result["reason"] == "retrodeck_unanswered"
    assert result["message"] == _UNANSWERED
    _assert_nothing_moved(harness, old_home, left)


async def test_without_retrodeck_the_migrate_press_is_refused_and_says_why(harness):
    old_home, left = _seed_pending_move(harness)

    result = await harness.endpoints.migrate_retrodeck_files(None)

    assert result["success"] is False
    assert result["reason"] == "retrodeck_not_installed"
    assert result["message"] == "Moving needs RetroDECK, which is not installed."
    _assert_nothing_moved(harness, old_home, left)
