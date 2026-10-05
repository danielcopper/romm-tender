"""Tests for SettingsService."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from _factories import _make_conflict_rules, _make_prune_conflicts, _refused_by_conflict_rule
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from adapters.steam_config import SteamConfigAdapter
from domain.rom import Rom
from host.logging_setup import LOG_FILENAME, configure_logging
from lib.errors import NotConfigured, Refused
from lib.input_driver_fix import InputDriverFix
from lib.steam_input_apply import SteamInputApply
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
    cfg.fix_retroarch_input_driver = MagicMock(return_value=InputDriverFix.FIXED)
    cfg.set_steam_input_config = MagicMock(return_value=SteamInputApply.APPLIED)
    return cfg


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_settings")


@pytest.fixture
def prune_conflicts():
    return _make_prune_conflicts()


@pytest.fixture
def service(settings, uow, logger, settings_persister, steam_config, prune_conflicts) -> SettingsService:
    return SettingsService(
        config=SettingsServiceConfig(
            settings=settings,
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            logger=logger,
            settings_persister=settings_persister,
            steam_config=steam_config,
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        ),
    )


# ── The conflict rules ─────────────────────────────────────────────────


_SETTINGS_WRITES = [
    ("save_server_url", ("http://romm.local",)),
    ("save_custom_headers", ([],)),
    ("apply_steam_input_setting", ()),
]


class TestConflictRulesAtTheUseCase:
    """Each settings write checks its endpoint's prune rule and holds an operation named after it."""

    @pytest.mark.parametrize(("use_case", "args"), _SETTINGS_WRITES)
    async def test_a_running_cleanup_refuses_the_write_and_changes_nothing(
        self, service, settings, settings_persister, steam_config, uow, prune_conflicts, use_case, args
    ):
        _seed_rom(uow, 1, app_id=1001)
        prune_conflicts.register_run("held-run")
        before = dict(settings)

        with _refused_by_conflict_rule("prune_active"):
            await getattr(service, use_case)(*args)

        assert settings == before
        settings_persister.save_settings.assert_not_called()
        steam_config.set_steam_input_config.assert_not_called()
        assert prune_conflicts.conflicting_operations == 0

    @pytest.mark.parametrize(("use_case", "args"), _SETTINGS_WRITES)
    async def test_the_write_runs_under_an_operation_named_after_its_endpoint(
        self, service, settings_persister, steam_config, uow, prune_conflicts, use_case, args
    ):
        _seed_rom(uow, 1, app_id=1001)
        seen: list[list[str]] = []

        def record(*_args, **_kwargs) -> SteamInputApply:
            seen.append(sorted(holder.label for holder in prune_conflicts._operations.values()))
            return SteamInputApply.APPLIED

        settings_persister.save_settings.side_effect = record
        steam_config.set_steam_input_config.side_effect = record

        await getattr(service, use_case)(*args)

        assert seen == [[use_case]]
        assert prune_conflicts.conflicting_operations == 0


# ── save_server_url ────────────────────────────────────────────────────


