#!/usr/bin/env python3
"""A fake GitHub for this program's own releases, served from tarballs on disk.

Contract: the two routes an update reads, answered from a directory of release
tarballs — ``build/`` as ``mise run package`` leaves it, or any other — and
nothing else:

* ``/api/releases/latest`` — a latest-release answer shaped like GitHub's:
  ``tag_name`` and ``assets``, each asset with its ``name``, a
  ``browser_download_url`` pointing back at this server, and the tarball's
  ``digest`` as ``sha256:<hex>``. It is what the backend's update check reads
  through ``TENDER_RELEASE_API``, and what ``install.sh`` reads its tag from
  when no ``--version`` is given.
* ``/download/<tag>/romm-tender-<V>.tar.gz`` and ``.sha256`` — every tarball in
  the directory under its own tag, not only the latest one, because that is the
  path ``install.sh`` builds from ``TENDER_DOWNLOAD_BASE`` and ``--version``.

The ``.sha256`` file is computed from the tarball's bytes rather than read from
beside it, so the digest and the sidecar always agree with each other.

The four faults an update has to refuse, one flag each: an asset stating no
digest, a release carrying no tarball, a release carrying no ``.sha256`` file,
and a tarball whose bytes do not match its digest. The first three change the
latest release only; the corrupt tarball is served under every tag.

It binds 127.0.0.1 and nothing else, and prints the two variables to point a
backend or the installer at it. Standard library only.

Usage:
    python scripts/fake_github.py                           # build/, its one tarball is latest
    python scripts/fake_github.py --dir DIR --latest 1.3.0 --port 8765
    python scripts/fake_github.py --no-checksum-file        # one of the faults, see --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import signal
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LATEST_PATH = "/api/releases/latest"
DOWNLOAD_PREFIX = "/download"

# The packager's spelling of a release tarball (scripts/package.sh) and
# release-please's spelling of its tag (release-please-config.json).
_TARBALL_RE = re.compile(r"romm-tender-(\d[^/]*)\.tar\.gz")
_TAG_PREFIX = "tender-v"


@dataclass(frozen=True)
class Faults:
    """Which of the refusals an update owes to a bad release this server provokes."""

    no_digest: bool = False
    no_tarball: bool = False
    no_checksum_file: bool = False
    corrupt_tarball: bool = False


def tarball_name(version: str) -> str:
    return f"romm-tender-{version}.tar.gz"


def find_versions(directory: pathlib.Path) -> list[str]:
    """Every version a tarball in *directory* is named for, sorted by name."""
    return sorted(
        match.group(1)
        for path in directory.iterdir()
        if path.is_file() and (match := _TARBALL_RE.fullmatch(path.name)) is not None
    )


def choose_latest(versions: list[str], asked: str | None) -> str:
    """The version to call latest: the one asked for, or the only one there is.

    Several tarballs and no choice is refused rather than sorted: which one is
    "newest" is exactly what a test of the update check is about, so it is
    stated rather than guessed.
    """
    if asked is not None:
        if asked not in versions:
            raise SystemExit(f"fake_github: no {tarball_name(asked)} to serve; found {', '.join(versions) or 'none'}")
        return asked
    if len(versions) != 1:
        raise SystemExit(
            f"fake_github: {len(versions)} tarballs to choose from ({', '.join(versions) or 'none'}); "
            "name the latest with --latest"
        )
    return versions[0]


def served_bytes(tarball: pathlib.Path, faults: Faults) -> bytes:
    """The tarball as this server hands it out: its own bytes, or with its last byte changed."""
    data = tarball.read_bytes()
    if not faults.corrupt_tarball or not data:
        return data
    return data[:-1] + bytes([data[-1] ^ 0xFF])


def sha256_of(tarball: pathlib.Path) -> str:
    return hashlib.sha256(tarball.read_bytes()).hexdigest()


def release_answer(base_url: str, directory: pathlib.Path, version: str, faults: Faults) -> dict[str, object]:
    """GitHub's latest-release answer for *version*, trimmed to what the readers use."""
    tag = f"{_TAG_PREFIX}{version}"
    name = tarball_name(version)
    download = f"{base_url}{DOWNLOAD_PREFIX}/{tag}"
    assets: list[dict[str, object]] = []
    if not faults.no_tarball:
        tarball: dict[str, object] = {"name": name, "browser_download_url": f"{download}/{name}"}
        if not faults.no_digest:
            tarball["digest"] = f"sha256:{sha256_of(directory / name)}"
        assets.append(tarball)
    if not faults.no_checksum_file:
        sidecar = checksum_text(directory / name).encode()
        assets.append(
            {
                "name": f"{name}.sha256",
                "browser_download_url": f"{download}/{name}.sha256",
                "digest": f"sha256:{hashlib.sha256(sidecar).hexdigest()}",
            }
        )
    return {"tag_name": tag, "name": tag, "draft": False, "prerelease": False, "assets": assets}


def checksum_text(tarball: pathlib.Path) -> str:
    """The ``.sha256`` sidecar's content, in the form ``sha256sum -c`` reads."""
    return f"{sha256_of(tarball)}  {tarball.name}\n"


