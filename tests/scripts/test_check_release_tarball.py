"""Tests for ``scripts/check_release_tarball.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path`` (and is excluded from ruff/basedpyright).

Every case about an archive's CONTENTS starts from a REAL tarball, produced by
the real ``scripts/package.sh`` from a synthetic checkout under ``tmp_path`` —
the same way ``test_package.py`` builds one — and then rewrites it into the
archive the case is about. A fixture that assembled a tar of its own would be
asserting against a layout this file invented, and the layout is precisely what
is under test: the whole point of the gate is that the packager and the
installer agree about a file neither of them can see from the other side.

The exception is the three cases in :class:`TestWhatTheRunAnswersWith` that ask
what the run does when there is no archive to read — a path nothing matched, an
archive with no members, bytes that are not a tar. A packed tarball is the one
thing those may not start from, so they write what they need themselves.

The checkout is laid out from the gate's own ``REQUIRED_FILES``, so a path added
there is a path the fixture ships, and the parametrized missing-file case covers
it without being told. What that derivation cannot say is whether the list is
RIGHT — a path the installer needs and nobody listed is missing from both sides
and every test here passes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from types import ModuleType

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO / "scripts" / "check_release_tarball.py"
_PACKAGE = _REPO / "scripts" / "package.sh"

_VERSION = "1.2.3"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_tarball", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_ROOT = check.TOP_LEVEL
_TAG = f"{check.TAG_PREFIX}{_VERSION}"

# One member name per banned pattern, spelled out rather than generated: a name
# derived from the pattern would match it by construction, which is the one
# thing a test of a pattern may not do.
_BANNED_NAME_CASES = (
    "index.js.map",
    "pnpm-lock.lock",
    "module.pyc",
    "module.pyo",
    "settings.json",
    "requirements-dev.txt",
    ".gitignore",
)


@pytest.fixture(scope="module")
def good(tmp_path_factory) -> Path:
    """A release tarball as the packager writes it, with its sidecar beside it.

    Module-scoped because it costs a ``tar`` and a ``gzip`` run and no case
    mutates it — each one rewrites it into a directory of its own.
    """
    base = tmp_path_factory.mktemp("packaged")
    source = base / "checkout"
    for relative in check.REQUIRED_FILES:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{relative}\n", encoding="utf-8")
    (source / check.VERSION_FILE).write_text(f"{_VERSION}\n", encoding="utf-8")
    for relative in check.EXECUTABLE_FILES:
        (source / relative).chmod(0o755)

    subprocess.run(
        ["bash", str(_PACKAGE), "--source", str(source), "--out", str(base / "built")],
        capture_output=True,
        text=True,
        check=True,
    )
    return base / "built" / f"{_ROOT}-{_VERSION}.tar.gz"


def _write_sidecar(archive: Path) -> None:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + check.SIDECAR_SUFFIX).write_text(f"{digest}  {archive.name}\n", encoding="utf-8")


def _repack(
    archive: Path,
    out_dir: Path,
    *,
    drop: Iterable[str] = (),
    add: Mapping[str, bytes] | None = None,
    rename: Mapping[str, str] | None = None,
    modes: Mapping[str, int] | None = None,
    symlinks: Mapping[str, str] | None = None,
    hardlinks: Mapping[str, str] | None = None,
    directories: Iterable[str] = (),
    name: str | None = None,
    sidecar: str = "rewrite",
) -> Path:
    """``archive`` rewritten into ``out_dir`` with the changes a case needs.

    ``drop``, ``rename`` and ``modes`` are keyed by the member's name in the
    SOURCE archive, so a case says what it is changing rather than what it
    changed it into. ``sidecar`` is ``rewrite`` for one that matches the result,
    ``stale`` to carry the source's over unchanged, and ``none`` to write none.
    """
    dropped = set(drop)
    add = add or {}
    rename = rename or {}
    modes = modes or {}
    symlinks = symlinks or {}
    hardlinks = hardlinks or {}

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (name or archive.name)
    with tarfile.open(archive) as source, tarfile.open(target, "w:gz") as out:
        for member in source.getmembers():
            original = member.name
            if original in dropped:
                continue
            payload = source.extractfile(member) if member.isfile() else None
            if original in modes:
                member.mode = modes[original]
            member.name = rename.get(original, original)
            out.addfile(member, payload)
        for path, content in add.items():
            info = tarfile.TarInfo(path)
            info.size = len(content)
            out.addfile(info, io.BytesIO(content))
        for path, link in symlinks.items():
            info = tarfile.TarInfo(path)
            info.type = tarfile.SYMTYPE
            info.linkname = link
            out.addfile(info)
        for path, link in hardlinks.items():
            info = tarfile.TarInfo(path)
            info.type = tarfile.LNKTYPE
            info.linkname = link
            out.addfile(info)
        for path in directories:
            info = tarfile.TarInfo(path)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            out.addfile(info)

    if sidecar == "rewrite":
        _write_sidecar(target)
    elif sidecar == "stale":
        shutil.copy(
            archive.with_name(archive.name + check.SIDECAR_SUFFIX), target.with_name(target.name + check.SIDECAR_SUFFIX)
        )
    return target


def _place(archive: Path, out_dir: Path, *, line: str | None) -> Path:
    """The good archive alone in ``out_dir``, with the sidecar line this case needs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / archive.name
    shutil.copy(archive, target)
    if line is not None:
        target.with_name(target.name + check.SIDECAR_SUFFIX).write_text(line, encoding="utf-8")
    return target


