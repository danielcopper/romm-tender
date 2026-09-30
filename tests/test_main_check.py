"""The installer's check: ``main.py --check`` builds the application on copies and touches nothing live.

Every case but the in-process ones runs the real entry point in a process of its
own, the way the installer does (``$PYTHON -B backend/main.py --check …``), over
a fake install laid out in the test's home: the live roots where the backend's
own ladder puts them when nothing names them, and the check's roots beside them
under ``tmp_path``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import main
from adapters.sqlite_migrations import apply_migrations

_MAIN = Path(__file__).resolve().parents[1] / "backend" / "main.py"

_ROOTS = (
    "TENDER_CODE_DIR",
    "TENDER_CONFIG_DIR",
    "TENDER_DATA_DIR",
    "TENDER_CACHE_DIR",
    "TENDER_STATE_DIR",
    "TENDER_BIN_DIR",
)


class _Machine:
    """A fake install in *home*, and the roots a check is pointed at under *tmp_path*."""

    def __init__(self, home: Path, tmp_path: Path) -> None:
        self.home = home
        self.live_data = home / ".local" / "share" / "romm-tender"
        self.live_config = home / ".config" / "romm-tender"
        self.live_state = home / ".local" / "state" / "romm-tender"
        self.live_cache = home / ".cache" / "romm-tender"
        self.launcher = home / ".local" / "bin" / "tender-rom-launcher"
        self.runtime = tmp_path / "run"
        self.check = tmp_path / "check"
        self.code = Path(__file__).resolve().parents[1]

    def lay_out(self) -> None:
        """The data a running install has: a migrated database, settings, a log, a cover, the launcher."""
        self.live_data.mkdir(parents=True)
        apply_migrations(str(self.live_data / "romm_sync.db"))
        db = sqlite3.connect(self.live_data / "romm_sync.db")
        with db:
            db.execute("CREATE TABLE marker (note TEXT)")
            db.execute("INSERT INTO marker VALUES ('in the file')")
        db.close()
        # Closed by the last connection, so nothing holds it open: no WAL, no index.
        assert sorted(path.name for path in self.live_data.iterdir()) == ["romm_sync.db"]
        self.live_config.mkdir(parents=True)
        (self.live_config / "settings.json").write_text('{"version": 13, "log_level": "warn"}\n', encoding="utf-8")
        self.live_state.mkdir(parents=True)
        (self.live_state / "backend.log").write_text("a line the running backend wrote\n", encoding="utf-8")
        (self.live_cache / "covers").mkdir(parents=True)
        (self.live_cache / "covers" / "1.png").write_bytes(b"\x89PNG a cover")
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("#!/bin/sh\n# the launcher the installed version put here\n", encoding="utf-8")
        self.launcher.chmod(0o700)
        self.runtime.mkdir()

    def env(self, **overrides: str) -> dict[str, str]:
        base = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "TENDER_CODE_DIR": str(self.code),
            "TENDER_CONFIG_DIR": str(self.check / "config"),
            "TENDER_DATA_DIR": str(self.check / "data"),
            "TENDER_CACHE_DIR": str(self.check / "cache"),
            "TENDER_STATE_DIR": str(self.check / "state"),
            "TENDER_BIN_DIR": str(self.check / "bin"),
        }
        base.update(overrides)
        return {name: value for name, value in base.items() if value}

    def run(self, *extra: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(_MAIN),
                "--check",
                "--data-from",
                str(self.live_data),
                "--config-from",
                str(self.live_config),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=self.env(**overrides),
            timeout=120,
        )


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Every name under *root*, with its mode and — for a file — its bytes."""
    seen: dict[str, tuple[int, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        seen[path.relative_to(root).as_posix()] = (mode, path.read_bytes() if stat.S_ISREG(mode) else None)
    return seen


@pytest.fixture
def machine(home: Path, tmp_path: Path) -> _Machine:
    laid_out = _Machine(home, tmp_path)
    laid_out.lay_out()
    return laid_out


class TestItBuildsOnCopies:
    def test_it_answers_zero_once_the_application_is_built(self, machine):
        result = machine.run()

        assert result.returncode == 0, result.stderr
        assert "check: " in result.stderr
        assert result.stdout == ""

    def test_the_live_install_is_left_exactly_as_it_was(self, machine):
        """Names, modes and bytes, the launcher among them — with no backend holding the database open."""
        before = _snapshot(machine.home)

        result = machine.run()

        assert result.returncode == 0, result.stderr
        assert _snapshot(machine.home) == before

    def test_the_copies_are_migrated_where_the_check_was_pointed(self, machine):
        result = machine.run()

        assert result.returncode == 0, result.stderr
        copy = sqlite3.connect(machine.check / "data" / "romm_sync.db")
        try:
            assert copy.execute("SELECT note FROM marker").fetchall() == [("in the file",)]
            assert copy.execute("PRAGMA user_version").fetchone()[0] > 0
        finally:
            copy.close()
        assert (machine.check / "config" / "settings.json").is_file()
        assert (machine.check / "bin" / "tender-rom-launcher").is_file()

    def test_it_takes_no_lock_writes_no_log_and_notes_no_port(self, machine):
        result = machine.run()

        assert result.returncode == 0, result.stderr
        assert not (machine.check / "data" / "backend.lock").exists()
        assert not (machine.check / "state").exists()
        assert list(machine.runtime.iterdir()) == []

    def test_a_first_install_has_nothing_to_copy_and_builds_all_the_same(self, machine, tmp_path):
        nowhere = tmp_path / "no-install-yet"

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(_MAIN),
                "--check",
                "--data-from",
                str(nowhere / "data"),
                "--config-from",
                str(nowhere / "config"),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=machine.env(),
            timeout=120,
        )

        assert result.returncode == 0, result.stderr
        assert "copied" not in result.stderr
        assert not nowhere.exists()


class TestADatabaseHeldOpen:
    """The running backend has the database open in WAL mode while the installer checks."""

    def test_what_its_wal_holds_is_in_the_copy_and_the_live_files_keep_their_bytes(self, machine):
        """The WAL index is left out of the byte comparison: every reader writes its read marks there."""
        live = sqlite3.connect(machine.live_data / "romm_sync.db")
        try:
            live.execute("PRAGMA wal_autocheckpoint=0")
            live.execute("INSERT INTO marker VALUES ('only in the wal')")
            live.commit()
            names = sorted(path.name for path in machine.live_data.iterdir())
            before = {name: (machine.live_data / name).read_bytes() for name in names if not name.endswith("-shm")}

            result = machine.run()

            assert result.returncode == 0, result.stderr
            assert sorted(path.name for path in machine.live_data.iterdir()) == names
            assert {name: (machine.live_data / name).read_bytes() for name in before} == before
        finally:
            live.close()
        copy = sqlite3.connect(machine.check / "data" / "romm_sync.db")
        try:
            assert copy.execute("SELECT note FROM marker ORDER BY rowid").fetchall() == [
                ("in the file",),
                ("only in the wal",),
            ]
        finally:
            copy.close()


class TestAVersionThatCannotBeBuilt:
    def test_a_migration_that_fails_on_the_copy_answers_non_zero_with_the_traceback(self, machine):
        """A table in the user's database that the first migration also creates: the build stops there."""
        (machine.live_data / "romm_sync.db").unlink()
        db = sqlite3.connect(machine.live_data / "romm_sync.db")
        with db:
            db.execute("CREATE TABLE roms (clashes TEXT)")
        db.close()
        before = _snapshot(machine.home)

        result = machine.run()

        assert result.returncode == 1
        assert "Traceback (most recent call last)" in result.stderr
        assert "could not be built" in result.stderr
        assert _snapshot(machine.home) == before

    def test_a_module_that_does_not_import_answers_non_zero_with_the_traceback(self, machine):
        """Python's own answer to a failed import, which is the one the installer relies on."""
        poisoned = (
            "import runpy, sys; "
            "sys.modules['bootstrap'] = None; "
            f"sys.argv = [{str(_MAIN)!r}, *sys.argv[1:]]; "
            f"runpy.run_path({str(_MAIN)!r}, run_name='__main__')"
        )

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                poisoned,
                "--check",
                "--data-from",
                str(machine.live_data),
                "--config-from",
                str(machine.live_config),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=machine.env(),
            timeout=120,
        )

        assert result.returncode == 1
        assert "Traceback (most recent call last)" in result.stderr
        assert "bootstrap" in result.stderr
        assert not machine.check.exists()


