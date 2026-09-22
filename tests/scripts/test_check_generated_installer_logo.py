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
import math
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
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


@functools.lru_cache(maxsize=1)
def _rendered(module: ModuleType) -> object:
    """The rasterised mark, drawn once for the whole file.

    Cached because every case below wants the same raster and drawing it is a
    rsvg-convert run plus a megapixel of classification.
    """
    return module.render_source()


def _source(generator: ModuleType) -> object:
    return _rendered(generator)


def _braille(generator: ModuleType) -> list[list[tuple[str, str]]]:
    source = generator.Classified(_source(generator), generator.INK_ONLY)
    return generator.braille_cells(source, generator.Buttons(source))


def _ascii(generator: ModuleType) -> list[list[tuple[str, str]]]:
    source = generator.Classified(_source(generator), generator.EVERYTHING_DRAWN)
    return generator.ascii_cells(source, generator.Buttons(source))


def _run_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CHECK_PATH)],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )


class TestTheCheck:
    def test_the_committed_block_is_what_the_generator_emits(self):
        result = _run_check()

        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK:" in result.stdout

    def test_one_edited_cell_is_reported(self):
        """The failure this exists for: a drawing tidied by hand, one cell at a time."""
        original = _INSTALL.read_bytes()
        text = original.decode("utf-8")
        edited = text.replace("LOGO_BRAILLE=(\n    '", "LOGO_BRAILLE=(\n    'r", 1)
        assert edited != text
        _INSTALL.write_bytes(edited.encode("utf-8"))
        try:
            result = _run_check()
        finally:
            _INSTALL.write_bytes(original)

        assert result.returncode == 1
        assert "not what `scripts/logo/terminal.py` emits today" in result.stdout
        assert "--terminal" in result.stdout


class TestTheDrawing:
    def test_both_renderings_are_the_size_they_say_they_are(self, generator):
        braille = _braille(generator)
        drawn = _ascii(generator)

        assert len(braille) == generator.BRAILLE_ROWS
        assert all(len(row) == generator.BRAILLE_COLUMNS for row in braille)
        assert len(drawn) == generator.ASCII_ROWS
        assert all(len(row) == generator.ASCII_COLUMNS for row in drawn)

    def test_every_braille_row_carries_ink(self, generator):
        """The crop is to what the cells DRAW, so none of the ten is blank.

        Cropping to the whole mark instead spends the last row on the disc's
        bottom rim, which carries no ink and so no dots.
        """
        for index, row in enumerate(_braille(generator)):
            assert any(character != " " for character, _colour in row), f"row {index} is blank"

    def test_the_two_tones_come_from_the_palette_the_build_ships(self, generator):
        """Written down nowhere: a drawing in colours the mark is not in is a different mark."""
        gen = _load(_REPO_ROOT / "scripts" / "logo" / "gen.py", "gen")
        shipped = gen.BY_NAME[gen.CHOSEN]

        ring, button = generator.tones()

        assert (ring, button) == (shipped.disc[0], shipped.dot_peach)

    def test_a_run_is_one_colour_and_the_runs_cover_the_row(self, generator):
        """The installer splits runs and prints them; it never counts columns."""
        for row in _braille(generator):
            encoded = generator.runs(row)
            runs = encoded.split(generator.RUN_SEPARATOR)
            rebuilt = "".join(run.split(":", 1)[1] for run in runs)

            assert rebuilt == "".join(character for character, _colour in row)
            assert len(rebuilt) == generator.BRAILLE_COLUMNS

    def test_the_nearest_256_index_is_the_nearest_one(self, generator):
        """Exact cube corners, and a grey that is not in the cube at all."""
        assert generator.nearest_256("#000000") == 16
        assert generator.nearest_256("#ffffff") == 231
        assert generator.nearest_256("#080808") == 232


class TestTheButtons:
    """The four dots are DRAWN, because sampling them frays a circle's edge."""

    def test_the_mark_has_exactly_four_of_them(self, generator):
        """Fewer or more means the classifier changed, not that the mark did."""
        source = generator.Classified(_source(generator), generator.INK_ONLY)

        assert len(generator.Buttons(source).centres) == 4

    @pytest.mark.parametrize(
        ("keep", "across", "down", "squash"),
        [("INK_ONLY", 48, 40, 1.0), ("EVERYTHING_DRAWN", 26, 13, 0.5)],
    )
    def test_all_four_are_stamped_as_the_same_shape(self, generator, keep, across, down, squash):
        """Symmetric by construction, and identical because the radius is shared.

        Checked one button at a time: two of them sit close enough on a bar that
        their discs touch, so a single grid cannot say which sub-cell came from
        which.
        """
        source = generator.Classified(_source(generator), getattr(generator, keep))
        buttons = generator.Buttons(source)

        shapes = set()
        for centre in buttons.centres:
            alone = generator.Buttons.__new__(generator.Buttons)
            alone.centres = [centre]
            alone.radius = buttons.radius
            grid = generator._stamped(source, alone, across, down, squash)
            origin_x = math.floor(centre[0] / source.width * across)
            origin_y = math.floor(centre[1] / source.height * down)
            shapes.add(
                frozenset(
                    (column - origin_x, row - origin_y)
                    for row in range(down)
                    for column in range(across)
                    if grid[row][column]
                )
            )

        assert len(shapes) == 1, "the four buttons are not the same shape"
        assert next(iter(shapes)), "nothing was stamped at all"

    def test_a_stamped_cell_is_the_buttons_colour_and_never_the_rings(self, generator):
        """The defect this replaced: the dots' dark rim read as ink, so each

        button wore a ring of the ring's blue. A cell holding any stamped
        sub-cell is the button's, whatever else is under it.
        """
        source = generator.Classified(_source(generator), generator.INK_ONLY)
        buttons = generator.Buttons(source)
        stamp = generator._stamped(source, buttons, generator.BRAILLE_COLUMNS * 2, generator.BRAILLE_ROWS * 4, 1.0)
        cells = generator.braille_cells(source, buttons)

        for row, line in enumerate(cells):
            for column, (_character, colour) in enumerate(line):
                stamped = any(stamp[row * 4 + dy][column * 2 + dx] for dx, dy, _bit in generator._DOT_BITS)
                if stamped:
                    assert colour == generator.BUTTON, f"cell {column},{row} holds a button and is not one"

    def test_the_radius_is_the_one_the_area_implies_plus_the_outline(self, generator):
        """The widest span would grow with a single misread rim pixel; the area does not.

        The outline is a fraction of the radius rather than a count of pixels,
        so the button is the same part of the mark at any render resolution.
        """
        source = generator.Classified(_source(generator), generator.INK_ONLY)
        buttons = generator.Buttons(source)
        areas = [len(cells) for cells in generator._components(source, generator.BUTTON_PIXEL)]
        implied = sum(math.sqrt(area / math.pi) for area in areas) / len(areas)

        assert buttons.radius == pytest.approx(implied * (1 + generator.BUTTON_OUTLINE))
