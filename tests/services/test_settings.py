"""Tests for SettingsService."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.rom import Rom
from services.settings import SettingsService, SettingsServiceConfig


@pytest.fixture
def settings() -> dict[str, Any]:
    return {}


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


def _seed_rom(uow: FakeUnitOfWork, rom_id: int, app_id: int | None) -> None:
    """Seed one ``Rom`` row bound to *app_id* (``None`` = unbound) into *uow*."""
    rom = Rom(
        rom_id=rom_id,
        platform_slug="snes",
        name=f"Game {rom_id}",
        fs_name=f"game_{rom_id}.sfc",
        shortcut_app_id=app_id,
        last_synced_at="2026-01-01T00:00:00",
    )
    with uow:
        uow.roms.save(rom)


@pytest.fixture
def settings_persister() -> MagicMock:
    return MagicMock()


@pytest.fixture
def steam_config() -> MagicMock:
    cfg = MagicMock()
    cfg.check_retroarch_input_driver = MagicMock(return_value=None)
    cfg.fix_retroarch_input_driver = MagicMock(
        return_value={"success": True, "message": "Changed input_driver to sdl2"},
    )
    cfg.set_steam_input_config = MagicMock()
    return cfg


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_settings")


@pytest.fixture
def service(settings, uow, logger, settings_persister, steam_config) -> SettingsService:
    return SettingsService(
        config=SettingsServiceConfig(
            settings=settings,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            logger=logger,
            settings_persister=settings_persister,
            steam_config=steam_config,
        ),
    )


# ── save_server_url ────────────────────────────────────────────────────


class TestSaveServerUrl:
    def test_persists_url(self, service, settings, settings_persister):
        result = service.save_server_url("http://romm.local")
        assert result == {"success": True, "message": "Settings saved"}
        assert settings["romm_url"] == "http://romm.local"
        settings_persister.save_settings.assert_called_once_with()

    def test_does_not_touch_token_or_credentials(self, service, settings):
        settings["romm_api_token"] = "rmm_keep"
        settings["romm_api_token_id"] = 7
        service.save_server_url("http://romm.local")
        assert settings["romm_api_token"] == "rmm_keep"
        assert settings["romm_api_token_id"] == 7

    def test_allow_insecure_ssl_none_does_not_touch_setting(self, service, settings):
        settings["romm_allow_insecure_ssl"] = True
        service.save_server_url("http://romm.local", None)
        assert settings["romm_allow_insecure_ssl"] is True

    def test_allow_insecure_ssl_true(self, service, settings):
        service.save_server_url("http://romm.local", True)
        assert settings["romm_allow_insecure_ssl"] is True

    def test_allow_insecure_ssl_false_overrides_true(self, service, settings):
        settings["romm_allow_insecure_ssl"] = True
        service.save_server_url("http://romm.local", False)
        assert settings["romm_allow_insecure_ssl"] is False

    def test_persistence_failure_returns_error(self, service, settings_persister):
        settings_persister.save_settings.side_effect = OSError("disk full")
        result = service.save_server_url("http://romm.local")
        assert result["success"] is False
        assert "disk full" in result["message"]

    def test_trims_url_before_persisting(self, service, settings, settings_persister):
        result = service.save_server_url("  https://romm.local  ")
        assert result["success"] is True
        assert settings["romm_url"] == "https://romm.local"
        settings_persister.save_settings.assert_called_once_with()

    @pytest.mark.parametrize("bad_url", ["", "   ", "romm.local", "ftp://romm.local", "https://"])
    def test_invalid_url_rejected_without_writing(self, service, settings, settings_persister, bad_url):
        result = service.save_server_url(bad_url)
        assert result == {
            "success": False,
            "reason": "config_error",
            "message": "Enter a valid http(s):// server URL",
        }
        assert "romm_url" not in settings
        settings_persister.save_settings.assert_not_called()


# ── get_settings ───────────────────────────────────────────────────────


class TestSaveCustomHeaders:
    """#1822: the whole configured list is replaced, validated, and never echoed back."""

    def _set(self, name: str, value: str) -> dict[str, str]:
        return {"name": name, "value_action": "set", "value": value}

    def _keep(self, name: str) -> dict[str, str]:
        return {"name": name, "value_action": "keep"}

    def test_persists_the_list_in_order(self, service, settings, settings_persister):
        result = service.save_custom_headers([self._set("P-Access-Token", "tok"), self._set("P-Access-Token-Id", "id")])
        assert result == {"success": True}
        assert settings["romm_custom_headers"] == [
            {"name": "P-Access-Token", "value": "tok"},
            {"name": "P-Access-Token-Id", "value": "id"},
        ]
        settings_persister.save_settings.assert_called_once_with()

    def test_keep_preserves_the_stored_value(self, service, settings):
        settings["romm_custom_headers"] = [{"name": "X-Token", "value": "stored"}]
        result = service.save_custom_headers([self._keep("X-Token")])
        assert result == {"success": True}
        assert settings["romm_custom_headers"] == [{"name": "X-Token", "value": "stored"}]

    def test_keep_for_an_unknown_name_fails_without_writing(self, service, settings, settings_persister):
        settings["romm_custom_headers"] = [{"name": "X-Token", "value": "stored"}]
        result = service.save_custom_headers([self._keep("X-Other")])
        assert result["success"] is False
        assert result["reason"] == "no_stored_header_value"
        assert "X-Other" in result["message"]
        assert settings["romm_custom_headers"] == [{"name": "X-Token", "value": "stored"}]
        settings_persister.save_settings.assert_not_called()

    def test_a_whole_list_replace_drops_a_removed_row(self, service, settings):
        settings["romm_custom_headers"] = [
            {"name": "X-Keep", "value": "a"},
            {"name": "X-Drop", "value": "b"},
        ]
        service.save_custom_headers([self._keep("X-Keep")])
        assert settings["romm_custom_headers"] == [{"name": "X-Keep", "value": "a"}]

    def test_an_empty_list_clears_every_header(self, service, settings):
        settings["romm_custom_headers"] = [{"name": "X-Token", "value": "a"}]
        assert service.save_custom_headers([]) == {"success": True}
        assert settings["romm_custom_headers"] == []

    def test_authorization_is_refused_with_its_own_message(self, service, settings):
        result = service.save_custom_headers([self._set("Authorization", "Basic abc")])
        assert result["success"] is False
        assert result["reason"] == "authorization_reserved"
        assert "RomM API token" in result["message"]
        assert "romm_custom_headers" not in settings

    @pytest.mark.parametrize(
        ("entries", "reason"),
        [
            ("not-a-list", "headers_not_a_list"),
            ([{"name": "X-Token"}], "unknown_value_action"),
            (["X-Token: v"], "malformed_header_entry"),
            ([{"name": 7, "value_action": "set", "value": "v"}], "malformed_header_entry"),
            ([{"name": "X-Token", "value_action": "wipe", "value": "v"}], "unknown_value_action"),
            ([{"name": "X Token", "value_action": "set", "value": "v"}], "invalid_header_name"),
            ([{"name": "User-Agent", "value_action": "set", "value": "v"}], "reserved_header_name"),
            ([{"name": "X-Token", "value_action": "set", "value": "a\r\nX-Injected: y"}], "unsafe_header_value"),
            ([{"name": "X-Token", "value_action": "set", "value": ""}], "empty_header_value"),
        ],
    )
    def test_garbage_from_the_wire_is_rejected_without_writing(
        self, service, settings, settings_persister, entries, reason
    ):
        result = service.save_custom_headers(entries)
        assert result["success"] is False
        assert result["reason"] == reason
        assert result["message"]
        assert "romm_custom_headers" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_a_refusal_never_carries_the_value(self, service):
        result = service.save_custom_headers([self._set("X-Token", "s3cret\r\nX-Injected: y")])
        assert result["success"] is False
        assert "s3cret" not in result["message"]