class TestSaveServerUrl:
    async def test_persists_url(self, service, settings, settings_persister):
        result = await service.save_server_url("http://romm.local")
        assert result == {"success": True, "message": "Settings saved"}
        assert settings["romm_url"] == "http://romm.local"
        settings_persister.save_settings.assert_called_once_with()

    async def test_does_not_touch_token_or_credentials(self, service, settings):
        settings["romm_api_token"] = "rmm_keep"
        settings["romm_api_token_id"] = 7
        await service.save_server_url("http://romm.local")
        assert settings["romm_api_token"] == "rmm_keep"
        assert settings["romm_api_token_id"] == 7

    async def test_allow_insecure_ssl_none_does_not_touch_setting(self, service, settings):
        settings["romm_allow_insecure_ssl"] = True
        await service.save_server_url("http://romm.local", None)
        assert settings["romm_allow_insecure_ssl"] is True

    async def test_allow_insecure_ssl_true(self, service, settings):
        await service.save_server_url("http://romm.local", True)
        assert settings["romm_allow_insecure_ssl"] is True

    async def test_allow_insecure_ssl_false_overrides_true(self, service, settings):
        settings["romm_allow_insecure_ssl"] = True
        await service.save_server_url("http://romm.local", False)
        assert settings["romm_allow_insecure_ssl"] is False

    async def test_a_failed_settings_write_is_refused_as_save_failed(self, service, settings_persister):
        cause = OSError("disk full")
        settings_persister.save_settings.side_effect = cause
        with pytest.raises(Refused) as refused:
            await service.save_server_url("http://romm.local")
        assert (refused.value.reason, refused.value.message, refused.value.details) == (
            "save_failed",
            "Save failed: disk full",
            {},
        )
        assert refused.value.__cause__ is cause

    @pytest.mark.parametrize(
        "previous",
        [
            pytest.param({"romm_url": "http://old.local", "romm_allow_insecure_ssl": False}, id="both-set"),
            pytest.param({"romm_url": "http://old.local"}, id="no-ssl-flag"),
            pytest.param({}, id="neither-set"),
        ],
    )
    async def test_a_failed_settings_write_gives_the_previous_url_back(
        self, service, settings, settings_persister, previous
    ):
        settings.update(previous)
        settings_persister.save_settings.side_effect = OSError("disk full")

        with pytest.raises(Refused, match=r"^Save failed: disk full$"):
            await service.save_server_url("https://new.local", True)

        assert settings == previous

    async def test_a_write_that_fails_with_a_bug_is_not_refused(self, service, settings_persister):
        settings_persister.save_settings.side_effect = TypeError("Object of type set is not JSON serializable")
        with pytest.raises(TypeError):
            await service.save_server_url("http://romm.local")

    async def test_trims_url_before_persisting(self, service, settings, settings_persister):
        result = await service.save_server_url("  https://romm.local  ")
        assert result["success"] is True
        assert settings["romm_url"] == "https://romm.local"
        settings_persister.save_settings.assert_called_once_with()

    @pytest.mark.parametrize("bad_url", ["", "   ", "romm.local", "ftp://romm.local", "https://"])
    async def test_invalid_url_rejected_without_writing(self, service, settings, settings_persister, bad_url):
        with pytest.raises(NotConfigured) as refused:
            await service.save_server_url(bad_url)
        assert (refused.value.message, refused.value.details) == ("Enter a valid http(s):// server URL", {})
        assert "romm_url" not in settings
        settings_persister.save_settings.assert_not_called()


# ── get_settings ───────────────────────────────────────────────────────


class TestSaveCustomHeaders:
    """#1822: the whole configured list is replaced, validated, and never echoed back."""

    def _set(self, name: str, value: str) -> dict[str, str]:
        return {"name": name, "value_action": "set", "value": value}

    def _keep(self, name: str) -> dict[str, str]:
        return {"name": name, "value_action": "keep"}

    async def test_persists_the_list_in_order(self, service, settings, settings_persister):
        result = await service.save_custom_headers(
            [self._set("P-Access-Token", "tok"), self._set("P-Access-Token-Id", "id")]
        )
        assert result == {"success": True}
        assert settings["romm_custom_headers"] == [
            {"name": "P-Access-Token", "value": "tok"},
            {"name": "P-Access-Token-Id", "value": "id"},
        ]
        settings_persister.save_settings.assert_called_once_with()

    async def test_keep_preserves_the_stored_value(self, service, settings):
        settings["romm_custom_headers"] = [{"name": "X-Token", "value": "stored"}]
        result = await service.save_custom_headers([self._keep("X-Token")])
        assert result == {"success": True}
        assert settings["romm_custom_headers"] == [{"name": "X-Token", "value": "stored"}]

    async def test_keep_for_an_unknown_name_fails_without_writing(self, service, settings, settings_persister):
        settings["romm_custom_headers"] = [{"name": "X-Token", "value": "stored"}]
        with pytest.raises(Refused) as refused:
            await service.save_custom_headers([self._keep("X-Other")])
        assert refused.value.reason == "no_stored_header_value"
        assert "X-Other" in refused.value.message
        assert settings["romm_custom_headers"] == [{"name": "X-Token", "value": "stored"}]
        settings_persister.save_settings.assert_not_called()

    async def test_a_whole_list_replace_drops_a_removed_row(self, service, settings):
        settings["romm_custom_headers"] = [
            {"name": "X-Keep", "value": "a"},
            {"name": "X-Drop", "value": "b"},
        ]
        await service.save_custom_headers([self._keep("X-Keep")])
        assert settings["romm_custom_headers"] == [{"name": "X-Keep", "value": "a"}]

    async def test_an_empty_list_clears_every_header(self, service, settings):
        settings["romm_custom_headers"] = [{"name": "X-Token", "value": "a"}]
        assert await service.save_custom_headers([]) == {"success": True}
        assert settings["romm_custom_headers"] == []

    async def test_authorization_is_refused_with_its_own_message(self, service, settings):
        with pytest.raises(Refused) as refused:
            await service.save_custom_headers([self._set("Authorization", "Basic abc")])
        assert refused.value.reason == "authorization_reserved"
        assert "RomM API token" in refused.value.message
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
    async def test_garbage_from_the_wire_is_rejected_without_writing(
        self, service, settings, settings_persister, entries, reason
    ):
        with pytest.raises(Refused) as refused:
            await service.save_custom_headers(entries)
        assert refused.value.reason == reason
        assert refused.value.message
        assert "romm_custom_headers" not in settings
        settings_persister.save_settings.assert_not_called()

    async def test_a_refusal_never_carries_the_value(self, service):
        with pytest.raises(Refused) as refused:
            await service.save_custom_headers([self._set("X-Token", "s3cret\r\nX-Injected: y")])
        assert "s3cret" not in refused.value.message


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


