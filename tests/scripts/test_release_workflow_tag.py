"""Tests for the release workflow's "Determine tag name" step.

The step's own script is cut out of ``.github/workflows/release.yml`` and run
with ``bash -e``, the shell GitHub runs a ``run:`` block in when the step names
none, in a ``tmp_path`` holding the ``package.json`` and ``plugin.json`` the
tree would carry.
``gh`` is a stub that fails, so a test sees it if the step asks GitHub for a
release instead of reading the tree.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
_STEP = "- name: Determine tag name"


def _step_script() -> str:
    """The ``run: |`` block of the step, dedented."""
    lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == _STEP)
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    run_indent = len(lines[run]) - len(lines[run].lstrip())
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= run_indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


def _run_step(
    tmp_path: Path,
    *,
    event: str,
    package: str,
    plugin: str = '{"name": "Tender", "version": "0.33.1"}',
    tag_name: str = "",
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the step as the runner would; answer the process and what it wrote to ``GITHUB_OUTPUT``."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "package.json").write_text(package, encoding="utf-8")
    (work / "plugin.json").write_text(plugin, encoding="utf-8")
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    gh = stub_bin / "gh"
    gh.write_text("#!/bin/sh\necho 'gh was asked' >&2\nexit 99\n", encoding="utf-8")
    gh.chmod(0o755)
    output = tmp_path / "github_output"
    output.touch()
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GITHUB_EVENT_NAME": event,
        "GITHUB_OUTPUT": str(output),
        "TAG_NAME": tag_name,
    }
    result = subprocess.run(
        ["bash", "-e", "-c", _step_script()],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output.read_text(encoding="utf-8")


def test_manual_run_takes_the_tag_from_the_trees_version(tmp_path: Path) -> None:
    result, output = _run_step(
        tmp_path, event="workflow_dispatch", package='{"name": "romm-tender", "version": "0.33.1"}'
    )

    assert result.returncode == 0, result.stderr
    assert output == "tag=tender-v0.33.1\n"
    assert "gh was asked" not in result.stderr


@pytest.mark.parametrize(
    "version",
    ["", ', "version": ""', ', "version": 33'],
    ids=["missing", "empty", "not-a-string"],
)
def test_manual_run_fails_without_a_version_in_the_tree(tmp_path: Path, version: str) -> None:
    # Both manifests alike, so the step cannot fail on their disagreeing instead.
    result, output = _run_step(
        tmp_path,
        event="workflow_dispatch",
        package=f'{{"name": "romm-tender"{version}}}',
        plugin=f'{{"name": "Tender"{version}}}',
    )

    assert result.returncode != 0
    assert output == ""
    assert "::error::package.json carries no version" in result.stdout


def test_manual_run_fails_where_the_plugin_manifest_carries_another_version(tmp_path: Path) -> None:
    result, output = _run_step(
        tmp_path,
        event="workflow_dispatch",
        package='{"name": "romm-tender", "version": "0.33.1"}',
        plugin='{"name": "Tender", "version": "0.33.0"}',
    )

    assert result.returncode != 0
    assert output == ""
    assert "::error::" in result.stdout
    assert "0.33.0" in result.stdout


def test_push_takes_the_tag_release_please_created(tmp_path: Path) -> None:
    result, output = _run_step(
        tmp_path,
        event="push",
        tag_name="tender-v0.34.0",
        package='{"name": "romm-tender", "version": "0.33.1"}',
    )

    assert result.returncode == 0, result.stderr
    assert output == "tag=tender-v0.34.0\n"


def test_push_fails_where_release_please_named_no_tag(tmp_path: Path) -> None:
    result, output = _run_step(tmp_path, event="push", package='{"name": "romm-tender", "version": "0.33.1"}')

    assert result.returncode != 0
    assert output == ""
    assert "::error::" in result.stdout
    assert "gh was asked" not in result.stderr
