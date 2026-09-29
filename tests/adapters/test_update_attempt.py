"""Tests for adapters.update_attempt — this program's record of an update attempt, in the state directory."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from adapters.update_attempt import UpdateAttemptFileAdapter
from domain.update_install import UPDATE_ATTEMPT_FILENAME, UpdateAttemptRecord

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
_RECORD = UpdateAttemptRecord(attempted_version="1.1.0", from_version="1.0.0", started_at="2026-09-29T10:00:00Z")


@pytest.fixture
def log():
    return []


@pytest.fixture
def adapter(tmp_path, log):
    return UpdateAttemptFileAdapter(state_dir=str(tmp_path / "state"), log_debug=log.append)


def _path(tmp_path) -> Path:
    return tmp_path / "state" / UPDATE_ATTEMPT_FILENAME


class TestTheRecord:
    def test_what_is_written_is_read_back(self, adapter, tmp_path):
        adapter.write(_RECORD)

        assert adapter.read() == _RECORD
        assert json.loads(_path(tmp_path).read_text(encoding="utf-8")) == {
            "attempted_version": "1.1.0",
            "from_version": "1.0.0",
            "started_at": "2026-09-29T10:00:00Z",
        }

    def test_a_write_leaves_no_temporary_file_behind(self, adapter, tmp_path):
        adapter.write(_RECORD)

        assert os.listdir(tmp_path / "state") == [UPDATE_ATTEMPT_FILENAME]

    def test_no_record_is_none_and_says_nothing(self, adapter, log):
        assert adapter.read() is None
        assert log == []

    @pytest.mark.parametrize("text", ["", "{half", "[]", json.dumps({"attempted_version": "1.1.0"})])
    def test_a_record_that_says_nothing_usable_is_none_and_reaches_the_debug_log(self, adapter, tmp_path, log, text):
        _path(tmp_path).parent.mkdir()
        _path(tmp_path).write_text(text, encoding="utf-8")

        assert adapter.read() is None
        assert log

    def test_removing_takes_it_away_and_removing_none_is_nothing_to_do(self, adapter, tmp_path):
        adapter.write(_RECORD)

        adapter.remove()
        adapter.remove()

        assert not _path(tmp_path).exists()


class TestTheBackendIsItsOnlyWriter:
    """Read off the source, by name: a record path assembled from pieces, or a write in a subprocess, slips past."""

    def test_no_backend_module_but_the_adapter_and_the_constant_s_home_names_the_record(self):
        naming = {
            path.relative_to(_BACKEND).as_posix()
            for path in _BACKEND.rglob("*.py")
            if "_vendor" not in path.parts and _names_the_record(path.read_text(encoding="utf-8"))
        }

        assert naming == {"adapters/update_attempt.py", "domain/update_install.py"}

    def test_the_installer_never_names_it(self):
        scripts = [
            _REPO / "install.sh",
            *sorted((_REPO / "scripts").rglob("*.sh")),
            *sorted((_REPO / "bin").rglob("*")),
        ]

        assert [str(path) for path in scripts if path.is_file() and UPDATE_ATTEMPT_FILENAME in path.read_text()] == []


def _names_the_record(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.alias) and "UPDATE_ATTEMPT_FILENAME" in (
            node.id if isinstance(node, ast.Name) else node.name
        ):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "UPDATE_ATTEMPT_FILENAME":
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == UPDATE_ATTEMPT_FILENAME:
            return True
    return False
