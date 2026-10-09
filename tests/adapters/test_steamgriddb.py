"""Tests for adapters.steamgriddb — SteamGridDB HTTP adapter."""

import http.client
import json
import logging
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from adapters.steamgriddb import SteamGridDbAdapter
from lib.errors import SgdbApiError


@pytest.fixture
def adapter():
    settings = {"steamgriddb_api_key": "test-key-123"}
    return SteamGridDbAdapter(
        settings=settings,
        logger=logging.getLogger("test"),
        user_agent="romm-tender/9.9.9",
    )


class TestRequest:
    def test_returns_none_when_no_api_key(self):
        adapter = SteamGridDbAdapter(
            settings={},
            logger=logging.getLogger("test"),
            user_agent="romm-tender/9.9.9",
        )
        assert adapter.request("/games/igdb/123") is None

    def test_sends_auth_header(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"success": true}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            adapter.request("/games/igdb/123")
            req = mock_open.call_args[0][0]
            assert req.get_header("Authorization") == "Bearer test-key-123"

    def test_sends_user_agent(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"success": true}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            adapter.request("/test")
            req = mock_open.call_args[0][0]
            assert req.get_header("User-agent") == "romm-tender/9.9.9"

    def test_uses_ssl_context(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"success": true}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            adapter.request("/test")
            # Context should be passed as a kwarg
            assert mock_open.call_args[1].get("context") is not None

    def test_returns_parsed_json(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"success": True, "data": {"id": 42}}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = adapter.request("/games/igdb/123")
            assert result == {"success": True, "data": {"id": 42}}

    def test_constructs_correct_url(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"success": true}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            adapter.request("/heroes/game/123")
            req = mock_open.call_args[0][0]
            assert req.full_url == "https://www.steamgriddb.com/api/v2/heroes/game/123"


class TestDownloadImage:
    def test_downloads_to_dest_path(self, adapter, tmp_path):
        dest = str(tmp_path / "test.png")
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"PNG_DATA", b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = adapter.download_image("https://example.com/img.png", dest)
            assert result is True
            with open(dest, "rb") as fh:
                assert fh.read() == b"PNG_DATA"

    def test_sends_user_agent(self, adapter, tmp_path):
        dest = str(tmp_path / "test.png")
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b"PNG", b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            adapter.download_image("https://example.com/img.png", dest)
            req = mock_open.call_args[0][0]
            assert req.get_header("User-agent") == "romm-tender/9.9.9"

    def test_atomic_write_cleans_tmp_on_failure(self, adapter, tmp_path):
        dest = str(tmp_path / "test.png")
        with patch("urllib.request.urlopen", side_effect=Exception("network")):
            result = adapter.download_image("https://example.com/img.png", dest)
            assert result is False
            assert not (tmp_path / "test.png").exists()
            assert not (tmp_path / "test.png.tmp").exists()


class TestVerifyApiKey:
    def test_returns_parsed_response(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"success": True}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = adapter.verify_api_key("my-key")
            assert result == {"success": True}

    def test_sends_provided_key_not_settings_key(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"success": true}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            adapter.verify_api_key("different-key")
            req = mock_open.call_args[0][0]
            assert req.get_header("Authorization") == "Bearer different-key"

    def test_sends_user_agent(self, adapter):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"success": true}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            adapter.verify_api_key("any-key")
            req = mock_open.call_args[0][0]
            assert req.get_header("User-agent") == "romm-tender/9.9.9"

    def test_http_error_raises_sgdb_api_error(self, adapter):
        http_error = urllib.error.HTTPError(
            "https://steamgriddb.com", 401, "Unauthorized", http.client.HTTPMessage(), None
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(SgdbApiError) as exc_info:
                adapter.verify_api_key("bad-key")
            assert exc_info.value.status_code == 401

    def test_http_500_raises_sgdb_api_error(self, adapter):
        http_error = urllib.error.HTTPError(
            "https://steamgriddb.com", 500, "Server Error", http.client.HTTPMessage(), None
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(SgdbApiError) as exc_info:
                adapter.verify_api_key("any-key")
            assert exc_info.value.status_code == 500


class TestRequestHttpErrorWrapping:
    def test_request_wraps_http_error(self, adapter):
        http_error = urllib.error.HTTPError(
            "https://steamgriddb.com", 403, "Forbidden", http.client.HTTPMessage(), None
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(SgdbApiError) as exc_info:
                adapter.request("/games/igdb/123")
            assert exc_info.value.status_code == 403


def _answering(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _breaking_mid_read() -> MagicMock:
    resp = MagicMock()
    resp.read.side_effect = http.client.IncompleteRead(b"{")
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# Each way SteamGridDB can fail to answer or be read, as ``urlopen`` meets it.
_NO_ANSWER = {
    "unreachable": {"side_effect": urllib.error.URLError("Name or service not known")},
    "timeout": {"side_effect": TimeoutError("timed out")},
    "connection reset": {"side_effect": ConnectionResetError("reset by peer")},
    "disconnected": {"side_effect": http.client.RemoteDisconnected("closed without response")},
    "broken mid-read": {"return_value": _breaking_mid_read()},
    "not JSON": {"return_value": _answering(b"<html>Bad Gateway</html>")},
    "not UTF-8": {"return_value": _answering(b"\xff\xfe")},
}


@pytest.fixture(params=["request", "verify_api_key"])
def asking(adapter, request):
    """One of the adapter's two calls to SteamGridDB's API, with its argument bound."""
    if request.param == "request":
        return lambda: adapter.request("/search/autocomplete/mario")
    return lambda: adapter.verify_api_key("any-key")


class TestEveryFailureIsAnSgdbApiError:
    @pytest.mark.parametrize("urlopen", list(_NO_ANSWER.values()), ids=list(_NO_ANSWER))
    def test_a_failure_with_no_status_raises_it_without_one(self, asking, urlopen):
        with patch("urllib.request.urlopen", **urlopen), pytest.raises(SgdbApiError) as exc_info:
            asking()

        assert exc_info.value.status_code is None
        assert exc_info.value.__cause__ is not None

    def test_an_answer_that_is_not_a_json_object_raises_it_without_a_status(self, asking):
        with patch("urllib.request.urlopen", return_value=_answering(b"[]")), pytest.raises(SgdbApiError) as exc_info:
            asking()

        assert exc_info.value.status_code is None

    def test_an_http_error_keeps_its_status(self, asking):
        http_error = urllib.error.HTTPError(
            "https://steamgriddb.com", 502, "Bad Gateway", http.client.HTTPMessage(), None
        )
        with patch("urllib.request.urlopen", side_effect=http_error), pytest.raises(SgdbApiError) as exc_info:
            asking()

        assert exc_info.value.status_code == 502
        assert exc_info.value.__cause__ is http_error