class TestItRefusesToBuildOnALiveRoot:
    @pytest.mark.parametrize("unset", _ROOTS)
    def test_a_root_the_environment_does_not_name_is_refused(self, machine, unset):
        before = _snapshot(machine.home)

        result = machine.run(**{unset: ""})

        assert result.returncode == 1
        assert f"check: refused, {unset} not set" in result.stderr
        assert _snapshot(machine.home) == before
        assert not machine.check.exists()

    @pytest.mark.parametrize(("root", "live"), [("TENDER_DATA_DIR", "live_data"), ("TENDER_CONFIG_DIR", "live_config")])
    def test_a_root_that_is_where_the_live_data_is_copied_from_is_refused(self, machine, root, live):
        before = _snapshot(machine.home)

        result = machine.run(**{root: str(getattr(machine, live))})

        assert result.returncode == 1
        assert "is where the live data is copied from" in result.stderr
        assert _snapshot(machine.home) == before


class TestTheEntryPoint:
    """In process: which of the two the entry point runs, and what a raising build answers."""

    def test_no_argument_runs_the_backend(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "argv", ["main.py"])
        monkeypatch.setattr(main, "run", lambda: 7)
        monkeypatch.setattr(main, "check", lambda argv: pytest.fail(f"the check ran with {argv}"))

        assert main.main() == 7

    def test_check_first_runs_the_check_with_what_follows_it(self, monkeypatch: pytest.MonkeyPatch):
        asked: list[list[str]] = []
        monkeypatch.setattr(sys, "argv", ["main.py", "--check", "--data-from", "/d", "--config-from", "/c"])
        monkeypatch.setattr(main, "run", lambda: pytest.fail("the backend ran"))
        monkeypatch.setattr(main, "check", lambda argv: asked.append(argv) or 0)

        assert main.main() == 0
        assert asked == [["--data-from", "/d", "--config-from", "/c"]]

    def test_a_build_that_raises_answers_one_and_logs_why(
        self, machine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        def broken(**_kwargs: object) -> None:
            raise RuntimeError("a service that cannot be built")

        for name, value in machine.env().items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(main, "build_application", broken)
        monkeypatch.setattr(main, "configure_stderr_logging", lambda: logging.getLogger("test_main_check"))

        with caplog.at_level(logging.ERROR, logger="test_main_check"):
            answer = main.check(["--data-from", str(machine.live_data), "--config-from", str(machine.live_config)])

        assert answer == 1
        raised = [record.exc_info[1] for record in caplog.records if record.exc_info is not None]
        assert [str(error) for error in raised] == ["a service that cannot be built"]
