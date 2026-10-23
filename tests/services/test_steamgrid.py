import asyncio
import base64
import hashlib
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from _factories import (
    _make_conflict_rules,
    _make_prune_conflicts,
    _record_operations_at_lease,
    _refused_by_conflict_rule,
)
from _vendor import vdf
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_cover_slot_reader import FakeCoverSlotReader
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_emulator_sources import FakeEmulatorSources
from fakes.fake_renderer_gc import FakeRendererGc
from fakes.fake_renderer_rss import FakeRendererRss
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_sgdb_artwork_cache import FakeSgdbArtworkCache
from fakes.fake_shortcut_artwork_job import FakeShortcutArtworkJob
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.library_peers import FakeArtworkManager
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock, FakeSleeper, FakeUuidGen

from adapters.debug_logger import SettingsAwareDebugLogger
from adapters.steam_config import SteamConfigAdapter
from adapters.steamgriddb import SteamGridDbAdapter
from domain.artwork_slot import ArtworkSlot
from domain.artwork_slot_choice import ArtworkSlotChoice
from domain.artwork_slot_handover import ArtworkSlotHandover
from domain.artwork_slot_outcome import RULED_OUT_SECONDS, ArtworkSlotOutcome, SlotOutcome
from domain.rom import Rom
from lib.errors import Refused, SgdbApiError, SgdbKeyUnsendableError, SgdbRateLimitedError, SteamGridDirMissingError
from lib.prune_conflicts import PruneConflicts
from models.shortcut_artwork import SlotAnswer, WrittenIcon
from services.library import LibraryService, LibraryServiceConfig
from services.steamgrid import SteamGridService, SteamGridServiceConfig


@pytest.fixture
def sgdb_artwork_cache():
    """Fresh in-memory SGDB artwork cache per test."""
    return FakeSgdbArtworkCache(cache_root="/runtime")


@pytest.fixture
def uow() -> FakeUnitOfWork:
    """Shared in-memory UoW the tests seed (``uow.roms``) and assert against."""
    return FakeUnitOfWork()


def _seed_rom(uow, rom_id, *, app_id=1, sgdb_id=None, platform_slug="n64", name="Game"):
    """Insert a bound (or unbound when app_id is None) ROM into the fake UoW."""
    rom = Rom(
        rom_id=rom_id,
        platform_slug=platform_slug,
        name=name,
        fs_name=f"{name}.z64",
        shortcut_app_id=app_id,
        last_synced_at="2025-01-01T00:00:00",
        sgdb_id=sgdb_id,
    )
    with uow:
        uow.roms.save(rom)


@dataclass
class SteamGridHarness:
    """The SteamGridDB service and what its tests seed or assert against beside it.

    ``library`` is the library service whose pending sync the SteamGridDB
    service reads when ``roms`` holds no SGDB id for the ROM. ``settings`` is the
    dict both services read, the SteamGridDB API key included. ``clock`` times
    an artwork slot's outcome. ``cover`` is the cover's owner the service reads
    the cover's state from and reloads RomM's cover through.
    """

    service: SteamGridService
    library: LibraryService
    steam_config: SteamConfigAdapter
    prune_conflicts: PruneConflicts
    settings: dict[str, Any]
    clock: FakeClock
    cover: FakeCoverSlotReader = field(default_factory=FakeCoverSlotReader)


@pytest.fixture
def steamgrid(sgdb_artwork_cache, fake_romm_api, fake_steamgrid_db_api, uow, emit, logger, home) -> SteamGridHarness:
    settings: dict[str, Any] = {"romm_url": "", "romm_user": "", "romm_pass": "", "enabled_platforms": {}}
    debug_logger = SettingsAwareDebugLogger(settings=settings, logger=logger)
    steam_config = SteamConfigAdapter(user_home=str(home), logger=logger)
    prune_conflicts = _make_prune_conflicts()
    clock = FakeClock()
    cover = FakeCoverSlotReader()

    library = LibraryService(
        config=LibraryServiceConfig(
            romm_api=fake_romm_api,
            steam_config=steam_config,
            settings=settings,
            loop=running_loop(),
            logger=logger,
            launcher_exe=f"{home}/.local/bin/tender-rom-launcher",
            emit=emit,
            clock=FakeClock(),
            uuid_gen=FakeUuidGen(),
            sleeper=FakeSleeper(),
            settings_persister=FakeSettingsPersister(),
            log_debug=debug_logger,
            artwork=FakeArtworkManager(),
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            active_core=FakeActiveCoreResolver(default=(None, None)),
            disc_resolver=FakeDiscResolver(),
            emulator_sources=FakeEmulatorSources(),
            renderer_rss=FakeRendererRss(),
            renderer_gc=FakeRendererGc(),
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
            artwork_job=FakeShortcutArtworkJob(),
        ),
    )

    # Bind the fake SGDB transport to the in-memory artwork cache so
    # `download_image` writes land in the cache the service consults.
    fake_steamgrid_db_api.bind_artwork_cache(sgdb_artwork_cache)

    service = SteamGridService(
        config=SteamGridServiceConfig(
            sgdb_api=fake_steamgrid_db_api,
            romm_api=fake_romm_api,
            steam_config=steam_config,
            sgdb_artwork_cache=sgdb_artwork_cache,
            settings=settings,
            loop=running_loop(),
            logger=logger,
            settings_persister=FakeSettingsPersister(),
            get_pending_sync=lambda: library._pending_sync,
            log_debug=debug_logger,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
            downscale_icon=lambda data: data,
            clock=clock,
            cover=cover,
        ),
    )
    return SteamGridHarness(
        service=service,
        library=library,
        steam_config=steam_config,
        prune_conflicts=prune_conflicts,
        settings=settings,
        clock=clock,
        cover=cover,
    )


def _cached_path(cache: FakeSgdbArtworkCache, rom_id: int, asset_type: str, image_id: int | None = None) -> str:
    """*rom_id*'s *asset_type* in the cache: under *image_id*, or under the name an older release gave it."""
    name = f"{rom_id}_{asset_type}.png" if image_id is None else f"{rom_id}_{asset_type}_{image_id}.png"
    return os.path.join(cache.cache_dir(), name)


class TestVerifySgdbApiKey:
    @pytest.mark.asyncio
    async def test_valid_api_key(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.seed_verify_response({"success": True})

        result = await steamgrid.service.verify_sgdb_api_key("valid-key-123")

        assert result["success"] is True
        assert "valid" in result["message"].lower()
        assert fake_steamgrid_db_api.verify_calls == ["valid-key-123"]

    @pytest.mark.asyncio
    async def test_invalid_api_key_401(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.verify_api_key_side_effect = SgdbApiError(401, "Unauthorized")
        verifying = steamgrid.service.verify_sgdb_api_key("bad-key")

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "Invalid API key")

    @pytest.mark.asyncio
    async def test_invalid_api_key_403(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.verify_api_key_side_effect = SgdbApiError(403, "Forbidden")
        verifying = steamgrid.service.verify_sgdb_api_key("bad-key")

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "Invalid API key")

    @pytest.mark.asyncio
    async def test_empty_string_falls_back_to_saved_key(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        steamgrid.settings["steamgriddb_api_key"] = "saved-key-456"
        fake_steamgrid_db_api.seed_verify_response({"success": True})

        result = await steamgrid.service.verify_sgdb_api_key("")

        assert result["success"] is True
        # Verify it used the saved key
        assert fake_steamgrid_db_api.verify_calls == ["saved-key-456"]

    @pytest.mark.asyncio
    async def test_masked_value_falls_back_to_saved_key(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        steamgrid.settings["steamgriddb_api_key"] = "saved-key-789"
        fake_steamgrid_db_api.seed_verify_response({"success": True})

        result = await steamgrid.service.verify_sgdb_api_key("••••")

        assert result["success"] is True
        assert fake_steamgrid_db_api.verify_calls == ["saved-key-789"]

    @pytest.mark.asyncio
    async def test_no_key_configured(self, steamgrid):
        steamgrid.service._loop = asyncio.get_running_loop()
        # No saved key, no provided key
        verifying = steamgrid.service.verify_sgdb_api_key("")

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("no_api_key", "No API key configured")

    @pytest.mark.asyncio
    async def test_no_key_at_all_default_param(self, steamgrid):
        steamgrid.service._loop = asyncio.get_running_loop()
        verifying = steamgrid.service.verify_sgdb_api_key()

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("no_api_key", "No API key configured")

    @pytest.mark.asyncio
    async def test_network_error(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.verify_api_key_side_effect = SgdbApiError(None, "DNS resolution failed")
        verifying = steamgrid.service.verify_sgdb_api_key("some-key")

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("server_unreachable", "Could not reach SteamGridDB")

    @pytest.mark.asyncio
    async def test_sgdb_rejects_key(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.seed_verify_response({"success": False})
        verifying = steamgrid.service.verify_sgdb_api_key("rejected-key")

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "API key rejected by SteamGridDB")

    @pytest.mark.asyncio
    async def test_http_500_error(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.verify_api_key_side_effect = SgdbApiError(500, "Internal Server Error")
        verifying = steamgrid.service.verify_sgdb_api_key("some-key")

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("server_unreachable", "SteamGridDB error: HTTP 500")

    @pytest.mark.asyncio
    async def test_bad_gateway_names_its_status(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.verify_api_key_side_effect = SgdbApiError(502, "HTTP Error 502: Bad Gateway")
        verifying = steamgrid.service.verify_sgdb_api_key("some-key")

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("server_unreachable", "SteamGridDB error: HTTP 502")

    @pytest.mark.asyncio
    async def test_anything_but_an_sgdb_api_error_is_not_a_refusal(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.verify_api_key_side_effect = RuntimeError("a bug")
        verifying = steamgrid.service.verify_sgdb_api_key("some-key")

        with pytest.raises(RuntimeError):
            await verifying


class TestGetSgdbArtworkBase64:
    @pytest.mark.asyncio
    async def test_cached_artwork_returns_base64(self, steamgrid, sgdb_artwork_cache):
        import base64

        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # Pre-populate the in-memory cache
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "hero", 7)] = b"fake png data"

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)  # 1 = hero
        assert result["no_api_key"] is False
        assert result["base64"] is not None
        assert base64.b64decode(result["base64"]) == b"fake png data"

    @pytest.mark.asyncio
    async def test_no_api_key_returns_no_api_key_true(self, steamgrid):
        # No API key in settings
        steamgrid.service._loop = asyncio.get_running_loop()

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)
        assert result["base64"] is None
        assert result["no_api_key"] is True

    @pytest.mark.asyncio
    async def test_invalid_asset_type(self, steamgrid):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 99)
        assert result["base64"] is None
        assert result["no_api_key"] is False

    @pytest.mark.asyncio
    async def test_state_only_no_romm_call_when_state_empty(self, steamgrid, fake_romm_api, fake_steamgrid_db_api):
        """base64 path is state-only — a registry row without sgdb_id never hits RomM."""
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # roms is empty — no row carries an sgdb_id for rom 42.
        # RomM would resolve if (incorrectly) consulted — it must not be.
        fake_romm_api.roms[42] = {"id": 42, "sgdb_id": 9999}

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        assert result["base64"] is None
        assert result["no_api_key"] is False
        # No RomM read happened on the passive path.
        assert not any(name == "get_rom" for name, _a, _k in fake_romm_api.call_log)
        assert not any(p.startswith("/games/igdb/") for p in fake_steamgrid_db_api.requested_paths)

    @pytest.mark.asyncio
    async def test_no_sgdb_id_in_state(self, steamgrid, fake_romm_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        assert result["base64"] is None
        assert result["no_api_key"] is False

    @pytest.mark.asyncio
    async def test_download_fails_returns_null(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # SGDB returns a URL but the image download fails (CDN 5xx).
        fake_steamgrid_db_api.seed_artwork(9999, "hero", "https://example.com/hero.png")
        fake_steamgrid_db_api.download_image_return = False

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        assert result["base64"] is None
        assert result["no_api_key"] is False

    @pytest.mark.asyncio
    async def test_sgdb_id_from_pending_sync(self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api):
        import base64

        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # Not in registry, but in pending sync with a resolved sgdb_id.
        steamgrid.library._pending_sync[42] = {
            "name": "Zelda",
            "platform_name": "N64",
            "sgdb_id": 9999,
        }

        # SGDB serves logo artwork for the cached sgdb_id.
        fake_steamgrid_db_api.seed_artwork(9999, "logo", "https://example.com/logo.png")
        fake_steamgrid_db_api.seed_image_bytes("https://example.com/logo.png", b"logo data")

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 2)  # 2 = logo

        assert result["base64"] is not None
        assert base64.b64decode(result["base64"]) == b"logo data"

    @pytest.mark.asyncio
    async def test_sgdb_id_cached_in_registry(self, steamgrid, uow, sgdb_artwork_cache, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # ROM with sgdb_id already cached on its row.
        _seed_rom(uow, 42, app_id=100001, sgdb_id=9999, name="Zelda")

        # Grid artwork available for the cached SGDB id.
        fake_steamgrid_db_api.seed_artwork(9999, "grid", "https://example.com/grid.png")
        fake_steamgrid_db_api.seed_image_bytes("https://example.com/grid.png", b"grid data")

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 3)  # 3 = grid

        assert not any(p.startswith("/games/igdb/") for p in fake_steamgrid_db_api.requested_paths)
        # The artwork request uses the cached sgdb_id.
        assert any("/grids/game/9999" in p for p in fake_steamgrid_db_api.requested_paths)
        assert result["base64"] is not None


class TestLogLevel:
    @pytest.mark.asyncio
    async def test_sgdb_artwork_silent_when_debug_off(self, steamgrid, logger):
        """SGDB artwork info calls should not log when log_level is 'warn'."""
        from unittest.mock import patch

        steamgrid.settings["log_level"] = "warn"
        with patch.object(logger, "info") as mock_info:
            result = await steamgrid.service.get_sgdb_artwork_base64(1, 99)
            assert result["base64"] is None
            for call in mock_info.call_args_list:
                assert "SGDB artwork" not in str(call)

    @pytest.mark.asyncio
    async def test_sgdb_artwork_logs_when_debug_enabled(self, steamgrid, logger):
        """SGDB artwork info calls should log when log_level is 'debug'."""
        from unittest.mock import patch

        steamgrid.settings["log_level"] = "debug"
        steamgrid.settings["steamgriddb_api_key"] = ""
        with patch.object(logger, "info") as mock_info:
            result = await steamgrid.service.get_sgdb_artwork_base64(1, 1)
            assert result["no_api_key"] is True
            logged_msgs = [str(c) for c in mock_info.call_args_list]
            assert any("SGDB artwork" in m for m in logged_msgs)


class TestConflictRulesAtTheUseCase:
    """Each SteamGridDB use case checks the prune rule of the endpoint that calls it, and the artwork
    answer's lease is taken inside it."""

    @pytest.mark.asyncio
    async def test_an_image_carries_a_lease_taken_inside_the_use_cases_operation(
        self, steamgrid, sgdb_artwork_cache, monkeypatch
    ):
        """No cleanup can start between the fetch's operation and the lease that outlasts it."""
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "hero", 7)] = b"fake png data"
        seen = _record_operations_at_lease(steamgrid.prune_conflicts, monkeypatch)

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        assert seen == [["get_sgdb_artwork_base64"]]
        assert result["prune_lease_token"].startswith("sgdb_artwork:")
        assert steamgrid.prune_conflicts.conflicting_operations == 1
        await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])
        assert steamgrid.prune_conflicts.conflicting_operations == 0

    @pytest.mark.asyncio
    async def test_an_answer_without_an_image_carries_no_lease(self, steamgrid):
        steamgrid.service._loop = asyncio.get_running_loop()

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        assert result == {"base64": None, "no_api_key": True}
        assert steamgrid.prune_conflicts.conflicting_operations == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("use_case", "args"),
        [
            ("get_sgdb_artwork_base64", (42, 1)),
            ("save_shortcut_icon", (42, 42)),
            ("get_sgdb_resolution", (42,)),
            ("apply_sgdb_game_id", (42, 7)),
        ],
    )
    async def test_a_running_cleanup_refuses_the_use_case(self, steamgrid, use_case, args):
        steamgrid.prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await getattr(steamgrid.service, use_case)(*args)

        assert steamgrid.prune_conflicts.conflicting_operations == 0


# Fake keys no HTTP header can carry: a line break that would end the header, and a character past latin-1.
_KEYS_NO_HEADER_CAN_CARRY = [
    pytest.param("fake-sgdb-key\nX-Injected: 1", id="line-feed"),
    pytest.param("fake-sgdb-key-\u2019", id="past-latin-1"),
]


class TestAKeyNoHeaderCanCarry:
    """A key that cannot travel in the ``Authorization`` header is refused before any request is made."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", _KEYS_NO_HEADER_CAN_CARRY)
    async def test_the_key_check_refuses_a_typed_key_without_a_request(self, steamgrid, fake_steamgrid_db_api, key):
        steamgrid.service._loop = asyncio.get_running_loop()
        verifying = steamgrid.service.verify_sgdb_api_key(key)

        with pytest.raises(Refused) as refused:
            await verifying

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "Invalid API key")
        assert fake_steamgrid_db_api.call_log == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", _KEYS_NO_HEADER_CAN_CARRY)
    @pytest.mark.parametrize(
        ("use_case", "args"),
        [
            ("verify_sgdb_api_key", ()),
            ("search_sgdb_games", ("Zelda",)),
            ("get_sgdb_resolution", (42,)),
            ("apply_sgdb_game_id", (42, 7)),
            ("get_sgdb_artwork_base64", (42, 1)),
        ],
    )
    async def test_every_use_case_that_sends_the_stored_key_refuses_it_without_a_request(
        self, steamgrid, uow, fake_romm_api, fake_steamgrid_db_api, key, use_case, args
    ):
        steamgrid.settings["steamgriddb_api_key"] = key
        steamgrid.service._loop = asyncio.get_running_loop()
        _seed_rom(uow, 42, sgdb_id=9999, name="Zelda")
        fake_romm_api.roms[42] = {"id": 42, "name": "Zelda", "igdb_id": 1234}
        answering = getattr(steamgrid.service, use_case)(*args)

        with pytest.raises(Refused) as refused:
            await answering

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "Invalid API key")
        assert fake_steamgrid_db_api.call_log == []


class TestGetSgdbResolution:
    """The picker-driven resolution cascade in ``get_sgdb_resolution``.

    Exercises every ``classify_resolution`` branch (RomM is the source of
    truth) plus the unresolved fall-through to the name-search picker.
    """

    @pytest.mark.asyncio
    async def test_no_api_key(self, steamgrid):
        steamgrid.service._loop = asyncio.get_running_loop()
        result = await steamgrid.service.get_sgdb_resolution(42)
        assert result == {"decision": "no_api_key"}

    @pytest.mark.asyncio
    async def test_use_state_when_romm_silent(self, steamgrid, uow, fake_romm_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        _seed_rom(uow, 42, app_id=1, sgdb_id=9999)
        # RomM has no sgdb_id → state wins, nothing persisted.
        fake_romm_api.roms[42] = {"id": 42}

        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "resolved", "sgdb_id": 9999}

    @pytest.mark.asyncio
    async def test_use_romm_persists_when_state_empty(self, steamgrid, uow, fake_romm_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # ROM row has no sgdb_id; RomM supplies one → persisted on the row.
        _seed_rom(uow, 42, app_id=1)
        fake_romm_api.roms[42] = {"id": 42, "sgdb_id": 7777}

        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "resolved", "sgdb_id": 7777}
        with uow:
            assert uow.roms.get(42).sgdb_id == 7777

    @pytest.mark.asyncio
    async def test_romm_wins_over_differing_state_and_persists(self, steamgrid, uow, fake_romm_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # Row holds an old id; RomM disagrees → RomM overwrites it.
        _seed_rom(uow, 42, app_id=1, sgdb_id=9999)
        fake_romm_api.roms[42] = {"id": 42, "sgdb_id": 7777}

        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "resolved", "sgdb_id": 7777}
        with uow:
            assert uow.roms.get(42).sgdb_id == 7777

    @pytest.mark.asyncio
    async def test_romm_matches_state_no_persist(self, steamgrid, uow, fake_romm_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # RomM agrees with the row → resolved, no redundant write.
        _seed_rom(uow, 42, app_id=1, sgdb_id=7777)
        fake_romm_api.roms[42] = {"id": 42, "sgdb_id": 7777}
        save_count_before = uow.roms.save_count

        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "resolved", "sgdb_id": 7777}
        assert uow.roms.save_count == save_count_before

    @pytest.mark.asyncio
    async def test_unresolved_needs_pick_and_searches_nothing(
        self, steamgrid, uow, fake_romm_api, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        _seed_rom(uow, 42, app_id=1)
        # No sgdb_id: the game is chosen in the ARTWORK tab, nothing is searched.
        fake_romm_api.roms[42] = {"id": 42, "name": "Zelda"}

        fake_steamgrid_db_api.seed_raw_response(
            "/search/autocomplete/Zelda",
            {"success": True, "data": [{"id": 100, "name": "Zelda", "release_date": 1234567890}]},
        )
        fake_steamgrid_db_api.seed_raw_response(
            "/grids/game/100", {"success": True, "data": [{"thumb": "z-thumb.png"}]}
        )

        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "needs_pick", "candidates": []}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_an_igdb_id_without_a_steamgriddb_id_asks_steamgriddb_nothing(
        self, steamgrid, uow, fake_romm_api, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        _seed_rom(uow, 42, app_id=1, name="Zelda")
        fake_romm_api.roms[42] = {"id": 42, "igdb_id": 1234, "name": "Zelda"}
        fake_steamgrid_db_api.seed_raw_response(
            "/search/autocomplete/Zelda",
            {"success": True, "data": [{"id": 100, "name": "Zelda", "release_date": 1234567890}]},
        )
        fake_steamgrid_db_api.seed_raw_response(
            "/grids/game/100", {"success": True, "data": [{"thumb": "z-thumb.png"}]}
        )

        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "needs_pick", "candidates": []}
        assert fake_steamgrid_db_api.requested_paths == []


class TestSearchSgdbGames:
    @pytest.mark.asyncio
    async def test_happy_path_enriches_thumbs(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        fake_steamgrid_db_api.seed_raw_response(
            "/search/autocomplete/mario",
            {
                "success": True,
                "data": [
                    {"id": 1, "name": "Mario", "release_date": 1234567890},
                    {"id": 2, "name": "Mario 2"},
                ],
            },
        )
        fake_steamgrid_db_api.seed_raw_response("/grids/game/1", {"success": True, "data": [{"thumb": "m1.png"}]})
        # game 2 has no grid → thumb None.

        result = await steamgrid.service.search_sgdb_games("mario")

        assert result["success"] is True
        assert result["games"] == [
            {"id": 1, "name": "Mario", "release_year": 2009, "thumb_url": "m1.png"},
            {"id": 2, "name": "Mario 2", "release_year": None, "thumb_url": None},
        ]

    @pytest.mark.asyncio
    async def test_no_api_key(self, steamgrid):
        steamgrid.service._loop = asyncio.get_running_loop()
        searching = steamgrid.service.search_sgdb_games("mario")

        with pytest.raises(Refused) as refused:
            await searching

        assert (refused.value.reason, refused.value.message) == ("no_api_key", "No SteamGridDB API key configured")

    @pytest.mark.asyncio
    async def test_network_error_refuses_with_a_fixed_message(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(None, "DNS failed")
        searching = steamgrid.service.search_sgdb_games("mario")

        with pytest.raises(Refused) as refused:
            await searching

        assert (refused.value.reason, refused.value.message) == ("server_unreachable", "Could not reach SteamGridDB")

    @pytest.mark.asyncio
    async def test_a_rejected_key_refuses_auth_failed(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(401, "HTTP Error 401: Unauthorized")
        searching = steamgrid.service.search_sgdb_games("mario")

        with pytest.raises(Refused) as refused:
            await searching

        assert (refused.value.reason, refused.value.message) == (
            "auth_failed",
            "SteamGridDB refused the API key — check it in Settings",
        )

    @pytest.mark.asyncio
    async def test_a_key_the_adapter_cannot_send_refuses_as_an_invalid_key(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.request_side_effect = SgdbKeyUnsendableError()
        searching = steamgrid.service.search_sgdb_games("mario")

        with pytest.raises(Refused) as refused:
            await searching

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "Invalid API key")

    @pytest.mark.asyncio
    async def test_a_server_error_names_its_status(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(503, "HTTP Error 503: Service Unavailable")
        searching = steamgrid.service.search_sgdb_games("mario")

        with pytest.raises(Refused) as refused:
            await searching

        assert (refused.value.reason, refused.value.message) == ("server_unreachable", "SteamGridDB error: HTTP 503")

    @pytest.mark.asyncio
    async def test_anything_but_an_sgdb_api_error_is_not_a_refusal(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        fake_steamgrid_db_api.request_side_effect = RuntimeError("a bug")
        searching = steamgrid.service.search_sgdb_games("mario")

        with pytest.raises(RuntimeError):
            await searching

    @pytest.mark.asyncio
    async def test_caps_at_six_candidates(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        data = [{"id": i, "name": f"Game {i}"} for i in range(1, 11)]
        fake_steamgrid_db_api.seed_raw_response("/search/autocomplete/many", {"success": True, "data": data})

        result = await steamgrid.service.search_sgdb_games("many")

        assert result["success"] is True
        assert len(result["games"]) == 6


# The SteamGridDB image id ``_seed_all_artwork`` seeds each asset type as, one apart.
_PICKED_IMAGE_IDS = {"hero": 801, "logo": 802, "grid": 803, "icon": 804}


def _seed_all_artwork(fake_steamgrid_db_api, sgdb_id: int) -> None:
    """Seed the four asset URLs + bytes for *sgdb_id* on the fake transport."""
    for asset_type in ("hero", "logo", "grid", "icon"):
        url = f"https://example.com/{sgdb_id}_{asset_type}.png"
        fake_steamgrid_db_api.seed_artwork(sgdb_id, asset_type, url, _PICKED_IMAGE_IDS[asset_type])
        fake_steamgrid_db_api.seed_image_bytes(url, f"{asset_type} bytes".encode())


def _picked_path(cache: FakeSgdbArtworkCache, rom_id: int, asset_type: str) -> str:
    """Where ``_seed_all_artwork``'s *asset_type* is cached: under its image id."""
    return os.path.join(cache.cache_dir(), f"{rom_id}_{asset_type}_{_PICKED_IMAGE_IDS[asset_type]}.png")


