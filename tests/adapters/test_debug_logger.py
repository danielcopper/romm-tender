"""Tests for the SettingsAwareDebugLogger adapter — debug lines gated on the live ``log_level`` setting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from adapters.debug_logger import SettingsAwareDebugLogger

if TYPE_CHECKING:
    import logging


@pytest.fixture
def settings() -> dict[str, Any]:
    return {"romm_url": "", "romm_user": "", "romm_pass": "", "enabled_platforms": {}}


@pytest.fixture
def debug_logger(settings: dict[str, Any], logger: logging.Logger) -> SettingsAwareDebugLogger:
    return SettingsAwareDebugLogger(settings=settings, logger=logger)


class TestLogLevel:
    def test_log_debug_enabled(self, settings, debug_logger, logger):
        """The debug logger logs when log_level is 'debug'."""
        settings["log_level"] = "debug"
        with patch.object(logger, "info") as mock_info:
            debug_logger("test message")
            mock_info.assert_called_once_with("test message")

    def test_log_debug_disabled_at_warn(self, settings, debug_logger, logger):
        """The debug logger does not log when log_level is 'warn' (default)."""
        settings["log_level"] = "warn"
        with patch.object(logger, "info") as mock_info:
            debug_logger("test message")
            mock_info.assert_not_called()

    def test_log_debug_disabled_at_info(self, settings, debug_logger, logger):
        """The debug logger does not log when log_level is 'info'."""
        settings["log_level"] = "info"
        with patch.object(logger, "info") as mock_info:
            debug_logger("test message")
            mock_info.assert_not_called()

    def test_log_debug_disabled_at_error(self, settings, debug_logger, logger):
        """The debug logger does not log when log_level is 'error'."""
        settings["log_level"] = "error"
        with patch.object(logger, "info") as mock_info:
            debug_logger("test message")
            mock_info.assert_not_called()

    def test_log_debug_missing_setting_defaults_warn(self, settings, debug_logger, logger):
        """The debug logger does not log when log_level key is missing (defaults to warn)."""
        settings.pop("log_level", None)
        with patch.object(logger, "info") as mock_info:
            debug_logger("test message")
            mock_info.assert_not_called()
