#!/usr/bin/env python3
"""Release-tarball gate.

``install.sh`` downloads one file and its checksum, verifies the pair with
``sha256sum -c``, unpacks the archive with ``--strip-components=1`` and starts
what comes out as a systemd user unit. Every one of those steps is an assumption
about the archive's shape, and none of them is visible from inside this
repository: the tarball is assembled by ``scripts/package.sh`` from a directory,
and a change on either side — a shipped path dropped, a prune rule widened, a
file the installer newly depends on — leaves both halves green and the download
broken on a user's machine.

It asserts:

  * one top-level entry and nothing beside it, which is what unpacking with
    ``--strip-components=1`` relies on, and that the entry is the directory
    ``romm-tender/`` — the name the packager stages the tree under, so an
    archive rooted in anything else did not come from it;
  * :data:`REQUIRED_FILES` are files inside it, and the launcher among them is
    executable;
  * nothing the packager prunes is in it, and no member is anything but a plain
    file or directory under a plain relative path;
  * ``version.txt`` agrees with the archive's own name and, where a tag is
    given, with the tag the installer reads that name off;
  * the sidecar beside it is one ``sha256sum`` line naming the archive by its
    bare name, with a digest that matches.

:data:`REQUIRED_FILES` is NOT a copy of the packager's ``SHIPPED``. That list
says what goes into the archive — whole directories — and this one names the
single paths that have to come out of it: the module the unit executes, the
bundles the panel is loaded from, the executable every Steam shortcut names as
its ``exe``, the catalogue and the compiled core the backend reads, the version
file the archive's own name is taken from, and the licence texts a distributed
copy carries. The two are allowed to differ in length, and folding either into
the other would make this gate agree with the packager by construction instead
of holding it to the installer.

**Blind spot, and it is a wide one: this reads names, modes and digests, and
starts nothing.** No bundle is evaluated, no module imported, no unit started.
An archive whose ``dist/index.js`` is present and corrupt passes here and fails
on a device.

Exit 0 when the archive is what the installer expects, 1 otherwise, one line per
finding and every finding at once.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import pathlib
import re
import sys
import tarfile

TOP_LEVEL = "romm-tender"

# What ``install.sh`` builds its download URL and its version from: the tag
# carries the version, and the archive beside it is named for the same one.
TAG_PREFIX = "tender-v"
SIDECAR_SUFFIX = ".sha256"

ARCHIVE_NAME_RE = re.compile(rf"^{re.escape(TOP_LEVEL)}-(.+)\.tar\.gz$")

# A ``sha256sum`` line: ``<64 hex><two spaces or space-star><name>``. The ``*``
# marks binary mode; ``sha256sum`` without ``-b`` writes the other one, and
# accepting both costs nothing.
SIDECAR_LINE_RE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")

VERSION_FILE = "version.txt"

# Paths relative to the top-level directory. Each is something a start depends
# on rather than something the packager happens to copy; see the module
# docstring for why this is its own list.
REQUIRED_FILES = (
    "LICENSE",
    "THIRD-PARTY-NOTICES.md",
    "backend/main.py",
    "backend/native/libgavel-x86_64-linux.so",
    "bin/tender-rom-launcher",
    "defaults/config.json",
    "dist/globals.js",
    "dist/index-coexistence.js",
    "dist/index.js",
    VERSION_FILE,
)

# Steam runs this as a shortcut's ``exe``. The copy a shortcut usually reaches
# is not this one: ``adapters/launcher_install.py`` writes it into the user's
# bin root at a mode of its own, so the mode shipped here decides nothing on
# that path. It decides on the other one — where that install fails,
# ``bootstrap/adapters.py`` points the shortcut at this copy in the unpacked
# tree instead, and it is then run as it arrived. That is the case with no other
# defence, which is why the mode is asserted here.
EXECUTABLE_FILES = frozenset({"bin/tender-rom-launcher"})

# A path segment that may not appear anywhere in the archive, and a name that
# may not stand as a member's last segment — both read from the other side of
# the packager, so finding one means the archive was not produced by it, or the
# packager stopped keeping it out.
#
# The segments come from two different mechanisms there. ``node_modules``,
# ``__pycache__`` and ``.venv`` are pruned out of the staged copy wherever they
# turn up (``PRUNE_DIRS``); ``tests``, ``frontend``, ``docs`` and ``scripts``
# are never copied into it at all, because ``SHIPPED`` does not name them. The
# names below are that script's ``PRUNE_FILES`` plus ``.git*``.
#
# All seven segments are refused at EVERY depth, while the four uncopied ones
# are only absent from the TOP of the packager's tree. A vendored dependency
# carrying its own ``docs/`` or ``tests/`` would therefore be refused here
# rather than shipped — deliberately, so that whether such a tree should be
# pruned or kept is a question asked out loud once instead of answered by
# whichever list it happened to fall through. The same strictness applies to
# the KIND of member: the packager prunes the names as files and the segments
# as directories, and this gate refuses either shape under either list.
BANNED_SEGMENTS = frozenset({"tests", "frontend", "node_modules", "__pycache__", ".venv", "docs", "scripts"})
BANNED_NAMES = ("*.map", "*.lock", "*.pyc", "*.pyo", "settings.json", "requirements-dev.*", ".git*")


def relative_name(name: str) -> str | None:
    """A member's path beneath the top-level directory, or ``None`` where it lies outside.

    The top-level directory itself answers with the empty string, which is
    neither outside nor a path within it.
    """
    if name == TOP_LEVEL:
        return ""
    prefix = f"{TOP_LEVEL}/"
    return name[len(prefix) :] if name.startswith(prefix) else None


def member_kind(member: tarfile.TarInfo) -> str:
    """What a member is, for a finding that has to say why it may not be there."""
    if member.issym():
        return "a symbolic link"
    if member.islnk():
        return "a hard link"
    return f"a tar entry of type {member.type!r}"


def digest_of(path: pathlib.Path) -> str:
    """SHA-256 of ``path``, as lowercase hex."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def layout_findings(members: list[tarfile.TarInfo]) -> list[str]:
    """Any way the archive is not one ``romm-tender/`` directory and nothing else.

    The installer unpacks with ``--strip-components=1``, so a second top-level
    entry lands beside the code root's CONTENTS rather than beside the code
    root. The name is a separate question, and stripping does not answer it:
    it removes whatever first component it finds, so an archive rooted in
    ``tender/`` would unpack exactly as well. What pins the name is the
    packager, which stages the tree under ``romm-tender/`` whatever the
    checkout is called — so another root is an archive it did not write.
    """
    if not members:
        return ["the archive is empty"]
    findings = [f"{member.name}: outside {TOP_LEVEL}/" for member in members if relative_name(member.name) is None]
    root = next((member for member in members if member.name == TOP_LEVEL), None)
    if root is None:
        findings.append(f"no {TOP_LEVEL}/ directory — scripts/package.sh stages the tree under that name")
    elif not root.isdir():
        findings.append(f"{TOP_LEVEL}: not a directory")
    return findings


