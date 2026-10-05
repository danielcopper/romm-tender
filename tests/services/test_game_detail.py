import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _factories import _make_conflict_rules
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_core_info_provider import FakeCoreInfoProvider
from fakes.fake_firmware_resolver import FakeFirmwareResolver
from fakes.fake_path_exists_reader import FakePathExistsReader
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_retrodeck_paths import FakeRetroDeckPaths
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock

from adapters.firmware_file import FirmwareFileAdapter
from domain.rom_save_sync_state import FileSyncState
from services.achievements import AchievementsService, AchievementsServiceConfig
from services.firmware import FirmwareService, FirmwareServiceConfig
from services.game_detail import GameDetailService, GameDetailServiceConfig

# RetroDECK ROMs root the target-path probe resolves against. Never touched on
# disk — the probe is a fake set of paths, so the base only has to be absolute
# and normalized for the containment guard to accept a child of it.
_ROMS_BASE = "/fake/retrodeck/roms"


@pytest.fixture
def clock():
    """FakeClock pinned to a fixed synthetic instant — drives all TTL comparisons deterministically."""
    return FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def active_core_resolver():
    """Per-ROM active-core resolver fake the game-detail BIOS path resolves through.

    Defaults to ``(None, None)`` (system default); result-flip tests seed
    ``per_rom`` so two ROMs on one platform resolve to different cores.
    """
    return FakeActiveCoreResolver(default=(None, None))


@pytest.fixture
def path_probe():
    """Existence probe backing the page's single target-path ``stat``.

    Empty by default, so an uninstalled ROM reports nothing in the way; a test
    that exercises the occupied case adds the exact path to ``paths``.
    """
    return FakePathExistsReader()


@dataclass
class GameDetailHarness:
    """The game-detail service, the services it reads, and the seams a test drives.

    ``uow`` is the one :class:`FakeUnitOfWork` the service reads, so a row a
    test seeds there is the row the page is built from. The candidate probe
    answers ``candidate_present`` (off unless a test stages it, so a test that
    does not finds no adoption candidate) and records each call on
    ``candidate_probe_calls`` — which is how "an installed ROM does no folder
    read" is stated as an absence rather than inferred.
    """

    service: GameDetailService
    achievements: AchievementsService
    firmware: FirmwareService
    uow: FakeUnitOfWork
    settings: dict[str, Any]
    candidate_present: bool = False
    candidate_probe_calls: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture
def game_detail(clock, active_core_resolver, path_probe) -> GameDetailHarness:
    """The game-detail service over one shared UoW and the pinned clock."""
    settings: dict[str, Any] = {
        "romm_url": "http://romm.local",
        "romm_user": "user",
        "romm_pass": "pass",
        "enabled_platforms": {},
        "log_level": "warn",
        "save_sync_enabled": False,
    }
    uow = FakeUnitOfWork()

    achievements = AchievementsService(
        config=AchievementsServiceConfig(
            romm_api=MagicMock(),
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            loop=running_loop(),
            logger=logging.getLogger("test"),
            clock=FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC)),
            log_debug=lambda msg: None,
        ),
    )

    firmware = FirmwareService(
        config=FirmwareServiceConfig(
            romm_api=MagicMock(),
            loop=running_loop(),
            logger=logging.getLogger("test"),
            clock=FakeClock(now=datetime(2026, 1, 1, tzinfo=UTC)),
            firmware_file_store=FirmwareFileAdapter(),
            firmware_resolver=FakeFirmwareResolver(),
            platform_firmware_resolver=FakeFirmwareResolver(),
            retrodeck_paths=FakeRetroDeckPaths(),
            core_info=FakeCoreInfoProvider(),
            resolve_system=lambda platform_slug, platform_fs_slug=None: platform_slug,
            platform_core_reader=FakePlatformCoreReader(),
            uow_factory=FakeUnitOfWorkFactory(),
            conflict_rules=_make_conflict_rules(),
        ),
    )

    def candidate_probe(platform_slug: str, fs_name: str) -> bool:
        harness.candidate_probe_calls.append((platform_slug, fs_name))
        return harness.candidate_present

    service = GameDetailService(
        config=GameDetailServiceConfig(
            settings=settings,
            loop=running_loop(),
            logger=logging.getLogger("test"),
            clock=clock,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            bios_checker=firmware,
            achievements=achievements,
            active_core=active_core_resolver,
            path_exists=path_probe,
            retrodeck_paths=FakeRetroDeckPaths(roms=_ROMS_BASE),
            resolve_system=lambda platform_slug, platform_fs_slug=None: platform_fs_slug or platform_slug,
            candidate_probe=candidate_probe,
        ),
    )
    harness = GameDetailHarness(
        service=service, achievements=achievements, firmware=firmware, uow=uow, settings=settings
    )
    return harness


def _seed_rom(
    game_detail,
    rom_id,
    *,
    app_id,
    name="Game",
    platform_slug="snes",
    fs_name="",
    ra_id=None,
    regions=(),
):
    """Seed one ``Rom`` row into the shared UoW (the synced-shortcut registry).

    *app_id* is the bound Steam shortcut id (``None`` = unbound). Children
    (install / save state / metadata) must be seeded after the Rom for the FK.
    """
    from domain.rom import Rom

    rom = Rom(
        rom_id=rom_id,
        platform_slug=platform_slug,
        name=name,
        fs_name=fs_name or f"game_{rom_id}.sfc",
        shortcut_app_id=app_id,
        last_synced_at="2025-01-01T00:00:00",
        ra_id=ra_id,
        regions=regions,
    )
    with game_detail.uow:
        game_detail.uow.roms.save(rom)


