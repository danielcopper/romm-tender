#!/usr/bin/env python3
"""Look for every listed Steam internal in Steam's published client, and report the ones it no longer spells.

Contract: reads both halves of the Steam-internals list — the backend's by
import, the panel's from the JSON ``frontend/scripts/steam-internals.mjs``
prints — fetches the Deck client's stable and public-beta manifests from Valve's
client CDN and the ``steamui_websrc_all`` package each names, refuses a package
whose SHA-256 is not the manifest's, and looks for each listed name in every
``.js`` file of it. A finding — an internal not found in a channel, or an
exempted name both channels now spell — goes to the one open issue labelled
``steam-internals``: a new issue when none is open, a comment when the finding
differs from the last one the issue records, nothing when it does not. A
download that still fails after three tries of up to two minutes each, a
manifest the script cannot read, or a package that does not match its manifest
exits 1 and reports nothing; a finding never fails the run.

How a name is looked for, and what that cannot see, is
``docs/architecture/frontend-bundles.md``'s, "Every Steam internal Tender
reaches", and the invariant register's entry for the list in
``docs/architecture/invariants/startup.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

CDN = "https://client-update.fastly.steamstatic.com/"
CHANNELS = (
    ("Public beta", "steam_client_steamdeck_publicbeta_steamrt64"),
    ("Stable", "steam_client_steamdeck_stable_steamrt64"),
)
PACKAGE = "steamui_websrc_all"

LABEL = "steam-internals"
LABEL_DESCRIPTION = "A Steam internal Tender reaches that Steam's published client no longer spells"
LABEL_COLOR = "d93f0b"
TITLE = "Steam internals missing from Steam's published client"
WORKFLOW = ".github/workflows/steam-internals.yml"
MARKER = "steam-internals-watch"

DOWNLOAD_DEADLINE_SECONDS = 120.0
DOWNLOAD_TRIES = 3
_READ_TIMEOUT_SECONDS = 15
_CHUNK = 1 << 16

_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_IDENTIFIER_PATH = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\([\w$]*\)|\[\])*")
# A host and a route with no scheme: ``steamloopback.host/customimages``.
_ADDRESS = re.compile(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/.+)")
_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
_MARKER_RE = re.compile(rf"<!-- {MARKER}: ([0-9a-f]+) -->")


class Refused(Exception):
    """The run cannot judge a channel; the message says why."""


@dataclass(frozen=True)
class Internal:
    """One listed Steam internal, as the watch reads it from either half."""

    path: str
    losses: tuple[str, ...]
    one_of: str | None = None
    not_spelled_by_steam: str | None = None
    dev_build_only: bool = False


@dataclass(frozen=True)
class Manifest:
    """What a client manifest says of its UI package."""

    version: str
    file: str
    sha256: str


@dataclass(frozen=True)
class Spellings:
    """What one channel's UI JavaScript spells: its identifiers, and its text for literals."""

    identifiers: frozenset[str]
    text: str


@dataclass(frozen=True)
class Miss:
    """An internal, or a group whose every member is, not found in a channel."""

    paths: tuple[str, ...]
    not_found: tuple[str, ...]
    losses: tuple[str, ...]
    dev_build_only: bool


@dataclass(frozen=True)
class ChannelResult:
    """One channel's answer: the misses, the exempted names it spells, and the lone group members missing."""

    channel: str
    version: str
    misses: tuple[Miss, ...]
    exempted_spelled: frozenset[str]
    lone_members_missing: tuple[str, ...]


# ---------------------------------------------------------------- the list


def read_panel_half(dump: str) -> list[Internal]:
    """The panel's entries from the JSON ``frontend/scripts/steam-internals.mjs`` prints."""
    return [
        Internal(
            path=entry["path"],
            losses=tuple(entry["losses"]),
            one_of=entry["oneOf"],
            not_spelled_by_steam=entry["notSpelledBySteam"],
            dev_build_only=entry["devBuildOnly"],
        )
        for entry in json.loads(dump)
    ]


