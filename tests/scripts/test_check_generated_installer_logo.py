"""Tests for ``scripts/check_generated_installer_logo.py`` and the art it guards.

The check is the only thing standing between the installer's greeter and a
hand-tidied copy of a drawing, so what is asserted here is that it notices — one
cell is enough — rather than that it passes today, which a check that always
passed would also manage.

Both modules load via ``importlib`` because ``scripts/`` is not on ``sys.path``.
"""

from __future__ import annotations

import functools
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]

# What one half-block cell is: its character, and the colour of each half — None
# where that half is left to the terminal's own background.
_Colour = tuple[int, int, int] | None
_Cell = tuple[str, _Colour, _Colour]
_CHECK_PATH = _REPO_ROOT / "scripts" / "check_generated_installer_logo.py"
_INSTALL = _REPO_ROOT / "install.sh"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "logo"))
    return _load(_REPO_ROOT / "scripts" / "logo" / "terminal.py", "terminal")


def _run_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CHECK_PATH)],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )


@functools.lru_cache(maxsize=1)
def _icon(module: ModuleType) -> tuple[tuple[_Cell, ...], ...]:
    """The icon's cells, drawn once for the whole file.

    Cached because every case below wants the same drawing and making it is an
    rsvg-convert run plus a megapixel of box filtering.
    """
    return tuple(tuple(row) for row in module.icon_cells())


@functools.lru_cache(maxsize=1)
def _drawing(module: ModuleType) -> tuple[tuple[tuple[str, str], ...], ...]:
    """The ASCII drawing's cells, drawn once for the whole file."""
    return tuple(tuple(row) for row in module.ascii_cells())


class TestTheCheck:
    def test_the_committed_block_is_what_the_generator_emits(self):
        result = _run_check()

        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK:" in result.stdout

    def test_one_edited_cell_is_reported(self):
        """The failure this exists for: a drawing tidied by hand, one cell at a time."""
        original = _INSTALL.read_bytes()
        text = original.decode("utf-8")
        edited = text.replace("LOGO_ASCII=(\n    '", "LOGO_ASCII=(\n    'r:", 1)
        assert edited != text
        _INSTALL.write_bytes(edited.encode("utf-8"))
        try:
            result = _run_check()
        finally:
            _INSTALL.write_bytes(original)

        assert result.returncode == 1
        assert "install.sh" in result.stdout
        assert "--terminal" in result.stdout

    def test_an_edited_wordmark_is_reported(self):
        """It ships two files nothing reads yet, and the check still holds them."""
        module = _load(_REPO_ROOT / "scripts" / "logo" / "terminal.py", "terminal")
        target = module.WORDMARK_FILES[1][0]
        original = target.read_bytes()
        target.write_bytes(original + b"tidied by hand\n")
        try:
            result = _run_check()
        finally:
            target.write_bytes(original)

        assert result.returncode == 1
        assert target.name in result.stdout


class TestTheIcon:
    """Half-blocks, where the two colours of a cell ARE the picture."""

    def test_it_is_the_size_it_says_it_is(self, generator):
        icon = _icon(generator)

        assert len(icon) == generator.icon_rows()
        assert all(len(row) == generator.ICON_COLUMNS for row in icon)

    def test_every_cell_is_a_half_block_or_a_space(self, generator):
        """Nothing is drawn in glyph shapes, so there are only three characters."""
        drawn = {character for row in _icon(generator) for character, _fg, _bg in row}

        assert drawn <= {generator.UPPER_HALF, generator.LOWER_HALF, " "}

    def test_a_space_is_left_to_the_terminal(self, generator):
        """Painting a box the colour of a GUESS at the background is the one thing
        that looks wrong on the terminal the guess was wrong about."""
        for row in _icon(generator):
            for character, foreground, background in row:
                if character == " ":
                    assert foreground is None
                    assert background is None

    def test_it_carries_the_marks_own_colours(self, generator):
        """The real mark, not a silhouette: the warm buttons and the navy ink are in it."""
        colours = [
            colour
            for row in _icon(generator)
            for _character, upper, lower in row
            for colour in (upper, lower)
            if colour
        ]

        assert any(red > 180 and blue < 170 for red, _green, blue in colours), "no warm button tone"
        assert any(sum(colour) < 210 for colour in colours), "no dark ink tone"
        assert any(blue > 200 and red < 190 for red, _green, blue in colours), "no light disc tone"

    def test_the_rows_it_emits_are_padded_to_one_width(self, generator):
        """A caller puts a text block beside it, and cannot measure a string with escapes in it."""
        for row in _icon(generator):
            printable = generator.block_row(row, "truecolor", generator.ICON_COLUMNS)
            stripped = re.sub(r"\\033\[[0-9;]*m", "", printable)

            assert len(stripped) == generator.ICON_COLUMNS

    def test_the_256_colour_form_writes_no_24_bit_escape(self, generator):
        """A terminal that did not say it takes 24-bit colour is not given any."""
        for row in _icon(generator):
            assert "38;2;" not in generator.block_row(row, "256", generator.ICON_COLUMNS)


class TestTheAsciiDrawing:
    """The other technique, for the terminal that cannot show the first one."""

    def test_it_is_the_size_it_says_it_is(self, generator):
        drawing = _drawing(generator)

        assert len(drawing) == generator.ASCII_COLUMNS // 2
        assert all(len(row) == generator.ASCII_COLUMNS for row in drawing)

    def test_it_is_drawn_in_weights_not_in_blocks(self, generator):
        """Without colour the only thing left to carry the mark is glyph weight."""
        drawn = {character for row in _drawing(generator) for character, _colour in row}

        assert drawn <= set("O#+:. ")
        assert "O" in drawn, "the buttons are not drawn"
        assert "#" in drawn, "the ring is not drawn"

    def test_a_run_is_one_colour_and_the_runs_cover_the_row(self, generator):
        """The installer splits runs and prints them; it never counts columns."""
        for row in _drawing(generator):
            encoded = generator.runs(list(row), generator.ASCII_COLUMNS)
            runs = encoded.split(generator.RUN_SEPARATOR)
            rebuilt = "".join(run.split(":", 1)[1] for run in runs)

            assert rebuilt == "".join(character for character, _colour in row)
            assert len(rebuilt) == generator.ASCII_COLUMNS

    def test_the_two_tones_come_from_the_palette_the_build_ships(self, generator):
        """A drawing in colours the mark is not in is a different mark."""
        gen = _load(_REPO_ROOT / "scripts" / "logo" / "gen.py", "gen")

        _ring, button = generator.tones()

        assert button == gen.BY_NAME[gen.CHOSEN].dot_peach


class TestTheWordmark:
    def test_it_is_the_size_it_was_approved_at(self, generator):
        rows = generator.wordmark_cells()

        assert len(rows) == generator.WORDMARK_ROWS
        assert all(len(row) == generator.WORDMARK_COLUMNS for row in rows)

    def test_its_plain_form_still_spells_the_word(self, generator):
        """A diff can read it; that is the whole reason the second file exists."""
        plain = generator.wordmark_plain()

        assert plain.count("\n") == generator.WORDMARK_ROWS
        assert any(line.strip() for line in plain.splitlines())


class TestTheColourMapping:
    def test_the_nearest_256_index_is_the_nearest_one(self, generator):
        """Exact cube corners, and a grey that is not in the cube at all."""
        assert generator.nearest_256((0, 0, 0)) == 16
        assert generator.nearest_256((255, 255, 255)) == 231
        assert generator.nearest_256((8, 8, 8)) == 232
