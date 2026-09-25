"""This program's own releases: where they are asked for, how they are named, what the last check saw.

Contract: everything pure about a Tender release — the environment's answer to
where the newest release is looked up and whether this process is the installed
program, the spelling of a release tag and of its tarball, the spelling GitHub
gives an asset digest, and the encode/decode of the answer one check leaves
behind. Talking to GitHub stays in the adapter; storing the answer stays in the
service.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from domain.app_directories import ENV_CODE_DIR

if TYPE_CHECKING:
    from collections.abc import Mapping

# The installer's own test seam, read under the same name and with the same
# default (``install.sh``, ``RELEASE_API``), so one override points both at the
# same fake server.
ENV_RELEASE_API = "TENDER_RELEASE_API"
DEFAULT_RELEASE_API = "https://api.github.com/repos/danielcopper/romm-tender/releases/latest"

# release-please's configured tag shape. ``install.sh``'s ``resolve_tag`` accepts
# exactly ``tender-v[0-9]*`` and refuses any other as "not a Tender release", and
# so does this module.
_TAG_RE = re.compile(r"tender-v([0-9].*)")

# GitHub reports an asset digest as ``<algorithm>:<hex>``.
_SHA256_PREFIX = "sha256:"
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class UpdateSource:
    """Where the newest release is asked for, and whether this process may be updated.

    ``installed_program`` is True only for the process the installed service
    runs — the one an update can replace. A run from a checkout checks and
    shows the card like any other, and is never offered an install.
    """

    release_api: str
    installed_program: bool


def resolve_update_source(environ: Mapping[str, str], running_code_dir: str) -> UpdateSource:
    """Answer where releases are asked for, and whether *running_code_dir* is the installed program.

    Only the installed service sets ``TENDER_CODE_DIR`` — the installer writes it
    into the unit — so its absence marks a start by hand. Its presence alone is
    not enough: a shell that exports it for an installer test would otherwise
    make a checkout claim to be the install. So the variable has to name the
    directory this process's code actually sits in. The comparison is
    normalised but resolves no symlink: the unit's ``ExecStart`` is built from
    the same ``$CODE`` it writes into the variable, so the two spellings agree
    wherever the install is real, and a mismatch errs on the side of offering
    no install.
    """
    release_api = environ.get(ENV_RELEASE_API, "").strip() or DEFAULT_RELEASE_API
    declared = environ.get(ENV_CODE_DIR, "").strip()
    installed = bool(declared) and os.path.normpath(declared) == os.path.normpath(running_code_dir)
    return UpdateSource(release_api=release_api, installed_program=installed)


@dataclass(frozen=True)
class ReleaseTarball:
    """The release's tarball asset: where it is downloaded from and what it must hash to.

    ``url`` names one release rather than whatever is newest, so it stays paired
    with ``digest``, the lowercase sha256 hex GitHub stated for that same asset.
    A tarball with no such digest cannot be verified, so it is not one.
    """

    url: str
    digest: str


@dataclass(frozen=True)
class LatestRelease:
    """The release GitHub calls latest, in this program's vocabulary.

    ``tarball`` is ``None`` while the release carries no tarball with a sha256
    digest — the tarball is attached minutes after the release is published —
    and such a release is not available to anyone.
    """

    version: str
    tarball: ReleaseTarball | None


@dataclass(frozen=True)
class UpdateCheck:
    """What the checks have established, and when the last one ran.

    ``checked_at`` stamps the ATTEMPT, so a check that reached nothing still
    holds the next one off. ``release`` is the last AVAILABLE release a check
    saw — always one with a tarball — and a check that reached
    nothing, or found a release without one, carries it forward unchanged.
    """

    checked_at: float
    release: LatestRelease | None


def version_from_tag(tag: object) -> str | None:
    """Return the bare version a ``tender-v<digit>…`` *tag* names, or ``None``."""
    if not isinstance(tag, str):
        return None
    match = _TAG_RE.fullmatch(tag.strip())
    return match.group(1) if match is not None else None


def tarball_name(version: str) -> str:
    """The name of the release asset an install is made from.

    Spelled the way ``scripts/package.sh`` spells it, literally rather than from
    ``identity.PACKAGE_NAME``: the asset is named by the packager, not by
    anything the package calls itself.
    """
    return f"romm-tender-{version}.tar.gz"


def sha256_hex(digest: object) -> str | None:
    """Return the lowercase hex of a ``sha256:<64 hex digits>`` *digest*, or ``None``.

    A digest under another algorithm, one whose algorithm nothing states, and
    one that is not 64 hex digits all answer ``None`` rather than travelling on
    as a sha256 nobody established.
    """
    if not isinstance(digest, str):
        return None
    stripped = digest.strip()
    if not stripped.startswith(_SHA256_PREFIX):
        return None
    hex_digits = stripped[len(_SHA256_PREFIX) :].lower()
    return hex_digits if _is_sha256_hex(hex_digits) else None


def _is_sha256_hex(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _SHA256_HEX_RE.fullmatch(value) is not None


def encode_update_check(check: UpdateCheck) -> str:
    """Render *check* as the JSON text the ``kv_config`` row holds."""
    release = check.release
    tarball = release.tarball if release is not None else None
    return json.dumps(
        {
            "checked_at": check.checked_at,
            "version": release.version if release is not None else None,
            "tarball_url": tarball.url if tarball is not None else None,
            "digest": tarball.digest if tarball is not None else None,
        }
    )


def decode_update_check(raw: str | None) -> UpdateCheck | None:
    """Decode the stored answer, or ``None`` when there is no usable one.

    ``None`` means "no check has run", so every unusable value — absent, empty,
    not JSON, not an object, no numeric ``checked_at`` — collapses onto it and
    the next call simply checks again. A stored release lacking a version, a
    tarball address or a sha256 digest is dropped on its own: the timestamp is
    still a true statement about when the last check ran, and a release with
    nothing to download and verify is not an available one.
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    checked_at = decoded.get("checked_at")
    if isinstance(checked_at, bool) or not isinstance(checked_at, (int, float)):
        return None
    version = decoded.get("version")
    url = decoded.get("tarball_url")
    digest = decoded.get("digest")
    release = None
    if isinstance(version, str) and version and isinstance(url, str) and url and _is_sha256_hex(digest):
        release = LatestRelease(version=version, tarball=ReleaseTarball(url=url, digest=digest))
    return UpdateCheck(checked_at=float(checked_at), release=release)