def _digest(archive: Path) -> str:
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def _findings(archive: Path, tag: str | None = None) -> list[str]:
    return check.collect_findings(archive, tag)


def _one(findings: list[str], needle: str) -> str:
    """The single finding carrying ``needle``, or a failure naming what was found instead."""
    matches = [line for line in findings if needle in line]
    assert len(matches) == 1, f"expected one finding containing {needle!r}, got {findings}"
    return matches[0]


class TestTheTarballThePackagerWrites:
    def test_it_has_nothing_against_it(self, good):
        assert _findings(good) == []

    def test_it_has_nothing_against_it_under_its_own_tag(self, good):
        assert _findings(good, _TAG) == []

    def test_main_accepts_it(self, good, capsys):
        assert check.main([str(good), "--tag", _TAG]) == 0
        assert "OK" in capsys.readouterr().out


class TestTheTopLevelDirectory:
    def test_a_second_top_level_entry_is_outside_it(self, good, tmp_path):
        archive = _repack(good, tmp_path / "out", add={"elsewhere/notes.txt": b"hi\n"})

        assert _one(_findings(archive), "elsewhere/notes.txt") == f"elsewhere/notes.txt: outside {_ROOT}/"

    def test_a_top_level_under_another_name_is_not_this_one(self, good, tmp_path):
        with tarfile.open(good) as tar:
            renamed = {name: name.replace(_ROOT, "tender", 1) for name in tar.getnames()}
        archive = _repack(good, tmp_path / "out", rename=renamed)

        findings = _findings(archive)
        assert f"tender: outside {_ROOT}/" in findings
        assert f"no {_ROOT}/ directory" in _one(findings, "directory")

    def test_the_top_level_directory_itself_has_to_be_in_it(self, good, tmp_path):
        """The packager's ``tar`` writes that directory as a member of its own; an archive lacking it is not its."""
        archive = _repack(good, tmp_path / "out", drop=[_ROOT])

        assert f"no {_ROOT}/ directory" in _one(_findings(archive), "directory")

    def test_a_top_level_that_is_not_a_directory(self, good, tmp_path):
        archive = _repack(good, tmp_path / "out", drop=[_ROOT], add={_ROOT: b"not a directory\n"})

        assert _one(_findings(archive), "not a directory") == f"{_ROOT}: not a directory"


class TestTheFilesAnInstallStartsFrom:
    @pytest.mark.parametrize("relative", check.REQUIRED_FILES)
    def test_each_required_file_is_missed_on_its_own(self, good, tmp_path, relative):
        archive = _repack(good, tmp_path / "out", drop=[f"{_ROOT}/{relative}"])

        assert f"{_ROOT}/{relative}: missing" in _findings(archive)

    def test_the_launcher_without_its_mode_is_a_finding(self, good, tmp_path):
        """Why the shipped mode is asserted at all — the fallback path runs this copy — is at ``EXECUTABLE_FILES``."""
        launcher = f"{_ROOT}/bin/tender-rom-launcher"
        archive = _repack(good, tmp_path / "out", modes={launcher: 0o644})

        assert _one(_findings(archive), "not executable") == f"{launcher}: mode 0644, not executable"

    def test_the_launcher_with_its_mode_is_not(self, good, tmp_path):
        launcher = f"{_ROOT}/bin/tender-rom-launcher"
        archive = _repack(good, tmp_path / "out", modes={launcher: 0o755})

        assert _findings(archive) == []