def read_backend_half(backend: Path = BACKEND) -> list[Internal]:
    """The backend's entries, imported from ``backend/domain/steam_internals.py``."""
    sys.path.insert(0, str(backend))
    try:
        from domain.steam_internals import STEAM_INTERNALS
    finally:
        sys.path.remove(str(backend))
    return [
        Internal(path=entry.path, losses=(entry.feature,), not_spelled_by_steam=entry.not_spelled_by_steam)
        for entry in STEAM_INTERNALS
    ]


def merge(*halves: Iterable[Internal]) -> list[Internal]:
    """One entry per path, carrying the losses of every half that lists it."""
    merged: dict[str, Internal] = {}
    for internal in (entry for half in halves for entry in half):
        known = merged.get(internal.path)
        if known is None:
            merged[internal.path] = internal
            continue
        merged[internal.path] = Internal(
            path=internal.path,
            losses=tuple(dict.fromkeys(known.losses + internal.losses)),
            one_of=known.one_of or internal.one_of,
            not_spelled_by_steam=known.not_spelled_by_steam or internal.not_spelled_by_steam,
            dev_build_only=known.dev_build_only and internal.dev_build_only,
        )
    return list(merged.values())


# ------------------------------------------------------------- the client


def parse_manifest(text: str) -> Manifest:
    """The version and the UI package a client manifest (Valve's KeyValues text) names."""
    stack: list[dict[str, Any]] = [{}]
    key: str | None = None
    for match in _VDF_TOKEN.finditer(text):
        string, brace = match.groups()
        if brace == "{":
            if key is None:
                raise Refused("the manifest opens a block with no key")
            child: dict[str, Any] = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) == 1:
                raise Refused("the manifest closes a block it never opened")
            stack.pop()
        elif key is None:
            key = string
        else:
            stack[-1][key] = string
            key = None
    # The first top-level block is the manifest; a ``kvsignatures`` block signing it follows.
    root = next((value for value in stack[0].values() if isinstance(value, dict)), None)
    if root is None:
        raise Refused("the manifest holds no block")
    package = root.get(PACKAGE)
    if not isinstance(root.get("version"), str) or not isinstance(package, dict):
        raise Refused(f"the manifest names no version or no {PACKAGE}")
    if not isinstance(package.get("file"), str) or not isinstance(package.get("sha2"), str):
        raise Refused(f"the manifest's {PACKAGE} names no file or no sha2")
    return Manifest(version=root["version"], file=package["file"], sha256=package["sha2"].lower())


Opener = Callable[..., Any]


def fetch(
    url: str,
    *,
    opener: Opener = urllib.request.urlopen,
    clock: Callable[[], float] = time.monotonic,
    deadline: float = DOWNLOAD_DEADLINE_SECONDS,
    tries: int = DOWNLOAD_TRIES,
) -> bytes:
    """The bytes at ``url``, each try abandoned once it runs past ``deadline`` seconds.

    The deadline is over the whole download, not each read: a download that
    trickles a little every few seconds never trips a socket timeout. ``read1``
    returns what one receive brought, so the deadline is checked that often.
    """
    failures: list[str] = []
    for _ in range(tries):
        started = clock()
        try:
            with opener(url, timeout=_READ_TIMEOUT_SECONDS) as response:
                chunks: list[bytes] = []
                while chunk := response.read1(_CHUNK):
                    chunks.append(chunk)
                    if clock() - started > deadline:
                        raise TimeoutError(f"still downloading after {deadline:.0f} s")
                data = b"".join(chunks)
                # ``read1`` answers a body cut short of its Content-Length with
                # an empty read, as at its end; only a chunked body raises.
                expected = response.headers.get("Content-Length")
                if expected is not None and len(data) != int(expected):
                    raise http.client.IncompleteRead(data, int(expected) - len(data))
                print(f"fetched {url} in {clock() - started:.1f} s", flush=True)
                return data
        # OSError covers urllib's URLError and HTTPError and TimeoutError;
        # HTTPException a body cut short (IncompleteRead).
        except (OSError, http.client.HTTPException) as error:
            failures.append(str(error))
    raise Refused(f"{url}: {'; '.join(failures)}")


