"""Exception types raised across layers, and the reason a RomM error answers with.

The RomM and SteamGridDB API errors, a cooperative abort, and ``Refused`` —
and ``classify_error``, which turns a RomM error into a wire reason, with
``romm_error_log_level``, the level such an error is logged at.
"""

import logging
import socket
from typing import Any

from lib.list_result import ErrorCode
from lib.url_host import without_userinfo


class SgdbApiError(Exception):
    """A SteamGridDB call that failed.

    ``status_code`` is the HTTP status of a non-2xx answer, ``None`` where there is none.
    """

    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class SgdbKeyUnsendableError(SgdbApiError):
    """A stored SteamGridDB API key no HTTP header can carry, refused before any request was made."""

    def __init__(self) -> None:
        super().__init__(None, "The SteamGridDB API key holds a character no HTTP header can carry")


class SgdbRateLimitedError(SgdbApiError):
    """A 429 from SteamGridDB's API or its image CDN, kept apart from "no image" so a caller can wait and ask again."""

    def __init__(self, message: str) -> None:
        super().__init__(429, message)


class SteamGridDirMissingError(Exception):
    """Raised when the Steam grid directory cannot be located.

    Distinguishes the "expected, user-recoverable" missing-grid-dir
    condition from generic write failures so callers can route the
    two cases through different log levels.
    """


class RommApiError(Exception):
    """Base exception for all RomM HTTP API errors; its ``url`` never carries the address's user name or password."""

    status_code = None
    # Parsed FastAPI ``detail`` body, populated only by the request paths that
    # read it (422 validation, the pairing-code exchange). ``None`` otherwise, so
    # a caller can branch on two responses that share a status code but differ in
    # ``detail`` without every error path paying to read the body.
    detail: Any = None
    # The reason phrase of the HTTP answer this error translates (``Request
    # Entity Too Large``), set by the adapter's translation; ``None`` where none
    # was kept — an error raised in code, or an unproven 404.
    phrase: str | None = None

    def __init__(self, message, url=None, method=None):
        self.url = without_userinfo(url) if url is not None else None
        self.method = method
        super().__init__(message)


class RommAuthError(RommApiError):
    """401 Unauthorized — bad credentials."""

    status_code = 401


class RommForbiddenError(RommApiError):
    """403 Forbidden — valid credentials but insufficient permissions."""

    status_code = 403


class RommNotFoundError(RommApiError):
    """404 Not Found — resource does not exist."""

    status_code = 404


class RommConflictError(RommApiError):
    """409 Conflict."""

    status_code = 409


class RommUnprocessableEntityError(RommApiError):
    """422 Unprocessable Entity — the request body failed server-side validation.

    Carries the parsed validation ``detail`` body (RomM/FastAPI's
    ``{"detail": [{"loc": [...], "msg": ...}, ...]}``) so a caller can identify
    which entries of a batch endpoint the server rejected. Non-retryable: the
    same body draws the same validation verdict, so replaying it cannot succeed.
    """

    status_code = 422

    def __init__(self, message, *, detail=None, url=None, method=None):
        self.detail = detail
        super().__init__(message, url=url, method=method)


class RommClientError(RommApiError):
    """A 4xx Tender has no class of its own for: RomM, or a proxy in front of it, refused the request.

    Carries the status, so a give-up never records RomM as unreachable — the
    server answered, whatever it answered.
    """

    def __init__(self, message, status_code, url=None, method=None):
        self.status_code = status_code
        super().__init__(message, url=url, method=method)


class RommServerError(RommApiError):
    """5xx server errors (500, 502, 503, etc.)."""

    def __init__(self, message, status_code=500, url=None, method=None):
        self.status_code = status_code
        super().__init__(message, url=url, method=method)


class RommReportedServerError(RommServerError):
    """A 5xx RomM answered itself: a JSON body carrying a non-blank ``detail``.

    The first half of the proof that makes a 404 an entity verdict, without its
    blocklist of FastAPI's stock ``Not Found``. Still retried, but never
    read as RomM being unreachable — a proxy's 5xx for a stopped container has
    no such body and stays a plain :class:`RommServerError`.
    """

    def __init__(self, message, status_code, *, detail: str, url=None, method=None):
        self.detail = detail
        super().__init__(message, status_code=status_code, url=url, method=method)