class TestGetSettings:
    def test_happy_path(self, service, settings, steam_config):
        settings.update(
            {
                "romm_url": "http://romm.local",
                "romm_api_token": "rmm_tok",
                "steam_input_mode": "force_on",
                "steamgriddb_api_key": "abc",
                "log_level": "info",
                "romm_allow_insecure_ssl": True,
                "collection_create_platform_groups": True,
                "collection_owner_scope": "own",
                "collection_naming_mode": "by_label",
                "preferred_region": "USA",
                "skip_preview": True,
            }
        )
        steam_config.check_retroarch_input_driver.return_value = {"warning": False}
        result = service.get_settings()
        assert result["romm_url"] == "http://romm.local"
        assert result["has_token"] is True
        assert result["sgdb_api_key_masked"] == "••••"
        assert result["steam_input_mode"] == "force_on"
        assert result["log_level"] == "info"
        assert result["romm_allow_insecure_ssl"] is True
        assert result["collection_create_platform_groups"] is True
        assert result["collection_owner_scope"] == "own"
        assert result["collection_naming_mode"] == "by_label"
        assert result["preferred_region"] == "USA"
        assert result["skip_preview"] is True
        assert result["retroarch_input_check"] == {"warning": False}

    def test_never_returns_credentials_or_token(self, service, settings):
        settings["romm_api_token"] = "rmm_secret"
        settings["romm_user"] = "alice"
        settings["romm_pass"] = "pw"
        result = service.get_settings()
        assert "romm_user" not in result
        assert "romm_pass_masked" not in result
        assert "has_credentials" not in result
        assert "rmm_secret" not in str(result)

    def test_has_token_true_when_set(self, service, settings):
        settings["romm_api_token"] = "rmm_x"
        result = service.get_settings()
        assert result["has_token"] is True

    def test_has_token_false_when_none(self, service, settings):
        settings["romm_api_token"] = None
        result = service.get_settings()
        assert result["has_token"] is False

    def test_has_token_false_when_empty(self, service, settings):
        settings["romm_api_token"] = ""
        result = service.get_settings()
        assert result["has_token"] is False

    def test_masks_sgdb_key_when_set(self, service, settings):
        settings["steamgriddb_api_key"] = "longkey"
        result = service.get_settings()
        assert result["sgdb_api_key_masked"] == "••••"
        assert "longkey" not in str(result)

    def test_empty_sgdb_key_returns_empty_mask(self, service, settings):
        settings["steamgriddb_api_key"] = ""
        result = service.get_settings()
        assert result["sgdb_api_key_masked"] == ""

    def test_defaults_when_keys_missing(self, service):
        result = service.get_settings()
        assert result["romm_url"] == ""
        assert result["has_token"] is False
        assert result["sgdb_api_key_masked"] == ""
        assert result["steam_input_mode"] == "default"
        assert result["log_level"] == "warn"
        assert result["romm_allow_insecure_ssl"] is False
        assert result["collection_create_platform_groups"] is False
        assert result["collection_owner_scope"] == "all"
        assert result["collection_naming_mode"] == "merge"
        assert result["preferred_region"] == "auto"
        assert result["skip_preview"] is False
        assert result["romm_custom_header_names"] == []

    def test_reports_custom_header_names_and_never_a_value(self, service, settings):
        """Same rule as the token and the SteamGridDB key: a stored value can be replaced, not read back."""
        settings["romm_custom_headers"] = [
            {"name": "P-Access-Token", "value": "s3cret"},
            {"name": "P-Access-Token-Id", "value": "id-42"},
        ]
        result = service.get_settings()
        assert result["romm_custom_header_names"] == ["P-Access-Token", "P-Access-Token-Id"]
        assert "s3cret" not in str(result)
        assert "id-42" not in str(result)

    def test_includes_retroarch_input_check_payload(self, service, steam_config):
        steam_config.check_retroarch_input_driver.return_value = {
            "warning": True,
            "current": "x",
            "config_path": "/cfg",
        }
        result = service.get_settings()
        assert result["retroarch_input_check"]["warning"] is True
        assert result["retroarch_input_check"]["current"] == "x"


