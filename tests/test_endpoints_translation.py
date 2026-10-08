"""What every endpoint answers when the use case behind it refuses — and what it leaves to the host.

Each call goes through the real ``CallDispatcher`` over the real ``Endpoints``,
so the transport half is the one the panel meets: a refusal arrives as a reply
carrying ``{success: False, reason, message}``, a bug as the host's
``backend_exception`` error. The services are stand-ins that raise or answer
what a case hands them, which puts the exception exactly where a use case would
raise it; Stop Game's refusals, the connection's, a partial bulk uninstall, the
conflict resolution's, a partial save deletion, the save syncs' and device
list's refusals, the save slots', the library preview's and the removed-game
cleanup's failures run through the real services.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import json
import logging
import socket
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from _factories import (
    _make_application,
    _make_conflict_rules,
    _make_prune_conflicts,
    _make_retry,
    _make_services_bundle,
)
from bootstrap import ServicesBundle
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_core_info_provider import FakeCoreInfoProvider
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_emulator_sources import FakeEmulatorSources
from fakes.fake_event_sink import FakeEventSink
from fakes.fake_firmware_file_store import FakeFirmwareFileStore
from fakes.fake_firmware_resolver import FakeFirmwareResolver
from fakes.fake_game_process_control import DEFAULT_LAUNCH_PATH, FakeGameProcessControlAdapter
from fakes.fake_journal import FakeJournal
from fakes.fake_platform_core_reader import FakePlatformCoreReader
from fakes.fake_release_download import FakeReleaseDownload
from fakes.fake_renderer_gc import FakeRendererGc
from fakes.fake_renderer_rss import FakeRendererRss
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders
from fakes.fake_rom_file_store import FakeRomFileStore
from fakes.fake_rom_launch_path import FakeRomLaunchPathReader
from fakes.fake_romm_api import FakeRommApi
from fakes.fake_save_location_reader import FakeSaveLocationReader
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_steam_interface import FakeSteamInterface
from fakes.fake_transient_units import FakeTransientUnits
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.library_peers import FakeArtworkManager
from fakes.system_time import FakeClock, FakeSleeper, FakeUuidGen

from adapters.steam_config import SteamConfigAdapter
from adapters.update_attempt import UpdateAttemptFileAdapter
from adapters.update_staging import UpdateStagingAdapter
from domain.bios_file import BiosFile
from domain.refusal import DomainRefused, NamedDomainRefused
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.rom_save_sync_state import FileSyncState, RomSaveSyncState
from domain.update_release import LatestRelease, ReleaseTarball
from host import CallDispatcher, HostStatus
from host.dispatch import route_names
from host.protocol import REASON_BACKEND_EXCEPTION, TYPE_ERROR, TYPE_REPLY
from lib.errors import (
    NamedRefused,
    PairingCodeInvalidError,
    Refused,
    RommApiError,
    RommAuthError,
    RommConflictError,
    RommConnectionError,
    RommForbiddenError,
    RommNotFoundError,
    RommServerError,
    RommSSLError,
    RommSyncDisabledError,
    RommTimeoutError,
    RommUnsupportedError,
    TokenHostMismatchError,
    classify_error,
)
from lib.partial_failure import PartialFailure
from main import Endpoints
from services.connection import ConnectionService, ConnectionServiceConfig
from services.firmware import FirmwareService, FirmwareServiceConfig
from services.game_process import GameProcessService, GameProcessServiceConfig
from services.library import LibraryService, LibraryServiceConfig
from services.playtime import PlaytimeService, PlaytimeServiceConfig
from services.prune import PruneService, PruneServiceConfig
from services.rom_removal import RomRemovalService, RomRemovalServiceConfig
from services.update_install import UpdateInstallService, UpdateInstallServiceConfig
from services.update_output import UpdateOutputService, UpdateOutputServiceConfig
from tests.services.saves._helpers import (
    _create_save,
    _enable_sync_with_device,
    _file_md5,
    _install_rom,
    _seed_save_state,
    _server_save,
    _server_save_with_syncs,
    make_service,
)

_MAIN_PY = Path(__file__).resolve().parents[1] / "backend" / "main.py"

LOGGER = logging.getLogger("test_endpoints_translation")

ROUTES = sorted(route_names(Endpoints))

# One endpoint of each kind, for the cases that are about the answer rather than the surface.
_A_SYNC_ROUTE = "get_known_regions"
_AN_ASYNC_ROUTE = "stop_running_game"


class _RaisingService:
    """A service whose every method raises *exc* when it is called."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Any:
        def _raise(*_args: Any, **_kwargs: Any) -> Any:
            raise self._exc

        return _raise


class _AnsweringService:
    """A service whose every method answers *result*, awaitable or not as its caller expects."""

    def __init__(self, result: Any, *, awaitable: bool) -> None:
        self._result = result
        self._awaitable = awaitable

    def __getattr__(self, name: str) -> Any:
        async def _answer_later(*_args: Any, **_kwargs: Any) -> Any:
            return self._result

        def _answer_now(*_args: Any, **_kwargs: Any) -> Any:
            return self._result

        return _answer_later if self._awaitable else _answer_now


class _AwaitableService:
    """A service whose every method hands back a fresh awaitable from *make*, as a ``def`` endpoint may pass on."""

    def __init__(self, make: Any) -> None:
        self._make = make

    def __getattr__(self, name: str) -> Any:
        def _hand_back(*_args: Any, **_kwargs: Any) -> Any:
            return self._make()

        return _hand_back


