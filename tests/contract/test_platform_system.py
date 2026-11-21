"""Contract tests for a RomM platform's system — the download, the emulator choice and the BIOS answers.

Driven frontend-shaped through the real ``Endpoints`` over the real
``PlatformSystemService`` (the kept platforms and their one live read from the
fake RomM); what the platform's slug and folder give in a source is the
harness's ``platform_systems`` fake, set per test.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from domain.platform_system import (
    CONTRADICTED,
    FOUND,
    NO_SYSTEM,
    NOT_HERE,
    PLATFORM_IDS_KEY,
    SEVERAL,
    SWITCHED_OFF,
    UNMATCHED,
    KeptPlatform,
    PlatformKeys,
    SystemPair,
    decode_platform_ids,
)
from lib.errors import RommAuthError, RommConnectionError

from ._seed import enable_save_sync, seed_es_systems, seed_install, seed_rom

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

# RomM's own slug for a platform the catalogue calls ``gba``.
_SLUG = "game-boy-advance"
# The folder RomM keeps it in, which the pages and a game's row name it by.
_FOLDER = "gba"
_KEPT_GBA = KeptPlatform(
    slug=_SLUG,
    fs_slug="gba",
    platform_id=1,
    name="Game Boy Advance",
    ids={"fs_slug": "gba", "igdb_id": 24, "tgdb_id": 5},
)


def _seed_server(harness, rom_id: int = 5) -> None:
    harness.romm.platforms = [
        {
            "id": 1,
            "slug": _SLUG,
            "fs_slug": "gba",
            "name": "Game Boy Advance",
            "rom_count": 1,
            "igdb_id": 24,
            "tgdb_id": 5,
        }
    ]
    harness.romm.roms[rom_id] = {
        "id": rom_id,
        "name": "Golden Sun",
        "fs_name": "golden-sun.gba",
        "fs_size_bytes": 8,
        "platform_slug": _SLUG,
        "platform_fs_slug": _FOLDER,
        "platform_name": "Game Boy Advance",
    }
    harness.romm.download_payloads[f"rom:{rom_id}:golden-sun.gba"] = b"GBAROM!!"


async def _drain_background_tasks() -> None:
    for _ in range(6):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


async def test_the_download_the_emulator_choice_and_the_bios_answer_ask_one_system(harness):
    _seed_server(harness)
    seed_rom(harness, 5, platform_slug=_SLUG, platform_folder=_FOLDER)
    harness.platform_systems.answers[_SLUG] = (FOUND, "gba")

    core = await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)
    game_core = await harness.endpoints.get_platform_core_info(5)
    bios = await harness.endpoints.get_platform_firmware_status(_SLUG, _FOLDER)
    started = await harness.endpoints.start_download(5)
    await _drain_background_tasks()

    assert core["platform_system"]["systems"] == ["gba"]
    assert [entry["label"] for entry in core["emulators"]] == ["mGBA", "VBA Next"]
    assert [entry["label"] for entry in game_core["emulators"]] == ["mGBA", "VBA Next"]
    assert bios["success"] is True
    assert started["success"] is True
    assert os.path.isfile(os.path.join(harness.roms_root, "gba", "golden-sun.gba"))
    with harness.uow_factory() as uow:
        install = uow.rom_installs.get(5)
    assert install is not None
    assert install.system == "gba"


@pytest.mark.parametrize(
    ("state", "system", "reason"),
    [
        (NO_SYSTEM, None, "no_platform_system"),
        (SWITCHED_OFF, "gbaoff", "platform_system_off"),
        (UNMATCHED, None, "platform_unmatched"),
        (NOT_HERE, "gbanothere", "platform_system_not_here"),
        (CONTRADICTED, None, "platform_pairs_contradict"),
    ],
)
async def test_a_platform_with_no_switched_on_system_downloads_nothing_and_says_why(harness, state, system, reason):
    _seed_server(harness)
    seed_rom(harness, 5, platform_slug=_SLUG, platform_folder=_FOLDER)
    harness.platform_systems.answers[_SLUG] = (state, system)

    started = await harness.endpoints.start_download(5)
    core = await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)
    game_core = await harness.endpoints.get_platform_core_info(5)

    assert started == {
        "success": False,
        "reason": reason,
        "message": started["message"],
        "source": "retrodeck",
        "platform": "Game Boy Advance",
        "systems": [] if system is None else [system],
        "slug": _SLUG,
        "folder": "gba",
    }
    assert not os.path.exists(os.path.join(harness.roms_root, _SLUG))
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(5) is None
    for answer in (core, game_core):
        assert answer["emulator_data_available"] is False
        assert answer["emulator_data_reason"] == reason
        assert answer["platform_system"] == {
            "state": state,
            "source": "retrodeck",
            "systems": [] if system is None else [system],
            "slug": _SLUG,
            "folder": "gba",
            "platform": "Game Boy Advance",
        }


async def test_a_platform_of_several_systems_downloads_nothing_and_names_them(harness):
    """#2225 D8: a download lands in one system's folder, so several systems refuse it."""
    _seed_server(harness)
    seed_rom(harness, 5, platform_slug=_SLUG, platform_folder=_FOLDER)
    several = (SystemPair("esde", "gba"), SystemPair("esde", "gbah"))
    harness.platform_systems.answers[_SLUG] = (SEVERAL, several)

    started = await harness.endpoints.start_download(5)

    assert started == {
        "success": False,
        "reason": "platform_systems_several",
        "message": started["message"],
        "source": "retrodeck",
        "platform": "Game Boy Advance",
        "systems": ["gba", "gbah"],
        "slug": _SLUG,
        "folder": "gba",
    }
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(5) is None


