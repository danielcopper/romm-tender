"""Tests for ``scripts/fake_github.py`` — the fake GitHub an update is tried against.

The script is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. Each case starts the real server on a free port in a thread and
reads it over HTTP, and the latest-release answer is read by the backend's own
``GithubReleaseAdapter`` rather than by a parser written here: what matters is
that the server says what the update check understands, and a second reading of
GitHub's shape would only agree with itself.

The downloads are checked the way ``install.sh`` checks them — ``sha256sum -c``
over the tarball and its sidecar side by side — so a sidecar spelled in a form
the installer cannot read fails here too. The tarballs are arbitrary bytes: no
reader on either route unpacks one.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from adapters.github_releases import GithubReleaseAdapter
from domain.update_release import LatestRelease, ReleaseTarball

if TYPE_CHECKING:
    from collections.abc import Iterator

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fake_github.py"
_spec = importlib.util.spec_from_file_location("fake_github", _SCRIPT)
assert _spec is not None and _spec.loader is not None
fake_github = importlib.util.module_from_spec(_spec)
sys.modules["fake_github"] = fake_github
_spec.loader.exec_module(fake_github)

_OLD = "1.2.3"
_NEW = "1.3.0"


@pytest.fixture
def build(tmp_path) -> Path:
    """Two releases' tarballs, as ``mise run package`` leaves one per version."""
    directory = tmp_path / "build"
    directory.mkdir()
    for version in (_OLD, _NEW):
        (directory / f"romm-tender-{version}.tar.gz").write_bytes(f"tarball of {version}\n".encode() * 64)
    return directory


def _serving(directory: Path, latest: str | None = _NEW, **faults: bool) -> Any:
    return fake_github.build_server(directory, latest, 0, fake_github.Faults(**faults))


@pytest.fixture
def serve(build) -> Iterator[Any]:
    """Start a server over *build* with the faults asked for; stopped at teardown."""
    started: list[Any] = []

    def start(latest: str | None = _NEW, **faults: bool) -> Any:
        server = _serving(build, latest, **faults)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        started.append((server, thread))
        return server

    yield start
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read()


def _latest(server: Any) -> LatestRelease | None:
    lines: list[str] = []
    adapter = GithubReleaseAdapter(
        api_url=server.environment()["TENDER_RELEASE_API"], user_agent="romm-tender/0.0.0-test", log_debug=lines.append
    )
    return adapter.get_latest_release()