class _RaisingHostStatus:
    """The host's record of this run, raising *exc* at every read."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Any:
        raise self._exc


def _every_service(service: object) -> ServicesBundle:
    return _make_services_bundle(**{field.name: service for field in dataclasses.fields(ServicesBundle)})


def _dispatcher_raising(exc: BaseException) -> CallDispatcher:
    """The real dispatcher over the real ``Endpoints``, with *exc* raised behind every route."""
    host_status = cast("HostStatus", _RaisingHostStatus(exc))
    endpoints = Endpoints(_make_application(_every_service(_RaisingService(exc))), host_status)
    return CallDispatcher(endpoints, LOGGER)


def _dispatcher_answering(result: Any, *, awaitable: bool) -> CallDispatcher:
    services = _every_service(_AnsweringService(result, awaitable=awaitable))
    endpoints = Endpoints(_make_application(services), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


def _args_for(route_name: str) -> list[Any]:
    """One positional argument per required parameter of *route_name*, each an id-shaped ``1``."""
    parameters = list(inspect.signature(getattr(Endpoints, route_name)).parameters.values())[1:]
    return [1 for parameter in parameters if parameter.default is inspect.Parameter.empty]


async def _call(dispatcher: CallDispatcher, route_name: str) -> dict[str, Any]:
    return json.loads(await dispatcher.dispatch(7, route_name, _args_for(route_name)))


def _endpoint_kinds_in_the_source() -> dict[str, bool]:
    """Each public method of ``Endpoints`` as written in ``main.py``, mapped to whether it is ``async def``."""
    tree = ast.parse(_MAIN_PY.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Endpoints")
    return {
        node.name: isinstance(node, ast.AsyncFunctionDef)
        for node in cls.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_")
    }


class TestARefusalIsAnAnswerOnEveryRoute:
    @pytest.mark.parametrize("route_name", ROUTES)
    async def test_a_raised_refusal_arrives_as_the_failure_shape_with_its_details(self, route_name):
        refusal = Refused("some_reason", "Nothing was done.", wait_reasons=["sync"], count=2)

        message = await _call(_dispatcher_raising(refusal), route_name)

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "some_reason",
            "message": "Nothing was done.",
            "wait_reasons": ["sync"],
            "count": 2,
        }

    @pytest.mark.parametrize("route_name", ROUTES)
    async def test_a_refusal_the_domain_raised_arrives_as_the_failure_shape(self, route_name):
        refusal = DomainRefused("target_occupied", "That name is taken.", collisions=["a.sav"])

        message = await _call(_dispatcher_raising(refusal), route_name)

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "target_occupied",
            "message": "That name is taken.",
            "collisions": ["a.sav"],
        }

    @pytest.mark.parametrize("route_name", ROUTES)
    async def test_a_bug_still_arrives_as_a_transport_error(self, route_name):
        message = await _call(_dispatcher_raising(RuntimeError("a bug")), route_name)

        assert message["type"] == TYPE_ERROR
        assert message["reason"] == REASON_BACKEND_EXCEPTION
        assert message["message"] == "RuntimeError: a bug"
        assert "result" not in message


class TestTheRefusalItself:
    async def test_a_named_subclass_answers_with_its_class_attribute(self):
        class SyncActive(NamedRefused):
            reason = "sync_active"

        message = await _call(_dispatcher_raising(SyncActive("A sync is running.")), _AN_ASYNC_ROUTE)

        assert message["result"] == {"success": False, "reason": "sync_active", "message": "A sync is running."}

    @pytest.mark.parametrize("named_base", [NamedRefused, NamedDomainRefused])
    def test_a_named_refusal_raised_with_only_a_message_carries_its_class_reason(self, named_base):
        named = type("TargetOccupied", (named_base,), {"reason": "target_occupied"})

        refusal = named("That name is taken.", collisions=["a.sav"])

        assert (refusal.reason, refusal.message, refusal.details) == (
            "target_occupied",
            "That name is taken.",
            {"collisions": ["a.sav"]},
        )

    @pytest.mark.parametrize("named_base", [NamedRefused, NamedDomainRefused])
    def test_a_named_refusal_cannot_be_raised_under_another_reason(self, named_base):
        named = type("TargetOccupied", (named_base,), {"reason": "target_occupied"})

        with pytest.raises(TypeError):
            named("another_reason", "That name is taken.")

    @pytest.mark.parametrize("refusal_type", [Refused, DomainRefused])
    async def test_a_detail_named_success_cannot_turn_a_refusal_into_a_success(self, refusal_type):
        message = await _call(_dispatcher_raising(refusal_type("x", "y", success=True)), _A_SYNC_ROUTE)

        assert message["result"]["success"] is False

    def test_a_refusal_carries_its_reason_message_and_details(self):
        refusal = Refused("not_running", "No running game was found to stop.", rom_id=3)

        assert (refusal.reason, refusal.message, refusal.details) == (
            "not_running",
            "No running game was found to stop.",
            {"rom_id": 3},
        )
        assert str(refusal) == "No running game was found to stop."

    def test_a_domain_refusal_carries_its_reason_message_and_details(self):
        refusal = DomainRefused("target_occupied", "That name is taken.", collisions=["a.sav"])

        assert (refusal.reason, refusal.message, refusal.details) == (
            "target_occupied",
            "That name is taken.",
            {"collisions": ["a.sav"]},
        )
        assert str(refusal) == "That name is taken."


_ROMM_API_ERRORS = [
    pytest.param(RommAuthError("401"), "auth_failed", id="401"),
    pytest.param(RommForbiddenError("403"), "auth_failed", id="403"),
    pytest.param(RommSSLError("ssl"), "server_unreachable", id="ssl"),
    pytest.param(RommTimeoutError("slow"), "server_unreachable", id="timeout"),
    pytest.param(RommConnectionError("refused"), "server_unreachable", id="connection"),
    pytest.param(RommServerError("boom", status_code=502), "server_unreachable", id="5xx"),
    pytest.param(RommNotFoundError("gone"), "not_found", id="404"),
    pytest.param(RommUnsupportedError("Device sync", "5.3.0"), "unsupported", id="unsupported"),
    pytest.param(TokenHostMismatchError("other host"), "config_error", id="token-host-mismatch"),
    pytest.param(RommSyncDisabledError("sync off"), "device_sync_disabled", id="sync-disabled"),
    pytest.param(RommConflictError("409"), "server_unreachable", id="any-other-api-error"),
    pytest.param(PairingCodeInvalidError("bad code"), "server_unreachable", id="pairing-code"),
]


class TestTheRommApiErrorFamily:
    @pytest.mark.parametrize("route_name", [_A_SYNC_ROUTE, _AN_ASYNC_ROUTE])
    @pytest.mark.parametrize(("exc", "reason"), _ROMM_API_ERRORS)
    async def test_arrives_with_classify_errors_reason_and_message(self, route_name, exc, reason):
        message = await _call(_dispatcher_raising(exc), route_name)

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {"success": False, "reason": reason, "message": classify_error(exc)[1]}

    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param(ConnectionError("reset"), id="ConnectionError"),
            pytest.param(TimeoutError("slow"), id="TimeoutError"),
            pytest.param(socket.gaierror("no such host"), id="gaierror"),
            pytest.param(OSError("disk"), id="OSError"),
            pytest.param(ValueError("broken invariant"), id="ValueError"),
        ],
    )
    async def test_an_exception_outside_the_family_is_not_translated(self, exc):
        message = await _call(_dispatcher_raising(exc), _AN_ASYNC_ROUTE)

        assert message["type"] == TYPE_ERROR
        assert message["reason"] == REASON_BACKEND_EXCEPTION


@dataclasses.dataclass(frozen=True)
class _DeletedSome(PartialFailure):
    deleted_count: int


@dataclasses.dataclass(frozen=True)
class _ClaimsSuccess(PartialFailure):
    success: bool


class TestAPartialFailureIsSerialized:
    @pytest.mark.parametrize(("route_name", "awaitable"), [(_A_SYNC_ROUTE, False), (_AN_ASYNC_ROUTE, True)])
    async def test_a_returned_partial_failure_arrives_in_todays_shape(self, route_name, awaitable):
        result = _DeletedSome(reason="unknown", message="Some files could not be deleted.", deleted_count=3)

        message = await _call(_dispatcher_answering(result, awaitable=awaitable), route_name)

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "unknown",
            "message": "Some files could not be deleted.",
            "deleted_count": 3,
        }

    async def test_a_field_named_success_cannot_turn_it_into_a_success(self):
        result = _ClaimsSuccess(reason="unknown", message="m", success=True)

        message = await _call(_dispatcher_answering(result, awaitable=False), _A_SYNC_ROUTE)

        assert message["result"]["success"] is False

    @pytest.mark.parametrize(("route_name", "awaitable"), [(_A_SYNC_ROUTE, False), (_AN_ASYNC_ROUTE, True)])
    async def test_any_other_answer_passes_through_unchanged(self, route_name, awaitable):
        answer = {"success": True, "stopped": 1, "force_killed": 0}

        message = await _call(_dispatcher_answering(answer, awaitable=awaitable), route_name)

        assert message["result"] == answer


class TestEveryEndpointKeepsItsKind:
    def test_every_endpoint_in_the_source_is_reachable(self):
        assert route_names(Endpoints) == set(_endpoint_kinds_in_the_source())

    @pytest.mark.parametrize("route_name", ROUTES)
    def test_a_def_stays_synchronous_and_an_async_def_a_coroutine_function(self, route_name):
        written_async = _endpoint_kinds_in_the_source()[route_name]

        assert inspect.iscoroutinefunction(getattr(Endpoints, route_name)) is written_async

    @pytest.mark.parametrize("route_name", ROUTES)
    def test_every_endpoint_is_the_translating_wrapper(self, route_name):
        # The code's own name: ``functools.wraps`` copies ``__qualname__`` from the endpoint, not this.
        assert getattr(Endpoints, route_name).__code__.co_qualname.startswith("_translated.<locals>.")


async def _raise_later(exc: BaseException) -> Any:
    raise exc


async def _answer_later(result: Any) -> Any:
    return result


def _dispatcher_handing_back(make: Any) -> CallDispatcher:
    endpoints = Endpoints(_make_application(_every_service(_AwaitableService(make))), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


class TestADefEndpointHandingBackAnAwaitable:
    """The dispatcher awaits whatever a ``def`` endpoint returns; what it awaits is translated too."""

    def test_the_route_these_cases_use_is_a_def(self):
        assert not inspect.iscoroutinefunction(getattr(Endpoints, _A_SYNC_ROUTE))

    async def test_a_refusal_raised_when_awaited_arrives_as_the_failure_shape(self):
        dispatcher = _dispatcher_handing_back(lambda: _raise_later(Refused("not_now", "Not now.", count=1)))

        message = await _call(dispatcher, _A_SYNC_ROUTE)

        assert message["result"] == {"success": False, "reason": "not_now", "message": "Not now.", "count": 1}

    async def test_a_romm_error_raised_when_awaited_arrives_with_classify_errors_reason(self):
        dispatcher = _dispatcher_handing_back(lambda: _raise_later(RommNotFoundError("gone")))

        message = await _call(dispatcher, _A_SYNC_ROUTE)

        assert message["result"] == {
            "success": False,
            "reason": "not_found",
            "message": classify_error(RommNotFoundError("gone"))[1],
        }

    async def test_a_partial_failure_answered_when_awaited_is_serialized(self):
        result = _DeletedSome(reason="unknown", message="Some files could not be deleted.", deleted_count=2)

        message = await _call(_dispatcher_handing_back(lambda: _answer_later(result)), _A_SYNC_ROUTE)

        assert message["result"] == {
            "success": False,
            "reason": "unknown",
            "message": "Some files could not be deleted.",
            "deleted_count": 2,
        }

    async def test_any_other_answer_passes_through(self):
        message = await _call(_dispatcher_handing_back(lambda: _answer_later({"success": True})), _A_SYNC_ROUTE)

        assert message["result"] == {"success": True}

    async def test_a_bug_raised_when_awaited_is_still_a_transport_error(self):
        message = await _call(_dispatcher_handing_back(lambda: _raise_later(RuntimeError("a bug"))), _A_SYNC_ROUTE)

        assert message["type"] == TYPE_ERROR
        assert message["reason"] == REASON_BACKEND_EXCEPTION


def _records_from_the_entrypoint(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == Endpoints.__module__]


class TestWhatTheEntrypointLogs:
    @pytest.mark.parametrize("route_name", [_A_SYNC_ROUTE, _AN_ASYNC_ROUTE])
    @pytest.mark.parametrize(
        ("exc", "level"),
        [
            pytest.param(RommNotFoundError("rom 7 is gone"), logging.WARNING, id="not-found"),
            pytest.param(RommAuthError("rom 7 is gone"), logging.WARNING, id="auth-failed"),
            pytest.param(RommConnectionError("rom 7 is gone"), logging.INFO, id="server-unreachable"),
            pytest.param(RommServerError("rom 7 is gone", status_code=502), logging.INFO, id="server-error"),
        ],
    )
    async def test_a_translated_romm_error_is_one_line_without_a_stack(self, route_name, exc, level, caplog):
        caplog.set_level(logging.DEBUG)

        await _call(_dispatcher_raising(exc), route_name)

        records = _records_from_the_entrypoint(caplog)
        assert [(record.levelno, record.getMessage()) for record in records] == [
            (level, f"{route_name}: answered {type(exc).__name__}: rom 7 is gone")
        ]
        assert records[0].exc_info is None
        assert records[0].stack_info is None

    async def test_only_an_unreachable_verdict_is_logged_below_warning(self, caplog):
        caplog.set_level(logging.DEBUG)

        cases = [cast("tuple[RommApiError, str]", case.values) for case in _ROMM_API_ERRORS]
        for exc, _reason in cases:
            await _call(_dispatcher_raising(exc), _AN_ASYNC_ROUTE)

        levels = [record.levelno for record in _records_from_the_entrypoint(caplog)]
        assert levels == [logging.INFO if reason == "server_unreachable" else logging.WARNING for _exc, reason in cases]

    @pytest.mark.parametrize(
        "refusal",
        [Refused("not_now", "Not now."), DomainRefused("target_occupied", "That name is taken.")],
    )
    async def test_a_refusal_is_not_logged(self, refusal, caplog):
        caplog.set_level(logging.DEBUG)

        await _call(_dispatcher_raising(refusal), _AN_ASYNC_ROUTE)

        assert _records_from_the_entrypoint(caplog) == []


class _YieldingSleeper:
    """A ``Sleeper`` that hands the loop over without waiting, so a second call lands inside the grace window."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)


