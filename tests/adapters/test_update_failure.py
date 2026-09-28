"""Tests for adapters.update_failure — the installer's record, read off the state directory."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from adapters.update_failure import UpdateFailureFileAdapter
from domain.update_outcome import UPDATE_FAILURE_FILENAME, UpdateFailure

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
_ADAPTER = _BACKEND / "adapters" / "update_failure.py"
_RECORD = {"attempted_version": "1.3.0", "restored_version": "1.2.3", "rolled_back_at": "2026-09-25T10:15:00Z"}


@pytest.fixture
def log():
    return []


@pytest.fixture
def adapter(tmp_path, log):
    return UpdateFailureFileAdapter(state_dir=str(tmp_path), log_debug=log.append)


def _write(tmp_path, text: str) -> None:
    (tmp_path / UPDATE_FAILURE_FILENAME).write_text(text, encoding="utf-8")


class TestReadUpdateFailure:
    def test_reads_the_record_in_the_state_directory(self, adapter, tmp_path):
        _write(tmp_path, json.dumps(_RECORD) + "\n")

        assert adapter.read_update_failure() == UpdateFailure(
            attempted_version="1.3.0", restored_version="1.2.3", rolled_back_at="2026-09-25T10:15:00Z"
        )

    def test_no_record_is_none_and_says_nothing(self, adapter, log):
        """The ordinary case: no update was ever rolled back, or a later one removed the record."""
        assert adapter.read_update_failure() is None
        assert log == []

    def test_a_record_that_went_away_is_gone_on_the_next_read(self, adapter, tmp_path):
        _write(tmp_path, json.dumps(_RECORD))
        assert adapter.read_update_failure() is not None

        (tmp_path / UPDATE_FAILURE_FILENAME).unlink()

        assert adapter.read_update_failure() is None

    @pytest.mark.parametrize("text", ["", "{half", "[]", json.dumps({**_RECORD, "restored_version": ""})])
    def test_a_malformed_record_is_none_and_reaches_the_debug_log(self, adapter, tmp_path, log, text):
        _write(tmp_path, text)

        assert adapter.read_update_failure() is None
        assert log

    def test_bytes_that_are_not_text_are_none(self, adapter, tmp_path, log):
        (tmp_path / UPDATE_FAILURE_FILENAME).write_bytes(b"\xff\xfe\x00")

        assert adapter.read_update_failure() is None
        assert log

    def test_a_record_that_cannot_be_opened_is_none(self, adapter, tmp_path, log):
        """A directory under the record's name stands in for every open that fails."""
        (tmp_path / UPDATE_FAILURE_FILENAME).mkdir()

        assert adapter.read_update_failure() is None
        assert log


def _docstring_nodes(tree: ast.Module) -> set[int]:
    scopes = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    owners = [tree, *(node for node in ast.walk(tree) if isinstance(node, scopes))]
    return {
        id(owner.body[0].value)
        for owner in owners
        if owner.body and isinstance(owner.body[0], ast.Expr) and isinstance(owner.body[0].value, ast.Constant)
    }


def _names_the_record(tree: ast.Module) -> bool:
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "UPDATE_FAILURE_FILENAME":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "UPDATE_FAILURE_FILENAME":
            return True
        if isinstance(node, ast.alias) and node.name == "UPDATE_FAILURE_FILENAME":
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and UPDATE_FAILURE_FILENAME in node.value
            and id(node) not in docstrings
        ):
            return True
    return False


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


# Every call that writes, moves or removes a file, by the name it is reached
# through. ``replace`` is ``os.replace`` and ``Path.replace``, and a
# ``str.replace`` in the adapter would read as one too.
_WRITING_CALLS = frozenset(
    {
        "write",
        "write_text",
        "write_bytes",
        "writelines",
        "touch",
        "truncate",
        "unlink",
        "remove",
        "rmdir",
        "rmtree",
        "rename",
        "renames",
        "replace",
        "move",
        "copy",
        "copy2",
        "copyfile",
        "link",
        "symlink",
        "mkstemp",
    }
)


# Every character ``open`` accepts in a mode, and the ones that make it a write.
# A string made only of the first set is read as a mode wherever it stands:
# ``open(path, "w")`` carries it second and ``Path(path).open("w")`` first.
_MODE_CHARACTERS = frozenset("rwxabt+")
_WRITING_MODE_CHARACTERS = frozenset("wax+")


def _opens_for_writing(call: ast.Call) -> bool:
    candidates = [*call.args, *(keyword.value for keyword in call.keywords if keyword.arg == "mode")]
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value != ""
        and set(node.value) <= _MODE_CHARACTERS
        and not _WRITING_MODE_CHARACTERS.isdisjoint(node.value)
        for node in candidates
    )


class TestOnlyTheInstallerWritesTheRecord:
    """The backend reads ``update-failure.json`` and never writes or removes it.

    Read off the syntax tree, so what these see is a name and a call. Two
    modules may name the record, and only the adapter's calls are read:
    ``domain/update_outcome.py``, the constant's home, is not. A call is judged
    by the name it is reached through, and an ``open`` by a mode written as a
    string constant. All of these pass: a write under a name not in the list,
    a mode computed at runtime, a record path assembled from pieces or handed
    in from elsewhere, a write through a helper in another module, ``getattr``
    and a subprocess.
    """

    def test_no_backend_module_but_the_adapter_and_the_constant_s_home_names_the_record(self):
        naming = {
            path.relative_to(_BACKEND).as_posix()
            for path in _BACKEND.rglob("*.py")
            if "_vendor" not in path.parts and _names_the_record(ast.parse(path.read_text(encoding="utf-8")))
        }

        assert naming == {"adapters/update_failure.py", "domain/update_outcome.py"}

    def test_the_adapter_calls_nothing_that_writes_moves_or_removes_a_file(self):
        tree = ast.parse(_ADAPTER.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

        assert [_called_name(call) for call in calls if _called_name(call) in _WRITING_CALLS] == []

    def test_the_adapter_opens_files_for_reading_only(self):
        tree = ast.parse(_ADAPTER.read_text(encoding="utf-8"))
        opens = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _called_name(node) == "open"]

        assert opens, "the adapter reads the record, so it opens it"
        assert [ast.unparse(call) for call in opens if _opens_for_writing(call)] == []
