"""Tests for ``scripts/check_coverage_exclusions.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path`` (and is excluded from ruff/basedpyright). ``collect_errors`` takes
the repository root and the file list to sweep explicitly, so every case below
lays out a synthetic root under ``tmp_path`` — two exclusion lists and a handful
of source files — and the real comparison runs against it. No synthetic case shells out to
git: ``tracked_sources`` is the only part that does, and what it contributes is
the file list the sweep is handed, so only the real-root case at the end calls
it.

Each of the gate's four assertions is pinned in BOTH directions, because a
coverage gate that only ever says OK is decorative:

* the two lists agreeing, and each declared asymmetry being present in its own
  list and absent from the other — a stale declaration is a silent hole, since
  the entry it names would otherwise be reported as drift;
* a listed path that does not exist, in its file and its folder form;
* a listed file with no marker — the reason not living where the file does;
* a marked file on neither list — the direction that catches the accident the
  gate was written for: an exclusion written as a folder (``src/patches/**``)
  stayed behind when a file left the folder, and nothing said so.

One case runs against the REAL repository root, so the two lists this repo ships
are verified by ``mise run test`` and not only by ``mise run lint``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_coverage_exclusions.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_coverage_exclusions", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

# The entries every synthetic root carries, so a case only has to state what it
# changes. They mirror the real lists' shape: one folder entry, one file entry,
# and the declared asymmetries on each side.
_SHARED = ["frontend/src/types/**", "frontend/src/utils/styleInjector.ts"]
_VITEST_ONLY = list(check.VITEST_ONLY)
_SONAR_ONLY = list(check.SONAR_ONLY)

_MARKED = "// coverage-exempt: a CSS payload, nothing to assert\nconst css = `a{}`;\n"


def _write_root(
    tmp_path: Path,
    *,
    vitest: list[str] | None = None,
    sonar: list[str] | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    """Lay out a synthetic repository root and return it."""
    vitest_entries = _VITEST_ONLY + _SHARED if vitest is None else vitest
    sonar_entries = _SONAR_ONLY + _SHARED if sonar is None else sonar
    body = ",\n        ".join(f'"{e}"' for e in vitest_entries)
    (tmp_path / "vitest.config.ts").write_text(
        "export default defineConfig({\n"
        "  test: {\n"
        "    coverage: {\n"
        '      include: ["frontend/src/**/*.{ts,tsx}"],\n'
        f"      exclude: [\n        {body},\n      ],\n"
        "    },\n  },\n});\n",
        encoding="utf-8",
    )
    (tmp_path / "sonar-project.properties").write_text(
        "sonar.projectKey=synthetic\nsonar.coverage.exclusions=" + ",".join(sonar_entries) + "\n",
        encoding="utf-8",
    )
    for rel, text in (files if files is not None else {"frontend/src/utils/styleInjector.ts": _MARKED}).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "frontend" / "src" / "types").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestTheTwoListsAgree:
    def test_a_matching_pair_of_lists_reports_nothing(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path)

        assert check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"]) == []

    def test_an_entry_only_vitest_excludes_is_reported(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path, sonar=[*_SONAR_ONLY, "frontend/src/types/**"])

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("styleInjector.ts" in e and "not from Sonar's" in e for e in errors)

    def test_an_entry_only_sonar_excludes_is_reported(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path, vitest=[*_VITEST_ONLY, "frontend/src/types/**"])

        errors = check.collect_errors(root, [])

        assert any("styleInjector.ts" in e and "not from the Vitest report" in e for e in errors)

    def test_a_declared_asymmetry_is_not_reported_as_drift(self, tmp_path: Path) -> None:
        """The whole point of declaring them: neither side is expected to match."""
        root = _write_root(tmp_path)

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert not any("**/tests/**" in e or "{test,spec}" in e for e in errors)

    def test_a_declared_sonar_only_entry_missing_from_sonars_list_is_reported(self, tmp_path: Path) -> None:
        dropped = [e for e in _SONAR_ONLY if e != "**/conftest.py"]
        root = _write_root(tmp_path, sonar=dropped + _SHARED)

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("**/conftest.py" in e and "declared Sonar-only" in e for e in errors)

    def test_a_sonar_only_entry_appearing_in_vitests_list_is_reported(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path, vitest=_VITEST_ONLY + _SHARED + ["**/conftest.py"])

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("**/conftest.py" in e and "appears in vitest.config.ts" in e for e in errors)

    def test_a_vitest_only_entry_appearing_in_sonars_list_is_reported(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path, sonar=_SONAR_ONLY + _SHARED + _VITEST_ONLY)

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("appears in sonar.coverage.exclusions" in e for e in errors)


class TestEveryListedPathExists:
    def test_a_file_entry_naming_nothing_is_reported(self, tmp_path: Path) -> None:
        entries = ["frontend/src/types/**", "frontend/src/utils/gone.ts"]
        root = _write_root(tmp_path, vitest=_VITEST_ONLY + entries, sonar=_SONAR_ONLY + entries, files={})

        errors = check.collect_errors(root, [])

        assert any("gone.ts" in e and "does not exist" in e for e in errors)

    def test_a_folder_entry_naming_nothing_is_reported(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path)
        (root / "frontend" / "src" / "types").rmdir()

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("frontend/src/types/**" in e and "directory that does not exist" in e for e in errors)

    def test_an_undeclared_folder_entry_is_refused(self, tmp_path: Path) -> None:
        """The rule itself: a place is not a property unless it is declared one."""
        entries = ["frontend/src/types/**", "frontend/src/utils/**"]
        root = _write_root(tmp_path, vitest=_VITEST_ONLY + entries, sonar=_SONAR_ONLY + entries)

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("frontend/src/utils/**" in e and "FOLDER_ENTRIES" in e for e in errors)


class TestTheReasonLivesInTheFile:
    def test_a_listed_file_without_the_marker_is_reported(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path, files={"frontend/src/utils/styleInjector.ts": "const css = `a{}`;\n"})

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("styleInjector.ts" in e and "carries no" in e for e in errors)

    def test_a_marker_below_the_window_does_not_count(self, tmp_path: Path) -> None:
        buried = "\n" * check.MARKER_WINDOW + check.MARKER + " too far down\n"
        root = _write_root(tmp_path, files={"frontend/src/utils/styleInjector.ts": buried})

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts"])

        assert any("styleInjector.ts" in e and "carries no" in e for e in errors)

    def test_a_marked_file_on_neither_list_is_reported(self, tmp_path: Path) -> None:
        """The original accident: a file left its folder, the exclusion did not."""
        root = _write_root(
            tmp_path,
            files={
                "frontend/src/utils/styleInjector.ts": _MARKED,
                "frontend/src/utils/moved.ts": _MARKED,
            },
        )

        errors = check.collect_errors(root, ["frontend/src/utils/styleInjector.ts", "frontend/src/utils/moved.ts"])

        assert any("moved.ts" in e and "on neither exclusion list" in e for e in errors)


class TestTheRealRepository:
    def test_this_repositorys_own_lists_are_in_order(self) -> None:
        assert check.collect_errors(_REPO_ROOT, check.tracked_sources(_REPO_ROOT)) == []