def _dispatcher_over_game_process(control: FakeGameProcessControlAdapter) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose Stop Game is the real service over a fake process table."""
    service = GameProcessService(
        config=GameProcessServiceConfig(
            game_process=control,
            launch_path=FakeRomLaunchPathReader({42: DEFAULT_LAUNCH_PATH}),
            sleeper=_YieldingSleeper(),
            logger=LOGGER,
            log_debug=MagicMock(),
            flatpak_app_id="net.retrodeck.retrodeck",
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(game_process_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


class TestTheStopGameRefusalsOnTheWire:
    """Stop Game's three refusals, raised by the real service, reach the wire with their exact reason and message."""

    async def test_nothing_running(self):
        message = json.loads(
            await _dispatcher_over_game_process(FakeGameProcessControlAdapter(pids=[])).dispatch(
                1, "stop_running_game", [42]
            )
        )

        assert message["result"] == {
            "success": False,
            "reason": "not_running",
            "message": "No running game was found to stop.",
        }

    async def test_another_game_running(self):
        control = FakeGameProcessControlAdapter()
        control.add_instance([201], "/home/deck/retrodeck/roms/snes/someone-elses.sfc")

        message = json.loads(await _dispatcher_over_game_process(control).dispatch(1, "stop_running_game", [42]))

        assert message["result"] == {
            "success": False,
            "reason": "game_not_running",
            "message": "RetroDECK is running, but not this game — nothing was stopped.",
        }

    async def test_a_second_press_while_the_first_is_stopping(self):
        control = FakeGameProcessControlAdapter(pids=[101])
        control.survive_stop = {101}
        dispatcher = _dispatcher_over_game_process(control)

        first, second = await asyncio.gather(
            dispatcher.dispatch(1, "stop_running_game", [42]),
            dispatcher.dispatch(2, "stop_running_game", [42]),
        )

        assert json.loads(first)["result"] == {"success": True, "stopped": 1, "force_killed": 1}
        assert json.loads(second)["result"] == {
            "success": False,
            "reason": "already_stopping",
            "message": "The game is already being stopped.",
        }
        assert control.stop_calls == [101]


