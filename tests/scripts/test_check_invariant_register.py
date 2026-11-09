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

import pytest

if TYPE_CHECKING:
    from types import ModuleType

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
        ((_, section),) = check.register_sections(claude)
        assert len(section) == check.SECTION_LIMIT
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
        assert "meets the criterion CLAUDE.md's register section states" in out

    def test_the_section_ends_at_the_next_level_two_heading(self) -> None:
        text = _claude_md("Body.\n\n### A subheading stays inside\n\nMore.\n\n")
        ((line, section),) = check.register_sections(text)
        assert line == 7
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
        assert "CLAUDE.md:9: a bold headline of 301 characters" in out

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
        _tree(tmp_path, _claude_md(_entry("A rule kept here")), {"index.md": "# Index\n\n- [A](a.md)\n"}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "no register entries found" in out

    def test_a_missing_area_folder_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "CLAUDE.md").write_text(_claude_md(_entry("A rule kept here")), encoding="utf-8")
        monkeypatch.setattr(check, "ROOT", tmp_path)
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/: no such folder" in out

    def test_a_file_in_a_subfolder_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md(), {"a.md": _entry("An area rule")}, monkeypatch)
        sub = tmp_path / check.AREA_DIR / "sub"
        sub.mkdir()
        (sub / "b.md").write_text(_entry("A rule out of sight") + "z" * 50_000 + "\n", encoding="utf-8")
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/sub: the area folder holds only .md files, directly in it." in out
        assert f"{check.AREA_DIR}/sub/b.md: the area folder holds only .md files" in out

    def test_a_file_that_is_not_markdown_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(
            tmp_path,
            _claude_md(),
            {"a.md": _entry("An area rule"), "b.txt": _entry("A rule out of sight")},
            monkeypatch,
        )
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/b.txt: the area folder holds only .md files" in out


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
        assert f"{check.AREA_DIR}/a.md:1: this headline also stands at CLAUDE.md:9" in out

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