def required_findings(members: list[tarfile.TarInfo]) -> list[str]:
    """Every required path that is not a file in the archive, or is one without its mode."""
    files = {
        relative: member
        for member in members
        if member.isfile() and (relative := relative_name(member.name)) is not None
    }
    findings: list[str] = []
    for relative in REQUIRED_FILES:
        member = files.get(relative)
        if member is None:
            findings.append(f"{TOP_LEVEL}/{relative}: missing")
        elif relative in EXECUTABLE_FILES and not member.mode & 0o111:
            findings.append(f"{TOP_LEVEL}/{relative}: mode {member.mode:04o}, not executable")
    return findings


def forbidden_findings(members: list[tarfile.TarInfo]) -> list[str]:
    """Every member that may not be in the archive, and every reason it may not.

    A member is reported once per rule it breaks rather than once: a symbolic
    link under a pruned directory is two different problems, and answering with
    the first would hide the second until it was fixed.
    """
    findings: list[str] = []
    for member in members:
        name = member.name
        segments = name.split("/")
        if name.startswith("/"):
            findings.append(f"{name}: an absolute path")
        if ".." in segments:
            findings.append(f"{name}: a '..' segment")
        if not member.isfile() and not member.isdir():
            findings.append(f"{name}: {member_kind(member)}, not a file or a directory")
        findings.extend(
            f"{name}: a '{segment}' path segment" for segment in sorted(BANNED_SEGMENTS.intersection(segments))
        )
        findings.extend(
            f"{name}: a name the packager prunes ({pattern})"
            for pattern in BANNED_NAMES
            if fnmatch.fnmatch(segments[-1], pattern)
        )
    return findings


def stated_version(tar: tarfile.TarFile, members: list[tarfile.TarInfo]) -> tuple[str | None, list[str]]:
    """The version the archive states, or ``None`` with any finding about reading it.

    ``None`` and no finding means there is no ``version.txt`` to read, which
    :func:`required_findings` has already answered for — saying so twice would
    report one missing file as two problems.
    """
    member = next(
        (member for member in members if member.isfile() and relative_name(member.name) == VERSION_FILE),
        None,
    )
    if member is None:
        return None, []
    # ``extractfile`` answers None for a member that is not a regular file,
    # which the search above has already excluded.
    payload = tar.extractfile(member)
    data = payload.read() if payload is not None else b""
    try:
        return data.decode("utf-8").strip(), []
    except UnicodeDecodeError as error:
        return None, [f"{member.name}: not UTF-8 text — {error}"]