def _seed_platform_names(game_detail, mapping):
    """Seed the offline ``platform_slug → display_name`` cache row in kv_config."""
    import json

    with game_detail.uow:
        game_detail.uow.kv_config.set("platform_names", json.dumps(mapping))


def _seed_save_state(game_detail, rom_id, *, files, last_sync_check_at):
    """Seed a ``RomSaveSyncState`` for *rom_id* (Rom must already exist for the FK)."""
    from domain.rom_save_sync_state import RomSaveSyncState

    with game_detail.uow:
        game_detail.uow.rom_save_sync_states.save(
            rom_id,
            RomSaveSyncState(files=files, last_sync_check_at=last_sync_check_at),
        )


def _seed_metadata(game_detail, rom_id, *, cached_at, summary="", genres=(), app_id=None, platform_slug="snes"):
    """Seed cached metadata for *rom_id* in the shared UoW (Rom first for the FK)."""
    from domain.rom import Rom
    from domain.rom_metadata import RomMetadata

    rom = Rom(
        rom_id=rom_id,
        platform_slug=platform_slug,
        name=f"Game {rom_id}",
        fs_name=f"game_{rom_id}.sfc",
        shortcut_app_id=app_id if app_id is not None else 1000 + rom_id,
        last_synced_at="2025-01-01T00:00:00",
    )
    meta = RomMetadata(
        summary=summary,
        genres=tuple(genres),
        companies=(),
        first_release_date=None,
        average_rating=None,
        game_modes=(),
        player_count="",
        cached_at=cached_at,
        steam_categories=(),
    )
    with game_detail.uow:
        game_detail.uow.roms.save(rom)
        game_detail.uow.rom_metadata.save(rom_id, meta)


def _install_rom(game_detail, tmp_path, rom_id=42, system="gba", file_name="pokemon.gba"):
    """Helper: seed a ``RomInstall`` record (Rom must already exist for the FK)."""
    from domain.rom_install import RomInstall

    install_dir = tmp_path / "retrodeck" / "roms" / system
    with game_detail.uow:
        game_detail.uow.rom_installs.save(
            RomInstall(
                rom_id=rom_id,
                file_path=str(install_dir / file_name),
                rom_dir=None,
                platform_slug=system,
                system=system,
                installed_at="2025-01-01T00:00:00",
            )
        )


def _create_save(tmp_path, system="gba", rom_name="pokemon", content=b"\x00" * 1024, ext=".srm"):
    """Helper: create a save file on disk."""
    saves_dir = tmp_path / "retrodeck" / "saves" / system
    saves_dir.mkdir(parents=True, exist_ok=True)
    save_file = saves_dir / (rom_name + ext)
    save_file.write_bytes(content)
    return save_file


def _server_save(
    save_id=100, rom_id=42, filename="pokemon.srm", updated_at="2026-02-17T06:00:00Z", file_size_bytes=1024
):
    """Helper: build a server save response dict."""
    return {
        "id": save_id,
        "rom_id": rom_id,
        "file_name": filename,
        "updated_at": updated_at,
        "file_size_bytes": file_size_bytes,
        "emulator": "retroarch",
        "download_path": f"/saves/{filename}",
    }


class TestGetCachedGameDetailFound:
    """Test get_cached_game_detail when the ROM is bound in ``uow.roms``."""

    @pytest.mark.asyncio
    async def test_found_with_full_data(self, game_detail, tmp_path):
        """All data present: rom, install, save status, metadata, platform-name cache."""
        # _seed_metadata seeds the Rom (123, snes, app_id 99999) + its metadata.
        _seed_metadata(
            game_detail,
            123,
            cached_at=100,
            summary="Classic SNES platformer",
            genres=("Platformer",),
            app_id=99999,
            platform_slug="snes",
        )
        _seed_platform_names(game_detail, {"snes": "Super Nintendo"})
        _install_rom(game_detail, tmp_path, rom_id=123, system="snes", file_name="smw.sfc")
        game_detail.settings["save_sync_enabled"] = True
        _seed_save_state(
            game_detail,
            123,
            files={"smw.srm": FileSyncState(last_sync_at="2025-01-01T00:00:00Z", last_sync_hash="abc123")},
            last_sync_check_at="2025-01-01T00:00:00Z",
        )

        result = await game_detail.service.get_cached_game_detail(99999)

        assert result["found"] is True
        assert result["rom_id"] == 123
        assert result["rom_name"] == "Game 123"
        assert result["platform_slug"] == "snes"
        assert result["platform_name"] == "Super Nintendo"
        assert result["installed"] is True
        assert result["rom_file"] == "smw.sfc"
        assert result["save_sync_enabled"] is True
        assert len(result["save_status"]["files"]) == 1
        assert result["save_status"]["files"][0]["filename"] == "smw.srm"
        assert result["save_status"]["files"][0]["status"] == "synced"
        assert result["save_status"]["last_sync_check_at"] == "2025-01-01T00:00:00Z"
        assert result["metadata"]["summary"] == "Classic SNES platformer"
        assert result["bios_status"] is None


class TestGetCachedGameDetailLeavesTheLoopThread:
    """The read runs on a worker, never on the loop thread; why is on
    ``GameDetailService.get_cached_game_detail``."""

    @pytest.mark.asyncio
    async def test_the_read_runs_on_a_worker_thread(self, game_detail):
        _seed_rom(game_detail, 42, app_id=50000)
        inner = game_detail.service._uow_factory
        seen: list[int] = []

        def recording_uow_factory():
            seen.append(threading.get_ident())
            return inner()

        game_detail.service._uow_factory = recording_uow_factory

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["found"] is True
        assert len(seen) == 1
        assert seen[0] != threading.get_ident()