class RommRateLimitedError(RommServerError):
    """429 Too Many Requests — never retried (``RetryLadder.is_retryable`` says why).

    A :class:`RommServerError` because the pairing-code exchange
    (``RommApiAdapter.exchange_pairing_code``) catches its 429 as one.
    """

    def __init__(self, message, url=None, method=None):
        super().__init__(message, status_code=429, url=url, method=method)


class RommAcknowledgementNotTaken(RommApiError):
    """RomM still does not show this device current: after an answer naming other bytes, or two acknowledgements.

    Each request succeeded, so there is no status: RomM answered, and what it
    recorded is not what was asked.
    """

    def __init__(self, save_id: int):
        self.save_id = save_id
        super().__init__(
            f"RomM did not record save {save_id} as synced on this device",
            url=f"/api/saves/{save_id}/downloaded",
            method="POST",
        )


class RommConnectionError(RommApiError):
    """Network-level failures: connection refused, DNS failure, reset, etc."""


class RommTimeoutError(RommApiError):
    """Request timed out."""


class RommSSLError(RommApiError):
    """SSL certificate verification failure."""


class RommUnsupportedError(RommApiError):
    """Feature not available in the connected RomM server version."""

    def __init__(self, feature, min_version, url=None, method=None):
        self.feature = feature
        self.min_version = min_version
        super().__init__(
            f"{feature} requires RomM {min_version} or newer",
            url=url,
            method=method,
        )


class TokenHostMismatchError(RommApiError):
    """Stored token's minting origin does not match the current ``romm_url``.

    Raised before any request carries the bearer token to a host the token was
    not minted for, so the credential never leaks to a wrong/hostile server.
    Non-retryable: replaying it cannot succeed, only re-signing-in can.
    """


class TokenUnsendableError(RommApiError):
    """The stored Client API Token holds a character no HTTP header can carry.

    Raised before the token is put on a request. Non-retryable: only signing in
    again stores a token that can be sent.
    """


class RommSyncDisabledError(RommApiError):
    """RomM has save sync disabled for this device (the negotiate 400 policy signal).

    RomM's per-device ``sync_enabled`` switch is enforced only at the
    ``negotiate`` endpoint, which 400s with detail "Sync is disabled for this
    device" when the user turned sync off for this device server-side. Distinct
    from a transport failure so the engine can stop the run with a visible
    policy reason instead of degrading to a sessionless sync. Non-retryable:
    replaying it cannot succeed, only re-enabling sync in RomM can.
    """


class PairingCodeInvalidError(RommApiError):
    """Pairing-code exchange rejected: the code is invalid, expired, or already used (404)."""


class PairingCodeTokenGoneError(RommApiError):
    """Pairing-code exchange rejected: the token the code was minted for no longer exists (404)."""


class PairingCodeOwnerDisabledError(RommApiError):
    """Pairing-code exchange rejected: the token owner account is disabled (403)."""


class PairingCodeRateLimitedError(RommApiError):
    """Pairing-code exchange rejected: too many exchange attempts from this client (429)."""


class DeviceWriteError(OSError):
    """A download's destination on this device could not be opened, written or closed.

    Raised by the RomM adapter in place of the ``OSError`` behind it, a full
    disk among them, with its ``errno`` and ``strerror`` but not its path. Not
    a :class:`RommApiError`, so it says nothing about RomM being reachable;
    ``RetryLadder.is_retryable`` refuses it by name — as an ``OSError`` it would
    otherwise be retried. A caller with an answer of its own words it as a
    fault on this device with :func:`with_system_text`; elsewhere its text is
    the message.
    """


def with_system_text(sentence: str, exc: OSError) -> str:
    """*sentence* with the system's own words for *exc* appended — ``… (No space left on device)``.

    Only ``strerror`` is appended, which names no path; an ``OSError`` raised
    with a message alone has none, and *sentence* then stands alone.
    """
    return f"{sentence} ({exc.strerror})" if exc.strerror else sentence