def _dispatcher_over_connection(romm_api: MagicMock, settings: dict[str, Any], persister: MagicMock) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose connection use cases are the real service over a RomM stand-in."""
    service = ConnectionService(
        config=ConnectionServiceConfig(
            settings=settings,
            romm_api=romm_api,
            settings_persister=persister,
            loop=asyncio.get_running_loop(),
            logger=LOGGER,
            min_required_version=(5, 3, 0),
            forget_device=MagicMock(),
            clear_playtime_scope_notice=MagicMock(),
            conflict_rules=_make_conflict_rules(),
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(connection_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


def _romm_running(version: str) -> MagicMock:
    romm_api = MagicMock()
    romm_api.heartbeat.return_value = {"SYSTEM": {"VERSION": version}}
    romm_api.mint_client_token.return_value = {"id": 42, "raw_token": "rmm_minted"}
    return romm_api


class TestTheConnectionRefusalsOnTheWire:
    """The connection refusals, raised by the real service, reach the wire with their reason, message and details."""

    @pytest.mark.parametrize(
        ("route_name", "args"),
        [
            ("test_connection", []),
            ("connect_with_credentials", ["http://romm.local", "u", "p", False]),
        ],
    )
    async def test_an_unsupported_version_carries_the_servers_version(self, route_name, args):
        settings = {"romm_url": "http://romm.local", "romm_api_token": "rmm_token"}
        dispatcher = _dispatcher_over_connection(_romm_running("4.5.0"), settings, MagicMock())

        message = json.loads(await dispatcher.dispatch(1, route_name, args))

        assert message["result"] == {
            "success": False,
            "reason": "version_error",
            "message": (
                "Tender requires RomM 5.3.0 or newer. Your server is running 4.5.0. "
                "Please update your RomM server to continue using Tender."
            ),
            "romm_version": "4.5.0",
        }

    @pytest.mark.parametrize(
        ("route_name", "args"),
        [
            ("connect_with_credentials", ["http://romm.local", "u", "p", False]),
            ("sign_out", []),
        ],
    )
    async def test_a_failed_settings_write_answers_save_failed_with_its_cause(self, route_name, args):
        settings = {"romm_url": "http://romm.local", "romm_api_token": "rmm_token"}
        persister = MagicMock()
        persister.save_settings.side_effect = OSError(28, "No space left on device")
        dispatcher = _dispatcher_over_connection(_romm_running("5.3.0"), settings, persister)

        message = json.loads(await dispatcher.dispatch(1, route_name, args))

        assert message["result"] == {
            "success": False,
            "reason": "save_failed",
            "message": "Save failed: [Errno 28] No space left on device",
        }
        assert settings["romm_api_token"] == "rmm_token"


def _dispatcher_over_rom_removal(uow: FakeUnitOfWork, rom_files: FakeRomFileStore) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose ROM removal is the real service over a fake file store."""
    service = RomRemovalService(
        config=RomRemovalServiceConfig(
            logger=LOGGER,
            loop=asyncio.get_running_loop(),
            clock=FakeClock(),
            emit=FakeEventSink().emit,
            rom_file_store=rom_files,
            retrodeck_folders=FakeRetroDeckFolders(roms="/retrodeck/roms"),
            download_queue_cleanup=None,
            uow_factory=FakeUnitOfWorkFactory(uow),
            conflict_rules=_make_conflict_rules(),
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(rom_removal_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


class TestTheBulkUninstallOnTheWire:
    """A bulk uninstall that removed only part of the ROMs answers ``uninstall_incomplete`` with what it did."""

    async def test_a_partial_run_answers_its_counts_its_app_ids_and_its_lease(self):
        uow = FakeUnitOfWork()
        rom_files = FakeRomFileStore()
        for rom_id in (1, 2, 3):
            rom_path = f"/retrodeck/roms/n64/game_{rom_id}.z64"
            rom_files.files[rom_path] = b"rom"
            with uow:
                uow.roms.save(
                    Rom(
                        rom_id=rom_id,
                        platform_slug="n64",
                        name=f"Game {rom_id}",
                        fs_name=f"game_{rom_id}.z64",
                        shortcut_app_id=1000 + rom_id,
                        last_synced_at="2025-01-01T00:00:00",
                    )
                )
                uow.rom_installs.save(
                    RomInstall.mark_installed(
                        rom_id=rom_id,
                        file_path=rom_path,
                        rom_dir=None,
                        platform_slug="n64",
                        system="n64",
                        installed_at="2025-01-01T00:00:00",
                    )
                )
        rom_files.remove_file_failures.add("/retrodeck/roms/n64/game_2.z64")

        message = json.loads(await _dispatcher_over_rom_removal(uow, rom_files).dispatch(1, "uninstall_all_roms", []))

        assert message["result"] == {
            "success": False,
            "reason": "uninstall_incomplete",
            "message": "1 ROM could not be uninstalled",
            "removed_count": 2,
            "errors": [{"rom_id": "2", "error": "simulated remove_file failure: /retrodeck/roms/n64/game_2.z64"}],
            "app_ids": [1001, 1003],
            "prune_lease_token": "bulk_uninstall:1",
        }


def _playtime_service_over(uow: FakeUnitOfWork) -> PlaytimeService:
    """The real playtime service over a fake unit of work, with no device registered and no RomM to reach."""
    return PlaytimeService(
        config=PlaytimeServiceConfig(
            romm_api=MagicMock(),
            retry=_make_retry(),
            device_id_provider=MagicMock(get_device_id=MagicMock(return_value=None)),
            loop=asyncio.get_running_loop(),
            logger=LOGGER,
            clock=FakeClock(),
            log_debug=MagicMock(),
            uow_factory=FakeUnitOfWorkFactory(uow),
            conflict_rules=_make_conflict_rules(),
        )
    )


class TestTheSessionStartRefusalOnTheWire:
    """A session start for a ROM with no ``roms`` row, refused by the real service, answers ``unknown_rom``."""

    async def test_an_unknown_rom(self):
        service = _playtime_service_over(FakeUnitOfWork())
        endpoints = Endpoints(_make_application(_make_services_bundle(playtime_service=service)), HostStatus())

        message = json.loads(await CallDispatcher(endpoints, LOGGER).dispatch(1, "record_session_start", [42]))
        await asyncio.gather(*service._flush_tasks)

        assert message["result"] == {"success": False, "reason": "unknown_rom", "message": "Unknown ROM"}


def _dispatcher_over_saves(service: Any) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose save use cases are the real ``SaveService``."""
    endpoints = Endpoints(_make_application(_make_services_bundle(save_sync_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


def _a_shown_conflict(tmp_path: Path) -> tuple[Any, Any]:
    """A real ``SaveService`` with an installed ROM, a local save and server save 100 in its slot."""
    svc, fake = make_service(tmp_path)
    _enable_sync_with_device(svc)
    _install_rom(svc, tmp_path)
    _create_save(tmp_path, content=b"local")
    fake.saves[100] = _server_save_with_syncs(device_syncs=[{"device_id": "device-1", "is_current": False}])
    return svc, fake


class TestTheConflictResolutionOnTheWire:
    """The conflict resolution's refusals and RomM errors, raised through the real service, reach the wire."""

    async def test_a_romm_error_listing_the_servers_saves_answers_classify_errors_verdict(self, tmp_path):
        svc, fake = _a_shown_conflict(tmp_path)
        failure = RommServerError("bad gateway", status_code=502)
        fake.fail_on_next(failure)

        message = json.loads(
            await _dispatcher_over_saves(svc).dispatch(
                1, "resolve_sync_conflict", [42, "pokemon.srm", 100, "keep_local"]
            )
        )

        reason, text = classify_error(failure)
        assert message["result"] == {"success": False, "reason": reason, "message": text}

    async def test_a_romm_error_from_the_transfer_answers_classify_errors_verdict(self, tmp_path, monkeypatch):
        svc, fake = _a_shown_conflict(tmp_path)
        failure = RommAuthError("unauthorized")

        def failing_download(*_args, **_kwargs):
            raise failure

        monkeypatch.setattr(fake, "download_save_content", failing_download)

        message = json.loads(
            await _dispatcher_over_saves(svc).dispatch(
                1, "resolve_sync_conflict", [42, "pokemon.srm", 100, "use_server"]
            )
        )

        reason, text = classify_error(failure)
        assert message["result"] == {"success": False, "reason": reason, "message": text}

    async def test_an_os_error_on_the_local_side_answers_resolve_failed(self, tmp_path, monkeypatch):
        svc, fake = _a_shown_conflict(tmp_path)

        def failing_download(*_args, **_kwargs):
            raise PermissionError("save directory is read-only")

        monkeypatch.setattr(fake, "download_save_content", failing_download)

        message = json.loads(
            await _dispatcher_over_saves(svc).dispatch(
                1, "resolve_sync_conflict", [42, "pokemon.srm", 100, "use_server"]
            )
        )

        assert message["result"] == {
            "success": False,
            "reason": "resolve_failed",
            "message": "save directory is read-only",
        }

    async def test_a_conflict_the_server_has_moved_past_answers_stale_conflict(self, tmp_path):
        svc, fake = _a_shown_conflict(tmp_path)
        fake.saves[200] = _server_save_with_syncs(
            save_id=200,
            updated_at="2026-03-01T00:00:00Z",
            device_syncs=[{"device_id": "device-2", "is_current": True}],
        )

        message = json.loads(
            await _dispatcher_over_saves(svc).dispatch(
                1, "resolve_sync_conflict", [42, "pokemon.srm", 100, "keep_local"]
            )
        )

        assert message["result"] == {
            "success": False,
            "reason": "stale_conflict",
            "message": "Server save changed since conflict was shown; please retry sync.",
        }


class TestThePartialSaveDeletionOnTheWire:
    """A save deletion that removed only part answers ``delete_incomplete`` with how many files it removed."""

    @pytest.mark.parametrize(
        ("route_name", "args", "text"),
        [
            ("delete_local_saves", [42], "Deleted 1 file(s), 1 error(s)"),
            ("delete_platform_saves", ["gba"], "Deleted 1 file(s) from 1 ROM(s), 1 error(s)"),
        ],
    )
    async def test_a_partial_deletion_answers_its_count(self, tmp_path, monkeypatch, route_name, args, text):
        svc, _fake = make_service(tmp_path)
        _install_rom(svc, tmp_path)
        _create_save(tmp_path, content=b"kept", ext=".srm")
        stuck = _create_save(tmp_path, content=b"stuck", ext=".rtc")
        store = svc._save_file_store
        remove_file = store.remove_file

        def remove_all_but_the_stuck_one(path: str) -> None:
            if path == str(stuck):
                raise PermissionError(f"cannot remove {path}")
            remove_file(path)

        monkeypatch.setattr(store, "remove_file", remove_all_but_the_stuck_one)

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, route_name, args))

        assert message["result"] == {
            "success": False,
            "reason": "delete_incomplete",
            "message": text,
            "deleted_count": 1,
        }
        assert stuck.exists()


class TestTheSaveSyncRefusalsOnTheWire:
    """The save syncs' and the device list's refusals, raised through the real service, keep their details."""

    async def test_an_offline_server_before_a_launch_answers_offline_with_nothing_synced(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        fake.saves[100] = _server_save()
        fake.heartbeat_raises = RommConnectionError("Connection refused")

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "pre_launch_sync", [42]))

        assert message["result"] == {
            "success": False,
            "reason": "server_unreachable",
            "message": "Server offline",
            "synced": 0,
            "offline": True,
        }

    async def test_a_romm_error_from_the_heartbeat_answers_classify_errors_verdict(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        failure = RommAuthError("401 Unauthorized")
        fake.heartbeat_raises = failure

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "pre_launch_sync", [42]))

        reason, text = classify_error(failure)
        assert message["result"] == {"success": False, "reason": reason, "message": text}

    async def test_a_save_beside_the_content_answers_its_benign_skip(self, tmp_path):
        svc, _fake = make_service(tmp_path, save_locations=FakeSaveLocationReader(beside_content=True))
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "pre_launch_sync", [42]))

        assert message["result"] == {
            "success": False,
            "reason": "savefiles_in_content_dir",
            "message": "Save sync is unavailable: saves are written to the game's content directory.",
            "synced": 0,
        }

    async def test_a_sweep_the_server_stops_partway_answers_what_it_synced(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        for rom_id, system, name, ext in ((1, "gba", "game1", "gba"), (2, "snes", "game2", "sfc")):
            _install_rom(svc, tmp_path, rom_id=rom_id, system=system, file_name=f"{name}.{ext}")
            _seed_save_state(
                svc,
                rom_id,
                RomSaveSyncState(system=system, slot_confirmed=True, active_slot="default"),
                platform_slug=system,
            )
            _create_save(tmp_path, system=system, rom_name=name, content=name.encode())
        negotiations = iter(
            [
                # The whole-device session does not open, so each ROM opens its own;
                # the first one's opens, the second one's meets the server's switch.
                {"session_id": None, "operations": []},
                {"session_id": 100, "operations": []},
                RommSyncDisabledError("Sync is disabled for this device"),
            ]
        )

        def negotiate(_device_id, _saves):
            answer = next(negotiations)
            if isinstance(answer, Exception):
                raise answer
            return answer

        fake.negotiate_sync = negotiate  # type: ignore[method-assign]

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "sync_all_saves", []))

        assert message["result"] == {
            "success": False,
            "reason": "device_sync_disabled",
            "message": "Save sync is disabled for this device on the RomM server — stopped after syncing 1 save(s)",
            "synced": 1,
            "conflicts": 0,
            "conflicts_list": [],
            "roms_checked": 2,
            "errors": [],
        }

    async def test_the_device_list_with_save_sync_off_answers_disabled(self, tmp_path):
        svc, _fake = make_service(tmp_path)

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "list_devices", []))

        assert message["result"] == {
            "success": False,
            "reason": "sync_disabled",
            "message": "Save sync is disabled",
            "disabled": True,
        }

    async def test_a_romm_error_listing_the_devices_answers_classify_errors_verdict(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        failure = RommNotFoundError("HTTP 404: Not Found")
        fake.fail_on_next(failure)

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "list_devices", []))

        reason, text = classify_error(failure)
        assert message["result"] == {"success": False, "reason": reason, "message": text}