class TestWhatMayNotBeInIt:
    @pytest.mark.parametrize("segment", sorted(check.BANNED_SEGMENTS))
    def test_each_banned_directory_is_caught_by_its_segment(self, good, tmp_path, segment):
        member = f"{_ROOT}/{segment}/carried.txt"
        archive = _repack(good, tmp_path / "out", add={member: b"x\n"})

        assert _one(_findings(archive), "path segment") == f"{member}: a '{segment}' path segment"

    @pytest.mark.parametrize("filename", _BANNED_NAME_CASES)
    def test_each_pruned_name_is_caught_wherever_it_sits(self, good, tmp_path, filename):
        member = f"{_ROOT}/backend/{filename}"
        archive = _repack(good, tmp_path / "out", add={member: b"x\n"})

        assert _one(_findings(archive), "the packager prunes").startswith(f"{member}: a name the packager prunes")

    def test_a_banned_name_standing_as_a_directory_is_caught_too(self, good, tmp_path):
        """The rule is about the last segment of a member's path, whatever the member is."""
        member = f"{_ROOT}/backend/settings.json"
        archive = _repack(good, tmp_path / "out", directories=[member])

        assert _one(_findings(archive), "the packager prunes") == (
            f"{member}: a name the packager prunes (settings.json)"
        )

    def test_a_symbolic_link_is_not_a_file(self, good, tmp_path):
        """The installer unpacks this archive, so a link in it is a write it did not choose."""
        archive = _repack(good, tmp_path / "out", symlinks={f"{_ROOT}/backend/main.cfg": "/etc/passwd"})

        assert (
            _one(_findings(archive), "symbolic link")
            == f"{_ROOT}/backend/main.cfg: a symbolic link, not a file or a directory"
        )

    def test_a_hard_link_is_not_a_file_either(self, good, tmp_path):
        """Named as what it is: a member that carries no bytes of its own answers differently to a reader."""
        member = f"{_ROOT}/backend/second-main.py"
        archive = _repack(good, tmp_path / "out", hardlinks={member: f"{_ROOT}/backend/main.py"})

        assert _one(_findings(archive), "hard link") == f"{member}: a hard link, not a file or a directory"

    def test_an_absolute_path_is_a_finding(self, good, tmp_path):
        archive = _repack(good, tmp_path / "out", add={"/etc/passwd": b"x\n"})

        assert "/etc/passwd: an absolute path" in _findings(archive)

    def test_a_dot_dot_segment_is_a_finding(self, good, tmp_path):
        archive = _repack(good, tmp_path / "out", add={f"{_ROOT}/../escape.txt": b"x\n"})

        assert f"{_ROOT}/../escape.txt: a '..' segment" in _findings(archive)


class TestTheVersionThreeThingsHaveToAgreeOn:
    def test_the_archive_name_is_held_against_what_is_in_it(self, good, tmp_path):
        archive = _repack(good, tmp_path / "out", name=f"{_ROOT}-9.9.9.tar.gz")

        assert (
            _one(_findings(archive), "named for")
            == f"{check.VERSION_FILE} says '{_VERSION}', the archive is named for '9.9.9'"
        )

    def test_an_archive_name_of_another_shape_is_a_finding(self, good, tmp_path):
        archive = _repack(good, tmp_path / "out", name="tender.tar.gz")

        assert _one(_findings(archive), "not named") == f"tender.tar.gz: not named {_ROOT}-<version>.tar.gz"

    def test_a_version_file_that_is_not_text_is_a_finding(self, good, tmp_path):
        """Stated rather than read past: an unreadable version compares equal to nothing."""
        version = f"{_ROOT}/{check.VERSION_FILE}"
        archive = _repack(good, tmp_path / "out", drop=[version], add={version: b"\xff\xfe\n"})

        assert _one(_findings(archive), "not UTF-8 text").startswith(f"{version}: not UTF-8 text")

    def test_the_tag_is_held_against_what_is_in_it(self, good):
        findings = _findings(good, f"{check.TAG_PREFIX}9.9.9")

        assert _one(findings, "the tag") == (
            f"{check.VERSION_FILE} says '{_VERSION}', the tag {check.TAG_PREFIX}9.9.9 names '9.9.9'"
        )

    def test_a_tag_that_is_not_one_of_ours_is_a_finding(self, good):
        findings = _findings(good, f"v{_VERSION}")

        assert _one(findings, "not a tag of ours").startswith(f"v{_VERSION}: not a tag of ours")

    def test_a_tag_that_is_not_one_of_ours_is_not_also_read_for_a_version(self, good):
        """It carries no version to compare, so the comparison is withheld rather than guessed."""
        assert len(_findings(good, f"v{_VERSION}")) == 1