# ── save_log_level ─────────────────────────────────────────────────────


class TestSaveLogLevel:
    @pytest.mark.parametrize("level", ["debug", "info", "warn", "error"])
    def test_valid_levels(self, service, settings, settings_persister, level):
        result = service.save_log_level(level)
        assert result == {"success": True}
        assert settings["log_level"] == level
        settings_persister.save_settings.assert_called_once_with()

    def test_invalid_level(self, service, settings, settings_persister):
        result = service.save_log_level("verbose")
        assert result["success"] is False
        assert "Invalid log level" in result["message"]
        assert "log_level" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_empty_string_rejected(self, service, settings):
        result = service.save_log_level("")
        assert result["success"] is False
        assert "log_level" not in settings


# ── save_preferred_region ──────────────────────────────────────────────


class TestSavePreferredRegion:
    @pytest.mark.parametrize("region", ["auto", "Europe", "USA", "Japan", "Germany"])
    def test_valid_regions_persist(self, service, settings, settings_persister, region):
        result = service.save_preferred_region(region)
        assert result == {"success": True}
        assert settings["preferred_region"] == region
        settings_persister.save_settings.assert_called_once_with()

    def test_trims_and_persists(self, service, settings):
        service.save_preferred_region("  Japan  ")
        assert settings["preferred_region"] == "Japan"

    def test_blank_normalises_to_auto(self, service, settings, settings_persister):
        result = service.save_preferred_region("   ")
        assert result == {"success": True}
        assert settings["preferred_region"] == "auto"
        settings_persister.save_settings.assert_called_once_with()

    def test_non_string_rejected_without_writing(self, service, settings, settings_persister):
        result = service.save_preferred_region(42)
        assert result["success"] is False
        assert result["reason"] == "invalid_region"
        assert "preferred_region" not in settings
        settings_persister.save_settings.assert_not_called()