def _slots_seeded(tmp_path: Path, *, files: dict[str, FileSyncState] | None = None) -> tuple[Any, Any]:
    """A real ``SaveService`` with an installed ROM whose confirmed active slot is ``default``, beside ``save1``."""
    svc, fake = make_service(tmp_path)
    _enable_sync_with_device(svc)
    _install_rom(svc, tmp_path)
    _seed_save_state(
        svc,
        42,
        RomSaveSyncState(
            active_slot="default",
            slot_confirmed=True,
            slots={
                "default": {"source": "server", "count": 1, "latest_updated_at": "2026-04-17T10:00:00"},
                "save1": {"source": "server", "count": 1, "latest_updated_at": None},
            },
            files=files or {},
        ),
    )
    return svc, fake


class TestTheSlotRefusalsOnTheWire:
    """The save slots' failure answers — refusals, RomM errors and the partial switch — keep their details
    through the real service.
    """

    async def test_an_unreachable_server_answers_the_last_known_slots(self, tmp_path):
        svc, fake = _slots_seeded(tmp_path)
        fake.fail_on_next(RommConnectionError("connection refused"))

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "get_save_slots", [42]))

        assert message["result"] == {
            "success": False,
            "reason": "server_unreachable",
            "message": "Server unreachable — check your URL and ensure RomM is running",
            "last_known": {
                "slots": [
                    {"slot": "default", "source": "server", "count": 1, "latest_updated_at": "2026-04-17T10:00:00"},
                    {"slot": "save1", "source": "server", "count": 1, "latest_updated_at": None},
                ],
                "active_slot": "default",
            },
        }

    async def test_an_auth_failure_answers_without_the_last_known_slots(self, tmp_path):
        svc, fake = _slots_seeded(tmp_path)
        failure = RommAuthError("401 Unauthorized")
        fake.fail_on_next(failure)

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "get_save_slots", [42]))

        reason, text = classify_error(failure)
        assert message["result"] == {"success": False, "reason": reason, "message": text}

    async def test_a_switch_that_downloaded_only_part_answers_switch_incomplete(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        save_path = _create_save(tmp_path)
        _seed_save_state(
            svc,
            42,
            RomSaveSyncState(
                active_slot="default",
                slot_confirmed=True,
                files={"pokemon.srm": FileSyncState(last_sync_hash=_file_md5(str(save_path)), tracked_save_id=100)},
            ),
        )
        fake.saves[700] = _server_save(save_id=700, slot="target")
        fake.fail_download_on(700, RommServerError("download blew up", status_code=500))

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "switch_slot", [42, "target"]))

        assert message["result"] == {
            "success": False,
            "reason": "switch_incomplete",
            "message": "Switched to slot but 1 save(s) failed to download — retry",
        }

    async def test_a_switch_over_unsynced_changes_answers_the_files(self, tmp_path):
        svc, _fake = _slots_seeded(
            tmp_path, files={"pokemon.srm": FileSyncState(last_sync_hash="before-the-last-play", tracked_save_id=100)}
        )
        _create_save(tmp_path, content=b"played since the last sync")

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, "switch_slot", [42, "save1"]))

        assert message["result"] == {
            "success": False,
            "reason": "pending_uploads",
            "message": "Pending local changes — upload or discard first",
            "files": ["pokemon.srm"],
        }

    async def test_a_migration_over_a_differing_local_save_answers_the_conflicts(self, tmp_path):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        _create_save(tmp_path, content=b"LOCAL" * 10)
        fake.saves[1] = _server_save(save_id=1, filename="pokemon [ts].srm", slot=None)
        fake.set_server_save_content(1, b"SERVER" * 10)

        message = json.loads(
            await _dispatcher_over_saves(svc).dispatch(1, "confirm_slot_choice", [42, "default", True, None, False])
        )

        result = message["result"]
        assert {key: result[key] for key in ("success", "reason", "message", "needs_conflict_resolution")} == {
            "success": False,
            "reason": "local_conflict",
            "message": "A local save differs from the legacy save for slot 'default'",
            "needs_conflict_resolution": True,
        }
        assert set(result) == {"success", "reason", "message", "needs_conflict_resolution", "conflicts"}
        assert [(c["filename"], c["server_save_id"]) for c in result["conflicts"]] == [("pokemon.srm", 1)]

    async def test_a_migration_over_a_local_save_that_cannot_be_read_answers_migration_failed(
        self, tmp_path, monkeypatch
    ):
        svc, fake = make_service(tmp_path)
        _enable_sync_with_device(svc)
        _install_rom(svc, tmp_path)
        local_save = _create_save(tmp_path, content=b"L" * 100)
        fake.saves[1] = _server_save(save_id=1, filename="pokemon [ts].srm", slot=None)
        fake.set_server_save_content(1, b"S" * 100)
        store = svc._save_file_store
        content_hash = store.content_hash

        def unreadable_local_save(path: str) -> str:
            if path == str(local_save):
                raise PermissionError(f"cannot read {path}")
            return content_hash(path)

        monkeypatch.setattr(store, "content_hash", unreadable_local_save)

        message = json.loads(
            await _dispatcher_over_saves(svc).dispatch(1, "confirm_slot_choice", [42, "default", True, None, False])
        )

        assert message["result"] == {
            "success": False,
            "reason": "migration_failed",
            "message": "The saves could not be migrated: a save file on this device could not be read,"
            " or its folder could not be created.",
        }

    @pytest.mark.parametrize("route_name", ["get_slot_delete_info", "delete_slot"])
    async def test_a_romm_error_inspecting_or_deleting_a_slot_answers_classify_errors_message(
        self, tmp_path, route_name
    ):
        svc, fake = _slots_seeded(tmp_path)
        fake.saves[10] = _server_save(save_id=10, filename="pokemon.srm", slot="save1")
        failure = RommServerError("bad gateway", status_code=502)
        if route_name == "delete_slot":

            def failing_delete(_save_ids):
                raise failure

            fake.delete_server_saves = failing_delete  # type: ignore[method-assign]
        else:
            fake.fail_on_next(failure)

        message = json.loads(await _dispatcher_over_saves(svc).dispatch(1, route_name, [42, "save1"]))

        reason, text = classify_error(failure)
        assert message["result"] == {"success": False, "reason": reason, "message": text}