async def test_where_no_ids_are_kept_they_are_read_once_from_rom_m_and_kept(harness):
    _seed_server(harness)

    await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)
    await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)

    assert sum(1 for name, *_ in harness.romm.call_log if name == "list_platforms") == 1
    assert [keys for keys, _source in harness.platform_systems.asked] == [PlatformKeys(_SLUG, "gba")] * 2
    with harness.uow_factory() as uow:
        kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
    assert kept == {"gba": _KEPT_GBA}


async def test_with_rom_m_unreachable_and_no_ids_kept_the_pages_show_the_offline_state(harness):
    _seed_server(harness)
    seed_rom(harness, 5, platform_slug=_SLUG, platform_folder=_FOLDER)
    enable_save_sync(harness)
    harness.romm.list_platforms_side_effect = RommConnectionError("connection refused")
    harness.romm.list_saves_side_effect = RommConnectionError("connection refused")

    status = await harness.endpoints.get_save_status(5)
    bios = await harness.endpoints.get_bios_status(5)
    game_core = await harness.endpoints.get_platform_core_info(5)
    core = await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)
    started = await harness.endpoints.start_download(5)

    assert status["server_query_failed"] is True
    assert status["server_query_reason"] == "server_unreachable"
    assert status["save_resolution"]["state"] == "unestablished"
    assert status["save_resolution"]["unestablished"] == "not_asked"
    assert bios["bios_level"] == "unknown"
    assert bios["bios_status_unknown"] is True
    for answer in (game_core, core):
        assert answer["emulator_data_available"] is False
        assert answer["emulator_data_reason"] == "server_unreachable"
        assert answer["emulator_source"] == {"kind": "retrodeck", "starts_games": True}
        assert answer["platform_system"] is None
    assert started["success"] is False
    assert started["reason"] == "server_unreachable"
    assert harness.platform_systems.asked == []
    with harness.uow_factory() as uow:
        assert uow.kv_config.get(PLATFORM_IDS_KEY) is None


async def test_the_next_read_with_rom_m_reachable_keeps_the_ids(harness):
    _seed_server(harness)
    harness.romm.list_platforms_side_effect = RommConnectionError("connection refused")
    await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)
    harness.romm.list_platforms_side_effect = None

    core = await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)

    assert core["platform_system"]["state"] == "found"
    with harness.uow_factory() as uow:
        kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
    assert kept == {"gba": _KEPT_GBA}


