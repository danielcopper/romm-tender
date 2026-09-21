"""Tests for ``scripts/check_generated_installer_logo.py`` and the art it guards.

The check is the only thing standing between the installer's greeter and a
hand-tidied copy of a drawing, so what is asserted here is that it notices — one
cell is enough — rather than that it passes today, which a check that always
passed would also manage.

Both modules load via ``importlib`` because ``scripts/`` is not on ``sys.path``.
"""

from __future__ import annotations

import importlib.util
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
        image = generator.read_png(generator.SOURCE)
        braille = generator.braille_cells(generator.Classified(image, generator.INK_ONLY))
        drawn = generator.ascii_cells(generator.Classified(image, generator.EVERYTHING_DRAWN))

        assert len(braille) == generator.BRAILLE_ROWS
        assert all(len(row) == generator.BRAILLE_COLUMNS for row in braille)
        assert len(drawn) == generator.ASCII_ROWS
        assert all(len(row) == generator.ASCII_COLUMNS for row in drawn)

    def test_every_braille_row_carries_ink(self, generator):
        """The crop is to what the cells DRAW, so none of the ten is blank.

        Cropping to the whole mark instead spends the last row on the disc's
        bottom rim, which carries no ink and so no dots.
        """
        image = generator.read_png(generator.SOURCE)
        braille = generator.braille_cells(generator.Classified(image, generator.INK_ONLY))

        for index, row in enumerate(braille):
            assert any(character != " " for character, _colour in row), f"row {index} is blank"

    def test_the_two_tones_come_from_the_palette_the_build_ships(self, generator):
        """Written down nowhere: a drawing in colours the mark is not in is a different mark."""
        gen = _load(_REPO_ROOT / "scripts" / "logo" / "gen.py", "gen")
        shipped = gen.BY_NAME[gen.CHOSEN]

        ring, button = generator.tones()

        assert (ring, button) == (shipped.disc[0], shipped.dot_peach)

    def test_a_run_is_one_colour_and_the_runs_cover_the_row(self, generator):
        """The installer splits runs and prints them; it never counts columns."""
        image = generator.read_png(generator.SOURCE)
        for row in generator.braille_cells(generator.Classified(image, generator.INK_ONLY)):
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