class TestApplySgdbGameId:
    @pytest.mark.asyncio
    async def test_downloads_all_four_asset_types_for_picked_id(
        self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        """A manual pick paints all four asset types into the rom's cache."""
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        result = await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert result == {"success": True}
        # All four cache files written for rom 42 with the picked game's bytes.
        for asset_type in ("hero", "logo", "grid", "icon"):
            path = _picked_path(sgdb_artwork_cache, 42, asset_type)
            assert sgdb_artwork_cache.files[path] == f"{asset_type} bytes".encode()
        # The four artwork requests targeted sgdb_id 8888.
        for endpoint in ("heroes", "logos", "grids", "icons"):
            assert any(f"/{endpoint}/game/8888" in p for p in fake_steamgrid_db_api.requested_paths)

    @pytest.mark.asyncio
    async def test_clears_existing_cache_before_redownload(self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api):
        """A re-pick evicts the prior PNGs first, then paints the new game's art."""
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        # Stale art from a previous pick.
        for asset_type in ("hero", "logo", "grid", "icon"):
            sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, asset_type)] = b"old"
        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        # Old bytes gone; new bytes painted in (cleared, not short-circuited).
        for asset_type in ("hero", "logo", "grid", "icon"):
            path = _picked_path(sgdb_artwork_cache, 42, asset_type)
            assert sgdb_artwork_cache.files[path] == f"{asset_type} bytes".encode()
        # The images cached under their old names are gone too.
        for asset_type in ("hero", "logo", "grid", "icon"):
            assert _cached_path(sgdb_artwork_cache, 42, asset_type) not in sgdb_artwork_cache.files

    @pytest.mark.asyncio
    async def test_stores_the_pick_beside_romms_id(self, steamgrid, uow, sgdb_artwork_cache, fake_steamgrid_db_api):
        """A manual pick is stored as the user's choice and never written as RomM's sgdb_id."""
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        _seed_rom(uow, 42, app_id=1)
        save_count_before = uow.roms.save_count
        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        with uow:
            rom = uow.roms.get(42)
        assert (rom.sgdb_id, rom.chosen_sgdb_id) == (None, 8888)
        assert uow.roms.save_count == save_count_before

    @pytest.mark.asyncio
    async def test_coerces_string_args(self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        result = await steamgrid.service.apply_sgdb_game_id("42", "8888")

        assert result == {"success": True}
        assert sgdb_artwork_cache.files[_picked_path(sgdb_artwork_cache, 42, "hero")] == b"hero bytes"

    @pytest.mark.asyncio
    async def test_missing_row_still_succeeds(self, steamgrid, uow, sgdb_artwork_cache, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        # No roms row for rom_id 42.
        result = await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert result == {"success": True}
        # Row absent → still painted artwork, still nothing persisted.
        with uow:
            assert uow.roms.get(42) is None
        assert sgdb_artwork_cache.files[_picked_path(sgdb_artwork_cache, 42, "hero")] == b"hero bytes"

    @pytest.mark.asyncio
    async def test_partial_download_failure_still_succeeds(self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api):
        """One asset failing to download must not break the pick — the rest paint."""
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        # Seed only three of the four assets — "logo" stays unseeded so its
        # download yields no data, simulating a single-asset failure.
        for asset_type in ("hero", "grid", "icon"):
            url = f"https://example.com/8888_{asset_type}.png"
            fake_steamgrid_db_api.seed_artwork(8888, asset_type, url, _PICKED_IMAGE_IDS[asset_type])
            fake_steamgrid_db_api.seed_image_bytes(url, f"{asset_type} bytes".encode())

        result = await steamgrid.service.apply_sgdb_game_id(42, 8888)

        # The gather must not propagate the missing asset.
        assert result == {"success": True}
        # The three seeded assets painted; the unseeded "logo" left no file.
        for asset_type in ("hero", "grid", "icon"):
            path = _picked_path(sgdb_artwork_cache, 42, asset_type)
            assert sgdb_artwork_cache.files[path] == f"{asset_type} bytes".encode()
        assert not any("_logo" in path for path in sgdb_artwork_cache.files)

    @pytest.mark.asyncio
    async def test_the_next_resolution_resolves_to_the_pick_without_asking_again(
        self, steamgrid, uow, sgdb_artwork_cache, fake_romm_api, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()

        _seed_rom(uow, 42, app_id=1)
        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        fake_romm_api.roms[42] = {"id": 42, "name": "Obscure Port"}
        requested = len(fake_steamgrid_db_api.requested_paths)

        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "resolved", "sgdb_id": 8888}
        assert fake_steamgrid_db_api.requested_paths[requested:] == []

    @pytest.mark.asyncio
    async def test_an_id_romm_supplies_later_wins_over_the_pick(
        self, steamgrid, uow, sgdb_artwork_cache, fake_romm_api, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        _seed_rom(uow, 42, app_id=1)
        _seed_all_artwork(fake_steamgrid_db_api, 8888)
        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        fake_romm_api.roms[42] = {"id": 42, "sgdb_id": 7777}
        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "resolved", "sgdb_id": 7777}
        with uow:
            rom = uow.roms.get(42)
        assert (rom.sgdb_id, rom.chosen_sgdb_id) == (7777, 8888)

    @pytest.mark.asyncio
    async def test_romm_naming_the_picked_game_is_still_stored_as_romms(
        self, steamgrid, uow, sgdb_artwork_cache, fake_romm_api, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        _seed_rom(uow, 42, app_id=1)
        _seed_all_artwork(fake_steamgrid_db_api, 8888)
        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        fake_romm_api.roms[42] = {"id": 42, "sgdb_id": 8888}
        result = await steamgrid.service.get_sgdb_resolution(42)

        assert result == {"decision": "resolved", "sgdb_id": 8888}
        with uow:
            assert uow.roms.get(42).sgdb_id == 8888


class TestIconSupport:
    """The icon's SteamGridDB endpoint, and that asset type 4 serves no icon."""

    @pytest.mark.asyncio
    async def test_icon_type_maps_to_icons_endpoint(self, steamgrid):
        """Asset type 'icon' should map to the SGDB /icons/ endpoint."""
        assert steamgrid.service._download_sgdb_artwork  # method exists

    @pytest.mark.asyncio
    async def test_the_icon_asset_type_is_refused_and_nothing_is_asked(
        self, steamgrid, uow, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.service._loop = asyncio.get_running_loop()
        _seed_rom(uow, 42, app_id=100001, sgdb_id=9999)
        _cache_icon(sgdb_artwork_cache, 42, b"icon png data")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_sgdb_artwork_base64(42, 4)  # 4 = icon

        assert (refused.value.reason, refused.value.message) == (
            "invalid_payload",
            "The icon is served by the per-slot fetch",
        )
        assert fake_steamgrid_db_api.requested_paths == []
        assert steamgrid.prune_conflicts.conflicting_operations == 0

    def test_download_sgdb_artwork_icon_endpoint(self, steamgrid, fake_steamgrid_db_api):
        """_download_sgdb_artwork should use /icons/ endpoint for icon type."""
        fake_steamgrid_db_api.seed_artwork(9999, "icon", "https://example.com/icon.png")
        # Don't seed image bytes — download_image_return defaults to True
        # so the service returns the cached path without writing.

        svc = steamgrid.service
        svc._download_sgdb_artwork(9999, 42, "icon")

        # Exactly one /icons/game/9999 request should have been issued.
        icon_requests = [p for p in fake_steamgrid_db_api.requested_paths if "/icons/game/9999" in p]
        assert len(icon_requests) == 1


class TestPruneOrphanedArtworkCache:
    def test_removes_orphan_artwork(self, steamgrid, uow, sgdb_artwork_cache):
        """Artwork for rom_id not bound in roms should be deleted."""
        orphan = _cached_path(sgdb_artwork_cache, 42, "hero")
        sgdb_artwork_cache.files[orphan] = b"orphaned data"

        # roms has rom 99, not 42.
        _seed_rom(uow, 99, app_id=1)

        steamgrid.service.prune_orphaned_artwork_cache()

        assert orphan not in sgdb_artwork_cache.files

    def test_keeps_artwork_in_registry(self, steamgrid, uow, sgdb_artwork_cache):
        """Artwork for a bound rom_id should survive."""
        kept = _cached_path(sgdb_artwork_cache, 42, "hero")
        sgdb_artwork_cache.files[kept] = b"keep me"

        _seed_rom(uow, 42, app_id=1)

        steamgrid.service.prune_orphaned_artwork_cache()

        assert sgdb_artwork_cache.files[kept] == b"keep me"

    def test_removes_leftover_tmp(self, steamgrid, uow, sgdb_artwork_cache):
        """Leftover .tmp files should always be removed regardless of rom_id."""
        tmp_path = _cached_path(sgdb_artwork_cache, 42, "hero") + ".tmp"
        sgdb_artwork_cache.files[tmp_path] = b"tmp data"

        # rom_id 42 IS bound, but .tmp should still be removed.
        _seed_rom(uow, 42, app_id=1)

        steamgrid.service.prune_orphaned_artwork_cache()

        assert tmp_path not in sgdb_artwork_cache.files

    def test_empty_artwork_dir(self, steamgrid):
        """No crash on empty artwork directory."""
        steamgrid.service.prune_orphaned_artwork_cache()
        # Should complete without error

    def test_no_artwork_dir(self, steamgrid, sgdb_artwork_cache):
        """No crash when artwork directory doesn't exist."""
        # Mark the cache directory as absent.
        sgdb_artwork_cache.isdir_paths = set()
        steamgrid.service.prune_orphaned_artwork_cache()
        # Should complete without error

    def test_handles_os_error(self, steamgrid, sgdb_artwork_cache):
        """OSError on cache.remove should log warning, not crash."""
        orphan = _cached_path(sgdb_artwork_cache, 42, "hero")
        sgdb_artwork_cache.files[orphan] = b"orphaned data"

        def raising_remove(_path: str) -> None:
            raise OSError("Permission denied")

        sgdb_artwork_cache.remove_file = raising_remove  # type: ignore[method-assign]
        steamgrid.service.prune_orphaned_artwork_cache()
        # File still exists because remove was patched to fail
        assert orphan in sgdb_artwork_cache.files


def _forbidden_vdf(*_args, **_kwargs):
    """Stand-in for shortcuts.vdf I/O that must never run during an icon save.

    Steam owns shortcuts.vdf in memory and clobbers external writes, so the
    icon-save path only lays down the grid PNG. Wiring this in for
    ``read_shortcuts`` / ``write_shortcuts`` turns any regression that
    re-introduces the VDF read-modify-write into a loud failure.
    """
    raise AssertionError("shortcuts.vdf must not be read or written during an icon save")


class TestSaveShortcutIcon:
    """The game page's icon write (``save_shortcut_icon``).

    It writes the ROM's cached icon into Steam's grid directory and returns its
    path; pointing the shortcut at that file is the frontend's job via
    SteamClient, so this path never touches shortcuts.vdf.
    """

    @pytest.mark.asyncio
    async def test_writes_the_cached_icon_and_answers_what_the_report_needs(
        self, steamgrid, uow, sgdb_artwork_cache, tmp_path
    ):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.steam_config.read_shortcuts = _forbidden_vdf  # type: ignore[method-assign]
        steamgrid.steam_config.write_shortcuts = _forbidden_vdf  # type: ignore[method-assign]
        steamgrid.service._loop = asyncio.get_running_loop()
        _seed_rom(uow, 42, app_id=12345, sgdb_id=9999)
        _cache_icon(sgdb_artwork_cache, 42, b"real icon png", image_id=31)

        result = await steamgrid.service.save_shortcut_icon(12345, 42)

        expected = os.path.join(str(tmp_path), "12345_icon.png")
        assert result == {
            "success": True,
            "icon_path": expected,
            "image_id": 31,
            "sha256": hashlib.sha256(b"real icon png").hexdigest(),
            "sgdb_id": 9999,
        }
        assert written[expected] == b"real icon png"

    @pytest.mark.asyncio
    async def test_an_icon_the_cache_does_not_hold_is_refused(self, steamgrid, tmp_path):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()

        with pytest.raises(Refused) as refused:
            await steamgrid.service.save_shortcut_icon(12345, 42)

        assert refused.value.reason == "icon_write_failed"
        assert written == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error", [SteamGridDirMissingError("Cannot find Steam grid directory"), OSError("disk full")]
    )
    async def test_a_grid_write_failure_refuses(self, steamgrid, sgdb_artwork_cache, error):
        def raise_error(_app_id, _bytes):
            raise error

        steamgrid.steam_config.write_shortcut_icon = raise_error  # type: ignore[method-assign]
        steamgrid.service._loop = asyncio.get_running_loop()
        _cache_icon(sgdb_artwork_cache, 42, b"real icon png")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.save_shortcut_icon(12345, 42)

        assert (refused.value.reason, refused.value.message) == (
            "icon_write_failed",
            "Failed to write icon to Steam grid directory",
        )


class TestDebugLoggerProtocolSeam:
    """SteamGridService routes debug messages through the injected ``DebugLogger``.

    Locks in the consolidation from #354: SteamGridService no longer owns a
    per-service ``_log_debug`` method that re-reads settings; the protocol
    seam is the sole knob. A no-op injection must suppress every SGDB
    debug message, regardless of log-level settings.
    """

    @pytest.fixture
    def steamgrid_with_captured_log(
        self, sgdb_artwork_cache, fake_romm_api, fake_steamgrid_db_api, emit, logger, home
    ) -> tuple[SteamGridHarness, list[str]]:
        """The harness with ``log_debug`` a list-capturing fake on both services, and the list."""

        settings: dict[str, Any] = {"log_level": "debug", "steamgriddb_api_key": ""}
        prune_conflicts = _make_prune_conflicts()

        captured: list[str] = []

        def capture(msg: str) -> None:
            captured.append(msg)

        steam_config = SteamConfigAdapter(user_home=str(home), logger=logger)

        library = LibraryService(
            config=LibraryServiceConfig(
                romm_api=fake_romm_api,
                steam_config=steam_config,
                settings=settings,
                loop=running_loop(),
                logger=logger,
                launcher_exe=f"{home}/.local/bin/tender-rom-launcher",
                emit=emit,
                clock=FakeClock(),
                uuid_gen=FakeUuidGen(),
                sleeper=FakeSleeper(),
                settings_persister=FakeSettingsPersister(),
                log_debug=capture,
                artwork=FakeArtworkManager(),
                uow_factory=FakeUnitOfWorkFactory(),
                active_core=FakeActiveCoreResolver(default=(None, None)),
                disc_resolver=FakeDiscResolver(),
                emulator_sources=FakeEmulatorSources(),
                renderer_rss=FakeRendererRss(),
                renderer_gc=FakeRendererGc(),
                conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
                artwork_job=FakeShortcutArtworkJob(),
            ),
        )

        fake_steamgrid_db_api.bind_artwork_cache(sgdb_artwork_cache)
        clock = FakeClock()
        service = SteamGridService(
            config=SteamGridServiceConfig(
                sgdb_api=fake_steamgrid_db_api,
                romm_api=fake_romm_api,
                steam_config=steam_config,
                sgdb_artwork_cache=sgdb_artwork_cache,
                settings=settings,
                loop=running_loop(),
                logger=logger,
                settings_persister=FakeSettingsPersister(),
                get_pending_sync=lambda: library._pending_sync,
                log_debug=capture,
                uow_factory=FakeUnitOfWorkFactory(),
                conflict_rules=_make_conflict_rules(),
                downscale_icon=lambda data: data,
                clock=clock,
                cover=FakeCoverSlotReader(),
            ),
        )
        harness = SteamGridHarness(
            service=service,
            library=library,
            steam_config=steam_config,
            prune_conflicts=prune_conflicts,
            settings=settings,
            clock=clock,
        )
        return harness, captured

    @pytest.mark.asyncio
    async def test_sgdb_messages_route_through_injected_debug_logger(self, steamgrid_with_captured_log):
        """SGDB debug messages reach the injected ``log_debug``, not a hidden ``.info()`` seam."""
        steamgrid, captured = steamgrid_with_captured_log
        steamgrid.service._loop = asyncio.get_running_loop()

        # No API key configured -> early "skipped" debug message
        await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        sgdb_msgs = [m for m in captured if "SGDB artwork" in m]
        assert sgdb_msgs, f"Expected SGDB debug messages on injected seam, got: {captured}"

    @pytest.mark.asyncio
    async def test_sgdb_debug_does_not_call_logger_info_directly(self, steamgrid_with_captured_log, logger):
        """SGDB debug must reach the injected callback only — never ``logger.info``.

        Regression for #354: pre-consolidation, ``SteamGridService._log_debug``
        was a per-service method that re-read settings and called
        ``self._logger.info(msg)`` directly whenever
        ``log_level == "debug"``. That meant the injected ``DebugLogger``
        callback was ignored — any consumer that wanted to silence
        SGDB output had no working knob. The protocol consolidation
        replaces the method with an injected callable, so the only
        sink is what bootstrap (or this fixture) provides.

        Would fail on ``main``: there a debug-level config would push
        every SGDB message through ``logger.info`` in addition
        to (and bypassing) the injected callback.
        """
        from unittest.mock import patch

        steamgrid, captured = steamgrid_with_captured_log
        steamgrid.service._loop = asyncio.get_running_loop()
        # log_level=debug — pre-fix this is exactly the state where the
        # per-service ``_log_debug`` emitted via ``self._logger.info``.
        steamgrid.settings["log_level"] = "debug"

        with patch.object(logger, "info") as mock_info:
            await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        # SGDB messages MUST land on the injected seam …
        assert any("SGDB artwork" in m for m in captured), (
            f"SGDB debug must reach the injected log_debug; captured={captured}"
        )
        # … and NOT on logger.info.
        sgdb_info_calls = [c for c in mock_info.call_args_list if "SGDB artwork" in str(c)]
        assert not sgdb_info_calls, (
            "SGDB debug must NOT leak to logger.info — the injected "
            f"DebugLogger is the only sink. Observed leaks: {sgdb_info_calls}"
        )


class TestDownloadSgdbArtwork:
    """SGDB artwork-download error paths in ``_download_sgdb_artwork``.

    Covers the unsupported-asset-type early exit, the cache-hit short
    circuit, every malformed-response branch (success=False, data
    missing, body None), the image-download failure return, and the
    ``except Exception`` net.
    """

    def test_unsupported_asset_type_returns_none(self, steamgrid, fake_steamgrid_db_api):
        """Unknown asset type → early ``None`` (no SGDB request issued)."""
        result = steamgrid.service._download_sgdb_artwork(9999, 42, "no-such-asset-type")

        assert result is None
        assert fake_steamgrid_db_api.requested_paths == []

    def test_cache_hit_short_circuits(self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api):
        """Pre-existing cache file → return cached path, no network call."""
        cached = _cached_path(sgdb_artwork_cache, 42, "hero", 7)
        sgdb_artwork_cache.files[cached] = b"already cached"

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result == cached
        assert fake_steamgrid_db_api.requested_paths == []

    def test_success_false_returns_none(self, steamgrid, fake_steamgrid_db_api):
        """SGDB body with ``success=False`` → None."""
        fake_steamgrid_db_api.seed_raw_response(
            "/heroes/game/9999",
            {"success": False, "data": [{"url": "https://example.com/hero.png"}]},
        )

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result is None
        assert fake_steamgrid_db_api.downloaded == []

    def test_empty_data_returns_none(self, steamgrid, fake_steamgrid_db_api):
        """SGDB body with empty ``data`` list → None (falsy short-circuit)."""
        fake_steamgrid_db_api.seed_raw_response(
            "/heroes/game/9999",
            {"success": True, "data": []},
        )

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result is None
        assert fake_steamgrid_db_api.downloaded == []

    def test_none_response_returns_none(self, steamgrid, fake_steamgrid_db_api):
        """Adapter returns ``None`` → None (no image download)."""
        fake_steamgrid_db_api.seed_raw_response("/heroes/game/9999", None)

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result is None
        assert fake_steamgrid_db_api.downloaded == []

    def test_download_image_failure_returns_none(self, steamgrid, fake_steamgrid_db_api):
        """``download_image`` returns False (e.g. 5xx on CDN) → None."""
        fake_steamgrid_db_api.seed_artwork(9999, "hero", "https://example.com/hero.png")
        fake_steamgrid_db_api.download_image_return = False

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result is None

    def test_sgdb_api_error_swallowed(self, steamgrid, fake_steamgrid_db_api):
        """``SgdbApiError`` raised by ``request`` is logged and swallowed."""
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(500, "Internal Server Error")

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result is None

    def test_malformed_data_key_error_swallowed(self, steamgrid, fake_steamgrid_db_api):
        """Missing ``url`` in data entry → ``KeyError`` is logged and swallowed."""
        fake_steamgrid_db_api.seed_raw_response(
            "/heroes/game/9999",
            {"success": True, "data": [{"id": 1}]},  # no 'url' key
        )

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result is None

    def test_network_error_during_download_swallowed(self, steamgrid, fake_steamgrid_db_api):
        """Connection-level error from ``download_image`` is swallowed."""
        fake_steamgrid_db_api.seed_artwork(9999, "hero", "https://example.com/hero.png")
        fake_steamgrid_db_api.download_image_side_effect = ConnectionError("connection refused")

        result = steamgrid.service._download_sgdb_artwork(9999, 42, "hero")

        assert result is None


class TestReadFileAsBase64:
    """``_read_file_as_base64`` exception branch.

    The happy path is already exercised by ``TestGetSgdbArtworkBase64``;
    this class pins the ``except Exception`` net that turns a cache-read
    failure (corrupt file, permissions error, vanished file) into a
    ``None`` return instead of bubbling the exception to the frontend.
    """

    @pytest.mark.asyncio
    async def test_returns_none_when_read_fails(self, steamgrid, sgdb_artwork_cache):
        """``read_bytes`` raising → ``None`` (frontend sees no artwork)."""
        steamgrid.service._loop = asyncio.get_running_loop()

        def raising_read(_path: str) -> bytes:
            raise OSError("permission denied")

        sgdb_artwork_cache.read_bytes = raising_read  # type: ignore[method-assign]

        result = await steamgrid.service._read_file_as_base64("/runtime/artwork/42_hero.png")

        assert result is None


class TestSaveSgdbApiKey:
    """``save_sgdb_api_key`` happy / masked / empty paths.

    It stores a real key and ignores the masked sentinel (set by the frontend
    modal when the user leaves the input untouched) and the empty string (no
    input given).
    """

    def test_stores_real_key(self, steamgrid):
        """Real key → persisted to settings and ``save_settings`` invoked."""
        persister = FakeSettingsPersister()
        steamgrid.service._settings_persister = persister

        result = steamgrid.service.save_sgdb_api_key("real-api-key-123")

        assert result == {"success": True, "message": "SteamGridDB API key saved"}
        assert steamgrid.settings["steamgriddb_api_key"] == "real-api-key-123"
        assert persister.save_count == 1

    def test_ignores_masked_sentinel(self, steamgrid):
        """Masked value ``••••`` → no settings mutation, no persister call."""
        steamgrid.settings["steamgriddb_api_key"] = "existing-key"
        persister = FakeSettingsPersister()
        steamgrid.service._settings_persister = persister

        result = steamgrid.service.save_sgdb_api_key("••••")

        assert result == {"success": True, "message": "SteamGridDB API key saved"}
        assert steamgrid.settings["steamgriddb_api_key"] == "existing-key"
        assert persister.save_count == 0

    def test_ignores_empty_string(self, steamgrid):
        """Empty input → no settings mutation, no persister call."""
        persister = FakeSettingsPersister()
        steamgrid.service._settings_persister = persister

        result = steamgrid.service.save_sgdb_api_key("")

        assert result == {"success": True, "message": "SteamGridDB API key saved"}
        assert "steamgriddb_api_key" not in steamgrid.settings
        assert persister.save_count == 0


class TestPruneOrphanedArtworkCacheEdgeCases:
    """Edge-case branches in ``prune_orphaned_artwork_cache``.

    Complements ``TestPruneOrphanedArtworkCache`` by covering the OSError
    branch for ``.tmp`` removal and filename shapes that have no rom_id
    prefix.
    """

    def test_tmp_remove_oserror_logged_not_crash(self, steamgrid, sgdb_artwork_cache):
        """OSError when removing a stale ``.tmp`` file is logged, not raised."""
        tmp_path = _cached_path(sgdb_artwork_cache, 42, "hero") + ".tmp"
        sgdb_artwork_cache.files[tmp_path] = b"tmp data"

        def raising_remove(_path: str) -> None:
            raise OSError("permission denied")

        sgdb_artwork_cache.remove_file = raising_remove  # type: ignore[method-assign]

        # Should not crash
        steamgrid.service.prune_orphaned_artwork_cache()

        # File still present (remove was patched to fail)
        assert tmp_path in sgdb_artwork_cache.files


def _record_icon_writes(steamgrid: SteamGridHarness, tmp_path) -> dict[str, bytes]:
    """Route the service's icon writes into a dict keyed by the path each would have had."""
    written: dict[str, bytes] = {}

    def fake_write_icon(app_id, icon_bytes):
        path = os.path.join(str(tmp_path), f"{app_id}_icon.png")
        written[path] = icon_bytes
        return path

    steamgrid.steam_config.write_shortcut_icon = fake_write_icon  # type: ignore[method-assign]
    return written


def _cache_icon(cache: FakeSgdbArtworkCache, rom_id: int, data: bytes, *, image_id: int = 9) -> None:
    """Put *data* into the artwork cache as *rom_id*'s icon, named by *image_id*."""
    cache.files[os.path.join(cache.cache_dir(), f"{rom_id}_icon_{image_id}.png")] = data


class TestEveryIconIsDownscaled:
    @pytest.mark.asyncio
    async def test_the_game_pages_icon_is_written_as_the_downscale_answers_it(
        self, steamgrid, sgdb_artwork_cache, tmp_path
    ):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()
        steamgrid.service._downscale_icon = lambda data: b"small:" + data
        _cache_icon(sgdb_artwork_cache, 1, b"big")

        result = await steamgrid.service.save_shortcut_icon(7, 1)

        assert written[result["icon_path"]] == b"small:big"
        assert result["sha256"] == hashlib.sha256(b"small:big").hexdigest()

    @pytest.mark.asyncio
    async def test_a_game_page_icon_the_downscale_cannot_read_is_not_written_and_leaves_the_cache(
        self, steamgrid, sgdb_artwork_cache, tmp_path, caplog
    ):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()
        steamgrid.service._downscale_icon = lambda _data: None
        _cache_icon(sgdb_artwork_cache, 1, b"<html>")

        with caplog.at_level(logging.WARNING), pytest.raises(Refused) as refused:
            await steamgrid.service.save_shortcut_icon(7, 1)

        assert refused.value.reason == "icon_write_failed"
        assert written == {}
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == []
        assert "rom_id=1 is not an image Tender reads" in caplog.text

    @pytest.mark.asyncio
    async def test_the_jobs_icon_is_written_as_the_downscale_answers_it(self, steamgrid, sgdb_artwork_cache, tmp_path):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()
        steamgrid.service._downscale_icon = lambda data: b"small:" + data
        _cache_icon(sgdb_artwork_cache, 1, b"big")

        icon = await steamgrid.service.write_job_icon(7, 1, 0)

        assert icon is not None
        assert written[icon.path] == b"small:big"


class TestTheJobsWriteAndAPickOnTheGamePage:
    @pytest.mark.asyncio
    async def test_the_job_writes_while_nothing_was_picked_since_it_read_the_generation(
        self, steamgrid, sgdb_artwork_cache, tmp_path
    ):
        written = _record_icon_writes(steamgrid, tmp_path)
        _cache_icon(sgdb_artwork_cache, 1, b"from the job")
        steamgrid.service._loop = asyncio.get_running_loop()
        generation = steamgrid.service.icon_generation(7)

        icon = await steamgrid.service.write_job_icon(7, 1, generation)

        assert icon is not None
        assert written[icon.path] == b"from the job"

    @pytest.mark.asyncio
    async def test_a_pick_made_since_wins_and_the_job_writes_nothing(self, steamgrid, sgdb_artwork_cache, tmp_path):
        written = _record_icon_writes(steamgrid, tmp_path)
        _cache_icon(sgdb_artwork_cache, 1, b"from the job")
        _cache_icon(sgdb_artwork_cache, 2, b"picked")
        steamgrid.service._loop = asyncio.get_running_loop()
        generation = steamgrid.service.icon_generation(7)

        await steamgrid.service.save_shortcut_icon(7, 2)
        icon = await steamgrid.service.write_job_icon(7, 1, generation)

        assert icon is None
        assert list(written.values()) == [b"picked"]

    @pytest.mark.asyncio
    async def test_a_pick_made_while_the_jobs_write_is_on_disk_waits_for_it_and_lands_last(
        self, steamgrid, sgdb_artwork_cache, tmp_path
    ):
        import threading

        written: dict[str, bytes] = {}
        jobs_write_started = threading.Event()
        jobs_write_may_finish = threading.Event()

        def write_icon(app_id, icon_bytes):
            if icon_bytes == b"from the job":
                jobs_write_started.set()
                jobs_write_may_finish.wait(timeout=5)
            path = os.path.join(str(tmp_path), f"{app_id}_icon.png")
            written[path] = icon_bytes
            return path

        steamgrid.steam_config.write_shortcut_icon = write_icon  # type: ignore[method-assign]
        steamgrid.service._loop = asyncio.get_running_loop()
        _cache_icon(sgdb_artwork_cache, 1, b"from the job")
        _cache_icon(sgdb_artwork_cache, 2, b"picked")
        generation = steamgrid.service.icon_generation(7)

        job = asyncio.ensure_future(steamgrid.service.write_job_icon(7, 1, generation))
        while not jobs_write_started.is_set():
            await asyncio.sleep(0.01)
        pick = asyncio.ensure_future(steamgrid.service.save_shortcut_icon(7, 2))
        await asyncio.sleep(0.05)
        jobs_write_may_finish.set()
        await asyncio.gather(job, pick)

        assert list(written.values()) == [b"picked"]

    @pytest.mark.asyncio
    async def test_each_pick_moves_its_shortcuts_generation_on_and_no_other(
        self, steamgrid, sgdb_artwork_cache, tmp_path
    ):
        _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()
        _cache_icon(sgdb_artwork_cache, 2, b"picked")

        await steamgrid.service.save_shortcut_icon(7, 2)
        await steamgrid.service.save_shortcut_icon(7, 2)

        assert steamgrid.service.icon_generation(7) == 2
        assert steamgrid.service.icon_generation(8) == 0


def _seed_rom_with_ids(uow, rom_id, *, sgdb_id=None):
    with uow:
        uow.roms.save(
            Rom(
                rom_id=rom_id,
                platform_slug="n64",
                name="Game",
                fs_name="Game.z64",
                shortcut_app_id=100 + rom_id,
                last_synced_at="2025-01-01T00:00:00",
                sgdb_id=sgdb_id,
            )
        )


class TestTheJobsIcon:
    """The icon slot through ``do_fetch_slot_artwork`` and ``write_job_icon``: the job's whole icon path."""

    def test_caches_the_first_candidate_under_its_image_id_and_records_nothing(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "icon", (31, 512, 512), (32, 64, 64))

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.ICON) is SlotAnswer.READY
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_icon_31.png"]
        assert fake_steamgrid_db_api.requested_paths[0] == "/icons/game/9999"
        assert uow.artwork_slot_outcomes.save_count == 0

    def test_an_icon_cached_under_its_old_name_is_downloaded_again_and_replaced(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "icon")] = b"old"
        _seed_candidates(fake_steamgrid_db_api, "icon", (31, 512, 512))

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.ICON) is SlotAnswer.READY
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_icon_31.png"]

    def test_no_icon_on_steamgriddb_is_recorded_as_none(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.seed_raw_response("/icons/game/9999", {"success": True, "data": []})

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.ICON) is SlotAnswer.NONE
        assert _outcome(uow, ArtworkSlot.ICON) == ArtworkSlotOutcome.record_none(
            rom_id=42, slot=ArtworkSlot.ICON, sgdb_id=9999, recorded_at=steamgrid.clock.time()
        )

    def test_a_rom_without_a_steamgriddb_id_is_recorded_as_none_without_a_request(
        self, steamgrid, uow, home, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.ICON) is SlotAnswer.NONE
        assert fake_steamgrid_db_api.requested_paths == []
        assert _outcome(uow, ArtworkSlot.ICON) == ArtworkSlotOutcome.record_none(
            rom_id=42, slot=ArtworkSlot.ICON, sgdb_id=None, recorded_at=steamgrid.clock.time()
        )

    @pytest.mark.asyncio
    async def test_the_write_answers_the_image_id_and_the_hash_of_the_downscaled_bytes(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, tmp_path
    ):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()
        steamgrid.service._downscale_icon = lambda data: b"small:" + data
        _cache_icon(sgdb_artwork_cache, 42, b"big", image_id=31)

        icon = await steamgrid.service.write_job_icon(_APP_ID, 42, 0)

        assert icon == WrittenIcon(
            path=os.path.join(str(tmp_path), f"{_APP_ID}_icon.png"),
            image_id=31,
            sha256=hashlib.sha256(b"small:big").hexdigest(),
            sgdb_id=9999,
        )
        assert written[icon.path] == b"small:big"

    @pytest.mark.asyncio
    async def test_an_icon_the_downscale_cannot_read_is_not_written_and_leaves_the_cache(
        self, steamgrid, sgdb_artwork_cache, tmp_path, caplog
    ):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()
        steamgrid.service._downscale_icon = lambda _data: None
        _cache_icon(sgdb_artwork_cache, 1, b"<html>")

        with caplog.at_level(logging.WARNING):
            icon = await steamgrid.service.write_job_icon(7, 1, 0)

        assert icon is None
        assert written == {}
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == []
        assert "rom_id=1 is not an image Tender reads" in caplog.text

    @pytest.mark.asyncio
    async def test_an_icon_the_cache_no_longer_holds_is_not_written(self, steamgrid, tmp_path):
        written = _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()

        assert await steamgrid.service.write_job_icon(7, 1, 0) is None
        assert written == {}

    @pytest.mark.asyncio
    async def test_a_cache_root_that_cannot_be_listed_answers_nothing(self, steamgrid, sgdb_artwork_cache, monkeypatch):
        steamgrid.service._loop = asyncio.get_running_loop()

        def unreadable() -> str:
            raise PermissionError("Permission denied: '/cache/sgdb'")

        monkeypatch.setattr(sgdb_artwork_cache, "cache_dir", unreadable)

        assert await steamgrid.service.write_job_icon(7, 1, 0) is None

    @pytest.mark.asyncio
    async def test_a_cached_icon_that_cannot_be_read_answers_nothing(self, steamgrid, sgdb_artwork_cache, monkeypatch):
        steamgrid.service._loop = asyncio.get_running_loop()
        _cache_icon(sgdb_artwork_cache, 1, b"icon")

        def unreadable(_path: str) -> bytes:
            raise OSError("Input/output error")

        monkeypatch.setattr(sgdb_artwork_cache, "read_bytes", unreadable)

        assert await steamgrid.service.write_job_icon(7, 1, 0) is None

    @pytest.mark.asyncio
    async def test_a_database_that_cannot_be_read_answers_nothing(self, steamgrid, sgdb_artwork_cache, tmp_path):
        _record_icon_writes(steamgrid, tmp_path)
        steamgrid.service._loop = asyncio.get_running_loop()
        _cache_icon(sgdb_artwork_cache, 1, b"icon")

        def locked() -> Any:
            raise sqlite3.OperationalError("database is locked")

        steamgrid.service._uow_factory = locked

        assert await steamgrid.service.write_job_icon(7, 1, 0) is None

    @pytest.mark.asyncio
    async def test_an_icon_that_could_not_be_written_answers_nothing(self, steamgrid, sgdb_artwork_cache):
        steamgrid.service._loop = asyncio.get_running_loop()
        _cache_icon(sgdb_artwork_cache, 1, b"icon")

        def full_disk(_app_id, _icon_bytes):
            raise OSError("No space left on device")

        steamgrid.steam_config.write_shortcut_icon = full_disk  # type: ignore[method-assign]

        assert await steamgrid.service.write_job_icon(7, 1, 0) is None