def verify(data: bytes, manifest: Manifest) -> None:
    """Refuse ``data`` unless its SHA-256 is the one the manifest states."""
    actual = hashlib.sha256(data).hexdigest()
    if actual != manifest.sha256:
        raise Refused(f"{manifest.file} is {actual}, the manifest says {manifest.sha256}")


def spellings_of(package: bytes) -> Spellings:
    """What every ``.js`` file of a UI package spells."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        text = "\n".join(
            archive.read(name).decode("utf-8", "replace") for name in archive.namelist() if name.endswith(".js")
        )
    return Spellings(identifiers=frozenset(_IDENTIFIER.findall(text)), text=text)


# ----------------------------------------------------------- the judgement


def _spelled_as_literal(literal: str, spellings: Spellings) -> bool:
    bounded = rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"
    return re.search(bounded, spellings.text) is not None


def not_found(path: str, spellings: Spellings) -> tuple[str, ...]:
    """The parts of ``path`` the channel does not spell; empty when it is found.

    An identifier path is found when each name it spells is a whole identifier
    of the JavaScript (a ``(param)`` name is Tender's own and is not looked
    for); an address when its host and its route are each a bounded literal;
    anything else when the whole path is one.
    """
    if _IDENTIFIER_PATH.fullmatch(path):
        names = re.sub(r"\([\w$]*\)|\[\]", "", path).split(".")
        return tuple(name for name in names if name not in spellings.identifiers)
    if "://" not in path and (address := _ADDRESS.fullmatch(path)):
        return tuple(literal for literal in address.groups() if not _spelled_as_literal(literal, spellings))
    return () if _spelled_as_literal(path, spellings) else (path,)


def judge(internals: Sequence[Internal], spellings: Spellings, *, channel: str, version: str) -> ChannelResult:
    """One channel's answer for every listed internal.

    A group is a miss only when none of its members is found; an exempted
    member counts as found, since it is never reported missing.
    """
    missing = {internal.path: not_found(internal.path, spellings) for internal in internals}
    exempted_spelled = frozenset(
        internal.path for internal in internals if internal.not_spelled_by_steam and not missing[internal.path]
    )
    counted = {
        internal.path: missing[internal.path]
        for internal in internals
        if missing[internal.path] and not internal.not_spelled_by_steam
    }

    groups: dict[str, list[Internal]] = {}
    misses: list[Miss] = []
    for internal in internals:
        if internal.one_of is not None:
            groups.setdefault(internal.one_of, []).append(internal)
        elif internal.path in counted:
            misses.append(Miss((internal.path,), counted[internal.path], internal.losses, internal.dev_build_only))

    lone: list[str] = []
    for members in groups.values():
        absent = [member for member in members if member.path in counted]
        if len(absent) == len(members):
            misses.append(
                Miss(
                    paths=tuple(member.path for member in members),
                    not_found=tuple(part for member in members for part in counted[member.path]),
                    losses=tuple(dict.fromkeys(loss for member in members for loss in member.losses)),
                    dev_build_only=all(member.dev_build_only for member in members),
                )
            )
        else:
            lone.extend(member.path for member in absent)
    return ChannelResult(channel, version, tuple(misses), exempted_spelled, tuple(lone))


def stale_exemptions(results: Sequence[ChannelResult]) -> list[str]:
    """The exempted names every channel spells, sorted."""
    if not results:
        return []
    return sorted(frozenset.intersection(*(result.exempted_spelled for result in results)))


# ---------------------------------------------------------------- the issue


def finding_id(results: Sequence[ChannelResult]) -> str | None:
    """A digest of what the run found, or ``None`` when it found nothing to report."""
    stale = stale_exemptions(results)
    if not stale and not any(result.misses for result in results):
        return None
    finding = {
        "channels": {
            result.channel: sorted(path for miss in result.misses for path in miss.paths) for result in results
        },
        "stale": stale,
    }
    return hashlib.sha256(json.dumps(finding, sort_keys=True).encode()).hexdigest()[:16]


def _cell(names: Iterable[str]) -> str:
    return ", ".join(f"`{name}`" for name in names)


def _members_absent(count: int) -> str:
    return "both" if count == 2 else "all"


def _row(miss: Miss) -> str:
    internal = " or ".join(f"`{path}`" for path in miss.paths)
    if miss.dev_build_only:
        internal += " (dev build only)"
    absent = _cell(miss.not_found) if len(miss.paths) == 1 else _members_absent(len(miss.paths))
    costs = "; ".join(miss.losses) or "a log line only"
    return f"| {internal} | {absent} | {costs} |"


def _sections(results: Sequence[ChannelResult]) -> list[str]:
    parts: list[str] = []
    for result in results:
        parts.append(f"### {result.channel} — manifest {result.version}")
        if result.misses:
            rows = "\n".join(_row(miss) for miss in result.misses)
            parts.append(f"| Internal | Not found | What it costs |\n|---|---|---|\n{rows}")
        else:
            parts.append("Every listed internal was found.")
    stale = stale_exemptions(results)
    if stale:
        parts.append("### Exemptions no longer needed")
        parts.append(f"Steam's JavaScript now spells these exempted names in both channels: {_cell(stale)}.")
    return parts


_CAVEAT = (
    "This is an early warning, not a proof: a name is looked for as text anywhere in the client's UI JavaScript, not "
    "on the object Tender reaches it on: a refactor that stops spelling it can raise this with nothing removed, a name "
    "spelled elsewhere can hide one that was, and a name Steam keeps can still have changed what it does. How a name "
    'is looked for: docs/architecture/frontend-bundles.md, "Every Steam internal Tender reaches".'
)


def _tail(finding: str, run_url: str | None) -> list[str]:
    tail = [_CAVEAT]
    if run_url:
        tail.append(f"Run: {run_url}")
    tail.append(f"<!-- {MARKER}: {finding} -->")
    return tail


def issue_body(results: Sequence[ChannelResult], finding: str, run_url: str | None) -> str:
    """The text of a new issue for this run's finding."""
    if any(result.misses for result in results):
        opening = (
            f"The daily check against Valve's client CDN (`{WORKFLOW}`) found Steam internals Tender reaches that "
            "Steam's own JavaScript no longer spells."
        )
    else:
        opening = (
            f"The daily check against Valve's client CDN (`{WORKFLOW}`) found names exempted from it that Steam's "
            "own JavaScript now spells."
        )
    return "\n\n".join([opening, *_sections(results), *_tail(finding, run_url)]) + "\n"


