"""The refusals the package's use cases answer SteamGridDB's errors and an unsendable key with."""

from __future__ import annotations

from domain.header_value import no_header_can_carry
from lib.errors import AuthFailed, Refused, ServerUnreachable, SgdbApiError, SgdbKeyUnsendableError

KEY_REFUSED = "SteamGridDB refused the API key — check it in Settings"
INVALID_KEY = "Invalid API key"
RATE_LIMITED = "SteamGridDB is rate-limiting requests — try again in a moment"


def refusal_for(error: SgdbApiError, *, key_refused: str = KEY_REFUSED) -> Refused:
    """The refusal an ``SgdbApiError`` answers with; never the error's own text.

    *key_refused* for a 401 or 403, and the same refusal as
    :func:`refuse_a_key_that_cannot_be_sent` for a key no header can carry.
    """
    if isinstance(error, SgdbKeyUnsendableError):
        return AuthFailed(INVALID_KEY)
    if error.status_code in (401, 403):
        return AuthFailed(key_refused)
    if error.status_code == 429:
        return Refused("rate_limited", RATE_LIMITED)
    if error.status_code is None:
        return ServerUnreachable("Could not reach SteamGridDB")
    return ServerUnreachable(f"SteamGridDB error: HTTP {error.status_code}")


def refuse_a_key_that_cannot_be_sent(api_key: str) -> None:
    """Refuse a key no HTTP header can carry (``domain.header_value``) as ``auth_failed``, before any other work.

    The adapter refuses the same key at the request as well, which is what
    covers the shortcut artwork job; this check gives each endpoint its answer
    before it does anything else.
    """
    if no_header_can_carry(api_key):
        raise AuthFailed(INVALID_KEY)
