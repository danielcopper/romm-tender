import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from _conflict_rules import endpoints_with_rule
from _factories import _make_conflict_rules, _make_prune_conflicts
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_game_process_control import FakeGameProcessControlAdapter
from fakes.fake_path_exists_reader import FakePathExistsReader
from fakes.fake_relaunch_options_resolver import FakeRelaunchOptionsResolver
from fakes.fake_renderer_gc import FakeRendererGc
from fakes.fake_renderer_rss import FakeRendererRss
from fakes.fake_resolved_path import FakeResolvedPath
from fakes.fake_retrodeck_paths import FakeRetroDeckPaths
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_sgdb_artwork_cache import FakeSgdbArtworkCache
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory
from fakes.library_peers import FakeArtworkManager
from fakes.running_loop import running_loop
from fakes.system_time import FakeClock, FakeSleeper, FakeUuidGen

from adapters.debug_logger import SettingsAwareDebugLogger
from adapters.steam_config import SteamConfigAdapter
from host import HostStatus
from main import Plugin
from services.connection import ConnectionService, ConnectionServiceConfig
from services.library import LibraryService, LibraryServiceConfig
from services.settings import SettingsService, SettingsServiceConfig
from services.startup_healing import StartupHealingService, StartupHealingServiceConfig
from services.steamgrid import SteamGridService, SteamGridServiceConfig


@pytest.fixture
def plugin(logger, home, data_dir):
    p = Plugin()
    p.settings = {"romm_url": "", "romm_user": "", "romm_pass": "", "enabled_platforms": {}}
    p._http_adapter = MagicMock()
    p._romm_api = MagicMock()
    # Default to "/tmp" so the prune guard sees an existing home in tests that
    # don't override it. Tests exercising the guard rebuild this with a
    # non-existent path or empty string.
    p._retrodeck_paths = FakeRetroDeckPaths(home="/tmp")
    # Default migration service mock — no migration pending.
    p._migration_service = MagicMock()
    p._migration_service.is_retrodeck_migration_pending.return_value = False
    p._prune_service = MagicMock()
    conflict_rules = _make_conflict_rules()

    p._debug_logger = SettingsAwareDebugLogger(settings=p.settings, logger=logger)
    steam_config = SteamConfigAdapter(user_home=str(home), logger=logger)
    p._steam_config = steam_config

    p._settings_persister = FakeSettingsPersister()

    p._sync_service = LibraryService(
        config=LibraryServiceConfig(
            romm_api=p._romm_api,
            steam_config=steam_config,
            settings=p.settings,
            loop=running_loop(),
            logger=logger,
            launcher_exe=f"{home}/.local/bin/tender-rom-launcher",
            # An emit that answers every event as heard.
            emit=AsyncMock(return_value=True),
            clock=FakeClock(),
            uuid_gen=FakeUuidGen(),
            sleeper=FakeSleeper(),
            settings_persister=p._settings_persister,
            log_debug=p._log_debug,
            artwork=FakeArtworkManager(),
            uow_factory=FakeUnitOfWorkFactory(),
            active_core=FakeActiveCoreResolver(default=(None, None)),
            disc_resolver=FakeDiscResolver(),
            renderer_rss=FakeRendererRss(),
            renderer_gc=FakeRendererGc(),
            conflict_rules=conflict_rules,
        ),
    )

    p._sgdb_service = SteamGridService(
        config=SteamGridServiceConfig(
            sgdb_api=MagicMock(),
            romm_api=p._romm_api,
            steam_config=steam_config,
            sgdb_artwork_cache=FakeSgdbArtworkCache(cache_root=data_dir),
            settings=p.settings,
            loop=running_loop(),
            logger=logger,
            settings_persister=FakeSettingsPersister(),
            get_pending_sync=lambda: p._sync_service._pending_sync,
            log_debug=p._log_debug,
            uow_factory=FakeUnitOfWorkFactory(),
            conflict_rules=conflict_rules,
        ),
    )

    p._settings_service = SettingsService(
        config=SettingsServiceConfig(
            settings=p.settings,
            uow_factory=FakeUnitOfWorkFactory(),
            logger=logger,
            settings_persister=p._settings_persister,
            steam_config=steam_config,
            conflict_rules=conflict_rules,
        ),
    )

    p._connection_service = ConnectionService(
        config=ConnectionServiceConfig(
            settings=p.settings,
            romm_api=p._romm_api,
            settings_persister=p._settings_persister,
            loop=running_loop(),
            logger=logger,
            min_required_version=Plugin._MIN_REQUIRED_VERSION,
            forget_device=MagicMock(),
            clear_playtime_scope_notice=MagicMock(),
            conflict_rules=conflict_rules,
        ),
    )

    p._startup_healing_service = StartupHealingService(
        config=StartupHealingServiceConfig(
            logger=logger,
            clock=FakeClock(),
            retrodeck_paths=p._retrodeck_paths,
            path_probe=FakePathExistsReader(),
            resolve_path=FakeResolvedPath(),
            uow_factory=FakeUnitOfWorkFactory(),
            relaunch_options=FakeRelaunchOptionsResolver(),
            loop=running_loop(),
            conflict_rules=conflict_rules,
        ),
    )
    return p


