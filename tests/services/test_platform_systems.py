"""Tests for PlatformSystemService — the kept ids, their one live read, and an installed game's system."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest
from fakes.fake_listing_refiler import FakeListingRefiler
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_source_platform_systems import RETRODECK_SOURCE, FakeSourcePlatformSystems
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory

from domain.platform_system import (
    FOUND,
    NO_SYSTEM,
    PLATFORM_IDS_KEY,
    UNASKED,
    KeptPlatform,
    PlatformIds,
    decode_platform_ids,
    encode_platform_ids,
)
from domain.rom_install import RomInstall
from lib.errors import RommAuthError, RommConnectionError, classify_error
from services.platform_systems import PlatformSystemService, PlatformSystemServiceConfig

_SNES = {"id": 3, "slug": "snes", "fs_slug": "snes", "name": "Super Nintendo", "igdb_id": 19, "ss_id": 4}
_PSX = {"id": 7, "slug": "psx", "fs_slug": "psx", "name": "PlayStation", "igdb_id": 7, "tgdb_id": 10}
_KEPT_SNES = KeptPlatform(
    slug="snes",
    fs_slug="snes",
    platform_id=3,
    name="Super Nintendo",
    ids={"fs_slug": "snes", "igdb_id": 19, "ss_id": 4},
)
_KEPT_PSX = KeptPlatform(
    slug="psx", fs_slug="psx", platform_id=7, name="PlayStation", ids={"fs_slug": "psx", "igdb_id": 7, "tgdb_id": 10}
)


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
def refiling() -> FakeListingRefiler:
    return FakeListingRefiler()


@pytest.fixture
def service(uow_factory, romm, sources, refiling) -> PlatformSystemService:
    return PlatformSystemService(
        config=PlatformSystemServiceConfig(
            uow_factory=uow_factory,
            romm_api=romm,
            refiling=refiling,
            source_platform_systems=sources,
            logger=logging.getLogger("test_platform_systems"),
            log_debug=MagicMock(),
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

    def test_the_name_is_the_one_kept_for_the_kept_platform_s_fs_slug(self, service, uow_factory):
        """The names cache is keyed by ``fs_slug``; the platform is asked by its slug (#2452 D10)."""
        kept = KeptPlatform(slug="ngc", fs_slug="gamecube", name="Nintendo GameCube", ids={"igdb_id": 21})
        _keep(uow_factory, encode_platform_ids({"gamecube": kept}))
        names = {"gamecube": "Nintendo GameCube (gamecube)", "ngc": "Another platform whose fs_slug is ngc"}
        uow_factory.uow.kv_config.set("platform_names", json.dumps(names))

        assert service.platform_system("ngc").platform_name == "Nintendo GameCube (gamecube)"

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
            "snes": _KEPT_SNES,
            "psx": _KEPT_PSX,
        }

    def test_a_platform_kept_before_is_kept_beside_the_listing(self, service, uow_factory, romm):
        _keep(uow_factory, json.dumps({"n64": {"igdb_id": 4}}))

        service.platform_system("snes")

        kept = decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY))
        assert kept is not None
        assert kept["n64"] == KeptPlatform(slug="n64", ids={"igdb_id": 4})
        assert kept["snes"] == _KEPT_SNES

    def test_the_listings_name_names_a_platform_with_no_kept_name(self, service):
        assert service.platform_system("psx").platform_name == "PlayStation"

    def test_a_platform_rom_m_does_not_list_is_kept_with_no_ids(self, service, uow_factory, romm, sources):
        service.platform_system("vic-20")
        service.platform_system("vic-20")

        assert _listing_reads(romm) == 1
        assert sources.asked[-1][0] == PlatformIds()

    def test_a_read_that_cannot_reach_rom_m_answers_unreachable_and_keeps_nothing(
        self, service, uow_factory, romm, sources
    ):
        romm.list_platforms_side_effect = RommConnectionError("connection refused")

        platform = service.platform_system("snes", source="retrodeck")

        assert (platform.state, platform.unasked) == (UNASKED, "server_unreachable")
        assert platform.unasked_message == classify_error(RommConnectionError("x"))[1]
        assert platform.source == RETRODECK_SOURCE
        assert sources.asked == []
        assert uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY) is None

    def test_a_refused_read_answers_its_own_reason(self, service, romm):
        romm.list_platforms_side_effect = RommAuthError("401")

        assert service.platform_system("snes").unasked == "auth_failed"

    @pytest.mark.parametrize(
        ("error", "level"),
        [(RommConnectionError("connection refused"), logging.INFO), (RommAuthError("401"), logging.WARNING)],
    )
    def test_a_failed_read_is_logged_once_at_the_classifier_s_level(self, service, romm, caplog, error, level):
        romm.list_platforms_side_effect = error
        caplog.set_level(logging.DEBUG, logger="test_platform_systems")

        service.platform_system("snes")

        lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "test_platform_systems"]
        assert lines == [
            (level, f"[platforms] no ids kept for snes, and RomM's listing failed: {type(error).__name__}: {error}")
        ]

    def test_the_next_read_with_rom_m_reachable_keeps_the_ids(self, service, uow_factory, romm):
        romm.list_platforms_side_effect = RommConnectionError("connection refused")
        service.platform_system("snes")
        romm.list_platforms_side_effect = None

        assert service.platform_system("snes").state == FOUND
        assert decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY)) is not None

    def test_a_caller_that_must_not_reach_rom_m_gets_no_ids(self, service, uow_factory, romm, sources):
        service.platform_system("snes", ask_romm=False)

        assert _listing_reads(romm) == 0
        assert sources.asked == [(PlatformIds(), "snes", None)]
        assert uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY) is None


