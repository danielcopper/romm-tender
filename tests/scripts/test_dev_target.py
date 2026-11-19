"""Tests for ``scripts/dev_target.py`` — the dev tasks' ``--target`` — and the target's half, ``scripts/dev_target.sh``.

Each case runs the build host's script as the tasks do, in a process of its
own, from a checkout laid out under ``tmp_path``: the real scripts, the real
backend (linked, since only the lock reading imports it), and the rest of what
a release ships. Every program it reaches is a stand-in from
``tests/_remote_target.py`` (#2844 D9): ``mise`` "builds" ``dist/``, ``rsync``
copies through the ``ssh`` stand-in and the real dispatcher, and on the target
the real ``dev_target.sh`` meets stand-ins for ``systemctl``, ``systemd-run``,
``pgrep``, ``journalctl`` and ``tail``. The target is set up first by the real
``setup:remote-target``, so every call goes through the key's ``from=`` and the
dispatcher that setup installed. Nothing here runs a real ``ssh``, ``rsync``,
``systemctl`` or Steam.

"Touches nothing on the target" is checked as every file and directory under
the target's home, with its bytes, before and after.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import _remote_target
import pytest
from _remote_target import (
    WORLD_VARIABLE,
    Command,
    Target,
    World,
    commands,
    end_processes,
    fake_path,
    set_unit,
    ssh_calls,
    units,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"

_spec = importlib.util.spec_from_file_location("setup_remote_target", _SCRIPTS / "setup_remote_target.py")
assert _spec is not None
assert _spec.loader is not None
setup_remote_target = importlib.util.module_from_spec(_spec)
sys.modules["setup_remote_target"] = setup_remote_target
_spec.loader.exec_module(setup_remote_target)

_ADDRESS = "192.168.1.20"
_SEEN_FROM = "10.8.0.6"
_HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyOfTheTarget"
_ROOT = ".local/lib/romm-tender-dev"
_LOG = ".local/state/romm-tender/backend.log"
_TIMEOUT = 60


def _shipped() -> list[str]:
    line = next(
        line
        for line in (_SCRIPTS / "package.sh").read_text(encoding="utf-8").splitlines()
        if line.startswith("SHIPPED=(")
    )
    return line.removeprefix("SHIPPED=(").removesuffix(")").split()


def _expand(characters: str) -> str:
    """A character class's ranges spelled out: ``A-Z`` as every letter in it."""
    expanded = ""
    index = 0
    while index < len(characters):
        if index + 2 < len(characters) and characters[index + 1] == "-":
            expanded += "".join(chr(code) for code in range(ord(characters[index]), ord(characters[index + 2]) + 1))
            index += 3
        else:
            expanded += characters[index]
            index += 1
    return expanded


def _snapshot(root: Path) -> dict[str, bytes | str | None]:
    """Every file and directory under *root*: a file's bytes, a link's target, ``None`` for a directory."""
    tree: dict[str, bytes | str | None] = {}
    for directory, folders, files in os.walk(root):
        for name in [*folders, *files]:
            path = Path(directory) / name
            relative = os.path.relpath(path, root)
            if path.is_symlink():
                tree[relative] = os.readlink(path)
            elif path.is_dir():
                tree[relative] = None
            else:
                tree[relative] = path.read_bytes()
    return tree