# ── one artwork slot, filled only while empty ─────────────────────────────

_APP_ID = 3345678901


def _grid_dir(home) -> Any:
    grid = home / ".local" / "share" / "Steam" / "userdata" / "123" / "config" / "grid"
    grid.mkdir(parents=True, exist_ok=True)
    return grid


def _seed_candidates(fake_steamgrid_db_api, asset_type: str, *images: tuple[int, int, int]) -> None:
    """Seed SteamGridDB's candidates for game 9999's *asset_type* as ``(image_id, width, height)``, each with bytes."""
    endpoint = {"grid": "grids", "logo": "logos", "hero": "heroes", "icon": "icons"}[asset_type]
    data = []
    for image_id, width, height in images:
        url = f"https://cdn.example/{image_id}.png"
        data.append({"id": image_id, "url": url, "width": width, "height": height})
        fake_steamgrid_db_api.seed_image_bytes(url, f"image {image_id}".encode())
    fake_steamgrid_db_api.seed_raw_response(f"/{endpoint}/game/9999", {"success": True, "data": data})


@pytest.fixture
def slot_ready(steamgrid, uow, home):
    """A bound ROM 42 with SteamGridDB id 9999, a key, and an empty grid directory."""
    steamgrid.settings["steamgriddb_api_key"] = "some-key"
    _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)
    return _grid_dir(home)


def _outcome(uow, slot: ArtworkSlot):
    with uow:
        return uow.artwork_slot_outcomes.get(42, slot)


def _handover(uow, slot: ArtworkSlot):
    with uow:
        return uow.artwork_slot_handovers.get(42, slot)


class TestAFilledSlotIsLeftAlone:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("slot", "name"),
        [("grid", f"{_APP_ID}.png"), ("logo", f"{_APP_ID}_logo.jpg"), ("hero", f"{_APP_ID}_hero.webp")],
    )
    async def test_a_file_in_the_slot_is_skipped_without_asking_steamgriddb(
        self, steamgrid, slot_ready, fake_steamgrid_db_api, slot, name
    ):
        (slot_ready / name).write_bytes(b"set by hand")

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, slot)

        assert result == {"status": "skip"}
        assert fake_steamgrid_db_api.requested_paths == []
        assert (slot_ready / name).read_bytes() == b"set by hand"

    @pytest.mark.asyncio
    async def test_the_logos_position_file_does_not_fill_the_logo(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        (slot_ready / f"{_APP_ID}_logo.json").write_text("{}")
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert result["status"] == "image"

    @pytest.mark.asyncio
    async def test_the_cover_does_not_fill_the_wide_grid(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        (slot_ready / f"{_APP_ID}p.png").write_bytes(b"cover")
        _seed_candidates(fake_steamgrid_db_api, "grid", (5, 920, 430))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result["status"] == "image"

    @pytest.mark.asyncio
    async def test_without_a_grid_directory_nothing_is_judged(self, steamgrid, uow, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)

        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert refused.value.reason == "no_grid_dir"
        assert fake_steamgrid_db_api.requested_paths == []


class TestAnEmptySlotIsServed:
    @pytest.mark.asyncio
    async def test_the_answer_carries_what_the_report_needs(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        _seed_candidates(fake_steamgrid_db_api, "grid", (11, 460, 215), (12, 920, 430))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        token = result.pop("prune_lease_token")
        assert result == {
            "status": "image",
            "base64": base64.b64encode(b"image 12").decode("ascii"),
            "image_id": 12,
            "sha256": hashlib.sha256(b"image 12").hexdigest(),
            "sgdb_id": 9999,
        }
        assert steamgrid.prune_conflicts.conflicting_operations == 1
        await steamgrid.prune_conflicts.release_lease(token)

    @pytest.mark.asyncio
    async def test_only_static_images_are_asked_for(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))

        await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert fake_steamgrid_db_api.requested_paths == ["/logos/game/9999?types=static"]

    @pytest.mark.asyncio
    async def test_the_cached_image_is_named_by_its_id(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (300, 300, 100), (301, 1000, 400))

        await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_logo_301.png"]

    @pytest.mark.asyncio
    async def test_a_cached_image_is_served_without_asking_steamgriddb(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        cached = os.path.join(sgdb_artwork_cache.cache_dir(), "42_logo_301.png")
        sgdb_artwork_cache.files[cached] = b"cached logo"

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert (result["image_id"], base64.b64decode(result["base64"])) == (301, b"cached logo")
        assert fake_steamgrid_db_api.requested_paths == []
        await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_cached_wide_grid_named_without_an_id_is_downloaded_again_and_replaced(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        legacy = os.path.join(sgdb_artwork_cache.cache_dir(), "42_grid.png")
        sgdb_artwork_cache.files[legacy] = b"picked by the old rule"
        _seed_candidates(fake_steamgrid_db_api, "grid", (11, 460, 215), (12, 920, 430))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result["image_id"] == 12
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_grid_12.png"]
        await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_the_hero_is_served_with_its_image_id(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_candidates(fake_steamgrid_db_api, "hero", (7, 1920, 620))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "hero")

        assert (result["status"], result["image_id"]) == ("image", 7)
        await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])
        assert uow.artwork_slot_outcomes.save_count == 0

    @pytest.mark.asyncio
    async def test_without_a_key_nothing_is_asked(self, steamgrid, uow, home, fake_steamgrid_db_api):
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)
        _grid_dir(home)

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result == {"status": "no_api_key"}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_an_unknown_slot_is_refused(self, steamgrid, slot_ready):
        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "banner")

        assert refused.value.reason == "invalid_payload"
        assert steamgrid.prune_conflicts.conflicting_operations == 0

    @pytest.mark.asyncio
    async def test_a_running_cleanup_refuses_it(self, steamgrid, slot_ready):
        steamgrid.prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")