# ── get_settings and save_server_url together ─────────────────────────


class TestSettings:
    @pytest.mark.asyncio
    async def test_get_settings_reports_token_present(self, service, settings):
        settings["romm_api_token"] = "rmm_abc"
        result = service.get_settings()
        assert result["has_token"] is True
        # The token itself is never sent to the frontend.
        assert "rmm_abc" not in str(result)

    @pytest.mark.asyncio
    async def test_save_server_url_persists_url(self, service, settings):
        result = await service.save_server_url("http://example.com")
        assert result["success"] is True
        assert settings["romm_url"] == "http://example.com"

    @pytest.mark.asyncio
    async def test_save_server_url_does_not_touch_token(self, service, settings):
        settings["romm_api_token"] = "rmm_keep"
        await service.save_server_url("http://example.com")
        assert settings["romm_api_token"] == "rmm_keep"


class TestInsecureSslSetting:
    @pytest.mark.asyncio
    async def test_get_settings_includes_field(self, service, settings):
        settings["romm_allow_insecure_ssl"] = True
        result = service.get_settings()
        assert result["romm_allow_insecure_ssl"] is True

    @pytest.mark.asyncio
    async def test_get_settings_defaults_false(self, service, settings):
        settings.pop("romm_allow_insecure_ssl", None)
        result = service.get_settings()
        assert result["romm_allow_insecure_ssl"] is False

    @pytest.mark.asyncio
    async def test_save_server_url_without_param_preserves(self, service, settings):
        settings["romm_allow_insecure_ssl"] = True
        await service.save_server_url("https://romm.local")
        assert settings["romm_allow_insecure_ssl"] is True


# ── save_log_level ─────────────────────────────────────────────────────


class TestSaveLogLevel:
    @pytest.mark.parametrize("level", ["debug", "info", "warn", "error"])
    def test_valid_levels(self, service, settings, settings_persister, level):
        result = service.save_log_level(level)
        assert result == {"success": True}
        assert settings["log_level"] == level
        settings_persister.save_settings.assert_called_once_with()

    def test_invalid_level(self, service, settings, settings_persister):
        with pytest.raises(Refused) as refused:
            service.save_log_level("verbose")
        assert refused.value.reason == "invalid_log_level"
        assert "Invalid log level" in refused.value.message
        assert "log_level" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_empty_string_rejected(self, service, settings):
        with pytest.raises(Refused) as refused:
            service.save_log_level("")
        assert refused.value.reason == "invalid_log_level"
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
        with pytest.raises(Refused) as refused:
            service.save_preferred_region(42)
        assert refused.value.reason == "invalid_region"
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
        with pytest.raises(Refused) as refused:
            service.save_skip_preview(value)
        assert (refused.value.reason, refused.value.message, refused.value.details) == (
            "invalid_value",
            "Invalid value",
            {},
        )
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