@dataclass
class Loop:
    world: World
    world_file: Path
    tools_path: str
    repo: Path
    build_home: Path
    target_home: Path
    running: list[subprocess.Popen[str]] = field(default_factory=list)
    """The tasks :meth:`start` started, ended at teardown if they still run."""

    def save(self) -> None:
        self.world.save(self.world_file)

    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.build_home),
            "PATH": self.tools_path,
            WORLD_VARIABLE: str(self.world_file),
        }

    def argv(self, task: str, *words: str, target: str = "deck") -> list[str]:
        flags = [word for word in words if word.startswith("--")]
        rest = [word for word in words if not word.startswith("--")]
        return [sys.executable, str(self.repo / "scripts/dev_target.py"), "--target", target, *flags, "--", task, *rest]

    def run(self, task: str, *words: str, target: str = "deck") -> subprocess.CompletedProcess[str]:
        self.save()
        return subprocess.run(
            self.argv(task, *words, target=target),
            env=self.env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=_TIMEOUT,
        )

    def start(self, task: str, *words: str) -> subprocess.Popen[str]:
        """The task in a session of its own, as a terminal runs it, so that Ctrl-C reaches its whole group."""
        self.save()
        process = subprocess.Popen(
            self.argv(task, *words),
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.running.append(process)
        return process

    def start_ui_scale(self, *words: str) -> tuple[subprocess.Popen[str], int]:
        """``dev:ui-scale`` started, with its unit's main process, once what the unit prints is being followed."""
        self.world.journal = [*self.world.journal, "HOLDING"]
        process = self.start("dev:ui-scale", *(words or ("deck",)))
        assert process.stdout is not None
        while "HOLDING" not in process.stdout.readline():
            assert process.poll() is None, process.communicate()
        return process, units(self.world)["romm-tender-dev-ui-scale"]["pid"]

    def end_running(self) -> None:
        for process in self.running:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.communicate()

    @property
    def root(self) -> Path:
        return self.target_home / _ROOT

    @property
    def log(self) -> Path:
        return self.target_home / _LOG

    def run_unit(self, unit: str) -> int:
        """A process on the target as *unit*'s main process, holding no lock."""
        process = subprocess.Popen(
            [sys.executable, _remote_target.__file__, "process"],
            env={**self.env(), "HOME": str(self.target_home)},
            start_new_session=True,
        )
        set_unit(self.world, unit, process.pid)
        return process.pid

    def hold_lock(self, unit: str) -> int:
        """A process holding the backend's lock on the target, as *unit*'s main process."""
        holder = subprocess.Popen(
            [sys.executable, _remote_target.__file__, "backend"],
            env={**self.env(), "HOME": str(self.target_home)},
            start_new_session=True,
        )
        set_unit(self.world, unit, holder.pid)
        _wait_for(lambda: self.log.exists() and self.log.read_text(encoding="utf-8").endswith("backend started\n"))
        return holder.pid


def _wait_for(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def _alive(pid: int) -> bool:
    return _remote_target._alive(pid)


def _interrupt(process: subprocess.Popen[str], until: str) -> tuple[str, str]:
    """Read the task's output up to a line holding *until*, then press Ctrl-C."""
    assert process.stdout is not None
    seen = []
    for line in process.stdout:
        seen.append(line)
        if until in line:
            break
    os.killpg(process.pid, signal.SIGINT)
    out, err = process.communicate(timeout=_TIMEOUT)
    return "".join(seen) + out, err


def _checkout(path: Path) -> Path:
    """A checkout as the build host holds it: what a release ships, the scripts, and things that do not ship."""
    path.mkdir()
    shutil.copytree(_SCRIPTS, path / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (path / "scripts/__pycache__").mkdir()
    (path / "scripts/__pycache__/setup_remote_target.cpython-314.pyc").write_bytes(b"compiled here")
    (path / "backend").symlink_to(_REPO / "backend")
    for entry in _shipped():
        if entry in ("backend", "dist"):
            continue
        source = _REPO / entry
        if source.is_dir():
            shutil.copytree(source, path / entry)
        else:
            shutil.copyfile(source, path / entry)
    (path / "dist").mkdir()
    (path / "dist/index.js").write_text("an earlier build\n", encoding="utf-8")
    (path / "frontend").mkdir()
    (path / "frontend/package.json").write_text("{}\n", encoding="utf-8")
    (path / "README.md").write_text("not shipped\n", encoding="utf-8")
    return path


@pytest.fixture
def loop(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Loop]:
    tools = tmp_path / "tools"
    tools.mkdir()
    target_home = tmp_path / "target"
    target_home.mkdir()
    path = fake_path(tools)
    world = World(
        targets={_ADDRESS: Target(home=str(target_home), client_address=_SEEN_FROM, host_key=_HOST_KEY, path=path)},
        log=str(tmp_path / "log"),
        service_python=sys.executable,
    )
    world_file = tmp_path / "world.json"
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv(WORLD_VARIABLE, str(world_file))
    world.save(world_file)
    assert setup_remote_target.main(["deck", f"deck@{_ADDRESS}"]) == 0
    built = Loop(world, world_file, path, _checkout(tmp_path / "checkout"), home, target_home)
    yield built
    built.end_running()
    end_processes(world)


def _steam_calls(world: World) -> list[Command]:
    return [
        call
        for call in commands(world)
        if call["program"] == "pgrep" or (call["program"] == "systemd-run" and "steam" in call["argv"])
    ]


class TestTheCopy:
    def test_the_tree_copied_is_what_a_release_ships_and_the_scripts(self, loop: Loop) -> None:
        """#2844 D7: SHIPPED from scripts/package.sh plus scripts/, read from that one list."""
        loop.world.backend_exits = True
        (loop.root / "incoming/stale-from-an-older-list").write_text("x\n", encoding="utf-8")

        result = loop.run("dev:backend")

        assert result.returncode == 1, result.stderr
        expected = {*_shipped(), "scripts"}
        assert {entry.name for entry in (loop.root / "incoming").iterdir()} == expected
        assert {entry.name for entry in (loop.root / "current").iterdir()} == expected
        assert not (loop.root / "current/scripts/__pycache__").exists()
        assert (loop.root / "current/scripts/dev_target.sh").read_bytes() == (_SCRIPTS / "dev_target.sh").read_bytes()

    def test_it_builds_here_before_the_copy_and_surface_builds_the_desktop_build(self, loop: Loop) -> None:
        loop.world.backend_exits = True

        result = loop.run("dev:backend", "--surface")

        assert result.returncode == 1, result.stderr
        assert [call["argv"] for call in commands(loop.world, "mise")] == [["run", "build", "--surface"]]
        assert (loop.root / "current/dist/index.js").read_text(encoding="utf-8") == "build:desktop\n"

    def test_a_failed_build_copies_nothing(self, loop: Loop) -> None:
        loop.world.build_fails = True
        before = _snapshot(loop.target_home)

        result = loop.run("dev")

        assert result.returncode == 1
        assert "the build failed; nothing was copied to deck" in result.stderr
        assert _snapshot(loop.target_home) == before

    def test_a_task_that_builds_nothing_copies_the_tree_and_leaves_the_running_one(self, loop: Loop) -> None:
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run("dev:status")

        assert result.returncode == 0, result.stderr
        assert commands(loop.world, "mise") == []
        assert (loop.root / "incoming/scripts/dev_target.sh").exists()
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]


class TestStartingABackend:
    def test_dev_restarts_steam_starts_the_backend_and_follows_its_log_until_ctrl_c(self, loop: Loop) -> None:
        """#2846 D2: Ctrl-C ends the following and stops the dev backend there, as it ends the backend locally."""
        loop.log.parent.mkdir(parents=True)
        loop.log.write_text("an earlier run's line\n", encoding="utf-8")
        service = loop.run_unit("romm-tender")
        process = loop.start("dev")
        # dev_steam.sh starts Steam in the background, so its call may land after the backend's — and a Ctrl-C
        # before it lands would end it.
        _wait_for(lambda: any(call["argv"][-1:] == ["steam"] for call in commands(loop.world, "systemd-run")))

        out, err = _interrupt(process, "backend started")

        assert process.returncode == 0, err
        assert "an earlier run's line" not in out
        assert "backend started" in out
        assert "stopped the dev backend on deck" in out + err
        assert "keeps running" not in err
        backend = units(loop.world)["romm-tender-dev"]["pid"]
        assert not _alive(backend)
        assert "no backend runs on deck" in loop.run("dev:status").stdout
        assert _alive(service)
        assert ["--user", "stop", "romm-tender"] not in [call["argv"] for call in commands(loop.world, "systemctl")]
        unit = [call for call in commands(loop.world, "systemd-run") if "--unit=romm-tender-dev" in call["argv"]]
        assert [call["argv"][-2:] for call in unit] == [[sys.executable, str(loop.root / "current/backend/main.py")]]

    def test_ctrl_c_on_dev_backend_restarts_nothing_while_it_stops_the_backend(self, loop: Loop) -> None:
        process = loop.start("dev:backend")
        assert process.stdout is not None
        while "backend started" not in process.stdout.readline():
            assert process.poll() is None, process.communicate()

        os.killpg(process.pid, signal.SIGINT)
        out, err = process.communicate(timeout=_TIMEOUT)

        assert process.returncode == 0, err
        assert "stopped the dev backend on deck" in out + err
        assert _steam_calls(loop.world) == []

    def test_a_stop_that_cannot_be_sent_after_ctrl_c_says_the_backend_may_run_and_names_dev_stop(
        self, loop: Loop
    ) -> None:
        process = loop.start("dev:backend")
        assert process.stdout is not None
        while "backend started" not in process.stdout.readline():
            assert process.poll() is None, process.communicate()
        loop.world.targets[_ADDRESS].serving = False
        loop.save()

        os.killpg(process.pid, signal.SIGINT)
        _, err = process.communicate(timeout=_TIMEOUT)

        assert process.returncode == 1
        assert "the backend may still run on deck" in err
        assert "`mise run dev:stop --target deck` stops it" in err
        assert _alive(units(loop.world)["romm-tender-dev"]["pid"])

    def test_ctrl_c_where_the_target_cannot_say_what_runs_says_so(self, loop: Loop) -> None:
        process = loop.start("dev:backend")
        assert process.stdout is not None
        while "backend started" not in process.stdout.readline():
            assert process.poll() is None, process.communicate()
        loop.world.manager_unreachable = True
        loop.save()

        os.killpg(process.pid, signal.SIGINT)
        _, err = process.communicate(timeout=_TIMEOUT)

        assert process.returncode == 1
        assert "the backend may still run on deck" in err
        assert "systemctl said: Failed to connect to bus: No medium found" in err
        assert "`mise run dev:stop --target deck` stops it" in err

    def test_ctrl_c_after_the_backend_ended_says_it_no_longer_runs(self, loop: Loop) -> None:
        process = loop.start("dev:backend")
        assert process.stdout is not None
        while "backend started" not in process.stdout.readline():
            assert process.poll() is None, process.communicate()
        backend = units(loop.world)["romm-tender-dev"]["pid"]
        os.kill(backend, signal.SIGKILL)
        _wait_for(lambda: not _alive(backend))

        os.killpg(process.pid, signal.SIGINT)
        out, err = process.communicate(timeout=_TIMEOUT)

        assert "keeps running" not in err
        assert "no dev backend runs on deck" in out + err

    def test_dev_bpm_restarts_into_big_picture_and_the_choice_is_remembered_on_the_target(self, loop: Loop) -> None:
        loop.world.backend_exits = True

        result = loop.run("dev:bpm")

        assert result.returncode == 1, result.stderr
        _wait_for(
            lambda: any("steam://open/bigpicture" in call["argv"] for call in commands(loop.world, "systemd-run"))
        )
        memory = loop.target_home / ".local/state/tender-dev/steam-window"
        assert memory.read_text(encoding="utf-8") == "window=bpm\ndisplay=\n"
        assert not (loop.build_home / ".local/state/tender-dev").exists()

    def test_the_steps_on_the_target_run_in_the_user_managers_session(self, loop: Loop) -> None:
        loop.world.backend_exits = True

        loop.run("dev:desktop")

        _wait_for(lambda: any(call["argv"][-1:] == ["steam"] for call in commands(loop.world, "systemd-run")))
        steam = next(call for call in commands(loop.world, "systemd-run") if call["argv"][-1:] == ["steam"])
        assert steam["env"]["DISPLAY"] == ":0"
        assert steam["env"]["WAYLAND_DISPLAY"] == "wayland-0"
        assert steam["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
        assert steam["env"]["PATH"] == loop.tools_path

    def test_dev_backend_restarts_nothing(self, loop: Loop) -> None:
        loop.world.backend_exits = True

        loop.run("dev:backend")

        assert _steam_calls(loop.world) == []

    def test_a_backend_that_stops_ends_the_following_with_what_it_wrote_to_stderr(self, loop: Loop) -> None:
        loop.world.backend_exits = True
        loop.world.journal = ["Traceback (most recent call last):", "SyntaxError: invalid syntax"]

        result = loop.run("dev:backend")

        assert result.returncode == 1
        assert "backend started" in result.stdout
        assert "the backend on deck has stopped" in result.stderr
        assert "SyntaxError: invalid syntax" in result.stderr
        assert "keeps running" not in result.stderr

    def test_with_the_dev_backend_holding_the_lock_the_start_is_refused_and_nothing_replaced(self, loop: Loop) -> None:
        loop.hold_lock("romm-tender-dev")
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")
        before = _snapshot(loop.root / "current")

        result = loop.run("dev")

        assert result.returncode == 1
        assert "a backend is already running on deck — the dev backend (romm-tender-dev.service" in result.stderr
        assert "mise run dev:stop --target deck" in result.stderr
        assert _snapshot(loop.root / "current") == before
        assert _steam_calls(loop.world) == []
        assert commands(loop.world, "systemd-run") == []

    @pytest.mark.parametrize("task", ["dev", "dev:bpm", "dev:desktop", "dev:backend"])
    def test_with_the_installed_service_holding_the_lock_the_message_names_dev_stop(
        self, loop: Loop, task: str
    ) -> None:
        """#2844 D2: the task refuses as it does locally, and names the task that stops the service."""
        service = loop.hold_lock("romm-tender")
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")
        before = _snapshot(loop.root / "current")

        result = loop.run(task)

        assert result.returncode == 1
        assert "the installed service (romm-tender.service" in result.stderr
        assert "Stop it with `mise run dev:stop --target deck`" in result.stderr
        assert _snapshot(loop.root / "current") == before
        assert _steam_calls(loop.world) == []
        assert _alive(service)

    @pytest.mark.parametrize("unit", ["romm-tender", None], ids=["installed-service", "another-process"])
    def test_dev_frontend_beside_any_backend_but_the_dev_one_is_refused_and_nothing_replaced(
        self, loop: Loop, unit: str | None
    ) -> None:
        """#2846 D3: the installed service loads its own panel, not this build, so dev:frontend needs the dev one."""
        holder = loop.hold_lock(unit or "some-other-unit")
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run("dev:frontend")

        assert result.returncode == 1
        stop_then_start = "`mise run dev:stop --target deck`, then `mise run dev:backend --target deck`"
        if unit is not None:
            assert "the installed service (romm-tender.service" in result.stderr
            assert stop_then_start in result.stderr
        else:
            assert f"pid {holder}" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]
        assert _steam_calls(loop.world) == []
        assert _alive(holder)

    def test_dev_frontend_with_no_backend_running_is_refused_and_nothing_replaced(self, loop: Loop) -> None:
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run("dev:frontend")

        assert result.returncode == 1
        assert "no backend is running on deck" in result.stderr
        assert "mise run dev:backend --target deck" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]
        assert _steam_calls(loop.world) == []

    def test_dev_frontend_beside_a_running_backend_replaces_the_tree_and_restarts_steam(self, loop: Loop) -> None:
        backend = loop.hold_lock("romm-tender-dev")

        result = loop.run("dev:frontend")

        assert result.returncode == 0, result.stderr
        assert (loop.root / "current/dist/index.js").read_text(encoding="utf-8") == "build:dev\n"
        _wait_for(lambda: any(call["argv"][-1:] == ["steam"] for call in commands(loop.world, "systemd-run")))
        assert not any("--unit=romm-tender-dev" in call["argv"] for call in commands(loop.world, "systemd-run"))
        assert _alive(backend)


class TestTheTasksThatStartNothing:
    @pytest.mark.parametrize(("task", "window"), [("dev:bpm-reset", "bpm"), ("dev:desktop-reset", "desktop")])
    def test_a_reset_restarts_steam_on_the_target_and_builds_nothing(self, loop: Loop, task: str, window: str) -> None:
        result = loop.run(task)

        assert result.returncode == 0, result.stderr
        assert commands(loop.world, "mise") == []
        memory = loop.target_home / ".local/state/tender-dev/steam-window"
        assert memory.read_text(encoding="utf-8") == f"window={window}\ndisplay=\n"
        assert [
            call["argv"] for call in commands(loop.world, "systemd-run") if "--unit=romm-tender-dev" in call["argv"]
        ] == []

    def test_dev_stop_stops_the_dev_backend_and_leaves_steam(self, loop: Loop) -> None:
        backend = loop.hold_lock("romm-tender-dev")

        result = loop.run("dev:stop")

        assert result.returncode == 0, result.stderr
        assert "stopped the dev backend on deck" in result.stdout
        assert not _alive(backend)
        assert _steam_calls(loop.world) == []

    def test_dev_stop_stops_the_installed_service_and_leaves_it_enabled(self, loop: Loop) -> None:
        """#2844 D4: stopped, not disabled; the message names how to start it now."""
        service = loop.hold_lock("romm-tender")

        result = loop.run("dev:stop")

        assert result.returncode == 0, result.stderr
        assert "stopped the installed service on deck (romm-tender.service)" in result.stdout
        assert "systemctl --user start romm-tender" in result.stdout
        assert not _alive(service)
        systemctl = [call["argv"] for call in commands(loop.world, "systemctl")]
        assert ["--user", "stop", "romm-tender"] in systemctl
        assert not any("disable" in argv or "mask" in argv for argv in systemctl)
        assert _steam_calls(loop.world) == []

    def test_dev_stop_with_nothing_holding_the_lock_stops_nothing(self, loop: Loop) -> None:
        result = loop.run("dev:stop")

        assert result.returncode == 0, result.stderr
        assert "nothing was stopped" in result.stdout
        assert not any(call["argv"][1:2] == ["stop"] for call in commands(loop.world, "systemctl"))

    def test_dev_stop_leaves_dev_ui_scale_running(self, loop: Loop) -> None:
        """#2844 D3."""
        backend = loop.hold_lock("romm-tender-dev")
        _, scale = loop.start_ui_scale()

        loop.run("dev:stop")

        assert not _alive(backend)
        assert _alive(scale)

    def test_dev_ui_scale_runs_as_a_unit_and_stop_ends_it_and_the_following(self, loop: Loop) -> None:
        loop.world.journal = [
            "Restored: AUTOMATIC scaling (the state captured before this run).",
            "Restore done: the scale and the window are as they were before this run.",
        ]
        started, scale = loop.start_ui_scale("1.5")

        stopped = loop.run("dev:ui-scale", "--stop")
        _, followed = started.communicate(timeout=_TIMEOUT)

        run = next(
            call for call in commands(loop.world, "systemd-run") if "--unit=romm-tender-dev-ui-scale" in call["argv"]
        )
        assert run["argv"][-3:] == [sys.executable, str(loop.root / "incoming/scripts/dev_ui_scale.py"), "1.5"]
        assert started.returncode == 1
        assert "dev:ui-scale on deck has stopped" in followed
        assert stopped.returncode == 0, stopped.stderr
        assert not _alive(scale)
        assert "Restored: AUTOMATIC scaling" in stopped.stdout
        assert "stopped dev:ui-scale on deck" in stopped.stdout

    def test_dev_ui_scale_while_one_runs_is_refused(self, loop: Loop) -> None:
        loop.start_ui_scale()

        again = loop.run("dev:ui-scale", "deck")

        assert again.returncode == 1
        assert "dev:ui-scale already runs on deck" in again.stderr

    def test_ctrl_c_on_dev_ui_scale_stops_it_and_says_whether_the_restore_landed(self, loop: Loop) -> None:
        """#2844 D3: as Ctrl-C does locally."""
        loop.world.journal = ["Restore done: the scale and the window are as they were before this run."]
        started, scale = loop.start_ui_scale()

        os.killpg(started.pid, signal.SIGINT)
        out, err = started.communicate(timeout=_TIMEOUT)

        assert started.returncode == 0, err
        assert "stopped dev:ui-scale on deck" in out + err
        assert "keeps running" not in err
        assert not _alive(scale)

    def test_ctrl_c_on_dev_ui_scale_whose_restore_did_not_land_says_so(self, loop: Loop) -> None:
        loop.world.journal = ["Restore incomplete: see the lines above."]
        started, _ = loop.start_ui_scale()

        os.killpg(started.pid, signal.SIGINT)
        _, err = started.communicate(timeout=_TIMEOUT)

        assert started.returncode == 1
        assert "the restore on deck did not land" in err

    def test_a_ui_scale_stop_that_cannot_be_sent_after_ctrl_c_names_ui_scale_stop(self, loop: Loop) -> None:
        started, scale = loop.start_ui_scale()
        loop.world.targets[_ADDRESS].serving = False
        loop.save()

        os.killpg(started.pid, signal.SIGINT)
        _, err = started.communicate(timeout=_TIMEOUT)

        assert started.returncode == 1
        assert "dev:ui-scale may still run on deck" in err
        assert "`mise run dev:ui-scale --stop --target deck`" in err
        assert _alive(scale)

    def test_ctrl_c_after_dev_ui_scale_ended_says_it_no_longer_runs(self, loop: Loop) -> None:
        started, scale = loop.start_ui_scale()
        os.kill(scale, signal.SIGKILL)
        _wait_for(lambda: not _alive(scale))

        os.killpg(started.pid, signal.SIGINT)
        out, err = started.communicate(timeout=_TIMEOUT)

        assert "keeps running" not in err
        assert "no dev:ui-scale runs on deck" in out + err

    def test_dev_ui_scale_stop_with_none_running_says_so(self, loop: Loop) -> None:
        result = loop.run("dev:ui-scale", "--stop")

        assert result.returncode == 0, result.stderr
        assert "no dev:ui-scale runs on deck" in result.stdout

    def test_dev_status_names_the_lock_holder_and_whether_dev_ui_scale_runs(self, loop: Loop) -> None:
        free = loop.run("dev:status")
        loop.hold_lock("romm-tender")
        loop.start_ui_scale()
        held = loop.run("dev:status")

        assert free.returncode == 0, free.stderr
        assert "no backend runs on deck — nothing holds the single-instance lock" in free.stdout
        assert "no dev:ui-scale runs on deck" in free.stdout
        assert "the installed service (romm-tender.service" in held.stdout
        assert "holds the single-instance lock on deck" in held.stdout
        assert "dev:ui-scale runs on deck (romm-tender-dev-ui-scale.service)" in held.stdout


class TestTheLog:
    def test_dev_log_shows_the_backend_log(self, loop: Loop) -> None:
        """#2844 D6."""
        loop.log.parent.mkdir(parents=True)
        loop.log.write_text("first\nsecond\n", encoding="utf-8")
        before = _snapshot(loop.target_home)

        result = loop.run("dev:log")

        assert result.returncode == 0, result.stderr
        assert result.stdout == "first\nsecond\n"
        assert _snapshot(loop.target_home) == before

    def test_dev_log_follow_follows_the_log_until_ctrl_c(self, loop: Loop) -> None:
        """#2846 D2."""
        loop.log.parent.mkdir(parents=True)
        loop.log.write_text("before\n", encoding="utf-8")
        process = loop.start("dev:log", "--follow")
        assert process.stdout is not None
        assert process.stdout.readline() == "before\n"
        with open(loop.log, "a", encoding="utf-8") as log:
            log.write("written while followed\n")

        out, err = _interrupt(process, "written while followed")

        assert "written while followed" in out
        assert process.returncode == 0, err
        assert "stopped following the log on deck" in err

    def test_an_older_dispatcher_that_refuses_the_follow_says_to_set_the_target_up_again(self, loop: Loop) -> None:
        dispatcher = loop.root / "dispatch.sh"
        text = dispatcher.read_text(encoding="utf-8")
        dispatcher.write_text(text.replace("    follow)\n", "    no-follow)\n"), encoding="utf-8")

        result = loop.run("dev:log", "--follow")

        assert result.returncode == 3
        assert "tender-dev: refused: follow log" in result.stderr
        assert "dispatcher refused the request; it is most likely older than this checkout's" in result.stderr
        assert f"mise run setup:remote-target deck deck@{_ADDRESS}" in result.stderr


class TestTheConnection:
    def test_every_ssh_call_gives_up_on_a_target_that_stops_answering(self, loop: Loop) -> None:
        """Keepalive and connect timeout on every call's command line."""
        before = len(ssh_calls(loop.world))
        loop.hold_lock("romm-tender-dev")

        loop.run("dev:stop")
        loop.run("dev:log")

        calls = ssh_calls(loop.world)[before:]
        assert len(calls) == 3
        for call in calls:
            assert call["serveraliveinterval"] == ["15"]
            assert call["serveralivecountmax"] == ["3"]
            assert call["connecttimeout"] == ["10"]

    def test_the_copy_is_announced_before_it_starts(self, loop: Loop) -> None:
        result = loop.run("dev:status")

        assert result.returncode == 0, result.stderr
        assert "tender-dev: copying to deck" in result.stderr

    def test_dev_log_follow_to_a_target_that_does_not_answer_gives_the_no_answer_hint(self, loop: Loop) -> None:
        loop.world.targets[_ADDRESS].serving = False

        result = loop.run("dev:log", "--follow")

        assert result.returncode == 255
        assert f"no answer from deck at {_ADDRESS} port 22" in result.stderr
        assert "ended while following" not in result.stderr

    def test_a_connection_a_read_loses_says_only_that_it_ended(self, loop: Loop) -> None:
        loop.world.fail_with = "Timeout, server 192.168.1.20 not responding."

        result = loop.run("dev:log")

        assert result.returncode == 255
        assert "tender-dev: the connection to deck ended." in result.stderr
        assert "no answer" not in result.stderr
        assert "check Steam" not in result.stderr

    def test_a_drop_after_a_read_answered_is_not_read_as_no_answer(self, loop: Loop) -> None:
        loop.world.drop_after_line = "tender-dev: no backend runs on deck"

        result = loop.run("dev:status")

        assert result.returncode == 255
        assert "tender-dev: the connection to deck ended." in result.stderr
        assert "no answer" not in result.stderr
        assert "check Steam" not in result.stderr

    def test_a_drop_after_a_task_that_changes_the_target_began_says_it_may_have_stopped(self, loop: Loop) -> None:
        loop.world.backend_exits = True
        loop.world.drop_after_line = "Shutting down Steam..."

        result = loop.run("dev:bpm")

        assert result.returncode == 255
        assert "the connection to deck ended; what the task was doing on deck may have stopped part-way" in (
            result.stderr
        )
        assert "check Steam there" in result.stderr
        assert "no answer" not in result.stderr

    @pytest.mark.parametrize(
        "says",
        [
            "client_loop: send disconnect: Connection reset by peer",
            "Read from remote host deck: Connection reset by peer",
        ],
        ids=["client-loop", "read-from-remote-host"],
    )
    def test_dev_log_follow_on_an_empty_log_that_drops_is_not_read_as_no_answer(self, loop: Loop, says: str) -> None:
        """Nothing written yet, so only ssh's own words tell the drop from a target that never answered."""
        loop.log.parent.mkdir(parents=True)
        loop.log.write_text("", encoding="utf-8")
        loop.world.follow_holds = False
        loop.world.drop_after_first_line = True
        loop.world.drop_says = says

        result = loop.run("dev:log", "--follow")

        assert result.returncode == 255
        assert "tender-dev: the connection to deck ended." in result.stderr
        assert "no answer" not in result.stderr

    def test_a_drop_after_the_target_wrote_only_on_stderr_is_not_read_as_no_answer(self, loop: Loop) -> None:
        """The target's own stderr line counts as an answer, whatever ssh then says."""
        loop.start_ui_scale()
        loop.world.drop_after_first_line = True
        loop.world.drop_only_for = "dev_target.sh ui-scale"
        loop.world.drop_says = "Connection reset by 192.168.1.20 port 22"

        result = loop.run("dev:ui-scale", "deck")

        assert result.returncode == 255
        assert "tender-dev: dev:ui-scale already runs on deck" in result.stderr
        assert "the connection to deck ended; what the task was doing on deck may have stopped part-way" in (
            result.stderr
        )
        assert "no answer" not in result.stderr

    def test_ctrl_c_on_dev_log_follow_before_its_first_line_stops_the_following(self, loop: Loop) -> None:
        loop.log.parent.mkdir(parents=True)
        loop.log.write_text("", encoding="utf-8")
        process = loop.start("dev:log", "--follow")
        _wait_for(lambda: bool(commands(loop.world, "tail")))

        os.killpg(process.pid, signal.SIGINT)
        _, err = process.communicate(timeout=_TIMEOUT)

        assert process.returncode == 0, err
        assert "stopped following the log on deck" in err

    @pytest.mark.parametrize(
        ("task", "follows"),
        [("dev:backend", "the log. The backend may still run there"), ("dev:ui-scale", "dev:ui-scale, which may")],
    )
    def test_a_connection_that_ends_while_a_task_follows_is_said(self, loop: Loop, task: str, follows: str) -> None:
        loop.world.backend_exits = True
        loop.world.processes_exit = True
        loop.world.drop_after_line = "tender-dev-target: following"

        result = loop.run(task, *(["deck"] if task == "dev:ui-scale" else []))

        assert result.returncode == 255
        assert f"the connection to deck ended while following {follows}" in result.stderr

    def test_a_connection_that_ends_while_following_is_said(self, loop: Loop) -> None:
        loop.log.parent.mkdir(parents=True)
        loop.log.write_text("one\ntwo\n", encoding="utf-8")
        loop.world.follow_holds = False
        loop.world.drop_after_first_line = True

        result = loop.run("dev:log", "--follow")

        assert result.returncode == 255
        assert "the connection to deck ended while following" in result.stderr


class TestWhenTheTargetCannotAnswer:
    """A question on the target that gets no answer ends the task, saying so — before a start, nothing started."""

    def test_a_user_manager_that_cannot_be_reached_is_refused_before_anything_is_touched(self, loop: Loop) -> None:
        loop.world.manager_unreachable = True
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run("dev")

        assert result.returncode == 1
        assert "could not ask the user manager on deck" in result.stderr
        assert "systemctl said: Failed to connect to bus: No medium found" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]
        assert _steam_calls(loop.world) == []

    def test_a_main_pid_that_cannot_be_asked_is_said_rather_than_read_as_another_holder(self, loop: Loop) -> None:
        loop.hold_lock("romm-tender")
        loop.world.main_pid_fails = True

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert "could not ask the user manager on deck" in result.stderr
        assert "systemctl said: Failed to connect to bus: No medium found" in result.stderr
        assert "holds the single-instance lock" not in result.stdout

    def test_no_display_from_the_user_manager_is_warned_about(self, loop: Loop) -> None:
        loop.world.session_environment = ["DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"]

        result = loop.run("dev:desktop-reset")

        assert "names no display" in result.stderr

    @pytest.mark.parametrize("task", ["dev", "dev:frontend"])
    def test_a_lock_that_cannot_be_read_is_refused_and_nothing_replaced(self, loop: Loop, task: str) -> None:
        lock = loop.target_home / ".local/share/romm-tender/backend.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        lock.chmod(0)
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run(task)

        assert result.returncode == 1
        assert "could not read the single-instance lock on deck" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]
        assert _steam_calls(loop.world) == []

    @pytest.mark.parametrize(
        ("task", "names"),
        [
            ("dev:backend", "`mise run dev:stop --target deck` stops it"),
            ("dev:ui-scale", "`mise run dev:ui-scale --stop --target deck` stops it"),
        ],
    )
    def test_a_user_manager_lost_after_a_start_says_what_was_started(self, loop: Loop, task: str, names: str) -> None:
        loop.world.main_pid_fails_while_running = True

        result = loop.run(task, *(["deck"] if task == "dev:ui-scale" else []))

        assert result.returncode == 1
        assert "could not ask the user manager on deck after starting" in result.stderr
        assert "systemctl said: Failed to connect to bus: No medium found" in result.stderr
        assert names in result.stderr
        assert "Nothing was started" not in result.stderr

    def test_a_broken_interpreter_is_named_as_broken_not_as_old(self, loop: Loop, tmp_path: Path) -> None:
        broken = tmp_path / "python-broken"
        broken.write_text("#!/bin/sh\necho 'error while loading shared libraries: libpython3.so' >&2\nexit 127\n")
        broken.chmod(0o755)
        loop.world.service_python = str(broken)

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert "older than 3.12" not in result.stderr
        assert f"{broken} on deck (the installed service's interpreter) does not run" in result.stderr
        assert "libpython3.so" in result.stderr