def _write_shortcut_icon_paths(grid, icons: dict[int, str]) -> None:
    """Write ``shortcuts.vdf`` beside *grid*, each app id pointing at its icon path, as Steam stores it."""
    shortcuts = {
        str(index): {"appid": app_id - 2**32 if app_id >= 2**31 else app_id, "AppName": "Game", "icon": icon}
        for index, (app_id, icon) in enumerate(icons.items())
    }
    (grid.parent / "shortcuts.vdf").write_bytes(vdf.binary_dumps({"shortcuts": shortcuts}))


class TestTheGamePagesIconSlot:
    """The icon through ``get_sgdb_slot_artwork``: a first visit by the job's own rule, a replacing apply past it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("icon", ["/run/media/deck/sd/icons/mine.png", "/home/deck/Pictures/{app_id}_icon.png"])
    async def test_an_icon_somebody_set_is_kept_without_asking_steamgriddb(
        self, steamgrid, slot_ready, fake_steamgrid_db_api, icon
    ):
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: icon.format(app_id=_APP_ID)})
        _seed_candidates(fake_steamgrid_db_api, "icon", (31, 512, 512))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert result == {"status": "skip"}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_an_icon_in_the_grid_directory_whose_file_is_there_is_kept(
        self, steamgrid, slot_ready, fake_steamgrid_db_api
    ):
        (slot_ready / f"{_APP_ID}_icon.png").write_bytes(b"picked")
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: str(slot_ready / f"{_APP_ID}_icon.png")})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert result == {"status": "skip"}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["tender-icon-placeholder.png", "tender-icon.png", f"{_APP_ID}_icon.png", None])
    async def test_the_placeholder_the_logo_icon_a_file_that_is_gone_and_no_icon_are_replaced(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api, name
    ):
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: str(slot_ready / name) if name else ""})
        _seed_candidates(fake_steamgrid_db_api, "icon", (31, 512, 512))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        token = result.pop("prune_lease_token")
        assert result == {"status": "ready"}
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_icon_31.png"]
        assert steamgrid.prune_conflicts.conflicting_operations == 1
        await steamgrid.prune_conflicts.release_lease(token)

    @pytest.mark.asyncio
    async def test_a_recorded_none_rules_the_icon_out(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: str(slot_ready / "tender-icon.png")})
        with uow:
            uow.artwork_slot_outcomes.save(
                ArtworkSlotOutcome.record_none(
                    rom_id=42, slot=ArtworkSlot.ICON, sgdb_id=9999, recorded_at=steamgrid.clock.time()
                )
            )

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert result == {"status": "skip"}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_no_icon_on_steamgriddb_is_recorded_as_none(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: ""})
        fake_steamgrid_db_api.seed_raw_response("/icons/game/9999", {"success": True, "data": []})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert result == {"status": "none"}
        assert _outcome(uow, ArtworkSlot.ICON) == ArtworkSlotOutcome.record_none(
            rom_id=42, slot=ArtworkSlot.ICON, sgdb_id=9999, recorded_at=steamgrid.clock.time()
        )

    @pytest.mark.asyncio
    async def test_a_shortcut_the_file_does_not_hold_yet_is_not_judged(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _write_shortcut_icon_paths(slot_ready, {7: ""})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert result == {"status": "failed"}
        assert fake_steamgrid_db_api.requested_paths == []
        assert uow.artwork_slot_outcomes.save_count == 0

    @pytest.mark.asyncio
    async def test_a_file_that_cannot_be_read_is_not_judged(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        (slot_ready.parent / "shortcuts.vdf").write_bytes(b"\x00not a vdf")

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert result == {"status": "failed"}
        assert fake_steamgrid_db_api.requested_paths == []
        assert uow.artwork_slot_outcomes.save_count == 0

    @pytest.mark.asyncio
    async def test_without_a_grid_directory_the_icon_is_not_judged(self, steamgrid, uow, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)

        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert refused.value.reason == "no_grid_dir"
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_a_replacing_fetch_serves_an_icon_somebody_set_and_one_a_record_rules_out(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: "/home/deck/Pictures/mine.png"})
        with uow:
            uow.artwork_slot_outcomes.save(
                ArtworkSlotOutcome.record_refused(
                    rom_id=42, slot=ArtworkSlot.ICON, sgdb_id=9999, recorded_at=steamgrid.clock.time()
                )
            )
        _cache_icon(sgdb_artwork_cache, 42, b"refreshed", image_id=31)

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon", True)

        await steamgrid.prune_conflicts.release_lease(result.pop("prune_lease_token"))
        assert result == {"status": "ready"}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_a_replacing_fetch_does_not_read_shortcuts_vdf(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.steam_config.read_shortcut_icons = _forbidden_vdf  # type: ignore[method-assign]
        _cache_icon(sgdb_artwork_cache, 42, b"refreshed", image_id=31)

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon", True)

        await steamgrid.prune_conflicts.release_lease(result.pop("prune_lease_token"))
        assert result == {"status": "ready"}

    @pytest.mark.asyncio
    async def test_a_replacing_fetch_records_none_where_steamgriddb_has_no_icon(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.seed_raw_response("/icons/game/9999", {"success": True, "data": []})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon", True)

        assert result == {"status": "none"}
        assert _outcome(uow, ArtworkSlot.ICON).outcome is SlotOutcome.NONE

    @pytest.mark.asyncio
    async def test_a_replacing_fetch_serves_a_filled_wide_grid_too(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        (slot_ready / f"{_APP_ID}.png").write_bytes(b"set by hand")
        _seed_candidates(fake_steamgrid_db_api, "grid", (12, 920, 430))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid", True)

        await steamgrid.prune_conflicts.release_lease(result.pop("prune_lease_token"))
        assert (result["status"], result["image_id"]) == ("image", 12)


def _seed_chosen_game(uow, rom_id: int, *, romm_sgdb_id: int | None = None, chosen: int = 8888) -> None:
    """A bound ROM *rom_id* the user chose SteamGridDB game *chosen* for, beside RomM's *romm_sgdb_id*."""
    _seed_rom(uow, rom_id, app_id=_APP_ID, sgdb_id=romm_sgdb_id)
    with uow:
        uow.roms.set_chosen_sgdb_id(rom_id, chosen)


class TestAChosenGameIsTheRomsSteamGridDbGame:
    """Without RomM's id, every artwork reader takes the game the user chose; with it, RomM's."""

    @pytest.mark.asyncio
    async def test_the_game_pages_fetch_downloads_from_the_chosen_game(
        self, steamgrid, uow, home, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_chosen_game(uow, 42)
        _grid_dir(home)
        fake_steamgrid_db_api.seed_raw_response(
            "/logos/game/8888?types=static",
            {"success": True, "data": [{"id": 5, "url": "https://cdn.example/5.png", "width": 800}]},
        )
        fake_steamgrid_db_api.seed_image_bytes("https://cdn.example/5.png", b"logo 5")

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        await steamgrid.prune_conflicts.release_lease(answer.pop("prune_lease_token"))
        assert (answer["status"], answer["image_id"], answer["sgdb_id"]) == ("image", 5, 8888)

    @pytest.mark.asyncio
    async def test_romms_id_wins_where_both_exist(self, steamgrid, uow, home, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_chosen_game(uow, 42, romm_sgdb_id=9999)
        _grid_dir(home)
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        await steamgrid.prune_conflicts.release_lease(answer.pop("prune_lease_token"))
        assert answer["sgdb_id"] == 9999
        assert [path.split("?")[0] for path in fake_steamgrid_db_api.requested_paths] == ["/logos/game/9999"]

    def test_the_job_fetches_from_the_chosen_game_and_records_under_it(self, steamgrid, uow, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_chosen_game(uow, 42)
        fake_steamgrid_db_api.seed_raw_response("/icons/game/8888", {"success": True, "data": []})

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.ICON) is SlotAnswer.NONE
        assert fake_steamgrid_db_api.requested_paths == ["/icons/game/8888"]
        assert _outcome(uow, ArtworkSlot.ICON) == ArtworkSlotOutcome.record_none(
            rom_id=42, slot=ArtworkSlot.ICON, sgdb_id=8888, recorded_at=steamgrid.clock.time()
        )

    @pytest.mark.asyncio
    async def test_a_refused_pick_stores_no_choice(self, steamgrid, uow, sgdb_artwork_cache, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID)
        fake_steamgrid_db_api.request_side_effect = SgdbRateLimitedError("Too Many Requests")

        with pytest.raises(Refused):
            await steamgrid.service.apply_sgdb_game_id(42, 8888)

        with uow:
            assert uow.roms.get(42).chosen_sgdb_id is None


class TestAPickWithoutAStoredId:
    """A ROM with no SteamGridDB game: the per-slot fetch serves what the cache holds.

    Such a cache is what a pick an older release made left, since a pick now stores its game; the tests that pick
    through ``apply_sgdb_game_id`` therefore exercise a stored game, and the rest seed the cache with none stored.
    """

    @pytest.mark.asyncio
    async def test_an_image_cached_without_a_game_is_served_without_a_request(
        self, steamgrid, uow, home, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)
        _grid_dir(home)
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "grid", 803)] = b"picked grid"

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        await steamgrid.prune_conflicts.release_lease(answer.pop("prune_lease_token"))
        assert (answer["status"], answer["image_id"], answer["sgdb_id"]) == ("image", 803, None)
        assert base64.b64decode(answer["base64"]) == b"picked grid"
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_the_picked_icon_is_served_to_the_replacing_fetch_and_written(
        self, steamgrid, uow, home, sgdb_artwork_cache, fake_steamgrid_db_api, tmp_path
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)
        _grid_dir(home)
        written = _record_icon_writes(steamgrid, tmp_path)
        _seed_all_artwork(fake_steamgrid_db_api, 7777)

        await steamgrid.service.apply_sgdb_game_id(42, 7777)
        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon", True)
        await steamgrid.prune_conflicts.release_lease(answer.pop("prune_lease_token"))
        saved = await steamgrid.service.save_shortcut_icon(_APP_ID, 42)

        assert answer == {"status": "ready"}
        assert written[saved["icon_path"]] == b"icon bytes"
        assert saved["image_id"] == _PICKED_IMAGE_IDS["icon"]
        assert _outcome(uow, ArtworkSlot.ICON) is None

    @pytest.mark.asyncio
    async def test_a_picked_wide_grid_is_served_from_the_cache(
        self, steamgrid, uow, home, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)
        _grid_dir(home)
        _seed_all_artwork(fake_steamgrid_db_api, 7777)
        await steamgrid.service.apply_sgdb_game_id(42, 7777)
        requested = len(fake_steamgrid_db_api.requested_paths)

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        await steamgrid.prune_conflicts.release_lease(answer.pop("prune_lease_token"))
        assert (answer["status"], answer["image_id"], answer["sgdb_id"]) == ("image", _PICKED_IMAGE_IDS["grid"], 7777)
        assert base64.b64decode(answer["base64"]) == b"grid bytes"
        assert len(fake_steamgrid_db_api.requested_paths) == requested

    @pytest.mark.asyncio
    async def test_a_first_visit_still_keeps_an_icon_somebody_set(
        self, steamgrid, uow, home, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)
        grid = _grid_dir(home)
        _write_shortcut_icon_paths(grid, {_APP_ID: "/home/deck/Pictures/mine.png"})
        _cache_icon(sgdb_artwork_cache, 42, b"picked", image_id=804)

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon")

        assert answer == {"status": "skip"}

    @pytest.mark.asyncio
    async def test_a_cache_that_cannot_be_listed_answers_failed_and_records_nothing(
        self, steamgrid, uow, home, sgdb_artwork_cache, monkeypatch
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)
        _grid_dir(home)

        def unreadable() -> str:
            raise PermissionError("Permission denied: '/cache/sgdb'")

        monkeypatch.setattr(sgdb_artwork_cache, "cache_dir", unreadable)

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "icon", True)

        assert answer == {"status": "failed"}
        assert uow.artwork_slot_outcomes.save_count == 0


