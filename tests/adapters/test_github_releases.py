"""Tests for adapters.github_releases — the plugin's own latest-release read."""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from adapters.github_releases import GithubReleaseAdapter
from domain.update_release import LatestRelease


def _payload(tag="tender-v0.33.0", assets=None):
    """A latest-release answer shaped like GitHub's, trimmed to what is read."""
    return {
        "tag_name": tag,
        "name": tag,
        "assets": [{"name": "Tender.zip", "digest": "sha256:ab33cd"}] if assets is None else assets,
    }


def _response(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.fixture
def log():
    """Collects the debug lines the adapter emits — a failed check's only trace."""
    return []


@pytest.fixture
def adapter(log):
    return GithubReleaseAdapter(user_agent="romm-tender/9.9.9", log_debug=log.append)


class TestGetLatestRelease:
    def test_reads_the_version_and_the_asset_digest(self, adapter):
        with patch("urllib.request.urlopen", return_value=_response(json.dumps(_payload()).encode())):
            assert adapter.get_latest_release() == LatestRelease(version="0.33.0", digest="ab33cd")

    def test_sends_the_plugin_user_agent(self, adapter):
        """GitHub's API refuses a request that carries no User-Agent at all."""
        with patch("urllib.request.urlopen", return_value=_response(json.dumps(_payload()).encode())) as opened:
            adapter.get_latest_release()
            req = opened.call_args[0][0]
            assert req.get_header("User-agent") == "romm-tender/9.9.9"
            assert req.full_url == "https://api.github.com/repos/danielcopper/romm-tender/releases/latest"

    def test_digest_of_the_named_asset_only(self, adapter):
        """The release also carries source archives; only the plugin's own zip is ours."""
        assets = [
            {"name": "Source code (zip)", "digest": "sha256:0000"},
            {"name": "Tender.zip", "digest": "sha256:ab33cd"},
        ]
        with patch("urllib.request.urlopen", return_value=_response(json.dumps(_payload(assets=assets)).encode())):
            assert adapter.get_latest_release() == LatestRelease(version="0.33.0", digest="ab33cd")

    @pytest.mark.parametrize(
        "assets",
        [[], [{"name": "other.zip", "digest": "sha256:ab33cd"}], "not-a-list", [{"name": "Tender.zip"}]],
    )
    def test_a_release_without_a_usable_digest_still_reports_the_version(self, adapter, assets):
        """A download stays possible unverified; the version is what the card is about."""
        with patch("urllib.request.urlopen", return_value=_response(json.dumps(_payload(assets=assets)).encode())):
            assert adapter.get_latest_release() == LatestRelease(version="0.33.0", digest=None)

    def test_a_bare_tag_is_still_a_version(self, adapter):
        with patch("urllib.request.urlopen", return_value=_response(json.dumps(_payload(tag="0.33.0")).encode())):
            assert adapter.get_latest_release() == LatestRelease(version="0.33.0", digest="ab33cd")


class TestEveryFailureIsSilent:
    @pytest.mark.parametrize(
        "error",
        [
            urllib.error.URLError("no route to host"),
            urllib.error.HTTPError("https://api.github.com", 403, "rate limited", Message(), None),
            TimeoutError("timed out"),
            OSError(101, "Network is unreachable"),
        ],
    )
    def test_a_failed_request_answers_nothing(self, adapter, log, error):
        with patch("urllib.request.urlopen", side_effect=error):
            assert adapter.get_latest_release() is None
        assert log, "the reason must reach the debug sink, and only there"

    def test_an_unreadable_body_answers_nothing(self, adapter, log):
        with patch("urllib.request.urlopen", return_value=_response(b"<html>502</html>")):
            assert adapter.get_latest_release() is None
        assert log

    def test_an_empty_body_answers_nothing(self, adapter):
        with patch("urllib.request.urlopen", return_value=_response(b"")):
            assert adapter.get_latest_release() is None

    @pytest.mark.parametrize("body", [b"[]", b'"tender-v0.33.0"', b"null"])
    def test_an_answer_that_is_not_an_object_answers_nothing(self, adapter, body):
        with patch("urllib.request.urlopen", return_value=_response(body)):
            assert adapter.get_latest_release() is None

    @pytest.mark.parametrize("tag", [None, "", "tender-v", 42])
    def test_an_answer_naming_no_version_answers_nothing(self, adapter, tag):
        with patch("urllib.request.urlopen", return_value=_response(json.dumps(_payload(tag=tag)).encode())):
            assert adapter.get_latest_release() is None
