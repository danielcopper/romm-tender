"""Contract tests for a RomM platform's system — the download, the emulator choice and the BIOS answers.

Driven frontend-shaped through the real ``Endpoints`` over the real
``PlatformSystemService`` (the kept ids and their one live read from the fake
RomM); what the platform's ids give in a source is the harness's
``platform_systems`` fake, set per test.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from domain.platform_system import FOUND, NO_SYSTEM, PLATFORM_IDS_KEY, SWITCHED_OFF, PlatformIds, decode_platform_ids
from lib.errors import RommAuthError, RommConnectionError

from ._seed import enable_save_sync, seed_rom

pytestmark = pytest.mark.usefixtures("seeded_retrodeck")

# RomM's own slug for a platform the catalogue calls ``gba``.
_SLUG = "game-boy-advance"


def _seed_server(harness, rom_id: int = 5) -> None:
    harness.romm.platforms = [
        {"id": 1, "slug": _SLUG, "name": "Game Boy Advance", "rom_count": 1, "igdb_id": 24, "tgdb_id": 5}
    ]
    harness.romm.roms[rom_id] = {
        "id": rom_id,
        "name": "Golden Sun",
        "fs_name": "golden-sun.gba",
        "fs_size_bytes": 8,
        "platform_slug": _SLUG,
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
    seed_rom(harness, 5, platform_slug=_SLUG)
    harness.platform_systems.answers[_SLUG] = (FOUND, "gba")

    core = await harness.endpoints.get_system_core_info(_SLUG)
    game_core = await harness.endpoints.get_platform_core_info(5)
    bios = await harness.endpoints.get_platform_firmware_status(_SLUG)
    started = await harness.endpoints.start_download(5)
    await _drain_background_tasks()

    assert core["platform_system"]["system"] == "gba"
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
    [(NO_SYSTEM, None, "no_platform_system"), (SWITCHED_OFF, "gbaoff", "platform_system_off")],
)
async def test_a_platform_with_no_switched_on_system_downloads_nothing_and_says_why(harness, state, system, reason):
    _seed_server(harness)
    seed_rom(harness, 5, platform_slug=_SLUG)
    harness.platform_systems.answers[_SLUG] = (state, system)

    started = await harness.endpoints.start_download(5)
    core = await harness.endpoints.get_system_core_info(_SLUG)
    game_core = await harness.endpoints.get_platform_core_info(5)

    assert started == {
        "success": False,
        "reason": reason,
        "message": started["message"],
        "source": "retrodeck",
        "platform": "Game Boy Advance",
        "system": system,
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
            "system": system,
            "platform": "Game Boy Advance",
        }


async def test_where_no_ids_are_kept_they_are_read_once_from_rom_m_and_kept(harness):
    _seed_server(harness)

    await harness.endpoints.get_system_core_info(_SLUG)
    await harness.endpoints.get_system_core_info(_SLUG)

    assert sum(1 for name, *_ in harness.romm.call_log if name == "list_platforms") == 1
    assert [ids for ids, _slug, _source in harness.platform_systems.asked] == [
        PlatformIds(igdb_id=24, tgdb_id=5, name="Game Boy Advance")
    ] * 2
    with harness.uow_factory() as uow:
        kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
    assert kept == {_SLUG: PlatformIds(igdb_id=24, tgdb_id=5, name="Game Boy Advance")}


async def test_with_rom_m_unreachable_and_no_ids_kept_the_pages_show_the_offline_state(harness):
    _seed_server(harness)
    seed_rom(harness, 5, platform_slug=_SLUG)
    enable_save_sync(harness)
    harness.romm.list_platforms_side_effect = RommConnectionError("connection refused")
    harness.romm.list_saves_side_effect = RommConnectionError("connection refused")

    status = await harness.endpoints.get_save_status(5)
    bios = await harness.endpoints.get_bios_status(5)
    game_core = await harness.endpoints.get_platform_core_info(5)
    core = await harness.endpoints.get_system_core_info(_SLUG)
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
    await harness.endpoints.get_system_core_info(_SLUG)
    harness.romm.list_platforms_side_effect = None

    core = await harness.endpoints.get_system_core_info(_SLUG)

    assert core["platform_system"]["state"] == "found"
    with harness.uow_factory() as uow:
        kept = decode_platform_ids(uow.kv_config.get(PLATFORM_IDS_KEY))
    assert kept == {_SLUG: PlatformIds(igdb_id=24, tgdb_id=5, name="Game Boy Advance")}


async def test_with_rom_m_refusing_the_read_the_pages_name_its_reason(harness):
    _seed_server(harness)
    harness.romm.list_platforms_side_effect = RommAuthError("401")

    core = await harness.endpoints.get_system_core_info(_SLUG)

    assert core["emulator_data_available"] is False
    assert core["emulator_data_reason"] == "auth_failed"
    assert core["emulator_source"] == {"kind": "retrodeck", "starts_games": True}