def _recorded(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """The (level name, message) of every record captured so far."""
    return [(record.levelname, record.message) for record in caplog.records]


class TestFrontendLog:
    """The setting decides whether a line is written; the caller decides its level."""

    @pytest.mark.parametrize(
        ("level", "levelname"),
        [("debug", "DEBUG"), ("info", "INFO"), ("warn", "WARNING"), ("error", "ERROR")],
    )
    def test_the_record_carries_the_level_the_caller_named(self, service, settings, caplog, level, levelname):
        settings["log_level"] = "debug"
        service.frontend_log(level, "msg")
        assert _recorded(caplog) == [(levelname, "[FE] msg")]

    def test_warn_configured_drops_info(self, service, settings, caplog):
        settings["log_level"] = "warn"
        service.frontend_log("info", "msg")
        assert _recorded(caplog) == []

    def test_warn_configured_drops_debug(self, service, settings, caplog):
        settings["log_level"] = "warn"
        service.frontend_log("debug", "msg")
        assert _recorded(caplog) == []

    def test_warn_configured_emits_warn(self, service, settings, caplog):
        settings["log_level"] = "warn"
        service.frontend_log("warn", "watch out")
        assert _recorded(caplog) == [("WARNING", "[FE] watch out")]

    def test_warn_configured_emits_error(self, service, settings, caplog):
        settings["log_level"] = "warn"
        service.frontend_log("error", "boom")
        assert _recorded(caplog) == [("ERROR", "[FE] boom")]

    def test_debug_configured_keeps_the_two_apart(self, service, settings, caplog):
        settings["log_level"] = "debug"
        service.frontend_log("debug", "d")
        service.frontend_log("info", "i")
        assert _recorded(caplog) == [("DEBUG", "[FE] d"), ("INFO", "[FE] i")]

    def test_unknown_level_treated_as_debug(self, service, settings, caplog):
        # Unknown level maps to threshold 0 (debug); with warn (2) configured it is dropped.
        settings["log_level"] = "warn"
        service.frontend_log("trace", "noise")
        assert _recorded(caplog) == []

    def test_unknown_level_is_recorded_as_debug(self, service, settings, caplog):
        settings["log_level"] = "debug"
        service.frontend_log("trace", "noise")
        assert _recorded(caplog) == [("DEBUG", "[FE] noise")]

    def test_missing_log_level_defaults_warn(self, service, settings, caplog):
        settings.pop("log_level", None)
        service.frontend_log("info", "msg")
        assert _recorded(caplog) == []
        service.frontend_log("warn", "msg2")
        assert _recorded(caplog) == [("WARNING", "[FE] msg2")]

    def test_returns_none(self, service):
        # Endpoint contract — frontend_log returns nothing meaningful.
        assert service.frontend_log("info", "msg") is None


class TestAFrontendDebugLineIsWrittenRatherThanDropped:
    """The join the mapping alone cannot pin: the process's own root level.

    ``configure_logging`` levels the root at INFO and the ``log_level`` setting
    never moves it, so a frontend debug record emitted through the injected
    logger would be dropped instead of written — the mapping above would then
    be green over a line nobody can read.
    """

    def test_it_reaches_the_log_file_the_entry_point_configures(
        self,
        settings,
        uow,
        settings_persister,
        steam_config,
        tmp_path,
        restore_root_logger,
    ):
        root = configure_logging(str(tmp_path / "state"), "tok")
        service = SettingsService(
            config=SettingsServiceConfig(
                settings=settings,
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                logger=root,
                settings_persister=settings_persister,
                steam_config=steam_config,
                conflict_rules=_make_conflict_rules(),
            ),
        )
        settings["log_level"] = "debug"

        service.frontend_log("debug", "adoption poll tick")

        written = (tmp_path / "state" / LOG_FILENAME).read_text()
        assert "[DEBUG]: [FE] adoption poll tick" in written

    def test_the_root_the_entry_point_configures_still_drops_its_own_debug_records(
        self,
        tmp_path,
        restore_root_logger,
    ):
        """The reprieve is the frontend logger's alone — nothing else gained one."""
        root = configure_logging(str(tmp_path / "state"), "tok")

        root.debug("a backend trace")

        assert "a backend trace" not in (tmp_path / "state" / LOG_FILENAME).read_text()


# ── log_level: saved, reported and applied ────────────────────────────


class TestLogLevel:
    @pytest.mark.asyncio
    async def test_save_log_level_valid(self, service, settings):
        for level in ("debug", "info", "warn", "error"):
            result = service.save_log_level(level)
            assert result["success"] is True
            assert settings["log_level"] == level

    @pytest.mark.asyncio
    async def test_save_log_level_invalid(self, service, settings):
        settings["log_level"] = "warn"
        with pytest.raises(Refused) as refused:
            service.save_log_level("verbose")
        assert refused.value.reason == "invalid_log_level"
        assert settings["log_level"] == "warn"  # unchanged

    @pytest.mark.asyncio
    async def test_get_settings_includes_log_level(self, service, settings):
        settings["log_level"] = "info"
        result = service.get_settings()
        assert result["log_level"] == "info"

    @pytest.mark.asyncio
    async def test_get_settings_defaults_log_level_warn(self, service, settings):
        settings.pop("log_level", None)
        result = service.get_settings()
        assert result["log_level"] == "warn"

    @pytest.mark.asyncio
    async def test_frontend_log_respects_level(self, service, settings, caplog):
        """frontend_log only logs when message level >= configured level."""
        settings["log_level"] = "warn"
        service.frontend_log("debug", "debug msg")
        service.frontend_log("info", "info msg")
        service.frontend_log("warn", "warn msg")
        service.frontend_log("error", "error msg")

        assert [(r.levelname, r.message) for r in caplog.records] == [
            ("WARNING", "[FE] warn msg"),
            ("ERROR", "[FE] error msg"),
        ]

    @pytest.mark.asyncio
    async def test_frontend_log_debug_level_logs_all(self, service, settings, caplog):
        """With log_level=debug, all levels are logged — each at its own."""
        settings["log_level"] = "debug"
        service.frontend_log("debug", "d")
        service.frontend_log("info", "i")
        service.frontend_log("warn", "w")
        service.frontend_log("error", "e")

        assert [(r.levelname, r.message) for r in caplog.records] == [
            ("DEBUG", "[FE] d"),
            ("INFO", "[FE] i"),
            ("WARNING", "[FE] w"),
            ("ERROR", "[FE] e"),
        ]


# ── save_steam_input_setting ──────────────────────────────────────────


class TestSaveSteamInputSetting:
    @pytest.mark.parametrize("mode", ["default", "force_on", "force_off"])
    def test_valid_modes(self, service, settings, settings_persister, mode):
        result = service.save_steam_input_setting(mode)
        assert result == {"success": True}
        assert settings["steam_input_mode"] == mode
        settings_persister.save_settings.assert_called_once_with()

    def test_invalid_mode(self, service, settings, settings_persister):
        with pytest.raises(Refused) as refused:
            service.save_steam_input_setting("turbo")
        assert refused.value.reason == "invalid_mode"
        assert "turbo" in refused.value.message
        assert "steam_input_mode" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_empty_string_rejected(self, service, settings):
        with pytest.raises(Refused) as refused:
            service.save_steam_input_setting("")
        assert refused.value.reason == "invalid_mode"
        assert "steam_input_mode" not in settings


# ── apply_steam_input_setting ─────────────────────────────────────────


class TestApplySteamInputSetting:
    async def test_applies_to_bound_shortcuts(self, service, settings, uow, steam_config):
        settings["steam_input_mode"] = "force_on"
        _seed_rom(uow, rom_id=1, app_id=111)
        _seed_rom(uow, rom_id=2, app_id=222)
        result = await service.apply_steam_input_setting()
        assert result["success"] is True
        assert "force_on" in result["message"]
        assert "2 shortcuts" in result["message"]
        steam_config.set_steam_input_config.assert_called_once_with([111, 222], mode="force_on")

    async def test_skips_unbound_roms(self, service, settings, uow, steam_config):
        """ROMs with NULL shortcut_app_id (unbound) are excluded from the apply set."""
        settings["steam_input_mode"] = "force_off"
        _seed_rom(uow, rom_id=1, app_id=111)
        _seed_rom(uow, rom_id=2, app_id=None)  # unbound — must be skipped
        result = await service.apply_steam_input_setting()
        assert result["success"] is True
        assert "1 shortcuts" in result["message"]
        steam_config.set_steam_input_config.assert_called_once_with([111], mode="force_off")

    async def test_empty_registry_returns_noop(self, service, steam_config):
        result = await service.apply_steam_input_setting()
        assert result == {"success": True, "message": "No shortcuts to update"}
        steam_config.set_steam_input_config.assert_not_called()

    async def test_all_roms_unbound(self, service, uow, steam_config):
        _seed_rom(uow, rom_id=1, app_id=None)
        _seed_rom(uow, rom_id=2, app_id=None)
        result = await service.apply_steam_input_setting()
        assert result["success"] is True
        assert "No shortcuts" in result["message"]
        steam_config.set_steam_input_config.assert_not_called()

    async def test_default_mode_when_unset(self, service, uow, steam_config):
        _seed_rom(uow, rom_id=1, app_id=1)
        result = await service.apply_steam_input_setting()
        assert result["success"] is True
        steam_config.set_steam_input_config.assert_called_once_with([1], mode="default")

    @pytest.mark.parametrize(
        ("outcome", "reason", "message"),
        [
            (
                SteamInputApply.NO_STEAM_USER,
                "steam_user_not_found",
                "Not applied — no Steam user was found on this device",
            ),
            (
                SteamInputApply.NO_LOCALCONFIG,
                "steam_config_not_found",
                "Not applied — Steam's localconfig.vdf was not found",
            ),
            (
                SteamInputApply.UNREADABLE,
                "steam_config_unreadable",
                "Not applied — Steam's localconfig.vdf could not be read",
            ),
            (
                SteamInputApply.WRITE_FAILED,
                "steam_config_write_failed",
                "Not applied — Steam's localconfig.vdf could not be written",
            ),
        ],
    )
    async def test_a_mode_that_was_not_applied_is_refused_saying_why(
        self, service, uow, steam_config, outcome, reason, message
    ):
        _seed_rom(uow, rom_id=1, app_id=1)
        steam_config.set_steam_input_config.return_value = outcome

        with pytest.raises(Refused) as refused:
            await service.apply_steam_input_setting()

        assert (refused.value.reason, refused.value.message, refused.value.details) == (reason, message, {})

    async def test_only_an_applied_mode_answers_success(self, service, uow, steam_config):
        _seed_rom(uow, rom_id=1, app_id=1)
        answers = {}
        for outcome in SteamInputApply:
            steam_config.set_steam_input_config.return_value = outcome
            try:
                answers[outcome] = (await service.apply_steam_input_setting())["success"]
            except Refused:
                answers[outcome] = False

        assert answers == {outcome: outcome is SteamInputApply.APPLIED for outcome in SteamInputApply}

    async def test_a_localconfig_that_cannot_be_written_is_refused(
        self, settings, uow, logger, settings_persister, prune_conflicts, home
    ):
        config_dir = home / ".local" / "share" / "Steam" / "userdata" / "123" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "localconfig.vdf").write_text('"UserLocalConfigStore"\n{\n}\n', encoding="utf-8")
        settings["steam_input_mode"] = "force_on"
        _seed_rom(uow, rom_id=1, app_id=111)
        service = SettingsService(
            config=SettingsServiceConfig(
                settings=settings,
                uow_factory=FakeUnitOfWorkFactory(uow=uow),
                logger=logger,
                settings_persister=settings_persister,
                steam_config=SteamConfigAdapter(user_home=str(home), logger=logger),
                conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
            ),
        )

        with (
            patch("adapters.steam_config.os.replace", side_effect=PermissionError("read-only")),
            pytest.raises(Refused) as refused,
        ):
            await service.apply_steam_input_setting()

        assert refused.value.reason == "steam_config_write_failed"
        assert (config_dir / "localconfig.vdf").read_text(encoding="utf-8") == '"UserLocalConfigStore"\n{\n}\n'

    async def test_an_adapter_error_is_left_to_the_transport(self, service, uow, steam_config):
        _seed_rom(uow, rom_id=1, app_id=1)
        steam_config.set_steam_input_config.side_effect = OSError("boom")

        with pytest.raises(OSError, match="boom"):
            await service.apply_steam_input_setting()


