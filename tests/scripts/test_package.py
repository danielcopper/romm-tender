"""Tests for ``scripts/package.sh`` — the release tarball's one producer.

Every case runs the real script over a synthetic checkout under ``tmp_path``:
what the script is for is the archive it writes, and its two failure modes are a
whole entry missing and a build that has not happened.

The synthetic tree carries the shipped layout plus a decoy for each rule the
script prunes by, because "the tarball has what it should" and "the tarball has
nothing else" are two different assertions and only the second one catches a
shipped credential or a vendored dependency tree.
"""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "package.sh"

# Every path the tarball must carry. The three vendored/compiled ones are here
# because they live several levels down inside ``backend/`` and are exactly the
# kind of file a copy rule written by extension would lose.
_REQUIRED = (
    "backend/main.py",
    "backend/native/libgavel-x86_64-linux.so",
    "backend/_vendor/atlas/__init__.py",
    "backend/_vendor/atlas/data/systems.json",
    # Ships despite ending in the name of a file that never ships. The prune is
    # by exact name, and widening it to a glob would take this with it.
    "backend/_vendor/atlas/data/emulator_settings.json",
    "backend/db/migrations/001_initial.sql",
    "dist/index.js",
    "dist/globals.js",
    "dist/index-coexistence.js",
    "bin/tender-rom-launcher",
    "defaults/config.json",
    "version.txt",
    "LICENSE",
    "THIRD-PARTY-NOTICES.md",
)

# One per rule the script prunes by: a whole directory that never ships, a
# compiled module below a shipped one, a source map beside a shipped bundle, a
# dependency lock, a user's own settings, and another package's manifest.
_DECOYS = (
    "tests/x.py",
    "frontend/package.json",
    "frontend/node_modules/left-pad/index.js",
    "backend/__pycache__/y.pyc",
    "dist/index.js.map",
    "requirements-dev.lock",
    "settings.json",
    "docs/index.md",
    "scripts/package.sh",
)


def _checkout(tmp_path: Path, *, version: str = "1.2.3") -> Path:
    """A minimal tree shaped like this repository, with a decoy for every prune rule."""
    source = tmp_path / "checkout"
    for relative in (*_REQUIRED, *_DECOYS):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{relative}\n", encoding="utf-8")
    (source / "version.txt").write_text(f"{version}\n", encoding="utf-8")
    (source / "bin" / "tender-rom-launcher").chmod(0o755)
    return source


def _package(source: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT), "--source", str(source), "--out", str(out), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def _refusals(stderr: str) -> list[str]:
    """Every line on which this script refused — one per abort, by its prefix.

    Counted rather than found: a refusal that ends a subshell instead of the run
    prints its message and lets the caller carry on with an empty answer, which
    refuses again further down. Both spellings exit 1, so only the count tells
    them apart.
    """
    return [line for line in stderr.splitlines() if line.startswith("package.sh: ")]


def _names(archive: Path) -> list[str]:
    with tarfile.open(archive) as tar:
        return tar.getnames()


class TestWhatItProduces:
    def test_the_archive_and_its_sidecar_are_named_after_the_version(self, tmp_path):
        result = _package(_checkout(tmp_path), tmp_path / "out")

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "out" / "romm-tender-1.2.3.tar.gz").is_file()
        assert (tmp_path / "out" / "romm-tender-1.2.3.tar.gz.sha256").is_file()

    def test_the_version_can_be_named_instead_of_read(self, tmp_path):
        _package(_checkout(tmp_path), tmp_path / "out", "--version", "9.9.9")

        assert (tmp_path / "out" / "romm-tender-9.9.9.tar.gz").is_file()

    def test_a_version_that_looks_like_a_flag_still_reaches_the_option(self, tmp_path):
        """Edge: ``echo`` would have swallowed ``-n``, and version.txt would have answered instead."""
        _package(_checkout(tmp_path), tmp_path / "out", "--version", "-n")

        assert (tmp_path / "out" / "romm-tender--n.tar.gz").is_file()
        assert not (tmp_path / "out" / "romm-tender-1.2.3.tar.gz").exists()

    def test_there_is_exactly_one_top_level_directory_and_it_is_romm_tender(self, tmp_path):
        """One entry, because the installer strips one component; this name, because the packager stages it."""
        _package(_checkout(tmp_path), tmp_path / "out")

        tops = {name.split("/")[0] for name in _names(tmp_path / "out" / "romm-tender-1.2.3.tar.gz")}
        assert tops == {"romm-tender"}

    def test_every_required_entry_is_in_it(self, tmp_path):
        _package(_checkout(tmp_path), tmp_path / "out")

        names = set(_names(tmp_path / "out" / "romm-tender-1.2.3.tar.gz"))
        assert {f"romm-tender/{entry}" for entry in _REQUIRED} <= names

    def test_the_launcher_is_executable_inside_the_archive(self, tmp_path):
        """Steam runs it as a shortcut's exe; a mode lost in packaging is a library that will not start."""
        _package(_checkout(tmp_path), tmp_path / "out")

        with tarfile.open(tmp_path / "out" / "romm-tender-1.2.3.tar.gz") as tar:
            member = tar.getmember("romm-tender/bin/tender-rom-launcher")
        assert member.mode & 0o111

    def test_the_sidecar_matches_the_archive_and_names_it_barely(self, tmp_path):
        """``sha256sum -c`` resolves the name against the working directory, so a path would not verify."""
        _package(_checkout(tmp_path), tmp_path / "out")
        archive = tmp_path / "out" / "romm-tender-1.2.3.tar.gz"

        digest, name = (tmp_path / "out" / "romm-tender-1.2.3.tar.gz.sha256").read_text(encoding="utf-8").split()
        assert name == "romm-tender-1.2.3.tar.gz"
        assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_two_runs_over_one_tree_produce_identical_bytes(self, tmp_path):
        """Deterministic: every timestamp, owner and order in the archive is fixed."""
        source = _checkout(tmp_path)
        _package(source, tmp_path / "one")
        _package(source, tmp_path / "two")

        assert (tmp_path / "one" / "romm-tender-1.2.3.tar.gz").read_bytes() == (
            tmp_path / "two" / "romm-tender-1.2.3.tar.gz"
        ).read_bytes()


