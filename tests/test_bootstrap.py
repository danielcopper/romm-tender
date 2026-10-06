"""Tests for the bootstrap composition root."""

import asyncio
import json
import logging
import os
import pathlib
import sqlite3
import subprocess
import sys
from dataclasses import fields
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bootstrap import (
    AdapterBundle,
    BootstrapResult,
    CallbackBundle,
    RuntimeBundle,
    StateBundle,
    WiringConfig,
    bootstrap,
    wire_services,
)
from fakes.fake_core_info_provider import FakeCoreInfoProvider
from fakes.fake_cover_art_file_store import FakeCoverArtFileStore
from fakes.fake_download_file_store import FakeDownloadFileStore
from fakes.fake_emulator_sources import FakeEmulatorSources
from fakes.fake_firmware_file_store import FakeFirmwareFileStore
from fakes.fake_firmware_resolver import FakeFirmwareResolver
from fakes.fake_game_process_control import FakeGameProcessControlAdapter
from fakes.fake_hostname_reader import FakeHostnameReader
from fakes.fake_journal import FakeJournal
from fakes.fake_latest_release import FakeLatestRelease
from fakes.fake_machine_id_reader import FakeMachineIdReader
from fakes.fake_migration_file_store import FakeMigrationFileStore
from fakes.fake_path_exists_reader import FakePathExistsReader
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_release_download import FakeReleaseDownload
from fakes.fake_renderer_gc import FakeRendererGc
from fakes.fake_renderer_rss import FakeRendererRss
from fakes.fake_resolved_path import FakeResolvedPath
from fakes.fake_retrodeck_paths import FakeRetroDeckPaths
from fakes.fake_rom_file_store import FakeRomFileStore
from fakes.fake_save_file_store import FakeSaveFileStore
from fakes.fake_save_location_reader import FakeSaveLocationReader
from fakes.fake_sgdb_artwork_cache import FakeSgdbArtworkCache
from fakes.fake_steam_interface import FakeSteamInterface
from fakes.fake_transient_units import FakeTransientUnits
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory
from fakes.system_time import FakeClock, FakeSleeper, FakeUuidGen
from models.shortcut_launcher import ShortcutLauncher

from adapters.gavel_native import GavelNativeAdapter
from adapters.retrodeck_paths import RetroDeckPathsAdapter
from adapters.romm.http import RommHttpAdapter
from adapters.romm.romm_api import RommApiAdapter
from adapters.steam_config import SteamConfigAdapter
from adapters.update_attempt import UpdateAttemptFileAdapter
from adapters.update_staging import UpdateStagingAdapter
from domain.app_directories import AppDirectories
from domain.identity import MIN_ROMM_VERSION, PACKAGE_NAME, VERSION
from domain.sync_run_kind import SyncRunKind
from domain.update_release import UpdateSource
from lib.errors import Refused
from lib.prune_conflicts import PruneConflicts
from services.achievements import AchievementsService
from services.cores import CoreService
from services.data_inventory import DataInventoryService
from services.disc import DiscService
from services.downloads import DownloadService
from services.firmware import FirmwareService
from services.game_process import GameProcessService
from services.leftover_tmp_cleanup import LeftoverTmpCleanupService
from services.library import LibraryService
from services.metadata import MetadataService
from services.playtime import PlaytimeService
from services.prune import PruneService
from services.prune_leases import PruneLeaseService
from services.saves import SaveService
from services.steamgrid import SteamGridService
from services.update_check import UpdateCheckService
from services.update_outcome import UpdateOutcomeService
from services.update_output import UpdateOutputService
from services.version_switch import VersionSwitchService

_GAVEL = GavelNativeAdapter()


def _current_umask() -> int:
    """This process's umask, read the only way there is — by setting it and putting it back."""
    import os

    current = os.umask(0o022)
    os.umask(current)
    return current


def _directories_at(tmp_path) -> AppDirectories:
    """The seven directories a run is told about, all under ``tmp_path``."""
    return AppDirectories(
        config_dir=str(tmp_path / "config"),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        state_dir=str(tmp_path / "state"),
        runtime_dir=str(tmp_path / "run"),
        code_dir=str(tmp_path / "code"),
        bin_dir=str(tmp_path / "home" / ".local" / "bin"),
    )


# A release API nothing answers on: bootstrap builds the adapter and never calls it.
_UPDATE_SOURCE = UpdateSource(release_api="http://127.0.0.1:9/releases/latest", installed_program=False)