class TestAUnitsState:
    """Read from ActiveState: systemd 261's is-active ends with 4 for a unit that is not loaded."""

    def test_units_that_are_not_loaded_read_as_not_running(self, loop: Loop) -> None:
        result = loop.run("dev:status")

        assert result.returncode == 0, result.stderr
        assert "no backend runs on deck" in result.stdout
        assert "no dev:ui-scale runs on deck" in result.stdout

    @pytest.mark.parametrize("state", ["activating", "deactivating"])
    def test_a_unit_starting_or_stopping_runs(self, loop: Loop, state: str) -> None:
        loop.world.unit_states = {"romm-tender-dev": state}

        result = loop.run("dev:backend")

        assert result.returncode == 1
        assert "romm-tender-dev.service runs on deck but holds no lock" in result.stderr

    def test_running_names_what_systemctl_said_when_it_cannot_ask(self, loop: Loop) -> None:
        loop.run("dev:status")
        loop.world.manager_unreachable = True
        loop.save()

        asked = subprocess.run(
            [
                "ssh",
                "-F",
                str(loop.build_home / ".config/romm-tender-dev/ssh_config"),
                "--",
                "deck",
                "scripts/dev_target.sh",
                "running",
                "deck",
                "backend",
            ],
            env=loop.env(),
            capture_output=True,
            text=True,
            check=False,
        )

        assert asked.returncode == 4
        assert "systemctl said: Failed to connect to bus: No medium found" in asked.stderr


