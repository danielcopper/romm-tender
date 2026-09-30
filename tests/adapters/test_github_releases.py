"""Tests for adapters.github_releases — this program's own latest-release read."""

from __future__ import annotations

import http.server
import json
import threading
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
_PINNED_SUM_URL = f"{_PINNED_URL}.sha256"
_SIDECAR = {
    "name": "romm-tender-0.34.0.tar.gz.sha256",
    "digest": "sha256:" + "cd56" * 16,
    "browser_download_url": _PINNED_SUM_URL,
}
_VERIFIABLE = ReleaseTarball(url=_PINNED_URL, digest=_HEX, checksum_url=_PINNED_SUM_URL)


def _payload(tag="tender-v0.34.0", assets=None):
    """A latest-release answer shaped like GitHub's, trimmed to what is read."""
    return {"tag_name": tag, "name": tag, "assets": [_TARBALL, _SIDECAR] if assets is None else assets}


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
    def test_reads_the_version_the_tarball_address_its_digest_and_its_checksum_address(self, adapter):
        with _answering(_payload()):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=_VERIFIABLE)

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

    def test_each_address_comes_off_the_asset_it_names(self, adapter):
        """The release also carries source archives; the tarball is the install and the sidecar its checksum."""
        assets = [
            {"name": "Source code (zip)", "digest": "sha256:0000", "browser_download_url": "https://x.test/src.zip"},
            {**_SIDECAR, "digest": "sha256:1111", "browser_download_url": "https://x.test/sum"},
            _TARBALL,
        ]
        with _answering(_payload(assets=assets)):
            release = adapter.get_latest_release()
        assert release is not None
        assert release.tarball == ReleaseTarball(url=_PINNED_URL, digest=_HEX, checksum_url="https://x.test/sum")

    def test_a_tarball_of_another_version_is_not_this_release_s(self, adapter):
        assets = [{**_TARBALL, "name": "romm-tender-0.33.0.tar.gz"}, _SIDECAR]
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
        assets = [{**_TARBALL, "browser_download_url": unusable}, _SIDECAR]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)

    @pytest.mark.parametrize("digest", [None, "", _HEX, f"sha512:{_HEX}", "sha256:", "sha256:ab34cd"])
    def test_a_tarball_without_a_valid_sha256_digest_is_no_tarball(self, adapter, log, digest):
        """It could not be verified before an install, so the release is not available."""
        assets = [{**_TARBALL, "digest": digest}, _SIDECAR]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)
        assert any("no sha256 digest" in line for line in log)

    def test_a_tarball_with_a_valid_sha256_digest_is_the_release_s(self, adapter):
        assets = [{**_TARBALL, "digest": f"sha256:{_HEX.upper()}"}, _SIDECAR]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=_VERIFIABLE)

    def test_a_release_without_the_checksum_file_is_not_there_yet(self, adapter, log):
        """The installer refuses a release it cannot verify against that file, so no card may offer one."""
        with _answering(_payload(assets=[_TARBALL])):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)
        assert any("romm-tender-0.34.0.tar.gz.sha256" in line for line in log)

    def test_a_checksum_file_of_another_version_is_not_this_tarball_s(self, adapter):
        assets = [_TARBALL, {**_SIDECAR, "name": "romm-tender-0.33.0.tar.gz.sha256"}]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)

    @pytest.mark.parametrize("unusable", [None, "", 42, [_PINNED_SUM_URL]])
    def test_a_checksum_file_stating_no_usable_address_is_no_checksum_file(self, adapter, unusable):
        assets = [_TARBALL, {**_SIDECAR, "browser_download_url": unusable}]
        with _answering(_payload(assets=assets)):
            assert adapter.get_latest_release() == LatestRelease(version="0.34.0", tarball=None)


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


class _AssetServer:
    """A loopback HTTP server answering one body per path, as a release's asset host would."""

    def __init__(self) -> None:
        self.bodies: dict[str, bytes] = {}
        self.announce: dict[str, int] = {}
        self.user_agents: list[str | None] = []
        server = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.user_agents.append(self.headers.get("User-Agent"))
                body = server.bodies.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(server.announce.get(self.path, len(body))))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}{path}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join()


@pytest.fixture
def assets():
    server = _AssetServer()
    yield server
    server.close()


class TestDownloadAsset:
    def test_the_whole_body_lands_at_the_destination_with_progress_along_the_way(self, adapter, assets, tmp_path):
        body = bytes(range(256)) * 1024
        assets.bodies["/romm-tender-1.1.0.tar.gz"] = body
        dest = tmp_path / "romm-tender-1.1.0.tar.gz"
        ticks: list[tuple[int, int | None]] = []

        adapter.download_asset(assets.url("/romm-tender-1.1.0.tar.gz"), str(dest), lambda d, t: ticks.append((d, t)))

        assert dest.read_bytes() == body
        assert ticks[-1] == (len(body), len(body))
        assert [done for done, _total in ticks] == sorted(done for done, _total in ticks)
        assert not (tmp_path / "romm-tender-1.1.0.tar.gz.part").exists()

    def test_it_asks_with_the_program_user_agent(self, adapter, assets, tmp_path):
        assets.bodies["/a"] = b"x"

        adapter.download_asset(assets.url("/a"), str(tmp_path / "a"), None)

        assert assets.user_agents == ["romm-tender/9.9.9"]

    def test_a_missing_asset_raises_and_leaves_nothing_behind(self, adapter, assets, tmp_path):
        url = assets.url("/missing")
        dest = str(tmp_path / "missing")

        with pytest.raises(urllib.error.HTTPError):
            adapter.download_asset(url, dest, None)

        assert list(tmp_path.iterdir()) == []

    def test_a_body_shorter_than_announced_raises_and_leaves_nothing_behind(self, adapter, tmp_path):
        """Written as the response a server gives when the connection drops mid-body."""
        resp = MagicMock()
        headers = Message()
        headers["Content-Length"] = "10"
        resp.headers = headers
        resp.read.side_effect = [b"12345", b""]
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        dest = tmp_path / "short"

        with patch("urllib.request.urlopen", return_value=resp), pytest.raises(OSError, match="5 of 10"):
            adapter.download_asset("https://github.test/short", str(dest), None)

        assert list(tmp_path.iterdir()) == []

    def test_a_download_that_raises_midway_leaves_no_partial_file(self, adapter, assets, tmp_path):
        assets.bodies["/a"] = b"x" * 200_000

        def interrupt(done: int, _total: int | None) -> None:
            raise RuntimeError("interrupted")

        url = assets.url("/a")
        dest = str(tmp_path / "a")

        with pytest.raises(RuntimeError, match="interrupted"):
            adapter.download_asset(url, dest, interrupt)

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.test/a", "/relative"])
    def test_an_address_that_is_not_http_is_refused_before_anything_is_asked(self, adapter, tmp_path, url):
        with patch("urllib.request.urlopen") as opened, pytest.raises(ValueError, match="not an HTTP"):
            adapter.download_asset(url, str(tmp_path / "a"), None)

        opened.assert_not_called()

    def test_no_announced_size_is_reported_as_none(self, adapter, tmp_path):
        resp = MagicMock()
        resp.headers = Message()
        resp.read.side_effect = [b"abc", b""]
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        ticks: list[tuple[int, int | None]] = []

        with patch("urllib.request.urlopen", return_value=resp):
            adapter.download_asset("https://github.test/a", str(tmp_path / "a"), lambda d, t: ticks.append((d, t)))

        assert ticks == [(3, None)]
        assert (tmp_path / "a").read_bytes() == b"abc"