def _bootstrap_for(tmp_path) -> BootstrapResult:
    return bootstrap(
        directories=_directories_at(tmp_path),
        update_source=_UPDATE_SOURCE,
        user_home=str(tmp_path / "home"),
        logger=logging.getLogger("test"),
    )


class TestBootstrap:
    def test_returns_typed_bootstrap_result(self, tmp_path):
        result = _bootstrap_for(tmp_path)
        assert isinstance(result, BootstrapResult)

    def test_http_adapter_shares_settings_reference(self, tmp_path):
        """RommHttpAdapter binds the same dict the StateBundle exposes."""
        result = _bootstrap_for(tmp_path)
        # Mutate the live settings dict — http_adapter holds the same ref.
        result.stores.settings["romm_url"] = "http://changed.com"
        assert result.adapters.http_adapter._settings["romm_url"] == "http://changed.com"
        assert result.adapters.http_adapter._settings is result.stores.settings

    def test_returns_http_adapter(self, tmp_path):
        result = _bootstrap_for(tmp_path)
        assert isinstance(result.adapters.http_adapter, RommHttpAdapter)

    def test_returns_steam_config(self, tmp_path):
        result = _bootstrap_for(tmp_path)
        assert isinstance(result.adapters.steam_config, SteamConfigAdapter)

    def test_returns_romm_api(self, tmp_path):
        result = _bootstrap_for(tmp_path)
        assert isinstance(result.adapters.romm_api, RommApiAdapter)

    def test_returns_retrodeck_paths_adapter(self, tmp_path):
        """Bootstrap instantiates the RetroDECK paths adapter for the callbacks bundle."""
        result = _bootstrap_for(tmp_path)
        assert isinstance(result.callbacks.retrodeck_paths, RetroDeckPathsAdapter)

    def test_returns_core_info_provider_on_adapters(self, tmp_path):
        """``core_info_provider`` is bundled with adapters, not callbacks.

        The stateful adapter sits in :class:`AdapterBundle`;
        :class:`CallbackBundle` carries only provider callables and persisters.
        """
        result = _bootstrap_for(tmp_path)
        # AdapterBundle exposes the stateful catalogue adapter.
        assert result.adapters.core_info_provider is not None
        # CallbackBundle no longer carries it.
        assert not hasattr(result.callbacks, "core_info_provider")

    def test_platform_core_reader_binds_live_settings(self, tmp_path):
        """``CallbackBundle.platform_core_reader`` reads the live settings dict.

        A per-platform core written into ``stores.settings`` after bootstrap is
        visible on the next ``get_platform_core`` read — the adapter holds the
        same dict, not a snapshot (the fan-out depends on this).
        """
        result = _bootstrap_for(tmp_path)
        reader = result.callbacks.platform_core_reader
        assert reader.get_platform_core("snes") is None
        result.stores.settings["platform_cores"]["snes"] = "bsnes"
        assert reader.get_platform_core("snes") == "bsnes"

    def test_state_bundle_carries_only_settings(self, tmp_path):
        """Post-cutover (#784) ``StateBundle`` holds only the live settings dict.

        The residual ``downloaded_bios`` JSON index was the last on-disk JSON
        state read at startup; with BIOS migration on SQLite the bundle no
        longer carries a ``state`` field.
        """
        result = _bootstrap_for(tmp_path)
        assert result.stores.settings is not None
        assert not hasattr(result.stores, "state")

    def test_runtime_adapters_bundle_populated(self, tmp_path):
        """Bootstrap builds the clock/uuid/sleeper/hostname/machine-id seams a RuntimeBundle is composed from."""
        result = _bootstrap_for(tmp_path)
        assert result.runtime_adapters.clock is not None
        assert result.runtime_adapters.uuid_gen is not None
        assert result.runtime_adapters.sleeper is not None
        assert result.runtime_adapters.hostname_provider is not None
        assert result.runtime_adapters.machine_id_provider is not None

    def test_user_agent_threaded_to_romm_http_adapter(self, tmp_path):
        """Bootstrap threads ``<package name>/<version>`` to ``RommHttpAdapter`` (#249, #719).

        Without a User-Agent, Cloudflare Bot Fight Mode 403s the default
        ``Python-urllib`` UA before the request reaches self-hosted RomM
        behind a tunnel.
        """
        result = _bootstrap_for(tmp_path)
        assert result.adapters.http_adapter._user_agent == f"{PACKAGE_NAME}/{VERSION}"

    def test_user_agent_threaded_to_steamgriddb_adapter(self, tmp_path):
        """Bootstrap threads the same ``<package name>/<version>`` UA into
        ``SteamGridDbAdapter`` so SGDB sees a non-default UA on every site
        (#719). SGDB rejects ``Python-urllib`` with 403.
        """
        result = _bootstrap_for(tmp_path)
        assert result.adapters.sgdb_adapter._user_agent == f"{PACKAGE_NAME}/{VERSION}"

    def test_user_agent_reads_the_constants_rather_than_spelling_them(self, tmp_path, monkeypatch):
        """Both halves come from ``domain/identity.py``, name included.

        The two assertions above spell the program's own name and version, so a
        literal ``"romm-tender/0.33.0"`` in bootstrap would satisfy them; this
        one asks with a name and a version the program will never carry, so only
        a read of the constants can answer it. What it protects: bootstrap
        spends the same name on the recovery root the cleanup writes its bundles
        into, so a literal here is free to drift away from it, and the drift
        shows up on a server's token list rather than in CI.
        """
        monkeypatch.setattr("bootstrap.adapters.PACKAGE_NAME", "not-the-programs-name")
        monkeypatch.setattr("bootstrap.adapters.VERSION", "9.9.9")

        result = _bootstrap_for(tmp_path)

        assert result.adapters.http_adapter._user_agent == "not-the-programs-name/9.9.9"
        assert result.adapters.sgdb_adapter._user_agent == "not-the-programs-name/9.9.9"

    def test_the_recovery_root_is_named_after_the_same_constant(self, tmp_path, monkeypatch):
        """The UA and the recovery root are one name, so they cannot drift apart.

        A recovery folder is named after whatever wrote it and a server reading
        a User-Agent is told which package is calling — the same answer, spent
        twice. Asked with a name the program will never carry, because the real
        one is what ``sanitize_package_name`` would produce either way.
        """
        monkeypatch.setattr("bootstrap.adapters.PACKAGE_NAME", "not-the-programs-name")

        result = _bootstrap_for(tmp_path)

        assert result.adapters.recovery_store.root().endswith("/not-the-programs-name-recovery")