class TestGetCachedGameDetailNotFound:
    """Test get_cached_game_detail when no ROM is bound to the app_id."""

    @pytest.mark.asyncio
    async def test_not_found(self, game_detail):
        """Unknown app_id returns found=False."""
        result = await game_detail.service.get_cached_game_detail(12345)
        assert result == {"found": False}

    @pytest.mark.asyncio
    async def test_not_found_empty_registry(self, game_detail):
        """Empty roms table returns found=False."""
        result = await game_detail.service.get_cached_game_detail(1)
        assert result == {"found": False}

    @pytest.mark.asyncio
    async def test_not_found_different_app_id(self, game_detail):
        """roms has entries but none match the requested app_id."""
        _seed_rom(game_detail, 10, app_id=11111, name="Other Game", platform_slug="nes")
        result = await game_detail.service.get_cached_game_detail(99999)
        assert result == {"found": False}


class TestGetCachedGameDetailPartialData:
    """Test with missing optional data (no save status, no metadata, etc.)."""

    @pytest.mark.asyncio
    async def test_no_save_status(self, game_detail):
        """No save state for this rom returns save_status=None."""
        _seed_rom(game_detail, 10, app_id=50000, name="Zelda", platform_slug="snes")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["found"] is True
        assert result["save_status"] is None

    @pytest.mark.asyncio
    async def test_no_metadata(self, game_detail):
        """No metadata cached returns metadata=None."""
        _seed_rom(game_detail, 10, app_id=50000, name="Zelda", platform_slug="snes")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["found"] is True
        assert result["metadata"] is None

    @pytest.mark.asyncio
    async def test_no_pending_conflicts_key(self, game_detail):
        """pending_conflicts is no longer in the response (conflicts are inline)."""
        _seed_rom(game_detail, 10, app_id=50000, name="Zelda", platform_slug="snes")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["found"] is True
        assert "pending_conflicts" not in result

    @pytest.mark.asyncio
    async def test_save_sync_disabled(self, game_detail):
        """save_sync_enabled reflects the setting."""
        _seed_rom(game_detail, 10, app_id=50000, name="Zelda", platform_slug="snes")
        game_detail.settings["save_sync_enabled"] = False
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["save_sync_enabled"] is False

    @pytest.mark.asyncio
    async def test_empty_platform_slug_defaults_empty(self, game_detail):
        """A ROM with an empty platform_slug degrades platform_name to empty."""
        _seed_rom(game_detail, 10, app_id=50000, name="", platform_slug="")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["found"] is True
        assert result["rom_name"] == ""
        assert result["platform_slug"] == ""
        assert result["platform_name"] == ""

    @pytest.mark.asyncio
    async def test_platform_name_degrades_to_slug_when_cache_absent(self, game_detail):
        """No platform-name cache row → platform_name degrades to the slug."""
        _seed_rom(game_detail, 10, app_id=50000, name="Zelda", platform_slug="snes")
        # No _seed_platform_names — the kv_config cache row is absent.
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["found"] is True
        assert result["platform_slug"] == "snes"
        assert result["platform_name"] == "snes"


class TestGetCachedGameDetailInstalled:
    """Test installed vs not installed detection + rom_file resolution."""

    @pytest.mark.asyncio
    async def test_installed(self, game_detail, tmp_path):
        """ROM with a rom_installs row returns installed=True."""
        _seed_rom(game_detail, 10, app_id=50000, name="Game", platform_slug="snes")
        _install_rom(game_detail, tmp_path, rom_id=10, system="snes", file_name="game.sfc")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["installed"] is True
        assert result["rom_file"] == "game.sfc"

    @pytest.mark.asyncio
    async def test_not_installed(self, game_detail):
        """ROM without a rom_installs row returns installed=False, rom_file from fs_name."""
        _seed_rom(game_detail, 10, app_id=50000, name="Game", platform_slug="snes", fs_name="game_10.sfc")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["installed"] is False
        # No install record → rom_file falls back to Rom.fs_name.
        assert result["rom_file"] == "game_10.sfc"


class TestFileMissingAt:
    """An installed ROM whose recorded file and folder are gone names the path it was recorded at."""

    @pytest.mark.asyncio
    async def test_an_installed_rom_whose_file_is_gone_names_its_path(self, game_detail, tmp_path):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes")
        _install_rom(game_detail, tmp_path, rom_id=10, system="snes", file_name="game.sfc")

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["installed"] is True
        assert result["file_missing_at"] == str(tmp_path / "retrodeck" / "roms" / "snes" / "game.sfc")

    @pytest.mark.asyncio
    async def test_an_installed_rom_whose_file_is_there_reports_nothing(self, game_detail, tmp_path, path_probe):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes")
        _install_rom(game_detail, tmp_path, rom_id=10, system="snes", file_name="game.sfc")
        path_probe.paths.add(str(tmp_path / "retrodeck" / "roms" / "snes" / "game.sfc"))

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["file_missing_at"] is None

    @pytest.mark.asyncio
    async def test_a_folder_backed_download_is_named_by_its_folder(self, game_detail):
        from domain.rom_install import RomInstall

        _seed_rom(game_detail, 10, app_id=50000, platform_slug="psx")
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        with game_detail.uow:
            game_detail.uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=10,
                    file_path=f"{rom_dir}/FF7.m3u",
                    rom_dir=rom_dir,
                    platform_slug="psx",
                    system="psx",
                    installed_at="2025-01-01T00:00:00",
                )
            )

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["file_missing_at"] == rom_dir

    @pytest.mark.asyncio
    async def test_a_folder_that_is_still_there_is_not_missing(self, game_detail, path_probe):
        from domain.rom_install import RomInstall

        _seed_rom(game_detail, 10, app_id=50000, platform_slug="psx")
        rom_dir = f"{_ROMS_BASE}/psx/FF7"
        with game_detail.uow:
            game_detail.uow.rom_installs.save(
                RomInstall.mark_installed(
                    rom_id=10,
                    file_path=f"{rom_dir}/FF7.m3u",
                    rom_dir=rom_dir,
                    platform_slug="psx",
                    system="psx",
                    installed_at="2025-01-01T00:00:00",
                )
            )
        path_probe.paths.add(rom_dir)

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["file_missing_at"] is None

    @pytest.mark.asyncio
    async def test_a_rom_with_no_install_record_reports_nothing(self, game_detail):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes")

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["installed"] is False
        assert result["file_missing_at"] is None


