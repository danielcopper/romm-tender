"""GitHub releases HTTP adapter — this program's own latest published release.

Owns the one request that asks whether a newer Tender exists, and answers in
this program's vocabulary rather than GitHub's. Every failure answers ``None``:
the caller's whole purpose is a card it may or may not show, so an unreachable
network, an unreadable body, or a payload shaped differently than expected all
mean "nothing to say" and never an error the user has to read.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from typing import TYPE_CHECKING, Any

from domain.update_release import LatestRelease, ReleaseTarball, sha256_hex, tarball_name, version_from_tag
from lib.certifi_bundle import ca_bundle as _ca_bundle

if TYPE_CHECKING:
    from collections.abc import Callable

# ``urllib.request.urlopen`` given no timeout waits indefinitely, and this call
# runs on the default executor's thread pool, which every other
# ``run_in_executor`` on the backend draws from. Nothing awaits the check before
# rendering, so the bound protects that pool rather than a page.
_TIMEOUT_SECONDS = 10


class GithubReleaseAdapter:
    """Reads the latest published release from GitHub's REST API.

    Parameters
    ----------
    api_url:
        The "latest release" route — GitHub's own unless ``TENDER_RELEASE_API``
        pointed it elsewhere. GitHub excludes drafts and pre-releases from that
        route server-side, so nothing here filters them.
    user_agent:
        Outgoing ``User-Agent`` header value — ``"<package name>/<version>"``,
        threaded in by bootstrap. GitHub's API refuses a request with no
        User-Agent outright.
    log_debug:
        Debug sink. A failed check is a non-event for the user, so why it
        failed is only visible to someone who turned debug logging on.
    """

    def __init__(self, *, api_url: str, user_agent: str, log_debug: Callable[[str], None]) -> None:
        self._api_url = api_url
        self._user_agent = user_agent
        self._log_debug = log_debug

    def get_latest_release(self) -> LatestRelease | None:
        """Return the latest published release, or ``None`` when none could be read."""
        payload = self._read_latest()
        if payload is None:
            return None
        version = version_from_tag(payload.get("tag_name"))
        if version is None:
            self._log_debug(f"[update] latest release is not a Tender release: {payload.get('tag_name')!r}")
            return None
        return LatestRelease(version=version, tarball=self._find_tarball(payload, version))

    def _read_latest(self) -> dict[str, Any] | None:
        """GET the latest-release payload, or ``None`` on any failure."""
        req = urllib.request.Request(self._api_url, method="GET")
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

    def _find_tarball(self, payload: dict[str, Any], version: str) -> ReleaseTarball | None:
        """Return the release's tarball asset, or ``None`` when it carries none yet.

        A tarball with no sha256 digest counts as none: it could not be
        verified before an install, so the release is not available.

        The address and the digest come off the SAME asset of the SAME answer:
        ``browser_download_url`` names the release it belongs to rather than
        whatever is newest, which is what makes it safe to pair with a digest.
        """
        name = tarball_name(version)
        assets = payload.get("assets")
        if isinstance(assets, list):
            for asset in assets:
                if not isinstance(asset, dict) or asset.get("name") != name:
                    continue
                url = asset.get("browser_download_url")
                if not isinstance(url, str) or not url:
                    self._log_debug(f"[update] {name} states no download address")
                    return None
                digest = sha256_hex(asset.get("digest"))
                if digest is None:
                    self._log_debug(f"[update] {name} carries no sha256 digest")
                    return None
                return ReleaseTarball(url=url, digest=digest)
        self._log_debug(f"[update] release {version} carries no {name} yet")
        return None

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        return ssl.create_default_context(cafile=_ca_bundle())