class TestASlotSteamGridDbHasNothingFor:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("slot", [ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO, ArtworkSlot.HERO])
    async def test_no_candidate_is_recorded_as_none(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, slot):
        endpoint = {ArtworkSlot.WIDE_GRID: "grids", ArtworkSlot.LOGO: "logos", ArtworkSlot.HERO: "heroes"}[slot]
        fake_steamgrid_db_api.seed_raw_response(f"/{endpoint}/game/9999", {"success": True, "data": []})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, slot.value)

        assert result == {"status": "none"}
        assert _outcome(uow, slot) == ArtworkSlotOutcome.record_none(
            rom_id=42, slot=slot, sgdb_id=9999, recorded_at=steamgrid.clock.time()
        )

    @pytest.mark.asyncio
    async def test_a_game_steamgriddb_does_not_know_is_recorded_as_none(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=404, message="Game not found.")

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert result == {"status": "none"}
        assert _outcome(uow, ArtworkSlot.LOGO).outcome is SlotOutcome.NONE

    @pytest.mark.asyncio
    async def test_it_is_not_asked_again_for_a_week(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", {"success": True, "data": []})
        await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")
        fake_steamgrid_db_api.requested_paths.clear()

        steamgrid.clock.advance(RULED_OUT_SECONDS - 1)
        within = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")
        steamgrid.clock.advance(1)
        after = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert (within, after) == ({"status": "skip"}, {"status": "none"})
        assert fake_steamgrid_db_api.requested_paths == ["/logos/game/9999?types=static"]

    @pytest.mark.asyncio
    async def test_it_is_asked_again_at_once_when_the_roms_steamgriddb_id_changes(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", {"success": True, "data": []})
        await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")
        with uow:
            rom = uow.roms.get(42)
            rom.assign_sgdb_id(8888)
            uow.roms.save(rom)
        fake_steamgrid_db_api.seed_raw_response("/logos/game/8888", {"success": True, "data": []})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert result == {"status": "none"}
        assert "/logos/game/8888?types=static" in fake_steamgrid_db_api.requested_paths
        assert _outcome(uow, ArtworkSlot.LOGO).sgdb_id == 8888

    @pytest.mark.asyncio
    async def test_a_rom_without_a_steamgriddb_id_is_recorded_as_none_until_it_has_one(
        self, steamgrid, uow, home, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)
        _grid_dir(home)

        first = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")
        again = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")
        with uow:
            rom = uow.roms.get(42)
            rom.assign_sgdb_id(9999)
            uow.roms.save(rom)
        _seed_candidates(fake_steamgrid_db_api, "grid", (12, 920, 430))
        once_it_has_one = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert (first, again, once_it_has_one["status"]) == ({"status": "none"}, {"status": "skip"}, "image")
        await steamgrid.prune_conflicts.release_lease(once_it_has_one["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_rom_that_is_not_synced_records_nothing(self, steamgrid, uow, home, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.library._pending_sync[42] = {"name": "Zelda", "sgdb_id": 9999}
        _grid_dir(home)
        fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", {"success": True, "data": []})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert result == {"status": "none"}
        assert _outcome(uow, ArtworkSlot.LOGO) is None


class TestAnAnswerThatIsNotNoneIsNotRecorded:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (SgdbApiError(status_code=500, message="boom"), "failed"),
            (SgdbApiError(status_code=None, message="timeout"), "failed"),
            (SgdbRateLimitedError("429"), "rate_limited"),
        ],
        ids=["server-error", "unreachable", "rate-limited"],
    )
    async def test_a_failed_request(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, error, status):
        fake_steamgrid_db_api.request_side_effect = error

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result == {"status": status}
        assert _outcome(uow, ArtworkSlot.WIDE_GRID) is None

    @pytest.mark.asyncio
    async def test_an_answer_that_says_it_failed(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.seed_raw_response("/grids/game/9999", {"success": False})

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result == {"status": "failed"}
        assert _outcome(uow, ArtworkSlot.WIDE_GRID) is None

    @pytest.mark.asyncio
    async def test_candidates_without_an_integer_id_are_skipped_and_none_left_is_no_answer(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.seed_raw_response(
            "/logos/game/9999",
            {
                "success": True,
                "data": [{"url": "https://cdn.example/a.png"}, {"id": "7", "url": "https://cdn.example/b.png"}],
            },
        )

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert result == {"status": "failed"}
        assert fake_steamgrid_db_api.downloaded == []
        assert _outcome(uow, ArtworkSlot.LOGO) is None

    @pytest.mark.asyncio
    async def test_a_candidate_without_an_integer_id_gives_way_to_one_with(
        self, steamgrid, slot_ready, fake_steamgrid_db_api
    ):
        url = "https://cdn.example/with-id.png"
        fake_steamgrid_db_api.seed_raw_response(
            "/logos/game/9999",
            {"success": True, "data": [{"url": "https://cdn.example/no-id.png", "width": 800}, {"id": 9, "url": url}]},
        )
        fake_steamgrid_db_api.seed_image_bytes(url, b"logo with an id")

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert (result["image_id"], base64.b64decode(result["base64"])) == (9, b"logo with an id")
        await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_download_that_fails(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.seed_raw_response(
            "/grids/game/9999", {"success": True, "data": [{"id": 1, "url": "https://cdn.example/x.png"}]}
        )
        fake_steamgrid_db_api.download_image_return = False

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result == {"status": "failed"}
        assert _outcome(uow, ArtworkSlot.WIDE_GRID) is None

    @pytest.mark.asyncio
    async def test_a_cached_image_that_cannot_be_read(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, monkeypatch
    ):
        sgdb_artwork_cache.files[os.path.join(sgdb_artwork_cache.cache_dir(), "42_grid_12.png")] = b"x"

        def _unreadable(path):
            raise OSError("gone")

        monkeypatch.setattr(sgdb_artwork_cache, "read_bytes", _unreadable)

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result == {"status": "failed"}
        assert steamgrid.prune_conflicts.conflicting_operations == 0


class TestReportSlotArtwork:
    @pytest.mark.asyncio
    async def test_a_set_image_is_recorded_with_its_id_and_hash(self, steamgrid, uow, slot_ready):
        result = await steamgrid.service.report_sgdb_slot_artwork(42, "grid", "set", 9999, 12, "ab" * 32)

        assert result == {"success": True}
        assert _outcome(uow, ArtworkSlot.WIDE_GRID) == ArtworkSlotOutcome.record_set(
            rom_id=42,
            slot=ArtworkSlot.WIDE_GRID,
            sgdb_id=9999,
            image_id=12,
            image_sha256="ab" * 32,
            recorded_at=steamgrid.clock.time(),
        )

    @pytest.mark.asyncio
    async def test_the_hero_is_recorded_set_with_its_id_and_hash(self, steamgrid, uow, slot_ready):
        result = await steamgrid.service.report_sgdb_slot_artwork(42, "hero", "set", 9999, 7, "ef" * 32)

        assert result == {"success": True}
        assert _outcome(uow, ArtworkSlot.HERO) == ArtworkSlotOutcome.record_set(
            rom_id=42,
            slot=ArtworkSlot.HERO,
            sgdb_id=9999,
            image_id=7,
            image_sha256="ef" * 32,
            recorded_at=steamgrid.clock.time(),
        )

    @pytest.mark.asyncio
    async def test_the_jobs_icon_is_recorded_set_with_its_id_and_hash(self, steamgrid, uow, slot_ready):
        result = await steamgrid.service.report_sgdb_slot_artwork(42, "icon", "set", 9999, 31, "cd" * 32)

        assert result == {"success": True}
        assert _outcome(uow, ArtworkSlot.ICON) == ArtworkSlotOutcome.record_set(
            rom_id=42,
            slot=ArtworkSlot.ICON,
            sgdb_id=9999,
            image_id=31,
            image_sha256="cd" * 32,
            recorded_at=steamgrid.clock.time(),
        )

    @pytest.mark.asyncio
    async def test_an_icon_steam_refused_is_recorded_refused(self, steamgrid, uow, slot_ready):
        await steamgrid.service.report_sgdb_slot_artwork(42, "icon", "refused", 9999, 31, "cd" * 32)

        assert _outcome(uow, ArtworkSlot.ICON) == ArtworkSlotOutcome.record_refused(
            rom_id=42, slot=ArtworkSlot.ICON, sgdb_id=9999, recorded_at=steamgrid.clock.time()
        )

    @pytest.mark.asyncio
    async def test_a_refused_image_rules_the_slot_out(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 5, "ab" * 32)

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert _outcome(uow, ArtworkSlot.LOGO).outcome is SlotOutcome.REFUSED
        assert result == {"status": "skip"}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_a_set_slot_whose_file_is_gone_is_served_again(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        await steamgrid.service.report_sgdb_slot_artwork(42, "grid", "set", 9999, 12, "ab" * 32)
        _seed_candidates(fake_steamgrid_db_api, "grid", (12, 920, 430))

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert result["status"] == "image"
        await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "args",
        [
            ("grid", "lost", 9999, 12, "ab"),
            ("grid", "set", 9999, None, "ab"),
            ("grid", "set", 9999, 12, ""),
        ],
        ids=["unknown-outcome", "set-without-id", "set-without-hash"],
    )
    async def test_a_report_that_cannot_be_recorded_is_refused(self, steamgrid, uow, slot_ready, args):
        with pytest.raises(Refused) as refused:
            await steamgrid.service.report_sgdb_slot_artwork(42, *args)

        assert refused.value.reason == "invalid_payload"
        assert uow.artwork_slot_outcomes.save_count == 0

    @pytest.mark.asyncio
    async def test_a_cover_set_from_steamgriddb_records_its_image_and_hash(self, steamgrid, uow, slot_ready):
        answer = await steamgrid.service.report_sgdb_slot_artwork(42, "cover", "set", 9999, 12, "ab" * 32)

        recorded = _outcome(uow, ArtworkSlot.COVER)
        assert answer == {"success": True}
        assert (recorded.outcome, recorded.image_id, recorded.image_sha256) == (SlotOutcome.SET, 12, "ab" * 32)

    @pytest.mark.asyncio
    async def test_a_cover_steam_refused_records_nothing(self, steamgrid, uow, slot_ready):
        answer = await steamgrid.service.report_sgdb_slot_artwork(42, "cover", "refused", 9999, None, None)

        assert answer == {"success": True}
        assert uow.artwork_slot_outcomes.save_count == 0

    @pytest.mark.asyncio
    async def test_a_report_for_a_rom_that_is_not_synced_records_nothing(self, steamgrid, uow):
        result = await steamgrid.service.report_sgdb_slot_artwork(42, "grid", "set", 9999, 12, "ab")

        assert result == {"success": True}
        assert _outcome(uow, ArtworkSlot.WIDE_GRID) is None

    @pytest.mark.asyncio
    async def test_a_running_cleanup_refuses_it(self, steamgrid, uow, slot_ready):
        steamgrid.prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await steamgrid.service.report_sgdb_slot_artwork(42, "grid", "set", 9999, 12, "ab")

        assert _outcome(uow, ArtworkSlot.WIDE_GRID) is None


class TestRefreshArtworkForgetsTheGamesOutcomes:
    @pytest.mark.asyncio
    async def test_the_resolution_deletes_that_games_rows_only(self, steamgrid, uow, slot_ready):
        _seed_rom(uow, 43, app_id=_APP_ID + 1, sgdb_id=9999)
        with uow:
            for rom_id in (42, 43):
                uow.artwork_slot_outcomes.save(
                    ArtworkSlotOutcome.record_none(rom_id=rom_id, slot=ArtworkSlot.LOGO, sgdb_id=9999, recorded_at=1.0)
                )

        await steamgrid.service.get_sgdb_resolution(42)

        with uow:
            assert uow.artwork_slot_outcomes.get(42, ArtworkSlot.LOGO) is None
            assert uow.artwork_slot_outcomes.get(43, ArtworkSlot.LOGO) is not None

    @pytest.mark.asyncio
    async def test_without_a_key_too(self, steamgrid, uow, home):
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)
        with uow:
            uow.artwork_slot_outcomes.save(
                ArtworkSlotOutcome.record_none(rom_id=42, slot=ArtworkSlot.LOGO, sgdb_id=9999, recorded_at=1.0)
            )

        assert await steamgrid.service.get_sgdb_resolution(42) == {"decision": "no_api_key"}
        assert _outcome(uow, ArtworkSlot.LOGO) is None


class TestTheCacheUnderItsNewNames:
    def test_the_orphan_prune_removes_an_id_named_file_of_an_unbound_rom(self, steamgrid, uow, sgdb_artwork_cache):
        _seed_rom(uow, 42, app_id=_APP_ID)
        art_dir = sgdb_artwork_cache.cache_dir()
        for name in ("42_grid_12.png", "42_logo_5.png", "7_grid_12.png", "7_logo_5.png"):
            sgdb_artwork_cache.files[os.path.join(art_dir, name)] = b"x"

        steamgrid.service.prune_orphaned_artwork_cache()

        assert sorted(sgdb_artwork_cache.listdir(art_dir)) == ["42_grid_12.png", "42_logo_5.png"]

    @pytest.mark.asyncio
    async def test_a_new_pick_clears_the_previous_picks_id_named_files(
        self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        art_dir = sgdb_artwork_cache.cache_dir()
        for name in ("42_grid_1.png", "42_logo_2.png"):
            sgdb_artwork_cache.files[os.path.join(art_dir, name)] = b"previous pick"

        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert sgdb_artwork_cache.listdir(art_dir) == []


class TestTheJobsSlotFetch:
    """``do_fetch_slot_artwork``: the shortcut artwork job's half of a wide grid or logo, before the frontend's."""

    def test_caches_the_chosen_image_and_records_nothing(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "grid", (11, 460, 215), (12, 920, 430))

        answer = steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.WIDE_GRID)

        assert answer is SlotAnswer.READY
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_grid_12.png"]
        assert uow.artwork_slot_outcomes.save_count == 0

    def test_a_cached_image_is_ready_without_asking_steamgriddb(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        sgdb_artwork_cache.files[os.path.join(sgdb_artwork_cache.cache_dir(), "42_logo_301.png")] = b"cached"

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.READY
        assert fake_steamgrid_db_api.requested_paths == []

    def test_no_candidate_is_recorded_as_none(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", {"success": True, "data": []})

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.NONE
        assert _outcome(uow, ArtworkSlot.LOGO) == ArtworkSlotOutcome.record_none(
            rom_id=42, slot=ArtworkSlot.LOGO, sgdb_id=9999, recorded_at=steamgrid.clock.time()
        )

    def test_a_game_steamgriddb_does_not_know_is_recorded_as_none(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=404, message="Game not found.")

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.WIDE_GRID) is SlotAnswer.NONE
        recorded = _outcome(uow, ArtworkSlot.WIDE_GRID)
        assert recorded is not None
        assert recorded.outcome is SlotOutcome.NONE

    def test_a_rom_without_a_steamgriddb_id_is_recorded_as_none_without_a_request(
        self, steamgrid, uow, home, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.NONE
        assert fake_steamgrid_db_api.requested_paths == []
        assert _outcome(uow, ArtworkSlot.LOGO) == ArtworkSlotOutcome.record_none(
            rom_id=42, slot=ArtworkSlot.LOGO, sgdb_id=None, recorded_at=steamgrid.clock.time()
        )

    def test_a_429_is_rate_limited_and_records_nothing(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.request_side_effect = SgdbRateLimitedError("Too Many Requests")

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.RATE_LIMITED
        assert uow.artwork_slot_outcomes.save_count == 0

    def test_a_429_from_the_image_cdn_is_rate_limited(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))
        fake_steamgrid_db_api.download_image_side_effect = SgdbRateLimitedError("Too Many Requests")

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.RATE_LIMITED

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refused_key_is_key_refused(self, steamgrid, slot_ready, fake_steamgrid_db_api, status):
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=status, message="Unauthorized")

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.WIDE_GRID) is SlotAnswer.KEY_REFUSED

    def test_a_server_error_fails_and_records_nothing(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=500, message="Server Error")

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.WIDE_GRID) is SlotAnswer.FAILED
        assert uow.artwork_slot_outcomes.save_count == 0

    def test_without_a_key_nothing_is_asked(self, steamgrid, uow, fake_steamgrid_db_api):
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.FAILED
        assert fake_steamgrid_db_api.requested_paths == []


class TestTheJobsSlotFetchWithAKeyNoHeaderCanCarry:
    """The shortcut artwork job's fetch over the real ``SteamGridDbAdapter``, with a stored key that cannot be sent."""

    @pytest.mark.parametrize(
        "key",
        [
            pytest.param("fake-sgdb-key-6b4\r\nX-Injected: 1", id="line-break"),
            pytest.param("fake-sgdb-key-6b4\u2019", id="past-latin-1"),
        ],
    )
    @pytest.mark.parametrize("slot", [ArtworkSlot.ICON, ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO])
    def test_it_is_a_refused_key_and_no_part_of_it_is_logged(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, fake_romm_api, logger, caplog, key, slot
    ):
        caplog.set_level(logging.DEBUG)
        steamgrid.settings["steamgriddb_api_key"] = key
        service = SteamGridService(
            config=SteamGridServiceConfig(
                sgdb_api=SteamGridDbAdapter(settings=steamgrid.settings, logger=logger, user_agent="romm-tender/9.9.9"),
                romm_api=fake_romm_api,
                steam_config=steamgrid.steam_config,
                sgdb_artwork_cache=sgdb_artwork_cache,
                settings=steamgrid.settings,
                loop=running_loop(),
                logger=logger,
                settings_persister=FakeSettingsPersister(),
                get_pending_sync=dict,
                log_debug=SettingsAwareDebugLogger(
                    settings={**steamgrid.settings, "log_level": "debug"}, logger=logger
                ),
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                conflict_rules=_make_conflict_rules(),
                downscale_icon=lambda data: data,
                clock=steamgrid.clock,
                cover=FakeCoverSlotReader(),
            )
        )

        with (
            patch("http.client.HTTPSConnection.connect", side_effect=OSError("no network in this test")) as connect,
        ):
            answer = service.do_fetch_slot_artwork(42, slot)

        assert answer is SlotAnswer.KEY_REFUSED
        connect.assert_not_called()
        assert "fake-sgdb-key" not in caplog.text
        assert "Bearer" not in caplog.text
        assert uow.artwork_slot_outcomes.save_count == 0


class TestTheReplacingPathCarriesWhatTheReportNeeds:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("asset_type_num", "asset_type"), [(2, "logo"), (3, "grid")])
    async def test_a_wide_grid_or_logo_carries_its_image_id_hash_and_steamgriddb_id(
        self, steamgrid, slot_ready, fake_steamgrid_db_api, asset_type_num, asset_type
    ):
        _seed_candidates(fake_steamgrid_db_api, asset_type, (12, 920, 430))

        result = await steamgrid.service.get_sgdb_artwork_base64(42, asset_type_num)

        await steamgrid.prune_conflicts.release_lease(result.pop("prune_lease_token"))
        assert result == {
            "base64": base64.b64encode(b"image 12").decode("ascii"),
            "no_api_key": False,
            "image_id": 12,
            "sha256": hashlib.sha256(b"image 12").hexdigest(),
            "sgdb_id": 9999,
        }

    @pytest.mark.asyncio
    async def test_a_cached_one_carries_it_too(self, steamgrid, slot_ready, sgdb_artwork_cache):
        sgdb_artwork_cache.files[os.path.join(sgdb_artwork_cache.cache_dir(), "42_logo_301.png")] = b"cached"

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 2)

        await steamgrid.prune_conflicts.release_lease(result.pop("prune_lease_token"))
        assert (result["image_id"], result["sha256"], result["sgdb_id"]) == (
            301,
            hashlib.sha256(b"cached").hexdigest(),
            9999,
        )

    @pytest.mark.asyncio
    async def test_the_hero_carries_it_too(self, steamgrid, slot_ready, sgdb_artwork_cache):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "hero", 7)] = b"hero"

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        await steamgrid.prune_conflicts.release_lease(result.pop("prune_lease_token"))
        assert (result["image_id"], result["sha256"], result["sgdb_id"]) == (
            7,
            hashlib.sha256(b"hero").hexdigest(),
            9999,
        )

    @pytest.mark.asyncio
    async def test_a_hero_cached_under_its_old_name_is_downloaded_again(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "hero")] = b"old hero"
        _seed_candidates(fake_steamgrid_db_api, "hero", (7, 1920, 620))

        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        await steamgrid.prune_conflicts.release_lease(result.pop("prune_lease_token"))
        assert (base64.b64decode(result["base64"]), result["image_id"]) == (b"image 7", 7)
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_hero_7.png"]


class TestTheCacheIsListedOnce:
    """A wide grid's or logo's cache lookup does not list the whole artwork cache each time."""

    @pytest.mark.asyncio
    async def test_the_jobs_lookups_and_the_frontends_fetches_list_it_once(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api, monkeypatch
    ):
        _seed_candidates(fake_steamgrid_db_api, "grid", (12, 920, 430))
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))
        _seed_rom(uow, 43, app_id=_APP_ID + 1, sgdb_id=9999)
        art_dir = sgdb_artwork_cache.cache_dir()
        for name in ("42_grid_12.png", "42_logo_5.png", "43_logo.png"):
            sgdb_artwork_cache.files[os.path.join(art_dir, name)] = b"cached"
        listings = 0
        listdir = sgdb_artwork_cache.listdir

        def counted(directory: str) -> list[str]:
            nonlocal listings
            listings += 1
            return listdir(directory)

        monkeypatch.setattr(sgdb_artwork_cache, "listdir", counted)

        for rom_id in (42, 43):
            for slot in (ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO):
                assert steamgrid.service.do_fetch_slot_artwork(rom_id, slot) is SlotAnswer.READY
        served = []
        for rom_id, app_id in ((42, _APP_ID), (43, _APP_ID + 1)):
            for slot in ("grid", "logo"):
                result = await steamgrid.service.get_sgdb_slot_artwork(rom_id, app_id, slot)
                await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])
                served.append((rom_id, slot, result["image_id"]))

        assert listings == 1
        assert [os.path.basename(dest) for _url, dest in fake_steamgrid_db_api.downloaded] == [
            "43_grid_12.png",
            "43_logo_5.png",
        ]
        assert served == [(42, "grid", 12), (42, "logo", 5), (43, "grid", 12), (43, "logo", 5)]
        assert sorted(listdir(art_dir)) == ["42_grid_12.png", "42_logo_5.png", "43_grid_12.png", "43_logo_5.png"]

    def test_a_cached_file_that_is_gone_is_downloaded_again(
        self, steamgrid, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))
        cached = os.path.join(sgdb_artwork_cache.cache_dir(), "42_logo_301.png")
        sgdb_artwork_cache.files[cached] = b"cached"
        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.READY
        del sgdb_artwork_cache.files[cached]

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.READY
        assert fake_steamgrid_db_api.requested_paths == ["/logos/game/9999?types=static"]
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_logo_5.png"]


class TestTheSlotFetchTellsARefusedKeyApart:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_a_refused_key_is_key_refused_and_not_recorded(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, status_code
    ):
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=status_code, message="Unauthorized")

        result = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "logo")

        assert result == {"status": "key_refused"}
        assert _outcome(uow, ArtworkSlot.LOGO) is None


class TestRemoveSgdbApiKey:
    def test_it_forgets_the_stored_key(self, steamgrid):
        steamgrid.settings["steamgriddb_api_key"] = "stored-key"
        persister = FakeSettingsPersister()
        steamgrid.service._settings_persister = persister

        result = steamgrid.service.remove_sgdb_api_key()

        assert result == {"success": True, "message": "SteamGridDB API key removed"}
        assert steamgrid.settings["steamgriddb_api_key"] == ""
        assert persister.save_count == 1

    def test_the_artwork_cache_stays(self, steamgrid, sgdb_artwork_cache):
        steamgrid.settings["steamgriddb_api_key"] = "stored-key"
        art_dir = sgdb_artwork_cache.cache_dir()
        held = {os.path.join(art_dir, name): b"cached" for name in ("42_hero.png", "42_grid_12.png", "42_icon.png")}
        sgdb_artwork_cache.files.update(held)

        steamgrid.service.remove_sgdb_api_key()

        assert sgdb_artwork_cache.files == held

    @pytest.mark.asyncio
    async def test_a_cached_image_is_still_served_without_the_key(self, steamgrid, sgdb_artwork_cache):
        steamgrid.settings["steamgriddb_api_key"] = "stored-key"
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "hero", 7)] = b"cached hero"

        steamgrid.service.remove_sgdb_api_key()
        result = await steamgrid.service.get_sgdb_artwork_base64(42, 1)

        assert base64.b64decode(result["base64"]) == b"cached hero"
        await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])


class TestSavingAKeyNoHeaderCanCarry:
    @pytest.mark.parametrize("key", _KEYS_NO_HEADER_CAN_CARRY)
    def test_it_is_refused_and_not_stored(self, steamgrid, key):
        steamgrid.settings["steamgriddb_api_key"] = "stored-key"
        persister = FakeSettingsPersister()
        steamgrid.service._settings_persister = persister

        with pytest.raises(Refused) as refused:
            steamgrid.service.save_sgdb_api_key(key)

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "Invalid API key")
        assert steamgrid.settings["steamgriddb_api_key"] == "stored-key"
        assert persister.save_count == 0


def _seed_cached_artwork(cache: FakeSgdbArtworkCache, rom_id: int) -> dict[str, str]:
    """Cache an old image of each asset type for *rom_id*; answers each one's path."""
    art_dir = cache.cache_dir()
    paths = {
        "hero": os.path.join(art_dir, f"{rom_id}_hero_10.png"),
        "logo": os.path.join(art_dir, f"{rom_id}_logo_11.png"),
        "grid": os.path.join(art_dir, f"{rom_id}_grid_12.png"),
        "icon": os.path.join(art_dir, f"{rom_id}_icon_13.png"),
    }
    for path in paths.values():
        cache.files[path] = b"old"
    return paths


class TestRefreshSgdbArtwork:
    @pytest.mark.asyncio
    async def test_all_four_images_are_downloaded_anew_over_the_cached_ones(
        self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        old = _seed_cached_artwork(sgdb_artwork_cache, 42)
        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        result = await steamgrid.service.refresh_sgdb_artwork(42, 8888)

        assert result == {"success": True}
        for asset_type in ("hero", "logo", "grid", "icon"):
            assert sgdb_artwork_cache.files[_picked_path(sgdb_artwork_cache, 42, asset_type)] == (
                f"{asset_type} bytes".encode()
            )
        for asset_type in ("hero", "logo", "grid", "icon"):
            assert old[asset_type] not in sgdb_artwork_cache.files

    @pytest.mark.asyncio
    async def test_a_failed_download_keeps_the_cached_image(self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        old = _seed_cached_artwork(sgdb_artwork_cache, 42)
        for asset_type in ("hero", "logo", "grid", "icon"):
            fake_steamgrid_db_api.seed_artwork(8888, asset_type, f"https://example.com/{asset_type}.png", 900)
        fake_steamgrid_db_api.download_image_return = False

        result = await steamgrid.service.refresh_sgdb_artwork(42, 8888)

        assert result == {"success": True}
        assert sgdb_artwork_cache.files == dict.fromkeys(old.values(), b"old")

    @pytest.mark.asyncio
    async def test_an_image_steamgriddb_no_longer_has_keeps_the_cached_one(
        self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        old = _seed_cached_artwork(sgdb_artwork_cache, 42)
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=404, message="Game not found.")

        assert await steamgrid.service.refresh_sgdb_artwork(42, 8888) == {"success": True}
        assert sgdb_artwork_cache.files == dict.fromkeys(old.values(), b"old")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "reason", "message"),
        [
            (
                SgdbApiError(status_code=401, message="Unauthorized"),
                "auth_failed",
                "SteamGridDB refused the API key — check it in Settings",
            ),
            (
                SgdbApiError(status_code=403, message="Forbidden"),
                "auth_failed",
                "SteamGridDB refused the API key — check it in Settings",
            ),
            (
                SgdbRateLimitedError("Too Many Requests"),
                "rate_limited",
                "SteamGridDB is rate-limiting requests — try again in a moment",
            ),
        ],
        ids=["401", "403", "429"],
    )
    async def test_a_refused_key_or_a_rate_limit_is_refused_and_the_cache_kept(
        self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api, error, reason, message
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        old = _seed_cached_artwork(sgdb_artwork_cache, 42)
        fake_steamgrid_db_api.request_side_effect = error

        with pytest.raises(Refused) as refused:
            await steamgrid.service.refresh_sgdb_artwork(42, 8888)

        assert (refused.value.reason, refused.value.message) == (reason, message)
        assert sgdb_artwork_cache.files == dict.fromkeys(old.values(), b"old")

    @pytest.mark.asyncio
    async def test_without_a_key_it_refuses_no_api_key(self, steamgrid, fake_steamgrid_db_api):
        with pytest.raises(Refused) as refused:
            await steamgrid.service.refresh_sgdb_artwork(42, 8888)

        assert refused.value.reason == "no_api_key"
        assert fake_steamgrid_db_api.call_log == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", _KEYS_NO_HEADER_CAN_CARRY)
    async def test_a_key_no_header_can_carry_is_refused_without_a_request(self, steamgrid, fake_steamgrid_db_api, key):
        steamgrid.settings["steamgriddb_api_key"] = key

        with pytest.raises(Refused) as refused:
            await steamgrid.service.refresh_sgdb_artwork(42, 8888)

        assert (refused.value.reason, refused.value.message) == ("auth_failed", "Invalid API key")
        assert fake_steamgrid_db_api.call_log == []


class TestARefusedNameSearchOpensNoPicker:
    @pytest.mark.asyncio
    async def test_a_rate_limited_search_refuses_rate_limited(self, steamgrid, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        fake_steamgrid_db_api.request_side_effect = SgdbRateLimitedError("Too Many Requests")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.search_sgdb_games("mario")

        assert (refused.value.reason, refused.value.message) == (
            "rate_limited",
            "SteamGridDB is rate-limiting requests — try again in a moment",
        )


class TestAVersionSwitchDownloadsOnlyWhatIsMissing:
    @pytest.mark.asyncio
    async def test_the_cached_images_are_served_and_only_the_missing_ones_downloaded(
        self, steamgrid, uow, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, sgdb_id=8888)
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "hero", 7)] = b"cached hero"
        sgdb_artwork_cache.files[os.path.join(sgdb_artwork_cache.cache_dir(), "42_logo_11.png")] = b"cached logo"
        _seed_all_artwork(fake_steamgrid_db_api, 8888)

        served = {}
        for asset_type, number in (("hero", 1), ("logo", 2), ("grid", 3)):
            result = await steamgrid.service.get_sgdb_artwork_base64(42, number)
            await steamgrid.prune_conflicts.release_lease(result["prune_lease_token"])
            served[asset_type] = base64.b64decode(result["base64"])

        assert served == {
            "hero": b"cached hero",
            "logo": b"cached logo",
            "grid": b"grid bytes",
        }
        assert [path.split("?")[0] for path in fake_steamgrid_db_api.requested_paths] == ["/grids/game/8888"]


class TestTheKeyDialogsVerification:
    @pytest.mark.asyncio
    async def test_a_429_refuses_rate_limited(self, steamgrid, fake_steamgrid_db_api):
        fake_steamgrid_db_api.verify_api_key_side_effect = SgdbRateLimitedError("Too Many Requests")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.verify_sgdb_api_key("some-key")

        assert (refused.value.reason, refused.value.message) == (
            "rate_limited",
            "SteamGridDB is rate-limiting requests — try again in a moment",
        )


