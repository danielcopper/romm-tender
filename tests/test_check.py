"""The pre-install check: ``backend/check.py`` builds the application on copies and touches nothing live.

Every case but the in-process ones runs the real entry point in a process of its
own, the way the installer does (``$PYTHON -B backend/check.py …``), over a fake
install laid out in the test's home: the live roots where the backend's own
ladder puts them when nothing names them, and the check's roots beside them
under ``tmp_path``.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import check
from adapters.sqlite_migrations import apply_migrations

_CHECK = Path(__file__).resolve().parents[1] / "backend" / "check.py"

_DATABASE = "romm-tender.db"
_OLD_DATABASE = "romm_sync.db"

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
        self.live_runtime = tmp_path / "run"
        self.check = tmp_path / "check"
        self.code = Path(__file__).resolve().parents[1]

    def lay_out(self, database: str = _DATABASE) -> None:
        """The data a running install has: a migrated database, settings, a log, a cover, the launcher."""
        self.live_data.mkdir(parents=True)
        apply_migrations(str(self.live_data / database))
        db = sqlite3.connect(self.live_data / database)
        with db:
            db.execute("CREATE TABLE marker (note TEXT)")
            db.execute("INSERT INTO marker VALUES ('in the file')")
        db.close()
        # Closed by the last connection, so nothing holds it open: no WAL, no index.
        assert sorted(path.name for path in self.live_data.iterdir()) == [database]
        self.live_config.mkdir(parents=True)
        (self.live_config / "settings.json").write_text('{"version": 13, "log_level": "warn"}\n', encoding="utf-8")
        self.live_state.mkdir(parents=True)
        (self.live_state / "backend.log").write_text("a line the running backend wrote\n", encoding="utf-8")
        (self.live_cache / "covers").mkdir(parents=True)
        (self.live_cache / "covers" / "1.png").write_bytes(b"\x89PNG a cover")
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("#!/bin/sh\n# the launcher the installed version put here\n", encoding="utf-8")
        self.launcher.chmod(0o700)
        (self.live_runtime / "romm-tender").mkdir(parents=True)
        (self.live_runtime / "romm-tender" / "port").write_text("8123\n", encoding="utf-8")

    def snapshot(self) -> dict[str, dict[str, tuple[int, bytes | None]]]:
        """The whole live install: the home, and the runtime directory beside it."""
        return {"home": _snapshot(self.home), "runtime": _snapshot(self.live_runtime)}

    def env(self, **overrides: str) -> dict[str, str]:
        base = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "XDG_RUNTIME_DIR": str(self.check / "run"),
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
                str(_CHECK),
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

        assert result.returncode == check.BUILT == 0, result.stderr
        assert "check: " in result.stderr
        assert result.stdout == ""

    def test_the_live_install_is_left_exactly_as_it_was(self, machine):
        """Names, modes and bytes, the launcher and the port note among them — with no backend holding the database."""
        before = machine.snapshot()

        result = machine.run()

        assert result.returncode == 0, result.stderr
        assert machine.snapshot() == before

    def test_the_copies_are_migrated_where_the_check_was_pointed(self, machine):
        (machine.live_data / "save_sync_state.json").write_text('{"device_name": "deck"}\n', encoding="utf-8")

        result = machine.run()

        assert result.returncode == 0, result.stderr
        copy = sqlite3.connect(machine.check / "data" / _DATABASE)
        try:
            assert copy.execute("SELECT note FROM marker").fetchall() == [("in the file",)]
            assert copy.execute("PRAGMA user_version").fetchone()[0] > 0
        finally:
            copy.close()
        assert (machine.check / "config" / "settings.json").is_file()
        assert (machine.check / "data" / "save_sync_state.json").is_file()
        assert (machine.check / "bin" / "tender-rom-launcher").is_file()

    def test_it_takes_no_lock_writes_no_log_and_notes_no_port(self, machine):
        result = machine.run()

        assert result.returncode == 0, result.stderr
        assert not (machine.check / "data" / "backend.lock").exists()
        assert not (machine.check / "state").exists()
        assert not (machine.check / "run").exists()

    def test_it_writes_nothing_into_the_tree_it_checks(self, machine, tmp_path):
        """The installer puts that tree in place as it is: a byte the build left there is one no tarball brought."""
        tree = tmp_path / "staged"
        for part in ("backend", "bin", "defaults"):
            shutil.copytree(machine.code / part, tree / part, ignore=shutil.ignore_patterns("__pycache__"))
        before = _snapshot(tree)

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(tree / "backend" / "check.py"),
                "--data-from",
                str(machine.live_data),
                "--config-from",
                str(machine.live_config),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=machine.env(TENDER_CODE_DIR=str(tree)),
            timeout=120,
        )

        assert result.returncode == 0, result.stderr
        assert _snapshot(tree) == before

    def test_a_first_install_has_nothing_to_copy_and_builds_all_the_same(self, machine, tmp_path):
        nowhere = tmp_path / "no-install-yet"

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(_CHECK),
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
        live = sqlite3.connect(machine.live_data / _DATABASE)
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
        copy = sqlite3.connect(machine.check / "data" / _DATABASE)
        try:
            assert copy.execute("SELECT note FROM marker ORDER BY rowid").fetchall() == [
                ("in the file",),
                ("only in the wal",),
            ]
        finally:
            copy.close()


class TestAnInstallFromBeforeTheRename:
    """The live database still has its old name, and a commit only its WAL holds — as a backend that died leaves it."""

    @pytest.fixture
    def machine(self, home: Path, tmp_path: Path) -> _Machine:
        laid_out = _Machine(home, tmp_path)
        laid_out.lay_out(_OLD_DATABASE)
        script = (
            "import os, sqlite3\n"
            f"db = sqlite3.connect({str(laid_out.live_data / _OLD_DATABASE)!r}, isolation_level=None)\n"
            "db.execute(\"INSERT INTO marker VALUES ('only in the wal')\")\n"
            "os._exit(0)\n"
        )
        subprocess.run([sys.executable, "-c", script], check=True)
        assert sorted(path.name for path in laid_out.live_data.iterdir()) == [
            _OLD_DATABASE,
            f"{_OLD_DATABASE}-shm",
            f"{_OLD_DATABASE}-wal",
        ]
        return laid_out

    def test_the_library_is_what_the_check_renames_and_migrates(self, machine):
        result = machine.run()

        assert result.returncode == 0, result.stderr
        assert sorted(path.name for path in (machine.check / "data").iterdir() if "romm" in path.name) == [_DATABASE]
        copy = sqlite3.connect(machine.check / "data" / _DATABASE)
        try:
            assert copy.execute("SELECT note FROM marker ORDER BY rowid").fetchall() == [
                ("in the file",),
                ("only in the wal",),
            ]
            assert copy.execute("PRAGMA user_version").fetchone()[0] > 0
        finally:
            copy.close()

    def test_the_live_install_keeps_its_old_name_and_its_bytes(self, machine):
        """The WAL index is left out of the byte comparison: every reader writes its read marks there."""
        before = {
            name: entry
            for name, entry in machine.snapshot()["home"].items()
            if not name.endswith(f"{_OLD_DATABASE}-shm")
        }

        result = machine.run()

        assert result.returncode == 0, result.stderr
        after = machine.snapshot()["home"]
        assert sorted(after) == sorted([*before, f".local/share/romm-tender/{_OLD_DATABASE}-shm"])
        assert {name: after[name] for name in before} == before

    def test_an_old_file_sqlite_cannot_open_answers_two_and_nothing_is_built(self, tmp_path, home):
        machine = _Machine(home, tmp_path)
        machine.lay_out(_OLD_DATABASE)
        (machine.live_data / _OLD_DATABASE).write_bytes(b"not a database, and long enough to have a header" * 4)
        before = machine.snapshot()

        result = machine.run()

        assert result.returncode == check.NOT_TRIED
        assert "check: the live data could not be copied" in result.stderr
        assert machine.snapshot() == before


class TestAVersionThatCannotBeBuilt:
    def test_a_migration_that_fails_on_the_copy_answers_one_with_the_traceback(self, machine):
        """A table in the user's database that the first migration also creates: the build stops there."""
        (machine.live_data / _DATABASE).unlink()
        db = sqlite3.connect(machine.live_data / _DATABASE)
        with db:
            db.execute("CREATE TABLE roms (clashes TEXT)")
        db.close()
        before = machine.snapshot()

        result = machine.run()

        assert result.returncode == check.NOT_BUILT == 1
        assert "Traceback (most recent call last)" in result.stderr
        assert "could not be built" in result.stderr
        assert machine.snapshot() == before

    def test_a_module_that_does_not_import_answers_one_with_the_traceback(self, machine):
        """Python's own answer to a failed import, which is the one the installer relies on."""
        result = _run_with_a_module_that_does_not_import(machine, "bootstrap")

        assert result.returncode == 1
        assert "Traceback (most recent call last)" in result.stderr
        assert "bootstrap" in result.stderr
        assert not machine.check.exists()

    def test_a_main_py_that_does_not_import_answers_one_with_the_traceback(self, machine):
        """The build never reaches the backend's own entry, so the check imports it itself."""
        before = machine.snapshot()

        result = _run_with_a_module_that_does_not_import(machine, "main")

        assert result.returncode == check.NOT_BUILT
        assert "could not be built" in result.stderr
        assert "import of main halted" in result.stderr
        assert machine.snapshot() == before