def _dispatcher_over_library(romm_api: FakeRommApi, events: FakeEventSink, home: Path) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose library use cases are the real ``LibraryService`` over a fake."""
    service = LibraryService(
        config=LibraryServiceConfig(
            romm_api=romm_api,
            steam_config=SteamConfigAdapter(user_home=str(home), logger=LOGGER),
            settings={"enabled_platforms": {}, "enabled_collections": {"standard": {}, "smart": {}, "virtual": {}}},
            loop=asyncio.get_running_loop(),
            logger=LOGGER,
            launcher_exe=f"{home}/.local/bin/tender-rom-launcher",
            emit=events.emit,
            clock=FakeClock(),
            uuid_gen=FakeUuidGen(),
            sleeper=FakeSleeper(),
            settings_persister=FakeSettingsPersister(),
            log_debug=lambda msg: None,
            artwork=FakeArtworkManager(),
            uow_factory=FakeUnitOfWorkFactory(FakeUnitOfWork()),
            active_core=FakeActiveCoreResolver(default=(None, None)),
            disc_resolver=FakeDiscResolver(),
            emulator_sources=FakeEmulatorSources(),
            renderer_rss=FakeRendererRss(),
            renderer_gc=FakeRendererGc(),
            conflict_rules=_make_conflict_rules(),
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(sync_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


class TestTheLibraryPreviewFailuresOnTheWire:
    """A library preview that fails ends its progress with an error frame, and the failure reaches the wire as
    ``classify_error``'s answer for a RomM error and as a transport error for anything else.
    """

    async def test_a_romm_error_answers_classify_errors_reason_and_message(self, tmp_path):
        romm_api = FakeRommApi()
        failure = RommConnectionError("connection refused")
        romm_api.list_platforms_side_effect = failure
        events = FakeEventSink()

        message = json.loads(await _dispatcher_over_library(romm_api, events, tmp_path).dispatch(1, "sync_preview", []))

        reason, text = classify_error(failure)
        assert message["type"] == TYPE_REPLY
        assert message["result"] == {"success": False, "reason": reason, "message": text}
        progress = [payload for name, payload in events.events if name == "sync_progress"]
        assert (progress[-1]["stage"], progress[-1]["message"], progress[-1]["running"]) == ("error", text, False)

    async def test_any_other_error_answers_a_transport_error(self, tmp_path):
        romm_api = FakeRommApi()
        romm_api.list_platforms_side_effect = RuntimeError("something broke")
        events = FakeEventSink()

        message = json.loads(await _dispatcher_over_library(romm_api, events, tmp_path).dispatch(1, "sync_preview", []))

        assert message["type"] == TYPE_ERROR
        assert message["reason"] == REASON_BACKEND_EXCEPTION
        assert message["message"] == "RuntimeError: something broke"
        assert "result" not in message
        progress = [payload for name, payload in events.events if name == "sync_progress"]
        assert (progress[-1]["stage"], progress[-1]["message"], progress[-1]["running"]) == (
            "error",
            "something broke",
            False,
        )


def _dispatcher_over_cleanup(uow_factory: Any, events: FakeEventSink) -> tuple[CallDispatcher, PruneService]:
    """The real dispatcher over ``Endpoints`` whose cleanup use cases are the real ``PruneService``.

    Only the preview and the action report are reached, so what a run would mutate through stays a mock.
    """
    prune_conflicts = _make_prune_conflicts()
    service = PruneService(
        config=PruneServiceConfig(
            loop=asyncio.get_running_loop(),
            logger=LOGGER,
            clock=FakeClock(),
            uuid_gen=FakeUuidGen(),
            emit=events.emit,
            uow_factory=uow_factory,
            romm_api=MagicMock(),
            recovery_store=MagicMock(),
            prune_artifacts=MagicMock(),
            steam_recovery=MagicMock(),
            retrodeck_folders=FakeRetroDeckFolders(),
            save_coordinator=MagicMock(),
            active_downloads=set,
            drift_probe=MagicMock(),
            remove_installed_files=MagicMock(),
            switch_version=MagicMock(),
            settings={},
            run_claim=prune_conflicts,
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(prune_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER), service


_A_PREVIEW_PAGE = {"scope": "bulk", "rom_id": None, "preview_id": None, "offset": 0, "limit": 50}


class TestTheCleanupRefusalsOnTheWire:
    """The cleanup's refusals answer their reason and message — the two the panel branches on included — and a fault
    in its preview is a transport error.
    """

    async def test_a_stale_action_answers_its_reason_and_message(self):
        dispatcher, _ = _dispatcher_over_cleanup(FakeUnitOfWorkFactory(FakeUnitOfWork()), FakeEventSink())
        claim = {"phase": "claim", "run_id": "run-1", "action_token": "token-1"}

        message = json.loads(await dispatcher.dispatch(1, "report_prune_action", [claim]))

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "stale_action",
            "message": "This cleanup action token is no longer active.",
        }

    async def test_a_binding_that_changed_before_the_claim_answers_local_state_changed(self):
        events = FakeEventSink()
        dispatcher, service = _dispatcher_over_cleanup(FakeUnitOfWorkFactory(FakeUnitOfWork()), events)
        app_id = 0x80000001
        # The run's own requester, so the action is pending exactly as a run leaves it; the empty
        # database binds no shortcut to it, so the claim's binding check fails.
        requested = asyncio.create_task(
            service._request_action("run-1", "remove_shortcut", {"app_id": app_id}, 1, None, {1})
        )
        async with asyncio.timeout(5):
            while not events.events:
                await asyncio.sleep(0)
        claim = {
            "phase": "claim",
            "run_id": "run-1",
            "action_token": events.events[-1][1]["action_token"],
            "action": "remove_shortcut",
            "app_id": app_id,
            "target_rom_id": None,
        }

        message = json.loads(await dispatcher.dispatch(1, "report_prune_action", [claim]))

        requested.cancel()
        with pytest.raises(asyncio.CancelledError):
            await requested
        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "local_state_changed",
            "message": "The shortcut binding changed before the Steam action.",
        }

    async def test_a_stale_preview_answers_its_reason_and_message(self):
        dispatcher, _ = _dispatcher_over_cleanup(FakeUnitOfWorkFactory(FakeUnitOfWork()), FakeEventSink())
        page = {**_A_PREVIEW_PAGE, "preview_id": "a-preview-nobody-built"}

        message = json.loads(await dispatcher.dispatch(1, "get_prune_preview", [page]))

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "stale_preview",
            "message": "This cleanup preview is stale. Scan again before confirming.",
        }

    async def test_a_fault_in_the_preview_answers_a_transport_error(self):
        def unreadable_database() -> FakeUnitOfWork:
            raise RuntimeError("database unavailable")

        dispatcher, _ = _dispatcher_over_cleanup(unreadable_database, FakeEventSink())

        message = json.loads(await dispatcher.dispatch(1, "get_prune_preview", [_A_PREVIEW_PAGE]))

        assert message["type"] == TYPE_ERROR
        assert message["reason"] == REASON_BACKEND_EXCEPTION
        assert message["message"] == "RuntimeError: database unavailable"
        assert "result" not in message


def _dispatcher_over_firmware(
    romm_api: FakeRommApi, store: FakeFirmwareFileStore, uow: FakeUnitOfWork
) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose firmware use cases are the real ``FirmwareService`` over fakes."""
    service = FirmwareService(
        config=FirmwareServiceConfig(
            romm_api=romm_api,
            loop=asyncio.get_running_loop(),
            logger=LOGGER,
            clock=FakeClock(),
            firmware_file_store=store,
            firmware_resolver=FakeFirmwareResolver(),
            platform_firmware_resolver=FakeFirmwareResolver(),
            retrodeck_folders=FakeRetroDeckFolders(bios="/retrodeck/bios"),
            core_info=FakeCoreInfoProvider(),
            resolve_system=lambda platform_slug, platform_fs_slug=None: platform_slug,
            platform_core_reader=FakePlatformCoreReader(),
            uow_factory=FakeUnitOfWorkFactory(uow),
            conflict_rules=_make_conflict_rules(),
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(firmware_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


class TestTheFirmwareAnswersOnTheWire:
    """The firmware downloads' and deletes' refusals, partial results and RomM errors, as the wire carries them."""

    async def test_a_partial_delete_answers_its_count(self):
        uow = FakeUnitOfWork()
        store = FakeFirmwareFileStore(
            {"/retrodeck/bios/dc_boot.bin": b"boot", "/retrodeck/bios/dc_flash.bin": b"flash"}
        )
        store.remove_failures.add("/retrodeck/bios/dc_flash.bin")
        for name in ("dc_boot.bin", "dc_flash.bin"):
            with uow:
                uow.bios_files.save(
                    BiosFile.mark_downloaded(
                        platform_slug="dc",
                        file_name=name,
                        file_path=f"/retrodeck/bios/{name}",
                        downloaded_at="2026-01-01T00:00:00+00:00",
                        firmware_id=None,
                    )
                )

        dispatcher = _dispatcher_over_firmware(FakeRommApi(), store, uow)
        message = json.loads(await dispatcher.dispatch(1, "delete_platform_bios", ["dc"]))

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "delete_incomplete",
            "message": "Deleted 1 file(s), 1 error(s)",
            "deleted_count": 1,
        }
        assert list(store.files) == ["/retrodeck/bios/dc_flash.bin"]

    async def test_a_name_the_library_does_not_hold_answers_without_a_count(self):
        romm_api = FakeRommApi()
        romm_api.firmware_files = [
            {"id": 1, "file_name": "dc_boot.bin", "file_path": "bios/dc/dc_boot.bin", "md5_hash": ""}
        ]
        dispatcher = _dispatcher_over_firmware(romm_api, FakeFirmwareFileStore(), FakeUnitOfWork())

        message = json.loads(await dispatcher.dispatch(1, "download_platform_firmware_file", ["n64", "dc_boot.bin"]))

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "not_in_library",
            "message": "dc_boot.bin is not in your RomM library for n64",
        }

    async def test_a_romm_error_reading_the_library_answers_its_classified_reason(self):
        romm_api = FakeRommApi()
        romm_api.list_firmware_side_effect = RommConnectionError("connection refused")
        dispatcher = _dispatcher_over_firmware(romm_api, FakeFirmwareFileStore(), FakeUnitOfWork())

        message = json.loads(await dispatcher.dispatch(1, "download_all_firmware", ["dc"]))

        reason, text = classify_error(RommConnectionError("connection refused"))
        assert message["type"] == TYPE_REPLY
        assert message["result"] == {"success": False, "reason": reason, "message": text}


class _StoredRelease:
    """The release check's answer: one newer release, as the last check stored it."""

    async def last_seen_release(self) -> LatestRelease | None:
        url = "https://github.test/releases/download/tender-v1.1.0/romm-tender-1.1.0.tar.gz"
        return LatestRelease(
            version="1.1.0", tarball=ReleaseTarball(url=url, digest="0" * 64, checksum_url=f"{url}.sha256")
        )


async def _acknowledge_nothing(rolled_back_at: object) -> dict[str, Any]:
    return {"success": True}


def _dispatcher_over_update_install(tmp_path: Path, steam: FakeSteamInterface) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose update install is the real ``UpdateInstallService`` over fakes."""
    service = UpdateInstallService(
        config=UpdateInstallServiceConfig(
            releases=_StoredRelease(),
            current_version="1.0.0",
            installed_program=True,
            steam=steam,
            library_sync_in_flight=lambda: False,
            rom_downloads_in_flight=set,
            download_queue=lambda: {"downloads": []},
            save_sync_in_flight=lambda: False,
            firmware_downloads_in_flight=lambda: False,
            save_directory_move_in_flight=lambda: False,
            cleanup_running=lambda: False,
            migration_running=lambda: False,
            held_claims=tuple,
            read_update_failure=lambda: None,
            attempts=UpdateAttemptFileAdapter(state_dir=str(tmp_path / "state"), log_debug=lambda msg: None),
            download_asset=FakeReleaseDownload(),
            staging=UpdateStagingAdapter(directory=str(tmp_path / "cache" / "update")),
            units=FakeTransientUnits(),
            installer_environment=(),
            uow_factory=FakeUnitOfWorkFactory(),
            acknowledge_failure_toast=_acknowledge_nothing,
            emit=FakeEventSink().emit,
            clock=FakeClock(),
            sleeper=FakeSleeper(),
            loop=asyncio.get_running_loop(),
            logger=LOGGER,
            log_debug=lambda msg: None,
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(update_install_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


def _dispatcher_over_update_output(journal: FakeJournal, started_at: str | None) -> CallDispatcher:
    """The real dispatcher over ``Endpoints`` whose installer's output is the real ``UpdateOutputService``."""
    service = UpdateOutputService(
        config=UpdateOutputServiceConfig(
            current_version="1.0.0",
            read_update_failure=lambda: None,
            failed_installer_started_at=lambda: started_at,
            journal=journal,
            loop=asyncio.get_running_loop(),
            logger=LOGGER,
        )
    )
    endpoints = Endpoints(_make_application(_make_services_bundle(update_output_service=service)), HostStatus())
    return CallDispatcher(endpoints, LOGGER)


class TestTheUpdaterRefusalsOnTheWire:
    """The install press's and the installer's output window's refusals, as the wire carries them."""

    async def test_a_press_that_has_to_wait_answers_every_reason_it_waits_for(self, tmp_path):
        dispatcher = _dispatcher_over_update_install(tmp_path, FakeSteamInterface(apps=("A Game",)))

        message = json.loads(await dispatcher.dispatch(1, "install_update", ["1.1.0"]))

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "update_waiting",
            "message": "Something that an update would interrupt is still under way",
            "wait_reasons": [{"reason": "app_running", "apps": ["A Game"]}],
        }

    async def test_no_failed_update_to_show_answers_not_found(self):
        dispatcher = _dispatcher_over_update_output(FakeJournal(), started_at=None)

        message = json.loads(await dispatcher.dispatch(1, "get_update_output", [None]))

        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "not_found",
            "message": "No failed update to show the output of",
        }

    async def test_a_journal_that_cannot_be_read_answers_a_fixed_message_without_its_error(self):
        unreadable = FakeJournal(raises=OSError("journalctl: index.js?token=s3cr3t-admission-token"))
        dispatcher = _dispatcher_over_update_output(unreadable, started_at="2026-09-30T11:03:20Z")

        raw = await dispatcher.dispatch(1, "get_update_output", [None])

        message = json.loads(raw)
        assert message["type"] == TYPE_REPLY
        assert message["result"] == {
            "success": False,
            "reason": "journal_unreadable",
            "message": "The journal could not be read",
        }
        assert "s3cr3t-admission-token" not in raw
