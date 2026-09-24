"""Tests for adapters.github_releases — this program's own latest-release read."""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from adapters.github_releases import GithubReleaseAdapter
from domain.update_release import LatestRelease, ReleaseTarball

_API = "http://fake-github.test/repos/danielcopper/romm-tender/releases/latest"
_PINNED_URL = "https://github.com/danielcopper/romm-tender/releases/download/tender-v0.34.0/romm-tender-0.34.0.tar.gz"
_HEX = "ab34" * 16
_TARBALL = {"name": "romm-tender-0.34.0.tar.gz", "digest": f"sha256:{_HEX}", "browser_download_url": _PINNED_URL}


def _payload(tag="tender-v0.34.0", assets=None):
    """A latest-release answer shaped like GitHub's, trimmed to what is read."""
    return {"tag_name": tag, "name": tag, "assets": [_TARBALL] if assets is None else assets}


def _response(body: bytes):
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _answering(payload: object):
    return patch("urllib.request.urlopen", return_value=_response(json.dumps(payload).encode()))


@pytest.fixture
def log():
    """Collects the debug lines the adapter emits — a failed check's only trace."""
    return []


@pytest.fixture
def adapter(log):
    return GithubReleaseAdapter(api_url=_API, user_agent="romm-tender/9.9.9", log_debug=log.append)


class TestGetLatestRelease:
    def test_reads_the_version_the_tarball_address_and_its_digest(self, adapter):
        with _answering(_payload()):
            assert adapter.get_latest_release() == LatestRelease(
                version="0.34.0", tarball=ReleaseTarball(url=_PINNED_URL, digest=_HEX)
            )

    def test_asks_the_configured_address_with_the_program_user_agent(self, adapter):
        """GitHub's API refuses a request that carries no User-Agent at all."""
        with _answering(_payload()) as opened:
            adapter.get_latest_release()
        req = opened.call_args[0][0]
        assert req.full_url == _API
        assert req.get_header("User-agent") == "romm-tender/9.9.9"
        assert req.get_header("Accept") == "application/vnd.github+json"

    def test_bounds_the_request_with_a_timeout(self, adapter):
        with _answering(_payload()) as opened:
            adapter.get_latest_release()
        assert opened.call_args.kwargs["timeout"] == 10

    def test_both_halves_come_off_the_named_tarball_only(self, adapter):
        """The release also carries source archives and the checksum file; only the tarball is the install."""
        assets = [
            {"name": "Source code (zip)", "digest": "sha256:0000", "browser_download_url": "https://x.test/src.zip"},
            {
                "name": "romm-tender-0.34.0.tar.gz.sha256",
                "digest": "sha256:1111",
                "browser_download_url": "https://x.test/sum",
            },
            _TARBALL,
        ]
        with _answering(_payload(assets=assets)):
            release = adapter.get_latest_release()
        assert release is not None
        assert release.tarball == ReleaseTarball(url=_PINNED_URL, digest=_HEX)

    def test_a_tarball_of_another_version_is_not_this_release_s(self, adapter):
        assets = [{**_TARBALL, "name": "romm-tender-0.33.0.tar.gz"}]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)

    @pytest.mark.parametrize(
        "assets",
        [[], [{"name": "Tender.zip", "digest": "sha256:ab", "browser_download_url": "https://x.test/T.zip"}], "nope"],
    )
    def test_a_release_whose_tarball_is_not_attached_yet_reports_none_for_it(self, adapter, log, assets):
        """The version is known; what is missing is anything to install, which the caller weighs."""
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)
        assert log

    @pytest.mark.parametrize("unusable", [None, "", 42, [_PINNED_URL]])
    def test_a_tarball_stating_no_usable_address_is_no_tarball(self, adapter, unusable):
        assets = [{**_TARBALL, "browser_download_url": unusable}]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)

    @pytest.mark.parametrize("digest", [None, "", _HEX, f"sha512:{_HEX}", "sha256:", "sha256:ab34cd"])
    def test_a_digest_that_is_not_a_sha256_is_carried_as_none(self, adapter, digest):
        """An address with no established sha256 is still an address; the verdict on it is the installer's."""
        assets = [{**_TARBALL, "digest": digest}]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(
                version="0.34.0", tarball=ReleaseTarball(url=_PINNED_URL, digest=None)
            )


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

    @pytest.mark.parametrize("body", [b"[]", b'"tender-v0.34.0"', b"null"])
    def test_an_answer_that_is_not_an_object_answers_nothing(self, adapter, log, body):
        with patch("urllib.request.urlopen", return_value=_response(body)):
            assert adapter.get_latest_release() is None
        assert log

    @pytest.mark.parametrize("tag", [None, "", "tender-v", "tender-vnext", 42, "0.34.0", "v0.34.0", "other-v0.34.0"])
    def test_an_answer_naming_no_tender_release_answers_nothing(self, adapter, log, tag):
        """The installer refuses any tag but ``tender-v…`` as not a Tender release, and so does this."""
        with _answering(_payload(tag=tag)):
            assert adapter.get_latest_release() is None
        assert log
