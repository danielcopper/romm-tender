"""Tests for the repo hooks that hand over to the developer's global git hooks.

``mise run setup`` points ``core.hooksPath`` at ``.githooks``, which replaces a
global hooks path rather than adding to it. ``commit-msg``, ``pre-merge-commit``
and ``pre-push`` therefore run the global hook of the same name, if there is an
executable one, with their own arguments and stdin, and exit with its status.

Every case runs the real hook as git would — the file itself, from a working
directory — with an environment built from a fixed set rather than inherited:
``HOME`` is the test's ``home``, ``GIT_CONFIG_GLOBAL`` names a gitconfig under
``tmp_path`` and ``GIT_CONFIG_NOSYSTEM`` shuts out the system one, so neither
the developer's real global config nor their real global hooks can be reached.
The global hook is a stand-in that records what it was given and exits with a
chosen status.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parents[2] / ".githooks"

# What git hands each hook: the message file for commit-msg, nothing for
# pre-merge-commit, the remote's name and URL for pre-push — which also reads
# one line per ref being pushed from stdin.
_MESSAGE_FILE = ".git/COMMIT_EDITMSG"
_REMOTE = ("origin", "git@example.invalid:owner/repo.git")
_REF_LINE = f"refs/heads/topic {'1' * 40} refs/heads/topic {'0' * 40}\n"

_CASES = {
    "commit-msg": ((_MESSAGE_FILE,), ""),
    "pre-merge-commit": ((), ""),
    "pre-push": (_REMOTE, _REF_LINE),
}

_STAND_IN = """#!/usr/bin/env bash
for arg in "$@"; do printf '%s\\n' "$arg"; done > "{record}/args"
cat > "{record}/stdin"
exit {status}
"""


def _write_global_config(tmp_path: Path, hooks_path: str | None) -> Path:
    config = tmp_path / "gitconfig"
    config.write_text("" if hooks_path is None else f"[core]\n\thooksPath = {hooks_path}\n", encoding="utf-8")
    return config


def _write_stand_in(global_hooks: Path, name: str, record: Path, status: int, *, executable: bool = True) -> None:
    global_hooks.mkdir(parents=True, exist_ok=True)
    record.mkdir(parents=True, exist_ok=True)
    hook = global_hooks / name
    hook.write_text(_STAND_IN.format(record=record, status=status), encoding="utf-8")
    hook.chmod(0o755 if executable else 0o644)


def _run_hook(name: str, home: Path, tmp_path: Path, config: Path) -> subprocess.CompletedProcess[str]:
    args, stdin = _CASES[name]
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        [str(_HOOKS / name), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("name", sorted(_CASES))
class TestGlobalHookHandover:
    def test_the_repo_hook_is_executable(self, name: str) -> None:
        assert os.access(_HOOKS / name, os.X_OK)

    def test_a_refusing_global_hook_refuses_with_its_own_status(self, name: str, home: Path, tmp_path: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        _write_stand_in(global_hooks, name, tmp_path / "record", status=3)

        result = _run_hook(name, home, tmp_path, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 3

    def test_an_accepting_global_hook_accepts(self, name: str, home: Path, tmp_path: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, name, record, status=0)

        result = _run_hook(name, home, tmp_path, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 0
        assert (record / "args").exists()

    def test_the_arguments_and_stdin_reach_the_global_hook(self, name: str, home: Path, tmp_path: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, name, record, status=0)
        args, stdin = _CASES[name]

        _run_hook(name, home, tmp_path, _write_global_config(tmp_path, str(global_hooks)))

        assert (record / "args").read_text(encoding="utf-8").splitlines() == list(args)
        assert (record / "stdin").read_text(encoding="utf-8") == stdin

    def test_a_hooks_path_under_the_home_is_expanded(self, name: str, home: Path, tmp_path: Path) -> None:
        record = tmp_path / "record"
        _write_stand_in(home / "global-hooks", name, record, status=3)

        result = _run_hook(name, home, tmp_path, _write_global_config(tmp_path, "~/global-hooks"))

        assert result.returncode == 3
        assert (record / "args").exists()

    def test_no_global_hooks_path_passes(self, name: str, home: Path, tmp_path: Path) -> None:
        result = _run_hook(name, home, tmp_path, _write_global_config(tmp_path, None))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_no_global_hook_of_that_name_passes(self, name: str, home: Path, tmp_path: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        global_hooks.mkdir()

        result = _run_hook(name, home, tmp_path, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_a_global_hook_that_is_not_executable_is_not_run(self, name: str, home: Path, tmp_path: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, name, record, status=3, executable=False)

        result = _run_hook(name, home, tmp_path, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 0
        assert not (record / "args").exists()