class TestADevUnitThatHoldsNoLock:
    """romm-tender-dev running without the lock — a backend that never got it, say."""

    def test_a_start_is_refused(self, loop: Loop) -> None:
        loop.run_unit("romm-tender-dev")
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run("dev:backend")

        assert result.returncode == 1
        assert "romm-tender-dev.service runs on deck but holds no lock" in result.stderr
        assert "mise run dev:stop --target deck" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]

    def test_dev_stop_stops_it(self, loop: Loop) -> None:
        unit = loop.run_unit("romm-tender-dev")

        result = loop.run("dev:stop")

        assert result.returncode == 0, result.stderr
        assert "stopped romm-tender-dev.service on deck, which held no lock" in result.stdout
        assert not _alive(unit)

    def test_dev_status_with_the_lock_unreadable_says_only_that_it_runs(self, loop: Loop) -> None:
        loop.run_unit("romm-tender-dev")
        lock = loop.target_home / ".local/share/romm-tender/backend.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        lock.chmod(0)

        result = loop.run("dev:status")

        assert result.returncode == 0, result.stderr
        assert "romm-tender-dev.service runs on deck." in result.stdout
        assert "holds no lock" not in result.stdout

    def test_a_held_lock_whose_holder_no_pid_names_goes_to_the_unit_that_runs(self, loop: Loop, tmp_path: Path) -> None:
        """Where the kernel names no pid, lock_holder asks which unit runs (unit_runs)."""
        lock = loop.target_home / ".local/share/romm-tender/backend.lock"
        python = tmp_path / "python-whose-lock-reading-names-no-pid"
        python.write_text(
            f"#!/bin/sh\ncase \"$*\" in *--holder*) printf 'held\\n\\n%s\\n\\n' '{lock}'; exit 0 ;; esac\n"
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        python.chmod(0o755)
        loop.world.service_python = str(python)
        loop.run_unit("romm-tender")

        result = loop.run("dev:status")

        assert result.returncode == 0, result.stderr
        assert "the installed service (romm-tender.service) holds the single-instance lock on deck" in result.stdout

    def test_dev_status_reports_it(self, loop: Loop) -> None:
        loop.run_unit("romm-tender-dev")

        result = loop.run("dev:status")

        assert result.returncode == 0, result.stderr
        assert "romm-tender-dev.service runs on deck but holds no lock" in result.stdout
        assert "no backend runs" not in result.stdout


class TestAnotherProcessHoldingTheLock:
    @pytest.mark.parametrize("task", ["dev", "dev:backend"])
    def test_a_start_is_refused_and_names_the_process(self, loop: Loop, task: str) -> None:
        holder = loop.hold_lock("some-other-unit")
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run(task)

        assert result.returncode == 1
        assert f"pid {holder} (" in result.stderr
        assert "neither the dev backend nor the installed service" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]

    def test_dev_stop_leaves_it_and_ends_with_1(self, loop: Loop) -> None:
        holder = loop.hold_lock("some-other-unit")

        result = loop.run("dev:stop")

        assert result.returncode == 1
        assert f"pid {holder} (" in result.stderr
        assert _alive(holder)
        assert not any("stop" in call["argv"] for call in commands(loop.world, "systemctl"))

    def test_dev_status_names_it(self, loop: Loop) -> None:
        holder = loop.hold_lock("some-other-unit")

        result = loop.run("dev:status")

        assert result.returncode == 0, result.stderr
        assert f"pid {holder} (" in result.stdout