def version_findings(
    tar: tarfile.TarFile, members: list[tarfile.TarInfo], archive_name: str, tag: str | None
) -> list[str]:
    """Any way the version in the archive, in its name and in the tag fail to agree.

    All three are the same number by three routes — release-please stamps
    ``version.txt``, the packager reads it into the archive's name, and the
    installer reads the tag — so holding them against each other is what catches
    a release built from the wrong tree.
    """
    match = ARCHIVE_NAME_RE.match(archive_name)
    findings: list[str] = []
    if match is None:
        findings.append(f"{archive_name}: not named {TOP_LEVEL}-<version>.tar.gz")
    if tag is not None and not tag.startswith(TAG_PREFIX):
        findings.append(f"{tag}: not a tag of ours, which the installer reads a version off as {TAG_PREFIX}<version>")

    stated, read_findings = stated_version(tar, members)
    findings.extend(read_findings)
    if stated is None:
        return findings
    if match is not None and stated != match.group(1):
        findings.append(f"{VERSION_FILE} says {stated!r}, the archive is named for {match.group(1)!r}")
    if tag is not None and tag.startswith(TAG_PREFIX) and stated != tag.removeprefix(TAG_PREFIX):
        findings.append(f"{VERSION_FILE} says {stated!r}, the tag {tag} names {tag.removeprefix(TAG_PREFIX)!r}")
    return findings


def sidecar_findings(archive: pathlib.Path) -> list[str]:
    """Any way the ``.sha256`` beside the archive is not what ``sha256sum -c`` needs.

    The installer runs that command from the directory it downloaded both into,
    so the name on the line is resolved against a working directory this side
    knows nothing about: a bare name is the only spelling that verifies
    anywhere, and a line for a second file is a second download the installer
    never made.
    """
    sidecar = archive.with_name(archive.name + SIDECAR_SUFFIX)
    if not sidecar.is_file():
        return [f"{sidecar.name}: missing — the installer refuses a release it cannot verify"]
    try:
        text = sidecar.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{sidecar.name}: cannot be read — {error}"]
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return [f"{sidecar.name}: carries {len(lines)} lines, and one archive is checked by one line"]
    match = SIDECAR_LINE_RE.match(lines[0])
    if match is None:
        return [f"{sidecar.name}: not a sha256sum line: {lines[0]!r}"]
    digest, named = match.group(1), match.group(2)

    findings: list[str] = []
    if "/" in named:
        findings.append(f"{sidecar.name}: names the path {named!r} rather than the bare {archive.name!r}")
    elif named != archive.name:
        findings.append(f"{sidecar.name}: names {named!r}, not {archive.name!r}")
    actual = digest_of(archive)
    if actual != digest:
        findings.append(f"{sidecar.name}: digest {digest} != the archive's {actual}")
    return findings


def collect_findings(archive: pathlib.Path, tag: str | None) -> list[str]:
    """Every way ``archive`` is not what ``install.sh`` expects, in stable order.

    A path that is not there is one finding rather than a crash, because the
    callers pass a shell glob: an unmatched pattern arrives here as its own
    literal text, and refusing it is what keeps a build that produced no tarball
    from passing this step.
    """
    if not archive.is_file():
        return [f"{archive}: no such file"]
    try:
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            findings = layout_findings(members)
            findings.extend(required_findings(members))
            findings.extend(forbidden_findings(members))
            findings.extend(version_findings(tar, members, archive.name, tag))
    except tarfile.TarError as error:
        return [f"{archive.name}: not a readable tar archive — {error}"]
    findings.extend(sidecar_findings(archive))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a release tarball against what install.sh expects.")
    parser.add_argument("archive", type=pathlib.Path, help="the packed tarball; its .sha256 is read from beside it")
    parser.add_argument("--tag", help=f"the release tag the archive is published under, as {TAG_PREFIX}<version>")
    arguments = parser.parse_args(argv)

    findings = collect_findings(arguments.archive, arguments.tag)
    if findings:
        print(f"ERROR: {arguments.archive} is not the tarball install.sh expects:", file=sys.stderr)
        for line in findings:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nThe tarball is assembled by scripts/package.sh and never edited by hand,\n"
            "so a finding here is a change on one of the two sides that have to agree:\n"
            "what the packager ships, or what the installer and the unit start from.\n"
            "Rebuild the pair with `mise run package` and fix the side that moved.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {arguments.archive} is the tarball install.sh expects.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
