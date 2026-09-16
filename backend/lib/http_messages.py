"""HTTP request heads and response heads — the pure half of the host's HTTP.

Contract: the bytes-to-structure half of HTTP/1.1, for the two things this host
serves — a ``GET`` and a WebSocket upgrade. No socket, no routing, no policy.
What a request MEANS is the host's business; what a request IS, is here, so it
can be pinned against a table of byte strings.

This is deliberately not a general HTTP implementation. It parses a request
*head* and builds a response *head*; there is no body parsing, no chunked
transfer, no content negotiation, because the host answers no request that has a
body and sends no response that needs negotiating. A parser that did more would
carry attack surface nothing here asks for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The end of a request head. A lone LF is tolerated inside the head (some
# hand-written clients and every ``printf`` in a test send it), but the
# terminator itself is the canonical CRLFCRLF a reader scans for.
HEAD_TERMINATOR = b"\r\n\r\n"

# A guard on the head itself, separate from any payload cap: a peer that never
# sends a blank line would otherwise grow this process's buffer without ever
# making a request. 64 KiB is far above any legitimate head and far below
# anything that matters.
MAX_HEAD_BYTES = 64 * 1024

# Reason phrases for the statuses this host answers with. A phrase is
# decoration — every client reads the code — but an absent one makes a raw
# transcript unreadable, and this host's refusals are meant to be read in a log.
_REASON_PHRASES = {
    101: "Switching Protocols",
    200: "OK",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Content Too Large",
    421: "Misdirected Request",
    426: "Upgrade Required",
    500: "Internal Server Error",
}


class HttpParseError(ValueError):
    """Raised when bytes cannot be a valid HTTP request head."""


@dataclass(frozen=True)
class RequestHead:
    """A parsed request head: what was asked for, and what was said about it.

    ``headers`` is keyed by **lower-cased** field name, because HTTP field names
    are case-insensitive and a host that compared them as written would accept
    ``Origin`` and miss ``origin``. A field sent more than once is folded into
    one comma-joined value, which is what RFC 9110 §5.3 says it means.
    """

    method: str
    target: str
    version: str
    headers: Mapping[str, str]

    def header(self, name: str, default: str = "") -> str:
        """Return the value of *name*, case-insensitively, or *default*."""
        return self.headers.get(name.lower(), default)

    @property
    def path(self) -> str:
        """The target's path, without the query string."""
        return urlsplit(self.target).path

    @property
    def query(self) -> Mapping[str, str]:
        """The target's query parameters, last value wins for a repeated name.

        Last-wins rather than first-wins is the choice a reader is least likely
        to be surprised by when a URL is assembled by hand, and nothing here
        treats a repeated parameter as meaningful either way — a name that must
        match a secret is compared in full, so a second spelling of it cannot
        widen anything.
        """
        return {name: values[-1] for name, values in parse_qs(urlsplit(self.target).query).items() if values}


def parse_request_head(raw: bytes) -> RequestHead:
    """Parse a complete request head — up to and excluding the blank line.

    *raw* is the head alone: the caller has already found the terminator, so a
    head that is merely incomplete never reaches here. Raises
    :class:`HttpParseError` for anything that is not a well-formed request line
    followed by well-formed fields, including the two shapes a naive split would
    silently accept — a field with no colon, and a continuation line
    (obs-fold), which RFC 9112 §5.2 requires a server to reject rather than
    guess at.
    """
    if not raw:
        raise HttpParseError("empty request head")

    lines = raw.replace(b"\r\n", b"\n").split(b"\n")
    method, target, version = _parse_request_line(lines[0])
    return RequestHead(method=method, target=target, version=version, headers=_parse_fields(lines[1:]))


def _parse_request_line(raw: bytes) -> tuple[str, str, str]:
    """Split the first line into method, target and version, or refuse it."""
    try:
        request_line = raw.decode("latin-1")
    except UnicodeDecodeError as exc:  # pragma: no cover — latin-1 decodes every byte
        raise HttpParseError("request line is not decodable") from exc

    parts = request_line.split(" ")
    if len(parts) != 3:
        raise HttpParseError(f"malformed request line: {request_line!r}")
    method, target, version = parts
    if not method or not target or not version.startswith("HTTP/"):
        raise HttpParseError(f"malformed request line: {request_line!r}")
    return method, target, version


def _parse_fields(lines: Sequence[bytes]) -> dict[str, str]:
    """Fold the field lines into one lower-cased mapping, or refuse one of them."""
    headers: dict[str, str] = {}
    for line in lines:
        if not line:
            continue
        if line[:1] in (b" ", b"\t"):
            raise HttpParseError("obsolete line folding in a request head")
        try:
            field = line.decode("latin-1")
        except UnicodeDecodeError as exc:  # pragma: no cover — latin-1 decodes every byte
            raise HttpParseError("header line is not decodable") from exc
        name, separator, value = field.partition(":")
        if not separator or not name or name != name.strip():
            raise HttpParseError(f"malformed header line: {field!r}")
        key = name.lower()
        stripped = value.strip()
        headers[key] = f"{headers[key]}, {stripped}" if key in headers else stripped
    return headers


def build_response_head(status: int, headers: Sequence[tuple[str, str]] = ()) -> bytes:
    """Build a response head for *status* carrying *headers*, terminator included.

    Header order is the caller's, unchanged: the host writes ``Server`` first on
    a refusal so a raw transcript identifies the answering program on its second
    line.
    """
    phrase = _REASON_PHRASES.get(status, "Unknown")
    lines = [f"HTTP/1.1 {status} {phrase}"]
    lines.extend(f"{name}: {value}" for name, value in headers)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