class TestAPickRefusesLikeRefresh:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "reason", "message"),
        [
            (
                SgdbApiError(status_code=401, message="Unauthorized"),
                "auth_failed",
                "SteamGridDB refused the API key — check it in Settings",
            ),
            (
                SgdbApiError(status_code=403, message="Forbidden"),
                "auth_failed",
                "SteamGridDB refused the API key — check it in Settings",
            ),
            (
                SgdbRateLimitedError("Too Many Requests"),
                "rate_limited",
                "SteamGridDB is rate-limiting requests — try again in a moment",
            ),
        ],
        ids=["401", "403", "429"],
    )
    async def test_it_is_refused_and_the_previous_pick_stays_cached(
        self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api, error, reason, message
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        old = _seed_cached_artwork(sgdb_artwork_cache, 42)
        fake_steamgrid_db_api.request_side_effect = error

        with pytest.raises(Refused) as refused:
            await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert (refused.value.reason, refused.value.message) == (reason, message)
        assert sgdb_artwork_cache.files == dict.fromkeys(old.values(), b"old")

    @pytest.mark.asyncio
    async def test_an_image_the_picked_game_has_none_of_drops_the_previous_picks(
        self, steamgrid, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_cached_artwork(sgdb_artwork_cache, 42)
        fake_steamgrid_db_api.seed_artwork(8888, "hero", "https://example.com/hero.png", 900)
        fake_steamgrid_db_api.seed_image_bytes("https://example.com/hero.png", b"new hero")
        for asset_type in ("logo", "grid", "icon"):
            endpoint = {"logo": "logos", "grid": "grids", "icon": "icons"}[asset_type]
            fake_steamgrid_db_api.seed_raw_response(f"/{endpoint}/game/8888", {"success": True, "data": []})

        assert await steamgrid.service.apply_sgdb_game_id(42, 8888) == {"success": True}
        assert sgdb_artwork_cache.files == {_cached_path(sgdb_artwork_cache, 42, "hero", 900): b"new hero"}


# ── the ARTWORK tab ────────────────────────────────────────────────────────


def _choose(uow, slot: ArtworkSlot, image_id: int, rom_id: int = 42) -> None:
    with uow:
        uow.artwork_slot_choices.save(ArtworkSlotChoice(rom_id=rom_id, slot=slot, image_id=image_id, chosen_at=1.0))


def _choice(uow, slot: ArtworkSlot, rom_id: int = 42):
    with uow:
        return uow.artwork_slot_choices.get(rom_id, slot)


def _record_set(uow, slot: ArtworkSlot, image_id: int, data: bytes) -> None:
    with uow:
        uow.artwork_slot_outcomes.save(
            ArtworkSlotOutcome.record_set(
                rom_id=42,
                slot=slot,
                sgdb_id=9999,
                image_id=image_id,
                image_sha256=hashlib.sha256(data).hexdigest(),
                recorded_at=1.0,
            )
        )


def _slot_rows(answer: dict[str, Any]) -> dict[str, tuple[str, str, int | None]]:
    return {row["slot"]: (row["state"], row["source"], row["image_id"]) for row in answer["slots"]}


def _seed_cover_candidates(fake_steamgrid_db_api, *images: tuple[int, int, int]) -> None:
    data = []
    for image_id, width, height in images:
        url = f"https://cdn.example/{image_id}.png"
        data.append(
            {
                "id": image_id,
                "url": url,
                "thumb": f"https://cdn.example/t{image_id}.jpg",
                "width": width,
                "height": height,
            }
        )
        fake_steamgrid_db_api.seed_image_bytes(url, f"image {image_id}".encode())
    fake_steamgrid_db_api.seed_raw_response("/grids/game/9999?dimensions=600x900", {"success": True, "data": data})


class TestTheArtworkTabReadsEachSlot:
    @pytest.mark.asyncio
    async def test_romms_game_is_named_from_steamgriddb(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.seed_raw_response("/games/id/9999", {"success": True, "data": {"name": "Mario Golf"}})

        answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert answer["success"] is True
        assert answer["has_api_key"] is True
        assert answer["game"] == {"sgdb_id": 9999, "source": "romm", "name": "Mario Golf"}

    @pytest.mark.asyncio
    async def test_a_game_the_user_chose_is_named_chosen(self, steamgrid, uow, home):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID)
        _grid_dir(home)
        with uow:
            uow.roms.set_chosen_sgdb_id(42, 8888)

        answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert (answer["game"]["sgdb_id"], answer["game"]["source"]) == (8888, "chosen")

    @pytest.mark.asyncio
    async def test_without_a_game_the_steamgriddb_slots_need_one_and_the_cover_is_romms(
        self, steamgrid, uow, home, fake_steamgrid_db_api
    ):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID)
        (_grid_dir(home) / f"{_APP_ID}p.png").write_bytes(b"romm's cover")

        answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert answer["game"] == {"sgdb_id": None, "source": None, "name": None}
        assert _slot_rows(answer) == {
            "cover": ("automatic", "romm", None),
            "grid": ("automatic", "needs_game", None),
            "hero": ("automatic", "needs_game", None),
            "logo": ("automatic", "needs_game", None),
            "icon": ("automatic", "needs_game", None),
        }
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_without_a_key_nothing_is_asked(self, steamgrid, uow, home, fake_steamgrid_db_api):
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)
        _grid_dir(home)

        answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert (answer["has_api_key"], answer["game"]["name"]) == (False, None)
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_the_rows_come_in_the_tabs_order(self, steamgrid, slot_ready):
        answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert [row["slot"] for row in answer["slots"]] == ["cover", "grid", "hero", "logo", "icon"]

    @pytest.mark.asyncio
    async def test_each_slot_reads_its_state(self, steamgrid, uow, slot_ready, sgdb_artwork_cache):
        (slot_ready / f"{_APP_ID}.png").write_bytes(b"tenders grid")
        _record_set(uow, ArtworkSlot.WIDE_GRID, 12, b"tenders grid")
        (slot_ready / f"{_APP_ID}_hero.png").write_bytes(b"a png set by hand")
        _record_set(uow, ArtworkSlot.HERO, 13, b"tenders hero")
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"chosen logo")
        _choose(uow, ArtworkSlot.LOGO, 77)
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: "/home/deck/Pictures/mine.png"})
        steamgrid.cover.set_in_steam.add(42)

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))

        assert rows == {
            "cover": ("set_in_steam", "steam", None),
            "grid": ("automatic", "tender", 12),
            "hero": ("set_in_steam", "steam", None),
            "logo": ("chosen", "chosen", 77),
            "icon": ("set_in_steam", "steam", None),
        }

    @pytest.mark.asyncio
    async def test_a_png_is_compared_with_the_cached_copy_where_nothing_is_recorded(
        self, steamgrid, slot_ready, sgdb_artwork_cache
    ):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "grid", 12)] = b"cached grid"
        (slot_ready / f"{_APP_ID}.png").write_bytes(b"not the cached grid")
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 5)] = b"cached logo"
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"cached logo")

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))

        assert rows["grid"] == ("set_in_steam", "steam", None)
        assert rows["logo"] == ("automatic", "tender", 5)

    @pytest.mark.asyncio
    async def test_another_extension_is_set_in_steam(self, steamgrid, slot_ready):
        (slot_ready / f"{_APP_ID}_logo.jpg").write_bytes(b"hand-set")

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))

        assert rows["logo"] == ("set_in_steam", "steam", None)

    @pytest.mark.asyncio
    async def test_tenders_icon_is_automatic(self, steamgrid, uow, slot_ready):
        (slot_ready / f"{_APP_ID}_icon.png").write_bytes(b"icon")
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: str(slot_ready / f"{_APP_ID}_icon.png")})
        _record_set(uow, ArtworkSlot.ICON, 21, b"icon")

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))

        assert rows["icon"] == ("automatic", "tender", 21)

    @pytest.mark.asyncio
    async def test_without_a_grid_directory_it_is_refused(self, steamgrid, uow):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)

        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert refused.value.reason == "no_grid_dir"


def _signatures(answer: dict[str, Any]) -> dict[str, str]:
    return {row["slot"]: row["signature"] for row in answer["slots"]}


class TestEachSlotIsSignedByItsOwnFiles:
    @pytest.mark.asyncio
    async def test_a_changed_file_changes_only_its_own_slots_signature(self, steamgrid, slot_ready):
        for name in (f"{_APP_ID}p.png", f"{_APP_ID}.png", f"{_APP_ID}_hero.png", f"{_APP_ID}_logo.png"):
            (slot_ready / name).write_bytes(b"an image")
            os.utime(slot_ready / name, ns=(1_000, 2_000))
        before = _signatures(await steamgrid.service.get_artwork_slots(42, _APP_ID))
        (slot_ready / f"{_APP_ID}_hero.png").write_bytes(b"another image")
        os.utime(slot_ready / f"{_APP_ID}_hero.png", ns=(1_000, 3_000))

        after = _signatures(await steamgrid.service.get_artwork_slots(42, _APP_ID))

        assert after["hero"] != before["hero"]
        assert {slot: after[slot] for slot in after if slot != "hero"} == {
            slot: before[slot] for slot in before if slot != "hero"
        }

    @pytest.mark.asyncio
    async def test_no_two_slots_share_a_signature_though_their_files_are_alike(self, steamgrid, slot_ready):
        for name in (f"{_APP_ID}p.png", f"{_APP_ID}.png", f"{_APP_ID}_hero.png", f"{_APP_ID}_logo.png"):
            (slot_ready / name).write_bytes(b"an image")
            os.utime(slot_ready / name, ns=(1_000, 2_000))

        signatures = _signatures(await steamgrid.service.get_artwork_slots(42, _APP_ID))

        assert len({signatures[slot] for slot in ("cover", "grid", "hero", "logo")}) == 4

    @pytest.mark.asyncio
    async def test_an_empty_slot_has_no_signature(self, steamgrid, slot_ready):
        (slot_ready / f"{_APP_ID}.png").write_bytes(b"an image")

        signatures = _signatures(await steamgrid.service.get_artwork_slots(42, _APP_ID))

        assert signatures["grid"] != ""
        assert signatures["hero"] == ""

    @pytest.mark.asyncio
    async def test_files_that_cannot_be_looked_at_still_answer_the_slots(self, steamgrid, slot_ready, caplog):
        (slot_ready / f"{_APP_ID}.png").write_bytes(b"an image")

        def refuse(names):
            raise PermissionError("denied")

        steamgrid.steam_config.grid_file_stamps = refuse  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING):
            answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert _slot_rows(answer)["grid"] == ("automatic", "tender", None)
        assert "sign the artwork thumbnails" in caplog.text


class TestThePickersCandidates:
    @pytest.mark.asyncio
    async def test_page_zero_lists_steamgriddbs_order_with_tenders_pick(
        self, steamgrid, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.seed_raw_response(
            "/grids/game/9999",
            {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "url": "https://cdn.example/1.png",
                        "thumb": "https://cdn.example/t1.jpg",
                        "width": 460,
                        "height": 215,
                    },
                    {
                        "id": 2,
                        "url": "https://cdn.example/2.png",
                        "thumb": "https://cdn.example/t2.jpg",
                        "width": 920,
                        "height": 430,
                    },
                ],
            },
        )

        answer = await steamgrid.service.get_artwork_slot_candidates(42, "grid", 0)

        assert answer == {
            "success": True,
            "candidates": [
                {"image_id": 1, "thumb": "https://cdn.example/t1.jpg", "width": 460, "height": 215},
                {"image_id": 2, "thumb": "https://cdn.example/t2.jpg", "width": 920, "height": 430},
            ],
            "has_more": True,
            "tenders_pick": 2,
        }
        assert fake_steamgrid_db_api.requested_paths == [
            "/grids/game/9999?dimensions=460x215,920x430&types=static&page=0"
        ]

    @pytest.mark.asyncio
    async def test_the_cover_lists_portrait_grids_and_names_no_pick(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        (slot_ready / f"{_APP_ID}p.png").write_bytes(b"romm's cover")
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))

        answer = await steamgrid.service.get_artwork_slot_candidates(42, "cover", 0)

        assert answer["tenders_pick"] is None
        assert [c["image_id"] for c in answer["candidates"]] == [5]
        assert fake_steamgrid_db_api.requested_paths == [
            "/grids/game/9999?dimensions=600x900,660x930,342x482&types=static&page=0"
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("shown", ["empty", "tenders"])
    async def test_a_cover_steamgriddb_fills_names_tenders_pick(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, shown
    ):
        if shown == "tenders":
            _show_tenders(slot_ready, uow, ArtworkSlot.COVER, 9)
        _seed_cover_candidates(fake_steamgrid_db_api, (8, 342, 482), (5, 600, 900))

        answer = await steamgrid.service.get_artwork_slot_candidates(42, "cover", 0)

        assert answer["tenders_pick"] == 5

    @pytest.mark.asyncio
    async def test_a_chosen_cover_names_no_pick(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _choose(uow, ArtworkSlot.COVER, 8)
        _seed_cover_candidates(fake_steamgrid_db_api, (8, 342, 482), (5, 600, 900))

        answer = await steamgrid.service.get_artwork_slot_candidates(42, "cover", 0)

        assert answer["tenders_pick"] is None

    @pytest.mark.asyncio
    async def test_a_later_page_names_no_pick(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        _seed_candidates(fake_steamgrid_db_api, "hero", (3, 1920, 620))

        answer = await steamgrid.service.get_artwork_slot_candidates(42, "hero", 2)

        assert answer["tenders_pick"] is None
        assert fake_steamgrid_db_api.requested_paths == ["/heroes/game/9999?page=2"]

    @pytest.mark.asyncio
    async def test_a_game_steamgriddb_does_not_know_lists_nothing(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=404, message="Game not found.")

        answer = await steamgrid.service.get_artwork_slot_candidates(42, "logo", 0)

        assert answer == {"success": True, "candidates": [], "has_more": False, "tenders_pick": None}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (SgdbRateLimitedError("429"), "rate_limited"),
            (SgdbApiError(status_code=401, message="no"), "auth_failed"),
            (SgdbApiError(status_code=None, message="down"), "server_unreachable"),
        ],
        ids=["429", "401", "unreachable"],
    )
    async def test_steamgriddbs_errors_refuse(self, steamgrid, slot_ready, fake_steamgrid_db_api, error, reason):
        fake_steamgrid_db_api.request_side_effect = error

        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_artwork_slot_candidates(42, "logo", 0)

        assert refused.value.reason == reason

    @pytest.mark.asyncio
    async def test_without_a_game_it_is_refused(self, steamgrid, uow, home):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID)

        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_artwork_slot_candidates(42, "logo", 0)

        assert refused.value.reason == "no_sgdb_game"

    @pytest.mark.asyncio
    async def test_without_a_key_it_is_refused(self, steamgrid, uow):
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)

        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_artwork_slot_candidates(42, "logo", 0)

        assert refused.value.reason == "no_api_key"

    @pytest.mark.asyncio
    async def test_an_unknown_slot_is_refused(self, steamgrid, slot_ready):
        with pytest.raises(Refused) as refused:
            await steamgrid.service.get_artwork_slot_candidates(42, "banner", 0)

        assert refused.value.reason == "invalid_payload"


class TestChoosingAnImage:
    @pytest.mark.asyncio
    async def test_the_listed_image_is_downloaded_recorded_and_served(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (1, 800, 300), (2, 900, 300))
        await steamgrid.service.get_artwork_slot_candidates(42, "logo", 0)

        answer = await steamgrid.service.choose_artwork_slot_image(42, "logo", 2)

        assert answer["status"] == "image"
        assert (answer["image_id"], answer["sgdb_id"]) == (2, 9999)
        assert base64.b64decode(answer["base64"]) == b"image 2"
        assert answer["sha256"] == hashlib.sha256(b"image 2").hexdigest()
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 2
        assert sorted(sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir())) == ["42_logo_2.png"]
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_chosen_icon_answers_ready_for_the_icon_write(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "icon", (4, 256, 256))

        answer = await steamgrid.service.choose_artwork_slot_image(42, "icon", 4)

        assert (answer["status"], answer["image_id"]) == ("ready", 4)
        assert _choice(uow, ArtworkSlot.ICON).image_id == 4
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_chosen_cover_is_a_portrait_grid(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))

        answer = await steamgrid.service.choose_artwork_slot_image(42, "cover", 5)

        assert (answer["status"], base64.b64decode(answer["base64"])) == ("image", b"image 5")
        assert _choice(uow, ArtworkSlot.COVER).image_id == 5
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_an_image_not_listed_any_more_is_refused_and_not_recorded(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (1, 800, 300))

        with pytest.raises(Refused) as refused:
            await steamgrid.service.choose_artwork_slot_image(42, "logo", 99)

        assert refused.value.reason == "image_not_found"
        assert _choice(uow, ArtworkSlot.LOGO) is None

    @pytest.mark.asyncio
    async def test_a_failed_download_is_refused_and_not_recorded(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.seed_raw_response(
            "/logos/game/9999", {"success": True, "data": [{"id": 1, "url": "https://cdn.example/1.png"}]}
        )
        fake_steamgrid_db_api.download_image_return = False

        with pytest.raises(Refused) as refused:
            await steamgrid.service.choose_artwork_slot_image(42, "logo", 1)

        assert refused.value.reason == "download_failed"
        assert _choice(uow, ArtworkSlot.LOGO) is None


class TestAutomaticAgain:
    @pytest.mark.asyncio
    async def test_a_steamgriddb_slot_gets_tenders_pick_anew_and_loses_its_choice(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "grid", 77)] = b"chosen"
        _choose(uow, ArtworkSlot.WIDE_GRID, 77)
        _seed_candidates(fake_steamgrid_db_api, "grid", (1, 460, 215), (2, 920, 430))

        answer = await steamgrid.service.set_artwork_slot_automatic(42, "grid")

        assert (answer["status"], answer["image_id"]) == ("image", 2)
        assert _choice(uow, ArtworkSlot.WIDE_GRID) is None
        assert sorted(sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir())) == ["42_grid_2.png"]
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_steamgriddb_having_none_answers_none_and_keeps_the_choice(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _choose(uow, ArtworkSlot.LOGO, 77)
        fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", {"success": True, "data": []})

        assert await steamgrid.service.set_artwork_slot_automatic(42, "logo") == {"status": "none"}
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77

    @pytest.mark.asyncio
    async def test_a_failed_download_keeps_the_choice(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _choose(uow, ArtworkSlot.LOGO, 77)
        fake_steamgrid_db_api.seed_raw_response(
            "/logos/game/9999", {"success": True, "data": [{"id": 1, "url": "https://cdn.example/1.png"}]}
        )
        fake_steamgrid_db_api.download_image_return = False

        with pytest.raises(Refused) as refused:
            await steamgrid.service.set_artwork_slot_automatic(42, "logo")

        assert refused.value.reason == "download_failed"
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77

    @pytest.mark.asyncio
    async def test_the_cover_reloads_romms_cover_and_forgets_the_chosen_image(self, steamgrid, uow, slot_ready):
        _choose(uow, ArtworkSlot.COVER, 5)
        _record_set(uow, ArtworkSlot.COVER, 5, b"chosen cover")

        answer = await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert answer["status"] == "cover"
        assert steamgrid.cover.reloaded == [42]
        assert _choice(uow, ArtworkSlot.COVER) is None
        assert _outcome(uow, ArtworkSlot.COVER) is None
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_cover_reload_romm_refuses_keeps_the_choice(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _choose(uow, ArtworkSlot.COVER, 5)
        _seed_cover_candidates(fake_steamgrid_db_api)
        steamgrid.cover.refusal = Refused("no_cover", "RomM has no cover artwork")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert refused.value.reason == "no_cover"
        assert _choice(uow, ArtworkSlot.COVER).image_id == 5

    @pytest.mark.asyncio
    async def test_without_a_romm_cover_tenders_steamgriddb_pick_is_served(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _choose(uow, ArtworkSlot.COVER, 5)
        _seed_cover_candidates(fake_steamgrid_db_api, (8, 342, 482), (5, 600, 900), (7, 660, 930), (6, 600, 900))
        steamgrid.cover.refusal = Refused("no_cover", "RomM has no cover artwork")

        answer = await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert (answer["status"], answer["image_id"], base64.b64decode(answer["base64"])) == ("image", 5, b"image 5")
        assert _choice(uow, ArtworkSlot.COVER) is None
        assert _handover(uow, ArtworkSlot.COVER).image_id == 5
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_without_a_romm_cover_the_pick_is_downloaded_anew(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "cover", 5)] = b"damaged"
        steamgrid.cover.refusal = Refused("no_cover", "RomM has no cover artwork")

        answer = await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert base64.b64decode(answer["base64"]) == b"image 5"
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", ["key", "game"])
    async def test_without_a_romm_cover_or_a_steamgriddb_game_or_key_romms_refusal_stands(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, missing
    ):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))
        if missing == "key":
            steamgrid.settings["steamgriddb_api_key"] = ""
        else:
            _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)
        steamgrid.cover.refusal = Refused("no_cover", "RomM has no cover artwork")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert (refused.value.reason, refused.value.message) == ("no_cover", "RomM has no cover artwork")
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (SgdbRateLimitedError("slow down"), "rate_limited"),
            (SgdbApiError(401, "Unauthorized"), "auth_failed"),
        ],
        ids=["429", "refused-key"],
    )
    async def test_without_a_romm_cover_steamgriddbs_refusal_is_answered(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, error, reason
    ):
        _choose(uow, ArtworkSlot.COVER, 5)
        fake_steamgrid_db_api.request_side_effect = error
        steamgrid.cover.refusal = Refused("no_cover", "RomM has no cover artwork")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert refused.value.reason == reason
        assert _choice(uow, ArtworkSlot.COVER).image_id == 5

    @pytest.mark.asyncio
    async def test_without_a_romm_cover_a_failed_download_is_refused(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        fake_steamgrid_db_api.seed_raw_response(
            "/grids/game/9999?dimensions=600x900",
            {"success": True, "data": [{"id": 5, "url": "https://cdn.example/5.png", "width": 600, "height": 900}]},
        )
        fake_steamgrid_db_api.download_image_return = False
        steamgrid.cover.refusal = Refused("no_cover", "RomM has no cover artwork")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert refused.value.reason == "download_failed"

    @pytest.mark.asyncio
    async def test_another_refusal_of_romms_reload_asks_steamgriddb_nothing(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))
        steamgrid.cover.refusal = Refused("not_found", "Could not fetch ROM from server")

        with pytest.raises(Refused) as refused:
            await steamgrid.service.set_artwork_slot_automatic(42, "cover")

        assert refused.value.reason == "not_found"
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_an_update_in_progress_refuses_it(self, steamgrid, slot_ready):
        steamgrid.service._tab._rules = _make_conflict_rules(update_in_progress=True)

        with _refused_by_conflict_rule("blocked_by_update"):
            await steamgrid.service.set_artwork_slot_automatic(42, "cover")


