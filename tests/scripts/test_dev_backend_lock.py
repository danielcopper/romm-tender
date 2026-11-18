"""Tests for ``scripts/dev_backend_lock.py --holder`` — the lock reading a target's ``dev_target.sh`` asks for.

The script runs as the dev tasks run it, in a process of its own, under the
test's home, where it finds the lock at the backend's default place.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_backend_lock.py"


def _holder(home: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--holder"],
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout.splitlines()


@pytest.fixture
def lock(home: Path) -> Path:
    path = home / ".local/share/romm-tender/backend.lock"
    path.parent.mkdir(parents=True)
    return path


def test_no_lock_file_is_free(home: Path, lock: Path) -> None:
    assert _holder(home) == ["free", "", str(lock)]


def test_a_lock_nobody_holds_is_free(home: Path, lock: Path) -> None:
    lock.touch()

    assert _holder(home) == ["free", "", str(lock)]


def test_a_held_lock_names_the_process_that_holds_it(home: Path, lock: Path) -> None:
    with open(lock, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)

        assert _holder(home) == ["held", str(os.getpid()), str(lock)]


def test_a_lock_that_cannot_be_read_is_unknown_and_refuses_nothing(home: Path, lock: Path) -> None:
    lock.touch()
    lock.chmod(0)

    assert _holder(home) == ["unknown", "", str(lock)]


def test_holder_and_require_running_are_two_questions_not_one(home: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--holder", "--require-running"],
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


@pytest.mark.parametrize(
    ("variables", "folder"),
    [
        ({}, ".local/state/romm-tender"),
        ({"XDG_STATE_HOME": "{home}/xdg-state"}, "xdg-state/romm-tender"),
        ({"TENDER_STATE_DIR": "{home}/tender-state", "XDG_STATE_HOME": "{home}/xdg-state"}, "tender-state"),
    ],
    ids=["default", "xdg", "tender-before-xdg"],
)
def test_log_path_is_where_the_backends_resolver_puts_its_log(
    home: Path, variables: dict[str, str], folder: str
) -> None:
    """dev_target.sh follows the log there rather than restating the ladder in shell."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--log-path"],
        env={"HOME": str(home), "PATH": os.environ["PATH"], **{k: v.format(home=home) for k, v in variables.items()}},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{home / folder / 'backend.log'}\n"