class TestTargetPathOccupied:
    """The single ``stat`` this network-free page runs on an uninstalled ROM (#260).

    It exists so the page stops offering an undifferentiated Download for content
    already in place. Everything it cannot know goes quiet — it never claims.
    """

    @pytest.mark.asyncio
    async def test_false_when_nothing_is_at_the_target_path(self, game_detail):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["target_path_occupied"] is False

    @pytest.mark.asyncio
    async def test_true_when_the_target_path_exists(self, game_detail, path_probe):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        path_probe.paths.add(f"{_ROMS_BASE}/snes/game_10.sfc")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["target_path_occupied"] is True

    @pytest.mark.asyncio
    async def test_a_candidate_in_the_folder_is_reported(self, game_detail):
        # The page says so without the user pressing Download to find out (#260).
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        game_detail.candidate_present = True

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["adoption_candidate_present"] is True
        assert game_detail.candidate_probe_calls == [("snes", "game_10.sfc")]

    @pytest.mark.asyncio
    async def test_no_candidate_leaves_the_page_saying_download(self, game_detail):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["adoption_candidate_present"] is False

    @pytest.mark.asyncio
    async def test_an_occupied_target_wins_and_skips_the_folder_read(self, game_detail, path_probe):
        # Two different states, and the occupied one is the exact path this ROM
        # would claim — there is nothing left to search for.
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        path_probe.paths.add(f"{_ROMS_BASE}/snes/game_10.sfc")
        game_detail.candidate_present = True

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["target_path_occupied"] is True
        assert result["adoption_candidate_present"] is False
        assert game_detail.candidate_probe_calls == []

    @pytest.mark.asyncio
    async def test_an_installed_rom_does_no_folder_read(self, game_detail, tmp_path):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        _install_rom(game_detail, tmp_path, rom_id=10, system="snes", file_name="game.sfc")
        game_detail.candidate_present = True

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["installed"] is True
        assert result["adoption_candidate_present"] is False
        assert game_detail.candidate_probe_calls == []

    @pytest.mark.asyncio
    async def test_an_installed_rom_is_never_probed(self, game_detail, tmp_path, path_probe):
        # An install record already answers the question, so the stat is skipped
        # outright rather than run and ignored.
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        _install_rom(game_detail, tmp_path, rom_id=10, system="snes", file_name="game.sfc")
        path_probe.paths.add(f"{_ROMS_BASE}/snes/game_10.sfc")

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["installed"] is True
        assert result["target_path_occupied"] is False

    @pytest.mark.asyncio
    async def test_exactly_one_probe_per_read(self, game_detail, path_probe):
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        probed: list[str] = []
        path_probe.exists = lambda path: bool(probed.append(path))

        await game_detail.service.get_cached_game_detail(50000)

        assert len(probed) == 1

    @pytest.mark.asyncio
    async def test_false_when_the_roms_path_is_unknown(self, game_detail, path_probe):
        game_detail.service._retrodeck_paths.roms = ""
        path_probe.exists = lambda _path: True
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="game_10.sfc")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["target_path_occupied"] is False

    @pytest.mark.asyncio
    async def test_false_when_the_rom_has_no_fs_name(self, game_detail, path_probe):
        # A pre-migration row: nothing to compute a path from, so the field goes
        # quiet rather than probing the bare platform directory.
        from domain.rom import Rom

        path_probe.exists = lambda _path: True
        with game_detail.uow:
            game_detail.uow.roms.save(
                Rom(
                    rom_id=10,
                    platform_slug="snes",
                    name="Game",
                    fs_name="",
                    shortcut_app_id=50000,
                    last_synced_at="2025-01-01T00:00:00",
                )
            )
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["target_path_occupied"] is False

    @pytest.mark.asyncio
    async def test_a_traversing_fs_name_is_refused_rather_than_probed(self, game_detail, path_probe):
        probed: list[str] = []
        path_probe.exists = lambda path: bool(probed.append(path))
        _seed_rom(game_detail, 10, app_id=50000, platform_slug="snes", fs_name="../../../etc/passwd")

        result = await game_detail.service.get_cached_game_detail(50000)

        assert result["target_path_occupied"] is False
        assert probed == []