# ── fix_retroarch_input_driver ────────────────────────────────────────


class TestFixRetroarchInputDriver:
    def test_a_fixed_config_answers_success(self, service, steam_config):
        steam_config.fix_retroarch_input_driver.return_value = InputDriverFix.FIXED
        result = service.fix_retroarch_input_driver()
        assert result == {"success": True, "message": "Changed input_driver to sdl2"}
        steam_config.fix_retroarch_input_driver.assert_called_once_with()

    def test_nothing_to_fix_is_refused(self, service, steam_config):
        steam_config.fix_retroarch_input_driver.return_value = InputDriverFix.NOTHING_TO_FIX
        with pytest.raises(Refused) as refused:
            service.fix_retroarch_input_driver()
        assert (refused.value.reason, refused.value.message) == ("nothing_to_fix", "No fix needed")

    def test_a_failed_write_is_refused_as_unknown(self, service, steam_config):
        steam_config.fix_retroarch_input_driver.return_value = InputDriverFix.WRITE_FAILED
        with pytest.raises(Refused) as refused:
            service.fix_retroarch_input_driver()
        assert (refused.value.reason, refused.value.message) == ("unknown", "Operation failed")


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
        with pytest.raises(Refused) as refused:
            service.update_whitelist_settings("not-a-list", [])
        assert refused.value.reason == "invalid_whitelist"
        assert "disabled_defaults" in refused.value.message
        settings_persister.save_settings.assert_not_called()

    def test_custom_names_not_list_rejected(self, service, settings_persister):
        with pytest.raises(Refused) as refused:
            service.update_whitelist_settings([], "not-a-list")
        assert refused.value.reason == "invalid_whitelist"
        assert "custom_names" in refused.value.message
        settings_persister.save_settings.assert_not_called()

    def test_disabled_defaults_with_non_string_rejected(self, service, settings_persister):
        with pytest.raises(Refused) as refused:
            service.update_whitelist_settings([1, 2], [])
        assert refused.value.reason == "invalid_whitelist"
        assert "disabled_defaults" in refused.value.message
        settings_persister.save_settings.assert_not_called()

    def test_custom_names_with_non_string_rejected(self, service, settings_persister):
        with pytest.raises(Refused) as refused:
            service.update_whitelist_settings([], ["ok", 42])
        assert refused.value.reason == "invalid_whitelist"
        assert "custom_names" in refused.value.message
        settings_persister.save_settings.assert_not_called()

    def test_empty_lists_accepted(self, service, settings):
        result = service.update_whitelist_settings([], [])
        assert result == {"success": True}
        assert settings["whitelist_disabled_defaults"] == []
        assert settings["whitelist_custom_names"] == []


