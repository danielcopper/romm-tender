import asyncio
import functools
import logging
import os
import sys
from dataclasses import asdict

backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

# Where the program sits, used only when nothing in the environment says. The
# installed case always says — the installer resolves the directories once and
# writes them into the unit — so this answers for a start by hand from a
# checkout, where the built panel and the shipped launcher sit one level up.
_CODE_DIR_FALLBACK = os.path.dirname(backend_dir)

from bootstrap import Application, build_application

from domain.app_directories import AppDirectories, resolve_directories
from domain.identity import VERSION
from domain.update_install import installer_environment
from domain.update_release import UpdateSource, resolve_update_source
from host import (
    LOCK_FILENAME,
    PORT_FILENAME,
    AlreadyRunningError,
    BackendBuild,
    CallDispatcher,
    EventSink,
    HostStatus,
    InjectionSetup,
    configure_logging,
    new_token,
    route,
    run_backend,
)


class Endpoints:
    """What the panel can call: one public method marked ``@route`` per endpoint.

    Every endpoint but ``get_host_status`` calls a use case on a service and
    hands its answer back; the few that do more translate an argument or an
    answer for the wire and nothing else. ``get_host_status`` answers from the
    host's own record of this run, which no service holds.
    """

    def __init__(self, app: Application, host_status: HostStatus) -> None:
        self._services = app.services
        self._host_status = host_status

    @route
    async def test_connection(self):
        return await self._services.connection_service.test_connection()

    @route
    async def connect_with_credentials(self, romm_url, username, password, allow_insecure_ssl=None):
        return await self._services.connection_service.establish_token(romm_url, username, password, allow_insecure_ssl)

    @route
    async def connect_with_token(self, romm_url, token, allow_insecure_ssl=None):
        return await self._services.connection_service.establish_user_token(romm_url, token, allow_insecure_ssl)

    @route
    async def connect_with_pairing_code(self, romm_url, code, allow_insecure_ssl=None):
        return await self._services.connection_service.establish_paired_token(romm_url, code, allow_insecure_ssl)

    @route
    async def sign_out(self):
        return await self._services.connection_service.sign_out()

    @route
    async def save_server_url(self, romm_url, allow_insecure_ssl=None):
        return await self._services.settings_service.save_server_url(romm_url, allow_insecure_ssl)

    @route
    async def save_custom_headers(self, headers):
        return await self._services.settings_service.save_custom_headers(headers)

    @route
    def frontend_log(self, level, message):
        self._services.settings_service.frontend_log(level, message)

    @route
    def debug_log(self, message):
        self._services.settings_service.frontend_log("debug", message)

    @route
    def save_log_level(self, level):
        return self._services.settings_service.save_log_level(level)

    @route
    def save_steam_input_setting(self, mode):
        return self._services.settings_service.save_steam_input_setting(mode)

    @route
    def save_preferred_region(self, region):
        return self._services.settings_service.save_preferred_region(region)

    @route
    def save_skip_preview(self, enabled):
        return self._services.settings_service.save_skip_preview(enabled)

    @route
    def get_known_regions(self):
        return self._services.settings_service.get_known_regions()

    @route
    async def apply_steam_input_setting(self):
        return await self._services.settings_service.apply_steam_input_setting()

    @route
    def fix_retroarch_input_driver(self):
        return self._services.settings_service.fix_retroarch_input_driver()

    @route
    def get_settings(self):
        return self._services.settings_service.get_settings()

    @route
    def get_retrodeck_status(self):
        return self._services.migration_service.get_retrodeck_status()

    @route
    def get_whitelist_settings(self):
        return self._services.settings_service.get_whitelist_settings()

    @route
    def update_whitelist_settings(self, disabled_defaults, custom_names):
        return self._services.settings_service.update_whitelist_settings(disabled_defaults, custom_names)

    @route
    async def get_cached_game_detail(self, app_id):
        return await self._services.game_detail_service.get_cached_game_detail(app_id)

    @route
    async def set_system_core(self, platform_slug, core_label):
        return await self._services.core_service.set_system_core(platform_slug, core_label)

    @route
    async def set_game_core(self, rom_id, label):
        return await self._services.core_service.set_game_core(rom_id, label)

    @route
    async def clear_game_core(self, rom_id):
        return await self._services.core_service.clear_game_core(rom_id)

    @route
    async def get_platform_core_info(self, rom_id):
        return await self._services.core_service.get_platform_core_info(rom_id)

    @route
    async def get_system_core_info(self, platform_slug):
        return await self._services.core_service.get_system_core_info(platform_slug)

    # ── Disc picker delegation to DiscService ──────────────

    @route
    async def get_disc_selection(self, rom_id):
        return await self._services.disc_service.get_disc_selection(rom_id)

    @route
    async def select_disc(self, rom_id, filename):
        return await self._services.disc_service.select_disc(rom_id, filename)

    # ── Version picker delegation to VersionSwitchService ──────────────

    @route
    async def get_version_list(self, app_id):
        return await self._services.version_switch_service.get_version_list(app_id)

    @route
    async def switch_version(self, app_id, target_rom_id, allow_stranded):
        return await self._services.version_switch_service.switch_version(app_id, target_rom_id, allow_stranded)

    @route
    async def get_prune_preview(self, request):
        return await self._services.prune_service.get_prune_preview(request)

    @route
    async def stage_prune_installed_selection(self, request):
        return await self._services.prune_service.stage_prune_installed_selection(request)

    @route
    async def start_prune(self, request):
        return await self._services.prune_service.start_prune(request)

    @route
    async def release_orphaned_prune_leases(self):
        return await self._services.prune_lease_service.release_orphaned_prune_leases()

    @route
    async def cancel_prune(self, run_id):
        return await self._services.prune_service.cancel_prune(run_id)

    @route
    async def report_prune_action(self, request):
        return await self._services.prune_service.report_prune_action(request)

    @route
    async def wait_for_prune_release(self, run_id):
        return await self._services.prune_service.wait_for_prune_release(run_id)

    @route
    async def release_prune_conflict_lease(self, lease_token):
        return await self._services.prune_lease_service.release_prune_conflict_lease(lease_token)

    @route
    async def renew_prune_conflict_lease(self, lease_token):
        return await self._services.prune_lease_service.renew_prune_conflict_lease(lease_token)

    # ── Firmware delegation to FirmwareService ──────────────

    @route
    async def get_firmware_status(self):
        return await self._services.firmware_service.get_firmware_status()

    @route
    async def get_platform_firmware_status(self, platform_slug):
        # One platform's BIOS state, which is a live per-system reading the
        # overview above deliberately does not pay. The page walks its own list
        # calling this; the order, and when to stop, are the caller's.
        return await self._services.firmware_service.get_platform_firmware_status(platform_slug)

    @route
    async def download_all_firmware(self, platform_slug):
        return await self._services.firmware_service.download_all_firmware(platform_slug)

    @route
    async def download_required_firmware(self, platform_slug):
        return await self._services.firmware_service.download_required_firmware(platform_slug)

    @route
    async def download_platform_firmware_file(self, platform_slug, file_name):
        return await self._services.firmware_service.download_platform_firmware_file(platform_slug, file_name)

    @route
    async def check_platform_bios(self, platform_slug):
        # Platform-level BIOS check (the frontend sends only the slug);
        # no per-game core to thread, so the system default drives the filter.
        return await self._services.firmware_service.check_platform_bios(platform_slug)

    @route
    async def get_bios_status(self, rom_id):
        return await self._services.game_detail_service.get_bios_status(rom_id)

    @route
    async def delete_platform_bios(self, platform_slug):
        return await self._services.firmware_service.delete_platform_bios(platform_slug)

    @route
    async def delete_bios_file(self, platform_slug, file_name):
        return await self._services.firmware_service.delete_bios_file(platform_slug, file_name)

    @route
    async def delete_bios_folder(self, platform_slug, folder_path):
        return await self._services.firmware_service.delete_bios_folder(platform_slug, folder_path)

    # ── Sync delegation to LibraryService ─────────────────────

    @route
    async def get_platforms(self):
        return await self._services.sync_service.get_platforms()

    @route
    async def save_platform_sync(self, platform_id, enabled):
        return await self._services.sync_service.save_platform_sync(platform_id, enabled)

    @route
    async def set_all_platforms_sync(self, enabled):
        return await self._services.sync_service.set_all_platforms_sync(enabled)

    @route
    async def get_collections(self):
        return await self._services.sync_service.get_collections()

    @route
    async def save_collection_sync(self, collection_id, kind, enabled):
        return await self._services.sync_service.save_collection_sync(collection_id, kind, enabled)

    @route
    async def save_collections_sync(self, collection_ids, kind, enabled):
        return await self._services.sync_service.save_collections_sync(collection_ids, kind, enabled)

    @route
    def save_collection_platform_groups(self, enabled):
        return self._services.settings_service.save_collection_platform_groups(enabled)

    @route
    def set_collection_owner_scope(self, scope):
        return self._services.settings_service.set_collection_owner_scope(scope)

    @route
    def set_collection_naming_mode(self, mode):
        return self._services.settings_service.set_collection_naming_mode(mode)

    @route
    async def start_sync(self):
        return await self._services.sync_service.start_sync()

    @route
    def cancel_sync(self, run_id):
        return self._services.sync_service.cancel_sync(run_id)

    @route
    def sync_heartbeat(self):
        return self._services.sync_service.sync_heartbeat()

    @route
    async def sync_preview(self):
        return await self._services.sync_service.sync_preview()

    @route
    async def sync_apply_delta(self, preview_id):
        return await self._services.sync_service.sync_apply_delta(preview_id)

    @route
    def sync_cancel_preview(self):
        return self._services.sync_service.sync_cancel_preview()

    @route
    def get_pending_preview(self):
        return self._services.sync_service.get_pending_preview()

    @route
    def get_sync_status(self):
        return self._services.sync_service.get_sync_status()

    @route
    async def get_session_budget_status(self):
        return await self._services.sync_service.get_session_budget_status()

    @route
    async def report_unit_results(self, rom_id_to_app_id, run_id, unit_id, chunk_index):
        return await self._services.sync_service.report_unit_results(rom_id_to_app_id, run_id, unit_id, chunk_index)

    @route
    def get_registry_platforms(self):
        return self._services.sync_service.get_registry_platforms()

    @route
    async def remove_platform_shortcuts(self, platform_slug):
        return await self._services.shortcut_removal_service.remove_platform_shortcuts(platform_slug)

    @route
    async def remove_all_shortcuts(self):
        return await self._services.shortcut_removal_service.remove_all_shortcuts()

    @route
    async def report_removal_results(self, removed_rom_ids, lease_token):
        return await self._services.shortcut_removal_service.report_removal_results(removed_rom_ids, lease_token)

    @route
    async def reconcile_shortcuts(self, live_app_ids):
        return await self._services.shortcut_removal_service.reconcile_live_shortcuts(live_app_ids)

    @route
    async def get_artwork_base64(self, rom_id):
        return await self._services.artwork_service.get_artwork_base64(rom_id)

    @route
    async def fetch_cover_base64(self, rom_id):
        return await self._services.artwork_service.fetch_cover_base64(rom_id)

    @route
    async def refresh_cover_artwork(self, rom_id):
        return await self._services.artwork_service.refresh_cover(int(rom_id))

    @route
    async def cleanup_orphaned_grid_images(self, live_app_ids, dry_run):
        return await self._services.artwork_service.cleanup_orphaned_grid_images(live_app_ids, dry_run)

    @route
    async def clear_sync_cache(self):
        return await self._services.sync_service.clear_sync_cache()

    @route
    def get_sync_stats(self):
        return self._services.sync_service.get_sync_stats()

    @route
    async def get_data_inventory(self):
        return await self._services.data_inventory_service.get_data_inventory()

    @route
    def get_sync_runs(self):
        return self._services.sync_service.get_sync_runs()

    @route
    async def evaluate_launch(self, steam_app_id):
        verdict = await self._services.launch_gate_service.evaluate(steam_app_id)
        return verdict if isinstance(verdict, dict) else asdict(verdict)

    @route
    async def check_local_drift(self, rom_id):
        return await self._services.launch_gate_service.check_local_drift(rom_id)

    @route
    async def get_rom_relaunch_options(self, rom_id):
        """Return one lease-bearing relaunch item, a refusal, or ``None``.

        Both launch funnels — the game-detail Play button and Steam's
        direct-launch watcher — re-confirm the shortcut's launch command from
        this just before a launch to heal mid-session ``launch_options`` drift
        (#1150). The lease covers the subsequent frontend Steam write.
        """
        return await self._services.relaunch_options_resolver.get_rom_relaunch_options(rom_id)

    @route
    async def probe_reachability(self):
        return await self._services.connection_service.probe_reachability()

    @route
    async def refresh_save_status(self, rom_id):
        return await self._services.save_sync_service.refresh_save_status(int(rom_id))

    @route
    async def stop_running_game(self, rom_id):
        """Terminate the RetroDECK instance running *rom_id*.

        Backs the game-detail running overlay's Stop Game action. Steam's own
        ``TerminateApp`` cannot end these games — the shortcut execs ``flatpak
        run``, whose portal-started sandbox is not under Steam's reaper — so the
        kill runs backend-side over the flatpak instance's host processes. The
        ROM is what picks the instance: RetroDECK can have several live at once,
        and only the one running this ROM may be signalled.
        """
        return await self._services.game_process_service.stop_running_game(int(rom_id))

    @route
    async def finalize_game_session(self, rom_id):
        result = await self._services.session_lifecycle_service.finalize(rom_id)
        return result if isinstance(result, dict) else asdict(result)

    # ── Download delegation to DownloadService ──────────────

    @route
    async def start_download(
        self, rom_id, replace_existing=False, candidate_path=None, collision_choice=None, page_saw_candidate=False
    ):
        """Start a download, refusing when this game is already on the device.

        ``candidate_path`` names the file the adopt dialog was showing when the
        user chose Download over it: with ``replace_existing`` it is removed and
        its saves carried to the canonical name, which is what that dialog's
        second confirmation promises. ``collision_choice`` answers the save
        collision that carry can raise. ``page_saw_candidate`` reports what the
        game page told the user, so a page that found a copy can never end in a
        silent download.
        """
        return await self._services.download_service.start_download(
            rom_id, replace_existing, candidate_path, collision_choice, page_saw_candidate
        )

    @route
    async def adopt_existing_rom(self, rom_id, candidate_path=None, collision_choice=None):
        """Record content already on disk as this ROM's install, without downloading.

        ``candidate_path`` names an entry elsewhere in the platform directory when
        the user picked one the search offered — it is renamed into place, saves
        and savestates with it. ``collision_choice`` answers the second dialog
        (``"overwrite"`` / ``"keep"``) and is null until that dialog has been
        shown.
        """
        return await self._services.rom_adoption_service.adopt_existing_rom(rom_id, candidate_path, collision_choice)

    @route
    async def verify_existing_content(self, rom_id, candidate_path=None):
        """Compare content already on disk against RomM's checksums for this ROM.

        ``candidate_path`` picks the entry to check when the user is deciding
        about one the search offered; null checks this ROM's own target path.
        """
        return await self._services.rom_adoption_service.verify_existing_content(rom_id, candidate_path)

    @route
    def cancel_download(self, rom_id):
        return self._services.download_service.cancel_download(rom_id)

    @route
    def pause_download(self, rom_id):
        return self._services.download_service.pause_download(rom_id)

    @route
    async def resume_download(self, rom_id):
        return await self._services.download_service.resume_download(rom_id)

    @route
    def get_download_queue(self):
        return self._services.download_service.get_download_queue()

    @route
    def clear_completed_downloads(self):
        return self._services.download_service.clear_completed_downloads()

    @route
    def get_installed_rom(self, rom_id):
        return self._services.download_service.get_installed_rom(rom_id)

    @route
    async def remove_rom(self, rom_id):
        return await self._services.rom_removal_service.remove_rom(rom_id)

    @route
    async def uninstall_all_roms(self):
        return await self._services.rom_removal_service.uninstall_all_roms()

    # ── Save Sync / Playtime delegation to services ──────────

    @route
    async def ensure_device_registered(self):
        return await self._services.save_sync_service.ensure_device_registered()

    @route
    async def list_devices(self):
        return await self._services.save_sync_service.list_devices()

    @route
    async def get_save_status(self, rom_id):
        return await self._services.save_sync_service.get_save_status(rom_id)

    @route
    def check_core_change(self, rom_id):
        return self._services.save_sync_service.check_core_change(rom_id)

    @route
    async def pre_launch_sync(self, rom_id):
        return await self._services.save_sync_service.pre_launch_sync(rom_id)

    @route
    async def sync_rom_saves(self, rom_id):
        return await self._services.save_sync_service.sync_rom_saves(rom_id)

    @route
    async def get_save_slots(self, rom_id):
        return await self._services.save_sync_service.get_save_slots(rom_id)

    @route
    async def get_slot_saves(self, rom_id, slot):
        return await self._services.save_sync_service.get_slot_saves(rom_id, slot)

    @route
    async def switch_slot(self, rom_id, new_slot):
        return await self._services.save_sync_service.switch_slot(rom_id, new_slot)

    @route
    async def get_slot_delete_info(self, rom_id, slot):
        return await self._services.save_sync_service.get_slot_delete_info(rom_id, slot)

    @route
    async def delete_slot(self, rom_id, slot):
        return await self._services.save_sync_service.delete_slot(rom_id, slot)

    @route
    def is_save_tracking_configured(self, rom_id):
        return self._services.save_sync_service.is_save_tracking_configured(rom_id)

    @route
    async def get_save_setup_info(self, rom_id):
        return await self._services.save_sync_service.get_save_setup_info(rom_id)

    @route
    async def confirm_slot_choice(
        self, rom_id, chosen_slot, migrate=False, migrate_from_slot=None, use_server_on_conflict=False
    ):
        return await self._services.save_sync_service.confirm_slot_choice(
            rom_id, chosen_slot, migrate, migrate_from_slot, use_server_on_conflict
        )

    @route
    async def sync_all_saves(self):
        return await self._services.save_sync_service.sync_all_saves()

    @route
    async def resolve_sync_conflict(self, rom_id, filename, server_save_id, action):
        return await self._services.save_sync_service.resolve_sync_conflict(rom_id, filename, server_save_id, action)

    @route
    def get_save_sync_settings(self):
        return self._services.save_sync_service.get_save_sync_settings()

    @route
    async def update_save_sync_settings(self, settings):
        return await self._services.save_sync_service.update_save_sync_settings(settings)

    @route
    async def delete_local_saves(self, rom_id):
        return await self._services.save_sync_service.delete_local_saves(rom_id)

    @route
    async def count_platform_saves(self, platform_slug):
        return await self._services.save_sync_service.count_platform_saves(platform_slug)

    @route
    async def delete_platform_saves(self, platform_slug):
        return await self._services.save_sync_service.delete_platform_saves(platform_slug)

    @route
    async def saves_list_file_versions(self, rom_id, slot, filename):
        return await self._services.save_sync_service.list_file_versions(rom_id, slot, filename)

    @route
    async def saves_rollback_to_version(self, rom_id, slot, save_id):
        return await self._services.save_sync_service.rollback_to_version(rom_id, slot, save_id)

    @route
    async def copy_save_to_slot(self, rom_id, save_id, target_slot):
        return await self._services.save_sync_service.copy_save_to_slot(rom_id, save_id, target_slot)

    @route
    async def record_session_start(self, rom_id):
        return await self._services.playtime_service.record_session_start(rom_id)

    @route
    def get_all_playtime(self):
        return self._services.playtime_service.get_all_playtime()

    @route
    async def reconcile_playtime(self, rom_id):
        return await self._services.playtime_service.reconcile_playtime(rom_id)

    @route
    def get_playtime_scope_notice(self):
        """Report whether the token lacks the play-session read scope.

        Returns ``{"pending": bool}``. ``pending`` is set when a reconcile GET
        403'd because the stored token predates the ``roms.user.read`` scope
        (#1280) — the frontend surfaces a persistent "sign in again to enable
        cross-device playtime" banner. Non-consuming (mirrors
        ``get_settings_reset_notice``): the durable flag is cleared only by a
        later successful reconcile GET or a fresh sign-in, so the banner stays up
        across backend restarts until the user re-authenticates.
        """
        return self._services.playtime_service.get_scope_notice()

    # ── SGDB delegation to SteamGridService ───────────────────────

    @route
    async def get_sgdb_artwork_base64(self, rom_id, asset_type_num):
        return await self._services.sgdb_service.get_sgdb_artwork_base64(rom_id, asset_type_num)

    @route
    async def verify_sgdb_api_key(self, api_key=None):
        return await self._services.sgdb_service.verify_sgdb_api_key(api_key)

    @route
    def save_sgdb_api_key(self, api_key):
        return self._services.sgdb_service.save_sgdb_api_key(api_key)

    @route
    async def save_shortcut_icon(self, app_id, icon_base64):
        return await self._services.sgdb_service.save_shortcut_icon(app_id, icon_base64)

    @route
    async def get_sgdb_resolution(self, rom_id):
        return await self._services.sgdb_service.get_sgdb_resolution(rom_id)

    @route
    async def search_sgdb_games(self, term):
        return await self._services.sgdb_service.search_sgdb_games(term)

    @route
    async def apply_sgdb_game_id(self, rom_id, sgdb_id):
        return await self._services.sgdb_service.apply_sgdb_game_id(rom_id, sgdb_id)

    # ── Metadata delegation to MetadataService ────────────────

    @route
    def get_rom_metadata(self, rom_id):
        return self._services.metadata_service.get_rom_metadata(rom_id)

    @route
    def get_metadata_cache_page(self, offset, limit):
        return self._services.metadata_service.get_metadata_cache_page(offset, limit)

    @route
    def get_app_id_rom_id_map(self):
        return self._services.metadata_service.get_app_id_rom_id_map()

    @route
    async def get_installed_relaunch_options(self):
        """Return lease-bearing relaunch items for installed and bound ROMs.

        The frontend uses them to heal Steam-shortcut drift at startup (#1043).
        """
        return await self._services.startup_healing_service.get_installed_relaunch_options()

    # ── Achievements delegation to AchievementsService ───────

    @route
    async def get_achievements(self, rom_id):
        return await self._services.achievements_service.get_achievements(rom_id)

    @route
    async def get_achievement_progress(self, rom_id):
        return await self._services.achievements_service.get_achievement_progress(rom_id)

    # ── Migration delegation to MigrationService ──────────────

    @route
    async def migrate_retrodeck_files(self, conflict_strategy=None):
        return await self._services.migration_service.migrate_retrodeck_files(conflict_strategy)

    @route
    async def get_migration_status(self):
        return await self._services.migration_service.get_migration_status()

    @route
    def dismiss_retrodeck_migration(self):
        return self._services.migration_service.dismiss_retrodeck_migration()

    @route
    async def refresh_migration_state(self):
        return await self._services.migration_service.refresh_state()

    @route
    def get_settings_reset_notice(self):
        return self._services.settings_service.get_settings_reset_notice()

    @route
    def dismiss_settings_reset_notice(self):
        return self._services.settings_service.dismiss_settings_reset_notice()

    @route
    async def get_update_notice(self):
        """Report the last available release a check saw, and whether the card should say so.

        Returns ``{"available", "newer", "latest_version", "current_version",
        "enabled", "installed_program"}``. ``available`` is the card itself: a
        newer release with its tarball and checksum file attached exists, and
        the user has not dismissed that exact version — whatever the check's
        switch says. ``newer`` is the first of those alone, for the Settings
        section that states the versions whether or not the card was dismissed.
        ``installed_program`` says whether this process is the installed program
        an update could replace — False for a run from a checkout.

        GitHub is asked at most once a day and the answer is kept, so a reload
        inside that window shows the card without a request; with the check
        switched off it is not asked at all, and the stored answer is reported.
        Every failure is silent: no network, an unreadable answer or a release
        whose tarball is not attached yet leave the previous answer standing.
        """
        return await self._services.update_check_service.get_update_notice()

    @route
    async def check_for_update_now(self):
        """Ask GitHub now, past the daily throttle and past any dismissal.

        Answers everything :meth:`get_update_notice` does, plus ``reached`` —
        whether the release read answered at all, which is what lets the Settings
        section tell "nothing newer" from "nothing found out". A dismissed card
        comes back. The check's switch does not hold this back: it governs only
        what the program asks by itself.
        """
        return await self._services.update_check_service.check_for_update_now()

    @route
    def dismiss_update_notice(self, version):
        """Record that the user waved away the card for one release version.

        Per version, so the next release raises the card again. Returns
        ``{"success": True}``, or the canonical failure shape for a version that
        is not a non-empty string.
        """
        return self._services.update_check_service.dismiss_update_notice(version)

    @route
    def set_update_check_enabled(self, enabled):
        """Persist whether the daily release check may ask GitHub.

        On by default; with it off nothing is fetched but what Check now asks
        for. Returns ``{"success": True}``, or the canonical failure shape for
        a non-boolean value.
        """
        return self._services.update_check_service.set_update_check_enabled(enabled)

    @route
    async def get_update_outcome(self):
        """Report what the panel owes the user about the last update.

        Returns ``{"announce_version", "announce_direction", "toast_owed",
        "failure", "failure_dismissed"}``. ``announce_version`` is the version
        this process moved to, until :meth:`dismiss_update_announcement` says
        the user waved its card away — ``None`` on every other start — and
        ``announce_direction`` which way it moved: ``"updated"`` to a later
        release, ``"back"`` to an earlier one, ``None`` exactly when
        ``announce_version`` is. ``toast_owed`` is true until
        :meth:`acknowledge_update_toast` says the panel raised its toast, and
        false whenever ``announce_version`` is ``None``. ``failure`` is the
        installer's record of an update that did not go through,
        ``{"attempted_version", "restored_version", "rolled_back_at", "kind"}``
        with ``kind`` ``"rollback"``, ``"check"`` (refused by the pre-install
        check) or ``"unknown"`` (a kind a later installer wrote), read afresh so
        it goes when the installer removes it; ``None`` where there is none, or
        where the running version is not the one it names as still running.
        ``failure_dismissed`` says the user waved away that exact record, and
        ``failure_toast_owed`` that its toast has not been raised yet — until
        :meth:`acknowledge_update_failure_toast` names it, and never for a
        dismissed record.
        """
        return await self._services.update_outcome_service.get_update_outcome()

    @route
    def acknowledge_update_toast(self):
        """Record that the panel raised the announcement's toast, so a reloaded panel does not raise it again.

        The card stays. Returns ``{"success": True}``.
        """
        return self._services.update_outcome_service.acknowledge_update_toast()

    @route
    async def acknowledge_update_failure_toast(self, rolled_back_at):
        """Record that the panel raised the toast for one update that did not go through, for every later start.

        Per record — named by its ``rolled_back_at``. Returns ``{"success":
        True}``, or the canonical failure shape for a stamp that is not a
        non-empty string.
        """
        return await self._services.update_outcome_service.acknowledge_update_failure_toast(rolled_back_at)

    @route
    def dismiss_update_announcement(self):
        """Record that the user waved away the announcement's card, for the rest of this process.

        Returns ``{"success": True}``.
        """
        return self._services.update_outcome_service.dismiss_update_announcement()

    @route
    def dismiss_update_failure(self, rolled_back_at):
        """Record that the user waved away the card for one update that did not go through.

        Per record — named by its ``rolled_back_at`` — so the next record the
        installer writes raises the card again. Returns ``{"success": True}``,
        or the canonical failure shape for a stamp that is not a non-empty
        string.
        """
        return self._services.update_outcome_service.dismiss_update_failure(rolled_back_at)

    @route
    async def get_update_install_state(self):
        """Report whether the panel may offer to install the last seen release, and what a press waits for.

        Returns ``{"offered", "version", "wait_reasons", "paused_downloads",
        "attempt", "try_again"}``. ``offered`` holds only on the installed
        program with a stored release newer than the running version, which
        ``version`` names, whatever the check's switch says. ``wait_reasons``
        lists, as ``{"reason", ...}``, everything a press would be refused for
        now — ``app_running`` (with ``apps``), ``running_apps_unknown``,
        ``library_sync``, ``rom_downloads``, ``save_sync``,
        ``firmware_downloads``, ``save_directory_move``,
        ``removed_games_cleanup``, ``retrodeck_migration``, ``other_work``,
        ``interface_reload_limit`` (with ``frees_at``, epoch seconds) and
        ``interface_reload_limit_unknown``.
        ``paused_downloads`` counts the paused ROM downloads the restart would
        cancel. ``attempt`` is the latest attempt's ``{"version", "step",
        "bytes_done", "bytes_total", "failure"}``, or ``None``; ``try_again``
        says the offered version already failed once.
        """
        return await self._services.update_install_service.get_update_install_state()

    @route
    async def install_update(self, version):
        """Download, verify and hand *version* to the installer; answer once the attempt has started.

        Returns ``{"success": True}``, or the canonical failure shape with
        ``reason`` ``update_in_progress``, ``not_offered``,
        ``version_changed``, or ``update_waiting`` together with
        ``wait_reasons``. From an accepted press on, every endpoint that a
        pending RetroDECK migration refuses answers ``blocked_by_update``
        instead, and so does starting the migration itself, until the attempt
        fails or the installer replaces this process; the attempt reports its
        steps through the ``update_install_progress`` event.
        """
        return await self._services.update_install_service.install_update(version)

    @route
    def get_stopped_update_attempt(self):
        """Report an update attempt whose installer stopped without updating, as an earlier start left it.

        Returns ``{"attempted_version", "from_version", "started_at",
        "toast_owed"}``, or ``None`` where there is none, it was dismissed, a
        new attempt has started since, or the installer's unit had not ended
        yet — a judgement made later is pushed as ``update_attempt_stopped``.
        ``toast_owed`` says its toast has not been raised yet.
        """
        return self._services.update_install_service.get_stopped_update_attempt()

    @route
    async def acknowledge_stopped_update_attempt_toast(self, started_at):
        """Record that the panel raised the toast for one stopped attempt, named by its ``started_at``, for good.

        Returns ``{"success": True}``, or the canonical failure shape for a
        stamp that is not a non-empty string.
        """
        return await self._services.update_install_service.acknowledge_stopped_attempt_toast(started_at)

    @route
    async def dismiss_stopped_update_attempt(self):
        """Wave away the notice of an installer that stopped without updating. Returns ``{"success": True}``."""
        return await self._services.update_install_service.dismiss_stopped_attempt()

    @route
    def get_update_attempt_toast(self):
        """Report the failed attempt of this process whose toast the panel has not raised yet.

        Returns ``{"attempt", "version", "failure"}`` — ``attempt`` numbers the
        press, and is what :meth:`acknowledge_update_attempt_toast` names — or
        ``None`` where none is owed: none failed, a new press started, the
        toast was acknowledged, or the failure is ``new_version_does_not_start``,
        whose record's toast tells it.
        """
        return self._services.update_install_service.get_update_attempt_toast()

    @route
    async def acknowledge_update_attempt_toast(self, attempt):
        """Record that the panel raised the toast for the failed attempt numbered *attempt*.

        Returns ``{"success": True}``, or the canonical failure shape for a
        number that is not an integer.
        """
        return await self._services.update_install_service.acknowledge_update_attempt_toast(attempt)

    @route
    async def get_update_output(self, rolled_back_at):
        """Report what the installer printed for one failed update, read from the journal.

        *rolled_back_at* names the installer's record by its stamp; ``None``
        names this process's latest attempt, where it failed after its
        installer ran. Returns ``{"success": True, "ran_at", "installer",
        "new_version", "missing"}``: ``installer`` and, after a rollback,
        ``new_version`` are ``{"lines", "earlier"}`` with the admission token
        hidden, ``ran_at`` is when the installer's run began in epoch seconds,
        and where the journal holds no such run both are ``None`` and
        ``missing`` is ``"rotated"``, ``"terminal"`` or ``"empty"``. Otherwise
        the canonical failure shape, with ``reason`` ``not_found``,
        ``invalid_value`` or ``journal_unreadable``.
        """
        return await self._services.update_output_service.get_update_output(rolled_back_at)

    @route
    async def get_shortcut_relocation(self):
        """Report which Steam shortcuts still have to be pointed at the launcher.

        Returns a discriminated status union: ``{"status": "done"}`` when no
        shortcut of ours names a launcher other than the bin root's any more;
        ``{"status": "outstanding", "exe", "start_dir", "app_ids"}`` naming
        exactly the shortcuts to rewrite and what to write on them; or
        ``{"status": "blocked", "message"}`` when nothing may be rewritten yet,
        which is the answer to every uncertainty — pointing a shortcut at a
        launcher that is not there stops its game from starting.

        The reading is the backend's because ``shortcuts.vdf`` holds every
        shortcut's ``exe`` and one 315 KB parse answers for all of them; the
        frontend's own route to the same fact is a ``RegisterForAppDetails``
        per shortcut, which loads and caches a fat details object each time
        (ADR-0032). Answered without reading anything at all once the transition
        is stamped complete.
        """
        return await self._services.shortcut_relocation_service.get_shortcut_relocation()

    @route
    def get_host_status(self):
        """Report what only the process hosting this backend knows about its own run.

        Returns ``{"port": int, "failed_startup_steps": [str],
        "dropped_messages": int}``.

        Both counts exist because the alternative is a fault that lives only in a
        log file. A start-up repair that fails on every start would otherwise be
        noticed by nobody who did not go looking, and a panel and backend that
        disagree about the protocol would show as nothing at all — the messages
        are dropped and the connection is deliberately kept. Neither is worth an
        event: an event is a statement about a moment, and both of these are
        states, read when the panel opens.
        """
        return {
            "port": self._host_status.port,
            "failed_startup_steps": list(self._host_status.failed_startup_steps),
            "dropped_messages": self._host_status.count_dropped_messages(),
        }