class TestWhatItRefusesToShip:
    def test_no_decoy_reaches_the_archive(self, tmp_path):
        """Each decoy by its exact archive path, never by a suffix.

        A suffix test cannot tell ``settings.json`` from the vendored
        ``emulator_settings.json`` that has to ship, so it would fail on a
        correct archive while a prune widened to ``*settings.json`` — which
        takes the vendored file with it — is what the required set catches.
        """
        _package(_checkout(tmp_path), tmp_path / "out")

        names = set(_names(tmp_path / "out" / "romm-tender-1.2.3.tar.gz"))
        for decoy in _DECOYS:
            assert f"romm-tender/{decoy}" not in names, decoy

    def test_nothing_under_a_pruned_directory_survives(self, tmp_path):
        """The prune is by shape, so it has to reach a nested directory, not just a top-level one."""
        _package(_checkout(tmp_path), tmp_path / "out")

        names = _names(tmp_path / "out" / "romm-tender-1.2.3.tar.gz")
        assert not [name for name in names if "__pycache__" in name or "node_modules" in name]


class TestWhatItRefusesToDo:
    def test_it_refuses_without_a_built_frontend(self, tmp_path):
        """The one failure a developer meets: the packager builds nothing itself."""
        source = _checkout(tmp_path)
        (source / "dist" / "index.js").unlink()

        result = _package(source, tmp_path / "out")

        assert result.returncode == 1
        assert "build the frontend first" in result.stderr
        assert not (tmp_path / "out").exists()

    def test_it_refuses_when_a_shipped_entry_is_missing(self, tmp_path):
        source = _checkout(tmp_path)
        (source / "LICENSE").unlink()

        result = _package(source, tmp_path / "out")

        assert result.returncode == 1
        assert "has no LICENSE" in result.stderr

    def test_it_refuses_a_source_directory_that_is_not_there(self, tmp_path):
        result = _package(tmp_path / "nowhere", tmp_path / "out")

        assert result.returncode == 1
        assert "no such source directory" in result.stderr

    def test_it_refuses_when_the_version_file_says_nothing(self, tmp_path):
        source = _checkout(tmp_path)
        (source / "version.txt").write_text("\n", encoding="utf-8")

        result = _package(source, tmp_path / "out")

        assert result.returncode == 1
        assert _refusals(result.stderr) == [f"package.sh: {source}/version.txt is empty"]

    def test_it_refuses_when_there_is_no_version_file_at_all(self, tmp_path):
        """A different answer from an empty one, so the two are two messages."""
        source = _checkout(tmp_path)
        (source / "version.txt").unlink()

        result = _package(source, tmp_path / "out")

        assert result.returncode == 1
        assert _refusals(result.stderr) == [
            f"package.sh: no --version given and no {source}/version.txt to read one from"
        ]

    @pytest.mark.parametrize("flag", ["--source", "--out", "--version"])
    def test_an_option_with_no_value_is_refused_once_by_name(self, tmp_path, flag):
        """Refused by name, once, before anything is read."""
        result = subprocess.run(
            ["bash", str(_SCRIPT), flag],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert _refusals(result.stderr) == [f"package.sh: {flag} needs a value"]

    @pytest.mark.parametrize("flag", ["--source", "--out", "--version"])
    def test_an_empty_value_is_not_a_value(self, tmp_path, flag):
        result = subprocess.run(
            ["bash", str(_SCRIPT), flag, "", "--source", str(_checkout(tmp_path))],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert _refusals(result.stderr) == [f"package.sh: {flag} needs a value"]