class FakeGithubServer(ThreadingHTTPServer):
    """The server, carrying what every request is answered from."""

    daemon_threads = True

    def __init__(self, port: int, directory: pathlib.Path, latest: str, faults: Faults) -> None:
        super().__init__((HOST, port), _Handler)
        self.directory = directory
        self.latest = latest
        self.faults = faults

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.server_address[1]}"

    def environment(self) -> dict[str, str]:
        """The two variables that point the backend and ``install.sh`` here."""
        return {
            "TENDER_RELEASE_API": f"{self.base_url}{LATEST_PATH}",
            "TENDER_DOWNLOAD_BASE": f"{self.base_url}{DOWNLOAD_PREFIX}",
        }


class _Handler(BaseHTTPRequestHandler):
    server: FakeGithubServer
    _DOWNLOAD_RE: ClassVar[re.Pattern[str]] = re.compile(
        rf"{DOWNLOAD_PREFIX}/{_TAG_PREFIX}(?P<version>[^/]+)/(?P<name>[^/]+)"
    )

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == LATEST_PATH:
            answer = release_answer(self.server.base_url, self.server.directory, self.server.latest, self.server.faults)
            self._send(json.dumps(answer, indent=2).encode(), "application/json")
            return
        match = self._DOWNLOAD_RE.fullmatch(path)
        if match is None:
            self._not_found()
            return
        self._send_asset(match.group("version"), match.group("name"))

    def _send_asset(self, version: str, name: str) -> None:
        tarball = self.server.directory / tarball_name(version)
        faults = self.server.faults
        on_latest = version == self.server.latest
        if not tarball.is_file():
            self._not_found()
        elif name == tarball.name and not (on_latest and faults.no_tarball):
            self._send(served_bytes(tarball, faults), "application/gzip")
        elif name == f"{tarball.name}.sha256" and not (on_latest and faults.no_checksum_file):
            self._send(checksum_text(tarball).encode(), "text/plain")
        else:
            self._not_found()

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        self.send_error(HTTPStatus.NOT_FOUND)


class _Stopped(BaseException):
    """A stop signal arrived: the way this server is meant to end, not a failure.

    A ``BaseException`` because ``socketserver.BaseServer._handle_request_noblock``
    catches ``Exception`` around ``process_request``: a stop raised there would be
    swallowed, and with both signals already switched to ``_swallow`` the server
    could then be ended by nothing short of SIGKILL.
    """


def _stop(signum: int, frame: object) -> None:
    # Under ``mise run`` one Ctrl-C arrives twice — the terminal signals the
    # whole process group and mise forwards the SIGINT it got to its child — so
    # every later signal is swallowed before the first one unwinds, or the
    # second lands inside ``server_close`` and the process dies of it with a
    # traceback. Swallowed by a handler rather than ``SIG_IGN``: a signal that
    # already arrived and is dispatched after the switch finds no callable and
    # CPython prints "ignored due to race condition" to stderr.
    signal.signal(signal.SIGINT, _swallow)
    signal.signal(signal.SIGTERM, _swallow)
    raise _Stopped


def _swallow(signum: int, frame: object) -> None:
    """A stop signal after the first: the stop is under way already."""


def build_server(directory: pathlib.Path, latest: str | None, port: int, faults: Faults) -> FakeGithubServer:
    """A server over *directory*, bound but not yet serving; port 0 takes a free one."""
    versions = find_versions(directory)
    return FakeGithubServer(port, directory, choose_latest(versions, latest), faults)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--dir", type=pathlib.Path, default=pathlib.Path("build"), help="where the tarballs are")
    parser.add_argument("--latest", help="the version to call latest; required when there are several")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}; 0 takes a free one")
    parser.add_argument("--no-digest", action="store_true", help="the latest tarball's asset states no digest")
    parser.add_argument("--no-tarball", action="store_true", help="the latest release carries no tarball")
    parser.add_argument("--no-checksum-file", action="store_true", help="the latest release carries no .sha256 file")
    parser.add_argument(
        "--corrupt-tarball", action="store_true", help="every tarball is served with bytes that do not match its digest"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    if not args.dir.is_dir():
        raise SystemExit(f"fake_github: no directory {args.dir}; run mise run package first, or pass --dir")
    faults = Faults(
        no_digest=args.no_digest,
        no_tarball=args.no_tarball,
        no_checksum_file=args.no_checksum_file,
        corrupt_tarball=args.corrupt_tarball,
    )
    server = build_server(args.dir, args.latest, args.port, faults)
    # Installed before the "Serving" line, and that line written inside the
    # ``try``: whoever reads it may signal at once, and the stop can land in the
    # print or the flush as well as in ``serve_forever``.
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        print(f"Serving {args.dir} on {server.base_url}, latest {_TAG_PREFIX}{server.latest}. Point Tender at it with:")
        for key, value in server.environment().items():
            print(f"  {key}={value}")
        sys.stdout.flush()
        server.serve_forever()
    except _Stopped:
        pass
    finally:
        server.server_close()
    # Interpreter shutdown puts every Python handler but SIG_IGN back on SIG_DFL, where a late signal kills the process.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
