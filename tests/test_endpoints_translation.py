"""What every endpoint answers when the use case behind it refuses — and what it leaves to the host.

Each call goes through the real ``CallDispatcher`` over the real ``Endpoints``,
so the transport half is the one the panel meets: a refusal arrives as a reply
carrying ``{success: False, reason, message}``, a bug as the host's
``backend_exception`` error. The services are stand-ins whose every method
raises what a case hands them, which puts the exception exactly where a use
case would raise it.
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
from _factories import _make_application, _make_services_bundle
from bootstrap import ServicesBundle
from fakes.fake_game_process_control import DEFAULT_LAUNCH_PATH, FakeGameProcessControlAdapter
from fakes.fake_rom_launch_path import FakeRomLaunchPathReader

from domain.refusal import DomainRefused
from host import CallDispatcher, HostStatus
from host.dispatch import route_names
from host.protocol import REASON_BACKEND_EXCEPTION, TYPE_ERROR, TYPE_REPLY
from lib.errors import (
    PairingCodeInvalidError,
    Refused,
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
from services.game_process import GameProcessService, GameProcessServiceConfig

_MAIN_PY = Path(__file__).resolve().parents[1] / "backend" / "main.py"

LOGGER = logging.getLogger("test_endpoints_translation")

ROUTES = sorted(route_names(Endpoints))

# One endpoint of each kind, for the cases that are about the answer rather than the surface.
_A_SYNC_ROUTE = "fix_retroarch_input_driver"
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
        class SyncActive(Refused):
            reason = "sync_active"

            def __init__(self, message: str) -> None:
                super().__init__(self.reason, message)

        message = await _call(_dispatcher_raising(SyncActive("A sync is running.")), _AN_ASYNC_ROUTE)

        assert message["result"] == {"success": False, "reason": "sync_active", "message": "A sync is running."}

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
        assert hasattr(getattr(Endpoints, route_name), "__wrapped__")


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
    """The first service that raises its refusals answers the panel exactly as it did when it returned them."""

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