class TestReplacingTheRunningTree:
    def test_a_half_built_copy_left_behind_is_thrown_away(self, loop: Loop) -> None:
        loop.world.backend_exits = True
        (loop.root / ".current.new/left-over").parent.mkdir(parents=True)
        (loop.root / ".current.new/left-over").write_text("x\n", encoding="utf-8")

        loop.run("dev:backend")

        assert not (loop.root / ".current.new").exists()
        assert not (loop.root / "current/left-over").exists()
        assert (loop.root / "current/dist/index.js").read_text(encoding="utf-8") == "build:dev\n"

    def test_a_tree_left_aside_by_a_swap_that_stopped_is_put_back_before_anything_else(self, loop: Loop) -> None:
        """Between the two renames only .current.old holds the tree; a copy that then fails must not lose it."""
        (loop.root / "current").rename(loop.root / ".current.old")
        (loop.root / ".current.old/sentinel").write_text("the only tree\n", encoding="utf-8")
        loop.world.backend_exits = True
        (loop.repo / "scripts/unreadable").write_text("x\n", encoding="utf-8")
        loop.run("dev:status")
        # A file in incoming/ the target user cannot read, which the next copy leaves as it is (unchanged here),
        # makes the tree's copy fail.
        (loop.root / "incoming/scripts/unreadable").chmod(0)

        result = loop.run("dev:backend")

        assert result.returncode != 0
        assert (loop.root / "current/sentinel").read_text(encoding="utf-8") == "the only tree\n"
        (loop.root / "incoming/scripts/unreadable").chmod(0o644)