# A RomM ``detail`` goes into a one-line message; a traceback or an essay
# would bury the status in front of it.
_DETAIL_LIMIT = 200


def _detail_line(detail: str) -> str:
    """The first line of *detail*, cut to :data:`_DETAIL_LIMIT` characters."""
    line = detail.strip().splitlines()[0]
    return line if len(line) <= _DETAIL_LIMIT else line[: _DETAIL_LIMIT - 1] + "\u2026"


def _reported_message(exc: RommReportedServerError) -> str:
    """:func:`classify_error`'s message for a 5xx RomM reported itself."""
    return f"RomM reported an error (HTTP {exc.status_code}) \u2014 {_detail_line(exc.detail)}"


def _server_error_message(exc: RommServerError) -> str:
    """:func:`classify_error`'s message for any other 5xx."""
    return f"Server error ({exc.status_code or 500}) \u2014 check your RomM server logs"


def _unreached_message(exc: RommSSLError | RommTimeoutError | RommConnectionError) -> str:
    """:func:`classify_error`'s message for a request that never reached RomM."""
    if isinstance(exc, RommSSLError):
        return "SSL certificate error \u2014 enable 'Allow Insecure SSL' in settings for self-signed certs"
    if isinstance(exc, RommTimeoutError):
        return "Request timed out \u2014 server may be overloaded or network is slow"
    return "Server unreachable \u2014 check your URL and ensure RomM is running"


def _is_client_refusal(exc: Exception) -> bool:
    """Whether *exc* is a RomM error carrying a 4xx status."""
    return isinstance(exc, RommApiError) and exc.status_code is not None and 400 <= exc.status_code < 500


def _refused_message(exc: RommApiError) -> str:
    """:func:`classify_error`'s message for a 4xx no earlier branch answers: status and phrase, never the address."""
    answer = f"HTTP {exc.status_code} {exc.phrase}" if exc.phrase else f"HTTP {exc.status_code}"
    return f"RomM or a proxy in front of it refused the request ({answer})"


def _unsent_token_message(exc: TokenHostMismatchError | TokenUnsendableError) -> str:
    """:func:`classify_error`'s message for a stored token refused before it is sent."""
    if isinstance(exc, TokenUnsendableError):
        return "Your saved RomM login cannot be used. Sign in again to continue."
    return "Your saved RomM login is for a different server. Sign in again to continue."


def classify_error(exc):
    """Return ``(reason, user_friendly_message)`` for an exception.

    ``reason`` is a canonical :class:`lib.list_result.ErrorCode` slug
    (returned as its string value). Several exception types fold onto one
    slug \u2014 connection/timeout/SSL, a 5xx RomM did not report itself,
    a status-less API error and raw socket errors all map to
    ``server_unreachable``; 401/403 map to ``auth_failed`` \u2014 but
    each branch keeps a distinct human ``message``. In particular the 403
    branch stays distinguishable from the 401 branch: a Cloudflare
    bot-fight 403 at the tunnel edge is not a wrong-credentials failure, so
    the two share the ``auth_failed`` slug but explain different remedies.
    """
    if isinstance(exc, RommAuthError):
        return ErrorCode.AUTH_FAILED.value, "Authentication failed \u2014 check your username and password"
    if isinstance(exc, RommForbiddenError):
        return ErrorCode.AUTH_FAILED.value, "Access denied \u2014 your account lacks permissions for this action"
    if isinstance(exc, RommSSLError | RommTimeoutError | RommConnectionError):
        return ErrorCode.SERVER_UNREACHABLE.value, _unreached_message(exc)
    if isinstance(exc, RommRateLimitedError):
        return "rate_limited", "RomM is rate-limiting requests \u2014 try again in a moment"
    if isinstance(exc, RommReportedServerError):
        return "server_error", _reported_message(exc)
    if isinstance(exc, RommServerError):
        return ErrorCode.SERVER_UNREACHABLE.value, _server_error_message(exc)
    if isinstance(exc, RommNotFoundError):
        return ErrorCode.NOT_FOUND.value, "Resource not found on server"
    if isinstance(exc, RommAcknowledgementNotTaken):
        return "server_error", "saved on this device, but RomM did not record it yet; the next sync will"
    if isinstance(exc, RommUnsupportedError):
        return ErrorCode.UNSUPPORTED.value, f"This feature requires RomM {exc.min_version} or newer"
    if isinstance(exc, TokenHostMismatchError | TokenUnsendableError):
        return "config_error", _unsent_token_message(exc)
    if isinstance(exc, RommSyncDisabledError):
        return (
            "device_sync_disabled",
            "Save sync is disabled for this device on the RomM server — enable it in RomM's device settings",
        )
    if _is_client_refusal(exc):
        return ErrorCode.UNKNOWN.value, _refused_message(exc)
    if isinstance(exc, RommApiError):
        return ErrorCode.SERVER_UNREACHABLE.value, str(exc)
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        # A socket-level failure that never passed through the adapter's
        # ``translate_http_error`` (a caller reaching the network outside the
        # RomM client). Same reachability verdict as ``RommConnectionError``.
        # Deliberately NOT bare ``OSError``: that also covers local disk I/O,
        # so a failed settings write would report the server as unreachable —
        # the same class of lie as reporting a 404 that way.
        return ErrorCode.SERVER_UNREACHABLE.value, f"Server unreachable — {exc}"
    return ErrorCode.UNKNOWN.value, str(exc)


