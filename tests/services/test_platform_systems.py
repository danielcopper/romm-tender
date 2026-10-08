"""Tests for PlatformSystemService — the kept ids, their one live read, and an installed game's system."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_source_platform_systems import FakeSourcePlatformSystems
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory

from domain.platform_system import FOUND, NO_SYSTEM, PLATFORM_IDS_KEY, PlatformIds, decode_platform_ids
from domain.rom_install import RomInstall
from lib.errors import RommApiError
from services.platform_systems import PlatformSystemService, PlatformSystemServiceConfig

_SNES = {"id": 3, "slug": "snes", "name": "Super Nintendo", "igdb_id": 19, "ss_id": 4}
_PSX = {"id": 7, "slug": "psx", "name": "PlayStation", "igdb_id": 7, "tgdb_id": 10}


@pytest.fixture
def uow_factory() -> FakeUnitOfWorkFactory:
    return FakeUnitOfWorkFactory()


@pytest.fixture
def romm() -> FakeRommApi:
    api = FakeRommApi()
    api.platforms = [_SNES, _PSX]
    return api


@pytest.fixture
def sources() -> FakeSourcePlatformSystems:
    return FakeSourcePlatformSystems()


@pytest.fixture
def service(uow_factory, romm, sources) -> PlatformSystemService:
    return PlatformSystemService(
        config=PlatformSystemServiceConfig(
            uow_factory=uow_factory, romm_api=romm, source_platform_systems=sources, log_debug=MagicMock()
        )
    )


def _keep(uow_factory: FakeUnitOfWorkFactory, value: str) -> None:
    uow_factory.uow.kv_config.set(PLATFORM_IDS_KEY, value)


def _listing_reads(romm: FakeRommApi) -> int:
    return sum(1 for name, _args, _kwargs in romm.call_log if name == "list_platforms")


class TestTheKeptIds:
    def test_the_kept_ids_are_what_the_source_is_asked_with(self, service, uow_factory, romm, sources):
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 19, "libretro_slug": None, "ss_id": None, "tgdb_id": 6}}))

        service.platform_system("snes")

        assert sources.asked == [(PlatformIds(igdb_id=19, tgdb_id=6), "snes", None)]
        assert _listing_reads(romm) == 0

    def test_the_named_source_is_passed_on(self, service, uow_factory, sources):
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 19}}))

        service.platform_system("snes", source="retrodeck")

        assert sources.asked[0][2] == "retrodeck"

    def test_the_platform_is_named_by_its_kept_display_name(self, service, uow_factory):
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 19}}))
        uow_factory.uow.kv_config.set("platform_names", json.dumps({"snes": "Super Nintendo"}))

        assert service.platform_system("snes").platform_name == "Super Nintendo"

    def test_a_platform_no_sync_named_is_named_by_the_name_kept_with_its_ids(self, service, uow_factory):
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 19, "name": "Super Famicom"}}))

        assert service.platform_system("snes").platform_name == "Super Famicom"

    def test_without_a_kept_name_the_slug_names_it(self, service, uow_factory):
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 19}}))

        assert service.platform_system("snes").platform_name == "snes"


class TestWhereNoneAreKept:
    def test_they_are_read_once_from_rom_m_and_kept(self, service, uow_factory, romm, sources):
        service.platform_system("snes")
        service.platform_system("psx")

        assert _listing_reads(romm) == 1
        assert [ids for ids, _slug, _source in sources.asked] == [
            PlatformIds(igdb_id=19, ss_id=4, name="Super Nintendo"),
            PlatformIds(igdb_id=7, tgdb_id=10, name="PlayStation"),
        ]
        assert decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY)) == {
            "snes": PlatformIds(igdb_id=19, ss_id=4, name="Super Nintendo"),
            "psx": PlatformIds(igdb_id=7, tgdb_id=10, name="PlayStation"),
        }

    def test_a_platform_kept_before_is_kept_beside_the_listing(self, service, uow_factory, romm):
        _keep(uow_factory, json.dumps({"n64": {"igdb_id": 4}}))

        service.platform_system("snes")

        kept = decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY))
        assert kept is not None
        assert kept["n64"] == PlatformIds(igdb_id=4)
        assert kept["snes"] == PlatformIds(igdb_id=19, ss_id=4, name="Super Nintendo")

    def test_the_listings_name_names_a_platform_with_no_kept_name(self, service):
        assert service.platform_system("psx").platform_name == "PlayStation"

    def test_a_platform_rom_m_does_not_list_is_kept_with_no_ids(self, service, uow_factory, romm, sources):
        service.platform_system("vic-20")
        service.platform_system("vic-20")

        assert _listing_reads(romm) == 1
        assert sources.asked[-1][0] == PlatformIds()

    def test_a_failed_read_raises_and_keeps_nothing(self, service, uow_factory, romm):
        romm.list_platforms_side_effect = RommApiError("server unreachable")

        with pytest.raises(RommApiError):
            service.platform_system("snes")

        assert uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY) is None

    def test_a_caller_that_must_not_reach_rom_m_gets_no_ids(self, service, uow_factory, romm, sources):
        service.platform_system("snes", ask_romm=False)

        assert _listing_reads(romm) == 0
        assert sources.asked == [(PlatformIds(), "snes", None)]
        assert uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY) is None


class TestAnInstalledGame:
    def test_it_keeps_the_system_its_install_record_holds(self, service, sources, romm):
        install = RomInstall(
            rom_id=1,
            file_path="/roms/snes/a.sfc",
            rom_dir=None,
            platform_slug="snes",
            system="sfc",
            installed_at="2026-01-01T00:00:00+00:00",
        )

        platform = service.rom_system("snes", install)

        assert (platform.state, platform.system) == (FOUND, "sfc")
        assert sources.asked == []
        assert _listing_reads(romm) == 0

    def test_a_game_not_installed_follows_the_platforms_answer(self, service, uow_factory, sources):
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 19}}))
        sources.answers["snes"] = (NO_SYSTEM, None)

        assert service.rom_system("snes", None).state == NO_SYSTEM
        assert len(sources.asked) == 1
