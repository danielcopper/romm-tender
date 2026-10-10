"""Tests for ``scripts/check_failure_shape.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path`` (and is excluded from ruff/basedpyright). Fixtures lay out a small
``backend/`` tree under ``tmp_path`` and monkeypatch the script's
``BACKEND_DIR`` / ``REPO_ROOT`` constants for the duration of the test.

Coverage centres on the one rule — the failure shape is built nowhere under
``backend/`` but the entrypoint: a failure dict literal in any layer and in an
event payload, an ``error_response`` call and a refusal helper's spread fail,
and so does a module that does not parse — and on its boundaries: the
entrypoint and the upstream copies are not read, a result type built through
its own constructor passes, and a computed ``success`` is not seen.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_failure_shape.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_failure_shape", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()


def _backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, files: dict[str, str]) -> None:
    """Lay out ``backend/<rel> -> source`` under *tmp_path* and point the check at it."""
    backend = tmp_path / "backend"
    for rel, source in files.items():
        path = backend / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check, "BACKEND_DIR", backend)


def _found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, files: dict[str, str]) -> list[str]:
    _backend(monkeypatch, tmp_path, files)
    return [finding.render() for finding in check.collect_findings(check.BACKEND_DIR)]


_FAILURE_RETURN = 'def f():\n    return {"success": False, "reason": "x", "message": "m"}\n'


class TestTheShapeBuiltOutsideTheEntrypointFails:
    @pytest.mark.parametrize("rel", ["adapters/mod.py", "domain/mod.py", "lib/mod.py", "host/mod.py", "check.py"])
    def test_a_failure_dict_in_any_layer(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: str):
        assert _found(monkeypatch, tmp_path, {rel: _FAILURE_RETURN}) == [f"backend/{rel}:2  a failure dict literal"]

    def test_a_failure_dict_in_a_nested_service_package(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        found = _found(monkeypatch, tmp_path, {"services/saves/slots/mod.py": _FAILURE_RETURN})

        assert found == ["backend/services/saves/slots/mod.py:2  a failure dict literal"]

    def test_a_failure_dict_in_an_event_payload(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        source = """\
            async def finish(self):
                await self._emit("prune_complete", {"success": False, "reason": "aborted"})
        """

        found = _found(monkeypatch, tmp_path, {"services/mod.py": source})

        assert found == ["backend/services/mod.py:2  a failure dict literal"]

    @pytest.mark.parametrize("falsy", ["0", "None"])
    def test_any_literal_falsy_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, falsy: str):
        source = f'answer = {{"message": "m", "success": {falsy}}}\n'

        assert _found(monkeypatch, tmp_path, {"lib/mod.py": source}) == ["backend/lib/mod.py:1  a failure dict literal"]

    def test_a_call_to_error_response(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        source = """\
            import lib.errors
            from lib.errors import error_response


            def f(e):
                return error_response(e)


            def g(e):
                return lib.errors.error_response(e)
        """

        found = _found(monkeypatch, tmp_path, {"services/mod.py": source})

        assert found == [
            "backend/services/mod.py:6  a call to error_response()",
            "backend/services/mod.py:10  a call to error_response()",
        ]

    def test_a_spread_of_a_refusal_or_failure_helper(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        source = """\
            def f(self):
                return {**busy_refusal(), "synced": 0}


            def g(self):
                return {**self._failure("x", "m"), "claimed": 0}
        """

        found = _found(monkeypatch, tmp_path, {"services/mod.py": source})

        assert found == [
            "backend/services/mod.py:2  a spread of busy_refusal()",
            "backend/services/mod.py:6  a spread of _failure()",
        ]

    def test_a_module_that_does_not_parse(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        found = _found(monkeypatch, tmp_path, {"adapters/broken.py": "def broken(:\n"})

        assert found == ["backend/adapters/broken.py:1  does not parse, so nothing in it could be checked"]


class TestWhatPasses:
    def test_the_entrypoint_is_not_read(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        assert _found(monkeypatch, tmp_path, {"main.py": _FAILURE_RETURN}) == []

    @pytest.mark.parametrize("rel", ["_vendor/pkg/mod.py", "native/mod.py"])
    def test_the_upstream_copies_are_not_read(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: str):
        assert _found(monkeypatch, tmp_path, {rel: _FAILURE_RETURN}) == []

    @pytest.mark.parametrize("rel", ["services/main.py", "adapters/native/mod.py", "lib/_vendor/mod.py"])
    def test_the_same_names_below_the_backend_root_are_read(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: str
    ):
        assert _found(monkeypatch, tmp_path, {rel: _FAILURE_RETURN}) == [f"backend/{rel}:2  a failure dict literal"]

    def test_a_result_built_through_its_own_type(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        source = """\
            from models.prune import MutationOutcome


            def remove(exc):
                return MutationOutcome(success=False, changed=False, ambiguous=False, message=str(exc))
        """

        assert _found(monkeypatch, tmp_path, {"adapters/mod.py": source}) == []

    def test_a_computed_success_is_not_seen(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """A documented blind spot: the save-sync results and ``prune_complete`` compute theirs."""
        source = """\
            def result(errors, success):
                synced = {"success": len(errors) == 0, "errors": errors}
                return {**synced, "event": {"success": success}}
        """

        assert _found(monkeypatch, tmp_path, {"services/mod.py": source}) == []

    def test_what_the_entrypoint_takes_over(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        source = """\
            from lib.errors import Refused


            def f(done):
                if not done:
                    raise Refused("nothing_to_fix", "No fix needed")
                return {"success": True, **dict(done)}
        """

        assert _found(monkeypatch, tmp_path, {"services/mod.py": source}) == []

    def test_a_status_union_and_a_partial_success_flag(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        source = """\
            def versions():
                return {"status": "server_unreachable", "message": "m"}


            def status():
                return {"files": [], "server_query_failed": True}
        """

        assert _found(monkeypatch, tmp_path, {"services/mod.py": source}) == []


class TestMainEntryPoint:
    def test_help_flag_returns_zero(self, capsys: pytest.CaptureFixture[str]):
        rc = check.main(["--help"])
        assert rc == 0
        assert "Failure-shape dialect gate" in capsys.readouterr().out

    def test_short_help_flag_returns_zero(self):
        assert check.main(["-h"]) == 0

    def test_real_repo_enforce_is_clean(self, capsys: pytest.CaptureFixture[str]):
        # The real backend builds the shape nowhere but backend/main.py.
        rc = check.main(["--check"])
        assert rc == 0
        assert "OK:" in capsys.readouterr().out

    def test_check_fails_and_names_each_site(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        _backend(monkeypatch, tmp_path, {"adapters/mod.py": _FAILURE_RETURN, "main.py": _FAILURE_RETURN})

        assert check.main(["--check"]) == 1
        out = capsys.readouterr().out
        assert "  backend/adapters/mod.py:2  a failure dict literal" in out
        assert "main.py:" not in out
        assert "ERROR:" in out

    def test_a_run_without_check_enforces_too(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _backend(monkeypatch, tmp_path, {"lib/mod.py": _FAILURE_RETURN})

        assert check.main([]) == 1

    def test_check_passes_on_a_tree_that_builds_no_shape(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        _backend(monkeypatch, tmp_path, {"main.py": _FAILURE_RETURN, "services/mod.py": "def f():\n    return {}\n"})

        assert check.main(["--check"]) == 0
        assert "OK: no failure shape built in backend/ outside backend/main.py." in capsys.readouterr().out