# ── save_skip_preview ──────────────────────────────────────────────────


class TestSaveSkipPreview:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_bool_persists_and_is_reported_back(self, service, settings, settings_persister, steam_config, enabled):
        steam_config.check_retroarch_input_driver.return_value = None
        result = service.save_skip_preview(enabled)
        assert result == {"success": True}
        assert settings["skip_preview"] is enabled
        settings_persister.save_settings.assert_called_once_with()
        assert service.get_settings()["skip_preview"] is enabled

    @pytest.mark.parametrize("value", ["true", 1, None, [], {"on": True}])
    def test_non_bool_rejected_without_writing(self, service, settings, settings_persister, value):
        result = service.save_skip_preview(value)
        assert result == {"success": False, "reason": "invalid_value", "message": "Invalid value"}
        assert "skip_preview" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_absent_value_reads_as_off(self, service, settings, steam_config):
        steam_config.check_retroarch_input_driver.return_value = None
        assert "skip_preview" not in settings
        assert service.get_settings()["skip_preview"] is False


# ── get_known_regions ──────────────────────────────────────────────────


class TestGetKnownRegions:
    @staticmethod
    def _seed(uow: FakeUnitOfWork, rom_id: int, regions: tuple[str, ...]) -> None:
        with uow:
            uow.roms.save(
                Rom(
                    rom_id=rom_id,
                    platform_slug="snes",
                    name=f"Game {rom_id}",
                    fs_name=f"game_{rom_id}.sfc",
                    shortcut_app_id=None,
                    last_synced_at="2026-01-01T00:00:00",
                    regions=regions,
                )
            )

    def test_empty_library_returns_empty(self, service):
        assert service.get_known_regions() == []

    def test_distinct_sorted_regions_across_roms(self, service, uow):
        self._seed(uow, 1, ("USA", "Europe"))
        self._seed(uow, 2, ("Japan", "USA"))  # USA repeats across rows
        self._seed(uow, 3, ())  # a region-less ROM contributes nothing
        assert service.get_known_regions() == ["Europe", "Japan", "USA"]