def _run_with_a_module_that_does_not_import(machine: _Machine, module: str) -> subprocess.CompletedProcess[str]:
    """The real entry, run as the installer runs it, with *module* made unimportable."""
    poisoned = (
        "import runpy, sys; "
        f"sys.modules[{module!r}] = None; "
        f"sys.argv = [{str(_CHECK)!r}, *sys.argv[1:]]; "
        f"runpy.run_path({str(_CHECK)!r}, run_name='__main__')"
    )
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            poisoned,
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


class TestACheckNotTried:
    """What says nothing about the version answers two, and nothing is built."""

    def test_live_data_that_cannot_be_copied_answers_two(self, machine):
        (machine.live_data / _DATABASE).write_bytes(b"not a database, and long enough to have a header" * 4)
        before = machine.snapshot()

        result = machine.run()

        assert result.returncode == check.NOT_TRIED == 2
        assert "check: the live data could not be copied" in result.stderr
        assert "could not be built" not in result.stderr
        assert machine.snapshot() == before

    def test_arguments_it_cannot_read_answer_two(self, machine):
        result = subprocess.run(
            [sys.executable, "-B", str(_CHECK), "--data-from", str(machine.live_data)],
            capture_output=True,
            text=True,
            check=False,
            env=machine.env(),
            timeout=120,
        )

        assert result.returncode == check.NOT_TRIED
        assert "--config-from" in result.stderr