class TestWhitelistSettings:
    @pytest.mark.asyncio
    async def test_get_whitelist_defaults_empty(self, service, settings):
        """Returns empty lists when no whitelist keys exist in settings."""
        settings.pop("whitelist_disabled_defaults", None)
        settings.pop("whitelist_custom_names", None)
        result = service.get_whitelist_settings()
        assert result == {"disabled_defaults": [], "custom_names": []}

    @pytest.mark.asyncio
    async def test_update_and_get_whitelist(self, service):
        """Round-trip: update then get returns the stored values."""

        service.update_whitelist_settings(["chrome"], ["My App"])
        result = service.get_whitelist_settings()
        assert result["disabled_defaults"] == ["chrome"]
        assert result["custom_names"] == ["My App"]

    @pytest.mark.asyncio
    async def test_update_whitelist_validates_disabled_defaults(self, service):
        """Rejects non-list disabled_defaults."""
        with pytest.raises(Refused) as refused:
            service.update_whitelist_settings("not-a-list", [])
        assert refused.value.reason == "invalid_whitelist"
        assert "disabled_defaults" in refused.value.message

    @pytest.mark.asyncio
    async def test_update_whitelist_validates_custom_names(self, service):
        """Rejects non-list custom_names."""
        with pytest.raises(Refused) as refused:
            service.update_whitelist_settings([], "not-a-list")
        assert refused.value.reason == "invalid_whitelist"
        assert "custom_names" in refused.value.message

    @pytest.mark.asyncio
    async def test_update_whitelist_validates_inner_types(self, service):
        """Rejects lists containing non-string items."""
        with pytest.raises(Refused) as refused_dd:
            service.update_whitelist_settings([1, 2], [])
        assert refused_dd.value.reason == "invalid_whitelist"
        assert "disabled_defaults" in refused_dd.value.message

        with pytest.raises(Refused) as refused_cn:
            service.update_whitelist_settings([], ["valid", 42])
        assert refused_cn.value.reason == "invalid_whitelist"
        assert "custom_names" in refused_cn.value.message

    @pytest.mark.asyncio
    async def test_update_whitelist_persists(self, service, settings):
        """Verifies values are stored in the settings dict after update."""

        result = service.update_whitelist_settings(["moonlight"], ["Custom Game"])
        assert result["success"] is True
        assert settings["whitelist_disabled_defaults"] == ["moonlight"]
        assert settings["whitelist_custom_names"] == ["Custom Game"]


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

    def test_an_invalid_scope_is_refused_without_writing(self, service, settings, settings_persister):
        with pytest.raises(Refused) as refused:
            service.set_collection_owner_scope("everyone")
        assert refused.value.reason == "invalid_scope"
        assert "Invalid owner scope" in refused.value.message
        assert "collection_owner_scope" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_non_string_rejected(self, service, settings, settings_persister):
        with pytest.raises(Refused) as refused:
            service.set_collection_owner_scope(None)  # type: ignore[arg-type]
        assert refused.value.reason == "invalid_scope"
        assert "collection_owner_scope" not in settings
        settings_persister.save_settings.assert_not_called()