class TestTheCacheRootAndTheDataRootStayApart:
    """Everything re-derivable is wired to the cache root, and the database is not.

    The two are six sibling `str` fields on one struct, so a wrong one is a
    rename away and fails silently in whichever direction it pointed. These
    assert the ROOTS the wiring composed, rather than that a file turned up:
    `PruneArtifactAdapter` owns exactly `covers/` and `artwork/`, so pointing it
    at the data root makes a removed-game purge find nothing and delete nothing,
    with no failure and nothing in the log.
    """

    def test_the_cover_cache_and_the_purge_that_clears_it_share_one_root(self, tmp_path):
        directories = _directories_at(tmp_path)
        result = _bootstrap_for(tmp_path)
        loop = asyncio.new_event_loop()
        services = wire_services(
            WiringConfig(
                adapters=result.adapters,
                stores=result.stores,
                runtime=RuntimeBundle(
                    loop=loop,
                    logger=logging.getLogger("test"),
                    emit=AsyncMock(),
                    clock=result.runtime_adapters.clock,
                    uuid_gen=result.runtime_adapters.uuid_gen,
                    sleeper=result.runtime_adapters.sleeper,
                    hostname_provider=result.runtime_adapters.hostname_provider,
                    machine_id_provider=result.runtime_adapters.machine_id_provider,
                    steam=FakeSteamInterface(),
                ),
                callbacks=result.callbacks,
                min_required_version=MIN_ROMM_VERSION,
                directories=directories,
                launcher=result.launcher,
                update_source=_UPDATE_SOURCE,
                installer_environment=(),
            )
        )

        try:
            # Where the covers are written, and where the purge goes looking —
            # the purge's own answer, through the seam the service calls.
            assert services.artwork_service._cover_cache_dir.startswith(directories.cache_dir)
            roots = {a["safe_root"] for a in result.adapters.prune_artifacts.recovery_artifacts([7])}
            assert roots == {directories.cache_dir}
        finally:
            loop.close()

    def test_the_sgdb_artwork_cache_is_on_the_cache_root(self, tmp_path):
        directories = _directories_at(tmp_path)

        result = _bootstrap_for(tmp_path)

        assert result.adapters.sgdb_artwork_cache.cache_dir() == os.path.join(directories.cache_dir, "artwork")

    def test_the_database_is_not(self, tmp_path):
        """The one thing that cannot be fetched again stays on the data root."""
        directories = _directories_at(tmp_path)

        _bootstrap_for(tmp_path)

        assert (pathlib.Path(directories.data_dir) / "romm-tender.db").exists()
        assert not (pathlib.Path(directories.cache_dir) / "romm-tender.db").exists()


