#!/usr/bin/env python3
"""Fetch the ``@decky/ui`` npm package the lock resolves, verified, as a release asset.

Two of the three bundles carry ``@decky/ui``, which is LGPL-2.1, and a release
offers its source from the same place as Tender itself: the npm package the
build installed — its ``src/`` included — attached to the release beside the
tarball. This script produces that file.

It reads the one ``@decky/ui`` version ``frontend/pnpm-lock.yaml`` resolves and
that entry's ``integrity``, fetches the package for exactly that version from
the npm registry, and writes it as ``<out>/decky-ui-<version>.tgz`` only once
its SHA-512 equals the integrity. A lock with no entry or with two versions, an
integrity that is not SHA-512, a fetch that fails and bytes that do not match
are each refused with exit 1, and leave no file behind.

**What it does not see:** whether the build that ran beside it installed from
the lock. CI and the release job install with ``--frozen-lockfile``, which
refuses a lock that disagrees with ``frontend/package.json``; that is what makes
the lock's bytes the bundled ones.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "frontend" / "pnpm-lock.yaml"

PACKAGE = "@decky/ui"
REGISTRY_URL = "https://registry.npmjs.org/@decky/ui/-/ui-{version}.tgz"
ASSET_NAME = "decky-ui-{version}.tgz"

FETCH_TIMEOUT_SECONDS = 60

# A key under the lock's top-level ``packages:`` is the package and its version,
# quoted because the name starts with ``@``. The same key under ``snapshots:``
# carries the resolved dependencies instead of the resolution, which is why the
# search is confined to the one section.
_ENTRY_RE = re.compile(rf"^  '{re.escape(PACKAGE)}@([^']+)':\s*$")
# A SHA-512 digest is 64 bytes, which base64 spells as 86 characters and ``==``.
_INTEGRITY_RE = re.compile(r"\bintegrity: (sha512-[A-Za-z0-9+/]{86}==)")


class Refused(Exception):
    """The asset cannot be produced; the message says why."""


def locked_package(lock_text: str) -> tuple[str, str]:
    """The ``(version, integrity)`` the lock resolves ``@decky/ui`` to."""
    integrities: dict[str, str | None] = {}
    section = current = None
    for line in lock_text.splitlines():
        if line and not line[0].isspace():
            section, current = line.rstrip(":"), None
        elif section == "packages" and line.startswith("  ") and not line.startswith("   "):
            match = _ENTRY_RE.match(line)
            current = match.group(1) if match else None
            if current is not None:
                integrities[current] = None
        elif current is not None and (integrity := _INTEGRITY_RE.search(line)):
            integrities[current] = integrity.group(1)

    if not integrities:
        raise Refused(f"the lock resolves no {PACKAGE}")
    if len(integrities) > 1:
        raise Refused(f"the lock resolves {PACKAGE} at more than one version: {', '.join(integrities)}")
    [(version, integrity)] = integrities.items()
    if integrity is None:
        raise Refused(f"{PACKAGE}@{version}: the lock states no SHA-512 integrity for it")
    return version, integrity


def verify(data: bytes, integrity: str) -> None:
    """Refuse ``data`` unless its SHA-512 is the one ``integrity`` states."""
    actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if actual != integrity:
        raise Refused(f"the fetched package is {actual}, the lock says {integrity}")


def fetch(url: str) -> bytes:
    """The bytes at ``url``. The only part of this script that uses the network."""
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
            return response.read()
    except OSError as error:  # urllib's URLError and HTTPError among them
        raise Refused(f"{url}: {error}") from error


def write_asset(data: bytes, version: str, out_dir: Path) -> Path:
    """Write ``data`` as the asset for ``version`` under ``out_dir``, whole or not at all."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ASSET_NAME.format(version=version)
    partial = target.with_name(f".{target.name}.part")
    try:
        partial.write_bytes(data)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Fetch the {PACKAGE} package the lock resolves, verified.")
    parser.add_argument("--out", type=Path, default=Path("build"), help="the directory to write the asset into")
    parser.add_argument("--lock", type=Path, default=LOCK, help="the pnpm lock to read the version and integrity from")
    arguments = parser.parse_args(argv)

    try:
        version, integrity = locked_package(arguments.lock.read_text(encoding="utf-8"))
        data = fetch(REGISTRY_URL.format(version=version))
        verify(data, integrity)
        target = write_asset(data, version, arguments.out)
    except (Refused, OSError) as error:
        print(f"ERROR: no {PACKAGE} source asset — {error}", file=sys.stderr)
        return 1
    print(f"OK: {target} is {PACKAGE} {version}, matching the lock's integrity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