async def test_with_rom_m_refusing_the_read_the_pages_name_its_reason(harness):
    _seed_server(harness)
    harness.romm.list_platforms_side_effect = RommAuthError("401")

    core = await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)

    assert core["emulator_data_available"] is False
    assert core["emulator_data_reason"] == "auth_failed"
    assert core["emulator_source"] == {"kind": "retrodeck", "starts_games": True}


async def test_two_folders_of_one_slug_each_answer_their_own_system(harness):
    """#2217 D16 (changed 2026-11-18): a game is asked with its own folder, a page with the one it is about."""
    harness.romm.platforms = [
        {"id": 1, "slug": "genesis", "fs_slug": "megadrive", "name": "Mega Drive", "rom_count": 1},
        {"id": 2, "slug": "genesis", "fs_slug": "megadrivejp", "name": "Mega Drive (Japan)", "rom_count": 1},
    ]
    harness.platform_systems.answers["megadrive"] = (FOUND, "genesis")
    harness.platform_systems.answers["megadrivejp"] = (FOUND, "megadrivejp")
    seed_rom(harness, 5, platform_slug="genesis", platform_folder="megadrivejp")

    page = await harness.endpoints.get_system_core_info("genesis", "megadrive")
    game = await harness.endpoints.get_platform_core_info(5)

    assert page["platform_system"]["systems"] == ["genesis"]
    assert game["platform_system"]["systems"] == ["megadrivejp"]
    assert {keys for keys, _source in harness.platform_systems.asked} == {
        PlatformKeys("genesis", "megadrive"),
        PlatformKeys("genesis", "megadrivejp"),
    }


async def test_a_game_installed_under_a_system_the_source_no_longer_lists_names_it(harness):
    """No emulator list for a recorded system the source does not list, said as such, never a failed read."""
    seed_install(harness, 5, system="gbretired", platform_slug="gb")

    game_core = await harness.endpoints.get_platform_core_info(5)

    assert game_core["emulator_data_available"] is False
    assert game_core["emulator_data_reason"] == "system_unknown"
    assert game_core["emulator_data_system"] == {"system_keys": ["gbretired"], "installed": True}


async def test_a_platform_page_whose_system_the_source_does_not_list_names_it_as_no_game_s(harness):
    """The platform page's system is the source's answer, never an install record's."""
    _seed_server(harness)
    harness.platform_systems.answers[_SLUG] = (FOUND, SystemPair("esde", "gbaretired"))

    core = await harness.endpoints.get_system_core_info(_SLUG, _FOLDER)

    assert core["emulator_data_reason"] == "system_unknown"
    assert core["emulator_data_system"] == {"system_keys": ["gbaretired"], "installed": False}


async def test_a_platform_s_bios_entry_names_the_system_its_source_does_not_list_as_no_game_s(harness):
    _seed_server(harness)
    harness.platform_systems.answers[_SLUG] = (FOUND, SystemPair("esde", "gbaretired"))

    bios = await harness.endpoints.get_platform_firmware_status(_SLUG, _FOLDER)

    assert bios["platform"]["emulator_data_reason"] == "system_unknown"
    assert bios["platform"]["emulator_data_system"] == {"system_keys": ["gbaretired"], "installed": False}


async def test_a_download_for_a_broken_systems_file_is_refused_for_that_catalogue(harness):
    """The real resolver reads the broken file; the download says the catalogue was refused, not the folders."""
    seed_es_systems(harness, "<systemList><system>broken")
    harness.platform_systems.real = harness.real_platform_systems
    _seed_server(harness)
    seed_rom(harness, 5, platform_slug=_SLUG, platform_folder=_FOLDER)

    started = await harness.endpoints.start_download(5)

    assert started["success"] is False
    assert (started["reason"], started["unasked"], started["source"]) == (
        "platform_system_unasked",
        "catalogue_invalid",
        "retrodeck",
    )
    with harness.uow_factory() as uow:
        assert uow.rom_installs.get(5) is None
