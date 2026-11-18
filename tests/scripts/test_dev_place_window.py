"""Tests for ``scripts/dev_place_window.sh --arm`` — what it leaves running once it returns.

KWin is a stand-in ``qdbus6`` that answers a script id, and ``sleep`` one that
waits for the test to let it go, so the self-cleaning watcher the arm leaves
behind is still there when the test looks. Nothing reaches a real KWin.
"""

from __future__ import annotations

import os
import shutil
import subprocess
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
    (tools / "sleep").write_text(
        f'#!/bin/sh\nwhile [ ! -e "{release}" ]; do "{real_sleep}" 0.05; done\n', encoding="utf-8"
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

    assert process.returncode == 0