class TestTheSidecar:
    def test_a_missing_one_is_a_finding(self, good, tmp_path):
        archive = _place(good, tmp_path / "out", line=None)

        assert _one(_findings(archive), "missing").startswith(f"{archive.name}{check.SIDECAR_SUFFIX}: missing")

    def test_the_binary_mode_spelling_is_accepted(self, good, tmp_path):
        """``sha256sum -b`` writes ``<digest> *<name>``, and ``-c`` reads both spellings."""
        archive = _place(good, tmp_path / "out", line=f"{_digest(good)} *{good.name}\n")

        assert _findings(archive) == []

    def test_a_digest_that_does_not_match_is_a_finding(self, good, tmp_path):
        archive = _place(good, tmp_path / "out", line=f"{'0' * 64}  {good.name}\n")

        assert _one(_findings(archive), "digest").endswith(f"!= the archive's {_digest(good)}")

    def test_a_name_with_a_path_in_it_is_a_finding(self, good, tmp_path):
        """``sha256sum -c`` resolves the name against the working directory, not against the sidecar."""
        archive = _place(good, tmp_path / "out", line=f"{_digest(good)}  build/{good.name}\n")

        assert _one(_findings(archive), "names the path").endswith(f"rather than the bare '{good.name}'")

    def test_a_name_that_is_another_file_is_a_finding(self, good, tmp_path):
        archive = _place(good, tmp_path / "out", line=f"{_digest(good)}  other.tar.gz\n")

        assert _one(_findings(archive), "names 'other.tar.gz'").endswith(f"not '{good.name}'")

    def test_a_second_line_is_a_finding(self, good, tmp_path):
        line = f"{_digest(good)}  {good.name}\n"
        archive = _place(good, tmp_path / "out", line=line + f"{'0' * 64}  other.tar.gz\n")

        assert _one(_findings(archive), "lines").startswith(f"{archive.name}{check.SIDECAR_SUFFIX}: carries 2 lines")

    def test_text_that_is_not_a_sha256sum_line_is_a_finding(self, good, tmp_path):
        archive = _place(good, tmp_path / "out", line="looks nothing like a checksum\n")

        assert _one(_findings(archive), "not a sha256sum line")

    def test_one_the_packager_wrote_for_another_archive_is_a_finding(self, good, tmp_path):
        """What a rebuild leaves behind: a sidecar beside an archive it no longer describes."""
        archive = _repack(good, tmp_path / "out", add={"elsewhere/x": b"x\n"}, sidecar="stale")

        assert _one(_findings(archive), "digest")


class TestWhatTheRunAnswersWith:
    def test_a_path_that_is_not_there_is_refused(self, tmp_path):
        """CI passes a shell glob, and an unmatched one arrives here as its own text."""
        missing = tmp_path / f"{_ROOT}-*.tar.gz"

        assert _findings(missing) == [f"{missing}: no such file"]
        assert check.main([str(missing)]) == 1

    def test_an_archive_with_nothing_in_it_is_refused(self, tmp_path):
        """The one fact behind every other finding an empty archive draws.

        A rule that walks members has nothing to walk, and the required list
        answers by naming every path in it one at a time — which reads as a
        packaging fault rather than as an archive carrying nothing at all.
        """
        archive = tmp_path / f"{_ROOT}-{_VERSION}.tar.gz"
        with tarfile.open(archive, "w:gz"):
            pass
        _write_sidecar(archive)

        assert "the archive is empty" in _findings(archive)

    def test_something_that_is_not_a_tar_archive_is_refused(self, tmp_path):
        archive = tmp_path / f"{_ROOT}-{_VERSION}.tar.gz"
        archive.write_bytes(b"not a tarball at all")

        assert _one(_findings(archive), "not a readable tar archive")

    def test_every_finding_is_reported_at_once(self, good, tmp_path, capsys):
        """One fault per run would make a broken release take as many runs as it has faults."""
        archive = _repack(
            good,
            tmp_path / "out",
            drop=[f"{_ROOT}/LICENSE"],
            add={f"{_ROOT}/dist/index.js.map": b"x\n"},
        )

        assert check.main([str(archive)]) == 1
        reported = capsys.readouterr().err
        assert f"{_ROOT}/LICENSE: missing" in reported
        assert f"{_ROOT}/dist/index.js.map: a name the packager prunes" in reported
