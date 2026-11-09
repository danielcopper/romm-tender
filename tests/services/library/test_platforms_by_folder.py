"""The library sync knows a RomM platform by its folder in RomM (``fs_slug``), #2452 / #2796.

Two RomM platforms can share a slug: a ``system.versions`` entry, a
``system.platforms`` alias or one of RomM's built-in folder aliases each make a
folder a platform of its own, with the slug of the platform it maps to. Each is
a platform of its own in Tender too (#2452 D1): its own sync stamp (D4), its own
Steam collection named by D2, and a game no sync has recorded a folder for is
placed by RomM's answer to its exact id (D5).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from domain.platform_skip import rom_id_checksum
from domain.rom import Rom
from domain.sync_state import SyncState
from lib.errors import RommNotFoundError

_OLD_UPDATED_AT = "2024-06-01T00:00:00+00:00"
_NEW_UPDATED_AT = "2030-01-01T00:00:00+00:00"


def _seed_folder(fake_romm_api, *, platform_id: int, folder: str, roms: list[int]) -> None:
    """Seed one RomM platform kept in *folder*, of the console ``snes``, with *roms*."""
    fake_romm_api.platforms.append(
        {"id": platform_id, "name": "Super Nintendo", "slug": "snes", "fs_slug": folder, "rom_count": len(roms)}
    )
    for rom_id in roms:
        fake_romm_api.roms[rom_id] = {
            "id": rom_id,
            "name": f"G{rom_id}",
            "fs_name": f"g{rom_id}.sfc",
            "platform_id": platform_id,
            "platform_name": "Super Nintendo",
            "platform_slug": "snes",
            "platform_fs_slug": folder,
            "updated_at": _OLD_UPDATED_AT,
        }


def _seed_bound_row(library, rom_id: int, *, folder: str | None, slug: str = "snes") -> None:
    """A bound ``roms`` row whose platform RomM keeps in *folder*; ``None`` is a row no sync gave its folder."""
    with library.uow:
        library.uow.roms.save(
            Rom(
                rom_id=rom_id,
                platform_slug=slug,
                platform_fs_slug=folder,
                name=f"G{rom_id}",
                fs_name=f"g{rom_id}.sfc",
                shortcut_app_id=5000 + rom_id,
                last_synced_at="2025-01-01T00:00:00",
                sibling_group_key=f"romm:seed:{rom_id}",
            )
        )
        library.uow.roms.set_shortcut_applied(rom_id, True)


async def _bind_nothing(_unit, event):
    event.set()
    return {}


async def _run(library, run_id: str) -> None:
    box = library.sync._box
    library.chunk_dispatcher._wait_for_unit_complete = _bind_nothing
    box.sync_state = SyncState.RUNNING
    box.current_sync_id = run_id
    await library.sync._orchestrator._do_sync_per_unit()


def _walked_platforms(fake_romm_api) -> set[int]:
    return {args[0] for name, args, _kwargs in fake_romm_api.call_log if name == "list_roms"}


def _events(emit, name: str, since: int = 0) -> list[dict[str, Any]]:
    return [c.args[1] for c in emit.call_args_list[since:] if c.args and c.args[0] == name]


class TestTwoPlatformsSharingASlugKeepAStampEach:
    """Done when: two listed platforms that share a slug keep a sync stamp each across a sync (#2452 D4)."""

    @pytest.mark.asyncio
    async def test_each_folder_is_stamped_and_skipped_on_its_own(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        _seed_folder(fake_romm_api, platform_id=1, folder="snes", roms=[1, 2])
        _seed_folder(fake_romm_api, platform_id=2, folder="snes-hacks", roms=[3])
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        for rom_id, folder in ((1, "snes"), (2, "snes"), (3, "snes-hacks")):
            _seed_bound_row(library, rom_id, folder=folder)

        await _run(library, "run-1")

        with library.uow as uow:
            snes = uow.platform_sync_state.get("snes")
            hacks = uow.platform_sync_state.get("snes-hacks")
        assert snes is not None and snes.rom_id_checksum == rom_id_checksum([1, 2])
        assert hacks is not None and hacks.rom_id_checksum == rom_id_checksum([3])

        fake_romm_api.call_log.clear()
        await _run(library, "run-2")
        assert _walked_platforms(fake_romm_api) == set(), "neither folder changed, so both are skipped"

        fake_romm_api.roms[3]["updated_at"] = _NEW_UPDATED_AT
        fake_romm_api.call_log.clear()
        await _run(library, "run-3")
        assert _walked_platforms(fake_romm_api) == {2}, "only the folder RomM changed is fetched"


class TestTwoPlatformsSharingASlugGetASteamCollectionEach:
    """Done when: two such platforms get a Steam collection each, named by #2452 D2."""

    @pytest.mark.asyncio
    async def test_the_folder_that_is_not_the_slug_carries_its_folder_in_its_name(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        _seed_folder(fake_romm_api, platform_id=1, folder="snes", roms=[1])
        _seed_folder(fake_romm_api, platform_id=2, folder="snes-hacks", roms=[3])
        library.settings["enabled_platforms"] = {"1": True, "2": True}
        _seed_bound_row(library, 1, folder="snes")
        _seed_bound_row(library, 3, folder="snes-hacks")

        await _run(library, "run-1")

        (collections,) = _events(emit, "sync_collections")
        assert collections["platform_app_ids"] == {
            "Super Nintendo": [5001],
            "Super Nintendo (snes-hacks)": [5003],
        }


class TestAGameWithoutARecordedFolder:
    """Done when: a game without a recorded fs_slug is asked for by its id, gets its fs_slug, and is
    removed or kept as today (#2452 D5)."""

    @staticmethod
    def _seed_switched_off(library, fake_romm_api) -> None:
        # The run syncs snes-hacks; snes is listed and switched off, and row 1 on
        # it was last synced before Tender recorded a platform's folder.
        library.cover_preparer._download_artwork = AsyncMock(return_value={})
        _seed_folder(fake_romm_api, platform_id=1, folder="snes", roms=[1])
        _seed_folder(fake_romm_api, platform_id=2, folder="snes-hacks", roms=[3])
        library.settings["enabled_platforms"] = {"1": False, "2": True}
        _seed_bound_row(library, 1, folder=None)
        _seed_bound_row(library, 3, folder="snes-hacks")

    @pytest.mark.asyncio
    async def test_a_game_romm_still_serves_gets_its_folder_and_goes_with_its_switched_off_platform(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        self._seed_switched_off(library, fake_romm_api)

        await _run(library, "run-1")

        assert ("get_rom_once", (1,)) in [(name, args) for name, args, _kwargs in fake_romm_api.call_log]
        (stale,) = _events(emit, "sync_stale")
        assert stale["remove"] == [{"rom_id": 1, "app_id": 5001}]
        with library.uow as uow:
            assert uow.roms.get(1).platform_fs_slug == "snes"

    @pytest.mark.asyncio
    async def test_a_game_romm_answers_404_for_keeps_its_shortcut_and_is_marked(
        self, make_library, fake_romm_api, emit
    ):
        library = make_library(romm_api=fake_romm_api)
        self._seed_switched_off(library, fake_romm_api)
        fake_romm_api.get_rom_once_side_effect_by_id[1] = RommNotFoundError("Rom with id 1 not found")

        await _run(library, "run-1")

        (stale,) = _events(emit, "sync_stale")
        assert stale["remove"] == []
        with library.uow as uow:
            row = uow.roms.get(1)
        assert row.shortcut_app_id == 5001
        assert row.gone_from_romm == "not_found"
        assert row.platform_fs_slug is None

    @pytest.mark.asyncio
    async def test_the_preview_counts_the_removal_the_apply_makes(self, make_library, fake_romm_api):
        library = make_library(romm_api=fake_romm_api)
        self._seed_switched_off(library, fake_romm_api)

        summary = (await library.sync.sync_preview())["summary"]

        assert summary["remove_count"] == 1
