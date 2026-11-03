"""What the process entry hands the host: the built application, repaired, behind the endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from _factories import _make_services_bundle

import main
from domain.app_directories import AppDirectories
from domain.update_release import UpdateSource
from host import EventSink, HostStatus, InjectionSetup
from host.dispatch import route_names
from main import Endpoints, build_backend

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

LOGGER = logging.getLogger("test_build_backend")


class _StubApplication:
    """An ``Application`` in shape, recording what the build does with it."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.services = _make_services_bundle(
            connection_service=MagicMock(test_connection=AsyncMock(return_value={"success": True})),
        )
        self.user_agent = "romm-tender/0.0.0-stub"

    def run_startup_repairs(self, report_failure: Callable[[str], None]) -> None:
        self.calls.append("run_startup_repairs")
        report_failure("prune_orphaned_cover_cache")

    async def shutdown(self) -> None:
        self.calls.append("shutdown")


def _directories_at(tmp_path) -> AppDirectories:
    return AppDirectories(
        config_dir=str(tmp_path / "config"),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        state_dir=str(tmp_path / "state"),
        runtime_dir=str(tmp_path / "run"),
        code_dir=str(tmp_path / "code"),
        bin_dir=str(tmp_path / "home" / ".local" / "bin"),
    )


_UPDATE_SOURCE = UpdateSource(release_api="http://127.0.0.1:9/releases/latest", installed_program=False)
_INSTALLER_ENVIRONMENT = (("TENDER_DATA_DIR", "/data"),)


class TestBuildBackend:
    @staticmethod
    async def _build(tmp_path, monkeypatch: pytest.MonkeyPatch):
        app = _StubApplication()
        asked: list[dict[str, Any]] = []

        def stub_build_application(**kwargs: Any) -> _StubApplication:
            asked.append(kwargs)
            return app

        monkeypatch.setattr(main, "build_application", stub_build_application)
        status = HostStatus()
        events = EventSink(LOGGER)
        built = await build_backend(
            directories=_directories_at(tmp_path),
            update_source=_UPDATE_SOURCE,
            installer_environment=_INSTALLER_ENVIRONMENT,
            user_home=str(tmp_path / "home"),
            logger=LOGGER,
            status=status,
            events=events,
        )
        return built, app, status, events, asked

    async def test_it_builds_the_application_on_the_running_loop_emitting_through_the_sink(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        _built, _app, status, events, asked = await self._build(tmp_path, monkeypatch)
        panel_connected = asked[0].pop("panel_connected")

        assert asked == [
            {
                "directories": _directories_at(tmp_path),
                "update_source": _UPDATE_SOURCE,
                "installer_environment": _INSTALLER_ENVIRONMENT,
                "user_home": str(tmp_path / "home"),
                "logger": LOGGER,
                "loop": asyncio.get_running_loop(),
                "emit": events.emit,
                "steam": status.steam,
                "focus_watcher": False,
                "steam_urls": status.steam_urls,
            }
        ]
        assert panel_connected() is False

        async def send(text: str) -> bool:
            return True

        events.attach(send)
        assert panel_connected() is True

    async def test_a_failing_repair_is_recorded_on_the_host_status(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        _built, app, status, _events, _asked = await self._build(tmp_path, monkeypatch)

        assert app.calls == ["run_startup_repairs"]
        assert status.failed_startup_steps == ["prune_orphaned_cover_cache"]

    async def test_the_host_shuts_down_the_application(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        built, app, _status, _events, _asked = await self._build(tmp_path, monkeypatch)
        app.calls.clear()

        await built.shutdown()

        assert app.calls == ["shutdown"]

    async def test_it_answers_under_the_application_s_identity(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        built, app, _status, _events, _asked = await self._build(tmp_path, monkeypatch)

        assert built.server_identity == app.user_agent

    async def test_calls_reach_the_endpoints_over_the_application_and_the_host_status(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        built, _app, _status, _events, _asked = await self._build(tmp_path, monkeypatch)

        service_answer = json.loads(await built.dispatcher.dispatch(1, "test_connection", []))
        host_answer = json.loads(await built.dispatcher.dispatch(2, "get_host_status", []))

        assert built.dispatcher.method_names == route_names(Endpoints)
        assert service_answer["result"] == {"success": True}
        assert host_answer["result"]["failed_startup_steps"] == ["prune_orphaned_cover_cache"]


class TestTheFocusWatcher:
    async def test_its_switch_reaches_the_application(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        asked: list[dict[str, Any]] = []

        def stub_build_application(**kwargs: Any) -> _StubApplication:
            asked.append(kwargs)
            return _StubApplication()

        monkeypatch.setattr(main, "build_application", stub_build_application)
        status = HostStatus()

        await build_backend(
            directories=_directories_at(tmp_path),
            update_source=_UPDATE_SOURCE,
            installer_environment=_INSTALLER_ENVIRONMENT,
            user_home=str(tmp_path / "home"),
            logger=LOGGER,
            status=status,
            events=EventSink(LOGGER),
            focus_watcher=True,
        )

        assert asked[0]["focus_watcher"] is True
        assert asked[0]["steam_urls"] is status.steam_urls

    async def test_the_step_before_the_detach_releases_its_pin(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        app = _StubApplication()
        monkeypatch.setattr(main, "build_application", lambda **_kwargs: app)

        built = await build_backend(
            directories=_directories_at(tmp_path),
            update_source=_UPDATE_SOURCE,
            installer_environment=_INSTALLER_ENVIRONMENT,
            user_home=str(tmp_path / "home"),
            logger=LOGGER,
            status=HostStatus(),
            events=EventSink(LOGGER),
        )

        assert built.before_detach is app.services.controller_focus_service.release_pin


def _injection(environ: dict[str, str]) -> InjectionSetup:
    return InjectionSetup.from_environment(
        environ, static_root="/dist", state_dir="/state", user_home="/home", version="1"
    )


class TestWhetherTheFocusWatcherRuns:
    def test_by_default_it_runs_and_says_nothing(
        self, logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=logger.name)

        assert main.watches_focus({}, _injection({}), logger) is True
        assert [r for r in caplog.records if r.name == logger.name] == []

    def test_its_switch_turns_it_off_and_it_says_so(
        self, logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=logger.name)
        environ = {"TENDER_FOCUS_WATCHER": "off"}

        assert main.watches_focus(environ, _injection(environ), logger) is False
        assert [r.getMessage() for r in caplog.records if r.name == logger.name] == [
            "controller focus: not watching, TENDER_FOCUS_WATCHER=off is set"
        ]

    def test_with_the_panel_not_loaded_it_does_not_run_and_says_why(
        self, logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=logger.name)
        environ = {"TENDER_INJECT": "off"}

        assert main.watches_focus(environ, _injection(environ), logger) is False
        assert [r.getMessage() for r in caplog.records if r.name == logger.name] == [
            "controller focus: not watching, the panel is not loaded into Steam, so nothing can reach it"
        ]

    def test_forcing_the_panel_in_leaves_it_running(self, logger: logging.Logger) -> None:
        environ = {"TENDER_INJECT": "force"}

        assert main.watches_focus(environ, _injection(environ), logger) is True
