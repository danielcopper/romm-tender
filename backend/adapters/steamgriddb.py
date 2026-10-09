"""SteamGridDB HTTP adapter — handles all HTTP I/O for the SteamGridDB API."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import ssl
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from lib.certifi_bundle import ca_bundle as _ca_bundle
from lib.errors import SgdbApiError

if TYPE_CHECKING:
    import logging

_SGDB_BASE_URL = "https://www.steamgriddb.com/api/v2"


class SteamGridDbAdapter:
    """Concrete SteamGridDB HTTP adapter.

    Parameters
    ----------
    settings:
        Shared settings dict (held by reference). Source of the SGDB API key.
    logger:
        Logger instance.
    user_agent:
        Outgoing ``User-Agent`` header value — ``"<package name>/<version>"``,
        both from ``domain/identity.py`` (e.g. ``"romm-tender/1.2.3"``).
        SteamGridDB rejects the default ``Python-urllib`` UA with 403.
    """

    def __init__(self, *, settings: dict[str, Any], logger: logging.Logger, user_agent: str) -> None:
        self._settings = settings
        self._logger = logger
        self._user_agent = user_agent

    def _ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=_ca_bundle())

    def _get_json(self, url: str, api_key: str) -> dict[str, Any]:
        """Authenticated GET of *url*, answering SteamGridDB's JSON object.

        Raises ``SgdbApiError`` on every failure to reach SteamGridDB or to read
        its answer: with the HTTP status of a non-2xx answer, or with
        ``status_code=None`` where there is none — no connection, a timeout, a
        broken response, or a body that is not a JSON object.
        """
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("User-Agent", self._user_agent)
        # Outside the try: a CA bundle that cannot be loaded is an OSError of
        # this device, not SteamGridDB out of reach.
        ctx = self._ssl_context()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                answer = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise SgdbApiError(status_code=e.code, message=str(e)) from e
        except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SgdbApiError(status_code=None, message=str(e)) from e
        if not isinstance(answer, dict):
            raise SgdbApiError(status_code=None, message=f"Not a JSON object: {type(answer).__name__}")
        return answer

    def request(self, path: str) -> dict[str, Any] | None:
        """Authenticated GET to SGDB API v2; ``None`` without an API key.

        Raises ``SgdbApiError`` as :meth:`_get_json` does.
        """
        api_key = self._settings.get("steamgriddb_api_key", "")
        if not api_key:
            return None
        return self._get_json(_SGDB_BASE_URL + path, api_key)

    def download_image(self, url: str, dest_path: str) -> bool:
        """Download image from URL to dest_path with atomic write."""
        tmp_path = dest_path + ".tmp"
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", self._user_agent)
            ctx = self._ssl_context()
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp, open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp_path, dest_path)
            return True
        except Exception as e:
            self._logger.warning(f"SGDB image download failed: {e}")
            if os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
            return False

    def verify_api_key(self, api_key: str) -> dict[str, Any]:
        """Verify an API key against SGDB; raises ``SgdbApiError`` as :meth:`_get_json` does."""
        return self._get_json(f"{_SGDB_BASE_URL}/search/autocomplete/test", api_key)
