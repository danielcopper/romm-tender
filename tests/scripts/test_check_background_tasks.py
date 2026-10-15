"""Tests for ``scripts/check_background_tasks.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. Fixtures lay out a small ``backend/`` tree under ``tmp_path`` so
the check walks it with its real exemption logic.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_background_tasks.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_background_tasks", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_HELPER_SOURCE = (
    "class BackgroundTasks:\n"
    "    def spawn(self, coroutine, name):\n"
    "        return self._loop.create_task(coroutine, name=name)\n"
)


def _make_backend_tree(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path]:
    """Build a fake ``backend/`` tree holding the helper and ``relative path -> source``."""
    backend_dir = tmp_path / "backend"
    helper = backend_dir / "lib" / "background_tasks.py"
    for relative, source in {"lib/background_tasks.py": _HELPER_SOURCE, **files}.items():
        path = backend_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return backend_dir, helper


def _point_check_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_dir: Path, helper: Path) -> None:
    monkeypatch.setattr(check, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(check, "HELPER", helper)


@pytest.fixture
def patched_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Yield a helper that retargets the check at a tmp-path backend tree and runs it."""

    def _run(files: dict[str, str]) -> list[str]:
        backend_dir, helper = _make_backend_tree(tmp_path, files)
        _point_check_at(monkeypatch, tmp_path, backend_dir, helper)
        return check.find_violations(check._iter_scanned_files(backend_dir, helper))

    return _run


class TestEveryCallKindFails:
    @pytest.mark.parametrize(
        "call",
        [
            "self._loop.create_task(work())",
            "asyncio.create_task(work())",
            "asyncio.get_running_loop().create_task(work())",
            "group.create_task(work())",
            "create_task(work())",
            "asyncio.ensure_future(work())",
            "ensure_future(work())",
            "asyncio.Task(work())",
        ],
    )
    def test_a_task_created_in_a_service_is_flagged(self, patched_check, call):
        findings = patched_check({"services/thing.py": f"def go(self, group):\n    {call}\n"})

        assert len(findings) == 1
        assert findings[0].startswith(str(Path("backend/services/thing.py")) + ":2:")

    def test_a_bare_task_imported_from_asyncio_is_flagged(self, patched_check):
        findings = patched_check({"services/thing.py": "from asyncio import Task\n\ndef go():\n    Task(work())\n"})

        assert len(findings) == 1
        assert "'Task(...)'" in findings[0]

    def test_a_bare_task_from_elsewhere_is_not(self, patched_check):
        findings = patched_check({"services/thing.py": "from models import Task\n\ndef go():\n    Task(1)\n"})

        assert findings == []

    def test_bootstrap_and_lib_are_scanned_too(self, patched_check):
        findings = patched_check(
            {
                "bootstrap/services.py": "def go(loop):\n    loop.create_task(emit())\n",
                "lib/prune_conflicts.py": "def go(loop):\n    loop.create_task(release())\n",
                "main.py": "def go(loop):\n    loop.create_task(work())\n",
            }
        )

        assert len(findings) == 3

    def test_every_offending_call_in_a_file_is_reported(self, patched_check):
        findings = patched_check(
            {"services/thing.py": "def go(loop):\n    loop.create_task(a())\n    loop.create_task(b())\n"}
        )

        assert len(findings) == 2


class TestWhatIsNotFlagged:
    def test_the_helpers_own_module_is_exempt(self, patched_check):
        assert patched_check({}) == []

    @pytest.mark.parametrize("tree", ["host", "_vendor", "native"])
    def test_an_exempt_tree_is_not_scanned(self, patched_check, tree):
        findings = patched_check({f"{tree}/connection.py": "def go():\n    asyncio.create_task(work())\n"})

        assert findings == []

    def test_a_module_merely_named_like_an_exempt_tree_is_scanned(self, patched_check):
        findings = patched_check(
            {
                "services/host.py": "def go():\n    asyncio.create_task(work())\n",
                "hosting/thing.py": "def go():\n    asyncio.create_task(work())\n",
            }
        )

        assert len(findings) == 2

    def test_spawn_and_awaited_calls_are_not_flagged(self, patched_check):
        findings = patched_check(
            {
                "services/thing.py": (
                    "async def go(self):\n"
                    "    self._tasks.spawn(work(), 'work')\n"
                    "    await asyncio.gather(a(), b())\n"
                    "    await self._loop.run_in_executor(None, io)\n"
                    "    # create_task(work()) in a comment\n"
                    "    'ensure_future(x)'\n"
                )
            }
        )

        assert findings == []


class TestMainEntryPoint:
    def test_help_flag_returns_zero(self, capsys):
        assert check.main(["--help"]) == 0
        assert "Background-task creation gate" in capsys.readouterr().out

    def test_real_repo_run_is_clean(self, capsys):
        rc = check.main([])

        out = capsys.readouterr().out
        assert rc == 0, out
        assert "OK:" in out

    def test_main_reports_and_returns_one_on_planted_violation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ):
        backend_dir, helper = _make_backend_tree(
            tmp_path, {"services/library/sync_orchestrator.py": "def go(self):\n    self._loop.create_task(run())\n"}
        )
        _point_check_at(monkeypatch, tmp_path, backend_dir, helper)

        rc = check.main([])

        assert rc == 1
        out = capsys.readouterr().out
        assert "sync_orchestrator.py" in out
        assert "ERROR:" in out