# ── frontend_log ───────────────────────────────────────────────────────


class TestFrontendLog:
    def test_warn_configured_drops_info(self, service, settings):
        settings["log_level"] = "warn"
        service._logger = MagicMock()
        service.frontend_log("info", "msg")
        service._logger.info.assert_not_called()
        service._logger.warning.assert_not_called()
        service._logger.error.assert_not_called()

    def test_warn_configured_drops_debug(self, service, settings):
        settings["log_level"] = "warn"
        service._logger = MagicMock()
        service.frontend_log("debug", "msg")
        service._logger.info.assert_not_called()

    def test_warn_configured_emits_warn(self, service, settings):
        settings["log_level"] = "warn"
        service._logger = MagicMock()
        service.frontend_log("warn", "watch out")
        service._logger.warning.assert_called_once_with("[FE] watch out")

    def test_warn_configured_emits_error(self, service, settings):
        settings["log_level"] = "warn"
        service._logger = MagicMock()
        service.frontend_log("error", "boom")
        service._logger.error.assert_called_once_with("[FE] boom")

    def test_debug_configured_emits_debug_and_info(self, service, settings):
        settings["log_level"] = "debug"
        service._logger = MagicMock()
        service.frontend_log("debug", "d")
        service.frontend_log("info", "i")
        # Both go through logger.info
        assert service._logger.info.call_args_list == [
            (("[FE] d",),),
            (("[FE] i",),),
        ]

    def test_unknown_level_treated_as_debug(self, service, settings):
        # Unknown level maps to threshold 0 (debug); with warn (2) configured it is dropped.
        settings["log_level"] = "warn"
        service._logger = MagicMock()
        service.frontend_log("trace", "noise")
        service._logger.info.assert_not_called()

    def test_missing_log_level_defaults_warn(self, service, settings):
        settings.pop("log_level", None)
        service._logger = MagicMock()
        service.frontend_log("info", "msg")
        service._logger.info.assert_not_called()
        service.frontend_log("warn", "msg2")
        service._logger.warning.assert_called_once_with("[FE] msg2")

    def test_returns_none(self, service):
        # Decky callable contract — frontend_log returns nothing meaningful.
        assert service.frontend_log("info", "msg") is None


# ── save_steam_input_setting ──────────────────────────────────────────


class TestSaveSteamInputSetting:
    @pytest.mark.parametrize("mode", ["default", "force_on", "force_off"])
    def test_valid_modes(self, service, settings, settings_persister, mode):
        result = service.save_steam_input_setting(mode)
        assert result == {"success": True}
        assert settings["steam_input_mode"] == mode
        settings_persister.save_settings.assert_called_once_with()

    def test_invalid_mode(self, service, settings, settings_persister):
        result = service.save_steam_input_setting("turbo")
        assert result["success"] is False
        assert "turbo" in result["message"]
        assert "steam_input_mode" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_empty_string_rejected(self, service, settings):
        result = service.save_steam_input_setting("")
        assert result["success"] is False
        assert "steam_input_mode" not in settings


# ── apply_steam_input_setting ─────────────────────────────────────────


