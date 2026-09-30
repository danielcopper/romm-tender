"""Coverage for the delegation seams of ``main.Endpoints``.

Every endpoint but ``get_host_status`` forwards to a backend service.
These tests exercise each delegation by:

- building ``Endpoints`` over an ``Application`` whose services are all
  ``MagicMock``s,
- invoking the endpoint,
- asserting the service method was called with the expected args,
- asserting the return value is propagated.

For a handful of representative endpoints we also assert that
exceptions raised by the underlying service propagate through the
endpoint (no swallowed errors). These tests are aimed at line coverage
of the delegation surface — they intentionally do NOT exercise the
underlying service logic, which is covered elsewhere.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from _factories import _make_application, _make_services_bundle

from host import HostStatus
from main import Endpoints


@pytest.fixture
def services():
    """Every service the endpoints reach, each a ``MagicMock``."""
    return _make_services_bundle()


@pytest.fixture
def endpoints(services):
    """The real ``Endpoints`` over an ``Application`` holding *services*."""
    return Endpoints(_make_application(services), HostStatus())


# ── Settings / connection / log-level callables ───────────────────────


class TestSettingsCallableDelegation:
    @pytest.mark.asyncio
    async def test_connect_with_credentials_delegates(self, endpoints, services):
        services.connection_service.establish_token = AsyncMock(return_value={"success": True})
        result = await endpoints.connect_with_credentials("http://x", "u", "p", True)
        services.connection_service.establish_token.assert_awaited_once_with("http://x", "u", "p", True)
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_save_server_url_delegates(self, endpoints, services):
        services.settings_service.save_server_url = AsyncMock(return_value={"success": True})
        result = await endpoints.save_server_url("http://x", True)
        services.settings_service.save_server_url.assert_awaited_once_with("http://x", True)
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_save_custom_headers_delegates(self, endpoints, services):
        services.settings_service.save_custom_headers = AsyncMock(return_value={"success": True})
        result = await endpoints.save_custom_headers([])
        services.settings_service.save_custom_headers.assert_awaited_once_with([])
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_frontend_log_delegates(self, endpoints, services):
        endpoints.frontend_log("warn", "msg")
        services.settings_service.frontend_log.assert_called_once_with("warn", "msg")

    @pytest.mark.asyncio
    async def test_save_log_level_delegates(self, endpoints, services):
        services.settings_service.save_log_level.return_value = {"success": True}
        result = endpoints.save_log_level("debug")
        services.settings_service.save_log_level.assert_called_once_with("debug")
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_save_steam_input_setting_delegates(self, endpoints, services):
        services.settings_service.save_steam_input_setting.return_value = {"ok": True}
        result = endpoints.save_steam_input_setting("default")
        services.settings_service.save_steam_input_setting.assert_called_once_with("default")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_apply_steam_input_setting_delegates(self, endpoints, services):
        services.settings_service.apply_steam_input_setting = AsyncMock(return_value={"ok": True})
        result = await endpoints.apply_steam_input_setting()
        services.settings_service.apply_steam_input_setting.assert_awaited_once_with()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_fix_retroarch_input_driver_delegates(self, endpoints, services):
        services.settings_service.fix_retroarch_input_driver.return_value = {"ok": True}
        result = endpoints.fix_retroarch_input_driver()
        services.settings_service.fix_retroarch_input_driver.assert_called_once_with()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_settings_delegates(self, endpoints, services):
        services.settings_service.get_settings.return_value = {"romm_url": "x"}
        result = endpoints.get_settings()
        services.settings_service.get_settings.assert_called_once_with()
        assert result == {"romm_url": "x"}

    @pytest.mark.asyncio
    async def test_get_whitelist_settings_delegates(self, endpoints, services):
        services.settings_service.get_whitelist_settings.return_value = {"disabled_defaults": []}
        result = endpoints.get_whitelist_settings()
        services.settings_service.get_whitelist_settings.assert_called_once_with()
        assert result == {"disabled_defaults": []}

    @pytest.mark.asyncio
    async def test_update_whitelist_settings_delegates(self, endpoints, services):
        services.settings_service.update_whitelist_settings.return_value = {"success": True}
        result = endpoints.update_whitelist_settings(["a"], ["b"])
        services.settings_service.update_whitelist_settings.assert_called_once_with(["a"], ["b"])
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_save_collection_platform_groups_delegates(self, endpoints, services):
        services.settings_service.save_collection_platform_groups.return_value = {"ok": True}
        result = endpoints.save_collection_platform_groups(True)
        services.settings_service.save_collection_platform_groups.assert_called_once_with(True)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_set_collection_owner_scope_delegates(self, endpoints, services):
        services.settings_service.set_collection_owner_scope.return_value = {"success": True}
        result = endpoints.set_collection_owner_scope("own")
        services.settings_service.set_collection_owner_scope.assert_called_once_with("own")
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_get_settings_reset_notice_delegates(self, endpoints, services):
        services.settings_service.get_settings_reset_notice.return_value = {"pending": False, "backed_up_to": None}
        result = endpoints.get_settings_reset_notice()
        services.settings_service.get_settings_reset_notice.assert_called_once_with()
        assert result == {"pending": False, "backed_up_to": None}

    @pytest.mark.asyncio
    async def test_debug_log_routes_through_frontend_log(self, endpoints, services):
        endpoints.debug_log("hello")
        services.settings_service.frontend_log.assert_called_once_with("debug", "hello")


class TestConnectionCallableDelegation:
    @pytest.mark.asyncio
    async def test_test_connection_delegates(self, endpoints, services):
        services.connection_service.test_connection = AsyncMock(return_value={"success": True})
        result = await endpoints.test_connection()
        services.connection_service.test_connection.assert_awaited_once_with()
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_sign_out_delegates(self, endpoints, services):
        services.connection_service.sign_out = AsyncMock(return_value={"success": True})
        result = await endpoints.sign_out()
        services.connection_service.sign_out.assert_awaited_once_with()
        assert result == {"success": True}


# ── Prune lease callables ──────────────────────────────────────────────


class TestPruneLeaseCallableDelegation:
    @pytest.mark.asyncio
    async def test_release_prune_conflict_lease_delegates(self, endpoints, services):
        answer = {"success": True, "message": "Operation lease released."}
        services.prune_lease_service.release_prune_conflict_lease = AsyncMock(return_value=answer)
        result = await endpoints.release_prune_conflict_lease("sgdb_artwork:1")
        services.prune_lease_service.release_prune_conflict_lease.assert_awaited_once_with("sgdb_artwork:1")
        assert result == answer

    @pytest.mark.asyncio
    async def test_renew_prune_conflict_lease_delegates(self, endpoints, services):
        answer = {"success": True, "message": "Operation lease renewed."}
        services.prune_lease_service.renew_prune_conflict_lease = AsyncMock(return_value=answer)
        result = await endpoints.renew_prune_conflict_lease("sgdb_artwork:1")
        services.prune_lease_service.renew_prune_conflict_lease.assert_awaited_once_with("sgdb_artwork:1")
        assert result == answer

    @pytest.mark.asyncio
    async def test_release_orphaned_prune_leases_delegates(self, endpoints, services):
        services.prune_lease_service.release_orphaned_prune_leases = AsyncMock(
            return_value={"success": True, "released": 2}
        )
        result = await endpoints.release_orphaned_prune_leases()
        services.prune_lease_service.release_orphaned_prune_leases.assert_awaited_once_with()
        assert result == {"success": True, "released": 2}


# ── Migration callables ────────────────────────────────────────────────


class TestMigrationCallableDelegation:
    @pytest.mark.asyncio
    async def test_migrate_retrodeck_files_delegates(self, endpoints, services):
        services.migration_service.migrate_retrodeck_files = AsyncMock(return_value={"ok": True})
        result = await endpoints.migrate_retrodeck_files("overwrite")
        services.migration_service.migrate_retrodeck_files.assert_awaited_once_with("overwrite")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_migration_status_delegates(self, endpoints, services):
        services.migration_service.get_migration_status = AsyncMock(return_value={"pending": False})
        result = await endpoints.get_migration_status()
        services.migration_service.get_migration_status.assert_awaited_once_with()
        assert result == {"pending": False}

    @pytest.mark.asyncio
    async def test_dismiss_retrodeck_migration_delegates(self, endpoints, services):
        services.migration_service.dismiss_retrodeck_migration.return_value = {"ok": True}
        result = endpoints.dismiss_retrodeck_migration()
        services.migration_service.dismiss_retrodeck_migration.assert_called_once_with()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_retrodeck_status_delegates(self, endpoints, services):
        services.migration_service.get_retrodeck_status.return_value = {"status": "ok"}
        result = endpoints.get_retrodeck_status()
        services.migration_service.get_retrodeck_status.assert_called_once_with()
        assert result == {"status": "ok"}


# ── Core / firmware / BIOS callables ───────────────────────────────────


class TestCoreCallableDelegation:
    @pytest.mark.asyncio
    async def test_set_system_core_delegates(self, endpoints, services):
        services.core_service.set_system_core = AsyncMock(return_value={"success": True})
        result = await endpoints.set_system_core("snes", "core_a")
        services.core_service.set_system_core.assert_awaited_once_with("snes", "core_a")
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_set_game_core_delegates(self, endpoints, services):
        # The per-game pin is keyed by rom_id (the DB anchor), not by
        # platform_slug + rom_path: the override lives on the Rom aggregate.
        services.core_service.set_game_core = AsyncMock(
            return_value={"success": True, "launch_options": "flatpak run …", "app_id": 99}
        )
        result = await endpoints.set_game_core(42, "Snes9x")
        services.core_service.set_game_core.assert_awaited_once_with(42, "Snes9x")
        assert result == {"success": True, "launch_options": "flatpak run …", "app_id": 99}

    @pytest.mark.asyncio
    async def test_clear_game_core_delegates(self, endpoints, services):
        # Reset / Follow default drops the pin; delegates by rom_id and returns
        # the PLAIN launch options for the live shortcut.
        services.core_service.clear_game_core = AsyncMock(
            return_value={"success": True, "launch_options": "flatpak run …", "app_id": 99}
        )
        result = await endpoints.clear_game_core(42)
        services.core_service.clear_game_core.assert_awaited_once_with(42)
        assert result == {"success": True, "launch_options": "flatpak run …", "app_id": 99}

    @pytest.mark.asyncio
    async def test_get_platform_core_info_delegates(self, endpoints, services):
        # Core info is served via its OWN path (CoreService.get_platform_core_info),
        # keyed by rom_id so the per-game active core (override or system default)
        # surfaces, independent of the BIOS firmware status (#923).
        services.core_service.get_platform_core_info = AsyncMock(
            return_value={"emulators": [], "active_core": "snes9x_libretro", "active_core_label": "Snes9x"}
        )
        result = await endpoints.get_platform_core_info(42)
        services.core_service.get_platform_core_info.assert_awaited_once_with(42)
        assert result == {"emulators": [], "active_core": "snes9x_libretro", "active_core_label": "Snes9x"}


class TestFirmwareCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_firmware_status_delegates(self, endpoints, services):
        services.firmware_service.get_firmware_status = AsyncMock(return_value={"items": []})
        result = await endpoints.get_firmware_status()
        services.firmware_service.get_firmware_status.assert_awaited_once_with()
        assert result == {"items": []}

    @pytest.mark.asyncio
    async def test_download_all_firmware_delegates(self, endpoints, services):
        services.firmware_service.download_all_firmware = AsyncMock(return_value={"success": True})
        result = await endpoints.download_all_firmware("snes")
        services.firmware_service.download_all_firmware.assert_awaited_once_with("snes")
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_download_required_firmware_delegates(self, endpoints, services):
        services.firmware_service.download_required_firmware = AsyncMock(return_value={"success": True})
        result = await endpoints.download_required_firmware("snes")
        services.firmware_service.download_required_firmware.assert_awaited_once_with("snes")
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_check_platform_bios_delegates(self, endpoints, services):
        services.firmware_service.check_platform_bios = AsyncMock(return_value={"present": []})
        # The frontend sends only the slug; main.py threads no per-game
        # core, so the system default drives the BIOS filter (active_core_so=None).
        result = await endpoints.check_platform_bios("snes")
        services.firmware_service.check_platform_bios.assert_awaited_once_with("snes")
        assert result == {"present": []}

    @pytest.mark.asyncio
    async def test_get_bios_status_delegates(self, endpoints, services):
        services.game_detail_service.get_bios_status = AsyncMock(return_value={"present": True})
        result = await endpoints.get_bios_status(7)
        services.game_detail_service.get_bios_status.assert_awaited_once_with(7)
        assert result == {"present": True}

    @pytest.mark.asyncio
    async def test_delete_platform_bios_delegates(self, endpoints, services):
        services.firmware_service.delete_platform_bios = AsyncMock(return_value={"removed": 0})
        result = await endpoints.delete_platform_bios("snes")
        services.firmware_service.delete_platform_bios.assert_awaited_once_with("snes")
        assert result == {"removed": 0}


# ── Sync / library callables ───────────────────────────────────────────


class TestLibrarySyncCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_platforms_delegates(self, endpoints, services):
        services.sync_service.get_platforms = AsyncMock(return_value=[])
        result = await endpoints.get_platforms()
        services.sync_service.get_platforms.assert_awaited_once_with()
        assert result == []

    @pytest.mark.asyncio
    async def test_save_platform_sync_delegates(self, endpoints, services):
        services.sync_service.save_platform_sync = AsyncMock(return_value={"ok": True})
        result = await endpoints.save_platform_sync(1, True)
        services.sync_service.save_platform_sync.assert_awaited_once_with(1, True)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_set_all_platforms_sync_delegates(self, endpoints, services):
        services.sync_service.set_all_platforms_sync = AsyncMock(return_value={"ok": True})
        result = await endpoints.set_all_platforms_sync(True)
        services.sync_service.set_all_platforms_sync.assert_awaited_once_with(True)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_collections_delegates(self, endpoints, services):
        services.sync_service.get_collections = AsyncMock(return_value=[])
        result = await endpoints.get_collections()
        services.sync_service.get_collections.assert_awaited_once_with()
        assert result == []

    @pytest.mark.asyncio
    async def test_save_collection_sync_delegates(self, endpoints, services):
        services.sync_service.save_collection_sync = AsyncMock(return_value={"ok": True})
        result = await endpoints.save_collection_sync(2, "standard", False)
        services.sync_service.save_collection_sync.assert_awaited_once_with(2, "standard", False)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_save_collections_sync_delegates(self, endpoints, services):
        services.sync_service.save_collections_sync = AsyncMock(return_value={"success": True})
        result = await endpoints.save_collections_sync(["1", "2"], "standard", True)
        services.sync_service.save_collections_sync.assert_awaited_once_with(["1", "2"], "standard", True)
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_start_sync_delegates(self, endpoints, services):
        services.sync_service.start_sync = AsyncMock(return_value={"started": True})
        result = await endpoints.start_sync()
        services.sync_service.start_sync.assert_awaited_once_with()
        assert result == {"started": True}

    @pytest.mark.asyncio
    async def test_sync_heartbeat_delegates(self, endpoints, services):
        services.sync_service.sync_heartbeat.return_value = {"alive": True}
        result = endpoints.sync_heartbeat()
        services.sync_service.sync_heartbeat.assert_called_once_with()
        assert result == {"alive": True}

    @pytest.mark.asyncio
    async def test_sync_preview_delegates(self, endpoints, services):
        services.sync_service.sync_preview = AsyncMock(return_value={"preview_id": "abc"})
        result = await endpoints.sync_preview()
        services.sync_service.sync_preview.assert_awaited_once_with()
        assert result == {"preview_id": "abc"}

    @pytest.mark.asyncio
    async def test_sync_apply_delta_delegates(self, endpoints, services):
        services.sync_service.sync_apply_delta = AsyncMock(return_value={"applied": True})
        result = await endpoints.sync_apply_delta("abc")
        services.sync_service.sync_apply_delta.assert_awaited_once_with("abc")
        assert result == {"applied": True}

    @pytest.mark.asyncio
    async def test_get_sync_status_delegates(self, endpoints, services):
        services.sync_service.get_sync_status.return_value = {"running": True, "stage": "applying"}
        result = endpoints.get_sync_status()
        services.sync_service.get_sync_status.assert_called_once_with()
        assert result == {"running": True, "stage": "applying"}

    @pytest.mark.asyncio
    async def test_report_unit_results_delegates(self, endpoints, services):
        services.sync_service.report_unit_results = AsyncMock(return_value={"ok": True})
        result = await endpoints.report_unit_results({"1": 100}, "run-1", 1, 0)
        services.sync_service.report_unit_results.assert_awaited_once_with({"1": 100}, "run-1", 1, 0)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_registry_platforms_delegates(self, endpoints, services):
        services.sync_service.get_registry_platforms.return_value = [{"slug": "snes"}]
        result = endpoints.get_registry_platforms()
        services.sync_service.get_registry_platforms.assert_called_once_with()
        assert result == [{"slug": "snes"}]

    @pytest.mark.asyncio
    async def test_clear_sync_cache_delegates(self, endpoints, services):
        services.sync_service.clear_sync_cache = AsyncMock(return_value={"ok": True})
        result = await endpoints.clear_sync_cache()
        services.sync_service.clear_sync_cache.assert_awaited_once_with()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_sync_stats_delegates(self, endpoints, services):
        services.sync_service.get_sync_stats.return_value = {"roms": 5}
        result = endpoints.get_sync_stats()
        services.sync_service.get_sync_stats.assert_called_once_with()
        assert result == {"roms": 5}


class TestShortcutRemovalCallableDelegation:
    @pytest.mark.asyncio
    async def test_remove_platform_shortcuts_delegates(self, endpoints, services):
        services.shortcut_removal_service.remove_platform_shortcuts = AsyncMock(return_value={"removed": 3})
        result = await endpoints.remove_platform_shortcuts("snes")
        services.shortcut_removal_service.remove_platform_shortcuts.assert_awaited_once_with("snes")
        assert result == {"removed": 3}

    @pytest.mark.asyncio
    async def test_remove_all_shortcuts_delegates(self, endpoints, services):
        services.shortcut_removal_service.remove_all_shortcuts = AsyncMock(return_value={"removed": 10})
        result = await endpoints.remove_all_shortcuts()
        services.shortcut_removal_service.remove_all_shortcuts.assert_awaited_once_with()
        assert result == {"removed": 10}

    @pytest.mark.asyncio
    async def test_report_removal_results_delegates(self, endpoints, services):
        services.shortcut_removal_service.report_removal_results = AsyncMock(return_value={"ok": True})
        result = await endpoints.report_removal_results([1, 2], "shortcut_removal:1")
        services.shortcut_removal_service.report_removal_results.assert_awaited_once_with([1, 2], "shortcut_removal:1")
        assert result == {"ok": True}


class TestArtworkCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_artwork_base64_delegates(self, endpoints, services):
        services.artwork_service.get_artwork_base64 = AsyncMock(return_value={"base64": None})
        result = await endpoints.get_artwork_base64(42)
        services.artwork_service.get_artwork_base64.assert_awaited_once_with(42)
        assert result == {"base64": None}

    @pytest.mark.asyncio
    async def test_fetch_cover_base64_delegates(self, endpoints, services):
        services.artwork_service.fetch_cover_base64 = AsyncMock(return_value={"base64": "QUJD"})
        result = await endpoints.fetch_cover_base64(42)
        services.artwork_service.fetch_cover_base64.assert_awaited_once_with(42)
        assert result == {"base64": "QUJD"}

    @pytest.mark.asyncio
    async def test_refresh_cover_artwork_delegates(self, endpoints, services):
        services.artwork_service.refresh_cover = AsyncMock(
            return_value={"success": True, "message": "Cover refreshed", "cover_path": "/grid/999p.png"},
        )
        result = await endpoints.refresh_cover_artwork(42)
        services.artwork_service.refresh_cover.assert_awaited_once_with(42)
        assert result["success"] is True
        assert result["cover_path"] == "/grid/999p.png"

    @pytest.mark.asyncio
    async def test_refresh_cover_artwork_coerces_string_rom_id(self, endpoints, services):
        services.artwork_service.refresh_cover = AsyncMock(return_value={"success": True, "message": "ok"})
        # Endpoints receive args as JSON — defensive int() coercion guards
        # against the frontend accidentally sending a string.
        await endpoints.refresh_cover_artwork("42")
        services.artwork_service.refresh_cover.assert_awaited_once_with(42)


# ── Launch / session lifecycle callables ───────────────────────────────


class TestLifecycleCallableDelegation:
    @pytest.mark.asyncio
    async def test_evaluate_launch_returns_asdict(self, endpoints, services):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Verdict:
            allowed: bool
            reason: str

        services.launch_gate_service.evaluate = AsyncMock(return_value=Verdict(allowed=True, reason="ok"))
        result = await endpoints.evaluate_launch(12345)
        services.launch_gate_service.evaluate.assert_awaited_once_with(12345)
        assert result == {"allowed": True, "reason": "ok"}

    @pytest.mark.asyncio
    async def test_finalize_game_session_returns_asdict(self, endpoints, services):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Outcome:
            synced: bool

        services.session_lifecycle_service.finalize = AsyncMock(return_value=Outcome(synced=False))
        result = await endpoints.finalize_game_session(7)
        services.session_lifecycle_service.finalize.assert_awaited_once_with(7)
        assert result == {"synced": False}

    @pytest.mark.asyncio
    async def test_evaluate_launch_passes_a_refusal_through(self, endpoints, services):
        """A refused use case answers the refusal dict in place of a verdict; there is nothing to translate."""
        refusal = {"success": False, "reason": "prune_active", "message": "held"}
        services.launch_gate_service.evaluate = AsyncMock(return_value=refusal)

        assert await endpoints.evaluate_launch(12345) == refusal

    @pytest.mark.asyncio
    async def test_finalize_game_session_passes_a_refusal_through(self, endpoints, services):
        refusal = {"success": False, "reason": "prune_active", "message": "held"}
        services.session_lifecycle_service.finalize = AsyncMock(return_value=refusal)

        assert await endpoints.finalize_game_session(7) == refusal

    @pytest.mark.asyncio
    async def test_stop_running_game_delegates(self, endpoints, services):
        services.game_process_service.stop_running_game = AsyncMock(
            return_value={"success": True, "stopped": 2, "force_killed": 0}
        )
        result = await endpoints.stop_running_game(42)
        services.game_process_service.stop_running_game.assert_awaited_once_with(42)
        assert result == {"success": True, "stopped": 2, "force_killed": 0}


# ── Download callables ─────────────────────────────────────────────────


class TestDownloadCallableDelegation:
    @pytest.mark.asyncio
    async def test_start_download_delegates(self, endpoints, services):
        services.download_service.start_download = AsyncMock(return_value={"queued": True})
        result = await endpoints.start_download(42)
        # No candidate named, no collision answered and nothing reported by the
        # page: the plain Download press.
        services.download_service.start_download.assert_awaited_once_with(42, False, None, None, False)
        assert result == {"queued": True}

    @pytest.mark.asyncio
    async def test_cancel_download_delegates(self, endpoints, services):
        services.download_service.cancel_download.return_value = {"cancelled": True}
        result = endpoints.cancel_download(42)
        services.download_service.cancel_download.assert_called_once_with(42)
        assert result == {"cancelled": True}

    @pytest.mark.asyncio
    async def test_get_download_queue_delegates(self, endpoints, services):
        services.download_service.get_download_queue.return_value = []
        result = endpoints.get_download_queue()
        services.download_service.get_download_queue.assert_called_once_with()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_installed_rom_delegates(self, endpoints, services):
        services.download_service.get_installed_rom.return_value = {"installed": True}
        result = endpoints.get_installed_rom(42)
        services.download_service.get_installed_rom.assert_called_once_with(42)
        assert result == {"installed": True}


class TestRomRemovalCallableDelegation:
    @pytest.mark.asyncio
    async def test_remove_rom_delegates(self, endpoints, services):
        answer = {"success": True, "prune_lease_token": "rom_uninstall:1"}
        services.rom_removal_service.remove_rom = AsyncMock(return_value=answer)
        result = await endpoints.remove_rom(42)
        services.rom_removal_service.remove_rom.assert_awaited_once_with(42)
        assert result == answer

    @pytest.mark.asyncio
    async def test_uninstall_all_roms_delegates(self, endpoints, services):
        answer = {"success": True, "app_ids": [42], "prune_lease_token": "bulk_uninstall:1"}
        services.rom_removal_service.uninstall_all_roms = AsyncMock(return_value=answer)
        result = await endpoints.uninstall_all_roms()
        services.rom_removal_service.uninstall_all_roms.assert_awaited_once_with()
        assert result == answer


# ── Saves callables ───────────────────────────────────────────────────


class TestSavesCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_save_status_delegates(self, endpoints, services):
        services.save_sync_service.get_save_status = AsyncMock(return_value={"status": "ok"})
        result = await endpoints.get_save_status(42)
        services.save_sync_service.get_save_status.assert_awaited_once_with(42)
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_check_core_change_delegates(self, endpoints, services):
        services.save_sync_service.check_core_change.return_value = {"changed": False}
        result = endpoints.check_core_change(42)
        services.save_sync_service.check_core_change.assert_called_once_with(42)
        assert result == {"changed": False}

    @pytest.mark.asyncio
    async def test_get_save_slots_delegates(self, endpoints, services):
        services.save_sync_service.get_save_slots = AsyncMock(return_value=["default"])
        result = await endpoints.get_save_slots(42)
        services.save_sync_service.get_save_slots.assert_awaited_once_with(42)
        assert result == ["default"]

    @pytest.mark.asyncio
    async def test_get_slot_saves_delegates(self, endpoints, services):
        services.save_sync_service.get_slot_saves = AsyncMock(return_value=[])
        result = await endpoints.get_slot_saves(42, "default")
        services.save_sync_service.get_slot_saves.assert_awaited_once_with(42, "default")
        assert result == []

    @pytest.mark.asyncio
    async def test_switch_slot_delegates(self, endpoints, services):
        services.save_sync_service.switch_slot = AsyncMock(return_value={"ok": True})
        result = await endpoints.switch_slot(42, "slot_2")
        services.save_sync_service.switch_slot.assert_awaited_once_with(42, "slot_2")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_get_slot_delete_info_delegates(self, endpoints, services):
        services.save_sync_service.get_slot_delete_info = AsyncMock(return_value={"local": 1})
        result = await endpoints.get_slot_delete_info(42, "default")
        services.save_sync_service.get_slot_delete_info.assert_awaited_once_with(42, "default")
        assert result == {"local": 1}

    @pytest.mark.asyncio
    async def test_delete_slot_delegates(self, endpoints, services):
        services.save_sync_service.delete_slot = AsyncMock(return_value={"deleted": True})
        result = await endpoints.delete_slot(42, "default")
        services.save_sync_service.delete_slot.assert_awaited_once_with(42, "default")
        assert result == {"deleted": True}

    @pytest.mark.asyncio
    async def test_is_save_tracking_configured_delegates(self, endpoints, services):
        services.save_sync_service.is_save_tracking_configured.return_value = True
        result = endpoints.is_save_tracking_configured(42)
        services.save_sync_service.is_save_tracking_configured.assert_called_once_with(42)
        assert result is True

    @pytest.mark.asyncio
    async def test_get_save_setup_info_delegates(self, endpoints, services):
        services.save_sync_service.get_save_setup_info = AsyncMock(return_value={"info": "x"})
        result = await endpoints.get_save_setup_info(42)
        services.save_sync_service.get_save_setup_info.assert_awaited_once_with(42)
        assert result == {"info": "x"}

    @pytest.mark.asyncio
    async def test_confirm_slot_choice_delegates(self, endpoints, services):
        services.save_sync_service.confirm_slot_choice = AsyncMock(return_value={"ok": True})
        result = await endpoints.confirm_slot_choice(42, "default", True, "slot_1", True)
        services.save_sync_service.confirm_slot_choice.assert_awaited_once_with(42, "default", True, "slot_1", True)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_resolve_sync_conflict_delegates(self, endpoints, services):
        services.save_sync_service.resolve_sync_conflict = AsyncMock(return_value={"ok": True})
        result = await endpoints.resolve_sync_conflict(42, "save.srm", 99, "keep_local")
        services.save_sync_service.resolve_sync_conflict.assert_awaited_once_with(
            42,
            "save.srm",
            99,
            "keep_local",
        )
        assert result == {"ok": True}


# ── SteamGridDB callables ──────────────────────────────────────────────


class TestSgdbCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_sgdb_artwork_base64_delegates(self, endpoints, services):
        answer = {"base64": "data", "prune_lease_token": "sgdb_artwork:1"}
        services.sgdb_service.get_sgdb_artwork_base64 = AsyncMock(return_value=answer)
        result = await endpoints.get_sgdb_artwork_base64(42, 1)
        services.sgdb_service.get_sgdb_artwork_base64.assert_awaited_once_with(42, 1)
        assert result == answer

    @pytest.mark.asyncio
    async def test_verify_sgdb_api_key_delegates(self, endpoints, services):
        services.sgdb_service.verify_sgdb_api_key = AsyncMock(return_value={"valid": True})
        result = await endpoints.verify_sgdb_api_key("abc")
        services.sgdb_service.verify_sgdb_api_key.assert_awaited_once_with("abc")
        assert result == {"valid": True}

    @pytest.mark.asyncio
    async def test_save_sgdb_api_key_delegates(self, endpoints, services):
        services.sgdb_service.save_sgdb_api_key.return_value = {"ok": True}
        result = endpoints.save_sgdb_api_key("abc")
        services.sgdb_service.save_sgdb_api_key.assert_called_once_with("abc")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_save_shortcut_icon_delegates(self, endpoints, services):
        services.sgdb_service.save_shortcut_icon = AsyncMock(return_value={"ok": True})
        result = await endpoints.save_shortcut_icon(123, "data")
        services.sgdb_service.save_shortcut_icon.assert_awaited_once_with(123, "data")
        assert result == {"ok": True}


# ── Metadata / achievements / game-detail callables ────────────────────


class TestMetadataCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_rom_metadata_delegates(self, endpoints, services):
        services.metadata_service.get_rom_metadata.return_value = {"name": "x"}
        result = endpoints.get_rom_metadata(42)
        services.metadata_service.get_rom_metadata.assert_called_once_with(42)
        assert result == {"name": "x"}

    @pytest.mark.asyncio
    async def test_get_metadata_cache_page_delegates(self, endpoints, services):
        services.metadata_service.get_metadata_cache_page.return_value = {"items": {}, "total": 0}
        result = endpoints.get_metadata_cache_page(0, 500)
        services.metadata_service.get_metadata_cache_page.assert_called_once_with(0, 500)
        assert result == {"items": {}, "total": 0}

    @pytest.mark.asyncio
    async def test_get_app_id_rom_id_map_delegates(self, endpoints, services):
        services.metadata_service.get_app_id_rom_id_map.return_value = {"100": 42}
        result = endpoints.get_app_id_rom_id_map()
        services.metadata_service.get_app_id_rom_id_map.assert_called_once_with()
        assert result == {"100": 42}


class TestAchievementsCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_achievements_delegates(self, endpoints, services):
        services.achievements_service.get_achievements = AsyncMock(return_value=[])
        result = await endpoints.get_achievements(42)
        services.achievements_service.get_achievements.assert_awaited_once_with(42)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_achievement_progress_delegates(self, endpoints, services):
        services.achievements_service.get_achievement_progress = AsyncMock(return_value={"completed": 0})
        result = await endpoints.get_achievement_progress(42)
        services.achievements_service.get_achievement_progress.assert_awaited_once_with(42)
        assert result == {"completed": 0}


class TestGameDetailCallableDelegation:
    @pytest.mark.asyncio
    async def test_get_cached_game_detail_delegates(self, endpoints, services):
        services.game_detail_service.get_cached_game_detail = AsyncMock(return_value={"detail": "x"})
        result = await endpoints.get_cached_game_detail("12345")
        services.game_detail_service.get_cached_game_detail.assert_awaited_once_with("12345")
        assert result == {"detail": "x"}


# ── Error-propagation tests ────────────────────────────────────────────
#
# Each endpoint below wraps a service call without any try/except — an
# exception from the service must propagate cleanly. We pick one
# representative endpoint per service family for the assertion; the
# delegation tests above already cover the happy paths.


class TestCallableErrorPropagation:
    @pytest.mark.asyncio
    async def test_save_server_url_propagates(self, endpoints, services):
        services.settings_service.save_server_url.side_effect = ValueError("bad")
        with pytest.raises(ValueError, match="bad"):
            await endpoints.save_server_url("x")

    @pytest.mark.asyncio
    async def test_test_connection_propagates(self, endpoints, services):
        services.connection_service.test_connection = AsyncMock(side_effect=RuntimeError("down"))
        with pytest.raises(RuntimeError, match="down"):
            await endpoints.test_connection()

    @pytest.mark.asyncio
    async def test_migrate_retrodeck_files_propagates(self, endpoints, services):
        services.migration_service.migrate_retrodeck_files = AsyncMock(side_effect=OSError("io"))
        with pytest.raises(OSError, match="io"):
            await endpoints.migrate_retrodeck_files()

    @pytest.mark.asyncio
    async def test_get_firmware_status_propagates(self, endpoints, services):
        services.firmware_service.get_firmware_status = AsyncMock(side_effect=RuntimeError("api"))
        with pytest.raises(RuntimeError, match="api"):
            await endpoints.get_firmware_status()

    @pytest.mark.asyncio
    async def test_sync_preview_propagates(self, endpoints, services):
        services.sync_service.sync_preview = AsyncMock(side_effect=RuntimeError("preview"))
        with pytest.raises(RuntimeError, match="preview"):
            await endpoints.sync_preview()

    @pytest.mark.asyncio
    async def test_start_download_propagates(self, endpoints, services):
        services.download_service.start_download = AsyncMock(side_effect=RuntimeError("dl"))
        with pytest.raises(RuntimeError, match="dl"):
            await endpoints.start_download(42)

    @pytest.mark.asyncio
    async def test_remove_rom_propagates(self, endpoints, services):
        services.rom_removal_service.remove_rom = AsyncMock(side_effect=RuntimeError("rm"))
        with pytest.raises(RuntimeError, match="rm"):
            await endpoints.remove_rom(42)

    @pytest.mark.asyncio
    async def test_get_save_status_propagates(self, endpoints, services):
        services.save_sync_service.get_save_status = AsyncMock(side_effect=RuntimeError("save"))
        with pytest.raises(RuntimeError, match="save"):
            await endpoints.get_save_status(42)

    @pytest.mark.asyncio
    async def test_get_sgdb_artwork_base64_propagates(self, endpoints, services):
        services.sgdb_service.get_sgdb_artwork_base64 = AsyncMock(side_effect=RuntimeError("sgdb"))
        with pytest.raises(RuntimeError, match="sgdb"):
            await endpoints.get_sgdb_artwork_base64(42, 1)

    @pytest.mark.asyncio
    async def test_get_achievements_propagates(self, endpoints, services):
        services.achievements_service.get_achievements = AsyncMock(side_effect=RuntimeError("ach"))
        with pytest.raises(RuntimeError, match="ach"):
            await endpoints.get_achievements(42)

    @pytest.mark.asyncio
    async def test_get_artwork_base64_propagates(self, endpoints, services):
        services.artwork_service.get_artwork_base64 = AsyncMock(side_effect=RuntimeError("art"))
        with pytest.raises(RuntimeError, match="art"):
            await endpoints.get_artwork_base64(42)

    @pytest.mark.asyncio
    async def test_fetch_cover_base64_propagates(self, endpoints, services):
        services.artwork_service.fetch_cover_base64 = AsyncMock(side_effect=RuntimeError("art"))
        with pytest.raises(RuntimeError, match="art"):
            await endpoints.fetch_cover_base64(42)

    @pytest.mark.asyncio
    async def test_evaluate_launch_propagates(self, endpoints, services):
        services.launch_gate_service.evaluate = AsyncMock(side_effect=RuntimeError("gate"))
        with pytest.raises(RuntimeError, match="gate"):
            await endpoints.evaluate_launch(42)