class TestWhatTheScriptsOnTheTargetSay:
    def test_a_restore_that_did_not_land_is_reported_by_ui_scale_stop(self, loop: Loop) -> None:
        loop.world.journal = [
            "Restored: AUTOMATIC scaling (the state captured before this run).",
            "WARNING: that is NOT the window state captured before this run (fullscreen 2560x1440 on DP-2).",
            "Restore incomplete: see the lines above.",
        ]
        loop.start_ui_scale()

        result = loop.run("dev:ui-scale", "--stop")

        assert result.returncode == 1
        assert "the restore on deck did not land" in result.stderr

    def test_ui_scale_stop_waits_for_a_restore_line_the_journal_hands_on_late(self, loop: Loop) -> None:
        loop.world.journal = ["Restore done: the scale and the window are as they were before this run."]
        loop.world.journal_late = True
        loop.start_ui_scale()

        result = loop.run("dev:ui-scale", "--stop")

        assert result.returncode == 0, result.stderr
        assert "stopped dev:ui-scale on deck" in result.stdout

    def test_a_stop_that_fails_is_reported_with_the_restore_verdict(self, loop: Loop) -> None:
        loop.world.journal = ["Restore incomplete: see the lines above."]
        loop.world.stop_fails = True
        loop.start_ui_scale()

        result = loop.run("dev:ui-scale", "--stop")

        assert result.returncode == 1
        assert "the restore on deck did not land" in result.stderr
        assert "stopping romm-tender-dev-ui-scale.service failed" in result.stderr

    def test_a_restore_that_says_nothing_is_not_confirmed(self, loop: Loop) -> None:
        """The unit was ended without its last line — killed after systemd's stop timeout, say."""
        loop.world.journal = ["Restored: AUTOMATIC scaling (the state captured before this run)."]
        loop.start_ui_scale()

        result = loop.run("dev:ui-scale", "--stop")

        assert result.returncode == 1
        assert "the restore on deck is not confirmed" in result.stderr

    def test_the_scripts_on_the_target_name_the_target_in_their_hints(self, loop: Loop) -> None:
        loop.start_ui_scale()

        run = next(
            call for call in commands(loop.world, "systemd-run") if "--unit=romm-tender-dev-ui-scale" in call["argv"]
        )

        assert "--setenv=TENDER_DEV_TARGET=deck" in run["argv"]

    @pytest.mark.parametrize(("target", "hint"), [("deck", " --target deck"), ("", "")])
    def test_dev_ui_scales_hints_name_the_target_it_is_given(self, target: str, hint: str) -> None:
        said = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import dev_ui_scale as d;"
                " print(d._task('dev:ui-scale auto')); print(d._stopper())",
                str(_SCRIPTS),
            ],
            env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "TENDER_DEV_TARGET": target},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()

        assert said[0] == f"mise run dev:ui-scale auto{hint}"
        assert said[1] == (f"`mise run dev:ui-scale --stop{hint}`" if target else "Ctrl-C")

    def test_dev_steams_hint_names_the_target(self, loop: Loop) -> None:
        memory = loop.target_home / ".local/state/tender-dev/steam-window"
        memory.parent.mkdir(parents=True)
        memory.write_text("window=bpm\ndisplay=dp9\n", encoding="utf-8")
        loop.world.backend_exits = True

        result = loop.run("dev")

        assert "`mise run dev:bpm <display> --target deck`" in result.stderr


class TestAMissingShippedPath:
    """A task that builds copies the tree whole; the others mirror what there is."""

    def test_a_task_that_builds_refuses_it_before_the_copy(self, loop: Loop) -> None:
        shutil.rmtree(loop.repo / "defaults")
        before = _snapshot(loop.target_home)

        result = loop.run("dev")

        assert result.returncode == 1
        assert "has no defaults" in result.stderr
        assert _snapshot(loop.target_home) == before

    @pytest.mark.parametrize("words", [("dev:status",), ("dev:ui-scale", "--stop")])
    def test_a_task_that_builds_nothing_goes_through_in_a_checkout_never_built(
        self, loop: Loop, words: tuple[str, ...]
    ) -> None:
        shutil.rmtree(loop.repo / "dist")

        result = loop.run(*words)

        assert result.returncode == 0, result.stderr
        assert (loop.root / "incoming/scripts/dev_target.sh").exists()
        assert not (loop.root / "incoming/dist").exists()


class TestHopsAndDisplays:
    def test_a_hop_that_fails_is_named_as_the_hop(self, loop: Loop) -> None:
        config = loop.build_home / ".config/romm-tender-dev/ssh_config"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("    User deck\n", "    User deck\n    ProxyJump jumper@203.0.113.5:22\n"))
        loop.world.hop_failure = "denied"
        before = _snapshot(loop.target_home)

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert "a hop on the way to deck (jumper@203.0.113.5:22) failed" in result.stderr
        assert "jumper@203.0.113.5: Permission denied" in result.stderr
        assert _snapshot(loop.target_home) == before

    def test_a_hop_whose_host_key_changed_is_named_as_the_hop(self, loop: Loop) -> None:
        """ssh names a known-hosts file that is not the dev loop's: the key is the hop's."""
        config = loop.build_home / ".config/romm-tender-dev/ssh_config"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("    User deck\n", "    User deck\n    ProxyJump jumper@203.0.113.5:22\n"))
        loop.world.hop_failure = "host-key"

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert "a hop on the way to deck (jumper@203.0.113.5:22) failed" in result.stderr

    def test_a_display_reaches_the_target(self, loop: Loop) -> None:
        loop.world.outputs = ["eDP-1", "DP-2"]

        result = loop.run("dev:bpm-reset", "dp2")

        assert result.returncode == 0, result.stderr
        memory = loop.target_home / ".local/state/tender-dev/steam-window"
        assert memory.read_text(encoding="utf-8") == "window=bpm\ndisplay=dp2\n"


class TestCtrlCBeforeTheFollowing:
    def test_it_says_the_task_there_may_have_stopped_part_way(self, loop: Loop) -> None:
        loop.world.steam_running = True
        process = loop.start("dev:bpm-reset")
        _wait_for(lambda: bool(commands(loop.world, "pgrep")))

        os.killpg(process.pid, signal.SIGINT)
        _, err = process.communicate(timeout=_TIMEOUT)

        assert process.returncode == 130
        assert "interrupted here; what the task was doing on deck may have stopped part-way" in err
        assert "`mise run dev:status --target deck` says what holds the lock; check Steam there." in err

    def test_no_follower_is_left_on_the_target_after_ctrl_c(self, loop: Loop) -> None:
        process = loop.start("dev:backend")
        assert process.stdout is not None
        while "backend started" not in process.stdout.readline():
            assert process.poll() is None, process.communicate()

        os.killpg(process.pid, signal.SIGINT)
        process.communicate(timeout=_TIMEOUT)

        followers = [call["pid"] for call in commands(loop.world, "tail")]
        assert followers
        _wait_for(lambda: not any(_alive(pid) for pid in followers))


