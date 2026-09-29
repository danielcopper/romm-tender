"""GitHub releases HTTP adapter — this program's own published releases.

Owns the requests that reach this program's releases: the one that asks whether
a newer Tender exists, answered in this program's vocabulary rather than
GitHub's, and the download of one of a release's assets. The two fail
differently. The question answers ``None`` on every failure: the caller's whole
purpose is a card it may or may not show, so an unreachable network, an
unreadable body, or a payload shaped differently than expected all mean
"nothing to say". A download raises, because its caller has to tell the user
the install did not happen.
"""

from __future__ import annotations

import contextlib
import json
import os
import ssl
import urllib.parse
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

# A download's bound is per read rather than for the whole body, which may take
# minutes on a slow line: it ends a transfer that has stalled, not a slow one.
_DOWNLOAD_READ_TIMEOUT_SECONDS = 30
_DOWNLOAD_BLOCK_BYTES = 64 * 1024

# An asset address comes out of a GitHub answer, or out of the fake server a
# test points ``TENDER_RELEASE_API`` at, which serves plain HTTP on loopback.
_DOWNLOAD_SCHEMES = frozenset({"https", "http"})


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

        A tarball with no sha256 digest counts as none, and so does one with no
        ``<tarball>.sha256`` asset beside it: either way it could not be
        verified the way the installer verifies it, so the release is not
        available.

        The addresses and the digest come off the SAME answer:
        ``browser_download_url`` names the release it belongs to rather than
        whatever is newest, which is what makes it safe to pair with a digest.
        """
        name = tarball_name(version)
        assets = payload.get("assets")
        by_name = (
            {asset.get("name"): asset for asset in assets if isinstance(asset, dict)}
            if isinstance(assets, list)
            else {}
        )
        asset = by_name.get(name)
        if asset is None:
            self._log_debug(f"[update] release {version} carries no {name} yet")
            return None
        url = _download_address(asset)
        if url is None:
            self._log_debug(f"[update] {name} states no download address")
            return None
        digest = sha256_hex(asset.get("digest"))
        if digest is None:
            self._log_debug(f"[update] {name} carries no sha256 digest")
            return None
        checksum_name = f"{name}.sha256"
        checksum = by_name.get(checksum_name)
        checksum_url = _download_address(checksum) if checksum is not None else None
        if checksum_url is None:
            self._log_debug(f"[update] release {version} carries no usable {checksum_name} yet")
            return None
        return ReleaseTarball(url=url, digest=digest, checksum_url=checksum_url)

    def download_asset(self, url: str, dest: str, progress: Callable[[int, int | None], None] | None) -> None:
        """Download the asset at *url* to *dest*, calling *progress* with the bytes so far and the announced size.

        Written beside *dest* first and renamed into place only once the whole
        body has arrived, so *dest* never holds part of one. Raises ``ValueError``
        for an address that is not HTTP(S) and ``OSError`` for a body shorter
        than the size the server announced; anything the request itself raises
        propagates.
        """
        if urllib.parse.urlsplit(url).scheme not in _DOWNLOAD_SCHEMES:
            raise ValueError(f"not an HTTP(S) address: {url!r}")
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", self._user_agent)
        partial = f"{dest}.part"
        try:
            with urllib.request.urlopen(
                req, context=self._ssl_context(), timeout=_DOWNLOAD_READ_TIMEOUT_SECONDS
            ) as resp:
                announced = resp.headers.get("Content-Length")
                total = int(announced) if announced and announced.isdigit() else None
                done = 0
                with open(partial, "wb") as out:
                    while chunk := resp.read(_DOWNLOAD_BLOCK_BYTES):
                        out.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
            if total is not None and done != total:
                raise OSError(f"download ended at {done} of {total} bytes")
            os.replace(partial, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(partial)
            raise

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        return ssl.create_default_context(cafile=_ca_bundle())


def _download_address(asset: dict[str, Any]) -> str | None:
    url = asset.get("browser_download_url")
    return url if isinstance(url, str) and url else None