def _download_base(server: Any) -> str:
    return server.environment()["TENDER_DOWNLOAD_BASE"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checks_out(tmp_path: Path, name: str, tarball: bytes, sidecar: bytes) -> bool:
    """Whether ``sha256sum -c`` accepts the pair, as the installer asks it."""
    (tmp_path / name).write_bytes(tarball)
    (tmp_path / f"{name}.sha256").write_bytes(sidecar)
    return subprocess.run(["sha256sum", "-c", f"{name}.sha256"], cwd=tmp_path, capture_output=True).returncode == 0


class TestTheLatestRelease:
    def test_the_update_check_reads_it_as_an_available_release(self, serve, build):
        server = serve()
        base = f"{_download_base(server)}/tender-v{_NEW}/romm-tender-{_NEW}.tar.gz"

        assert _latest(server) == LatestRelease(
            version=_NEW,
            tarball=ReleaseTarball(
                url=base,
                digest=_sha256((build / f"romm-tender-{_NEW}.tar.gz").read_bytes()),
                checksum_url=f"{base}.sha256",
            ),
        )

    def test_it_is_the_version_asked_for(self, serve):
        release = _latest(serve(latest=_OLD))

        assert release is not None
        assert release.version == _OLD

    def test_the_answer_is_shaped_like_github_s(self, serve):
        answer = json.loads(_get(serve().environment()["TENDER_RELEASE_API"]))

        assert answer["tag_name"] == f"tender-v{_NEW}"
        assert [asset["name"] for asset in answer["assets"]] == [
            f"romm-tender-{_NEW}.tar.gz",
            f"romm-tender-{_NEW}.tar.gz.sha256",
        ]
        assert all(asset["digest"].startswith("sha256:") for asset in answer["assets"])

    def test_the_installer_reads_its_tag_off_the_same_answer(self, serve):
        """``resolve_tag`` greps ``"tag_name": "<tag>"`` out of the body, with nothing between but blanks."""
        body = _get(serve().environment()["TENDER_RELEASE_API"]).decode()

        assert f'"tag_name": "tender-v{_NEW}"' in body


class TestTheDownloads:
    @pytest.mark.parametrize("version", [_NEW, _OLD])
    def test_every_tarball_is_served_under_its_own_tag_with_a_sidecar_the_installer_accepts(
        self, serve, build, tmp_path, version
    ):
        """``install.sh --version`` downloads a release that is not the latest one."""
        name = f"romm-tender-{version}.tar.gz"
        tag_url = f"{_download_base(serve())}/tender-v{version}"

        tarball = _get(f"{tag_url}/{name}")
        sidecar = _get(f"{tag_url}/{name}.sha256")

        assert tarball == (build / name).read_bytes()
        assert sidecar.decode() == f"{_sha256(tarball)}  {name}\n"
        assert _checks_out(tmp_path, name, tarball, sidecar)

    def test_the_digest_the_answer_states_is_the_downloaded_tarball_s(self, serve):
        server = serve()
        release = _latest(server)
        assert release is not None and release.tarball is not None

        assert _sha256(_get(release.tarball.url)) == release.tarball.digest

    @pytest.mark.parametrize(
        "path",
        [
            "/download/tender-v9.9.9/romm-tender-9.9.9.tar.gz",
            f"/download/tender-v{_NEW}/romm-tender-{_OLD}.tar.gz",
            f"/download/tender-v{_NEW}/something-else",
            "/api/releases/tags/tender-v1.3.0",
        ],
    )
    def test_anything_else_is_not_found(self, serve, path):
        with pytest.raises(urllib.error.HTTPError) as refused:
            _get(f"{serve().base_url}{path}")

        assert refused.value.code == 404


class TestTheFaults:
    def test_an_asset_without_a_digest_is_no_release_to_the_update_check(self, serve):
        assert _latest(serve(no_digest=True)) == LatestRelease(version=_NEW, tarball=None)

    def test_a_release_without_its_tarball_is_no_release_and_has_nothing_to_download(self, serve):
        server = serve(no_tarball=True)

        assert _latest(server) == LatestRelease(version=_NEW, tarball=None)
        with pytest.raises(urllib.error.HTTPError) as refused:
            _get(f"{_download_base(server)}/tender-v{_NEW}/romm-tender-{_NEW}.tar.gz")
        assert refused.value.code == 404

    def test_a_release_without_its_checksum_file_is_no_release_and_has_no_sidecar(self, serve):
        server = serve(no_checksum_file=True)

        assert _latest(server) == LatestRelease(version=_NEW, tarball=None)
        with pytest.raises(urllib.error.HTTPError) as refused:
            _get(f"{_download_base(server)}/tender-v{_NEW}/romm-tender-{_NEW}.tar.gz.sha256")
        assert refused.value.code == 404

    def test_a_fault_on_the_latest_release_leaves_an_older_one_whole(self, serve):
        tag_url = f"{_download_base(serve(no_tarball=True, no_checksum_file=True))}/tender-v{_OLD}"

        assert _get(f"{tag_url}/romm-tender-{_OLD}.tar.gz")
        assert _get(f"{tag_url}/romm-tender-{_OLD}.tar.gz.sha256")

    def test_a_corrupt_tarball_is_still_offered_and_fails_its_checksum(self, serve, tmp_path):
        """The check has no bytes to compare, so only a download can find this — which is the point of the fault."""
        server = serve(corrupt_tarball=True)
        release = _latest(server)
        assert release is not None and release.tarball is not None

        tarball = _get(release.tarball.url)
        sidecar = _get(release.tarball.checksum_url)

        assert _sha256(tarball) != release.tarball.digest
        assert not _checks_out(tmp_path, f"romm-tender-{_NEW}.tar.gz", tarball, sidecar)


class TestChoosingTheLatest:
    def test_one_tarball_is_the_latest_without_being_named(self, tmp_path):
        (tmp_path / f"romm-tender-{_OLD}.tar.gz").write_bytes(b"x")

        server = _serving(tmp_path, latest=None)
        try:
            assert server.latest == _OLD
        finally:
            server.server_close()

    def test_several_tarballs_and_no_choice_is_refused_by_name(self, build):
        with pytest.raises(SystemExit, match="name the latest with --latest"):
            _serving(build, latest=None)

    def test_a_version_with_no_tarball_is_refused(self, build):
        with pytest.raises(SystemExit, match=re.escape("no romm-tender-9.9.9.tar.gz to serve")):
            _serving(build, latest="9.9.9")

    def test_files_that_are_not_release_tarballs_are_not_versions(self, build):
        (build / f"romm-tender-{_NEW}.tar.gz.sha256").write_text("x")
        (build / "notes.txt").write_text("x")

        assert fake_github.find_versions(build) == [_OLD, _NEW]


class TestWhereItListens:
    def test_it_binds_the_loopback_address_only(self, serve):
        assert serve().server_address[0] == "127.0.0.1"

    def test_it_names_both_variables_to_set(self, serve):
        server = serve()

        assert server.environment() == {
            "TENDER_RELEASE_API": f"http://127.0.0.1:{server.server_address[1]}/api/releases/latest",
            "TENDER_DOWNLOAD_BASE": f"http://127.0.0.1:{server.server_address[1]}/download",
        }

    def test_a_missing_directory_is_refused_before_anything_is_bound(self, tmp_path):
        with pytest.raises(SystemExit, match="mise run package"):
            fake_github.main(["--dir", str(tmp_path / "nowhere"), "--port", "0"])
