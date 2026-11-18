"""Tests for ``scripts/dev_place_window.sh --arm`` — what it leaves running once it returns.

KWin is a stand-in ``qdbus6`` that answers a script id, and ``sleep`` one that
waits for the test to let it go, so the self-cleaning watcher the arm leaves
behind is still there when the test looks. The test ends that watcher itself
before it returns. Nothing reaches a real KWin.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_place_window.sh"


def test_the_watcher_left_behind_holds_none_of_the_callers_output(tmp_path: Path) -> None:
    """Over ssh (a dev task's --target) a session ends only once nothing holds its output."""
    tools = tmp_path / "bin"
    tools.mkdir()
    release = tmp_path / "release"
    (tools / "qdbus6").write_text('#!/bin/sh\ncase "$*" in *loadScript*) echo 7 ;; esac\n', encoding="utf-8")
    real_sleep = shutil.which("sleep")
    assert real_sleep is not None
    started = tmp_path / "started"
    # Bounded as well: a stand-in that starts after this test's directory is gone would never see the release.
    (tools / "sleep").write_text(
        f'#!/bin/sh\necho "$$ $PPID" >> "{started}"\n'
        f'i=0\nwhile [ ! -e "{release}" ] && [ "$i" -lt 200 ]; do "{real_sleep}" 0.05; i=$((i + 1)); done\n',
        encoding="utf-8",
    )
    for name in ("qdbus6", "sleep"):
        (tools / name).chmod(0o755)
    for tool in ("bash", "mktemp", "cat", "sed", "rm"):
        found = shutil.which(tool)
        assert found is not None
        (tools / tool).symlink_to(found)
    process = subprocess.Popen(
        ["bash", str(_SCRIPT), "--arm", "bpm", "DP-2"],
        env={"PATH": str(tools), "TMPDIR": str(tmp_path), "HOME": os.environ["HOME"]},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.communicate(timeout=10)
    finally:
        release.touch()
        _end(started)

    assert process.returncode == 0


def _end(started: Path) -> None:
    """End the stand-in ``sleep`` and the watcher it runs in, once the watcher has started it.

    The watcher runs in the background and may start its ``sleep`` only after
    the arm has returned, so the test waits for it rather than ending nothing.
    """
    deadline = time.monotonic() + 10
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    pids = [int(pid) for line in (started.read_text().splitlines() if started.exists() else []) for pid in line.split()]
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while any(Path(f"/proc/{pid}").exists() and _running(pid) for pid in pids):
        assert time.monotonic() < deadline, f"still running: {pids}"
        time.sleep(0.02)


def _running(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False