class TestUnsetSlotsAreLoud:
    """A test-only slot on ``Plugin`` is an annotation, not an attribute: bare access raises."""

    def test_settings_persister_missing_on_bare_plugin(self):
        """``_settings_persister`` is never set by production; bare access raises."""
        from main import Plugin

        bare = Plugin()

        with pytest.raises(AttributeError, match="_settings_persister"):
            _ = bare._settings_persister


class TestConnection:
    @pytest.mark.asyncio
    async def test_test_connection_sets_version_on_romm_api(self, plugin, logger):
        plugin.loop = asyncio.get_running_loop()
        plugin.settings["romm_url"] = "http://romm.local"
        plugin.settings["romm_api_token"] = "rmm_token"
        plugin._romm_api.heartbeat.return_value = {"SYSTEM": {"VERSION": "5.3.0"}}
        plugin._romm_api.list_platforms.return_value = [{"id": 1, "slug": "n64"}]
        # Rebuild connection service with the live event loop so executor
        # callbacks dispatch on the same loop the test awaits.
        plugin._connection_service = ConnectionService(
            config=ConnectionServiceConfig(
                settings=plugin.settings,
                romm_api=plugin._romm_api,
                settings_persister=MagicMock(),
                loop=plugin.loop,
                logger=logger,
                min_required_version=Plugin._MIN_REQUIRED_VERSION,
                forget_device=MagicMock(),
                clear_playtime_scope_notice=MagicMock(),
                conflict_rules=_make_conflict_rules(),
            ),
        )
        result = await plugin.test_connection()
        assert result["success"] is True
        plugin._romm_api.set_version.assert_called_once_with("5.3.0")


class TestLogLevel:
    @pytest.mark.asyncio
    async def test_debug_log_backward_compat(self, plugin, caplog):
        """debug_log reaches the log as a debug line of its own."""
        plugin.settings["log_level"] = "debug"
        plugin.debug_log("test backward compat")

        assert [(r.levelname, r.message) for r in caplog.records] == [
            ("DEBUG", "[FE] test backward compat"),
        ]

    @pytest.mark.asyncio
    async def test_sgdb_artwork_silent_when_debug_off(self, plugin, tmp_path, logger):
        """SGDB artwork info calls should not log when log_level is 'warn'."""
        from unittest.mock import patch

        plugin.settings["log_level"] = "warn"
        with patch.object(logger, "info") as mock_info:
            result = await plugin.get_sgdb_artwork_base64(1, 99)
            assert result["base64"] is None
            for call in mock_info.call_args_list:
                assert "SGDB artwork" not in str(call)

    @pytest.mark.asyncio
    async def test_sgdb_artwork_logs_when_debug_enabled(self, plugin, tmp_path, logger):
        """SGDB artwork info calls should log when log_level is 'debug'."""
        from unittest.mock import patch

        plugin.settings["log_level"] = "debug"
        plugin.settings["steamgriddb_api_key"] = ""
        with patch.object(logger, "info") as mock_info:
            result = await plugin.get_sgdb_artwork_base64(1, 1)
            assert result["no_api_key"] is True
            logged_msgs = [str(c) for c in mock_info.call_args_list]
            assert any("SGDB artwork" in m for m in logged_msgs)