class TestSetCollectionNamingMode:
    @pytest.mark.parametrize("mode", ["merge", "by_label"])
    def test_valid_modes_persist(self, service, settings, settings_persister, mode):
        result = service.set_collection_naming_mode(mode)
        assert result == {"success": True}
        assert settings["collection_naming_mode"] == mode
        settings_persister.save_settings.assert_called_once_with()

    def test_an_invalid_mode_is_refused_without_writing(self, service, settings, settings_persister):
        with pytest.raises(Refused) as refused:
            service.set_collection_naming_mode("fancy")
        assert refused.value.reason == "invalid_mode"
        assert "Invalid naming mode" in refused.value.message
        assert "collection_naming_mode" not in settings
        settings_persister.save_settings.assert_not_called()

    def test_non_string_rejected(self, service, settings, settings_persister):
        with pytest.raises(Refused) as refused:
            service.set_collection_naming_mode(None)  # type: ignore[arg-type]
        assert refused.value.reason == "invalid_mode"
        assert "collection_naming_mode" not in settings
        settings_persister.save_settings.assert_not_called()


# ── settings reset notice ─────────────────────────────────────────────


class TestGetSettingsResetNotice:
    """The reset notice is read off the live settings dict and never consumed by reading."""

    def test_clean_boot_is_not_pending(self, service, settings):
        settings["romm_url"] = "http://romm.local"
        assert service.get_settings_reset_notice() == {"pending": False, "backed_up_to": None}

    def test_a_marker_is_pending_with_its_backup(self, service, settings):
        settings["_settings_reset_notice"] = {"backed_up_to": "settings.json.corrupt-1781697600"}
        assert service.get_settings_reset_notice() == {
            "pending": True,
            "backed_up_to": "settings.json.corrupt-1781697600",
        }

    def test_repeated_reads_stay_pending_and_write_nothing(self, service, settings, settings_persister):
        settings["_settings_reset_notice"] = {"backed_up_to": "settings.json.corrupt-42"}
        first = service.get_settings_reset_notice()
        second = service.get_settings_reset_notice()
        assert first == {"pending": True, "backed_up_to": "settings.json.corrupt-42"}
        assert second == first
        assert settings["_settings_reset_notice"] == {"backed_up_to": "settings.json.corrupt-42"}
        settings_persister.save_settings.assert_not_called()

    def test_a_marker_without_its_backup_is_pending_with_none(self, service, settings):
        settings["_settings_reset_notice"] = {}
        assert service.get_settings_reset_notice() == {"pending": True, "backed_up_to": None}

    def test_after_a_dismissal_it_is_no_longer_pending(self, service, settings):
        settings["_settings_reset_notice"] = {"backed_up_to": "settings.json.corrupt-42"}
        service.dismiss_settings_reset_notice()
        assert service.get_settings_reset_notice() == {"pending": False, "backed_up_to": None}


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