class TestItRefusesToBuildOnALiveRoot:
    @pytest.mark.parametrize("unset", _ROOTS)
    def test_a_root_the_environment_does_not_name_is_refused(self, machine, unset):
        before = machine.snapshot()

        result = machine.run(**{unset: ""})

        assert result.returncode == check.NOT_TRIED
        assert f"check: refused, {unset} not set" in result.stderr
        assert machine.snapshot() == before
        assert not machine.check.exists()

    @pytest.mark.parametrize(("root", "live"), [("TENDER_DATA_DIR", "live_data"), ("TENDER_CONFIG_DIR", "live_config")])
    def test_a_root_that_is_where_the_live_data_is_copied_from_is_refused(self, machine, root, live):
        """Checked before emptiness, so it holds for a live directory that is still empty."""
        for path in getattr(machine, live).iterdir():
            path.unlink()
        before = machine.snapshot()

        result = machine.run(**{root: str(getattr(machine, live))})

        assert result.returncode == check.NOT_TRIED
        assert "is where the live data is copied from" in result.stderr
        assert machine.snapshot() == before

    @pytest.mark.parametrize(
        ("root", "live"),
        [
            ("TENDER_CONFIG_DIR", "live_config"),
            ("TENDER_DATA_DIR", "live_data"),
            ("TENDER_CACHE_DIR", "live_cache"),
            ("TENDER_STATE_DIR", "live_state"),
            ("TENDER_BIN_DIR", "launcher"),
            ("XDG_RUNTIME_DIR", "live_runtime"),
        ],
    )
    def test_a_root_that_already_holds_something_is_refused(self, machine, tmp_path, root, live):
        """A live root named by mistake holds something; the runtime one holds the running backend's port note."""
        target = getattr(machine, live)
        named = target.parent if live == "launcher" else target
        other_sources = {"--data-from": tmp_path / "elsewhere-data", "--config-from": tmp_path / "elsewhere-config"}
        before = machine.snapshot()

        result = subprocess.run(
            [sys.executable, "-B", str(_CHECK), *(str(part) for pair in other_sources.items() for part in pair)],
            capture_output=True,
            text=True,
            check=False,
            env=machine.env(**{root: str(named)}),
            timeout=120,
        )

        assert result.returncode == check.NOT_TRIED, result.stderr
        assert "already exists and is not an empty directory" in result.stderr
        assert machine.snapshot() == before

    def test_an_empty_root_is_one_it_may_build_under(self, machine):
        (machine.check / "data").mkdir(parents=True)

        result = machine.run()

        assert result.returncode == 0, result.stderr

    def test_a_code_root_other_than_the_tree_being_checked_is_refused(self, machine, tmp_path):
        before = machine.snapshot()

        result = machine.run(TENDER_CODE_DIR=str(tmp_path / "another-tree"))

        assert result.returncode == check.NOT_TRIED
        assert "not the tree being checked" in result.stderr
        assert machine.snapshot() == before
        assert not machine.check.exists()