class TestBootstrapRenamesTheOldDatabase:
    """A data root that still holds the database under its old name starts on it under the current one."""

    @staticmethod
    def _old_database_with_a_hot_wal(tmp_path) -> pathlib.Path:
        """The old database as a process that died after a commit leaves it: the commit only in ``-wal``."""
        data = tmp_path / "data"
        data.mkdir()
        script = (
            "import os, sqlite3\n"
            f"db = sqlite3.connect({str(data / 'romm_sync.db')!r}, isolation_level=None)\n"
            "db.execute('PRAGMA journal_mode=WAL')\n"
            "db.execute('CREATE TABLE marker (note TEXT)')\n"
            "db.execute(\"INSERT INTO marker VALUES ('the library')\")\n"
            "os._exit(0)\n"
        )
        subprocess.run([sys.executable, "-c", script], check=True)
        assert sorted(path.name for path in data.iterdir()) == ["romm_sync.db", "romm_sync.db-shm", "romm_sync.db-wal"]
        return data

    def test_the_library_is_intact_under_the_current_name(self, tmp_path):
        data = self._old_database_with_a_hot_wal(tmp_path)

        _bootstrap_for(tmp_path)

        assert not any(path.name.startswith("romm_sync.db") for path in data.iterdir())
        db = sqlite3.connect(data / "romm-tender.db")
        try:
            assert db.execute("SELECT note FROM marker").fetchall() == [("the library",)]
            assert db.execute("PRAGMA user_version").fetchone()[0] > 0
        finally:
            db.close()

    def test_an_old_file_sqlite_cannot_open_fails_the_start_and_no_empty_library_takes_its_place(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        content = b"not a database, and long enough to have a header" * 4
        (data / "romm_sync.db").write_bytes(content)

        with pytest.raises(sqlite3.DatabaseError):
            _bootstrap_for(tmp_path)

        assert sorted(path.name for path in data.iterdir()) == ["romm_sync.db"]
        assert (data / "romm_sync.db").read_bytes() == content


class TestBootstrapInstallsTheLauncher:
    """The launcher leaves the code root, and every start puts this release's there."""

    @staticmethod
    def _ship(tmp_path) -> bytes:
        shipped = tmp_path / "code" / "bin" / "tender-rom-launcher"
        shipped.parent.mkdir(parents=True, exist_ok=True)
        shipped.write_bytes(b'#!/bin/bash\nexec "$@"\n')
        shipped.chmod(0o755)
        return shipped.read_bytes()

    @staticmethod
    def _home(tmp_path) -> pathlib.Path:
        return tmp_path / "home" / ".local" / "bin" / "tender-rom-launcher"

    def test_the_launcher_is_installed_into_the_bin_root(self, tmp_path):
        content = self._ship(tmp_path)

        result = _bootstrap_for(tmp_path)

        assert result.launcher.path == str(self._home(tmp_path))
        assert result.launcher.at_home is True
        assert self._home(tmp_path).read_bytes() == content

    def test_the_launcher_is_not_left_beside_the_program(self, tmp_path):
        """The bin root and the code root are two of seven strings on one struct.

        Nothing but this pins which of them the launcher followed, and the whole
        point of the launcher's home is that a shortcut's ``exe`` must not name a
        file inside the directory the program itself was installed into.
        """
        self._ship(tmp_path)

        result = _bootstrap_for(tmp_path)

        assert str(tmp_path / "code") not in result.launcher.path
        assert str(tmp_path / "cache") not in result.launcher.path
        assert str(tmp_path / "data") not in result.launcher.path

    def test_the_bin_root_is_created_at_the_umasks_mode(self, tmp_path):
        """A shared directory: narrowing it would bind every other program's files."""
        import os
        import stat

        self._ship(tmp_path)

        _bootstrap_for(tmp_path)

        mode = stat.S_IMODE(os.stat(self._home(tmp_path).parent).st_mode)
        assert mode == 0o777 & ~_current_umask()

    def test_a_launcher_the_release_did_not_ship_leaves_the_start_running(self, tmp_path):
        """The one thing that must not happen is the backend failing to start over it.

        The path falls back to the copy the release ships, which in this one case
        is the file that is missing — there is nowhere honest left to point, and
        naming the home would claim a launcher no start ever wrote.
        """
        result = _bootstrap_for(tmp_path)

        assert result.launcher.at_home is False
        assert result.launcher.path == str(tmp_path / "code" / "bin" / "tender-rom-launcher")


class TestBootstrapSettingsResetMarker:
    """Bootstrap folds a corrupt-settings reset into the persistent
    ``_settings_reset_notice`` marker so it survives a backend restart."""

    def test_corrupt_boot_persists_marker_into_settings(self, tmp_path):
        import json
        import os
        import pathlib

        # Seeded where bootstrap actually reads: the config directory this run
        # was TOLD about, which is the only place it looks.
        settings_dir = pathlib.Path(_directories_at(tmp_path).config_dir)
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path = settings_dir / "settings.json"
        settings_path.write_text("NOT VALID JSON {{{")

        result = _bootstrap_for(tmp_path)

        # In-memory dict carries the marker pointing at the quarantined backup.
        notice = result.stores.settings.get("_settings_reset_notice")
        assert notice is not None
        backup_name = notice["backed_up_to"]
        assert backup_name.startswith("settings.json.corrupt-")
        assert os.path.exists(settings_dir / backup_name)

        # It was persisted to the fresh settings.json (survives a reload).
        with open(settings_path) as f:
            persisted = json.load(f)
        assert persisted["_settings_reset_notice"] == {"backed_up_to": backup_name}

    def test_clean_boot_writes_no_marker(self, tmp_path):
        result = _bootstrap_for(tmp_path)
        assert "_settings_reset_notice" not in result.stores.settings


class TestBootstrapReadsOnlySettingsAtTheOldestVersionOrNewer:
    """A settings file at version 13 loads as it is; an older one, or one without
    a usable version, starts on the defaults with a warning and does not crash."""

    def _seed(self, tmp_path, content: Any) -> pathlib.Path:
        settings_dir = pathlib.Path(_directories_at(tmp_path).config_dir)
        settings_dir.mkdir(parents=True, exist_ok=True)
        path = settings_dir / "settings.json"
        path.write_text(json.dumps(content))
        return path

    def test_a_file_at_version_13_loads_unchanged(self, tmp_path, caplog):
        content = {
            "version": 13,
            "romm_url": "https://romm.example",
            "enabled_platforms": {"3": True},
            "enabled_collections": {"standard": {"1": True}, "smart": {}, "virtual": {"v": True}},
            "default_slot": "slot-a",
            "log_level": "debug",
        }
        path = self._seed(tmp_path, content)

        with caplog.at_level(logging.WARNING):
            result = _bootstrap_for(tmp_path)

        assert {key: result.stores.settings[key] for key in content} == content
        assert {key: json.loads(path.read_text())[key] for key in content} == content
        assert not [r for r in caplog.records if "settings.json" in r.getMessage()]

    @pytest.mark.parametrize(
        ("content", "found"),
        [
            ({"version": 12, "romm_url": "https://romm.example"}, "version 12"),
            ({"romm_url": "https://romm.example"}, "no version"),
            ({"version": "13", "romm_url": "https://romm.example"}, "version is a JSON string"),
            ({"version": None, "romm_url": "https://romm.example"}, "version is a JSON null"),
            ([], "array"),
        ],
        ids=["version-12", "no-version", "version-string", "version-null", "not-an-object"],
    )
    def test_a_file_it_does_not_read_starts_on_the_defaults_with_a_warning(self, tmp_path, caplog, content, found):
        path = self._seed(tmp_path / "old", content)
        fresh = _bootstrap_for(tmp_path / "fresh").stores.settings

        with caplog.at_level(logging.WARNING):
            result = _bootstrap_for(tmp_path / "old")

        assert result.stores.settings == fresh
        assert json.loads(path.read_text()) == fresh
        warnings = [r for r in caplog.records if "settings.json" in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING
        assert found in warnings[0].getMessage()


class TestBootstrapWarnsWhenCertificateChecksAreOff:
    """A start with ``romm_allow_insecure_ssl`` on says so once, so a log read
    for a later problem shows that the RomM connection was not verified."""

    _WARNING = "Certificate verification is off for RomM requests"

    def _seed_settings(self, tmp_path, **values):
        import json
        import pathlib

        settings_dir = pathlib.Path(_directories_at(tmp_path).config_dir)
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.json").write_text(json.dumps({"version": 13, **values}))

    def test_a_start_with_the_setting_on_logs_one_warning(self, tmp_path, caplog):
        self._seed_settings(tmp_path, romm_allow_insecure_ssl=True)

        with caplog.at_level(logging.WARNING):
            result = _bootstrap_for(tmp_path)

        assert result.stores.settings["romm_allow_insecure_ssl"] is True
        warnings = [r for r in caplog.records if self._WARNING in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING

    def test_a_start_with_the_setting_off_logs_nothing_about_it(self, tmp_path, caplog):
        self._seed_settings(tmp_path, romm_allow_insecure_ssl=False)

        with caplog.at_level(logging.WARNING):
            _bootstrap_for(tmp_path)

        assert not [r for r in caplog.records if self._WARNING in r.getMessage()]


class TestWireServices:
    def _make_deps(self, tmp_path):
        logger = logging.getLogger("test_wire")
        settings = {}
        http_adapter = MagicMock(spec=RommHttpAdapter)
        steam_config = SteamConfigAdapter(user_home=str(tmp_path), logger=logger)
        romm_api = MagicMock(spec=RommApiAdapter)
        return {
            "http_adapter": http_adapter,
            "romm_api": romm_api,
            "steam_config": steam_config,
            "sgdb_adapter": MagicMock(),
            "cover_art_file_store": FakeCoverArtFileStore(),
            "sgdb_artwork_cache": FakeSgdbArtworkCache(),
            "download_file_store": FakeDownloadFileStore(),
            "adoption_move": MagicMock(),
            "firmware_file_store": FakeFirmwareFileStore(),
            "firmware_resolver": FakeFirmwareResolver(),
            "platform_firmware_resolver": FakeFirmwareResolver(),
            "migration_file_store": FakeMigrationFileStore(),
            "rom_file_store": FakeRomFileStore(),
            "save_file_store": FakeSaveFileStore(),
            "path_probe": FakePathExistsReader(),
            "resolve_path": FakeResolvedPath(),
            "renderer_rss": FakeRendererRss(),
            "renderer_gc": FakeRendererGc(),
            "game_process": FakeGameProcessControlAdapter(),
            "resolve_upload_conflict": _GAVEL,
            "compute_sync_action": _GAVEL.compute_sync_action,
            "recovery_store": MagicMock(),
            "prune_artifacts": MagicMock(),
            "steam_recovery": MagicMock(),
            "latest_release": FakeLatestRelease(),
            "update_failure": MagicMock(return_value=None),
            "update_staging": UpdateStagingAdapter(directory=str(tmp_path / "cache" / "update")),
            "update_attempt": UpdateAttemptFileAdapter(state_dir=str(tmp_path / "state"), log_debug=lambda msg: None),
            "settings": settings,
            "loop": asyncio.new_event_loop(),
            "logger": logger,
            "emit": AsyncMock(),
            "clock": FakeClock(),
            "uuid_gen": FakeUuidGen(),
            "sleeper": FakeSleeper(),
            "hostname_provider": FakeHostnameReader(),
            "machine_id_provider": FakeMachineIdReader(),
            "min_required_version": MIN_ROMM_VERSION,
            "retrodeck_paths": FakeRetroDeckPaths(
                saves=str(tmp_path / "saves"),
                roms=str(tmp_path / "retrodeck" / "roms"),
                bios=str(tmp_path / "retrodeck" / "bios"),
                home=str(tmp_path / "retrodeck"),
            ),
            "platform_core_reader": FakePlatformCoreReader(),
            "m3u_support": MagicMock(return_value=True),
            "sandbox_launcher": MagicMock(return_value=None),
            "system_extensions": MagicMock(return_value=frozenset()),
            "system_known": MagicMock(return_value=None),
            "list_rom_dir_files": MagicMock(return_value=[]),
            "settings_persister": MagicMock(),
            "core_info_provider": FakeCoreInfoProvider(),
            "log_debug": MagicMock(),
            "uow_factory": FakeUnitOfWorkFactory(),
            "directories": _directories_at(tmp_path),
            "launcher": ShortcutLauncher(
                path=str(tmp_path / "home" / ".local" / "bin" / "tender-rom-launcher"),
                at_home=True,
            ),
        }

    @staticmethod
    def _make_config(deps: dict[str, Any]) -> WiringConfig:
        """Build a WiringConfig from the flat deps dict produced by ``_make_deps``."""
        return WiringConfig(
            adapters=AdapterBundle(
                http_adapter=deps["http_adapter"],
                romm_api=deps["romm_api"],
                steam_config=deps["steam_config"],
                sgdb_adapter=deps["sgdb_adapter"],
                cover_art_file_store=deps["cover_art_file_store"],
                sgdb_artwork_cache=deps["sgdb_artwork_cache"],
                download_file_store=deps["download_file_store"],
                adoption_move=deps["adoption_move"],
                firmware_file_store=deps["firmware_file_store"],
                firmware_resolver=deps["firmware_resolver"],
                platform_firmware_resolver=deps["platform_firmware_resolver"],
                migration_file_store=deps["migration_file_store"],
                rom_file_store=deps["rom_file_store"],
                save_file_store=deps["save_file_store"],
                path_probe=deps["path_probe"],
                resolve_path=deps["resolve_path"],
                core_info_provider=deps["core_info_provider"],
                save_locations=FakeSaveLocationReader(),
                emulator_sources=FakeEmulatorSources(),
                renderer_rss=deps["renderer_rss"],
                renderer_gc=deps["renderer_gc"],
                game_process=deps["game_process"],
                resolve_upload_conflict=deps["resolve_upload_conflict"],
                compute_sync_action=deps["compute_sync_action"],
                recovery_store=deps["recovery_store"],
                recovery_inventory=deps["recovery_store"],
                prune_artifacts=deps["prune_artifacts"],
                steam_recovery=deps["steam_recovery"],
                latest_release=deps["latest_release"],
                update_failure=deps["update_failure"],
                update_attempt=deps["update_attempt"],
                download_release_asset=FakeReleaseDownload(),
                update_staging=deps["update_staging"],
                transient_units=FakeTransientUnits(),
                journal=FakeJournal(),
            ),
            stores=StateBundle(
                settings=deps["settings"],
            ),
            runtime=RuntimeBundle(
                loop=deps["loop"],
                logger=deps["logger"],
                emit=deps["emit"],
                clock=deps["clock"],
                uuid_gen=deps["uuid_gen"],
                sleeper=deps["sleeper"],
                hostname_provider=deps["hostname_provider"],
                machine_id_provider=deps["machine_id_provider"],
                steam=FakeSteamInterface(),
            ),
            callbacks=CallbackBundle(
                retrodeck_paths=deps["retrodeck_paths"],
                platform_core_reader=deps["platform_core_reader"],
                m3u_support=deps["m3u_support"],
                sandbox_launcher=deps["sandbox_launcher"],
                system_extensions=deps["system_extensions"],
                system_known=deps["system_known"],
                list_rom_dir_files=deps["list_rom_dir_files"],
                settings_persister=deps["settings_persister"],
                log_debug=deps["log_debug"],
                uow_factory=deps["uow_factory"],
            ),
            min_required_version=deps["min_required_version"],
            directories=deps["directories"],
            launcher=deps["launcher"],
            update_source=_UPDATE_SOURCE,
            installer_environment=(),
        )

    def test_returns_all_services(self, tmp_path):
        deps = self._make_deps(tmp_path)
        result = wire_services(self._make_config(deps))
        assert isinstance(result.save_sync_service, SaveService)
        assert isinstance(result.playtime_service, PlaytimeService)
        assert isinstance(result.sync_service, LibraryService)
        assert isinstance(result.download_service, DownloadService)
        assert isinstance(result.firmware_service, FirmwareService)
        assert isinstance(result.sgdb_service, SteamGridService)
        assert isinstance(result.metadata_service, MetadataService)
        assert isinstance(result.achievements_service, AchievementsService)
        deps["loop"].close()

    def test_wires_http_adapter_on_retry_to_threadsafe_emit(self, tmp_path):
        # #1345: wire_services installs a retry listener on the http adapter that
        # marshals a ``server_retry_progress`` emit onto the loop (with_retry runs
        # in executor threads). Fire it as with_retry would and pump the loop so
        # the marshaled emit runs.
        deps = self._make_deps(tmp_path)
        wire_services(self._make_config(deps))
        on_retry = deps["http_adapter"].on_retry
        assert callable(on_retry)

        loop = deps["loop"]
        on_retry(2, 3, 1.0)

        async def _drain():
            # One yield runs the call_soon_threadsafe callback (which creates the
            # emit task); the second lets that task run to completion.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        loop.run_until_complete(_drain())
        deps["emit"].assert_awaited_once_with(
            "server_retry_progress",
            {"attempt": 2, "max_attempts": 3, "delay_s": 1.0},
        )
        loop.close()

    def test_services_share_settings_reference(self, tmp_path):
        deps = self._make_deps(tmp_path)
        result = wire_services(self._make_config(deps))
        # MigrationService holds the live settings dict; all relational
        # migration state (installs, BIOS, markers) reads through the UoW
        # factory after the SQLite cutover (#784).
        assert result.migration_service._settings is deps["settings"]
        deps["loop"].close()

    def test_returns_expected_services(self, tmp_path):
        deps = self._make_deps(tmp_path)
        result = wire_services(self._make_config(deps))
        assert len(fields(result)) == 34
        assert all(getattr(result, field.name) is not None for field in fields(result))
        assert isinstance(result.prune_conflicts, PruneConflicts)
        assert isinstance(result.core_service, CoreService)
        assert isinstance(result.disc_service, DiscService)
        assert isinstance(result.version_switch_service, VersionSwitchService)
        assert isinstance(result.prune_service, PruneService)
        assert isinstance(result.prune_lease_service, PruneLeaseService)
        assert isinstance(result.data_inventory_service, DataInventoryService)
        assert isinstance(result.game_process_service, GameProcessService)
        assert isinstance(result.update_check_service, UpdateCheckService)
        assert isinstance(result.leftover_tmp_cleanup_service, LeftoverTmpCleanupService)
        assert isinstance(result.update_outcome_service, UpdateOutcomeService)
        assert isinstance(result.update_output_service, UpdateOutputService)
        deps["loop"].close()

    def test_pending_sync_binding_observes_library_rebinds(self, tmp_path):
        """ArtworkService/SgdbService see live LibraryService._pending_sync rebinds.

        Regression for #349: the bootstrap binding must defer the read so
        post-bind reassignments of ``_pending_sync`` (e.g., line 417 of
        library.py after a sync diff) are visible to consumers.
        """
        deps = self._make_deps(tmp_path)
        result = wire_services(self._make_config(deps))
        sync_service = result.sync_service
        artwork_service = result.artwork_service
        sgdb_service = result.sgdb_service

        # Producer rebinds _pending_sync to a fresh dict (mirrors sync_apply_delta).
        sync_service._pending_sync = {42: {"name": "Game", "platform_name": "N64"}}

        assert artwork_service._get_pending_sync() == {42: {"name": "Game", "platform_name": "N64"}}
        assert sgdb_service._get_pending_sync() == {42: {"name": "Game", "platform_name": "N64"}}
        deps["loop"].close()

    def test_library_service_bakes_the_launcher_it_was_given(self, tmp_path):
        """Every shortcut this run writes names the launcher's home, not the code root."""
        deps = self._make_deps(tmp_path)

        result = wire_services(self._make_config(deps))

        assert result.sync_service._orchestrator._launcher_exe == deps["launcher"].path
        deps["loop"].close()

    def test_migration_service_receives_the_firmware_resolver(self, tmp_path):
        """The untracked-BIOS sweep asks the same seam every firmware surface asks.

        It replaced a late-bound reader of the registry index (#349): with the
        answer read live there is nothing to rebind, and the sweep's candidate
        list is whatever the resolver declares at the moment it runs.
        """
        deps = self._make_deps(tmp_path)
        result = wire_services(self._make_config(deps))
        migration_service = result.migration_service

        assert migration_service._firmware_resolver is deps["firmware_resolver"]
        deps["loop"].close()

    def test_save_sync_and_migration_share_uow(self, tmp_path):
        """SaveService and MigrationService resolve the same Unit of Work — one database."""
        deps = self._make_deps(tmp_path)
        shared_uow = deps["uow_factory"].uow
        result = wire_services(self._make_config(deps))
        save_sync_service = result.save_sync_service
        migration_service = result.migration_service
        assert migration_service._uow_factory() is shared_uow
        assert save_sync_service._rom_info._uow_factory() is shared_uow
        deps["loop"].close()

    async def test_the_sync_rule_reads_the_library_sync_the_wiring_built(self, tmp_path):
        deps = self._make_deps(tmp_path)
        result = wire_services(self._make_config(deps))
        rules = result.save_sync_service._rules

        async with rules.hold("probe", sync=True):
            pass
        assert result.sync_service._box.try_begin_run("held-sync-run", kind=SyncRunKind.APPLY)
        with pytest.raises(Refused) as refused:
            async with rules.hold("probe", sync=True):
                pass
        assert refused.value.reason == "sync_active"
        deps["loop"].close()

    def test_save_service_receives_is_retrodeck_migration_pending(self, tmp_path):
        """Regression test for #251: SaveService must receive the bound
        ``migration_service.is_retrodeck_migration_pending`` callback so
        pre_launch_sync / post_exit_sync can short-circuit while the user
        still has files at the previous RetroDECK home."""
        deps = self._make_deps(tmp_path)
        result = wire_services(self._make_config(deps))
        save_sync_service = result.save_sync_service
        migration_service = result.migration_service
        # is_retrodeck_migration_pending is consumed by the sync_engine sub-service.
        assert save_sync_service._sync_engine._is_retrodeck_migration_pending == (
            migration_service.is_retrodeck_migration_pending
        )
        assert save_sync_service._sync_engine._is_retrodeck_migration_pending.__self__ is migration_service  # type: ignore[union-attr]
        deps["loop"].close()