class TestRefreshMigrationState:
    @pytest.mark.asyncio
    async def test_delegates_to_migration_service_refresh_state(self, plugin):
        """Plugin callable forwards to MigrationService.refresh_state."""
        from unittest.mock import AsyncMock

        sentinel = {
            "retrodeck": {"pending": True, "old_path": "/a", "new_path": "/b"},
        }
        plugin._migration_service = MagicMock()
        plugin._migration_service.refresh_state = AsyncMock(return_value=sentinel)
        result = await plugin.refresh_migration_state()
        plugin._migration_service.refresh_state.assert_awaited_once_with()
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_propagates_exceptions(self, plugin):
        from unittest.mock import AsyncMock

        plugin._migration_service = MagicMock()
        plugin._migration_service.refresh_state = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await plugin.refresh_migration_state()


_MIGRATION_RULE_WHITELIST: set[str] = {
    # Migration management itself (the unblock pathway must work while pending).
    "migrate_retrodeck_files",
    "get_migration_status",
    "dismiss_retrodeck_migration",
    "refresh_migration_state",
    # Connection / settings (read-only or non-retrodeck).
    "test_connection",
    "connect_with_credentials",
    "connect_with_token",
    "connect_with_pairing_code",
    # Local-forget-only token clear — mutates settings.json, never RetroDECK state.
    "sign_out",
    "save_server_url",
    # Custom proxy headers (#1822) — a settings.json-only write, never touches
    # RetroDECK state. It has to answer while a migration is pending for a
    # stronger reason than the other settings writes: without them the plugin
    # cannot reach the server at all, so blocking it would leave a user behind an
    # authenticating proxy unable to configure their way out.
    "save_custom_headers",
    "get_settings",
    "get_whitelist_settings",
    "update_whitelist_settings",
    "save_collection_platform_groups",
    # Collection owner-scope (#1532) — a settings.json-only write, never touches
    # RetroDECK state; takes effect on the next sync's work-queue build.
    "set_collection_owner_scope",
    # Collection naming mode (#1539) — a settings.json-only write, never touches
    # RetroDECK state; takes effect on the next sync's reporter-key build.
    "set_collection_naming_mode",
    # Preferred sibling-group region — a settings.json-only write (ADR-0021 §3),
    # never touches RetroDECK state; takes effect on the next sync. Its companion
    # read (distinct regions in the local library) is a pure local DB read.
    "save_preferred_region",
    "get_known_regions",
    # Sync-button intent — a settings.json-only write, never touches RetroDECK
    # state; it records which action the sync button should take.
    "save_skip_preview",
    # Persistent corrupt-settings-reset notice — the read (banner/card) and the
    # user's explicit QAM ack must both work regardless of a pending migration.
    "get_settings_reset_notice",
    "dismiss_settings_reset_notice",
    # The one-time move of the shortcuts onto the launcher's home: the plan, the
    # completion stamp, and the user's answer to the card that ends the
    # transition. None of the three touches RetroDECK state — the reading is of
    # Steam's own shortcut file and the two writes are a kv_config row and a
    # settings key. They have to answer while a migration is pending for the
    # same reason as the notice above: the frontend points the shortcuts at the
    # launcher at plugin load, whatever page the panel happens to be showing,
    # and a shortcut left naming a file inside the plugin folder is the
    # condition the relocation exists to end.
    "get_shortcut_relocation",
    # The release check: whether a newer release exists, the card's per-version
    # Dismiss, the daily-check switch and the reader's own Check now. None of
    # the four touches RetroDECK state — the reads talk to GitHub and one
    # kv_config row, the writes are settings keys — and the read is fired at
    # panel load whatever page the panel is showing.
    "get_update_notice",
    "check_for_update_now",
    "dismiss_update_notice",
    "set_update_check_enabled",
    # What the last update did: the read the panel makes at load, its
    # acknowledgement of the announcement's one toast, the announcement card's
    # Dismiss, and the rolled-back card's per-record Dismiss. None touches
    # RetroDECK state — the read is the installer's record in the state
    # directory and this process's own memory, the writes are memory and a
    # settings key — and the read is fired at panel load whatever page the
    # panel is showing.
    "get_update_outcome",
    "acknowledge_update_toast",
    "dismiss_update_announcement",
    "dismiss_update_failure",
    # What the hosting process knows about its own run — the port it bound, the
    # start-up repairs that failed, the protocol messages it could not act on.
    # Touches no RetroDECK state and reads nothing from disk. It has to answer
    # while a migration is pending for the strongest reason on this list: a
    # blocked panel is exactly when a reader needs to see that a start-up repair
    # has been failing, and blocking the call would hide the diagnosis behind
    # the condition it might explain.
    "get_host_status",
    # Read-only RetroDECK path-resolution health probe (for the frontend banner).
    "get_retrodeck_status",
    # Cancel / pause operations — must remain callable mid-operation when
    # migration marker fires so the user can stop in-flight work. (Resume,
    # which re-begins a filesystem transfer, IS migration-blocked.)
    "cancel_sync",
    "sync_cancel_preview",
    "cancel_download",
    "pause_download",
    # Read-only content check: hashes files already on disk and compares them
    # against RomM's checksums. Writes nothing, so a pending migration has
    # nothing to protect from it — and the adopt dialog it answers for is
    # reachable through the (blocked) download itself.
    "verify_existing_content",
    # Terminating the running game — signals host processes only, never a
    # RetroDECK path, and the user must be able to stop a live game whatever the
    # migration marker says (same reasoning as the cancel/pause group above).
    "stop_running_game",
    # In-memory download-queue cleanup (#149) — evicts terminal entries from the
    # queue dict only, never touching SQLite or RetroDECK, so a pending migration
    # doesn't gate it.
    "clear_completed_downloads",
    # Frontend logging / diagnostic helpers.
    "frontend_log",
    "debug_log",
    "save_log_level",
    "save_steam_input_setting",
    "apply_steam_input_setting",
    "fix_retroarch_input_driver",
    # Read-only library / sync state queries.
    "get_cached_game_detail",
    "get_platform_core_info",
    "get_system_core_info",
    "count_platform_saves",
    # Read-only disc-picker state query (the pin-write select_disc names the rule).
    "get_disc_selection",
    # Read-only version-picker state query (the binding-move switch_version names the rule).
    "get_version_list",
    "get_platforms",
    "get_collections",
    "sync_heartbeat",
    "report_unit_results",
    "get_registry_platforms",
    "report_removal_results",
    "stage_prune_installed_selection",
    "release_prune_conflict_lease",
    "renew_prune_conflict_lease",
    "wait_for_prune_release",
    # Ack for an already-started cleanup run must remain available while the
    # run waits on its exact Steam action token; gating it would deadlock the
    # cleanup run if migration state changed mid-run.
    "report_prune_action",
    # Stopping a run destroys nothing and touches no RetroDECK path — it only
    # cancels the run's own task. Gating it on a migration that appeared
    # mid-run would strand the user with a cleanup they cannot stop.
    "cancel_prune",
    # Disowning leases a dead frontend context stranded touches no RetroDECK
    # path either, and must run at mount regardless of migration state — a
    # stranded lease is precisely what would otherwise refuse the callables
    # that resolve the migration.
    "release_orphaned_prune_leases",
    # Sync-start reconcile of Steam-UI-deleted shortcut bindings (#1046) — clears
    # only the SQLite ``shortcut_app_id`` link (never a RetroDECK path), so it is
    # not gated by a pending migration, matching report_removal_results above.
    "reconcile_shortcuts",
    "get_artwork_base64",
    # Cache-first per-ROM cover fetch for the version picker (#1346) — a
    # read-only data callable (fills the cover cache on a miss), never mutates
    # RetroDECK state.
    "fetch_cover_base64",
    "get_sync_status",
    "get_sync_stats",
    # Read-only population figures for the Data Management page — one SQLite
    # scan plus a listing of the recovery root, which is outside every
    # RetroDECK path a migration moves, so a pending migration has nothing to
    # protect from it.
    "get_data_inventory",
    # Read-only run-history listing — reads the sync_runs table and nothing
    # else, so a pending migration has nothing to protect from it.
    "get_sync_runs",
    "get_session_budget_status",
    # Read-only pending-preview query (plus the lazy drop of an expired
    # snapshot) — it starts no run and touches no RetroDECK path, and the panel
    # asks for it on every mount, so a pending migration must not refuse it.
    "get_pending_preview",
    "get_download_queue",
    "get_installed_rom",
    "evaluate_launch",
    # Launch-gate offline funnel: a local-only drift hash check, a version-free
    # reachability heartbeat, a fire-and-forget read-only save-status refresh,
    # and the pre-launch relaunch re-confirm read (#1150). None mutate RetroDECK
    # state, so all stay callable mid-migration.
    "check_local_drift",
    "probe_reachability",
    "refresh_save_status",
    "get_rom_relaunch_options",
    # End-of-session orchestration — composes the playtime session-end record,
    # the post-exit save sync, the fire-and-forget achievement refresh and the
    # migration-state refresh. Only the save sync writes to a RetroDECK path,
    # and SessionLifecycleService checks ``is_retrodeck_migration_pending``
    # before it, so the destructive sync stays gated while the rest runs.
    "finalize_game_session",
    # Firmware / BIOS read-only checks.
    "get_firmware_status",
    "get_platform_firmware_status",
    "check_platform_bios",
    "get_bios_status",
    # Save sync read-only / device queries.
    "ensure_device_registered",
    "list_devices",
    "get_save_status",
    "check_core_change",
    "get_save_slots",
    "get_slot_saves",
    "get_slot_delete_info",
    "is_save_tracking_configured",
    "get_save_setup_info",
    "get_save_sync_settings",
    "saves_list_file_versions",
    # Playtime queries.
    "record_session_start",
    "get_all_playtime",
    "reconcile_playtime",
    "get_playtime_scope_notice",
    # SteamGridDB / Steam shortcut artwork (Steam-side, not retrodeck).
    "get_sgdb_artwork_base64",
    "verify_sgdb_api_key",
    "save_sgdb_api_key",
    "save_shortcut_icon",
    "get_sgdb_resolution",
    "search_sgdb_games",
    "apply_sgdb_game_id",
    # Metadata cache reads.
    "get_rom_metadata",
    "get_metadata_cache_page",
    "get_app_id_rom_id_map",
    # Read-only startup launch-options reconcile pull (#1043) — heals drifted
    # shortcut launch commands; pure read, never gated.
    "get_installed_relaunch_options",
    # Achievements queries (server-side).
    "get_achievements",
    "get_achievement_progress",
}