class TestGetCachedGameDetailConflictFiltering:
    """pending_conflicts was removed from get_cached_game_detail response."""

    @pytest.mark.asyncio
    async def test_no_pending_conflicts_in_response(self, game_detail):
        """pending_conflicts key is no longer in the response."""
        _seed_rom(game_detail, 10, app_id=50000, name="Game A", platform_slug="snes")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert "pending_conflicts" not in result

    @pytest.mark.asyncio
    async def test_response_still_has_save_status(self, game_detail):
        """Response still includes save status fields."""
        _seed_rom(game_detail, 10, app_id=50000, name="Game A", platform_slug="snes")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["found"] is True
        assert "save_sync_enabled" in result

    @pytest.mark.asyncio
    async def test_app_id_as_string(self, game_detail):
        """app_id passed as string is handled correctly."""
        _seed_rom(game_detail, 10, app_id=50000, name="Game", platform_slug="snes")
        result = await game_detail.service.get_cached_game_detail("50000")
        assert result["found"] is True
        assert result["rom_id"] == 10


# ============================================================================
# get_cached_game_detail bios_status from cache tests
# ============================================================================


class TestGetCachedGameDetailCarriesNoBiosAnswer:
    """The cached payload never carries a BIOS answer, and says it does not know.

    What an emulator wants is read off the machine, and an answer read for a
    previous page open may not stand in for this one — so every open starts
    not-knowing and the live ``get_bios_status`` fills it in a moment later. The
    frontend clears a shown requirement on an absent ``bios_status``, so the flag
    is what keeps "not known yet" apart from "this core needs none" (#1693).
    """

    @pytest.mark.asyncio
    async def test_cold_cache_flags_bios_status_unknown(self, game_detail):
        _seed_rom(game_detail, 42, app_id=50000, name="Pokemon", platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert result["found"] is True
        assert result["bios_status"] is None
        assert result["bios_status_unknown"] is True

    @pytest.mark.asyncio
    async def test_a_populated_firmware_cache_changes_nothing(self, game_detail, tmp_path):
        """Even with the server listing warm, no answer rides this payload.

        The seed is the whole difference between this case and the cold one above
        — every assertion below is equally true of an empty cache. So the cache is
        read before it is written: that raises the moment it moves off this
        attribute, where a bare assignment would create a dead name and leave the
        test asserting what its cold-cache sibling already covers.
        """
        from unittest.mock import patch

        _seed_rom(game_detail, 42, app_id=50000, name="Pokemon", platform_slug="gba")
        listing = game_detail.firmware._listing
        assert listing._firmware_cache is None
        listing._firmware_cache = [
            {
                "file_path": "bios/gba/gba_bios.bin",
                "file_name": "gba_bios.bin",
                "file_size_bytes": 16384,
                "md5_hash": "abc123",
                "id": 1,
            },
        ]
        listing._firmware_cache_epoch = 99.0

        with patch.object(game_detail.firmware._demand, "_retrodeck_paths", FakeRetroDeckPaths(bios=str(tmp_path))):
            result = await game_detail.service.get_cached_game_detail(50000)

        assert result["bios_status"] is None
        assert result["bios_level"] is None
        assert result["bios_label"] is None
        assert result["bios_status_unknown"] is True

    @pytest.mark.asyncio
    async def test_bios_is_always_stale_so_the_page_always_asks(self, game_detail):
        """With nothing stored there is nothing to age — the refresh is unconditional."""
        _seed_rom(game_detail, 42, app_id=50000, name="Pokemon", platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(50000)
        assert "bios" in result["stale_fields"]

    @pytest.mark.asyncio
    async def test_a_rom_without_a_platform_asks_nothing(self, game_detail):
        """No platform, no BIOS question — the flag would promise an answer that never comes."""
        _seed_rom(game_detail, 43, app_id=50001, name="Homebrew", platform_slug="")
        result = await game_detail.service.get_cached_game_detail(50001)
        assert result["bios_status_unknown"] is False
        assert "bios" not in result["stale_fields"]


class TestGetBiosStatusFound:
    """Test get_bios_status when ROM has BIOS requirements."""

    @pytest.mark.asyncio
    async def test_returns_bios_status(self, game_detail):
        """ROM with needs_bios=True returns the BIOS status dict + the checker's level/label.

        The BIOS payload carries no core fields after #923 — core info is served via
        the dedicated ``get_platform_core_info`` path.
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="gba")
        mock_check = AsyncMock(
            return_value={
                "needs_bios": True,
                "server_count": 3,
                "local_count": 1,
                "all_downloaded": False,
                "required_count": 2,
                "required_downloaded": 1,
                "bios_level": "partial",
                "bios_label": "1/2 required",
                "files": [{"file_name": "gba_bios.bin", "downloaded": True}],
            }
        )
        game_detail.service._bios_checker.check_platform_bios = mock_check

        result = await game_detail.service.get_bios_status(42)
        bs = result["bios_status"]
        assert bs is not None
        assert bs["platform_slug"] == "gba"
        assert bs["server_count"] == 3
        assert bs["local_count"] == 1
        assert bs["all_downloaded"] is False
        assert bs["required_count"] == 2
        assert bs["required_downloaded"] == 1
        # bios_level/bios_label are the BIOS checker's own — computed against the
        # active core's required counts (core-aware badge) where the reading
        # state that decides them is known, and threaded through untouched.
        assert result["bios_level"] == "partial"
        assert result["bios_label"] == "1/2 required"
        assert result["bios_status_unknown"] is False
        # The dataclass wrapper is gone with the recomputation: its
        # ``reading_complete`` default would ship a True this call site has no
        # basis for, and no frontend models the field.
        assert "reading_complete" not in bs

    @pytest.mark.asyncio
    async def test_badge_keys_off_active_core(self, game_detail):
        """The missing-BIOS badge is computed against the ACTIVE CORE's requirements (#923).

        Two cores for the same platform produce different ``required_count`` /
        ``required_downloaded`` from ``check_platform_bios``, which filters by the
        active core and derives the verdict over its own filtered list.
        ``get_bios_status`` carries that verdict through unchanged — so the badge
        follows the active core, not a platform default.
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="gba")

        # gpSP requires gba_bios.bin and it is missing → missing badge.
        gpsp_payload = {
            "needs_bios": True,
            "server_count": 1,
            "local_count": 0,
            "all_downloaded": False,
            "required_count": 1,
            "required_downloaded": 0,
            "bios_level": "missing",
            "bios_label": "Missing",
            "files": [{"file_name": "gba_bios.bin", "downloaded": False}],
        }
        game_detail.service._bios_checker.check_platform_bios = AsyncMock(return_value=gpsp_payload)
        result = await game_detail.service.get_bios_status(42)
        assert result["bios_level"] == "missing"
        assert result["bios_label"] == "Missing"

        # mGBA treats gba_bios.bin as optional → required_count 0 → no missing badge.
        mgba_payload = {
            "needs_bios": True,
            "server_count": 1,
            "local_count": 0,
            "all_downloaded": False,
            "required_count": 0,
            "required_downloaded": 0,
            "bios_level": "ok",
            "bios_label": "OK",
            "files": [{"file_name": "gba_bios.bin", "downloaded": False}],
        }
        game_detail.service._bios_checker.check_platform_bios = AsyncMock(return_value=mgba_payload)
        result = await game_detail.service.get_bios_status(42)
        assert result["bios_level"] == "ok"
        assert result["bios_label"] == "OK"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_bios_needed(self, game_detail):
        """ROM with needs_bios=False returns bios_status / level / label all None.

        This is the real negative — the answer the frontend is allowed to clear a
        shown requirement on, so it must NOT be flagged unknown (#1693).
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="gba")
        game_detail.service._bios_checker.check_platform_bios = AsyncMock(return_value={"needs_bios": False})

        result = await game_detail.service.get_bios_status(42)
        assert result["bios_status"] is None
        assert result["bios_level"] is None
        assert result["bios_label"] is None
        assert result["bios_status_unknown"] is False

    @pytest.mark.asyncio
    async def test_passes_resolved_per_game_core_to_bios_check(self, game_detail, active_core_resolver):
        """The per-game active core (resolved by rom_id) is threaded into the BIOS filter.

        The BIOS check no longer receives a ROM filename — game-detail resolves
        the emulator through ``ActiveCoreReader`` (which folds the per-game
        emulator_override pin) and passes that whole resolution in, so the
        core-aware filter keys off the pin rather than a platform default.

        The PICK goes over rather than its identity, because the answer coming
        back states both the emulator it was filtered by and the name it prints:
        split here, the two could be resolved separately and disagree.
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="gba")
        active_core_resolver.per_rom[42] = ("gpsp_libretro", "gpSP")

        captured = {}

        async def capture_check(slug, launching_emulator=None, rom_regions=()):
            captured["slug"] = slug
            captured["launching_emulator"] = launching_emulator
            return {"needs_bios": False}

        game_detail.service._bios_checker.check_platform_bios = capture_check

        await game_detail.service.get_bios_status(42)
        assert captured["slug"] == "gba"
        assert captured["launching_emulator"].emulator == "gpsp_libretro.so"
        assert captured["launching_emulator"].label == "gpSP"
        assert active_core_resolver.emulator_calls == [42]

    @pytest.mark.asyncio
    async def test_passes_the_roms_own_regions_to_bios_check(self, game_detail, active_core_resolver):
        """A one-of group is judged for the regions this game is sold in, as RomM names them."""
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="psx", regions=("Japan", "Asia"))
        captured = {}

        async def capture_check(slug, launching_emulator=None, rom_regions=()):
            captured["rom_regions"] = rom_regions
            return {"needs_bios": False}

        game_detail.service._bios_checker.check_platform_bios = capture_check

        await game_detail.service.get_bios_status(42)
        assert captured["rom_regions"] == ("Japan", "Asia")

    @pytest.mark.asyncio
    async def test_bios_check_differs_by_per_game_override(self, game_detail, active_core_resolver):
        """RESULT-FLIP: two gba ROMs, one pinned to gpSP + one default, drive different BIOS results.

        gpSP requires ``gba_bios.bin`` → missing badge; the default core treats it
        as optional → ok. The badge flips on the per-game override alone, proving
        the resolved core (not a platform default) feeds the BIOS filter.
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Pinned", platform_slug="gba")
        _seed_rom(game_detail, 43, app_id=50001, name="Default", platform_slug="gba")
        active_core_resolver.per_rom[42] = ("gpsp_libretro", "gpSP")
        # rom 43 falls through to the default (None, None) → system default.

        async def fake_check(slug, launching_emulator=None, rom_regions=()):
            if launching_emulator is not None and launching_emulator.emulator == "gpsp_libretro.so":
                return {
                    "needs_bios": True,
                    "server_count": 1,
                    "local_count": 0,
                    "all_downloaded": False,
                    "required_count": 1,
                    "required_downloaded": 0,
                    "bios_level": "missing",
                    "bios_label": "Missing",
                    "files": [{"file_name": "gba_bios.bin", "downloaded": False}],
                }
            return {"needs_bios": False}

        game_detail.service._bios_checker.check_platform_bios = fake_check

        pinned = await game_detail.service.get_bios_status(42)
        plain = await game_detail.service.get_bios_status(43)

        assert pinned["bios_level"] == "missing"
        assert plain["bios_status"] is None
        assert active_core_resolver.emulator_calls == [42, 43]


class TestGetBiosStatusNotFound:
    """Test get_bios_status when ROM is not in registry."""

    @pytest.mark.asyncio
    async def test_unknown_rom_id(self, game_detail):
        """Unknown rom_id returns bios_status / level / label all None, answered."""
        result = await game_detail.service.get_bios_status(999)
        assert result == {
            "bios_status": None,
            "bios_level": None,
            "bios_label": None,
            "bios_status_unknown": False,
        }

    @pytest.mark.asyncio
    async def test_no_platform_slug(self, game_detail):
        """ROM without platform_slug returns bios_status / level / label all None, answered."""
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="")
        result = await game_detail.service.get_bios_status(42)
        assert result == {
            "bios_status": None,
            "bios_level": None,
            "bios_label": None,
            "bios_status_unknown": False,
        }

    @pytest.mark.asyncio
    async def test_firmware_error_flags_unknown(self, game_detail):
        """A raising BIOS check answers ``bios_status_unknown`` — not "no BIOS need" (#1693).

        The frontend clears a shown requirement on an absent ``bios_status``, so
        a failed check that shipped the same payload as a real negative took the
        missing-BIOS warning off the page.
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="gba")
        game_detail.service._bios_checker.check_platform_bios = AsyncMock(side_effect=Exception("fail"))

        result = await game_detail.service.get_bios_status(42)
        assert result == {
            "bios_status": None,
            "bios_level": None,
            "bios_label": None,
            "bios_status_unknown": True,
        }

    @pytest.mark.asyncio
    async def test_checker_unknown_verdict_is_passed_through(self, game_detail):
        """A check that answered "unknown" itself keeps that verdict on the wire (#1693).

        ``check_platform_bios`` handles a failed firmware fetch internally and
        flags the answer when it had no registry coverage to degrade to — no
        exception reaches here, so the flag is the only thing separating it from
        a platform that genuinely needs nothing.
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="gba")
        game_detail.service._bios_checker.check_platform_bios = AsyncMock(
            return_value={"needs_bios": False, "bios_status_unknown": True}
        )

        result = await game_detail.service.get_bios_status(42)
        assert result["bios_status"] is None
        assert result["bios_status_unknown"] is True

    @pytest.mark.asyncio
    async def test_an_unknown_verdict_carries_the_level_a_failed_read_does_not(self, game_detail):
        """The two payloads behind ``bios_status_unknown`` are told apart by the level (#1660).

        Both ship an absent ``bios_status`` and both leave a shown requirement
        standing, so the flag cannot separate them — but only one of them is an
        ANSWER, and the frontend words that one as its requirement-unknown state
        (``frontend/src/utils/biosSummary.ts``) instead of dropping the BIOS
        tab. A read that raised carries no level, so it stays a non-answer; the
        pair is asserted together because a change to either side is only a
        defect against the other.
        """
        _seed_rom(game_detail, 42, app_id=50000, name="Game", platform_slug="ps3")
        game_detail.service._bios_checker.check_platform_bios = AsyncMock(
            return_value={"needs_bios": False, "bios_status_unknown": True}
        )
        answered = await game_detail.service.get_bios_status(42)

        game_detail.service._bios_checker.check_platform_bios = AsyncMock(side_effect=Exception("fail"))
        raised = await game_detail.service.get_bios_status(42)

        assert answered == {
            "bios_status": None,
            "bios_level": "unknown",
            "bios_label": "Unknown",
            "bios_status_unknown": True,
        }
        assert raised["bios_level"] is None
        assert raised["bios_label"] is None


class TestGetCachedGameDetailSaveStatusConflicts:
    @pytest.mark.asyncio
    async def test_save_status_includes_empty_conflicts(self, game_detail):
        """Lightweight save_status should include an empty conflicts list."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba")
        game_detail.settings["save_sync_enabled"] = True
        _seed_save_state(
            game_detail,
            42,
            files={"test.srm": FileSyncState(last_sync_hash="abc", last_sync_at="2026-01-01T00:00:00Z")},
            last_sync_check_at="2026-01-01T00:00:00Z",
        )
        result = await game_detail.service.get_cached_game_detail(99999)
        assert result["save_status"] is not None
        assert "conflicts" in result["save_status"]
        assert result["save_status"]["conflicts"] == []

    @pytest.mark.asyncio
    async def test_save_status_empty_files_unknown_status(self, game_detail):
        """A save state with an empty files{} returns an empty files list."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba")
        _seed_save_state(game_detail, 42, files={}, last_sync_check_at=None)
        result = await game_detail.service.get_cached_game_detail(99999)
        assert result["save_status"] is not None
        assert result["save_status"]["files"] == []
        assert result["save_status"]["last_sync_check_at"] is None


class TestComputedFields:
    """Test bios_level, bios_label, save_sync_display in response."""

    @pytest.mark.asyncio
    async def test_bios_level_and_label_are_never_computed_here(self, game_detail):
        """The cached payload carries no level: there is no answer to derive one from."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(99999)
        assert result["bios_level"] is None
        assert result["bios_label"] is None

    @pytest.mark.asyncio
    async def test_save_sync_display_with_saves(self, game_detail):
        """When save data exists, save_sync_display is the typed dataclass payload."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba")
        game_detail.settings["save_sync_enabled"] = True
        _seed_save_state(
            game_detail,
            42,
            files={"test.srm": FileSyncState(last_sync_hash="abc", last_sync_at="2026-01-01T00:00:00Z")},
            last_sync_check_at="2026-01-01T00:00:00Z",
        )
        result = await game_detail.service.get_cached_game_detail(99999)
        assert result["save_sync_display"] is not None
        assert result["save_sync_display"]["status"] == "synced"
        # Synced + recorded check → backend leaves label None for frontend formatTimeAgo.
        assert result["save_sync_display"]["label"] is None
        assert result["save_sync_display"]["last_sync_check_at"] == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_save_sync_display_none_when_no_saves(self, game_detail):
        """When no save data, save_sync_display should be None."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(99999)
        assert result["save_sync_display"] is None


