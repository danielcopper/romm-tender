"""Tests for ``scripts/check_third_party_notices.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. Every case but the first points ``BUDGETS`` and ``NOTICES`` at
files under ``tmp_path``; the first runs against the repository's own pair, so
the notices are held to the budgets by ``mise run test`` and not only by
``mise run lint``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_third_party_notices.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_third_party_notices", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_BUDGETS = {"index.js": {"@decky/ui": 100, "react-icons": 50}, "globals.js": {"@decky/ui": 10}}


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, budgets: str, notices: str) -> int:
    budgets_path = tmp_path / "package-budgets.json"
    notices_path = tmp_path / "THIRD-PARTY-NOTICES.md"
    budgets_path.write_text(budgets, encoding="utf-8")
    notices_path.write_text(notices, encoding="utf-8")
    monkeypatch.setattr(check, "BUDGETS", budgets_path)
    monkeypatch.setattr(check, "NOTICES", notices_path)
    return check.main()


def test_the_repositorys_own_notices_name_every_budgeted_package(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main() == 0
    assert "OK" in capsys.readouterr().out


def test_a_package_without_a_section_is_a_finding_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notices = "# Third-party notices\n\n## @decky/ui\n\nLGPL-2.1.\n"

    assert _run(monkeypatch, tmp_path, budgets=json.dumps(_BUDGETS), notices=notices) == 1
    findings = [line.strip() for line in capsys.readouterr().err.splitlines() if line.startswith("  ")]
    assert findings == ["react-icons: no '## react-icons' heading"]


def test_a_package_named_only_in_running_text_is_still_a_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notices = (
        "# Third-party notices\n\n"
        "## @decky/ui\n\nThe panel also draws icons from react-icons, under MIT.\n\n"
        "### react-icons\n\nA subsection is not a section of its own.\n"
    )

    assert _run(monkeypatch, tmp_path, budgets=json.dumps(_BUDGETS), notices=notices) == 1
    assert "react-icons: no '## react-icons' heading" in capsys.readouterr().err


def test_a_budgets_file_it_cannot_read_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notices = "# Third-party notices\n"

    for budgets in ("{not json", json.dumps(["@decky/ui"]), json.dumps({"index.js": ["@decky/ui"]})):
        assert _run(monkeypatch, tmp_path, budgets=budgets, notices=notices) == 1, budgets
        assert "cannot read the budgets" in capsys.readouterr().err