class TestApplySteamInputSetting:
    def test_applies_to_bound_shortcuts(self, service, settings, uow, steam_config):
        settings["steam_input_mode"] = "force_on"
        _seed_rom(uow, rom_id=1, app_id=111)
        _seed_rom(uow, rom_id=2, app_id=222)
        result = service.apply_steam_input_setting()
        assert result["success"] is True
        assert "force_on" in result["message"]
        assert "2 shortcuts" in result["message"]
        steam_config.set_steam_input_config.assert_called_once_with([111, 222], mode="force_on")

    def test_skips_unbound_roms(self, service, settings, uow, steam_config):
        """ROMs with NULL shortcut_app_id (unbound) are excluded from the apply set."""
        settings["steam_input_mode"] = "force_off"
        _seed_rom(uow, rom_id=1, app_id=111)
        _seed_rom(uow, rom_id=2, app_id=None)  # unbound — must be skipped
        result = service.apply_steam_input_setting()
        assert result["success"] is True
        assert "1 shortcuts" in result["message"]
        steam_config.set_steam_input_config.assert_called_once_with([111], mode="force_off")

    def test_empty_registry_returns_noop(self, service, steam_config):
        result = service.apply_steam_input_setting()
        assert result == {"success": True, "message": "No shortcuts to update"}
        steam_config.set_steam_input_config.assert_not_called()

    def test_all_roms_unbound(self, service, uow, steam_config):
        _seed_rom(uow, rom_id=1, app_id=None)
        _seed_rom(uow, rom_id=2, app_id=None)
        result = service.apply_steam_input_setting()
        assert result["success"] is True
        assert "No shortcuts" in result["message"]
        steam_config.set_steam_input_config.assert_not_called()

    def test_default_mode_when_unset(self, service, uow, steam_config):
        _seed_rom(uow, rom_id=1, app_id=1)
        result = service.apply_steam_input_setting()
        assert result["success"] is True
        steam_config.set_steam_input_config.assert_called_once_with([1], mode="default")

    def test_adapter_failure_returns_error(self, service, uow, steam_config):
        _seed_rom(uow, rom_id=1, app_id=1)
        steam_config.set_steam_input_config.side_effect = OSError("boom")
        result = service.apply_steam_input_setting()
        assert result["success"] is False
        assert result["message"] == "Operation failed"


# ── fix_retroarch_input_driver ────────────────────────────────────────


class TestFixRetroarchInputDriver:
    def test_delegates_to_adapter(self, service, steam_config):
        steam_config.fix_retroarch_input_driver.return_value = {"success": True, "message": "ok"}
        result = service.fix_retroarch_input_driver()
        assert result == {"success": True, "message": "ok"}
        steam_config.fix_retroarch_input_driver.assert_called_once_with()


# ── whitelist ──────────────────────────────────────────────────────────


class TestGetWhitelistSettings:
    def test_defaults_to_empty_lists(self, service):
        result = service.get_whitelist_settings()
        assert result == {"disabled_defaults": [], "custom_names": []}

    def test_returns_stored_values(self, service, settings):
        settings["whitelist_disabled_defaults"] = ["chrome"]
        settings["whitelist_custom_names"] = ["My App"]
        result = service.get_whitelist_settings()
        assert result == {"disabled_defaults": ["chrome"], "custom_names": ["My App"]}


class TestUpdateWhitelistSettings:
    def test_happy_path(self, service, settings, settings_persister):
        result = service.update_whitelist_settings(["chrome"], ["My App"])
        assert result == {"success": True}
        assert settings["whitelist_disabled_defaults"] == ["chrome"]
        assert settings["whitelist_custom_names"] == ["My App"]
        settings_persister.save_settings.assert_called_once_with()

    def test_disabled_defaults_not_list_rejected(self, service, settings_persister):
        result = service.update_whitelist_settings("not-a-list", [])
        assert result["success"] is False
        assert "disabled_defaults" in result["message"]
        settings_persister.save_settings.assert_not_called()

    def test_custom_names_not_list_rejected(self, service, settings_persister):
        result = service.update_whitelist_settings([], "not-a-list")
        assert result["success"] is False
        assert "custom_names" in result["message"]
        settings_persister.save_settings.assert_not_called()

    def test_disabled_defaults_with_non_string_rejected(self, service, settings_persister):
        result = service.update_whitelist_settings([1, 2], [])
        assert result["success"] is False
        assert "disabled_defaults" in result["message"]
        settings_persister.save_settings.assert_not_called()

    def test_custom_names_with_non_string_rejected(self, service, settings_persister):
        result = service.update_whitelist_settings([], ["ok", 42])
        assert result["success"] is False
        assert "custom_names" in result["message"]
        settings_persister.save_settings.assert_not_called()

    def test_empty_lists_accepted(self, service, settings):
        result = service.update_whitelist_settings([], [])
        assert result == {"success": True}
        assert settings["whitelist_disabled_defaults"] == []
        assert settings["whitelist_custom_names"] == []