class TestAchievementSummaryCachedAt:
    """Test that achievement_summary includes cached_at from progress cache."""

    @pytest.mark.asyncio
    async def test_achievement_summary_includes_cached_at(self, game_detail, clock):
        """When progress is cached, achievement_summary includes cached_at timestamp."""
        cached_time = clock.time() - 600  # 10 minutes ago
        _seed_rom(game_detail, 42, app_id=99999, name="Sonic", platform_slug="genesis", ra_id=555)
        game_detail.achievements._achievements_cache["_ra_user"] = {
            "username": "testuser",
            "cached_at": clock.time(),
        }
        game_detail.achievements._achievements_cache["42"] = {
            "user_progress": {
                "earned": 5,
                "earned_hardcore": 3,
                "total": 20,
                "earned_achievements": [],
                "cached_at": cached_time,
            },
            "cached_at": clock.time(),
        }

        result = await game_detail.service.get_cached_game_detail(99999)

        assert result["achievement_summary"] is not None
        assert result["achievement_summary"]["earned"] == 5
        assert result["achievement_summary"]["total"] == 20
        assert result["achievement_summary"]["earned_hardcore"] == 3
        assert result["achievement_summary"]["cached_at"] == cached_time

    @pytest.mark.asyncio
    async def test_achievement_summary_cached_at_reflects_storage_time(self, game_detail, clock):
        """cached_at in summary matches the time progress was stored, not current time."""
        storage_time = clock.time() - 1800  # 30 minutes ago
        _seed_rom(game_detail, 42, app_id=99999, name="Sonic", platform_slug="genesis", ra_id=555)
        game_detail.achievements._achievements_cache["_ra_user"] = {
            "username": "testuser",
            "cached_at": clock.time(),
        }
        game_detail.achievements._achievements_cache["42"] = {
            "user_progress": {
                "earned": 10,
                "earned_hardcore": 10,
                "total": 10,
                "earned_achievements": [],
                "cached_at": storage_time,
            },
            "cached_at": clock.time(),
        }

        result = await game_detail.service.get_cached_game_detail(99999)

        assert result["achievement_summary"]["cached_at"] == storage_time
        assert result["achievement_summary"]["cached_at"] < clock.time() - 1700

    @pytest.mark.asyncio
    async def test_no_achievement_summary_without_ra_username(self, game_detail):
        """Without RA username, achievement_summary is None even with ra_id."""
        _seed_rom(game_detail, 42, app_id=99999, name="Sonic", platform_slug="genesis", ra_id=555)

        result = await game_detail.service.get_cached_game_detail(99999)

        assert result["achievement_summary"] is None

    @pytest.mark.asyncio
    async def test_no_achievement_summary_without_cached_progress(self, game_detail, clock):
        """With RA username but no cached progress, achievement_summary is None."""
        _seed_rom(game_detail, 42, app_id=99999, name="Sonic", platform_slug="genesis", ra_id=555)
        game_detail.achievements._achievements_cache["_ra_user"] = {
            "username": "testuser",
            "cached_at": clock.time(),
        }

        result = await game_detail.service.get_cached_game_detail(99999)

        assert result["achievement_summary"] is None