class TestMigrationRuleCoverage:
    """Every endpoint on Plugin must be classified: either explicitly
    whitelisted (read-only / unblock pathway / non-retrodeck) or declaring the
    migration rule — a ``hold("<endpoint>", migration=True)`` or
    ``hold_start("<endpoint>", migration=True)`` at the entry of the use case
    it calls. Prevents a new endpoint from being silently unguarded against
    pending migration corruption."""

    def test_all_callables_either_whitelisted_or_declaring_the_rule(self):
        from host.dispatch import reachable_methods
        from main import Plugin

        migration_ruled = endpoints_with_rule("migration")
        unclassified: list[str] = []
        for name in reachable_methods(Plugin()):
            if name in _MIGRATION_RULE_WHITELIST:
                continue
            if name in migration_ruled:
                continue
            unclassified.append(name)

        assert not unclassified, (
            "Unclassified endpoints on Plugin — every one must be in "
            "_MIGRATION_RULE_WHITELIST or declare the migration rule: "
            f"{sorted(unclassified)}"
        )

    def test_no_callable_both_declares_the_rule_and_is_whitelisted(self):
        """An endpoint that both declares the migration rule AND is whitelisted
        is silently passing the coverage check — likely a misclassification.
        Catch it."""
        from host.dispatch import reachable_methods
        from main import Plugin

        migration_ruled = endpoints_with_rule("migration")
        double_classified = [
            name
            for name in reachable_methods(Plugin())
            if name in _MIGRATION_RULE_WHITELIST and name in migration_ruled
        ]

        assert not double_classified, (
            "Endpoints both whitelisted AND declaring the migration rule — "
            f"remove from one: {sorted(double_classified)}"
        )

    def test_whitelisted_callables_are_endpoints_declaring_no_rule(self):
        """Every name in _MIGRATION_RULE_WHITELIST must be an endpoint on
        Plugin and must NOT declare the migration rule. Reads from the
        whitelist side, so a name left behind by a removed or renamed endpoint
        fails here instead of classifying nothing."""
        from host.dispatch import reachable_methods
        from main import Plugin

        endpoints = reachable_methods(Plugin())
        stale = sorted(_MIGRATION_RULE_WHITELIST - endpoints.keys())
        assert not stale, f"Whitelisted names that are not endpoints on Plugin: {stale}"

        ruled = sorted(_MIGRATION_RULE_WHITELIST & endpoints_with_rule("migration"))
        assert not ruled, f"Whitelisted endpoints that also declare the migration rule: {ruled}"