async def build_backend(
    *,
    directories: AppDirectories,
    update_source: UpdateSource,
    installer_environment: tuple[tuple[str, str], ...],
    user_home: str,
    logger: logging.Logger,
    status: HostStatus,
    events: EventSink,
) -> BackendBuild:
    """Build the :class:`Application`, run its start-up repairs, and answer with what the host needs of it.

    A start-up repair that fails is recorded on *status*, which is where
    ``get_host_status`` reads it from.
    """
    app = build_application(
        directories=directories,
        update_source=update_source,
        installer_environment=installer_environment,
        user_home=user_home,
        logger=logger,
        loop=asyncio.get_running_loop(),
        emit=events.emit,
        steam=status.steam,
    )
    app.run_startup_repairs(status.record_failed_step)
    logger.info("Tender backend loaded")
    return BackendBuild(
        dispatcher=CallDispatcher(Endpoints(app, status), logger),
        server_identity=app.user_agent,
        open_network=app.open_network,
        shutdown=app.shutdown,
    )


def run() -> int:
    """Run the backend as a process of its own, until it is asked to stop.

    The whole start, in the order the host needs it: resolve where the
    directories are, mint the admission token, configure logging around it, then
    hand the host :func:`build_backend` to build the application and the
    endpoints to dispatch onto.

    Synchronous on purpose. Everything here is path and environment work that
    belongs before a loop exists — and the token has to be minted before the
    first log line, because the formatter that keeps it out of the log file
    is built with the file handler and needs the token to redact.

    Answers 0 for a clean stop, 1 when another backend already holds the lock.
    """
    user_home = os.path.expanduser("~")
    directories = resolve_directories(os.environ, user_home, _CODE_DIR_FALLBACK)
    update_source = resolve_update_source(os.environ, _CODE_DIR_FALLBACK)
    installer_env = installer_environment(os.environ, directories, sys.executable)
    token = new_token()
    logger = configure_logging(directories.state_dir, token)
    logger.info(f"host: code {directories.code_dir}, data {directories.data_dir}, cache {directories.cache_dir}")
    status = HostStatus()
    events = EventSink(logger)
    build = functools.partial(
        build_backend,
        directories=directories,
        update_source=update_source,
        installer_environment=installer_env,
        user_home=user_home,
        logger=logger,
        status=status,
        events=events,
    )

    # Handed in, never searched for: a host that looked for its own build
    # output relative to ``__file__`` would be the only part of this backend
    # that knew the repository's layout. Resolved once here because the
    # server serves this directory and the injector hashes the bundles in
    # it, and two spellings of one directory are free to drift apart.
    static_root = os.path.join(directories.code_dir, "dist")
    try:
        asyncio.run(
            run_backend(
                build=build,
                events=events,
                status=status,
                static_root=static_root,
                # The lock lies beside what it protects, which is the database.
                lock_path=os.path.join(directories.data_dir, LOCK_FILENAME),
                port_file_path=os.path.join(directories.runtime_dir, PORT_FILENAME),
                logger=logger,
                token=token,
                injection=InjectionSetup.from_environment(
                    os.environ,
                    static_root=static_root,
                    state_dir=directories.state_dir,
                    user_home=user_home,
                    version=VERSION,
                ),
            )
        )
    except AlreadyRunningError as exc:
        print(f"tender: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Process entry point."""
    return run()


if __name__ == "__main__":
    sys.exit(main())