# ── save_collection_platform_groups ───────────────────────────────────


class TestSaveCollectionPlatformGroups:
    def test_enables(self, service, settings, settings_persister):
        result = service.save_collection_platform_groups(True)
        assert result == {"success": True}
        assert settings["collection_create_platform_groups"] is True
        settings_persister.save_settings.assert_called_once_with()

    def test_disables(self, service, settings, settings_persister):
        settings["collection_create_platform_groups"] = True
        result = service.save_collection_platform_groups(False)
        assert result == {"success": True}
        assert settings["collection_create_platform_groups"] is False
        settings_persister.save_settings.assert_called_once_with()

    def test_coerces_truthy(self, service, settings):
        service.save_collection_platform_groups(1)  # type: ignore[arg-type]
        assert settings["collection_create_platform_groups"] is True

    def test_coerces_falsy(self, service, settings):
        service.save_collection_platform_groups(0)  # type: ignore[arg-type]
        assert settings["collection_create_platform_groups"] is False


class TestSetCollectionOwnerScope:
    @pytest.mark.parametrize("scope", ["own", "all"])
    def test_valid_scopes_persist(self, service, settings, settings_persister, scope):
        result = service.set_collection_owner_scope(scope)
        assert result == {"success": True}
        assert settings["collection_owner_scope"] == scope
        settings_persister.save_settings.assert_called_once_with()

    def test_invalid_scope_rejected_with_canonical_failure(self, service, settings, settings_persister):
        result = service.set_collection_owner_scope("everyone")
        assert result["success"] is False
        assert result["reason"] == "invalid_scope"
        assert "Invalid owner scope" in result["message"]
        assert "collection_owner_scope" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_non_string_rejected(self, service, settings, settings_persister):
        result = service.set_collection_owner_scope(None)  # type: ignore[arg-type]
        assert result["success"] is False
        assert "collection_owner_scope" not in settings
        settings_persister.save_settings.assert_not_called()


class TestSetCollectionNamingMode:
    @pytest.mark.parametrize("mode", ["merge", "by_label"])
    def test_valid_modes_persist(self, service, settings, settings_persister, mode):
        result = service.set_collection_naming_mode(mode)
        assert result == {"success": True}
        assert settings["collection_naming_mode"] == mode
        settings_persister.save_settings.assert_called_once_with()

    def test_invalid_mode_rejected_with_canonical_failure(self, service, settings, settings_persister):
        result = service.set_collection_naming_mode("fancy")
        assert result["success"] is False
        assert result["reason"] == "invalid_mode"
        assert "Invalid naming mode" in result["message"]
        assert "collection_naming_mode" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_non_string_rejected(self, service, settings, settings_persister):
        result = service.set_collection_naming_mode(None)  # type: ignore[arg-type]
        assert result["success"] is False
        assert result["reason"] == "invalid_mode"
        assert "collection_naming_mode" not in settings
        settings_persister.save_settings.assert_not_called()


class TestDismissSettingsResetNotice:
    def test_pops_marker_and_persists(self, service, settings, settings_persister):
        settings["_settings_reset_notice"] = {"backed_up_to": "settings.json.corrupt-42"}
        result = service.dismiss_settings_reset_notice()
        assert result == {"success": True}
        assert "_settings_reset_notice" not in settings
        settings_persister.save_settings.assert_called_once_with()

    def test_idempotent_no_marker_still_persists(self, service, settings, settings_persister):
        assert "_settings_reset_notice" not in settings
        result = service.dismiss_settings_reset_notice()
        assert result == {"success": True}
        assert "_settings_reset_notice" not in settings
        settings_persister.save_settings.assert_called_once_with()