class TestTheHeadlineClose:
    def test_a_bold_word_before_a_parenthesis_does_not_close_the_headline(self) -> None:
        text = "- **x **and** (" + "z" * 400 + ")** — check — `x`\n"
        (found,), malformed = check.entries(text, "a.md")
        assert malformed == []
        assert found.headline == "x **and** (" + "z" * 400 + ")"

    def test_a_bold_word_before_a_dash_does_not_close_the_headline(self) -> None:
        text = "- **x **and** — " + "z" * 400 + "** — check — `x`\n"
        (found,), _ = check.entries(text, "a.md")
        assert found.headline == "x **and** — " + "z" * 400

    def test_a_headline_left_with_an_open_bold_word_is_malformed(self) -> None:
        found, malformed = check.entries("- **x **and — check — `x`\n", "a.md")
        assert found == []
        assert malformed == ["a.md:1: an entry whose bold headline never closes"]

    def test_a_headline_alone_on_its_line_closes_at_the_end(self) -> None:
        (found,), malformed = check.entries("- **A headline**\n", "a.md")
        assert malformed == []
        assert found.headline == "A headline"

    @pytest.mark.parametrize("marker", ["* ", "+ ", "1. ", "2) "])
    def test_a_bold_item_under_another_marker_is_malformed(self, marker: str) -> None:
        found, malformed = check.entries(f"{marker}**A rule** — check — `x`\n", "a.md")
        assert found == []
        assert malformed == ["a.md:1: a bold list item under another marker than '- ' — an entry is a '- ' item."]

    def test_a_fenced_code_block_is_not_read(self) -> None:
        text = "```markdown\n- **An example** — check — `x`\n- **Unclosed\n```\n" + _entry("A rule")
        (found,), malformed = check.entries(text, "a.md")
        assert malformed == []
        assert (found.line, found.headline) == (5, "A rule")

    def test_an_unclosed_headline_in_the_claude_md_section_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, _claude_md("- **Rule**: kept here\n\n"), {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:9: an entry whose bold headline never closes" in out


class TestOneRegisterSectionInClaudeMd:
    def test_a_second_section_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = _claude_md() + "\n## Invariant register, continued\n\n" + _entry("x" * 400)
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:15: a second register section" in out

    def test_a_heading_in_a_code_block_is_no_section(self) -> None:
        text = _claude_md() + "```\n## Invariant register\n```\n"
        assert len(check.register_sections(text)) == 1

    def test_an_entry_outside_the_section_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = _claude_md() + "\n## Save rules\n\n" + _entry("A rule out of sight", "test + prompt-only — `t`")
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:17: a register entry outside the register section" in out

    def test_an_entry_with_a_suffix_outside_the_section_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = "## Traps\n\n- **A rule** (dev build only) — check — `x`\n\n" + _claude_md()
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:3: a register entry outside the register section" in out

    def test_a_trap_is_no_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        traps = "## Traps\n\n- **Shortcuts**: use the API — check it twice.\n- **A note** — not a tier word.\n\n"
        _tree(tmp_path, traps + _claude_md(), {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 0, out

    def test_an_area_headline_anywhere_in_claude_md_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = _claude_md() + "\n## Save rules\n\nRemember: Every save\noperation sends the live id.\n"
        _tree(tmp_path, claude, {"saves.md": _entry("Every save operation sends the live id")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/saves.md:1: this headline also stands in CLAUDE.md" in out


class TestFences:
    def test_an_area_file_ending_inside_an_open_fence_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        area = _entry("An area rule") + "```\n" + _entry("x" * 400)
        _tree(tmp_path, _claude_md(), {"a.md": area}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert f"{check.AREA_DIR}/a.md:2: a fenced code block that never closes" in out

    def test_claude_md_ending_inside_an_open_fence_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = _claude_md("Body.\n\n~~~\n" + _entry("A rule out of sight", "check — `x`") + "\n")
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert out.count("CLAUDE.md:11: a fenced code block that never closes") == 1

    def test_claude_md_ending_inside_an_open_fence_after_the_section_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = _claude_md() + "\n~~~\n" + _entry("A rule out of sight", "check — `x`")
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:15: a fenced code block that never closes" in out

    def test_a_shorter_run_does_not_close_a_longer_fence(self) -> None:
        text = "````markdown\n```\n- **Inside** — check — `x`\n```\n````\n" + _entry("Outside")
        (found,), malformed = check.entries(text, "a.md")
        assert malformed == []
        assert found.headline == "Outside"

    def test_a_run_with_an_info_string_does_not_close_a_fence(self) -> None:
        found, malformed = check.entries("```\n- **Inside** — check — `x`\n```python\n", "a.md")
        assert found == []
        assert malformed == ["a.md:1: a fenced code block that never closes"]

    def test_a_fenced_level_two_line_does_not_end_the_section(self) -> None:
        text = _claude_md("Body.\n\n```\n## Not a heading\n```\n\nStill inside.\n\n")
        ((_, section),) = check.register_sections(text)
        assert "Still inside." in section
        assert "## Security" not in section


class TestOtherMarkersOutsideTheSection:
    @pytest.mark.parametrize("marker", ["* ", "+ ", "1. "])
    def test_an_entry_under_another_marker_outside_the_section_fails(
        self, marker: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = _claude_md() + "\n## More\n\n" + f"{marker}**{'x' * 400}** — check — `x`\n"
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:17: a register entry outside the register section" in out

    @pytest.mark.parametrize("tier", ["check", "test", "prompt-only", "test + prompt-only"])
    def test_an_entry_with_any_tier_outside_the_section_fails(
        self, tier: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = _claude_md() + "\n## More\n\n" + _entry("A rule out of sight", f"{tier} — `x`")
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 1
        assert "CLAUDE.md:17: a register entry outside the register section" in out

    def test_a_tier_word_followed_by_letters_is_no_tier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        claude = "## Traps\n\n- **A trap** — checked by hand, see below.\n\n" + _claude_md()
        _tree(tmp_path, claude, {"a.md": _entry("An area rule")}, monkeypatch)
        code, out = _run(capsys)
        assert code == 0, out