class TestTheRecordKeyedByFolder:
    def test_a_record_kept_by_folder_asks_what_the_same_platform_kept_by_slug_asked(
        self, service, uow_factory, sources
    ):
        before = {"igdb_id": 21, "libretro_slug": "Nintendo - GameCube", "ss_id": 13, "tgdb_id": 2, "name": "GameCube"}
        _keep(uow_factory, json.dumps({"ngc": before}))
        service.platform_system("ngc")
        listed = {"id": 12, "slug": "ngc", "fs_slug": "gc", **before, "moby_id": 14, "ra_id": 16, "igdb_slug": "ngc"}
        _keep(
            uow_factory,
            encode_platform_ids({"gc": KeptPlatform(slug="ngc", fs_slug="gc", name="GameCube", ids=listed)}),
        )

        service.platform_system("ngc")

        assert sources.asked[0] == sources.asked[1]
        assert sources.asked[1][0] == PlatformIds(
            igdb_id=21, libretro_slug="Nintendo - GameCube", ss_id=13, tgdb_id=2, name="GameCube"
        )

    def test_of_two_listed_folders_that_share_a_slug_the_last_listed_answers_it(
        self, service, uow_factory, romm, sources
    ):
        romm.platforms = [
            {"id": 12, "slug": "ngc", "fs_slug": "gc", "igdb_id": 21},
            {"id": 13, "slug": "ngc", "fs_slug": "gamecube", "igdb_id": 99},
        ]

        service.platform_system("ngc")

        assert sources.asked[0][0] == PlatformIds(igdb_id=99)
        kept = decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY))
        assert kept is not None
        assert list(kept) == ["gc", "gamecube"]

    def test_a_platform_kept_by_slug_that_the_listing_names_is_replaced(self, service, uow_factory, romm):
        romm.platforms = [{"id": 3, "slug": "snes", "fs_slug": "sfc", "igdb_id": 19}, _PSX]
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 99}, "n64": {"igdb_id": 4}}))

        service.platform_system("psx")

        kept = decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY))
        assert kept is not None
        assert sorted(kept) == ["n64", "psx", "sfc"]

    def test_a_listed_folder_takes_the_key_of_a_slug_the_listing_does_not_name(self, service, uow_factory, romm):
        romm.platforms = [{"id": 12, "slug": "ngc", "fs_slug": "gc", "igdb_id": 21}]

        service.platform_system("gc")

        kept = decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY))
        assert kept is not None
        assert kept["gc"].slug == "ngc"

    def test_a_live_read_keeps_the_rom_m_version_it_was_read_with(self, service, uow_factory, romm):
        romm.set_version("5.4.1")

        service.platform_system("snes")

        kept = decode_platform_ids(uow_factory.uow.kv_config.get(PLATFORM_IDS_KEY))
        assert kept is not None
        assert {entry.romm_version for entry in kept.values()} == {"5.4.1"}

    def test_the_listed_platforms_are_named_by_their_slug_not_their_folder(self, service, uow_factory):
        _keep(uow_factory, encode_platform_ids({"gc": KeptPlatform(slug="ngc", fs_slug="gc")}))

        assert service.listed_platforms() == frozenset({"ngc"})


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


class TestTheListedPlatforms:
    def test_they_are_every_platform_whose_ids_are_kept(self, service, uow_factory, romm):
        _keep(uow_factory, json.dumps({"gb": {"igdb_id": 33}, "gbc": {"igdb_id": 22}}))

        assert service.listed_platforms() == frozenset({"gb", "gbc"})
        assert _listing_reads(romm) == 0

    def test_without_any_kept_there_is_no_answer(self, service, romm):
        assert service.listed_platforms() is None
        assert _listing_reads(romm) == 0


class TestAPlatformJustReadIsNamedByItsFolder:
    def test_a_platform_read_from_the_listing_is_named_by_the_name_kept_for_its_fs_slug(
        self, service, uow_factory, romm
    ):
        """The names cache is keyed by ``fs_slug`` (#2452 D10), also for a platform no ids were kept for."""
        romm.platforms = [{"id": 7, "slug": "psx", "fs_slug": "ps", "name": "PlayStation", "igdb_id": 7}]
        uow_factory.uow.kv_config.set("platform_names", json.dumps({"ps": "Sony PlayStation"}))

        assert service.platform_system("psx").platform_name == "Sony PlayStation"


class TestAListingReadHereIsRefiled:
    """Every read of RomM's platform listing hands it to the refiling (#2452 D6, D7)."""

    def test_the_listing_read_for_a_platform_with_no_ids_is_handed_over(self, service, romm, refiling):
        service.platform_system("snes")

        assert refiling.listings == [romm.platforms]

    def test_kept_ids_read_no_listing_and_hand_nothing_over(self, service, uow_factory, refiling):
        _keep(uow_factory, json.dumps({"snes": {"igdb_id": 19}}))

        service.platform_system("snes")

        assert refiling.listings == []

    def test_a_refiling_that_fails_costs_nothing_of_the_answer(self, uow_factory, romm, sources, caplog):
        service = PlatformSystemService(
            config=PlatformSystemServiceConfig(
                uow_factory=uow_factory,
                romm_api=romm,
                refiling=FakeListingRefiler(error=OSError("disk full")),
                source_platform_systems=sources,
                logger=logging.getLogger("test_platform_systems"),
                log_debug=MagicMock(),
            )
        )

        with caplog.at_level(logging.WARNING):
            service.platform_system("snes")

        assert sources.asked[0][0] == PlatformIds(igdb_id=19, ss_id=4, name="Super Nintendo")
        assert "Failed to move older per-platform entries to their platforms: disk full" in caplog.text