class TestRefreshArtworkLeavesAChoiceAndSteamAlone:
    """D6: Refresh Artwork downloads only the automatic slots that are not set in Steam."""

    @pytest.mark.asyncio
    async def test_a_chosen_slot_and_a_slot_set_in_steam_are_not_downloaded(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        for asset_type in ("grid", "logo", "hero", "icon"):
            _seed_candidates(fake_steamgrid_db_api, asset_type, (1, 920, 430))
        _choose(uow, ArtworkSlot.LOGO, 77)
        (slot_ready / f"{_APP_ID}_hero.jpg").write_bytes(b"hand-set")

        assert await steamgrid.service.refresh_sgdb_artwork(42, 9999) == {"success": True}

        assert sorted(path.split("/")[1] for path in fake_steamgrid_db_api.requested_paths) == ["grids", "icons"]
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77

    @pytest.mark.asyncio
    async def test_the_resolution_keeps_a_set_record_and_drops_a_none(self, steamgrid, uow, slot_ready):
        _record_set(uow, ArtworkSlot.WIDE_GRID, 12, b"tenders grid")
        with uow:
            uow.artwork_slot_outcomes.save(
                ArtworkSlotOutcome.record_none(rom_id=42, slot=ArtworkSlot.LOGO, sgdb_id=9999, recorded_at=1.0)
            )

        await steamgrid.service.get_sgdb_resolution(42)

        assert _outcome(uow, ArtworkSlot.WIDE_GRID).image_id == 12
        assert _outcome(uow, ArtworkSlot.LOGO) is None


class TestChangingTheGameDiscardsTheChoices:
    """D12: a new SteamGridDB game sends every chosen slot back to automatic; a slot set in Steam stays."""

    @pytest.mark.asyncio
    async def test_the_choices_go_and_a_slot_set_in_steam_is_not_downloaded(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        for asset_type in ("logo", "hero", "icon"):
            fake_steamgrid_db_api.seed_artwork(8888, asset_type, f"https://cdn.example/{asset_type}.png")
        _choose(uow, ArtworkSlot.LOGO, 77)
        (slot_ready / f"{_APP_ID}.jpg").write_bytes(b"wide grid set by hand")

        assert await steamgrid.service.apply_sgdb_game_id(42, 8888) == {"success": True}

        assert _choice(uow, ArtworkSlot.LOGO) is None
        assert sorted(path.split("/")[1] for path in fake_steamgrid_db_api.requested_paths) == [
            "heroes",
            "icons",
            "logos",
        ]

    @pytest.mark.asyncio
    async def test_a_chosen_cover_is_left_for_set_artwork_slot_automatic(self, steamgrid, uow, slot_ready):
        _choose(uow, ArtworkSlot.COVER, 5)
        _record_set(uow, ArtworkSlot.COVER, 5, b"chosen cover")

        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert steamgrid.cover.reloaded == []
        assert _choice(uow, ArtworkSlot.COVER).image_id == 5
        assert _outcome(uow, ArtworkSlot.COVER).image_id == 5

    @pytest.mark.asyncio
    async def test_a_chosen_cover_set_in_steam_is_left_alone(self, steamgrid, uow, slot_ready):
        _choose(uow, ArtworkSlot.COVER, 5)
        steamgrid.cover.set_in_steam.add(42)

        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert steamgrid.cover.reloaded == []
        assert _choice(uow, ArtworkSlot.COVER) is None

    @pytest.mark.asyncio
    async def test_a_dropped_cover_choice_takes_the_record_of_the_image_it_set(self, steamgrid, uow, slot_ready):
        _choose(uow, ArtworkSlot.COVER, 5)
        _record_set(uow, ArtworkSlot.COVER, 5, b"chosen cover")
        steamgrid.cover.set_in_steam.add(42)

        await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert _outcome(uow, ArtworkSlot.COVER) is None

    @pytest.mark.asyncio
    async def test_a_refused_key_discards_nothing(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _choose(uow, ArtworkSlot.LOGO, 77)
        fake_steamgrid_db_api.request_side_effect = SgdbApiError(status_code=401, message="no")

        with pytest.raises(Refused):
            await steamgrid.service.apply_sgdb_game_id(42, 8888)

        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77


class TestAChosenImageStays:
    @pytest.mark.asyncio
    async def test_an_empty_chosen_slot_is_filled_with_the_chosen_image_not_tenders(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "grid", (1, 920, 430), (2, 460, 215))
        _choose(uow, ArtworkSlot.WIDE_GRID, 2)

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "grid")

        assert (answer["status"], answer["image_id"]) == ("image", 2)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    def test_the_job_fills_a_chosen_slot_with_the_chosen_image(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (1, 800, 300), (2, 900, 300))
        _choose(uow, ArtworkSlot.LOGO, 2)

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.READY
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == ["42_logo_2.png"]

    def test_a_chosen_image_steamgriddb_no_longer_lists_fills_nothing(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (1, 800, 300))
        _choose(uow, ArtworkSlot.LOGO, 2)

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.LOGO) is SlotAnswer.FAILED
        assert sgdb_artwork_cache.listdir(sgdb_artwork_cache.cache_dir()) == []


class TestARefusedOrLostReportKeepsTendersImageTenders:
    """D15: Tender's own image never reads "Set in Steam" because its report was refused or lost."""

    @pytest.mark.asyncio
    async def test_a_refused_report_keeps_the_last_set_record(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache, fake_steamgrid_db_api
    ):
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 5")
        await steamgrid.service.report_sgdb_slot_artwork(
            42, "logo", "set", 9999, 5, hashlib.sha256(b"image 5").hexdigest()
        )
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        await steamgrid.service.refresh_sgdb_artwork(42, 9999)

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, None, None)

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))
        assert rows["logo"] == ("automatic", "tender", 5)
        assert _outcome(uow, ArtworkSlot.LOGO).outcome is SlotOutcome.SET

    @pytest.mark.asyncio
    async def test_a_refused_report_without_a_set_record_is_recorded(self, steamgrid, uow, slot_ready):
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, None, None)

        assert _outcome(uow, ArtworkSlot.LOGO).outcome is SlotOutcome.REFUSED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slot", ["logo", "cover"])
    async def test_a_chosen_image_whose_report_never_lands_reads_chosen(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, slot
    ):
        if slot == "cover":
            _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))
        else:
            _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))
        answer = await steamgrid.service.choose_artwork_slot_image(42, slot, 5)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])
        name = f"{_APP_ID}p.png" if slot == "cover" else f"{_APP_ID}_logo.png"
        (slot_ready / name).write_bytes(b"image 5")

        handed = _handover(uow, ArtworkSlot(slot))
        assert (handed.image_id, handed.image_sha256) == (5, hashlib.sha256(b"image 5").hexdigest())
        if slot == "logo":
            rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))
            assert rows["logo"] == ("chosen", "chosen", 5)

    @pytest.mark.asyncio
    async def test_a_chosen_icon_records_no_reference_its_path_is_judged_by(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "icon", (4, 256, 256))

        answer = await steamgrid.service.choose_artwork_slot_image(42, "icon", 4)

        assert _outcome(uow, ArtworkSlot.ICON) is None
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])


class TestAVersionSwitchBringsBackTheChosenImages:
    """D16: the replacing fetch serves the chosen image, also once the cache lost it."""

    @pytest.mark.asyncio
    async def test_a_cache_without_the_chosen_image_downloads_exactly_that_one(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (1, 800, 300), (2, 900, 300))
        _choose(uow, ArtworkSlot.LOGO, 2)

        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 2)

        assert (answer["image_id"], base64.b64decode(answer["base64"])) == (2, b"image 2")
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_cache_holding_another_image_than_the_chosen_one_is_not_served(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 1)] = b"tenders pick"
        _seed_candidates(fake_steamgrid_db_api, "logo", (1, 800, 300), (2, 900, 300))
        _choose(uow, ArtworkSlot.LOGO, 2)

        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 2)

        assert answer["image_id"] == 2
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_the_chosen_image_in_the_cache_is_served_from_it(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 2)] = b"chosen"
        _choose(uow, ArtworkSlot.LOGO, 2)

        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 2)

        assert base64.b64decode(answer["base64"]) == b"chosen"
        assert fake_steamgrid_db_api.requested_paths == []
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])


class TestRommsGameReplacesTheChosenOne:
    """D17: once RomM gives an id, the choices made under the user's game go back to automatic, once."""

    def _seed(self, uow, *, romm_id: int | None):
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=romm_id)
        with uow:
            uow.roms.set_chosen_sgdb_id(42, 8888)

    @pytest.mark.asyncio
    async def test_the_choices_go_once_and_the_game_says_so(self, steamgrid, uow, home, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _grid_dir(home)
        self._seed(uow, romm_id=9999)
        _choose(uow, ArtworkSlot.LOGO, 77)
        fake_steamgrid_db_api.seed_raw_response("/games/id/9999", {"success": True, "data": {"name": "RomM's game"}})

        first = await steamgrid.service.get_artwork_game(42)
        second = await steamgrid.service.get_artwork_game(42)

        assert first["superseded"] == {"name": "RomM's game", "slots": ["logo"]}
        assert first["game"] == {"sgdb_id": 9999, "source": "romm", "name": None}
        assert second["superseded"] is None
        assert _choice(uow, ArtworkSlot.LOGO) is None
        with uow:
            assert uow.roms.get(42).chosen_sgdb_id is None

    @pytest.mark.asyncio
    async def test_a_chosen_cover_is_left_for_set_artwork_slot_automatic(self, steamgrid, uow, home):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _grid_dir(home)
        self._seed(uow, romm_id=9999)
        _choose(uow, ArtworkSlot.COVER, 5)

        answer = await steamgrid.service.get_artwork_game(42)

        assert answer["superseded"]["slots"] == ["cover"]
        assert _choice(uow, ArtworkSlot.COVER).image_id == 5

    @pytest.mark.asyncio
    async def test_without_romms_id_nothing_changes(self, steamgrid, uow, home):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        self._seed(uow, romm_id=None)
        _choose(uow, ArtworkSlot.LOGO, 77)

        answer = await steamgrid.service.get_artwork_game(42)

        assert (answer["superseded"], answer["game"]["source"]) == (None, "chosen")
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77

    @pytest.mark.asyncio
    async def test_romm_giving_the_chosen_game_changes_nothing(self, steamgrid, uow, home):
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=8888)
        with uow:
            uow.roms.set_chosen_sgdb_id(42, 8888)
        _choose(uow, ArtworkSlot.LOGO, 77)

        assert (await steamgrid.service.get_artwork_game(42))["superseded"] is None
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77

    @pytest.mark.asyncio
    async def test_a_visit_asks_steamgriddb_nothing(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        await steamgrid.service.get_artwork_game(42)

        assert fake_steamgrid_db_api.requested_paths == []


class TestTheTabsSmallerAnswers:
    @pytest.mark.asyncio
    async def test_the_games_name_is_asked_once_per_game(self, steamgrid, slot_ready, fake_steamgrid_db_api):
        fake_steamgrid_db_api.seed_raw_response("/games/id/9999", {"success": True, "data": {"name": "Mario Golf"}})

        await steamgrid.service.get_artwork_slots(42, _APP_ID)
        await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert fake_steamgrid_db_api.requested_paths == ["/games/id/9999"]

    @pytest.mark.asyncio
    async def test_a_choice_under_an_image_set_in_steam_is_named(self, steamgrid, uow, slot_ready):
        (slot_ready / f"{_APP_ID}_logo.jpg").write_bytes(b"hand-set")
        _choose(uow, ArtworkSlot.LOGO, 77)

        answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        logo = next(row for row in answer["slots"] if row["slot"] == "logo")
        assert (logo["state"], logo["has_choice"]) == ("set_in_steam", True)
        assert all(not row["has_choice"] for row in answer["slots"] if row["slot"] != "logo")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestTwoReferencesTellTendersImage:
    """D15: a grid file holding the image Steam confirmed, or the one Tender last handed over, is Tender's."""

    @pytest.mark.asyncio
    async def test_a_refused_choice_leaves_the_slot_as_before_the_press(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 5")
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 5, _sha(b"image 5"))
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300), (6, 800, 300))
        answer = await steamgrid.service.choose_artwork_slot_image(42, "logo", 6)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))
        assert rows["logo"] == ("automatic", "tender", 5)
        assert _choice(uow, ArtworkSlot.LOGO) is None
        assert _handover(uow, ArtworkSlot.LOGO) is None
        assert (_outcome(uow, ArtworkSlot.LOGO).outcome, _outcome(uow, ArtworkSlot.LOGO).image_id) == (
            SlotOutcome.SET,
            5,
        )

    @pytest.mark.asyncio
    async def test_a_refused_chosen_cover_discards_its_choice(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))
        answer = await steamgrid.service.choose_artwork_slot_image(42, "cover", 5)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "cover", "refused", 9999, 5, None)

        assert _choice(uow, ArtworkSlot.COVER) is None
        assert _handover(uow, ArtworkSlot.COVER) is None
        assert _outcome(uow, ArtworkSlot.COVER) is None

    @pytest.mark.asyncio
    async def test_a_lost_report_after_refresh_artwork_reads_tenders(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 5")
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 5, _sha(b"image 5"))
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        await steamgrid.service.refresh_sgdb_artwork(42, 9999)

        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 6")

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))
        assert rows["logo"][:2] == ("automatic", "tender")

    @pytest.mark.asyncio
    async def test_a_cancelled_apply_on_a_slot_without_a_record_keeps_it_tenders(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 5")
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 5)] = b"image 5"
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))

        await steamgrid.service.refresh_sgdb_artwork(42, 9999)

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))
        assert rows["logo"] == ("automatic", "tender", 5)
        assert (_outcome(uow, ArtworkSlot.LOGO).image_id, _handover(uow, ArtworkSlot.LOGO).image_id) == (5, 6)

    @pytest.mark.asyncio
    async def test_a_file_that_is_neither_image_reads_set_in_steam(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 5")
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 5, _sha(b"image 5"))
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        await steamgrid.service.refresh_sgdb_artwork(42, 9999)

        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"a logo the user set")

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))
        assert rows["logo"] == ("set_in_steam", "steam", None)

    @pytest.mark.asyncio
    async def test_a_set_report_makes_the_handed_image_the_confirmed_one(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache
    ):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 6)] = b"image 6"
        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 2)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])
        assert _handover(uow, ArtworkSlot.LOGO).image_sha256 == _sha(b"image 6")

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 6, _sha(b"image 6"))

        assert _handover(uow, ArtworkSlot.LOGO) is None
        assert _outcome(uow, ArtworkSlot.LOGO).image_sha256 == _sha(b"image 6")

    @pytest.mark.asyncio
    async def test_an_answer_for_an_older_image_keeps_the_newer_hand_over(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        await steamgrid.service.refresh_sgdb_artwork(42, 9999)

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 5, None)

        assert _handover(uow, ArtworkSlot.LOGO).image_id == 6

    @pytest.mark.asyncio
    async def test_the_per_slot_fetch_hands_over_what_it_serves(self, steamgrid, uow, slot_ready, sgdb_artwork_cache):
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "hero", 3)] = b"image 3"

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "hero", True)

        assert (_handover(uow, ArtworkSlot.HERO).image_id, answer["sha256"]) == (3, _sha(b"image 3"))
        assert _handover(uow, ArtworkSlot.HERO).image_sha256 == answer["sha256"]
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_the_icon_is_handed_over_without_a_record(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_candidates(fake_steamgrid_db_api, "icon", (4, 256, 256))

        answer = await steamgrid.service.choose_artwork_slot_image(42, "icon", 4)

        assert _handover(uow, ArtworkSlot.ICON) is None
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])


class TestARefusedChoiceGivesBackTheOneBefore:
    """D20: a chosen image Steam refuses gives back the choice the press replaced, and leaves the cache."""

    @pytest.mark.asyncio
    async def test_chosen_a_then_b_refused_is_chosen_a_again(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300), (6, 800, 300))
        a = await steamgrid.service.choose_artwork_slot_image(42, "logo", 5)
        await steamgrid.prune_conflicts.release_lease(a["prune_lease_token"])
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 5")
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 5, _sha(b"image 5"))
        before = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))["logo"]
        b = await steamgrid.service.choose_artwork_slot_image(42, "logo", 6)
        await steamgrid.prune_conflicts.release_lease(b["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        after = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))["logo"]
        assert before == after == ("chosen", "chosen", 5)
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 5

    @pytest.mark.asyncio
    async def test_the_same_image_chosen_again_and_refused_stays_chosen(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))
        first = await steamgrid.service.choose_artwork_slot_image(42, "logo", 5)
        await steamgrid.prune_conflicts.release_lease(first["prune_lease_token"])
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 5, _sha(b"image 5"))
        again = await steamgrid.service.choose_artwork_slot_image(42, "logo", 5)
        await steamgrid.prune_conflicts.release_lease(again["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 5, None)

        assert _choice(uow, ArtworkSlot.LOGO).image_id == 5

    @pytest.mark.asyncio
    async def test_a_late_refusal_after_steam_took_it_changes_no_choice(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300))
        a = await steamgrid.service.choose_artwork_slot_image(42, "logo", 5)
        await steamgrid.prune_conflicts.release_lease(a["prune_lease_token"])
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 5")
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 5, _sha(b"image 5"))

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 5, None)

        rows = _slot_rows(await steamgrid.service.get_artwork_slots(42, _APP_ID))
        assert rows["logo"] == ("chosen", "chosen", 5)

    @pytest.mark.asyncio
    async def test_a_late_refusal_of_an_older_chosen_image_keeps_the_newer_choice(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300), (7, 800, 300))
        for image_id in (6, 7):
            answer = await steamgrid.service.choose_artwork_slot_image(42, "logo", image_id)
            await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        assert _choice(uow, ArtworkSlot.LOGO).image_id == 7

    @pytest.mark.asyncio
    async def test_a_refused_image_that_is_no_longer_the_choice_changes_no_choice(self, steamgrid, uow, slot_ready):
        _choose(uow, ArtworkSlot.LOGO, 9)
        with uow:
            uow.artwork_slot_handovers.save(
                ArtworkSlotHandover.hand_over(
                    rom_id=42,
                    slot=ArtworkSlot.LOGO,
                    image_id=6,
                    image_sha256="sha",
                    handed_at=1.0,
                    previous_choice_id=5,
                )
            )

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        assert _choice(uow, ArtworkSlot.LOGO).image_id == 9

    @pytest.mark.asyncio
    async def test_the_hand_over_keeps_the_choice_the_press_replaced(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300), (6, 800, 300))
        _choose(uow, ArtworkSlot.LOGO, 5)

        answer = await steamgrid.service.choose_artwork_slot_image(42, "logo", 6)

        assert (_handover(uow, ArtworkSlot.LOGO).image_id, _handover(uow, ArtworkSlot.LOGO).previous_choice_id) == (
            6,
            5,
        )
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_the_refused_image_leaves_the_cache(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        answer = await steamgrid.service.choose_artwork_slot_image(42, "logo", 6)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        assert _cached_path(sgdb_artwork_cache, 42, "logo", 6) not in sgdb_artwork_cache.files

    @pytest.mark.asyncio
    async def test_a_refused_image_nobody_chose_stays_in_the_cache(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        await steamgrid.service.refresh_sgdb_artwork(42, 9999)

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        assert _cached_path(sgdb_artwork_cache, 42, "logo", 6) in sgdb_artwork_cache.files


class TestTheChoiceTheTabReadsFirst:
    """Round 4: the press's own previous choice, a version switch's chosen image, and only the refused file going."""

    @pytest.mark.asyncio
    async def test_a_choice_changed_since_an_earlier_press_is_not_given_back(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300), (5, 800, 300))
        for press in (5, 6):
            answer = await steamgrid.service.choose_artwork_slot_image(42, "logo", press)
            await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])
            if press == 5:
                await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 5, _sha(b"image 5"))
        automatic = await steamgrid.service.set_artwork_slot_automatic(42, "logo")
        await steamgrid.prune_conflicts.release_lease(automatic["prune_lease_token"])
        again = await steamgrid.service.choose_artwork_slot_image(42, "logo", 6)
        await steamgrid.prune_conflicts.release_lease(again["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        assert _choice(uow, ArtworkSlot.LOGO) is None

    @pytest.mark.asyncio
    async def test_a_version_switchs_chosen_cover_steam_refuses_stays_chosen_and_cached(
        self, steamgrid, uow, slot_ready, sgdb_artwork_cache
    ):
        _choose(uow, ArtworkSlot.COVER, 5)
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "cover", 5)] = b"chosen cover"
        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 0)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

        await steamgrid.service.report_sgdb_slot_artwork(42, "cover", "refused", 9999, 5, None)

        assert _choice(uow, ArtworkSlot.COVER).image_id == 5
        assert _cached_path(sgdb_artwork_cache, 42, "cover", 5) in sgdb_artwork_cache.files

    @pytest.mark.asyncio
    async def test_only_the_refused_images_file_leaves_the_cache(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        answer = await steamgrid.service.choose_artwork_slot_image(42, "logo", 6)
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 5)] = b"another file of the slot"
        steamgrid.service._files._slot_cache_names = None

        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "refused", 9999, 6, None)

        assert _cached_path(sgdb_artwork_cache, 42, "logo", 6) not in sgdb_artwork_cache.files
        assert _cached_path(sgdb_artwork_cache, 42, "logo", 5) in sgdb_artwork_cache.files

    @pytest.mark.asyncio
    async def test_a_refused_chosen_cover_read_says_it_was_chosen(self, steamgrid, uow, slot_ready):
        _choose(uow, ArtworkSlot.COVER, 6)
        steamgrid.settings["steamgriddb_api_key"] = "k\u00e9y\u2603"

        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 0)

        assert (answer["base64"], answer["chosen"]) == (None, True)


