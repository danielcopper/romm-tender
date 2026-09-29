"""What the endpoints do beyond forwarding a call, and the rule every one of them is classified under."""

from unittest.mock import AsyncMock

import pytest
from _conflict_rules import endpoints_with_rule
from _factories import _make_application, _make_conflict_rules, _make_services_bundle
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWorkFactory

from adapters.steam_config import SteamConfigAdapter
from host import HostStatus
from host.dispatch import route_names
from main import Endpoints
from services.settings import SettingsService, SettingsServiceConfig


@pytest.fixture
def settings():
    return {"romm_url": "", "romm_user": "", "romm_pass": "", "enabled_platforms": {}}


@pytest.fixture
def services(logger, home, settings):
    settings_service = SettingsService(
        config=SettingsServiceConfig(
            settings=settings,
            uow_factory=FakeUnitOfWorkFactory(),
            logger=logger,
            settings_persister=FakeSettingsPersister(),
            steam_config=SteamConfigAdapter(user_home=str(home), logger=logger),
            conflict_rules=_make_conflict_rules(),
        ),
    )
    return _make_services_bundle(settings_service=settings_service)


@pytest.fixture
def endpoints(services):
    return Endpoints(_make_application(services), HostStatus())


class TestLogLevel:
    @pytest.mark.asyncio
    async def test_debug_log_backward_compat(self, endpoints, settings, caplog):
        """debug_log reaches the log as a debug line of its own."""
        settings["log_level"] = "debug"
        endpoints.debug_log("test backward compat")

        assert [(r.levelname, r.message) for r in caplog.records] == [
            ("DEBUG", "[FE] test backward compat"),
        ]


class TestRefreshMigrationState:
    @pytest.mark.asyncio
    async def test_delegates_to_migration_service_refresh_state(self, endpoints, services):
        """The endpoint forwards to MigrationService.refresh_state."""
        sentinel = {
            "retrodeck": {"pending": True, "old_path": "/a", "new_path": "/b"},
        }
        services.migration_service.refresh_state = AsyncMock(return_value=sentinel)
        result = await endpoints.refresh_migration_state()
        services.migration_service.refresh_state.assert_awaited_once_with()
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_propagates_exceptions(self, endpoints, services):
        services.migration_service.refresh_state = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await endpoints.refresh_migration_state()


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
    # Installing the last seen release from the panel. The read touches no
    # RetroDECK state — it reads the stored release, the installer's record and
    # this process's memory. The press does not name the migration rule because
    # it asks the narrower question itself: a migration that is RUNNING makes it
    # wait, while one that is merely pending survives the restart and does not.
    "get_update_install_state",
    "install_update",
    # An installer that stopped without updating: the read and the dismissal
    # touch this program's own record of the attempt and nothing else.
    "get_stopped_update_attempt",
    "dismiss_stopped_update_attempt",
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
    """Every endpoint on Endpoints must be classified: either explicitly
    whitelisted (read-only / unblock pathway / non-retrodeck) or declaring the
    migration rule — a ``hold("<endpoint>", migration=True)`` or
    ``hold_start("<endpoint>", migration=True)`` at the entry of the use case
    it calls. Prevents a new endpoint from being silently unguarded against
    pending migration corruption."""

    def test_all_callables_either_whitelisted_or_declaring_the_rule(self):
        migration_ruled = endpoints_with_rule("migration")
        unclassified: list[str] = []
        for name in route_names(Endpoints):
            if name in _MIGRATION_RULE_WHITELIST:
                continue
            if name in migration_ruled:
                continue
            unclassified.append(name)

        assert not unclassified, (
            "Unclassified endpoints on Endpoints — every one must be in "
            "_MIGRATION_RULE_WHITELIST or declare the migration rule: "
            f"{sorted(unclassified)}"
        )

    def test_no_callable_both_declares_the_rule_and_is_whitelisted(self):
        """An endpoint that both declares the migration rule AND is whitelisted
        is silently passing the coverage check — likely a misclassification.
        Catch it."""
        migration_ruled = endpoints_with_rule("migration")
        double_classified = [
            name for name in route_names(Endpoints) if name in _MIGRATION_RULE_WHITELIST and name in migration_ruled
        ]

        assert not double_classified, (
            "Endpoints both whitelisted AND declaring the migration rule — "
            f"remove from one: {sorted(double_classified)}"
        )

    def test_whitelisted_callables_are_endpoints_declaring_no_rule(self):
        """Every name in _MIGRATION_RULE_WHITELIST must be an endpoint on
        Endpoints and must NOT declare the migration rule. Reads from the
        whitelist side, so a name left behind by a removed or renamed endpoint
        fails here instead of classifying nothing."""
        stale = sorted(_MIGRATION_RULE_WHITELIST - route_names(Endpoints))
        assert not stale, f"Whitelisted names that are not endpoints on Endpoints: {stale}"

        ruled = sorted(_MIGRATION_RULE_WHITELIST & endpoints_with_rule("migration"))
        assert not ruled, f"Whitelisted endpoints that also declare the migration rule: {ruled}"
