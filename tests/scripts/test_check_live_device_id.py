"""Tests for ``scripts/check_live_device_id.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path`` (and is excluded from ruff/basedpyright). Each case writes small
stand-in modules under a ``tmp_path`` backend and runs the real AST walk over
them.

Coverage centres on the confinement rule (a ``LiveDeviceId`` is built only in
``SyncEngine.live_device_id``) and on the boundaries that decide whether a site
counts: the module and class the entry is bound to, the spellings of a
construction (a call, a module attribute, a ``cast`` by name or as a string), a
renamed import, the exempt trees, and a mere reference that is no construction.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_live_device_id.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_live_device_id", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_ENTRY = """
from domain.live_device_id import LiveDeviceId


class SyncEngine:
    async def live_device_id(self):
        device_id = await self._register()
        return LiveDeviceId(device_id)
"""

_ENTRY_PATH = "services/saves/sync_engine/engine.py"


@pytest.fixture
def run_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Yield a helper that writes ``{relative path: source}`` under a tmp backend and runs the real check."""

    def _run(modules: dict[str, str]) -> list[str]:
        backend = tmp_path / "backend"
        for rel, source in modules.items():
            path = backend / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(check, "BACKEND_DIR", backend)
        monkeypatch.setattr(check, "ENTRY_MODULE", backend / _ENTRY_PATH)
        return check.find_violations()

    return _run


class TestTheEntryMayBuildIt:
    def test_the_entry_itself_is_allowed(self, run_check):
        assert run_check({_ENTRY_PATH: _ENTRY}) == []

    def test_a_reference_that_builds_nothing_is_allowed(self, run_check):
        source = """
from domain.live_device_id import LiveDeviceId


def sync(device_id: LiveDeviceId) -> None:
    kinds = [LiveDeviceId]
"""
        assert run_check({_ENTRY_PATH: _ENTRY, "services/saves/copies.py": source}) == []


class TestEveryOtherConstructionFails:
    def test_a_call_in_another_module_fails(self, run_check):
        source = """
from domain.live_device_id import LiveDeviceId


def stored(registry):
    return LiveDeviceId(registry.get_device_id())
"""
        findings = run_check({_ENTRY_PATH: _ENTRY, "services/saves/copies.py": source})
        assert len(findings) == 1
        assert "backend/services/saves/copies.py:6:" in findings[0]
        assert "outside SyncEngine.live_device_id()" in findings[0]

    def test_a_call_through_the_module_fails(self, run_check):
        source = """
import domain.live_device_id as ids


def stored(device_id):
    return ids.LiveDeviceId(device_id)
"""
        findings = run_check({_ENTRY_PATH: _ENTRY, "services/saves/versions.py": source})
        assert [f.split(" ")[0] for f in findings] == ["backend/services/saves/versions.py:6:11"]

    @pytest.mark.parametrize("spelling", ["LiveDeviceId", '"LiveDeviceId"'])
    def test_a_cast_to_it_fails(self, run_check, spelling):
        source = f"""
from typing import cast


def stored(device_id):
    return cast({spelling}, device_id)
"""
        findings = run_check({_ENTRY_PATH: _ENTRY, "services/saves/status/service.py": source})
        assert len(findings) == 1
        assert "builds a LiveDeviceId" in findings[0]

    def test_a_cast_to_another_type_is_allowed(self, run_check):
        source = """
from typing import cast


def stored(device_id):
    return cast(str, device_id)
"""
        assert run_check({_ENTRY_PATH: _ENTRY, "services/saves/status/service.py": source}) == []

    def test_a_renamed_import_fails(self, run_check):
        source = """
from domain.live_device_id import LiveDeviceId as Live
"""
        findings = run_check({_ENTRY_PATH: _ENTRY, "services/saves/slots/setup.py": source})
        assert len(findings) == 1
        assert "imports LiveDeviceId as Live" in findings[0]

    def test_another_method_of_the_entry_class_fails(self, run_check):
        source = (
            _ENTRY
            + """
    def stored_id(self):
        return LiveDeviceId(self._devices.get_device_id())
"""
        )
        findings = run_check({_ENTRY_PATH: source})
        assert len(findings) == 1
        assert f"backend/{_ENTRY_PATH}:11:" in findings[0]

    def test_a_method_of_that_name_in_another_module_is_not_the_entry(self, run_check):
        findings = run_check({_ENTRY_PATH: _ENTRY, "services/saves/service.py": _ENTRY})
        assert len(findings) == 1
        assert "backend/services/saves/service.py" in findings[0]

    def test_a_method_of_that_name_on_another_class_is_not_the_entry(self, run_check):
        source = _ENTRY.replace("class SyncEngine:", "class SaveService:")
        findings = run_check({_ENTRY_PATH: source})
        assert len(findings) == 1


class TestTheExemptTrees:
    @pytest.mark.parametrize("tree", ["_vendor", "native"])
    def test_a_construction_under_an_exempt_tree_is_not_read(self, run_check, tree):
        source = """
LiveDeviceId("x")
"""
        assert run_check({_ENTRY_PATH: _ENTRY, f"{tree}/mod.py": source}) == []


class TestTheRealTree:
    def test_the_repository_holds_the_rule(self):
        assert check.find_violations() == []

    def test_main_answers_one_on_a_finding(self, run_check, capsys):
        run_check({_ENTRY_PATH: _ENTRY, "lib/mod.py": 'LiveDeviceId("x")\n'})
        assert check.main([]) == 1
        assert "ERROR" in capsys.readouterr().out
