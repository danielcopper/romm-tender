"""Turning a call message into an answer — the reachable surface and its limit.

Contract: which methods a caller may reach, and what comes back when one is
called. Owns the name resolution, the exception boundary and the payload cap.
It holds the plugin object and nothing else about the connection, so a call can
be dispatched and judged without a socket in sight.

**Reachable is exactly the public async surface of the plugin object** — an
``async def`` on the class with no leading underscore, which is the same set
``scripts/check_callable_manifest.py`` derives from the source. The two are
asserted equal by a test rather than kept equal by care: the gate reads the
file and this reads the loaded class, and a method whose reachability the two
disagree about is either a callable the panel cannot reach or a method nobody
meant to expose.
"""

from __future__ import annotations

import inspect
import traceback
from typing import TYPE_CHECKING, Any

from host.protocol import (
    REASON_BACKEND_EXCEPTION,
    REASON_METHOD_UNKNOWN,
    REASON_PAYLOAD_TOO_LARGE,
    encode_error,
    encode_reply,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Sequence

# The largest answer this host will put on the wire, in bytes of encoded JSON.
#
# It is a cap on ONE call's answer and is deliberately not the connection's
# frame cap. The two exist for different reasons and must fail differently: a
# frame above the connection's cap is a protocol violation and closes the
# socket, which rejects every other call in flight, while an answer that came
# out too large is one call's problem and must not cost the others anything.
#
# The number is chosen, not inherited. The reference library's largest cover is
# 5,869,834 bytes, which base64 turns into 7,826,448 (7.46 MiB); a 4 MiB cap
# would have refused that image with nothing to show for it. Decky's own 1 MiB
# limit belongs to a bridge that is not in this path at all.
DEFAULT_PAYLOAD_LIMIT = 12 * 1024 * 1024


def reachable_methods(target: object) -> dict[str, Any]:
    """Map every reachable method name on *target* to its bound method.

    Reachability is read off the **class**, not the instance, and only off the
    classes in its own hierarchy above ``object``: an instance attribute that
    happens to hold a coroutine function is state, not surface, and exposing it
    would mean a name became reachable because of something a test poked in.
    """
    names: dict[str, Any] = {}
    for klass in reversed(type(target).__mro__):
        if klass is object:
            continue
        for name, value in vars(klass).items():
            if name.startswith("_") or not inspect.iscoroutinefunction(value):
                continue
            names[name] = getattr(target, name)
    return names


class CallDispatcher:
    """Resolves a call onto the plugin object and answers with one wire message."""

    def __init__(self, target: object, logger: logging.Logger, payload_limit: int = DEFAULT_PAYLOAD_LIMIT) -> None:
        self._methods = reachable_methods(target)
        self._logger = logger
        self._payload_limit = payload_limit

    @property
    def method_names(self) -> frozenset[str]:
        """Every name a caller may reach."""
        return frozenset(self._methods)

    async def dispatch(self, call_id: Any, method: str, args: Sequence[Any]) -> str:
        """Run *method* with *args* and return the message text to send back.

        Never raises for anything the call itself did: an unknown name, a raised
        exception and an oversized answer each come back as an ``error`` message
        for this call alone, leaving the connection and every other call in
        flight untouched. ``CancelledError`` is the one thing that passes
        through — it means this call's connection is gone, and there is nobody
        left to answer.
        """
        bound = self._methods.get(method)
        if bound is None:
            self._logger.warning(f"host: call {call_id} names no reachable method: {method!r}")
            return encode_error(call_id, REASON_METHOD_UNKNOWN, f"no such method: {method}")

        try:
            result = await bound(*args)
        except Exception as exc:
            stack = traceback.format_exc()
            self._logger.error(f"host: call {call_id} to {method} raised {type(exc).__name__}: {exc}\n{stack}")
            return encode_error(call_id, REASON_BACKEND_EXCEPTION, f"{type(exc).__name__}: {exc}", stack)

        return self._encoded_reply(call_id, method, result)

    def _encoded_reply(self, call_id: Any, method: str, result: Any) -> str:
        """Encode *result*, or refuse it for being too large or unserialisable.

        The cap is judged on the encoded bytes, because that is what the socket
        would carry — a structure's size in memory says nothing about it.
        """
        try:
            encoded = encode_reply(call_id, result)
        except (TypeError, ValueError) as exc:
            self._logger.error(f"host: the answer from {method} is not encodable: {exc}")
            return encode_error(call_id, REASON_BACKEND_EXCEPTION, f"answer is not JSON-encodable: {exc}")

        size = len(encoded.encode("utf-8"))
        if size > self._payload_limit:
            self._logger.warning(f"host: the answer from {method} is {size} bytes, over the {self._payload_limit} cap")
            return encode_error(
                call_id,
                REASON_PAYLOAD_TOO_LARGE,
                f"answer is {size} bytes, over the {self._payload_limit}-byte limit",
            )
        return encoded
