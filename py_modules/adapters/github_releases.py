"""GitHub releases HTTP adapter — this plugin's own latest published release.

Owns the one request that asks whether a newer Tender exists, and answers in
this plugin's vocabulary rather than GitHub's. Every failure answers ``None``:
the caller's whole purpose is a card it may or may not show, so an unreachable
network, an unreadable body, or a payload shaped differently than expected all
mean "nothing to say" and never an error the user has to read.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from typing import TYPE_CHECKING, Any

from domain.update_release import LatestRelease, sha256_hex, version_from_tag
from lib.certifi_bundle import ca_bundle as _ca_bundle

if TYPE_CHECKING:
    from collections.abc import Callable

# GitHub's own "the release that is latest" route. Drafts and pre-releases are
# excluded by that route server-side, so nothing here has to filter them.
# Unauthenticated it is rate-limited per IP (``x-ratelimit-limit: 60`` an hour),
# which the caller's once-a-day throttle stays far below.
_LATEST_RELEASE_URL = "https://api.github.com/repos/danielcopper/romm-tender/releases/latest"

# The release asset Decky is pointed at. The name is a literal line in
# ``.github/workflows/release.yml`` and is NOT derived from the package name, so
# it is spelled literally here rather than composed from one.
_ASSET_NAME = "Tender.zip"

# Long enough for a slow mobile connection, short enough that a Deck with no
# route to the internet does not hold the panel's read open: this request is
# made from a callable the QAM waits on.
_TIMEOUT_SECONDS = 10


class GithubReleaseAdapter:
    """Reads the plugin's latest published release from GitHub's REST API.

    Parameters
    ----------
    user_agent:
        Outgoing ``User-Agent`` header value — ``"<package name>/<version>"``,
        threaded in by bootstrap. GitHub's API rejects a request with no
        User-Agent outright.
    log_debug:
        Debug sink. A failed check is a non-event for the user, so the reason it
        failed is only ever visible to someone who turned debug logging on.
    """

    def __init__(self, *, user_agent: str, log_debug: Callable[[str], None]) -> None:
        self._user_agent = user_agent
        self._log_debug = log_debug

    def get_latest_release(self) -> LatestRelease | None:
        """Return the latest published release, or ``None`` when none could be read."""
        payload = self._read_latest()
        if payload is None:
            return None
        version = version_from_tag(payload.get("tag_name"))
        if version is None:
            self._log_debug(f"[update] latest release names no version: {payload.get('tag_name')!r}")
            return None
        return LatestRelease(version=version, digest=self._asset_digest(payload))

    def _read_latest(self) -> dict[str, Any] | None:
        """GET the latest-release payload, or ``None`` on any failure."""
        req = urllib.request.Request(_LATEST_RELEASE_URL, method="GET")
        req.add_header("User-Agent", self._user_agent)
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(req, context=self._ssl_context(), timeout=_TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode())
        except Exception as e:
            self._log_debug(f"[update] latest-release read failed: {e!r}")
            return None
        if not isinstance(payload, dict):
            self._log_debug(f"[update] latest-release answer is not an object: {type(payload).__name__}")
            return None
        return payload

    def _asset_digest(self, payload: dict[str, Any]) -> str | None:
        """Return the sha256 hex of the release's Tender asset, or ``None``."""
        assets = payload.get("assets")
        if not isinstance(assets, list):
            return None
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == _ASSET_NAME:
                digest = sha256_hex(asset.get("digest"))
                if digest is None:
                    self._log_debug(f"[update] {_ASSET_NAME} carries no sha256 digest")
                return digest
        self._log_debug(f"[update] latest release has no {_ASSET_NAME} asset")
        return None

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        return ssl.create_default_context(cafile=_ca_bundle())
