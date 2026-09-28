"""Tests for the repo hooks that hand over to the developer's global git hooks.

``mise run setup`` points ``core.hooksPath`` at ``.githooks``, which replaces a
global hooks path rather than adding to it. ``commit-msg``, ``pre-merge-commit``
and ``pre-push`` therefore run the global hook of the same name, if there is an
executable one, with their own arguments and stdin, and exit with its status;
``pre-commit`` runs the global ``pre-commit`` before its formatters and refuses
the commit if it does. A global hooks path that is ``.githooks`` itself must
not make a hook call itself for ever.

Every case runs the real hook as git would — the file itself, from inside a
throwaway git repository — with an environment built from a fixed set rather
than inherited: ``HOME`` is the test's ``home``, ``GIT_CONFIG_GLOBAL`` names a
gitconfig under ``tmp_path`` and ``GIT_CONFIG_NOSYSTEM`` shuts out the system
one, so neither the developer's real global config nor their real global hooks
can be reached. ``PATH`` is the one value taken from this process. The global
hook is a stand-in that records what it was given and exits with a chosen
status.

A hook that calls itself never returns, so every run has a deadline, and the
hook is started in a session of its own so that everything it spawned is killed
with it when the deadline passes.
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parents[2] / ".githooks"
_DEADLINE_SECONDS = 10

# What git hands each pass-through hook: the message file for commit-msg,
# nothing for pre-merge-commit, the remote's name and URL for pre-push — which
# also reads one line per ref being pushed from stdin.
_MESSAGE_FILE = ".git/COMMIT_EDITMSG"
_REMOTE = ("origin", "git@example.invalid:owner/repo.git")
_REF_LINE = f"refs/heads/topic {'1' * 40} refs/heads/topic {'0' * 40}\n"

_PASS_THROUGH = {
    "commit-msg": ((_MESSAGE_FILE,), ""),
    "pre-merge-commit": ((), ""),
    "pre-push": (_REMOTE, _REF_LINE),
}

_STAND_IN = """#!/usr/bin/env bash
for arg in "$@"; do printf '%s\\n' "$arg"; done > "{record}/args"
cat > "{record}/stdin"
exit {status}
"""


@dataclass(frozen=True)
class _Result:
    returncode: int
    stdout: str
    stderr: str


def _env(home: Path, config: Path) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], stdin: str = "") -> _Result:
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(stdin, timeout=_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail(f"{argv[0]} did not return within {_DEADLINE_SECONDS}s — it keeps calling itself")
    return _Result(process.returncode, stdout, stderr)


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


@pytest.fixture
def repo(home: Path, tmp_path: Path) -> Path:
    """An empty git repository with nothing staged, whose ``.githooks`` is this repo's.

    ``pre-commit`` asks git what is staged, so it has to run inside a
    repository; the pass-through hooks run there too, so every hook sees the
    same surroundings. The ``.githooks`` link is what a relative global hooks
    path of ``.githooks`` resolves to from here, without running anything in
    the real checkout.
    """
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=path,
        env=_env(home, _write_global_config(tmp_path, None)),
        check=True,
        timeout=_DEADLINE_SECONDS,
    )
    (path / ".githooks").symlink_to(_HOOKS, target_is_directory=True)
    return path


def _run_pass_through(name: str, home: Path, repo: Path, config: Path) -> _Result:
    args, stdin = _PASS_THROUGH[name]
    return _run([str(_HOOKS / name), *args], cwd=repo, env=_env(home, config), stdin=stdin)


def _run_pre_commit(home: Path, repo: Path, config: Path) -> _Result:
    return _run([str(_HOOKS / "pre-commit")], cwd=repo, env=_env(home, config))


@pytest.mark.parametrize("name", sorted(_PASS_THROUGH))
class TestPassThroughHooks:
    def test_the_repo_hook_is_executable(self, name: str) -> None:
        assert os.access(_HOOKS / name, os.X_OK)

    def test_a_refusing_global_hook_refuses_with_its_own_status(
        self, name: str, home: Path, tmp_path: Path, repo: Path
    ) -> None:
        global_hooks = tmp_path / "global-hooks"
        _write_stand_in(global_hooks, name, tmp_path / "record", status=3)

        result = _run_pass_through(name, home, repo, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 3

    def test_an_accepting_global_hook_accepts(self, name: str, home: Path, tmp_path: Path, repo: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, name, record, status=0)

        result = _run_pass_through(name, home, repo, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 0
        assert (record / "args").exists()

    def test_the_arguments_and_stdin_reach_the_global_hook(
        self, name: str, home: Path, tmp_path: Path, repo: Path
    ) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, name, record, status=0)
        args, stdin = _PASS_THROUGH[name]

        _run_pass_through(name, home, repo, _write_global_config(tmp_path, str(global_hooks)))

        assert (record / "args").read_text(encoding="utf-8").splitlines() == list(args)
        assert (record / "stdin").read_text(encoding="utf-8") == stdin

    def test_a_hooks_path_under_the_home_is_expanded(self, name: str, home: Path, tmp_path: Path, repo: Path) -> None:
        record = tmp_path / "record"
        _write_stand_in(home / "global-hooks", name, record, status=3)

        result = _run_pass_through(name, home, repo, _write_global_config(tmp_path, "~/global-hooks"))

        assert result.returncode == 3
        assert (record / "args").exists()

    def test_no_global_hooks_path_passes(self, name: str, home: Path, tmp_path: Path, repo: Path) -> None:
        result = _run_pass_through(name, home, repo, _write_global_config(tmp_path, None))

        assert result == _Result(0, "", "")

    def test_no_global_hook_of_that_name_passes(self, name: str, home: Path, tmp_path: Path, repo: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        global_hooks.mkdir()

        result = _run_pass_through(name, home, repo, _write_global_config(tmp_path, str(global_hooks)))

        assert result == _Result(0, "", "")

    def test_a_global_hook_that_is_not_executable_is_not_run(
        self, name: str, home: Path, tmp_path: Path, repo: Path
    ) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, name, record, status=3, executable=False)

        result = _run_pass_through(name, home, repo, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 0
        assert not (record / "args").exists()

    @pytest.mark.parametrize("hooks_path", [str(_HOOKS), ".githooks"], ids=["absolute", "relative"])
    def test_a_global_hooks_path_that_is_this_directory_passes_promptly(
        self, name: str, hooks_path: str, home: Path, tmp_path: Path, repo: Path
    ) -> None:
        result = _run_pass_through(name, home, repo, _write_global_config(tmp_path, hooks_path))

        assert result == _Result(0, "", "")


class TestPreCommitChain:
    """``pre-commit``'s chain block, run in a repository with nothing staged.

    With nothing staged every formatter section skips itself, so the exit
    status is the chain's alone. A section whose tool is missing says so on
    stdout, which is why these cases assert the status only.
    """

    def test_a_refusing_global_hook_refuses_the_commit(self, home: Path, tmp_path: Path, repo: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, "pre-commit", record, status=3)

        result = _run_pre_commit(home, repo, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 1
        assert (record / "args").exists()

    def test_an_accepting_global_hook_lets_the_commit_through(self, home: Path, tmp_path: Path, repo: Path) -> None:
        global_hooks = tmp_path / "global-hooks"
        record = tmp_path / "record"
        _write_stand_in(global_hooks, "pre-commit", record, status=0)

        result = _run_pre_commit(home, repo, _write_global_config(tmp_path, str(global_hooks)))

        assert result.returncode == 0
        assert (record / "args").exists()

    def test_a_hooks_path_under_the_home_is_expanded(self, home: Path, tmp_path: Path, repo: Path) -> None:
        record = tmp_path / "record"
        _write_stand_in(home / "global-hooks", "pre-commit", record, status=3)

        result = _run_pre_commit(home, repo, _write_global_config(tmp_path, "~/global-hooks"))

        assert result.returncode == 1
        assert (record / "args").exists()

    def test_no_global_hooks_path_passes(self, home: Path, tmp_path: Path, repo: Path) -> None:
        result = _run_pre_commit(home, repo, _write_global_config(tmp_path, None))

        assert result.returncode == 0

    @pytest.mark.parametrize("hooks_path", [str(_HOOKS), ".githooks"], ids=["absolute", "relative"])
    def test_a_global_hooks_path_that_is_this_directory_passes_promptly(
        self, hooks_path: str, home: Path, tmp_path: Path, repo: Path
    ) -> None:
        result = _run_pre_commit(home, repo, _write_global_config(tmp_path, hooks_path))

        assert result.returncode == 0