class TestMainStartupOrdering:
    """Lock-in test for the #251 startup-order invariant: ``detect_retrodeck_path_change``
    must run BEFORE ``prune_stale_installed_roms`` so the prune skips entries living
    under a pending migration's previous home. Brittle by design — the assertion
    is intentionally narrow."""

    @pytest.mark.asyncio
    async def test_main_calls_detect_path_change_before_prune(self):
        from unittest.mock import patch

        from bootstrap import (
            AdapterBundle,
            BootstrapHandles,
            BootstrapResult,
            CallbackBundle,
            RuntimeAdaptersBundle,
            ServicesBundle,
            StateBundle,
        )
        from models.shortcut_launcher import ShortcutLauncher

        from domain.app_directories import AppDirectories
        from domain.update_release import UpdateSource
        from main import Plugin

        plugin = Plugin()

        call_order: list[str] = []

        # Mocks for the call-order check.
        migration_service = MagicMock()
        migration_service.detect_retrodeck_path_change.side_effect = lambda: call_order.append(
            "detect_retrodeck_path_change"
        )
        save_sync_service = MagicMock()
        save_sync_service.record_save_directories_once = AsyncMock()

        sgdb_service = MagicMock()
        sgdb_service.prune_orphaned_artwork_cache = MagicMock()

        artwork_service = MagicMock()
        artwork_service.prune_orphaned_staging_artwork = MagicMock()

        download_service = MagicMock()

        leftover_tmp_cleanup_service = MagicMock()
        leftover_tmp_cleanup_service.cleanup_leftover_tmp_files = MagicMock()

        firmware_service = MagicMock()

        startup_healing_service = MagicMock()
        startup_healing_service.prune_stale_installed_roms.side_effect = lambda: call_order.append(
            "prune_stale_installed_roms"
        )
        startup_healing_service.reconcile_orphaned_sync_runs.side_effect = lambda: call_order.append(
            "reconcile_orphaned_sync_runs"
        )

        connection_service = MagicMock()
        connection_service.migrate_legacy_credentials = AsyncMock()

        update_outcome_service = MagicMock()

        wired_services = ServicesBundle(
            prune_conflicts=_make_prune_conflicts(),
            save_sync_service=save_sync_service,
            playtime_service=MagicMock(),
            sync_service=MagicMock(),
            download_service=download_service,
            rom_adoption_service=MagicMock(),
            rom_removal_service=MagicMock(),
            firmware_service=firmware_service,
            sgdb_service=sgdb_service,
            metadata_service=MagicMock(),
            achievements_service=MagicMock(),
            migration_service=migration_service,
            game_detail_service=MagicMock(),
            artwork_service=artwork_service,
            shortcut_removal_service=MagicMock(),
            settings_service=MagicMock(),
            core_service=MagicMock(),
            disc_service=MagicMock(),
            version_switch_service=MagicMock(),
            prune_service=MagicMock(shutdown=AsyncMock()),
            prune_lease_service=MagicMock(),
            data_inventory_service=MagicMock(),
            connection_service=connection_service,
            startup_healing_service=startup_healing_service,
            shortcut_relocation_service=MagicMock(),
            update_check_service=MagicMock(),
            update_outcome_service=update_outcome_service,
            launch_gate_service=MagicMock(),
            session_lifecycle_service=MagicMock(),
            game_process_service=MagicMock(),
            relaunch_options_resolver=MagicMock(),
            leftover_tmp_cleanup_service=leftover_tmp_cleanup_service,
        )

        bootstrap_result = BootstrapResult(
            adapters=AdapterBundle(
                http_adapter=MagicMock(),
                romm_api=MagicMock(),
                steam_config=MagicMock(),
                sgdb_adapter=MagicMock(),
                cover_art_file_store=MagicMock(),
                sgdb_artwork_cache=MagicMock(),
                download_file_store=MagicMock(),
                adoption_move=MagicMock(),
                firmware_file_store=MagicMock(),
                firmware_resolver=MagicMock(),
                platform_firmware_resolver=MagicMock(),
                migration_file_store=MagicMock(),
                rom_file_store=MagicMock(),
                save_file_store=MagicMock(),
                path_probe=MagicMock(),
                resolve_path=MagicMock(),
                core_info_provider=MagicMock(),
                save_locations=MagicMock(),
                renderer_rss=FakeRendererRss(),
                renderer_gc=FakeRendererGc(),
                game_process=FakeGameProcessControlAdapter(),
                resolve_upload_conflict=MagicMock(),
                compute_sync_action=MagicMock(),
                recovery_store=MagicMock(),
                recovery_inventory=MagicMock(),
                prune_artifacts=MagicMock(),
                steam_recovery=MagicMock(),
                latest_release=MagicMock(),
                update_failure=MagicMock(return_value=None),
            ),
            stores=StateBundle(
                settings={},
            ),
            callbacks=CallbackBundle(
                retrodeck_paths=MagicMock(),
                platform_core_reader=MagicMock(),
                m3u_support=MagicMock(),
                sandbox_launcher=MagicMock(return_value=None),
                system_extensions=MagicMock(),
                system_known=MagicMock(return_value=None),
                list_rom_dir_files=MagicMock(),
                settings_persister=MagicMock(),
                log_debug=MagicMock(),
                uow_factory=MagicMock(),
            ),
            runtime_adapters=RuntimeAdaptersBundle(
                clock=MagicMock(),
                uuid_gen=MagicMock(),
                sleeper=MagicMock(),
                hostname_provider=MagicMock(),
                machine_id_provider=MagicMock(),
            ),
            handles=BootstrapHandles(debug_logger=MagicMock()),
            directories=AppDirectories(
                config_dir="/fake/config",
                data_dir="/fake/data",
                cache_dir="/fake/cache",
                state_dir="/fake/state",
                runtime_dir="/fake/run",
                code_dir="/fake/code",
                bin_dir="/fake/home/.local/bin",
            ),
            launcher=ShortcutLauncher(path="/fake/home/.local/bin/tender-rom-launcher", at_home=True),
            user_agent="romm-tender/0.0.0-test",
        )
        events = FakeEventSink()

        with (
            patch("main.bootstrap", return_value=bootstrap_result),
            patch("main.wire_services", return_value=wired_services) as wire,
        ):
            await plugin._main(
                directories=bootstrap_result.directories,
                update_source=UpdateSource(release_api="http://127.0.0.1:9/", installed_program=False),
                user_home="/fake/home",
                logger=logging.getLogger("test_startup_order"),
                emit=events.emit,
                status=HostStatus(),
            )

        assert "detect_retrodeck_path_change" in call_order
        assert "prune_stale_installed_roms" in call_order
        assert call_order.index("detect_retrodeck_path_change") < call_order.index("prune_stale_installed_roms")
        # The save-directory backfill is started without holding start-up up.
        await plugin._save_directory_backfill
        save_sync_service.record_save_directories_once.assert_awaited_once_with()
        # Services emit through the sink itself, so its answer reaches them
        # unchanged: a claim goes back only when nobody heard the event.
        service_emit = wire.call_args.args[0].runtime.emit
        events.delivers = False
        assert await service_emit("probe", {"n": 1}) is False
        events.delivers = True
        assert await service_emit("probe", {"n": 2}) is True
        assert events.events == [("probe", {"n": 1}), ("probe", {"n": 2})]
        # A start compares its version with the last one's once, before the port.
        update_outcome_service.note_start.assert_called_once_with()