class TestTheEntryPoint:
    """In process: what a raising build answers, and that the backend's own entry knows nothing of a check."""

    def test_a_build_that_raises_answers_one_and_logs_why(
        self, machine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        def broken(**_kwargs: object) -> None:
            raise RuntimeError("a service that cannot be built")

        for name, value in machine.env().items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(check, "build_application", broken)
        monkeypatch.setattr(check, "configure_stderr_logging", lambda: logging.getLogger("test_check"))

        with caplog.at_level(logging.ERROR, logger="test_check"):
            answer = check.check(["--data-from", str(machine.live_data), "--config-from", str(machine.live_config)])

        assert answer == check.NOT_BUILT
        raised = [record.exc_info[1] for record in caplog.records if record.exc_info is not None]
        assert [str(error) for error in raised] == ["a service that cannot be built"]

    def test_a_copy_that_raises_answers_two_and_builds_nothing(
        self, machine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        def uncopyable(**_kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        for name, value in machine.env().items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(check, "copy_live_data", uncopyable)
        monkeypatch.setattr(check, "build_application", lambda **_kwargs: pytest.fail("the build ran"))
        monkeypatch.setattr(check, "configure_stderr_logging", lambda: logging.getLogger("test_check"))

        with caplog.at_level(logging.ERROR, logger="test_check"):
            answer = check.check(["--data-from", str(machine.live_data), "--config-from", str(machine.live_config)])

        assert answer == check.NOT_TRIED
        assert [record.getMessage() for record in caplog.records] == ["check: the live data could not be copied"]

    def test_the_backend_s_entry_takes_no_check_flag(self):
        """A check call can never reach the backend's start: main.py does not read its arguments at all."""
        source = (Path(__file__).resolve().parents[1] / "backend" / "main.py").read_text(encoding="utf-8")

        assert "--check" not in source
        assert "sys.argv" not in source