class TestStaleFields:
    """Test stale_fields computation in get_cached_game_detail."""

    @pytest.mark.asyncio
    async def test_stale_fields_empty_when_all_fresh(self, game_detail, clock):
        """No stale fields when all caches are fresh."""
        _seed_metadata(game_detail, 42, cached_at=clock.time(), app_id=99999, platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(99999)
        assert "stale_fields" in result
        assert "metadata" not in result["stale_fields"]

    @pytest.mark.asyncio
    async def test_metadata_stale_when_old(self, game_detail, clock):
        """Metadata older than 7 days should appear in stale_fields."""
        _seed_metadata(game_detail, 42, cached_at=clock.time() - 8 * 24 * 3600, app_id=99999, platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(99999)
        assert "metadata" in result["stale_fields"]

    @pytest.mark.asyncio
    async def test_metadata_stale_when_missing(self, game_detail):
        """Missing metadata should appear in stale_fields."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(99999)
        assert "metadata" in result["stale_fields"]

    @pytest.mark.asyncio
    async def test_bios_stale_when_old(self, game_detail):
        """BIOS older than 1 hour should appear in stale_fields."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba")
        result = await game_detail.service.get_cached_game_detail(99999)
        # With no BIOS cache, bios_status is None → bios should be stale
        assert "bios" in result["stale_fields"]

    @pytest.mark.asyncio
    async def test_achievements_stale_when_missing(self, game_detail):
        """Missing achievement progress should appear in stale_fields when ra_id is set."""
        _seed_rom(game_detail, 42, app_id=99999, name="Test", platform_slug="gba", ra_id=123)
        result = await game_detail.service.get_cached_game_detail(99999)
        assert "achievements" in result["stale_fields"]

    @pytest.mark.asyncio
    async def test_not_found_has_no_stale_fields(self, game_detail):
        """When ROM not found, response has no stale_fields."""
        result = await game_detail.service.get_cached_game_detail(99999)
        assert "stale_fields" not in result