class TestTheChoiceAGameChangeDiscards:
    """D17 against the re-review: what the discard leaves for the automatic apply, and what it never touches."""

    def _seed(self, uow, home, steamgrid):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _grid_dir(home)
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=9999)
        with uow:
            uow.roms.set_chosen_sgdb_id(42, 8888)

    @pytest.mark.asyncio
    async def test_the_automatic_apply_after_it_serves_romms_game_not_the_old_choice(
        self, steamgrid, uow, home, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        self._seed(uow, home, steamgrid)
        _choose(uow, ArtworkSlot.LOGO, 77)
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 77)] = b"old game's chosen logo"
        _seed_candidates(fake_steamgrid_db_api, "logo", (1, 800, 300))

        game = await steamgrid.service.get_artwork_game(42)
        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 2)

        assert game["superseded"]["slots"] == ["logo"]
        assert (answer["image_id"], base64.b64decode(answer["base64"])) == (1, b"image 1")
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_cover_set_in_steam_is_not_named_and_not_kept(self, steamgrid, uow, home):
        self._seed(uow, home, steamgrid)
        _choose(uow, ArtworkSlot.COVER, 5)
        _choose(uow, ArtworkSlot.LOGO, 77)
        steamgrid.cover.set_in_steam.add(42)

        answer = await steamgrid.service.get_artwork_game(42)

        assert answer["superseded"]["slots"] == ["logo"]
        assert _choice(uow, ArtworkSlot.COVER) is None
        assert steamgrid.cover.reloaded == []

    @pytest.mark.asyncio
    async def test_a_dropped_cover_choice_takes_the_record_of_the_image_it_set(self, steamgrid, uow, home):
        self._seed(uow, home, steamgrid)
        _choose(uow, ArtworkSlot.COVER, 5)
        _record_set(uow, ArtworkSlot.COVER, 5, b"chosen cover")
        _choose(uow, ArtworkSlot.LOGO, 77)
        steamgrid.cover.set_in_steam.add(42)

        await steamgrid.service.get_artwork_game(42)

        assert _outcome(uow, ArtworkSlot.COVER) is None

    @pytest.mark.asyncio
    async def test_a_steamgriddb_cover_loses_its_cached_image_for_the_re_pick(
        self, steamgrid, uow, home, sgdb_artwork_cache
    ):
        self._seed(uow, home, steamgrid)
        _show_tenders(_grid_dir(home), uow, ArtworkSlot.COVER, 6)
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "cover", 6)] = b"image 6"
        _choose(uow, ArtworkSlot.LOGO, 77)

        answer = await steamgrid.service.get_artwork_game(42)

        assert answer["superseded"]["slots"] == ["logo"]
        assert _cached_path(sgdb_artwork_cache, 42, "cover", 6) not in sgdb_artwork_cache.files

    def test_a_game_chosen_since_the_read_is_not_discarded(self, steamgrid, uow, home):
        self._seed(uow, home, steamgrid)
        _choose(uow, ArtworkSlot.LOGO, 77)

        discarded = steamgrid.service._slots.discard_superseded_choices_io(42, 9999, 7777)

        assert discarded == set()
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77
        with uow:
            assert uow.roms.get(42).chosen_sgdb_id == 8888

    @pytest.mark.asyncio
    async def test_the_tabs_read_discards_nothing(self, steamgrid, uow, home):
        self._seed(uow, home, steamgrid)
        _choose(uow, ArtworkSlot.LOGO, 77)

        answer = await steamgrid.service.get_artwork_slots(42, _APP_ID)

        assert "superseded" not in answer
        assert _choice(uow, ArtworkSlot.LOGO).image_id == 77
        with uow:
            assert uow.roms.get(42).chosen_sgdb_id == 8888


class TestAVersionSwitchWritesTheChosenCover:
    """D16 for the cover: asset type 0 serves the cover the user chose, and nothing without a choice."""

    @pytest.mark.asyncio
    async def test_a_chosen_cover_is_served_and_handed_over(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900), (6, 600, 900))
        _choose(uow, ArtworkSlot.COVER, 6)

        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 0)

        assert (answer["image_id"], base64.b64decode(answer["base64"])) == (6, b"image 6")
        assert _handover(uow, ArtworkSlot.COVER).image_id == 6
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    async def test_a_chosen_cover_that_cannot_be_served_says_it_was_chosen(self, steamgrid, uow, slot_ready):
        _choose(uow, ArtworkSlot.COVER, 6)
        steamgrid.settings["steamgriddb_api_key"] = ""

        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 0)

        assert (answer["base64"], answer["chosen"]) == (None, True)

    @pytest.mark.asyncio
    async def test_without_a_chosen_cover_nothing_is_served(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))

        answer = await steamgrid.service.get_sgdb_artwork_base64(42, 0)

        assert answer == {"base64": None, "no_api_key": False, "chosen": False}
        assert fake_steamgrid_db_api.requested_paths == []


# ── keeping automatic artwork current ─────────────────────────────────────


def _show_tenders(grid, uow, slot: ArtworkSlot, image_id: int) -> None:
    """Steam's grid directory shows Tender's SteamGridDB image *image_id* for ROM 42's *slot*, with its record."""
    data = f"image {image_id}".encode()
    name = {
        ArtworkSlot.WIDE_GRID: f"{_APP_ID}.png",
        ArtworkSlot.LOGO: f"{_APP_ID}_logo.png",
        ArtworkSlot.HERO: f"{_APP_ID}_hero.png",
        ArtworkSlot.COVER: f"{_APP_ID}p.png",
    }[slot]
    (grid / name).write_bytes(data)
    _record_set(uow, slot, image_id, data)


def _checked_at(uow) -> float | None:
    with uow:
        return uow.roms.get(42).artwork_checked_at


def _check_was(uow, at: float | None) -> None:
    with uow:
        uow.roms.set_artwork_checked_at(42, at)


class _ClockThatWaitsForASecondReader(FakeClock):
    """A clock whose first reading waits, briefly, for a second one, so two readers overlap if nothing serialises them.

    The check reads the clock between reading a ROM's time and recording its
    own; a second check let in meanwhile reads the time the first has not
    recorded yet.
    """

    def __init__(self) -> None:
        super().__init__()
        self._second = threading.Event()
        self._readings = 0

    def time(self) -> float:
        self._readings += 1
        if self._readings == 1:
            self._second.wait(timeout=0.3)
        else:
            self._second.set()
        return super().time()


class TestKeepingArtworkCurrent:
    """D5, D22: a page visit checks the automatic slots against SteamGridDB, at most once in seven days."""

    @pytest.mark.asyncio
    async def test_a_new_pick_is_cached_and_named_and_the_time_recorded(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300), (5, 800, 300))

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": ["logo"]}
        assert sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 6)] == b"image 6"
        assert _checked_at(uow) == steamgrid.clock.time()

    @pytest.mark.asyncio
    async def test_the_same_pick_downloads_nothing(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _seed_candidates(fake_steamgrid_db_api, "logo", (5, 800, 300), (6, 800, 300))

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": []}
        assert fake_steamgrid_db_api.downloaded == []
        assert _checked_at(uow) == steamgrid.clock.time()

    @pytest.mark.asyncio
    async def test_a_pick_the_cache_already_holds_is_not_downloaded_again(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 6)] = b"image 6"

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer["replaced"] == ["logo"]
        assert fake_steamgrid_db_api.downloaded == []

    @pytest.mark.asyncio
    async def test_inside_the_seven_days_nothing_is_asked(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        earlier = steamgrid.clock.time() - (7 * 24 * 60 * 60 - 60)
        _check_was(uow, earlier)

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "not_due", "replaced": []}
        assert fake_steamgrid_db_api.requested_paths == []
        assert _checked_at(uow) == earlier

    @pytest.mark.asyncio
    async def test_after_seven_days_it_is_asked_again(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        _check_was(uow, steamgrid.clock.time() - 7 * 24 * 60 * 60)

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": ["logo"]}
        assert _checked_at(uow) == steamgrid.clock.time()

    @pytest.mark.asyncio
    async def test_the_visit_after_its_own_write_checks_nothing(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        await steamgrid.service.keep_artwork_current(42)
        asked = list(fake_steamgrid_db_api.requested_paths)
        # The write that makes Steam rebuild the page: the new logo set and reported.
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 6")
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 6, _sha(b"image 6"))
        steamgrid.clock.advance(5)

        again = await steamgrid.service.keep_artwork_current(42)

        assert again == {"status": "not_due", "replaced": []}
        assert fake_steamgrid_db_api.requested_paths == asked

    @pytest.mark.asyncio
    async def test_two_visits_at_once_check_once(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _seed_candidates(fake_steamgrid_db_api, "logo", (6, 800, 300))
        steamgrid.service._keeping_current._clock = _ClockThatWaitsForASecondReader()

        answers = await asyncio.gather(
            steamgrid.service.keep_artwork_current(42), steamgrid.service.keep_artwork_current(42)
        )

        assert sorted(answer["status"] for answer in answers) == ["checked", "not_due"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            SgdbRateLimitedError("slow down"),
            SgdbApiError(500, "server error"),
            SgdbApiError(401, "refused"),
            SgdbKeyUnsendableError(),
            OSError("no connection"),
        ],
        ids=["429", "5xx", "refused-key", "unsendable-key", "no-connection"],
    )
    async def test_a_missing_answer_puts_the_time_back(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, error):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        earlier = steamgrid.clock.time() - 30 * 24 * 60 * 60
        _check_was(uow, earlier)
        fake_steamgrid_db_api.request_side_effect = error

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "unanswered", "replaced": []}
        assert _checked_at(uow) == earlier

    @pytest.mark.asyncio
    async def test_a_first_check_left_unanswered_leaves_the_game_never_checked(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        fake_steamgrid_db_api.request_side_effect = SgdbRateLimitedError("slow down")

        await steamgrid.service.keep_artwork_current(42)

        assert _checked_at(uow) is None

    @pytest.mark.asyncio
    async def test_an_unsuccessful_answer_puts_the_time_back(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", {"success": False})

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer["status"] == "unanswered"
        assert _checked_at(uow) is None

    @pytest.mark.asyncio
    async def test_a_download_that_failed_puts_the_time_back(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        fake_steamgrid_db_api.seed_raw_response(
            "/logos/game/9999", {"success": True, "data": [{"id": 6, "url": "https://cdn.example/6.png"}]}
        )
        fake_steamgrid_db_api.download_image_return = False

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "unanswered", "replaced": []}
        assert _checked_at(uow) is None

    @pytest.mark.asyncio
    async def test_a_slot_replaced_before_one_left_unanswered_stays_replaced(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.WIDE_GRID, 5)
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 7)
        _seed_candidates(fake_steamgrid_db_api, "grid", (6, 920, 430))
        fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", {"success": False})

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "unanswered", "replaced": ["grid"]}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "listing",
        [{"success": True, "data": []}, None],
        ids=["no-images", "no-listing"],
    )
    async def test_no_images_is_an_answer(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, listing):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        if listing is None:
            fake_steamgrid_db_api.request_side_effect = SgdbApiError(404, "Game not found.")
        else:
            fake_steamgrid_db_api.seed_raw_response("/logos/game/9999", listing)

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": []}
        assert _checked_at(uow) == steamgrid.clock.time()

    @pytest.mark.asyncio
    async def test_a_chosen_slot_a_slot_set_in_steam_and_an_empty_one_are_not_asked(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        _choose(uow, ArtworkSlot.LOGO, 5)
        (slot_ready / f"{_APP_ID}_hero.jpg").write_bytes(b"set by hand")

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": []}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_romms_cover_is_not_asked(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        (slot_ready / f"{_APP_ID}p.png").write_bytes(b"romm's cover")

        await steamgrid.service.keep_artwork_current(42)

        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_a_cover_tender_picked_from_steamgriddb_is_kept_current(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.COVER, 5)
        _seed_cover_candidates(fake_steamgrid_db_api, (8, 342, 482), (6, 600, 900), (5, 660, 930))

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": ["cover"]}
        assert _cached_path(sgdb_artwork_cache, 42, "cover", 6) in sgdb_artwork_cache.files

    @pytest.mark.asyncio
    async def test_an_icon_tender_set_is_kept_current(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        icon = slot_ready / f"{_APP_ID}_icon.png"
        icon.write_bytes(b"icon 5")
        _write_shortcut_icon_paths(slot_ready, {_APP_ID: str(icon)})
        _record_set(uow, ArtworkSlot.ICON, 5, b"icon 5")
        _seed_candidates(fake_steamgrid_db_api, "icon", (6, 256, 256))

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": ["icon"]}
        assert _cached_path(sgdb_artwork_cache, 42, "icon", 6) in sgdb_artwork_cache.files

    @pytest.mark.asyncio
    async def test_without_a_key_nothing_is_asked_or_recorded(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        steamgrid.settings["steamgriddb_api_key"] = ""

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "no_api_key", "replaced": []}
        assert _checked_at(uow) is None

    @pytest.mark.asyncio
    async def test_without_a_steamgriddb_game_nothing_is_asked_or_recorded(self, steamgrid, uow, home):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        _seed_rom(uow, 42, app_id=_APP_ID, sgdb_id=None)

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "no_sgdb_game", "replaced": []}
        assert _checked_at(uow) is None

    @pytest.mark.asyncio
    async def test_an_unsynced_rom_is_not_checked(self, steamgrid, uow):
        steamgrid.settings["steamgriddb_api_key"] = "some-key"
        steamgrid.library._pending_sync[42] = {"sgdb_id": 9999}

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "not_synced", "replaced": []}

    @pytest.mark.asyncio
    async def test_a_key_no_header_can_carry_is_refused(self, steamgrid, slot_ready):
        steamgrid.settings["steamgriddb_api_key"] = "bad\nkey"
        checking = steamgrid.service.keep_artwork_current(42)

        with pytest.raises(Refused) as refused:
            await checking

        assert refused.value.reason == "auth_failed"

    @pytest.mark.asyncio
    async def test_a_running_cleanup_refuses_it(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        steamgrid.prune_conflicts.register_run("held-run")

        with _refused_by_conflict_rule("prune_active"):
            await steamgrid.service.keep_artwork_current(42)

        assert fake_steamgrid_db_api.requested_paths == []
        assert _checked_at(uow) is None

    @pytest.mark.asyncio
    async def test_a_replaced_cover_is_served_as_the_other_slots_are(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.COVER, 5)
        _seed_cover_candidates(fake_steamgrid_db_api, (6, 600, 900))
        await steamgrid.service.keep_artwork_current(42)

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "cover", True)

        assert (answer["status"], answer["image_id"], base64.b64decode(answer["base64"])) == ("image", 6, b"image 6")
        assert _handover(uow, ArtworkSlot.COVER).image_id == 6
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    def test_a_withdrawal_leaves_a_later_time_alone(self, steamgrid, uow, slot_ready):
        from services.steamgrid._current import _Started

        _check_was(uow, 500.0)

        steamgrid.service._keeping_current._withdraw_io(42, _Started(at=400.0, previous=None))

        assert _checked_at(uow) == 500.0


class TestTheCoverWhereRommHasNone:
    """D23, D27: an empty cover slot is filled with Tender's pick among SteamGridDB's covers, as the other slots are."""

    @pytest.mark.asyncio
    async def test_an_empty_cover_is_served_tenders_pick(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_cover_candidates(fake_steamgrid_db_api, (8, 342, 482), (7, 660, 930), (5, 600, 900))

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "cover")

        assert (answer["status"], answer["image_id"], base64.b64decode(answer["base64"])) == ("image", 5, b"image 5")
        assert _handover(uow, ArtworkSlot.COVER).image_id == 5
        await steamgrid.prune_conflicts.release_lease(answer["prune_lease_token"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", [f"{_APP_ID}p.png", f"{_APP_ID}p.jpg"])
    async def test_a_cover_in_the_grid_folder_is_left_alone(self, steamgrid, slot_ready, fake_steamgrid_db_api, name):
        (slot_ready / name).write_bytes(b"a cover")

        answer = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "cover")

        assert answer == {"status": "skip"}
        assert fake_steamgrid_db_api.requested_paths == []

    @pytest.mark.asyncio
    async def test_no_cover_on_steamgriddb_is_recorded_and_rules_it_out_for_a_week(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _seed_cover_candidates(fake_steamgrid_db_api)

        first = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "cover")
        again = await steamgrid.service.get_sgdb_slot_artwork(42, _APP_ID, "cover")

        assert (first, again) == ({"status": "none"}, {"status": "skip"})
        assert _outcome(uow, ArtworkSlot.COVER).outcome is SlotOutcome.NONE
        assert len(fake_steamgrid_db_api.requested_paths) == 1

    @pytest.mark.asyncio
    async def test_refresh_artwork_forgets_that_steamgriddb_had_none(self, steamgrid, uow, slot_ready):
        with uow:
            uow.artwork_slot_outcomes.save(
                ArtworkSlotOutcome.record_none(rom_id=42, slot=ArtworkSlot.COVER, sgdb_id=9999, recorded_at=1.0)
            )

        await steamgrid.service.get_sgdb_resolution(42)

        assert _outcome(uow, ArtworkSlot.COVER) is None

    @pytest.mark.asyncio
    async def test_steam_refusing_the_cover_records_nothing(self, steamgrid, uow, slot_ready):
        await steamgrid.service.report_sgdb_slot_artwork(42, "cover", "refused", 9999, 5, None)

        assert _outcome(uow, ArtworkSlot.COVER) is None

    def test_the_job_gets_the_cover_into_the_cache(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _seed_cover_candidates(fake_steamgrid_db_api, (5, 600, 900))

        assert steamgrid.service.do_fetch_slot_artwork(42, ArtworkSlot.COVER) is SlotAnswer.READY

    @pytest.mark.asyncio
    async def test_a_change_of_game_picks_a_steamgriddb_cover_anew_under_it(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.COVER, 5)
        fake_steamgrid_db_api.seed_raw_response(
            "/grids/game/7777?dimensions=600x900",
            {"success": True, "data": [{"id": 6, "url": "https://cdn.example/6.png", "width": 600, "height": 900}]},
        )
        fake_steamgrid_db_api.seed_image_bytes("https://cdn.example/6.png", b"image 6")

        await steamgrid.service.apply_sgdb_game_id(42, 7777)

        assert sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "cover", 6)] == b"image 6"

    @pytest.mark.asyncio
    async def test_a_change_of_game_leaves_romms_cover_alone(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        (slot_ready / f"{_APP_ID}p.png").write_bytes(b"romm's cover")

        await steamgrid.service.apply_sgdb_game_id(42, 7777)

        covers_asked = [path for path in fake_steamgrid_db_api.requested_paths if "dimensions=600x900" in path]
        assert covers_asked == []


class TestWhatAVisitLeftNotHandedOver:
    """D29: a visit names every automatic slot downloaded anew and not yet set, inside the seven days too."""

    @pytest.mark.asyncio
    async def test_a_handover_cut_short_finishes_at_the_next_visit(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.WIDE_GRID, 5)
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 7)
        _seed_candidates(fake_steamgrid_db_api, "grid", (6, 920, 430))
        _seed_candidates(fake_steamgrid_db_api, "logo", (8, 800, 300))
        first = await steamgrid.service.keep_artwork_current(42)
        # Steam took the wide grid; the page was rebuilt before the logo was handed over.
        (slot_ready / f"{_APP_ID}.png").write_bytes(b"image 6")
        await steamgrid.service.report_sgdb_slot_artwork(42, "grid", "set", 9999, 6, _sha(b"image 6"))
        asked = len(fake_steamgrid_db_api.requested_paths)

        second = await steamgrid.service.keep_artwork_current(42)

        assert first == {"status": "checked", "replaced": ["grid", "logo"]}
        assert second == {"status": "not_due", "replaced": ["logo"]}
        assert len(fake_steamgrid_db_api.requested_paths) == asked

    @pytest.mark.asyncio
    async def test_a_visit_with_nothing_left_names_nothing(self, steamgrid, uow, slot_ready, fake_steamgrid_db_api):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 7)
        _seed_candidates(fake_steamgrid_db_api, "logo", (8, 800, 300))
        await steamgrid.service.keep_artwork_current(42)
        (slot_ready / f"{_APP_ID}_logo.png").write_bytes(b"image 8")
        await steamgrid.service.report_sgdb_slot_artwork(42, "logo", "set", 9999, 8, _sha(b"image 8"))

        again = await steamgrid.service.keep_artwork_current(42)

        assert again == {"status": "not_due", "replaced": []}

    @pytest.mark.asyncio
    async def test_a_due_check_names_what_was_left_beside_what_it_replaced(
        self, steamgrid, uow, slot_ready, fake_steamgrid_db_api, sgdb_artwork_cache
    ):
        _show_tenders(slot_ready, uow, ArtworkSlot.WIDE_GRID, 5)
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 7)
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 8)] = b"image 8"
        _seed_candidates(fake_steamgrid_db_api, "grid", (6, 920, 430))
        _seed_candidates(fake_steamgrid_db_api, "logo", (8, 800, 300))

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "checked", "replaced": ["grid", "logo"]}

    @pytest.mark.asyncio
    async def test_a_chosen_slot_is_never_left_over(self, steamgrid, uow, slot_ready, sgdb_artwork_cache):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 7)
        _choose(uow, ArtworkSlot.LOGO, 7)
        sgdb_artwork_cache.files[_cached_path(sgdb_artwork_cache, 42, "logo", 8)] = b"image 8"
        _check_was(uow, steamgrid.clock.time())

        answer = await steamgrid.service.keep_artwork_current(42)

        assert answer == {"status": "not_due", "replaced": []}


class TestACheckThatRaises:
    @pytest.mark.asyncio
    async def test_its_time_is_taken_back(self, steamgrid, uow, slot_ready, monkeypatch):
        _show_tenders(slot_ready, uow, ArtworkSlot.LOGO, 5)
        earlier = steamgrid.clock.time() - 30 * 24 * 60 * 60
        _check_was(uow, earlier)

        def boom(_rom_id):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(steamgrid.service._slots, "kept_current_views_io", boom)
        checking = steamgrid.service.keep_artwork_current(42)

        with pytest.raises(RuntimeError):
            await checking

        assert _checked_at(uow) == earlier

    def test_a_withdrawal_for_a_rom_gone_meanwhile_does_nothing(self, steamgrid, uow, slot_ready):
        from services.steamgrid._current import _Started

        with uow:
            uow.roms.delete(42)

        steamgrid.service._keeping_current._withdraw_io(42, _Started(at=400.0, previous=None))

        with uow:
            assert uow.roms.get(42) is None
