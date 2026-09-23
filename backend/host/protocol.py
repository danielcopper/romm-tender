"""The wire protocol between the panel and this backend — messages and refusals.

Contract: what a message on the socket looks like, and the vocabulary a refusal
is stated in. One place, because both ends read it and a second spelling of a
reason is indistinguishable from a new one.

Every message carries its kind as a readable word in ``type``. Without it, each
later addition would be a protocol change and a receiver would have to guess the
kind from which keys happen to be present::

    call   {type, id, method, args}
    reply  {type, id, result}
    error  {type, id, reason, message, traceback?}
    event  {type, name, payload}

``args`` are positional. A named-argument form would have to agree with every
callable's parameter names, which are an implementation detail on this side and
would become wire contract the moment the other side spelled one.

**A transport reason is not a callable's failure.** ``reason`` on an ``error``
message names something that went wrong carrying the call — the method does not
exist, the answer is too large to send, the method raised. A callable's own
failure is a perfectly successful transport and arrives in ``result``, in the
``{success, reason, message}`` shape ``scripts/check_failure_shape.py`` guards.
Reading one as the other shows a user a sentence about their game where a
programming error stands.
"""

from __future__ import annotations

import json
from typing import Any

# Message kinds, both directions.
TYPE_CALL = "call"
TYPE_REPLY = "reply"
TYPE_ERROR = "error"
TYPE_EVENT = "event"

MESSAGE_TYPES = frozenset({TYPE_CALL, TYPE_REPLY, TYPE_ERROR, TYPE_EVENT})

# Transport reasons — the whole vocabulary of ``error.reason``. These name the
# carriage, never the cargo.
#
# ``CONNECTION_LOST`` is the one no backend ever sends: it is what the caller's
# own pending register answers with when the socket goes before the reply comes
# back. It is named here anyway, because the alternative is that the other end
# invents a second spelling of it and the two vocabularies drift apart from the
# first day.
REASON_METHOD_UNKNOWN = "method_unknown"
REASON_PAYLOAD_TOO_LARGE = "payload_too_large"
REASON_BACKEND_EXCEPTION = "backend_exception"
REASON_MALFORMED_MESSAGE = "malformed_message"
REASON_CONNECTION_LOST = "connection_lost"

TRANSPORT_REASONS = frozenset(
    {
        REASON_METHOD_UNKNOWN,
        REASON_PAYLOAD_TOO_LARGE,
        REASON_BACKEND_EXCEPTION,
        REASON_MALFORMED_MESSAGE,
        REASON_CONNECTION_LOST,
    }
)


def decode_message(text: str) -> dict[str, Any]:
    """Decode one text frame into a message mapping.

    Raises :class:`ValueError` when the frame is not JSON, or is JSON that is
    not an object — a bare array or number carries no ``type`` and so cannot be
    any message this protocol defines.
    """
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError(f"a message is a JSON object, got {type(decoded).__name__}")
    return decoded


def encode_reply(call_id: Any, result: Any) -> str:
    """Encode a successful answer to the call numbered *call_id*."""
    return json.dumps({"type": TYPE_REPLY, "id": call_id, "result": result})


def encode_error(call_id: Any, reason: str, message: str, traceback: str | None = None) -> str:
    """Encode a transport failure for the call numbered *call_id*.

    *traceback* is carried only when there is one — a backend exception has a
    stack worth reading, a refused method name does not — so the key's presence
    itself says which kind of failure this was.
    """
    payload: dict[str, Any] = {"type": TYPE_ERROR, "id": call_id, "reason": reason, "message": message}
    if traceback is not None:
        payload["traceback"] = traceback
    return json.dumps(payload)


def encode_event(name: str, payload: Any) -> str:
    """Encode a named event carrying *payload*."""
    return json.dumps({"type": TYPE_EVENT, "name": name, "payload": payload})
