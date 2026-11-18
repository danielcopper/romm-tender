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

import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
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
    python: str = sys.executable
    """The target's ``TENDER_PYTHON``."""

    def save(self) -> None:
        self.world.save(self.world_file)

    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.build_home),
            "PATH": self.tools_path,
            WORLD_VARIABLE: str(self.world_file),
            "TENDER_PYTHON": self.python,
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
        return subprocess.Popen(
            self.argv(task, *words),
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    @property
    def root(self) -> Path:
        return self.target_home / _ROOT

    @property
    def log(self) -> Path:
        return self.target_home / _LOG

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
    world = World(
        targets={_ADDRESS: Target(home=str(target_home), client_address=_SEEN_FROM, host_key=_HOST_KEY)},
        log=str(tmp_path / "log"),
    )
    path = fake_path(tools)
    world_file = tmp_path / "world.json"
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv(WORLD_VARIABLE, str(world_file))
    world.save(world_file)
    assert setup_remote_target.main(["deck", f"deck@{_ADDRESS}"]) == 0
    built = Loop(world, world_file, path, _checkout(tmp_path / "checkout"), home, target_home)
    yield built
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
        """#2846 D2: Ctrl-C ends only the following, says the backend runs on, and names dev:stop."""
        loop.log.parent.mkdir(parents=True)
        loop.log.write_text("an earlier run's line\n", encoding="utf-8")
        process = loop.start("dev")

        out, err = _interrupt(process, "backend started")

        assert process.returncode == 0, err
        assert "an earlier run's line" not in out
        assert "backend started" in out
        assert "The backend keeps running on deck" in err
        assert "`mise run dev:stop --target deck` stops it" in err
        backend = units(loop.world)["romm-tender-dev"]["pid"]
        assert _alive(backend)
        started = commands(loop.world, "systemd-run")
        assert started[-1]["argv"][-2:] == [sys.executable, str(loop.root / "current/backend/main.py")]
        assert "--unit=romm-tender-dev" in started[-1]["argv"]
        assert any(call["argv"][-1:] == ["steam"] for call in started)

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
        loop.world.follow_holds = False
        loop.run("dev:ui-scale")
        scale = units(loop.world)["romm-tender-dev-ui-scale"]["pid"]

        loop.run("dev:stop")

        assert not _alive(backend)
        assert _alive(scale)

    def test_dev_ui_scale_runs_as_a_unit_and_stop_ends_it(self, loop: Loop) -> None:
        loop.world.follow_holds = False
        loop.world.journal = ["Restored: AUTOMATIC scaling (the state captured before this run)."]

        started = loop.run("dev:ui-scale", "1.5")
        scale = units(loop.world)["romm-tender-dev-ui-scale"]["pid"]
        stopped = loop.run("dev:ui-scale", "--stop")

        assert started.returncode == 0, started.stderr
        run = next(
            call for call in commands(loop.world, "systemd-run") if "--unit=romm-tender-dev-ui-scale" in call["argv"]
        )
        assert run["argv"][-3:] == [sys.executable, str(loop.root / "incoming/scripts/dev_ui_scale.py"), "1.5"]
        assert "`mise run dev:ui-scale --stop --target deck` restores the scale and the window" in started.stderr
        assert stopped.returncode == 0, stopped.stderr
        assert not _alive(scale)
        assert "Restored: AUTOMATIC scaling" in stopped.stdout
        assert "stopped dev:ui-scale on deck" in stopped.stdout

    def test_dev_ui_scale_while_one_runs_is_refused(self, loop: Loop) -> None:
        loop.world.follow_holds = False
        loop.run("dev:ui-scale")

        again = loop.run("dev:ui-scale")

        assert again.returncode == 1
        assert "dev:ui-scale already runs on deck" in again.stderr

    def test_dev_ui_scale_stop_with_none_running_says_so(self, loop: Loop) -> None:
        result = loop.run("dev:ui-scale", "--stop")

        assert result.returncode == 0, result.stderr
        assert "no dev:ui-scale runs on deck" in result.stdout

    def test_dev_status_names_the_lock_holder_and_whether_dev_ui_scale_runs(self, loop: Loop) -> None:
        free = loop.run("dev:status")
        loop.hold_lock("romm-tender")
        loop.world.follow_holds = False
        loop.run("dev:ui-scale")
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
        assert "dispatcher refused the request, so it is older than this checkout's" in result.stderr
        assert f"mise run setup:remote-target deck deck@{_ADDRESS}" in result.stderr


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

    def test_a_word_the_dispatcher_would_refuse_is_refused_here(self, loop: Loop) -> None:
        result = loop.run("dev:bpm", "dp2;x")

        assert result.returncode == 1
        assert "cannot be sent to a target" in result.stderr
        assert commands(loop.world, "mise") == []


class TestTheTargetsHalf:
    def test_no_python_at_tender_python_is_refused(self, loop: Loop) -> None:
        loop.python = str(loop.target_home / "no-python")

        result = loop.run("dev:status")

        assert result.returncode == 1
        assert f"no Python at {loop.python} on deck" in result.stderr

    def test_a_python_older_than_3_12_is_refused(self, loop: Loop, tmp_path: Path) -> None:
        old = tmp_path / "python3.11"
        old.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        old.chmod(0o755)
        loop.python = str(old)
        (loop.root / "current/sentinel").write_text("running\n", encoding="utf-8")

        result = loop.run("dev")

        assert result.returncode == 1
        assert f"{old} on deck is older than 3.12" in result.stderr
        assert [entry.name for entry in (loop.root / "current").iterdir()] == ["sentinel"]

    @pytest.mark.parametrize("value", ["$'odd\\nvalue'", "$':0'"])
    def test_a_session_value_in_quoting_is_not_taken(self, loop: Loop, value: str) -> None:
        loop.world.session_environment = [f"DISPLAY={value}", "WAYLAND_DISPLAY=wayland-0"]

        loop.run("dev:desktop-reset")

        _wait_for(lambda: any(call["argv"][-1:] == ["steam"] for call in commands(loop.world, "systemd-run")))
        steam = next(call for call in commands(loop.world, "systemd-run") if call["argv"][-1:] == ["steam"])
        assert "DISPLAY" not in steam["env"]
        assert steam["env"]["WAYLAND_DISPLAY"] == "wayland-0"

    def test_the_names_it_reads_by_are_the_backends_and_the_installers(self) -> None:
        from domain.user_data_location import APP_DIR_NAME
        from host.logging_setup import LOG_FILENAME

        script = (_SCRIPTS / "dev_target.sh").read_text(encoding="utf-8")
        installer = (_REPO / "install.sh").read_text(encoding="utf-8")

        assert f'APP_DIR="{APP_DIR_NAME}"' in script
        assert f'LOG_NAME="{LOG_FILENAME}"' in script
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