def comment_body(results: Sequence[ChannelResult], finding: str, run_url: str | None) -> str:
    """The text of a comment adding this run's finding to the open issue."""
    return "\n\n".join(["A later check found:", *_sections(results), *_tail(finding, run_url)]) + "\n"


def last_recorded_finding(texts: Iterable[str]) -> str | None:
    """The finding the last marker in the issue's body and comments records."""
    found = [match for text in texts for match in _MARKER_RE.findall(text)]
    return found[-1] if found else None


# --------------------------------------------------------------- reporting

Runner = Callable[[Sequence[str], str | None], str]


def run_gh(arguments: Sequence[str], stdin: str | None = None) -> str:
    """Run ``gh`` with ``arguments``, failing the run when it fails; what ``gh`` writes to stderr reaches the log."""
    finished = subprocess.run(["gh", *arguments], input=stdin, stdout=subprocess.PIPE, text=True, check=True)
    return finished.stdout


class GitHub:
    """The issue tracker, through the ``gh`` command line."""

    def __init__(self, repository: str, run: Runner = run_gh) -> None:
        self._repo = ["--repo", repository]
        self._run = run

    def ensure_label(self) -> None:
        listed = json.loads(self._run(["label", "list", *self._repo, "--search", LABEL, "--json", "name"], None))
        if LABEL not in {label["name"] for label in listed}:
            create = ["label", "create", LABEL, *self._repo, "--description", LABEL_DESCRIPTION, "--color", LABEL_COLOR]
            self._run(create, None)

    def open_issue(self) -> int | None:
        listed = json.loads(
            self._run(["issue", "list", *self._repo, "--label", LABEL, "--state", "open", "--json", "number"], None)
        )
        return min((issue["number"] for issue in listed), default=None)

    def texts(self, number: int) -> list[str]:
        issue = json.loads(self._run(["issue", "view", str(number), *self._repo, "--json", "body,comments"], None))
        return [issue["body"], *(comment["body"] for comment in issue["comments"])]

    def create(self, body: str) -> None:
        self._run(["issue", "create", *self._repo, "--title", TITLE, "--label", LABEL, "--body-file", "-"], body)

    def comment(self, number: int, body: str) -> None:
        self._run(["issue", "comment", str(number), *self._repo, "--body-file", "-"], body)


