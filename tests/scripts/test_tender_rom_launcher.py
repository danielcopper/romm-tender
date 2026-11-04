"""Tests for ``bin/tender-rom-launcher`` — the Steam shortcut launcher.

Steam starts the launcher as ``tender-rom-launcher <appId>``. The start check
has handed it that start's command in ``$XDG_RUNTIME_DIR/romm-tender/start/<appId>``
(#2221 D15): the folder to start in, then the program and its arguments, each
ended by a NUL. These tests run the real script with ``bash`` under a runtime
directory of their own, and assert it runs exactly what was handed over, in the
folder handed over, once — and nothing without a hand-over, or with one older
than 30 seconds.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_LAUNCHER = Path(__file__).resolve().parents[2] / "bin" / "tender-rom-launcher"

_APP_ID = "2981234567"


def _handover(runtime: Path, app_id: str = _APP_ID) -> Path:
    return runtime / "romm-tender" / "start" / app_id


def _hand_over(runtime: Path, folder: Path, *argv: str, app_id: str = _APP_ID, age: float = 0) -> Path:
    """Write the record the start check writes, *age* seconds old."""
    path = _handover(runtime, app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(word.encode() + b"\0" for word in (str(folder), *argv)))
    if age:
        stamp = path.stat().st_mtime - age
        os.utime(path, (stamp, stamp))
    return path


def _run(runtime: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"]}
    if runtime is not None:
        env["XDG_RUNTIME_DIR"] = str(runtime)
    return subprocess.run(["bash", str(_LAUNCHER), *args], capture_output=True, text=True, check=False, env=env)


def test_it_runs_the_handed_over_command_in_the_folder_handed_over(tmp_path: Path) -> None:
    folder = tmp_path / "emulator"
    folder.mkdir()
    _hand_over(tmp_path, folder, "/bin/sh", "-c", 'printf "%s|%s" "$PWD" "$1"', "sh", "two words")

    result = _run(tmp_path, _APP_ID)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{folder}|two words"


def test_it_takes_the_hand_over_once(tmp_path: Path) -> None:
    _hand_over(tmp_path, tmp_path, "/usr/bin/printf", "started")

    first = _run(tmp_path, _APP_ID)
    second = _run(tmp_path, _APP_ID)

    assert (first.returncode, first.stdout) == (0, "started")
    assert second.returncode == 1
    assert second.stdout == ""
    assert list(_handover(tmp_path).parent.iterdir()) == []


def test_without_a_hand_over_it_starts_nothing(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    _hand_over(tmp_path, tmp_path, "/usr/bin/touch", str(marker), app_id="111")

    result = _run(tmp_path, _APP_ID)

    assert result.returncode == 1
    assert f"no start was handed over for {_APP_ID}" in result.stderr
    assert not marker.exists()


def test_a_hand_over_older_than_thirty_seconds_starts_nothing_and_is_removed(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    path = _hand_over(tmp_path, tmp_path, "/usr/bin/touch", str(marker), age=31)

    result = _run(tmp_path, _APP_ID)

    assert result.returncode == 1
    assert "older than 30 seconds" in result.stderr
    assert not marker.exists()
    assert not path.exists()
    assert list(path.parent.iterdir()) == []


def test_a_hand_over_within_thirty_seconds_starts(tmp_path: Path) -> None:
    _hand_over(tmp_path, tmp_path, "/usr/bin/printf", "started", age=25)

    result = _run(tmp_path, _APP_ID)

    assert (result.returncode, result.stdout) == (0, "started")


def test_every_word_after_the_app_id_is_ignored(tmp_path: Path) -> None:
    _hand_over(tmp_path, tmp_path, "/usr/bin/printf", "%s", "as handed over")

    result = _run(tmp_path, _APP_ID, "flatpak", "run", "net.retrodeck.retrodeck", "'/old/rom.iso'")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "as handed over"


def test_without_an_app_id_it_starts_nothing(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 1
    assert "no appId given" in result.stderr


def test_a_first_word_that_is_no_app_id_starts_nothing(tmp_path: Path) -> None:
    """A shortcut still carrying an old baked command names a program first, never an appId."""
    _hand_over(tmp_path, tmp_path, "/usr/bin/printf", "x", app_id="flatpak")

    result = _run(tmp_path, "flatpak", "run")

    assert result.returncode == 1
    assert result.stdout == ""


def test_without_a_runtime_directory_it_starts_nothing(tmp_path: Path) -> None:
    result = _run(None, _APP_ID)

    assert result.returncode == 1
    assert "no runtime directory" in result.stderr


def test_a_hand_over_that_names_no_program_starts_nothing(tmp_path: Path) -> None:
    _hand_over(tmp_path, tmp_path)

    result = _run(tmp_path, _APP_ID)

    assert result.returncode == 1
    assert "names no program" in result.stderr


def test_the_programs_exit_code_is_the_launchers(tmp_path: Path) -> None:
    _hand_over(tmp_path, tmp_path, "/bin/sh", "-c", "exit 7")

    assert _run(tmp_path, _APP_ID).returncode == 7
