"""Tests for ``scripts/check_romm_min_version.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. The per-claim cases copy the files the claims read into
``tmp_path`` and point ``ROOT`` there, so a drifted number can be planted in
one claim at a time without touching the repository; ``SOURCE`` stays the real
``main.py``, so the enforced floor is the one the plugin ships. Every claim is
exercised in both directions — the real statement passes, the same statement
with another number fails naming its file and label — because a regex that
stopped matching would otherwise read as a claim that holds. One case runs
against the real tree, so the stated floors are verified by ``mise run test``
and not only by ``mise run lint``.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import re
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_romm_min_version.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_romm_min_version", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_CLAIM_IDS = [label for _, _, label in check.CLAIMS]


def _copy_claim_files(root: Path) -> None:
    for filename in {filename for filename, _, _ in check.CLAIMS}:
        target = root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REPO_ROOT / filename, target)


def _run(monkeypatch: pytest.MonkeyPatch, root: Path, *args: str) -> int:
    monkeypatch.setattr(check, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["check_romm_min_version.py", *args])
    return check.main()


def test_the_real_tree_states_the_enforced_floor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, _REPO_ROOT) == 0
    assert "OK: every stated RomM minimum matches" in capsys.readouterr().out


@pytest.mark.parametrize("claim", check.CLAIMS, ids=_CLAIM_IDS)
def test_each_claim_matches_exactly_one_statement_in_the_real_tree(claim: tuple[str, re.Pattern[str], str]) -> None:
    filename, pattern, _ = claim
    found = pattern.findall((_REPO_ROOT / filename).read_text(encoding="utf-8"))
    assert found == [check.enforced_version()]


@pytest.mark.parametrize("claim", check.CLAIMS, ids=_CLAIM_IDS)
def test_a_drifted_claim_fails_naming_its_file(
    claim: tuple[str, re.Pattern[str], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    filename, pattern, label = claim
    _copy_claim_files(tmp_path)
    path = tmp_path / filename
    path.write_text(pattern.sub("5.2.0", path.read_text(encoding="utf-8")), encoding="utf-8")

    assert _run(monkeypatch, tmp_path) == 1
    err = capsys.readouterr().err
    assert f"{filename}: {label} says 5.2.0" in err
    # Only the planted claim drifted; every other one still holds.
    assert err.count(" says ") == 1


@pytest.mark.parametrize("claim", check.CLAIMS, ids=_CLAIM_IDS)
def test_fix_rewrites_a_drifted_claim_back_to_the_floor(
    claim: tuple[str, re.Pattern[str], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filename, pattern, _ = claim
    _copy_claim_files(tmp_path)
    path = tmp_path / filename
    original = path.read_text(encoding="utf-8")
    path.write_text(pattern.sub("5.2.0", original), encoding="utf-8")

    assert _run(monkeypatch, tmp_path, "--fix") == 0
    assert path.read_text(encoding="utf-8") == original
    assert _run(monkeypatch, tmp_path) == 0


@pytest.mark.parametrize("claim", check.CLAIMS, ids=_CLAIM_IDS)
def test_a_claim_that_no_longer_matches_fails_rather_than_passing(
    claim: tuple[str, re.Pattern[str], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    filename, pattern, label = claim
    _copy_claim_files(tmp_path)
    path = tmp_path / filename
    path.write_text(pattern.sub("X.Y.Z", path.read_text(encoding="utf-8")), encoding="utf-8")

    assert _run(monkeypatch, tmp_path) == 1
    assert f"{filename}: no RomM version found for the {label}" in capsys.readouterr().err


_REFLOWABLE_CLAIMS = [claim for claim in check.CLAIMS if "badge" not in claim[2]]


@pytest.mark.parametrize("claim", _REFLOWABLE_CLAIMS, ids=[label for _, _, label in _REFLOWABLE_CLAIMS])
def test_a_statement_reflowed_across_lines_still_matches(claim: tuple[str, re.Pattern[str], str]) -> None:
    # The formatter may break a line at any space; the worst case is every one.
    filename, pattern, _ = claim
    reflowed = (_REPO_ROOT / filename).read_text(encoding="utf-8").replace(" ", "\n")
    assert pattern.findall(reflowed) == [check.enforced_version()]