def romm_error_log_level(exc: RommApiError) -> int:
    """The level a RomM error is logged at.

    Info where :func:`classify_error` answers ``server_unreachable``, because an
    offline handheld is an expected state, and warning otherwise.
    """
    reason, _message = classify_error(exc)
    return logging.INFO if reason == ErrorCode.SERVER_UNREACHABLE.value else logging.WARNING


class Refused(Exception):
    """An operation the service layer will not carry out, raised rather than answered.

    ``main.Endpoints`` builds the wire answer from it; a service raising it
    never builds that dict itself. ``reason`` is the first argument, or the
    class attribute of a :class:`NamedRefused`, spelled as
    ``.claude/rules/endpoints.md`` says. ``details`` travel beside ``reason``
    and ``message``.

    A refusal is a decision, never a bug: a broken invariant raises
    ``ValueError`` and its kind.
    """

    reason: str

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details


class NamedRefused(Refused):
    """A refusal named for its reason: a subclass declares ``reason = "…"`` and nothing else.

    It is raised with a message and details alone, so it always carries its
    class's reason and can never be raised under another.
    """

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(type(self).reason, message, **details)


class NotConfigured(NamedRefused):
    """Nothing to connect with: no server URL, no valid one, or no credential."""

    reason = "config_error"


class AuthFailed(NamedRefused):
    """The server answered and would not accept the credential."""

    reason = "auth_failed"


class ServerUnreachable(NamedRefused):
    """The server could not be reached, or did not answer in a usable way."""

    reason = "server_unreachable"


class VersionUnsupported(NamedRefused):
    """The server runs a RomM older than the minimum this program accepts."""

    reason = "version_error"


class NotInstalled(NamedRefused):
    """The ROM has no installed content on this device."""

    reason = "not_installed"


class SyncBusy(NamedRefused):
    """Another save sync still held the device when this one's wait ran out.

    A local scheduling outcome: the skipped run may never have contacted the
    server, so it is never collapsed onto ``server_unreachable``.
    """

    reason = "sync_busy"


class DeviceSyncDisabled(NamedRefused):
    """The RomM server has save sync turned off for this device.

    RomM's per-device switch, enforced at ``negotiate``; distinct from the local
    ``sync_disabled`` toggle.
    """

    reason = "device_sync_disabled"


class OperationAbortedError(Exception):
    """A cooperative worker stopped because its caller asked it to stop.

    Never a failure: the work was abandoned on request, before it committed
    anything, and whatever partial state it had created has been cleaned up.
    A caller that cannot distinguish this from a fault would report an
    obedient stop as an error.
    """
