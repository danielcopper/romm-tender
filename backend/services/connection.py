"""ConnectionService — RomM server reachability, version gate, and token auth.

Owns the ``test_connection`` reachability flow and the Client API Token
lifecycle: ``establish_token`` mints a scoped token from a one-time
username/password and discards the credentials, ``establish_user_token``
validates and stores a token the user pasted (the OIDC path, which has no
password to mint from), ``establish_paired_token`` exchanges a short-lived RomM
pairing code for a token (the same OIDC path without pasting). Pure I/O happens
through the ``RommConnectionApi`` Protocol and disk writes through the
``SettingsPersister`` Protocol; this service composes that I/O into the results
the frontend reads and the refusals it raises. The minimum version is injected — ``MIN_ROMM_VERSION`` in
``domain/identity.py``, passed in by bootstrap — so this service remains a
pure orchestration layer.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.identity import DISPLAY_NAME
from domain.version import meets_min_version
from lib.errors import (
    AuthFailed,
    NotConfigured,
    PairingCodeInvalidError,
    PairingCodeOwnerDisabledError,
    PairingCodeRateLimitedError,
    PairingCodeTokenGoneError,
    Refused,
    RommAuthError,
    RommForbiddenError,
    ServerUnreachable,
    VersionUnsupported,
)
from lib.url_host import is_origin_change, is_valid_server_url, normalize_origin, same_origin

if TYPE_CHECKING:
    import asyncio
    import logging

    from services.protocols import (
        ConflictRules,
        DeviceForgetFn,
        PlaytimeScopeNoticeClearFn,
        RommConnectionApi,
        SettingsPersister,
    )


_NO_SERVER_URL_MESSAGE = "No server URL configured"
_INVALID_URL_MESSAGE = "Enter a valid http(s):// server URL"

_FORBIDDEN_TOKEN_MESSAGE = (
    "RomM rejected the sign-in. Check your username and password — if they are correct, your account may not be "
    "allowed to create API tokens (ask your admin or use an account with a higher role)."
)

# Pasted-token validation (``establish_user_token``): a 401 means the token
# string itself is wrong or was revoked; a 403 means the token authenticates
# but lacks a scope the validation probe needs (``me.read`` — see the docs for
# the full scope list Tender requires).
_USER_TOKEN_INVALID_MESSAGE = "The API token is invalid or has been revoked. Create a new token in RomM and try again."
_USER_TOKEN_SCOPE_MESSAGE = (
    "The API token is missing required permissions (scopes). "
    "Grant the scopes listed in Tender's configuration guide and try again."
)
_USER_TOKEN_REJECTED_MESSAGE = (
    "RomM rejected this token. Check you pasted it correctly and that it has the required scopes "
    "(see Tender's configuration guide)."
)

# Pairing-code sign-in (``establish_paired_token``). The 60s single-use pairing
# code is exchanged for a token over a public endpoint; each rejection carries a
# distinct, actionable message. The rate-limit refusal has a reason of its own
# (the server IS reachable — it is neither an auth nor a reachability fault).
_ENTER_PAIRING_CODE_MESSAGE = "Enter the pairing code from RomM"
_PAIRING_CODE_INVALID_MESSAGE = (
    "Pairing code is invalid or has expired — generate a new one in RomM and try again "
    "(codes are valid for 60 seconds)."
)
_PAIRING_TOKEN_GONE_MESSAGE = (
    "The token this pairing code was created for no longer exists in RomM. Create a new API token, then pair again."
)
_PAIRING_OWNER_DISABLED_MESSAGE = (
    "This RomM account is disabled — ask your administrator to re-enable it, then try again."
)
_PAIRING_RATE_LIMITED_MESSAGE = "Too many attempts — wait a minute and generate a new code."
_NO_USABLE_TOKEN_MESSAGE = "RomM did not return a usable token"

# Sign-out (``sign_out``). Local-forget only — Tender never deletes the
# token on the server, so the copy tells the user it stays valid in RomM.
_SIGNED_OUT_MESSAGE = (
    "Signed out. The token is still valid in RomM — revoke it there (Settings → API Tokens) if you no longer want it."
)


def _normalize_pairing_code(code: str) -> str:
    """Normalize a pairing code the way RomM does: drop all whitespace and ``-``, then uppercase.

    RomM's exchange endpoint strips ``-`` and uppercases; Tender additionally
    removes any whitespace the user pasted (leading, trailing, or embedded). So
    ``"ab-cd ef23"`` normalizes to ``"ABCDEF23"``.
    """
    return "".join(code.split()).replace("-", "").upper()


@dataclass(frozen=True)
class ConnectionServiceConfig:
    """Frozen wiring bundle handed to ``ConnectionService.__init__``.

    Carries the live settings dict, the RomM API Protocol, the settings
    persister, the runtime infrastructure (event loop, logger), the
    minimum-version policy tuple, the device-forget callback fired on a
    server-origin change, the playtime scope-notice clear callback fired on
    a fresh sign-in, and the ``ConflictRules`` the sign-in, sign-out and
    connection-test use cases check at their entry. Bundled here so the ctor
    stays within the S107 parameter budget, and so the minimum version
    arrives from the composition root rather than being declared here.
    """

    settings: dict[str, Any]
    romm_api: RommConnectionApi
    settings_persister: SettingsPersister
    loop: asyncio.AbstractEventLoop
    logger: logging.Logger
    min_required_version: tuple[int, ...]
    forget_device: DeviceForgetFn
    clear_playtime_scope_notice: PlaytimeScopeNoticeClearFn
    conflict_rules: ConflictRules


class ConnectionService:
    """Heartbeat, version gate, auth probe, and Client API Token lifecycle.

    The connection test, the three sign-ins and the sign-out each check their
    endpoint's conflict rules at their entry, under that endpoint's name, and
    raise the rule's refusal when one holds (GLOSSARY.md → Conflict rules).
    """

    def __init__(self, *, config: ConnectionServiceConfig) -> None:
        self._settings = config.settings
        self._romm_api = config.romm_api
        self._settings_persister = config.settings_persister
        self._loop = config.loop
        self._logger = config.logger
        self._min_required_version = config.min_required_version
        self._forget_device = config.forget_device
        self._clear_playtime_scope_notice = config.clear_playtime_scope_notice
        self._rules = config.conflict_rules

    async def test_connection(self) -> dict[str, Any]:
        """Probe the configured server and answer ``success`` with a ``message``.

        Raises ``NotConfigured`` when the server URL is unset or no token has
        been minted yet, and ``VersionUnsupported`` (carrying ``romm_version``)
        for a server below the minimum; a RomM error from the heartbeat or the
        platform listing propagates as it is. On success ``romm_version``
        carries the detected server version when the heartbeat exposed one.
        """
        async with self._rules.hold("test_connection", prune=True):
            return await self._test_connection()

    async def _test_connection(self) -> dict[str, Any]:
        if not self._settings.get("romm_url"):
            raise NotConfigured(_NO_SERVER_URL_MESSAGE)
        if not self._settings.get("romm_api_token"):
            raise NotConfigured("Not signed in — sign in to RomM first")

        version = await self._probe_version_or_forget()
        await self._loop.run_in_executor(None, self._romm_api.list_platforms)
        self._refuse_unsupported_version(version)

        await self._backfill_user_id_best_effort()
        return self._success_result(version)

    async def probe_reachability(self) -> dict[str, Any]:
        """Probe server reachability with a fresh heartbeat — no version assertion, no cache.

        A bare connectivity check the launch path runs at the decision point
        instead of trusting a possibly-stale cached connection state. Fires a
        SINGLE-attempt, short-timeout ``/api/heartbeat`` request
        (``heartbeat_once``) on the executor thread — NOT the retrying
        ``heartbeat`` the version/sync flows use — so an offline verdict returns
        in ~3s instead of 3 retries x 30s. Reports ``{"online": True}`` on
        success, ``{"online": False}`` on any exception (transport error, auth
        failure, malformed response). It deliberately does NOT gate on version
        or persist anything — it answers only "can we reach the server right
        now?".
        """
        try:
            await self._loop.run_in_executor(None, self._romm_api.heartbeat_once)
        except Exception as e:
            # Logged so a genuine code/wiring bug in the heartbeat path is
            # diagnosable rather than masquerading silently as "server offline".
            self._logger.debug(f"probe_reachability heartbeat failed: {e}")
            return {"online": False}
        return {"online": True}

    async def establish_token(
        self,
        romm_url: str,
        username: str,
        password: str,
        allow_insecure_ssl: bool | None = None,
    ) -> dict[str, Any]:
        """Mint a Client API Token from one-time credentials and store it.

        Validates the server URL, probes the server, and only on a successful
        mint commits ``romm_url`` / SSL flag / token / id / minting origin to
        disk in a single atomic save. Nothing is persisted before the mint
        succeeds, so a failed sign-in leaves the previous working URL and token
        untouched (#1015). The candidate URL is held only in memory while
        probing, and the old token is cleared in memory first so it never
        leaks to the candidate host (#1039). The username/password are never
        persisted. On a successful sign-in whose origin differs from the
        previous token's, the registered server device id is forgotten
        (best-effort) — it is bound to its minting origin and would otherwise
        404 against the new server's negotiate. Raises ``NotConfigured`` for a
        missing or invalid URL, ``VersionUnsupported`` as :meth:`test_connection`
        does, ``AuthFailed`` when RomM answers the mint with a 403,
        ``ServerUnreachable`` when RomM answers no usable token, and ``Refused``
        with ``save_failed`` when the settings file cannot be written; a RomM
        error propagates as it is.
        """
        async with self._rules.hold("connect_with_credentials", prune=True):
            return await self._establish_token(romm_url, username, password, allow_insecure_ssl)

    async def _establish_token(
        self,
        romm_url: str,
        username: str,
        password: str,
        allow_insecure_ssl: bool | None = None,
    ) -> dict[str, Any]:
        if not romm_url:
            raise NotConfigured(_NO_SERVER_URL_MESSAGE)
        trimmed = romm_url.strip()
        if not is_valid_server_url(trimmed):
            raise NotConfigured(_INVALID_URL_MESSAGE)

        snapshot = self._snapshot_auth_state()
        old_token_id = snapshot["romm_api_token_id"]
        old_token_origin = snapshot["romm_api_token_origin"]
        old_token_source = snapshot["romm_api_token_source"]

        try:
            # Hold the candidate URL in memory only; clear the stored token so the
            # version probe never carries the old server's bearer to this host (and
            # the auth-header origin guard stays quiet during sign-in).
            self._settings["romm_url"] = trimmed
            if allow_insecure_ssl is not None:
                self._settings["romm_allow_insecure_ssl"] = bool(allow_insecure_ssl)
            self._settings["romm_api_token"] = None
            self._settings["romm_api_token_id"] = None
            self._settings["romm_api_token_origin"] = None
            # A new sign-in invalidates the stored identity — re-derived below from
            # the freshly minted token, so it can never linger for a different user
            # or a different server.
            self._settings["romm_user_id"] = None

            version = await self._probe_version_or_forget()
            self._refuse_unsupported_version(version)

            # #1309: never DELETE a user-supplied token — it belongs to the user, not
            # this device (and it has no stored id to delete by anyway).
            # #1038: only replay the DELETE against the same server the old token
            # was minted on. A different (or unknown) origin would delete an
            # unrelated token on the new host, so skip it.
            if old_token_source != "user" and old_token_id is not None:
                if same_origin(old_token_origin, trimmed):
                    await self._delete_existing_token(username, password, old_token_id)
                else:
                    self._logger.info(
                        "Previous token was minted for a different/unknown server; "
                        "skipping DELETE to avoid replaying it against the current server"
                    )

            try:
                minted = await self._loop.run_in_executor(None, self._mint, username, password)
            except RommForbiddenError as e:
                # 403 on token mint: the same auth_failed reason as a 401, but a
                # distinct message. RomM answers 403 indistinguishably for wrong
                # credentials AND for an account that lacks token-creation permission
                # (and a Cloudflare bot-fight 403 lands here too), so the message
                # names both causes rather than asserting only the permission one.
                raise AuthFailed(_FORBIDDEN_TOKEN_MESSAGE) from e

            raw_token = minted.get("raw_token")
            token_id = minted.get("id")
            if not raw_token or token_id is None:
                raise ServerUnreachable(_NO_USABLE_TOKEN_MESSAGE)

            # Host-bind the minted token in memory so the identity probe
            # authenticates, then stamp the user id — both ride the single atomic
            # token-persist save below (the mint response carries only the token id,
            # not the user id, so /api/users/me is the only source here).
            self._settings["romm_api_token"] = raw_token
            self._settings["romm_api_token_origin"] = normalize_origin(trimmed)
            await self._resolve_user_id_in_memory()

            try:
                self._persist_token(raw_token, token_id, origin=normalize_origin(trimmed), source="minted")
            except OSError as e:
                raise Refused("save_failed", f"Save failed: {e}") from e
        except BaseException:
            self._restore_auth_state(snapshot)
            raise

        await self._forget_device_on_origin_change(old_token_origin, trimmed)
        await self._clear_playtime_scope_notice_best_effort()

        return self._success_result(version)

    async def establish_user_token(
        self,
        romm_url: str,
        token: str,
        allow_insecure_ssl: bool | None = None,
    ) -> dict[str, Any]:
        """Store a user-supplied Client API Token after validating it.

        The sign-in path for OIDC accounts, which have no password to mint a
        token from: the user creates a token in RomM's web UI and pastes it
        here. Structurally mirrors :meth:`establish_token` (validate URL → probe
        version → gate → validate credential → persist on success only, rolling
        the in-memory auth state back on any failure), but the credential is the
        pasted token rather than a fresh mint, so there is no mint and no
        server-side DELETE of any prior token. The token is host-bound to the
        entered URL's origin during probing so the auth-header guard attaches it
        and the old server's bearer never leaks to the candidate host. The token
        is validated with an authenticated ``/api/users/me`` probe — a 401 means
        the token is invalid/revoked, a 403 means it authenticates but lacks a
        required scope. The token value is never logged. Raises
        ``NotConfigured`` for a missing or invalid URL or a blank token,
        ``VersionUnsupported`` and ``save_failed`` as :meth:`establish_token`
        does, and ``AuthFailed`` for a token RomM rejects, including a version
        probe that fails while the server answers; any other RomM error
        propagates as it is.
        """
        async with self._rules.hold("connect_with_token", prune=True):
            return await self._establish_user_token(romm_url, token, allow_insecure_ssl)

    async def _establish_user_token(
        self,
        romm_url: str,
        token: str,
        allow_insecure_ssl: bool | None = None,
    ) -> dict[str, Any]:
        if not romm_url:
            raise NotConfigured(_NO_SERVER_URL_MESSAGE)
        trimmed = romm_url.strip()
        if not is_valid_server_url(trimmed):
            raise NotConfigured(_INVALID_URL_MESSAGE)
        trimmed_token = token.strip()
        if not trimmed_token:
            raise NotConfigured("Enter your RomM API token")

        snapshot = self._snapshot_auth_state()
        old_token_origin = snapshot["romm_api_token_origin"]

        try:
            # Hold the candidate URL + pasted token in memory only; stamp the origin
            # so the auth-header guard attaches this token (and not the old server's)
            # to the validation probe. Nothing is persisted until validation passes.
            self._settings["romm_url"] = trimmed
            if allow_insecure_ssl is not None:
                self._settings["romm_allow_insecure_ssl"] = bool(allow_insecure_ssl)
            self._settings["romm_api_token"] = trimmed_token
            self._settings["romm_api_token_id"] = None
            self._settings["romm_api_token_origin"] = normalize_origin(trimmed)
            self._settings["romm_api_token_source"] = "user"
            # A new sign-in invalidates the stored identity — re-derived from the
            # pasted token's /api/users/me validation probe below.
            self._settings["romm_user_id"] = None

            try:
                version = await self._probe_version_or_forget()
            except Exception as e:
                # RomM answers the token-carrying probe with a 500 (a malformed token)
                # or a 403 (a token-shaped but invalid/revoked one) instead of a clean
                # 401, so a bad pasted token otherwise surfaces as a generic server
                # error. The previous auth state, its URL included, is given back before
                # the reachability probe, so the probe asks whether the PREVIOUS server
                # is up, not the one just entered; when it answers, the pasted token is
                # taken to be at fault.
                self._restore_auth_state(snapshot)
                if (await self.probe_reachability()).get("online"):
                    raise AuthFailed(_USER_TOKEN_REJECTED_MESSAGE) from e
                raise
            self._refuse_unsupported_version(version)
        except BaseException:
            self._restore_auth_state(snapshot)
            raise

        return await self._validate_and_persist_user_token(trimmed_token, trimmed, old_token_origin, version, snapshot)

    async def establish_paired_token(
        self,
        romm_url: str,
        code: str,
        allow_insecure_ssl: bool | None = None,
    ) -> dict[str, Any]:
        """Exchange a short-lived RomM pairing code for a Client API Token and store it.

        The zero-typing sign-in for OIDC accounts: instead of pasting a token, the
        user generates a 60-second pairing code in RomM's web UI and enters it
        here; Tender exchanges it for the token over a public endpoint.
        Structurally mirrors :meth:`establish_user_token` (validate URL → probe
        version → gate → obtain the credential → validate via ``/api/users/me`` →
        persist on success only, rolling the in-memory auth state back on any
        failure), but the credential is fetched by the exchange rather than
        pasted. The candidate URL is held in memory with the token trio CLEARED,
        so no old bearer can leak to the candidate host during the unauthenticated
        exchange (or the version probe before it). Each exchange rejection maps to
        a distinct, actionable message; the exchange is never auto-retried (a
        single-use code). The pairing code and the returned token are never
        logged. Refuses as :meth:`establish_token` does, with ``AuthFailed`` for
        a code RomM rejects and ``rate_limited`` when it turns away too many
        exchange attempts.
        """
        async with self._rules.hold("connect_with_pairing_code", prune=True):
            return await self._establish_paired_token(romm_url, code, allow_insecure_ssl)

    async def _establish_paired_token(
        self,
        romm_url: str,
        code: str,
        allow_insecure_ssl: bool | None = None,
    ) -> dict[str, Any]:
        if not romm_url:
            raise NotConfigured(_NO_SERVER_URL_MESSAGE)
        trimmed = romm_url.strip()
        if not is_valid_server_url(trimmed):
            raise NotConfigured(_INVALID_URL_MESSAGE)
        normalized_code = _normalize_pairing_code(code)
        if not normalized_code:
            raise NotConfigured(_ENTER_PAIRING_CODE_MESSAGE)

        snapshot = self._snapshot_auth_state()
        old_token_origin = snapshot["romm_api_token_origin"]

        try:
            # Hold the candidate URL in memory; clear the token trio so the
            # unauthenticated exchange (and the version probe before it) never carries
            # an old server's bearer to the candidate host.
            self._settings["romm_url"] = trimmed
            if allow_insecure_ssl is not None:
                self._settings["romm_allow_insecure_ssl"] = bool(allow_insecure_ssl)
            self._settings["romm_api_token"] = None
            self._settings["romm_api_token_id"] = None
            self._settings["romm_api_token_origin"] = None
            # A new sign-in invalidates the stored identity — re-derived from the
            # exchanged token's /api/users/me validation probe below.
            self._settings["romm_user_id"] = None

            version = await self._probe_version_or_forget()
            self._refuse_unsupported_version(version)

            try:
                exchanged = await self._loop.run_in_executor(None, self._exchange, normalized_code)
            except PairingCodeInvalidError as e:
                raise AuthFailed(_PAIRING_CODE_INVALID_MESSAGE) from e
            except PairingCodeTokenGoneError as e:
                raise AuthFailed(_PAIRING_TOKEN_GONE_MESSAGE) from e
            except PairingCodeOwnerDisabledError as e:
                raise AuthFailed(_PAIRING_OWNER_DISABLED_MESSAGE) from e
            except PairingCodeRateLimitedError as e:
                raise Refused("rate_limited", _PAIRING_RATE_LIMITED_MESSAGE) from e

            raw_token = exchanged.get("raw_token")
            if not raw_token:
                raise ServerUnreachable(_NO_USABLE_TOKEN_MESSAGE)

            # Host-bind the freshly rotated token in memory, then run the exact same
            # ``/api/users/me`` validation + persist tail as the pasted-token path.
            self._settings["romm_api_token"] = raw_token
            self._settings["romm_api_token_id"] = None
            self._settings["romm_api_token_origin"] = normalize_origin(trimmed)
            self._settings["romm_api_token_source"] = "user"
        except BaseException:
            self._restore_auth_state(snapshot)
            raise

        return await self._validate_and_persist_user_token(raw_token, trimmed, old_token_origin, version, snapshot)

    async def _validate_and_persist_user_token(
        self,
        raw_token: str,
        trimmed: str,
        old_token_origin: str | None,
        version: str | None,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate the in-memory host-bound bearer via ``/api/users/me`` and persist on success.

        The shared tail of the two token-handoff sign-ins — the pasted token
        (:meth:`establish_user_token`) and the paired token
        (:meth:`establish_paired_token`). Assumes the caller has already placed
        *raw_token* in memory, host-bound to *trimmed*'s origin, with
        ``romm_api_token_source = "user"``. Runs the authenticated
        ``/api/users/me`` probe (a 401 means the token is invalid/revoked, a 403
        means it lacks a required scope), persists the token with ``id = None``
        and ``"user"`` provenance, then fires the device-forget-on-origin-change
        and playtime-scope-notice clear. Rolls the in-memory auth state back to
        *snapshot* on any failure up to a successful save; disk is untouched until
        validation passes. The token value is never logged.
        """
        try:
            try:
                user_data = await self._loop.run_in_executor(None, self._romm_api.get_current_user)
            except RommAuthError as e:
                raise AuthFailed(_USER_TOKEN_INVALID_MESSAGE) from e
            except RommForbiddenError as e:
                raise AuthFailed(_USER_TOKEN_SCOPE_MESSAGE) from e

            # Stamp the user identity in memory so it rides the single atomic
            # token-persist save (the validation probe already returned it — no
            # second /api/users/me call).
            self._set_user_id_in_memory(user_data)

            try:
                self._persist_token(raw_token, None, origin=normalize_origin(trimmed), source="user")
            except OSError as e:
                raise Refused("save_failed", f"Save failed: {e}") from e
        except BaseException:
            self._restore_auth_state(snapshot)
            raise

        await self._forget_device_on_origin_change(old_token_origin, trimmed)
        await self._clear_playtime_scope_notice_best_effort()

        return self._success_result(version)

    async def _clear_playtime_scope_notice_best_effort(self) -> None:
        """Clear the durable playtime read-scope notice after a fresh sign-in.

        The freshly minted token carries ``roms.user.read`` (#1280), so any "sign
        in again to enable cross-device playtime" notice a prior 403 raised is now
        stale. Best-effort + local-only: a clear failure must never turn a
        successful sign-in into a failure.
        """
        try:
            await self._loop.run_in_executor(None, self._clear_playtime_scope_notice)
        except Exception as e:
            self._logger.warning(f"Could not clear playtime scope notice after sign-in: {e}")

    async def _forget_device_on_origin_change(self, old_token_origin: str | None, new_url: str) -> None:
        """Forget the registered device id only when the sign-in origin genuinely changed.

        A device id is bound to the origin it was minted against, so a real
        server switch must drop it (RomM's negotiate hard-404s a foreign id) and
        let the next sync re-register. A same-server re-sign-in — including a
        token swap on the unchanged URL — must KEEP the id, or the next post-exit
        sync flags a spurious conflict for a save this device itself synced
        (#1437). An *unknown* old origin (``None`` — a legacy/unstamped token, or
        the origin cleared by a prior sign-out) is treated as unknown, not
        different, and never forgets; only two known, differing normalized
        origins are a real change (:func:`is_origin_change`). *old_token_origin*
        is captured from the pre-clear snapshot by the caller, so the
        leak-safety in-memory token clear never destroys the comparison input.
        Called on the success path only — a failed sign-in keeps the
        still-current server's id. Best-effort: the new token is already valid,
        so a failed local clear must not turn a successful sign-in into a
        failure.
        """
        if not is_origin_change(old_token_origin, new_url):
            return
        try:
            await self._loop.run_in_executor(None, self._forget_device)
        except Exception as e:
            self._logger.warning(f"Could not clear device id after origin change: {e}")

    @staticmethod
    def _extract_user_id(user_data: object) -> int | None:
        """Read the RomM user id off a ``/api/users/me`` dict, or ``None``.

        Tolerant of an untrusted server shape: a non-dict payload, a missing
        ``id``, a non-int, or a bool (``isinstance(True, int)`` is ``True`` in
        Python) all yield ``None`` so a malformed identity never scopes
        collections against the wrong owner.
        """
        user_id = user_data.get("id") if isinstance(user_data, dict) else None
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            return None
        return user_id

    def _set_user_id_in_memory(self, user_data: object) -> None:
        """Stamp ``settings['romm_user_id']`` from a ``/api/users/me`` dict, no save.

        The value rides the sign-in's single atomic token-persist save, so
        identity lands on disk together with the token (never a second write). A
        malformed payload yields ``None`` — the ``own`` owner scope then hides
        nothing until the lazy backfill re-derives it.
        """
        self._settings["romm_user_id"] = self._extract_user_id(user_data)

    async def _resolve_user_id_in_memory(self) -> None:
        """Fetch the minted token's user identity into memory (best-effort, no save).

        The mint response carries only the token id, not the user id, so the
        freshly host-bound token is probed against ``/api/users/me``.
        Best-effort: a failure leaves ``romm_user_id`` cleared (``None``) so the
        single token-persist save records ``None`` and the lazy backfill
        re-derives it on the next connection check. Never fails the sign-in.
        """
        try:
            user_data = await self._loop.run_in_executor(None, self._romm_api.get_current_user)
        except Exception as e:
            self._logger.warning(f"Could not read RomM user identity at sign-in: {e}")
            return
        self._set_user_id_in_memory(user_data)

    async def _backfill_user_id_best_effort(self) -> None:
        """Lazily stamp ``romm_user_id`` from the current token when it is unknown.

        Existing installs carry a valid token but no stored identity (the setting
        postdates them). This backfills it on the next connection check so the
        collection owner scope takes effect without a re-login. Fires only
        when the id is missing (a known id needs no network) and a token exists;
        persists in its own save (no token write is in flight here). Best-effort
        — a failure or malformed payload leaves the id unknown, so the ``own``
        owner scope keeps hiding nothing.
        """
        if self._settings.get("romm_user_id") is not None:
            return
        if not self._settings.get("romm_api_token"):
            return
        try:
            user_data = await self._loop.run_in_executor(None, self._romm_api.get_current_user)
        except Exception as e:
            self._logger.warning(f"Could not backfill RomM user identity: {e}")
            return
        user_id = self._extract_user_id(user_data)
        if user_id is None:
            return
        self._settings["romm_user_id"] = user_id
        self._settings_persister.save_settings()

    async def sign_out(self) -> dict[str, Any]:
        """Forget the stored Client API Token on this device — local only.

        Clears the token, its server-side id, its minting origin, and its
        provenance from settings and persists them in a single atomic save,
        keeping ``romm_url`` and the SSL flag so the user need not re-enter
        them. Mirrors the sign-in paths' persist discipline: the auth state is
        snapshotted first, and if the atomic save fails the in-memory state is
        rolled back to *snapshot* and the sign-out is refused with
        ``save_failed``, so a disk error never strands the user with a
        half-forgotten but still-valid token. Only on a successful save is the
        cached RomM server version dropped (``set_version(None)``) so a stale
        value cannot linger. No server-side token deletion ever happens: a
        token Tender minted deliberately lacks the ``me.write`` scope needed to
        delete it (that would require re-entering the password), and a
        user-supplied token belongs to the user, who manages it in RomM's web
        UI. Idempotent — signing out when already signed out still succeeds and
        is harmless.
        """
        async with self._rules.hold("sign_out", prune=True):
            return self._sign_out()

    def _sign_out(self) -> dict[str, Any]:
        snapshot = self._snapshot_auth_state()
        try:
            self._settings["romm_api_token"] = None
            self._settings["romm_api_token_id"] = None
            self._settings["romm_api_token_origin"] = None
            self._settings["romm_api_token_source"] = None
            # Identity is forgotten alongside the token — the ``own`` owner scope has
            # no basis without a signed-in user, and the next sign-in re-derives it.
            self._settings["romm_user_id"] = None
            try:
                self._settings_persister.save_settings()
            except OSError as e:
                raise Refused("save_failed", f"Save failed: {e}") from e
        except BaseException:
            self._restore_auth_state(snapshot)
            raise
        self._romm_api.set_version(None)
        return {"success": True, "message": _SIGNED_OUT_MESSAGE}

    # ── Internal helpers ─────────────────────────────────────────────────

    _AUTH_STATE_KEYS = (
        "romm_url",
        "romm_allow_insecure_ssl",
        "romm_api_token",
        "romm_api_token_id",
        "romm_api_token_origin",
        "romm_api_token_source",
        # Identity is bound to the token: a failed sign-in restores the previous
        # user id, never leaving a half-changed identity behind.
        "romm_user_id",
    )

    def _snapshot_auth_state(self) -> dict[str, Any]:
        """Capture the in-memory auth-relevant settings for restore-on-failure."""
        return {key: self._settings.get(key) for key in self._AUTH_STATE_KEYS}

    def _restore_auth_state(self, snapshot: dict[str, Any]) -> None:
        """Restore the in-memory auth-relevant settings from *snapshot*.

        Disk is untouched (no ``save_settings``), so a failed sign-in rolls the
        live dict back to the previous working URL + token without clobbering
        the on-disk state.
        """
        for key, value in snapshot.items():
            self._settings[key] = value

    def _persist_token(self, raw_token: str, token_id: int | None, *, origin: str | None, source: str) -> None:
        """Persist a token and retire the legacy credentials.

        Stores the token + its id (``None`` for a user-supplied token, which
        carries no server id) + its *origin* + its *source* provenance
        (``"minted"`` or ``"user"``), drops any ``romm_user`` / ``romm_pass``
        an install that stored a password left behind (nothing reads them),
        and saves.
        """
        self._settings["romm_api_token"] = raw_token
        self._settings["romm_api_token_id"] = token_id
        self._settings["romm_api_token_origin"] = origin
        self._settings["romm_api_token_source"] = source
        self._settings.pop("romm_user", None)
        self._settings.pop("romm_pass", None)
        self._settings_persister.save_settings()

    def _probe_version(self) -> str | None:
        """Heartbeat the server, cache the detected version, and return it.

        Runs on the executor thread. ``SYSTEM.VERSION`` is server-controlled and
        untrusted: a missing, non-string, or otherwise malformed value yields
        ``None`` (and clears the cached version), so the version gate downstream
        only ever sees a real version string or ``None``.
        """
        heartbeat = self._romm_api.heartbeat()
        version: str | None = None
        with contextlib.suppress(AttributeError, TypeError):
            raw = heartbeat.get("SYSTEM", {}).get("VERSION")
            if isinstance(raw, str):
                version = raw
        self._romm_api.set_version(version)
        if version:
            self._logger.info(f"RomM server version: {version}")
        return version

    async def _probe_version_or_forget(self) -> str | None:
        """Probe the server's version; a failed probe drops the cached version before it propagates."""
        try:
            return await self._loop.run_in_executor(None, self._probe_version)
        except Exception:
            self._romm_api.set_version(None)
            raise

    def _refuse_unsupported_version(self, version: str | None) -> None:
        """Raise ``VersionUnsupported``, carrying ``romm_version``, when *version* is below the minimum.

        ``development`` builds and an absent version bypass the gate.
        """
        if version and version != "development" and not meets_min_version(version, self._min_required_version):
            min_str = ".".join(str(v) for v in self._min_required_version)
            raise VersionUnsupported(
                f"Tender requires RomM {min_str} or newer. "
                f"Your server is running {version}. "
                "Please update your RomM server to continue using Tender.",
                romm_version=version,
            )

    @staticmethod
    def _success_result(version: str | None) -> dict[str, Any]:
        """Build the success response, carrying ``romm_version`` when detected."""
        result: dict[str, Any] = {"success": True, "message": "Connected to RomM"}
        if version and version != "development":
            result["message"] = f"Connected to RomM {version}"
            result["romm_version"] = version
        elif version == "development":
            result["romm_version"] = version
        return result

    async def _delete_existing_token(self, username: str, password: str, token_id: int) -> None:
        """Best-effort delete of the token this device previously minted on this server.

        Runs on the executor thread via Basic auth (unaffected by the cleared
        bearer). Failures are logged and swallowed so re-establishing auth
        never fails on a stale-token cleanup. The caller is responsible for the
        same-origin guard (#1038) — this only fires the request.
        """
        try:
            await self._loop.run_in_executor(None, self._delete, username, password, token_id)
        except Exception as e:
            self._logger.warning(f"Failed to delete previous Client API Token: {e}")

    def _mint(self, username: str, password: str) -> dict[str, Any]:
        """Synchronous mint worker invoked on the executor thread."""
        return self._romm_api.mint_client_token(username, password, token_name=self._token_name())

    def _exchange(self, code: str) -> dict[str, Any]:
        """Synchronous pairing-code exchange worker invoked on the executor thread."""
        return self._romm_api.exchange_pairing_code(code)

    def _delete(self, username: str, password: str, token_id: int) -> None:
        """Synchronous delete worker invoked on the executor thread."""
        self._romm_api.delete_client_token(username, password, token_id=token_id)

    def _token_name(self) -> str:
        """Build the device-scoped token name from the configured device name.

        A label, and only a label: it is what the user reads in RomM's own token
        list, and nothing on either side ever looks a token up by it — the
        delete above goes by ``token_id``. So a token minted under an earlier
        spelling keeps that spelling and keeps working, and this string is free
        to be the display name rather than the package identifier.
        """
        device_name = self._settings.get("device_name") or "Steam Deck"
        return f"{DISPLAY_NAME} ({device_name})"
