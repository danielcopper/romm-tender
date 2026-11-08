"""Tests for ``scripts/check_invariant_register.py``.

The check is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. Each test retargets the module's ``ROOT`` at a ``tmp_path`` tree
holding a ``CLAUDE.md`` and an area folder, so the real parsing and the real
limits run against a controlled layout.

Coverage follows the three limits — each at its boundary and one past it — and
the one-home rule, plus the parsing the limits rest on: a headline the
formatter wrapped, a bold word inside a headline, a headline followed by the
paragraph that holds the rest of its rule, and the two ways the gate could lose
its input and go quiet.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_invariant_register.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_invariant_register", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_check_module()

_SECTION_HEAD = "## Invariant register — cross-cutting safety rules\n\n"


def _claude_md(section_body: str = "The map.\n\n") -> str:
    return "# Title\n\n## Before\n\nText.\n\n" + _SECTION_HEAD + section_body + "## Security\n\nAfter.\n"


def _entry(headline: str, rest: str = "check — `scripts/check_x.py`") -> str:
    return f"- **{headline}** — {rest}\n"


def _tree(tmp_path: Path, claude: str, areas: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "CLAUDE.md").write_text(claude, encoding="utf-8")
    folder = tmp_path / check.AREA_DIR
    folder.mkdir(parents=True)
    for name, text in areas.items():
        (folder / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(check, "ROOT", tmp_path)


def _run(capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = check.main()
    captured = capsys.readouterr()
    return code, captured.out + captured.err


class TestAWellKeptRegister:
    def test_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _tree(
            tmp_path,
            _claude_md(_entry("A rule kept here")),
            {"saves.md": "# Saves\n\n" + _entry("A saves rule") + _entry("Another saves rule")},
            monkeypatch,
        )
        code, out = _run(capsys)
        assert code == 0, out
        assert "3 entries" in out


class TestTheSectionLimit:
    def _claude_of_length(self, length: int) -> str:
        filler_length = length - len(_SECTION_HEAD) - 1
        return _claude_md("x" * filler_length + "\n")

    def test_a_section_at_the_limit_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = self._claude_of_length(check.SECTION_LIMIT)
        assert len(check.register_section(claude) or "") == check.SECTION_LIMIT
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 0, out

    def test_a_section_one_past_the_limit_fails_and_names_what_belongs_there(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, self._claude_of_length(check.SECTION_LIMIT + 1), {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "the register section is 6,001 characters" in out
        assert "loses user data for good" in out

    def test_the_section_ends_at_the_next_level_two_heading(self) -> None:
        text = _claude_md("Body.\n\n### A subheading stays inside\n\nMore.\n\n")
        section = check.register_section(text)
        assert section is not None
        assert section.startswith(_SECTION_HEAD)
        assert "A subheading stays inside" in section
        assert "## Security" not in section

    def test_a_missing_section_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, "# Title\n\n## Something else\n", {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "no '## Invariant register' section" in out


class TestTheHeadlineLimit:
    def test_a_headline_at_the_limit_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"a.md": _entry("h" * check.HEADLINE_LIMIT)}, monkeypatch)
        code, out = _run(capsys)
        assert code == 0, out

    def test_a_headline_one_past_the_limit_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"a.md": "# A\n\n" + _entry("h" * (check.HEADLINE_LIMIT + 1))}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/a.md:3: a bold headline of 301 characters" in out

    def test_a_headline_in_claude_md_is_held_to_the_same_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(_entry("h" * 301)), {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:" in out
        assert "a bold headline of 301 characters" in out

    def test_a_wrapped_headline_is_measured_with_one_space_per_wrap(self) -> None:
        text = "- **" + "a" * 150 + "\n  " + "b" * 149 + "** — check — `x`\n"
        (found,), malformed = check.entries(text, "a.md")
        assert malformed == []
        assert len(found.headline) == check.HEADLINE_LIMIT

    def test_a_bold_word_inside_a_headline_stays_part_of_it(self) -> None:
        text = "- **held by `a.py` **and** `b.py`, never a third** — check — `x`\n"
        (found,), _ = check.entries(text, "a.md")
        assert found.headline == "held by `a.py` **and** `b.py`, never a third"

    def test_the_paragraph_under_a_headline_is_not_part_of_it(self) -> None:
        rest = "The rest of the rule, " + "word " * 100
        text = "- **A headline** — test + prompt-only\n\n  " + rest + "\n\n  `tests/x.py`\n"
        (found,), _ = check.entries(text, "a.md")
        assert found.headline == "A headline"

    def test_a_headline_with_a_suffix_before_its_tier_ends_at_the_bold(self) -> None:
        text = "- **No code names a class hash** (dev build only) — check — `x`\n"
        (found,), _ = check.entries(text, "a.md")
        assert found.headline == "No code names a class hash"

    def test_a_headline_that_never_closes_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"a.md": _entry("Fine") + "- **Never closed — check — `x`\n"}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "a.md:2: an entry whose bold headline never closes" in out

    def test_an_indented_list_item_is_no_entry(self) -> None:
        found, malformed = check.entries("- plain item\n  - **nested bold** — not an entry\n", "a.md")
        assert found == []
        assert malformed == []


class TestTheAreaFileLimit:
    def _area_of_length(self, length: int) -> str:
        entry = _entry("An area rule")
        return entry + "y" * (length - len(entry) - 1) + "\n"

    def test_a_file_at_the_limit_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = self._area_of_length(check.AREA_FILE_LIMIT)
        assert len(text) == check.AREA_FILE_LIMIT
        _tree(tmp_path, _claude_md(), {"a.md": text}, monkeypatch)
        code, out = _run(capsys)
        assert code == 0, out

    def test_a_file_one_past_the_limit_fails_and_says_to_split_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"big.md": self._area_of_length(check.AREA_FILE_LIMIT + 1)}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/big.md: 40,001 characters" in out
        assert "Split the area" in out

    def test_characters_are_counted_not_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        entry = _entry("An area rule")
        text = entry + "—" * (check.AREA_FILE_LIMIT - len(entry) - 1) + "\n"
        assert len(text.encode("utf-8")) > check.AREA_FILE_LIMIT
        _tree(tmp_path, _claude_md(), {"a.md": text}, monkeypatch)
        code, out = _run(capsys)
        assert code == 0, out

    def test_an_area_folder_with_no_entries_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"index.md": "# Index\n\n- [A](a.md)\n"}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "no register entries found" in out


class TestOneHomePerEntry:
    def test_a_headline_in_two_area_files_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"a.md": _entry("The same rule"), "b.md": _entry("The same rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/b.md:1: this headline also stands at {check.AREA_DIR}/a.md:1" in out

    def test_a_headline_in_claude_md_and_an_area_file_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(_entry("The same rule")), {"a.md": _entry("The same rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "this headline also stands at CLAUDE.md:" in out

    def test_a_headline_wrapped_differently_is_still_the_same_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wrapped = "- **The same\n  rule** — check — `x`\n"
        _tree(tmp_path, _claude_md(), {"a.md": _entry("The same rule"), "b.md": wrapped}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "this headline also stands at" in out

    def test_two_different_headlines_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"a.md": _entry("One rule"), "b.md": _entry("Another rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 0, out
