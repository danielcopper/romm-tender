"""Canonical server-origin derivation and comparison for server-identity binding.

A stored Client API Token is minted against one RomM server and must never be
replayed against another. The comparison key is the **full origin** — scheme,
host, and (non-default) port — so this module turns an arbitrary user-entered
URL into that canonical key and answers whether two URLs name the same server.
``https://h`` and ``http://h`` are deliberately distinct origins: a downgrade to
plaintext is a different (and hostile) destination, not the same one.

Anything that needs to decide "is this URL the same server as that one?",
"is this a usable http(s) server URL?" or "does this URL carry a user name or
password?" belongs here. Stdlib-only (``urllib``); no I/O, no network — purely
lexical.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(url: str) -> str | None:
    """Return the canonical ``scheme://host[:port]`` origin of *url*, or ``None``.

    Scheme and host are lowercased; the default port for the scheme (``443``
    for https, ``80`` for http) is folded out; any path, query, fragment, or
    trailing slash is dropped. An IPv6 literal keeps its brackets
    (``https://[::1]:8080``) and a trailing FQDN root dot is normalized away
    (``host.com.`` → ``host.com``), so the result always re-parses through this
    function unchanged. Returns ``None`` when *url* has no scheme, no host, or a
    scheme other than ``http`` / ``https`` — i.e. it is not a usable server
    origin and cannot serve as a comparison key.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    # urlsplit().hostname strips IPv6 brackets and never trims the FQDN root dot.
    # Re-bracket an IPv6 literal (any host containing ``:``) so the origin
    # re-parses, and drop a single trailing dot so ``host.com.`` == ``host.com``.
    host = host.rstrip(".") or host
    host_part = f"[{host}]" if ":" in host else host
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host_part}"
    return f"{scheme}://{host_part}:{port}"


def same_origin(a: str | None, b: str | None) -> bool:
    """Return True iff *a* and *b* normalize to the same non-``None`` origin.

    A ``None`` (or otherwise unparseable) origin on either side is never equal
    to anything — fail closed: an unknown origin is not "the same server".
    """
    origin_a = normalize_origin(a) if a is not None else None
    origin_b = normalize_origin(b) if b is not None else None
    return origin_a is not None and origin_a == origin_b


def is_origin_change(old: str | None, new: str | None) -> bool:
    """Return True iff *old* and *new* are both known origins that name different servers.

    The "did the server genuinely change?" question — the complement of
    :func:`same_origin` but with the opposite posture on an *unknown* origin. A
    ``None`` (or otherwise unparseable) origin on either side is treated as
    unknown, **not** different: a legacy/unstamped token origin (``None``)
    against any URL is not a change, and nothing bound to a server origin (the
    registered device id) is dropped just because the old binding was never
    recorded. Both sides are normalized before comparing; only two parseable
    origins that normalize to different values count as a real change.
    """
    old_origin = normalize_origin(old) if old is not None else None
    new_origin = normalize_origin(new) if new is not None else None
    if old_origin is None or new_origin is None:
        return False
    return old_origin != new_origin


def is_valid_server_url(url: str) -> bool:
    """Return True iff *url* (after stripping surrounding whitespace) names an http(s) origin.

    One of the two checks on a user-entered server URL, :func:`carries_userinfo`
    the other: a value is valid when it carries an ``http`` / ``https`` scheme
    and a host. Path / port / query are allowed (they are dropped by
    :func:`normalize_origin`); a scheme-less, hostless, or non-http(s) value is
    rejected.
    """
    return normalize_origin(url.strip()) is not None


# urllib takes no credentials from an address, so none reaches RomM. With a
# password and no port, ``http.client`` reads the password as a non-numeric port
# and raises ``InvalidURL``; with a port, or a user name alone, it looks the whole
# ``user…@host`` up as a host name, which fails. The panel's Connection row tells
# this refusal apart by "user name and password" (``connectionFailureLabel`` in
# ``frontend/src/bigpicture/MainPage.tsx``), so those words stay in both sentences.
ENTERED_ADDRESS_WITH_USERINFO_MESSAGE = (
    "Tender cannot sign in through a user name and password in the RomM URL — leave them out. "
    "Sign in to RomM under RomM Account; a login for a proxy in front of RomM goes into Custom headers."
)
STORED_ADDRESS_WITH_USERINFO_MESSAGE = (
    "Your saved RomM URL holds a user name and password, which Tender cannot sign in through. "
    "Remove them from the RomM URL, then sign in to RomM under RomM Account; a login for a proxy in front of RomM goes "
    "into Custom headers."
)


def _userinfo_end(url: str) -> tuple[int, int] | None:
    """Where *url*'s ``user:password@`` part starts and where the host after it begins, or ``None``.

    Read off the text the way ``urllib.parse.urlsplit`` bounds the authority —
    after ``//``, up to the first ``/``, ``?`` or ``#`` — but without parsing,
    so an address ``urlsplit`` refuses is still read. A password holding an
    unescaped ``/``, ``?`` or ``#`` ends the authority early, as it does for
    ``urlsplit``, and is not recognised as one.
    """
    start = url.find("//")
    if start == -1:
        return None
    start += 2
    end = len(url)
    for delimiter in "/?#":
        found = url.find(delimiter, start)
        if found != -1:
            end = min(end, found)
    at = url.rfind("@", start, end)
    return None if at == -1 else (start, at + 1)


def carries_userinfo(url: str) -> bool:
    """Whether *url*'s authority carries a user name, a password or both."""
    return _userinfo_end(url) is not None


def without_userinfo(url: str) -> str:
    """*url* with its user name and password taken out, every other character as it was."""
    span = _userinfo_end(url)
    return url if span is None else url[: span[0]] + url[span[1] :]


def romm_namespace(settings: dict[str, Any]) -> str:
    """Return the stable server/user namespace destructive RomM proofs belong to."""
    origin = normalize_origin(str(settings.get("romm_url") or "")) or "invalid-origin"
    token_origin = normalize_origin(str(settings.get("romm_api_token_origin") or "")) or "no-token-origin"
    raw_user = settings.get("romm_user_id")
    user = str(raw_user) if raw_user not in {None, ""} else "unknown-user"
    return f"{origin}|{token_origin}|{user}"
