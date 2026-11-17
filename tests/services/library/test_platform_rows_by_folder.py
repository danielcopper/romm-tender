"""The Platforms tab's rows are RomM platforms by their folder in RomM (``fs_slug``), #2452 / #2797.

Each read of RomM's platform listing is handed to the refiling (#2452 D6, D7),
and each row is named as its Steam collection is (D2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from fakes.fake_listing_refiler import FakeListingRefiler
from fakes.fake_listing_replacer import FakeListingReplacer
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory

from domain.platform_names import listed_platform_names
from domain.rom import Rom
from services.library._state import LibrarySyncStateBox
from services.library.fetcher import LibraryFetcher, LibraryFetcherConfig
from services.platform_switches import PlatformSwitches, PlatformSwitchesConfig


def _platform(platform_id: int, name: str, slug: str, fs_slug: str | None, rom_count: int = 1) -> dict[str, Any]:
    return {"id": platform_id, "name": name, "slug": slug, "fs_slug": fs_slug, "rom_count": rom_count}


def _fetcher(api: FakeRommApi, refiling: FakeListingRefiler, settings: dict[str, Any] | None = None) -> LibraryFetcher:
    settings = settings if settings is not None else {"enabled_platforms": {}}
    persister = FakeSettingsPersister()
    return LibraryFetcher(
        config=LibraryFetcherConfig(
            romm_api=api,
            settings=settings,
            loop=asyncio.get_running_loop(),
            logger=logging.getLogger("test"),
            settings_persister=persister,
            log_debug=lambda msg: None,
            uow_factory=FakeUnitOfWorkFactory(),
            sync_state_box=LibrarySyncStateBox(),
            emit_progress=MagicMock(),
            local_library_reader=MagicMock(),
            refiling=refiling,
            platform_listing=FakeListingReplacer(),
            platform_switches=PlatformSwitches(
                config=PlatformSwitchesConfig(settings=settings, settings_persister=persister, lock=threading.Lock())
            ),
        )
    )


def _api(*platforms: dict[str, Any]) -> FakeRommApi:
    api = FakeRommApi()
    api.platforms.extend(platforms)
    return api


class TestTwoPlatformsSharingASlugAreTwoRows:
    """Done when: two listed platforms that share a slug are two rows of the Platforms tab, named by D2."""

    @pytest.mark.asyncio
    async def test_each_folder_is_a_row_named_as_its_collection_is(self):
        api = _api(
            _platform(1, "Super Nintendo", "snes", "snes"),
            _platform(2, "Super Nintendo", "snes", "snes-hacks"),
        )

        rows = (await _fetcher(api, FakeListingRefiler()).get_platforms())["platforms"]

        assert [(row["name"], row["slug"], row["fs_slug"]) for row in rows] == [
            ("Super Nintendo", "snes", "snes"),
            ("Super Nintendo (snes-hacks)", "snes", "snes-hacks"),
        ]

    @pytest.mark.asyncio
    async def test_a_platform_without_a_folder_is_no_row(self):
        api = _api(_platform(1, "Super Nintendo", "snes", "snes"), _platform(2, "Mystery", "mystery", None))

        rows = (await _fetcher(api, FakeListingRefiler()).get_platforms())["platforms"]

        assert [row["fs_slug"] for row in rows] == ["snes"]


class TestARowOfAnEntryWithAnUnusableField:
    """#2761 D3, D5: an entry read through the one reader is still a row the tab can switch on and off."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", [None, "  "], ids=["null", "blank"])
    async def test_a_platform_without_a_usable_name_is_named_by_its_fs_slug(self, name):
        api = _api({"id": 12, "name": name, "display_name": "GameCube", "slug": "ngc", "fs_slug": "gc", "rom_count": 4})

        rows = (await _fetcher(api, FakeListingRefiler()).get_platforms())["platforms"]

        assert [(row["name"], row["fs_slug"]) for row in rows] == [("gc", "gc")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rom_count", [None, "many", 4.5], ids=["null", "text", "fraction"])
    async def test_a_rom_count_that_is_not_a_number_is_shown_without_a_count(self, rom_count):
        api = _api({**_platform(1, "Super Nintendo", "snes", "snes"), "rom_count": rom_count})

        rows = (await _fetcher(api, FakeListingRefiler()).get_platforms())["platforms"]

        assert [(row["fs_slug"], row["rom_count"]) for row in rows] == [("snes", None)]

    @pytest.mark.asyncio
    async def test_a_platform_without_a_rom_count_is_shown_without_a_count(self):
        api = _api({"id": 1, "name": "Super Nintendo", "slug": "snes", "fs_slug": "snes"})

        rows = (await _fetcher(api, FakeListingRefiler()).get_platforms())["platforms"]

        assert [(row["fs_slug"], row["rom_count"]) for row in rows] == [("snes", None)]

    @pytest.mark.asyncio
    async def test_a_platform_with_no_games_is_no_row(self):
        api = _api(_platform(1, "Super Nintendo", "snes", "snes", rom_count=0), _platform(2, "GBA", "gba", "gba"))

        rows = (await _fetcher(api, FakeListingRefiler()).get_platforms())["platforms"]

        assert [row["fs_slug"] for row in rows] == ["gba"]

    @pytest.mark.asyncio
    async def test_the_row_carries_the_fields_as_read(self):
        api = _api({"id": "12", "name": "Nintendo GameCube", "slug": "  ", "fs_slug": "gc", "rom_count": "40"})

        rows = (await _fetcher(api, FakeListingRefiler()).get_platforms())["platforms"]

        assert rows == [
            {
                "id": 12,
                "name": "Nintendo GameCube",
                "slug": "",
                "fs_slug": "gc",
                "rom_count": 40,
                "sync_enabled": False,
            }
        ]


class TestEveryListingReadIsRefiled:
    @pytest.mark.asyncio
    async def test_the_tab_s_read_hands_the_listing_over(self):
        api = _api(_platform(1, "Super Nintendo", "snes", "snes"))
        refiling = FakeListingRefiler()

        await _fetcher(api, refiling).get_platforms()

        assert refiling.listings == [api.platforms]

    @pytest.mark.asyncio
    async def test_enable_all_hands_the_listing_over(self):
        api = _api(_platform(1, "Super Nintendo", "snes", "snes"))
        refiling = FakeListingRefiler()

        await _fetcher(api, refiling).set_all_platforms_sync(True)

        assert refiling.listings == [api.platforms]

    @pytest.mark.asyncio
    async def test_a_sync_s_read_hands_the_listing_over(self):
        api = _api(_platform(1, "Super Nintendo", "snes", "snes"))
        refiling = FakeListingRefiler()

        await _fetcher(api, refiling)._fetch_enabled_platforms()

        assert refiling.listings == [api.platforms]


def _seed_bound(uow, rom_id: int, folder: str | None, *, slug: str = "snes") -> None:
    """A game bound to a Steam shortcut on the ``fs_slug`` *folder*; ``None`` is one no sync recorded a folder for."""
    with uow:
        uow.roms.save(
            Rom(
                rom_id=rom_id,
                platform_slug=slug,
                platform_fs_slug=folder,
                name=f"G{rom_id}",
                fs_name=f"g{rom_id}.sfc",
                shortcut_app_id=1000 + rom_id,
                last_synced_at="2025-01-01T00:00:00",
            )
        )


def _seed_names(uow, names: dict[str, str]) -> None:
    with uow:
        uow.kv_config.set("platform_names", json.dumps(names))


class TestTheShortcutCountsArePerFolder:
    """The counts the Platforms tab shows beside each row, one entry per ``fs_slug`` (#2452 D1, D17)."""

    @pytest.mark.asyncio
    async def test_two_folders_sharing_a_slug_are_two_entries_each_with_its_own_count(self, library):
        for rom_id, folder in ((10, "snes"), (20, "snes-hacks"), (21, "snes-hacks")):
            _seed_bound(library.uow, rom_id, folder)
        _seed_names(library.uow, {"snes": "Super Nintendo", "snes-hacks": "Super Nintendo (snes-hacks)"})

        platforms = library.sync.get_registry_platforms()["platforms"]

        assert [(p["name"], p["slug"], p["fs_slug"], p["count"]) for p in platforms] == [
            ("Super Nintendo", "snes", "snes", 1),
            ("Super Nintendo (snes-hacks)", "snes", "snes-hacks", 2),
        ]

    @pytest.mark.asyncio
    async def test_a_bound_game_without_a_folder_counts_on_no_row(self, library):
        """D17: a bound game with no fs_slug belongs to no row of the Platforms tab."""
        _seed_bound(library.uow, 10, "snes")
        _seed_bound(library.uow, 30, None)

        platforms = library.sync.get_registry_platforms()["platforms"]

        assert [(p["fs_slug"], p["count"]) for p in platforms] == [("snes", 1)]


class TestRemovingOnePlatformsShortcuts:
    """Done when: removing one platform's shortcuts leaves the other platform of its slug (#2452 D11, D17)."""

    @pytest.mark.asyncio
    async def test_removing_one_folder_leaves_the_other_of_its_slug(self, library):
        _seed_bound(library.uow, 10, "snes")
        _seed_bound(library.uow, 20, "snes-hacks")
        _seed_names(library.uow, {"snes": "Super Nintendo", "snes-hacks": "Super Nintendo (snes-hacks)"})

        result = await library.shortcut_removal.remove_platform_shortcuts("snes-hacks")

        assert (result["app_ids"], result["rom_ids"]) == ([1020], ["20"])
        assert result["platform_name"] == "Super Nintendo (snes-hacks)"

    @pytest.mark.asyncio
    async def test_a_platform_listed_without_a_name_is_named_by_its_folder_in_the_answer(self, library):
        """#2761 D3 (changed): the kept names, which the removal reads, name it as its Steam collection is."""
        _seed_bound(library.uow, 10, "gc", slug="ngc")
        _seed_names(library.uow, listed_platform_names([{"id": 12, "slug": "ngc", "fs_slug": "gc", "name": None}]))

        result = await library.shortcut_removal.remove_platform_shortcuts("gc")

        assert (result["app_ids"], result["platform_name"]) == ([1010], "gc")

    @pytest.mark.asyncio
    async def test_one_platform_s_removal_leaves_a_game_without_a_folder(self, library):
        _seed_bound(library.uow, 10, "snes")
        _seed_bound(library.uow, 30, None)

        result = await library.shortcut_removal.remove_platform_shortcuts("snes")

        assert result["app_ids"] == [1010]

    @pytest.mark.asyncio
    async def test_removing_all_still_removes_a_game_without_a_folder(self, library):
        _seed_bound(library.uow, 10, "snes")
        _seed_bound(library.uow, 30, None)

        result = await library.shortcut_removal.remove_all_shortcuts()

        assert sorted(result["app_ids"]) == [1010, 1030]
