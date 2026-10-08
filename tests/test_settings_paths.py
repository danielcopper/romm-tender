"""A settings path in a sentence a reader sees is written with "›" — ``Settings › Emulator sources`` — never an arrow.

What the scan reads as such a sentence: every line of the documentation, every
Python string literal under ``backend/`` and ``scripts/`` that is not a
docstring, and every line of ``frontend/src/`` that does not open as a comment.
A comment or a docstring that names a settings path is no sentence a reader
sees and may spell it either way. What it sees is a ``Settings`` step beside an
arrow (``→``, ``->``, or ``>`` between spaces) on one line; a path broken across
two lines between its steps, a TS comment that does not open its line, and a
path with no ``Settings`` step in it (``Data Management › Gone from RomM``, a
menu of Decky's or of an emulator's) pass it: those are held to "›" by review
alone.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_DOCS = ("docs", "GLOSSARY.md", "README.md")
_PYTHON = ("backend", "scripts")
_FRONTEND = "frontend/src"
_SKIPPED = ("backend/_vendor/", "backend/native/")
# A bare ">" counts only between spaces, so markup (``<Dot>Settings</Dot>``) is no path.
_ARROW = r"(?:\s*→\s*|\s*->\s*|\s>\s)"
_ARROWED = re.compile(rf"\bSettings{_ARROW}\w|\w{_ARROW}Settings\b")
_COMMENT_OPENERS = ("//", "/*", "*")


def _files(root: str, suffixes: set[str]) -> list[Path]:
    base = _REPO / root
    paths = [base] if base.is_file() else sorted(path for path in base.rglob("*") if path.is_file())
    return [path for path in paths if path.suffix in suffixes and not any(s in path.as_posix() for s in _SKIPPED)]


def _docstrings(tree: ast.AST) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, owners)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def python_sentences(source: str) -> list[tuple[int, str]]:
    """Every string literal of *source* that is not a docstring, with its line."""
    tree = ast.parse(source)
    skipped = _docstrings(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped
    ]


def frontend_sentences(source: str) -> list[tuple[int, str]]:
    """Every line of *source* that does not open as a comment."""
    return [
        (number, line)
        for number, line in enumerate(source.splitlines(), 1)
        if not line.lstrip().startswith(_COMMENT_OPENERS)
    ]


def _hits(path: Path, sentences: list[tuple[int, str]]) -> list[str]:
    return [f"{path.relative_to(_REPO)}:{number}" for number, text in sentences if _ARROWED.search(text)]


def test_no_settings_path_a_reader_sees_is_written_with_an_arrow():
    found: list[str] = []
    for root in _DOCS:
        for path in _files(root, {".md"}):
            found += _hits(path, list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1)))
    for root in _PYTHON:
        for path in _files(root, {".py"}):
            found += _hits(path, python_sentences(path.read_text(encoding="utf-8")))
    for path in _files(_FRONTEND, {".ts", ".tsx"}):
        found += _hits(path, frontend_sentences(path.read_text(encoding="utf-8")))

    assert found == []


@pytest.mark.parametrize(
    "line",
    [
        "Downloads need RetroDECK, which is switched off in Settings → Emulator sources.",
        "re-set it in Steam (Settings -> Display -> UI Scale).",
        "RetroArch's two settings (**Settings > Saving**)",
        "check Steam → Settings › Display",
    ],
)
def test_the_scan_sees_an_arrow_beside_a_settings_step(line):
    assert _ARROWED.search(line) is not None


def test_the_scan_passes_a_path_written_with_the_chevron():
    assert _ARROWED.search("Every emulator source is switched off in Settings › Emulator sources.") is None


def test_a_python_string_is_read_and_its_docstring_and_comments_are_not():
    source = '"""Settings → Updates."""\n# Settings → Updates\nMESSAGE = "Open Settings → Updates."\n'

    assert python_sentences(source) == [(3, "Open Settings → Updates.")]


def test_a_frontend_line_is_read_unless_it_opens_as_a_comment():
    source = '/** Settings → Updates */\n// Settings → Updates\nconst text = "Settings → Updates";\n'

    assert frontend_sentences(source) == [(3, 'const text = "Settings → Updates";')]
