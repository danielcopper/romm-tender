"""What a caller can reach, and what comes back when it does.

The set a caller can reach is asserted EQUAL to the set the callable-manifest
gate derives. The two read different things — the gate parses ``main.py``, the
dispatcher inspects the loaded class — so a name they disagree about is either a
callable the panel cannot reach or a method nobody meant to expose, and neither
shows up anywhere else.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

from host.dispatch import CallDispatcher, reachable_methods
from host.protocol import (
    REASON_BACKEND_EXCEPTION,
    REASON_METHOD_UNKNOWN,
    REASON_PAYLOAD_TOO_LARGE,
    TYPE_ERROR,
    TYPE_REPLY,
)
from tests.host.conftest import FakePlugin

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE_PATH = _REPO_ROOT / "scripts" / "check_callable_manifest.py"
_MAIN_PY = _REPO_ROOT / "backend" / "main.py"

LOGGER = logging.getLogger("test_dispatch")


def _load_gate():
    """Load the gate script — ``scripts/`` is not importable."""
    spec = importlib.util.spec_from_file_location("check_callable_manifest_for_host", _GATE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dispatcher():
    return CallDispatcher(FakePlugin(), LOGGER)


class TestTheReachableSurface:
    def test_the_dispatcher_reaches_exactly_what_the_gate_derives(self):
        """Two different readings of one surface, held to each other."""
        from main import Plugin

        gate = _load_gate()

        assert set(reachable_methods(Plugin())) == set(gate.parse_backend_callables(_MAIN_PY))

    def test_a_public_coroutine_method_is_reachable(self, dispatcher):
        assert "echo" in dispatcher.method_names

    def test_an_underscored_method_is_not(self, dispatcher):
        assert "_private" not in dispatcher.method_names

    def test_a_synchronous_method_is_not(self, dispatcher):
        assert "synchronous" not in dispatcher.method_names

    def test_an_instance_attribute_holding_a_coroutine_function_is_not(self):
        """Reachability is a property of the class, never of what a test poked in."""
        plugin = FakePlugin()

        async def smuggled() -> None: ...

        plugin.smuggled = smuggled  # type: ignore[attr-defined]

        assert "smuggled" not in CallDispatcher(plugin, LOGGER).method_names

    def test_inherited_public_coroutines_are_reachable(self):
        class Extended(FakePlugin):
            async def extra(self) -> str:
                return "extra"

        assert "extra" in CallDispatcher(Extended(), LOGGER).method_names
        assert "echo" in CallDispatcher(Extended(), LOGGER).method_names

    def test_nothing_from_object_is_reachable(self, dispatcher):
        assert not any(name.startswith("__") for name in dispatcher.method_names)


class TestDispatch:
    async def test_it_answers_a_reply_carrying_the_call_id(self, dispatcher):
        answer = json.loads(await dispatcher.dispatch(42, "echo", ["hi"]))

        assert answer == {"type": TYPE_REPLY, "id": 42, "result": {"echo": "hi"}}

    async def test_a_string_call_id_comes_back_unchanged(self, dispatcher):
        """The id is the caller's; nothing here interprets it."""
        answer = json.loads(await dispatcher.dispatch("call-1", "no_arguments", []))

        assert answer["id"] == "call-1"

    async def test_an_unknown_method_answers_with_the_transport_reason(self, dispatcher):
        answer = json.loads(await dispatcher.dispatch(1, "nope", []))

        assert (answer["type"], answer["reason"]) == (TYPE_ERROR, REASON_METHOD_UNKNOWN)

    async def test_an_unknown_method_carries_no_traceback(self, dispatcher):
        """The key's presence is what tells the two kinds of failure apart."""
        assert "traceback" not in json.loads(await dispatcher.dispatch(1, "nope", []))

    async def test_a_raising_method_answers_with_a_traceback(self, dispatcher):
        answer = json.loads(await dispatcher.dispatch(1, "boom", []))

        assert answer["reason"] == REASON_BACKEND_EXCEPTION
        assert "the backend broke" in answer["traceback"]

    async def test_wrong_arity_is_a_backend_exception(self, dispatcher):
        answer = json.loads(await dispatcher.dispatch(1, "echo", []))

        assert answer["reason"] == REASON_BACKEND_EXCEPTION
        assert "TypeError" in answer["message"]

    async def test_cancellation_passes_through(self, dispatcher):
        """Its connection is gone, so there is nobody to answer."""
        import asyncio

        task = asyncio.ensure_future(dispatcher.dispatch(1, "never_returns", []))
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


class TestThePayloadCap:
    async def test_an_answer_at_the_cap_is_sent(self):
        dispatcher = CallDispatcher(FakePlugin(), LOGGER, payload_limit=200)
        answer = json.loads(await dispatcher.dispatch(1, "blob", [100]))

        assert answer["type"] == TYPE_REPLY

    async def test_an_answer_over_the_cap_is_refused(self):
        dispatcher = CallDispatcher(FakePlugin(), LOGGER, payload_limit=50)
        answer = json.loads(await dispatcher.dispatch(1, "blob", [100]))

        assert answer["reason"] == REASON_PAYLOAD_TOO_LARGE

    async def test_the_refusal_names_the_size_and_the_limit(self):
        dispatcher = CallDispatcher(FakePlugin(), LOGGER, payload_limit=50)
        answer = json.loads(await dispatcher.dispatch(1, "blob", [100]))

        assert "50" in answer["message"]

    async def test_the_cap_is_judged_on_encoded_bytes(self):
        """A character can encode to more than one byte; the socket carries bytes."""
        dispatcher = CallDispatcher(FakePlugin(), LOGGER, payload_limit=40)

        assert json.loads(await dispatcher.dispatch(1, "echo", ["ä" * 20]))["reason"] == REASON_PAYLOAD_TOO_LARGE

    async def test_an_unencodable_answer_is_refused_rather_than_raised(self, dispatcher):
        answer = json.loads(await dispatcher.dispatch(1, "unserialisable", []))

        assert answer["reason"] == REASON_BACKEND_EXCEPTION
        assert "not JSON-encodable" in answer["message"]