class TestWhenATargetCannotBeUsed:
    """Each of the four --target failures says why and what to do, and touches nothing on the target."""

    def test_no_such_target_is_refused_before_the_build(self, loop: Loop) -> None:
        before = _snapshot(loop.target_home)

        result = loop.run("dev", target="tv")

        assert result.returncode == 1
        assert "no target named tv in" in result.stderr
        assert "(set up: deck)" in result.stderr
        assert "mise run setup:remote-target tv <destination>" in result.stderr
        assert commands(loop.world, "mise") == []
        assert _snapshot(loop.target_home) == before

    def test_with_no_target_set_up_at_all_it_says_so(self, loop: Loop) -> None:
        shutil.rmtree(loop.build_home / ".config/romm-tender-dev")

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert "this machine has set up none" in result.stderr

    def test_no_answer_at_its_address(self, loop: Loop) -> None:
        loop.world.targets[_ADDRESS].serving = False
        before = _snapshot(loop.target_home)

        result = loop.run("dev")

        assert result.returncode == 1
        assert f"no answer from deck at {_ADDRESS} port 22" in result.stderr
        assert "ssh: connect to host 192.168.1.20 port 22: Connection refused" in result.stderr
        assert "mise run setup:remote-target deck <new address>" in result.stderr
        assert "Nothing was done on deck." in result.stderr
        assert _snapshot(loop.target_home) == before

    def test_a_host_key_that_is_not_the_recorded_one(self, loop: Loop) -> None:
        loop.world.targets[_ADDRESS].host_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAnotherMachine"
        before = _snapshot(loop.target_home)

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert "is not the one recorded for deck — another machine answers at that address" in result.stderr
        assert "ssh-keygen -R romm-tender-dev.deck" in result.stderr
        assert _snapshot(loop.target_home) == before

    def test_the_build_hosts_key_refused(self, loop: Loop) -> None:
        loop.world.targets[_ADDRESS].client_address = "10.8.0.99"
        before = _snapshot(loop.target_home)

        result = loop.run("dev:log")

        assert result.returncode == 255
        assert "deck refused this machine's key" in result.stderr
        assert f"Set it up again: mise run setup:remote-target deck deck@{_ADDRESS}" in result.stderr
        assert _snapshot(loop.target_home) == before

    def test_a_copy_the_dispatcher_refuses_shows_its_refusal_under_the_hint(self, loop: Loop) -> None:
        dispatcher = loop.root / "dispatch.sh"
        text = dispatcher.read_text(encoding="utf-8")
        dispatcher.write_text(text.replace("    rsync)\n", "    no-rsync)\n"), encoding="utf-8")

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert "most likely older than this checkout's" in result.stderr
        hint = result.stderr.index("most likely older")
        assert result.stderr.index("tender-dev: refused: rsync --server", hint) > hint

    def test_a_word_the_dispatcher_would_refuse_is_refused_here(self, loop: Loop) -> None:
        result = loop.run("dev:bpm", "dp2;x")

        assert result.returncode == 1
        assert "cannot be sent to a target" in result.stderr
        assert commands(loop.world, "mise") == []


