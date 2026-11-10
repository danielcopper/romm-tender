"""The Platforms tab's rows are RomM platforms by their folder in RomM (``fs_slug``), #2452 / #2797.

Each read of RomM's platform listing is handed to the refiling (#2452 D6, D7),
and each row is named as its Steam collection is (D2).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from fakes.fake_listing_refiler import FakeListingRefiler
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory

from domain.rom import Rom
from services.library._state import LibrarySyncStateBox
from services.library.fetcher import LibraryFetcher, LibraryFetcherConfig


def _platform(platform_id: int, name: str, slug: str, fs_slug: str | None, rom_count: int = 1) -> dict[str, Any]:
    return {"id": platform_id, "name": name, "slug": slug, "fs_slug": fs_slug, "rom_count": rom_count}


def _fetcher(api: FakeRommApi, refiling: FakeListingRefiler, settings: dict[str, Any] | None = None) -> LibraryFetcher:
    return LibraryFetcher(
        config=LibraryFetcherConfig(
            romm_api=api,
            settings=settings if settings is not None else {"enabled_platforms": {}},
            loop=asyncio.get_running_loop(),
            logger=logging.getLogger("test"),
            settings_persister=FakeSettingsPersister(),
            log_debug=lambda msg: None,
            uow_factory=FakeUnitOfWorkFactory(),
            sync_state_box=LibrarySyncStateBox(),
            emit_progress=MagicMock(),
            local_library_reader=MagicMock(),
            refiling=refiling,
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