def report(results: Sequence[ChannelResult], github: GitHub, run_url: str | None) -> str:
    """Put this run's finding on the open issue, and say what was done."""
    finding = finding_id(results)
    if finding is None:
        return "nothing to report"
    github.ensure_label()
    number = github.open_issue()
    if number is None:
        github.create(issue_body(results, finding, run_url))
        return "opened an issue"
    if last_recorded_finding(github.texts(number)) == finding:
        return f"#{number} already records this finding"
    github.comment(number, comment_body(results, finding, run_url))
    return f"commented on #{number}"


# -------------------------------------------------------------------- main


def check_channel(internals: Sequence[Internal], channel: str, manifest_name: str) -> ChannelResult:
    """Fetch one channel's UI package, verified, and judge every internal in it."""
    manifest = parse_manifest(fetch(CDN + manifest_name).decode("utf-8"))
    package = fetch(CDN + manifest.file)
    verify(package, manifest)
    return judge(internals, spellings_of(package), channel=channel, version=manifest.version)


def _log(result: ChannelResult) -> None:
    print(f"{result.channel}, manifest {result.version}:")
    for miss in result.misses:
        print(f"  missing: {' or '.join(miss.paths)} (not found: {', '.join(miss.not_found)})")
    for path in result.lone_members_missing:
        print(f"  missing, an alternative is found: {path}")
    for path in sorted(result.exempted_spelled):
        print(f"  exempted but spelled: {path}")
    if not (result.misses or result.lone_members_missing or result.exempted_spelled):
        print("  every listed internal found")


def _run_url() -> str | None:
    server, repository, run = (
        os.environ.get(name) for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    return f"{server}/{repository}/actions/runs/{run}" if server and repository and run else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Look for every listed Steam internal in Steam's published client.")
    parser.add_argument(
        "--panel", type=Path, required=True, help="the JSON frontend/scripts/steam-internals.mjs prints"
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would be reported instead of reporting it")
    arguments = parser.parse_args(argv)

    internals = merge(read_panel_half(arguments.panel.read_text(encoding="utf-8")), read_backend_half())
    try:
        results = [check_channel(internals, channel, name) for channel, name in CHANNELS]
    except Refused as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for result in results:
        _log(result)

    if arguments.dry_run:
        finding = finding_id(results)
        print("nothing to report" if finding is None else issue_body(results, finding, _run_url()))
        return 0
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        print("error: GITHUB_REPOSITORY is not set; pass --dry-run to report nothing", file=sys.stderr)
        return 1
    print(report(results, GitHub(repository), _run_url()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