class TestTheTargetsHalf:
    def test_the_dev_backend_runs_on_the_interpreter_the_installed_service_runs_on(
        self, loop: Loop, tmp_path: Path
    ) -> None:
        """#2844 D8: read from the service's ExecStart."""
        other = tmp_path / "python-of-the-service"
        other.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        other.chmod(0o755)
        loop.world.service_python = str(other)
        loop.world.backend_exits = True

        loop.run("dev:backend")

        started = [call for call in commands(loop.world, "systemd-run") if "--unit=romm-tender-dev" in call["argv"]]
        assert [call["argv"][-2:] for call in started] == [[str(other), str(loop.root / "current/backend/main.py")]]

    def test_with_no_service_installed_the_dev_backend_runs_on_usr_bin_python3(self, loop: Loop) -> None:
        """#2844 D8. The lock is read with /usr/bin/python3 itself, so this machine needs one of 3.12 or newer."""
        loop.world.service_python = None
        loop.world.backend_exits = True

        result = loop.run("dev:backend")

        assert "older than 3.12" not in result.stderr, result.stderr
        started = [call for call in commands(loop.world, "systemd-run") if "--unit=romm-tender-dev" in call["argv"]]
        assert [call["argv"][-2:] for call in started] == [
            ["/usr/bin/python3", str(loop.root / "current/backend/main.py")]
        ]

    def test_an_interpreter_that_is_not_there_is_refused(self, loop: Loop) -> None:
        loop.world.service_python = str(loop.target_home / "no-python")

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert f"no Python at {loop.world.service_python} on deck" in result.stderr
        assert "TENDER_PYTHON" not in result.stderr

    def test_an_interpreter_older_than_3_12_is_refused(self, loop: Loop, tmp_path: Path) -> None:
        old = tmp_path / "python3.11"
        old.write_text("#!/bin/sh\necho 3.11\nexit 3\n", encoding="utf-8")
        old.chmod(0o755)
        loop.world.service_python = str(old)
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run("dev")

        assert result.returncode == 1
        assert f"{old} on deck (the installed service's interpreter) is Python 3.11" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]

    @pytest.mark.parametrize("value", ["$'odd\\nvalue'", "$':0'"])
    def test_a_session_value_in_quoting_is_not_taken(self, loop: Loop, value: str) -> None:
        loop.world.session_environment = [f"DISPLAY={value}", "WAYLAND_DISPLAY=wayland-0"]

        loop.run("dev:desktop-reset")

        _wait_for(lambda: any(call["argv"][-1:] == ["steam"] for call in commands(loop.world, "systemd-run")))
        steam = next(call for call in commands(loop.world, "systemd-run") if call["argv"][-1:] == ["steam"])
        assert "DISPLAY" not in steam["env"]
        assert steam["env"]["WAYLAND_DISPLAY"] == "wayland-0"

    def test_the_one_shot_modes_it_runs_in_the_foreground_are_dev_ui_scales(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spec = importlib.util.spec_from_file_location("dev_ui_scale", _SCRIPTS / "dev_ui_scale.py")
        assert spec is not None
        assert spec.loader is not None
        dev_ui_scale = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "dev_ui_scale", dev_ui_scale)
        spec.loader.exec_module(dev_ui_scale)
        script = (_SCRIPTS / "dev_target.sh").read_text(encoding="utf-8")

        assert f"        {' | '.join(dev_ui_scale.ONE_SHOT_MODES)}) exec " in script

    def test_a_word_it_sends_is_one_the_dispatcher_lets_through(self) -> None:
        """dev_target.py's word rule and the dispatcher's check_words name the same characters."""
        dispatcher = (_SCRIPTS / "dev_dispatch.sh").read_text(encoding="utf-8")
        refused = re.search(r"'' \| \*\[!([^\]]+)\]\*\) refuse", dispatcher)
        assert refused is not None
        word = re.search(r'_WORD = re\.compile\(r"\[([^\]]+)\]\+"\)', (_SCRIPTS / "dev_target.py").read_text())
        assert word is not None

        assert refused.group(1) == word.group(1)
        name_characters = re.findall(r"\[([^\]]+)\]", setup_remote_target._NAME.pattern)[-1]
        assert set(_expand(name_characters)) <= set(_expand(word.group(1)))

    def test_dev_ui_scale_without_a_mode_is_refused_here(self, loop: Loop) -> None:
        result = loop.run("dev:ui-scale")

        assert result.returncode == 1
        assert "dev:ui-scale needs a mode" in result.stderr
        assert commands(loop.world, "systemd-run") == []

    @pytest.mark.parametrize(
        ("reads_only", "says"),
        [
            (True, "tender-dev: interrupted here."),
            (False, "tender-dev: interrupted here; what the task was doing on deck may have stopped part-way"),
        ],
    )
    def test_a_task_cut_short_names_steam_only_where_it_changes_the_target(
        self, monkeypatch: pytest.MonkeyPatch, reads_only: bool, says: str
    ) -> None:
        spec = importlib.util.spec_from_file_location("dev_target_module", _SCRIPTS / "dev_target.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "dev_target_module", module)
        spec.loader.exec_module(module)
        destination = setup_remote_target.Destination("deck", "deck", _ADDRESS, "22", None, None)
        target = module.Target("deck", Path("/nowhere/ssh_config"), destination)

        said = module.cut_short(target, "interrupted here", reads_only=reads_only)

        assert said.startswith(says)
        assert ("check Steam" in said) is not reads_only
        assert {name for name, spec in module.SPECS.items() if spec.reads_only} == {"dev:status"}

    def test_every_task_but_dev_log_has_its_spec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spec = importlib.util.spec_from_file_location("dev_target_module", _SCRIPTS / "dev_target.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "dev_target_module", module)
        spec.loader.exec_module(module)

        assert set(module.SPECS) == set(module.TASKS) - {"dev:log"}

    def test_a_target_name_ssh_could_not_carry_is_refused(self, loop: Loop) -> None:
        result = loop.run("dev:status", target="de:ck")

        assert result.returncode == 1
        assert "a target's name is letters, digits" in result.stderr
        assert commands(loop.world, "mise") == []

    @pytest.mark.parametrize("name", ["RESTORE_DONE", "RESTORE_INCOMPLETE"])
    def test_the_restore_lines_it_waits_for_are_dev_ui_scales(self, name: str) -> None:
        source = (_SCRIPTS / "dev_ui_scale.py").read_text(encoding="utf-8")
        line = re.search(rf'^{name} = "([^"]+)"$', source, re.MULTILINE)
        assert line is not None
        opening = line.group(1).split(":")[0] + ":"

        assert f'*"{opening}"*' in (_SCRIPTS / "dev_target.sh").read_text(encoding="utf-8")

    def test_the_default_python_is_the_installers(self) -> None:
        installer = re.search(
            r'^PYTHON="\$\{TENDER_PYTHON:-([^}]+)\}"$', (_REPO / "install.sh").read_text(), re.MULTILINE
        )
        assert installer is not None

        assert f'readonly DEFAULT_PYTHON="{installer.group(1)}"' in (_SCRIPTS / "dev_target.sh").read_text()

    def test_the_python_floor_is_the_installers(self) -> None:
        script = (_SCRIPTS / "dev_target.sh").read_text(encoding="utf-8")
        installer = (_REPO / "install.sh").read_text(encoding="utf-8")
        floor = re.search(r"sys\.version_info >= \((\d+), (\d+)\)", installer)
        assert floor is not None

        assert f"sys.version_info >= ({floor.group(1)}, {floor.group(2)})" in script

    def test_the_service_it_stops_is_the_installers_unit(self) -> None:
        script = (_SCRIPTS / "dev_target.sh").read_text(encoding="utf-8")
        installer = (_REPO / "install.sh").read_text(encoding="utf-8")

        unit = re.search(r'^UNIT_NAME="([^"]+)"$', installer, re.MULTILINE)
        assert unit is not None
        assert f'readonly SERVICE_UNIT="{unit.group(1)}"' in script


class TestTheTasks:
    """Without --target every dev task runs what it ran before; with it, scripts/dev_target.py."""

    _BEFORE: ClassVar[dict[str, list[str]]] = {
        "dev": [
            "python scripts/dev_backend_lock.py",
            "mise run build",
            "bash scripts/dev_steam.sh restart",
            "python backend/main.py",
        ],
        "dev:bpm": [
            "python scripts/dev_backend_lock.py",
            "mise run build",
            "bash scripts/dev_steam.sh restart bpm dp2",
            "python backend/main.py",
        ],
        "dev:desktop": [
            "python scripts/dev_backend_lock.py",
            "mise run build",
            "bash scripts/dev_steam.sh restart desktop dp2",
            "python backend/main.py",
        ],
        "dev:backend": ["python scripts/dev_backend_lock.py", "mise run build", "python backend/main.py"],
        "dev:frontend": [
            "python scripts/dev_backend_lock.py --require-running",
            "mise run build",
            "bash scripts/dev_steam.sh restart-remembered dp2",
        ],
        "dev:bpm-reset": ["bash scripts/dev_steam.sh restart bpm dp2"],
        "dev:desktop-reset": ["bash scripts/dev_steam.sh restart desktop dp2"],
        "dev:ui-scale": ["python3 scripts/dev_ui_scale.py 1.5"],
    }

    @staticmethod
    def _run_block(task: str, tmp_path: Path, **usage: str) -> list[str]:
        """The task's script run as mise runs it, with every program it calls standing in to record its words."""
        script = tomllib.loads((_REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"][task]["run"]
        fakes = tmp_path / "bin"
        fakes.mkdir(exist_ok=True)
        record = tmp_path / "calls"
        for program in ("python", "python3", "mise", "bash"):
            stand_in = fakes / program
            stand_in.write_text(f'#!/bin/sh\necho "{program} $*" >> "{record}"\n', encoding="utf-8")
            stand_in.chmod(0o755)
        env = {"PATH": f"{fakes}:/usr/bin:/bin", **{f"usage_{name}": value for name, value in usage.items()}}
        subprocess.run(["/bin/sh", "-e", "-c", script], env=env, cwd=tmp_path, check=True, timeout=_TIMEOUT)
        lines = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
        record.unlink(missing_ok=True)
        return lines

    @pytest.mark.parametrize("task", list(_BEFORE))
    def test_without_target_each_task_runs_what_it_ran_before(self, tmp_path: Path, task: str) -> None:
        assert self._run_block(task, tmp_path, display="dp2", mode="1.5") == self._BEFORE[task]

    @pytest.mark.parametrize(
        ("task", "usage", "words"),
        [
            ("dev", {"surface": "true"}, "--target deck --surface -- dev"),
            ("dev:bpm", {"display": "dp2"}, "--target deck -- dev:bpm dp2"),
            ("dev:desktop", {}, "--target deck -- dev:desktop "),
            ("dev:backend", {}, "--target deck -- dev:backend"),
            ("dev:frontend", {"display": "dp2", "surface": "true"}, "--target deck --surface -- dev:frontend dp2"),
            ("dev:bpm-reset", {"display": "dp2"}, "--target deck -- dev:bpm-reset dp2"),
            ("dev:desktop-reset", {}, "--target deck -- dev:desktop-reset "),
            ("dev:ui-scale", {"mode": "deck"}, "--target deck -- dev:ui-scale deck"),
            ("dev:ui-scale", {"mode": "deck", "stop": "true"}, "--target deck --stop -- dev:ui-scale deck"),
            ("dev:stop", {}, "--target deck -- dev:stop"),
            ("dev:log", {}, "--target deck -- dev:log"),
            ("dev:log", {"follow": "true"}, "--target deck --follow -- dev:log"),
            ("dev:status", {}, "--target deck -- dev:status"),
        ],
    )
    def test_with_target_each_task_hands_over_to_dev_target_py(
        self, tmp_path: Path, task: str, usage: dict[str, str], words: str
    ) -> None:
        assert self._run_block(task, tmp_path, target="deck", **usage) == [f"python scripts/dev_target.py {words}"]

    @pytest.mark.parametrize("task", ["dev:stop", "dev:log", "dev:status"])
    def test_the_tasks_only_a_target_has_refuse_without_one(self, tmp_path: Path, task: str) -> None:
        script = tomllib.loads((_REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"][task]["run"]

        result = subprocess.run(["/bin/sh", "-e", "-c", script], capture_output=True, text=True, check=False)

        assert result.returncode == 2
        assert "--target <name>" in result.stderr

    def test_ui_scale_stop_without_a_target_is_refused(self, tmp_path: Path) -> None:
        script = tomllib.loads((_REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"]["dev:ui-scale"]["run"]

        result = subprocess.run(
            ["/bin/sh", "-e", "-c", script],
            env={"usage_stop": "true", "usage_mode": "deck", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert "--stop" in result.stderr

    def test_every_dev_task_takes_target(self) -> None:
        tasks = tomllib.loads((_REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"]
        dev_tasks = [name for name in tasks if (name == "dev" or name.startswith("dev:")) and name != "dev:fake-github"]

        assert sorted(dev_tasks) == sorted([*self._BEFORE, "dev:stop", "dev:log", "dev:status"])
        for name in dev_tasks:
            assert 'flag "--target <name>"' in tasks[name]["usage"], name
            assert tasks[name]["raw"] is True, name
            builds = name in ("dev", "dev:bpm", "dev:desktop", "dev:backend", "dev:frontend")
            assert ("built here, Steam and the backend there" in tasks[name]["usage"]) is builds, name
        assert "--target" not in tasks["dev:fake-github"].get("usage", "")


class TestTheGuide:
    def test_the_guide_covers_the_remote_loop_where_the_scripts_point(self) -> None:
        guide = (_REPO / "docs/contributing/frontend-dev-loop.md").read_text(encoding="utf-8")
        section = guide.split("\n### The dev tasks on a target\n", 1)[1].split("\n## ", 1)[0]
        for task in ("dev:bpm", "dev:stop", "dev:log", "dev:status", "dev:ui-scale --stop", "--target <name>"):
            assert task in section, task
        for failure in ("no answer", "not the recorded one", "refused", "no such target"):
            assert failure in section, failure
        headings = {line.lstrip("#").strip() for line in guide.splitlines() if line.startswith("#")}
        for script in ("dev_target.py", "dev_target.sh"):
            text = " ".join((_SCRIPTS / script).read_text(encoding="utf-8").replace("#", " ").split())
            pointers = re.findall(r'docs/contributing/frontend-dev-loop\.md, "([^"]+)"', text)
            assert pointers, script
            assert set(pointers) <= headings, script
