"""Extra HTTP headers the user configures for an authenticating reverse proxy.

Contract: what a configured header may be, what a whole configured list may be,
and how the persisted list is read back for sending. Pure compute — a refusal is
returned as a code the caller words, and no refusal, repr or return value here
ever carries a header VALUE: the values are proxy credentials.

The reserved set below is the half that cannot be relaxed. A configured header
that took one of those names would not add anything to a request, it would
REPLACE what the transport put there — the RomM bearer, the body's declared
type, or the ``Host`` the connection was opened against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

# RFC 7230 field-name: a token — the listed punctuation plus digits and letters.
# The two exclusions that matter are the space and the colon: either one ends the
# field name early, so a name carrying them is a second header, not a name.
_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# Header names the transport sets itself, matched case-insensitively.
# ``host`` is here for a reason that is invisible at the call site: nothing in
# the plugin writes it, but ``http.client._send_request`` skips its own derived
# ``Host`` header when the caller supplied one, so a configured ``Host`` silently
# retargets every request's virtual host.
RESERVED_NAMES = frozenset(
    {
        "authorization",
        "user-agent",
        "content-type",
        "content-length",
        "host",
        "range",
        "if-none-match",
        "if-modified-since",
    }
)

# What an entry says to do with its value. ``keep`` exists because a stored value
# is never sent to the frontend, so a row the user did not touch has no value to
# send back — it names the stored one instead.
VALUE_ACTIONS = ("set", "keep")


class HeaderProblem(StrEnum):
    """Why a configured header list was refused. The caller words each one."""

    NOT_A_LIST = "headers_not_a_list"
    MALFORMED_ENTRY = "malformed_header_entry"
    UNKNOWN_VALUE_ACTION = "unknown_value_action"
    INVALID_NAME = "invalid_header_name"
    RESERVED_NAME = "reserved_header_name"
    AUTHORIZATION_RESERVED = "authorization_reserved"
    DUPLICATE_NAME = "duplicate_header_name"
    EMPTY_VALUE = "empty_header_value"
    UNSAFE_VALUE = "unsafe_header_value"
    PADDED_VALUE = "padded_header_value"
    UNENCODABLE_VALUE = "unencodable_header_value"
    UNEXPECTED_VALUE = "unexpected_header_value"
    NO_STORED_VALUE = "no_stored_header_value"


@dataclass(frozen=True, repr=False)
class CustomHeader:
    """One configured name/value pair, ready to be attached to a request."""

    name: str
    value: str

    def __repr__(self) -> str:
        """Name only — the value is a proxy credential and never reaches a log line."""
        return f"CustomHeader(name={self.name!r})"


@dataclass(frozen=True)
class HeaderRefusal:
    """A refused list: why, and which header name it was about where one was readable."""

    problem: HeaderProblem
    name: str | None = None


def value_problem(value: str) -> HeaderProblem | None:
    """Why *value* may not be sent as a header value, or ``None`` when it may be.

    The control-character check runs first and is the security-relevant one: a
    ``\\r\\n`` in a value is a second header injected into every outgoing
    request. Surrounding whitespace is refused rather than trimmed — a value is a
    credential, and silently changing one is how a user ends up debugging a
    rejection the plugin caused.
    """
    if value == "":
        return HeaderProblem.EMPTY_VALUE
    if any(ch < " " or ch == "\x7f" for ch in value):
        return HeaderProblem.UNSAFE_VALUE
    if value != value.strip():
        return HeaderProblem.PADDED_VALUE
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        # ``http.client.putheader`` encodes a str value as latin-1, so anything
        # outside it raises at send time — on every request, long after the user
        # left the modal.
        return HeaderProblem.UNENCODABLE_VALUE
    return None


def _name_refusal(name: str) -> HeaderRefusal | None:
    """Why *name* may not be a configured header name, or ``None`` when it may."""
    if not _TOKEN.match(name):
        return HeaderRefusal(HeaderProblem.INVALID_NAME, name)
    lowered = name.lower()
    if lowered == "authorization":
        return HeaderRefusal(HeaderProblem.AUTHORIZATION_RESERVED, name)
    if lowered in RESERVED_NAMES:
        return HeaderRefusal(HeaderProblem.RESERVED_NAME, name)
    return None


def _resolve_entry(entry: object, stored_values: dict[str, str]) -> CustomHeader | HeaderRefusal:
    """Resolve one wire entry against the values already stored, keyed lowercase."""
    if not isinstance(entry, dict):
        return HeaderRefusal(HeaderProblem.MALFORMED_ENTRY)
    raw_name: Any = entry.get("name")
    if not isinstance(raw_name, str):
        return HeaderRefusal(HeaderProblem.MALFORMED_ENTRY)
    # Surrounding whitespace in a NAME is never meaningful (unlike in a value),
    # so it is normalised away instead of refused.
    name = raw_name.strip()
    refusal = _name_refusal(name)
    if refusal is not None:
        return refusal

    action: Any = entry.get("value_action")
    if action not in VALUE_ACTIONS:
        return HeaderRefusal(HeaderProblem.UNKNOWN_VALUE_ACTION, name)
    if action == "keep":
        if "value" in entry:
            return HeaderRefusal(HeaderProblem.UNEXPECTED_VALUE, name)
        stored_value = stored_values.get(name.lower())
        if stored_value is None:
            return HeaderRefusal(HeaderProblem.NO_STORED_VALUE, name)
        return CustomHeader(name=name, value=stored_value)

    value: Any = entry.get("value")
    if not isinstance(value, str):
        return HeaderRefusal(HeaderProblem.MALFORMED_ENTRY, name)
    problem = value_problem(value)
    if problem is not None:
        return HeaderRefusal(problem, name)
    return CustomHeader(name=name, value=value)


def resolve_custom_headers(
    entries: object,
    stored: Sequence[CustomHeader],
) -> tuple[CustomHeader, ...] | HeaderRefusal:
    """Resolve a whole wire list into the headers to persist, or refuse it.

    *entries* arrives from the untrusted frontend wire, so every shape is
    checked: a non-list, a non-dict entry, a missing or non-string name, and an
    unrecognised ``value_action`` are all refusals rather than skipped rows.
    Each entry is ``{"name": str, "value_action": "set" | "keep"}`` plus a
    ``value`` exactly when the action is ``"set"``; ``"keep"`` reuses the value
    *stored* holds under that name and fails when there is none, so a row can
    never quietly become an empty header.

    The whole list is refused on the first problem — a partial write would leave
    the user with a configuration they never asked for. Order is preserved, and
    the returned pairs replace the stored list wholesale.
    """
    if not isinstance(entries, list):
        return HeaderRefusal(HeaderProblem.NOT_A_LIST)
    stored_values = {header.name.lower(): header.value for header in stored}
    resolved: list[CustomHeader] = []
    seen: set[str] = set()
    for entry in entries:
        outcome = _resolve_entry(entry, stored_values)
        if isinstance(outcome, HeaderRefusal):
            return outcome
        lowered = outcome.name.lower()
        if lowered in seen:
            return HeaderRefusal(HeaderProblem.DUPLICATE_NAME, outcome.name)
        seen.add(lowered)
        resolved.append(outcome)
    return tuple(resolved)


def stored_custom_headers(stored: object) -> tuple[CustomHeader, ...]:
    """Read the persisted list back as the pairs a request may carry.

    The single reading of ``settings.json``'s ``romm_custom_headers`` — the
    transport attaches what this returns and the settings read reports its names.
    Anything :func:`resolve_custom_headers` would have refused is skipped rather
    than sent: the only way such an entry reaches the file is a hand edit, and a
    header the plugin cannot vouch for must never ride on a request. That is the
    second of the two defences against a reserved name — the first is that the
    transport attaches these before its own headers, so even a name that got
    through could not displace one.
    """
    if not isinstance(stored, list):
        return ()
    headers: list[CustomHeader] = []
    seen: set[str] = set()
    for entry in stored:
        if not isinstance(entry, dict):
            continue
        name: Any = entry.get("name")
        value: Any = entry.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        name = name.strip()
        lowered = name.lower()
        if _name_refusal(name) is not None or lowered in seen or value_problem(value) is not None:
            continue
        seen.add(lowered)
        headers.append(CustomHeader(name=name, value=value))
    return tuple(headers)