class TestDismissSettingsResetNoticePersistsOnce:
    """Dismissing the settings-reset notice pops the persistent marker and
    persists the dismissal — the user's explicit QAM acknowledgement."""

    @pytest.fixture
    def settings_persister(self) -> FakeSettingsPersister:
        return FakeSettingsPersister()

    @pytest.mark.asyncio
    async def test_pops_marker_and_persists(self, service, settings, settings_persister):
        settings["_settings_reset_notice"] = {"backed_up_to": "settings.json.corrupt-42"}
        before = settings_persister.save_count

        result = service.dismiss_settings_reset_notice()

        assert result == {"success": True}
        assert "_settings_reset_notice" not in settings
        # Read-side now reports not-pending.
        assert service.get_settings_reset_notice() == {"pending": False, "backed_up_to": None}
        # The dismissal was persisted.
        assert settings_persister.save_count == before + 1

    @pytest.mark.asyncio
    async def test_idempotent_when_no_marker(self, service, settings, settings_persister):
        """Acking with no marker present is a harmless persisted no-op."""
        assert "_settings_reset_notice" not in settings
        before = settings_persister.save_count

        result = service.dismiss_settings_reset_notice()

        assert result == {"success": True}
        assert "_settings_reset_notice" not in settings
        assert settings_persister.save_count == before + 1
