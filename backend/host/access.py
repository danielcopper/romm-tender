"""Who may talk to this server — the three checks, in one order, for both routes.

Contract: the whole admission decision for an incoming request. The server has
exactly two places a request can enter — the static route and the WebSocket
upgrade — and each one puts the request through :func:`check_access`, so there
is one order and one set of answers rather than two that drift.

**The order is Host, then Origin, then Token, and it is not arbitrary.** Host
first so a refusal is logged as the thing it is: a request that reached us under
a name we do not answer to is a rebinding attempt, and logging it as "wrong
token" would bury that. Origin second, because it is a statement about *who
asked*, which is worth having in the log before the credential decides anything.
Token last, because it is the only one of the three that actually authorises.

Two of the three are defence in depth rather than the gate:

- The **Host** check closes DNS rebinding. An attacker who resolves their own
  name to 127.0.0.1 makes the request same-origin, so no CORS check fires, and a
  plain ``GET`` carries no ``Origin`` at all. Followed through, the attack buys
  nothing here anyway because every route demands the token — the check is in
  place so nobody has to reconstruct that reasoning again.
- The **Origin** check is protection against a foreign web page, **not a
  login**. An absent origin is allowed through: a browser never omits it on a
  WebSocket handshake, so whoever omitted it is not a browser, and they still
  need the token. A foreign origin is refused *and written to the log with its
  value*, which is what answers "what origin does Steam's UI actually send"
  on the first run instead of by a measurement on the device.

The token is a per-process random value that lives only in memory. No route and
no file hands it out, and there is no reader for one: the bundle receives it in
the address it is loaded from, and whatever injects that address runs in this
process. It travels as part of the address and never as a header — a module
load cannot set a header of its own, and any header of ours would force a CORS
preflight on every request.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.http_messages import RequestHead

# Steam's own UI. Plugin code runs in the SharedJSContext window, whose origin
# this is; it is the one origin that is not ours.
STEAM_UI_ORIGIN = "https://steamloopback.host"

# The query parameter the token travels in, and the one the caller's session
# identity travels in. Both are address, never header — see the module docstring.
TOKEN_PARAM = "token"
SESSION_PARAM = "session"

_HOST_MISMATCH = 421
_ORIGIN_REFUSED = 403
_TOKEN_REFUSED = 401


@dataclass(frozen=True)
class AccessPolicy:
    """What this process will answer to, and what it will answer for.

    Both halves are built from the port the server **actually bound**, never
    from the port it asked for: the bind falls back when the preferred port is
    taken, and a policy built from the wish would refuse every request the
    moment it did.
    """

    port: int
    token: str

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """The two spellings of this server's own authority."""
        return frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        """Steam's UI plus our own address — which a browser counts as two origins.

        ``127.0.0.1`` and ``localhost`` are different origins to a browser even
        though they are one address to the kernel, so both are listed. ``[::1]``
        is deliberately absent: this server binds no IPv6 address, so nothing
        can arrive from one.
        """
        return frozenset(
            {
                STEAM_UI_ORIGIN,
                f"http://127.0.0.1:{self.port}",
                f"http://localhost:{self.port}",
            }
        )


@dataclass(frozen=True)
class AccessVerdict:
    """The admission decision, plus what to say about it.

    ``echo_origin`` is the origin a CORS header must name when the request is
    allowed and carried one. It is empty for a request with no origin, which is
    a request no CORS header belongs on.
    """

    allowed: bool
    status: int
    log_line: str
    echo_origin: str

    @property
    def refused(self) -> bool:
        """Was this request turned away?"""
        return not self.allowed


def check_access(head: RequestHead, policy: AccessPolicy) -> AccessVerdict:
    """Put *head* through Host, Origin and Token, in that order.

    Returns the first refusal, or an allowing verdict carrying the origin to
    echo. The log line is written by the caller rather than here — this function
    performs no I/O — but its wording is decided here so both routes describe
    the same refusal the same way.
    """
    request_host = head.header("host")
    if request_host not in policy.allowed_hosts:
        return AccessVerdict(
            allowed=False,
            status=_HOST_MISMATCH,
            log_line=f"refused {head.method} {head.path}: host {request_host!r} is not this server",
            echo_origin="",
        )

    origin = head.header("origin")
    if origin and origin not in policy.allowed_origins:
        return AccessVerdict(
            allowed=False,
            status=_ORIGIN_REFUSED,
            log_line=f"refused {head.method} {head.path}: origin {origin!r} is not allowed",
            echo_origin="",
        )

    offered = head.query.get(TOKEN_PARAM, "")
    if not offered or not secrets.compare_digest(offered, policy.token):
        return AccessVerdict(
            allowed=False,
            status=_TOKEN_REFUSED,
            log_line=f"refused {head.method} {head.path}: {'no token' if not offered else 'wrong token'}",
            echo_origin="",
        )

    return AccessVerdict(allowed=True, status=0, log_line="", echo_origin=origin)


def new_token() -> str:
    """A fresh admission token for one process lifetime."""
    return secrets.token_urlsafe(32)
