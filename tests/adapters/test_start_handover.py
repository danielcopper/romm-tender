"""Tests for the start hand-over adapter — the file the shortcut launcher claims at one start (#2221 D15).

The last class runs the real ``bin/tender-rom-launcher`` over what the adapter
wrote, so the two halves of the record's format are held against each other.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from adapters.start_handover import StartHandoverAdapter

_LAUNCHER = Path(__file__).resolve().parents[2] / "bin" / "tender-rom-launcher"


def _folder(runtime: Path) -> Path:
    return runtime / "romm-tender" / "start"


def _adapter(runtime: Path) -> StartHandoverAdapter:
    return StartHandoverAdapter(folder=str(_folder(runtime)))


class TestTheRecord:
    def test_it_holds_the_folder_then_the_program_and_its_arguments_each_ended_by_a_nul(self, tmp_path):
        _adapter(tmp_path).hand_over(42, "/games", ["flatpak", "run", "a b"], create_folder=False)

        assert (_folder(tmp_path) / "42").read_bytes() == b"/games\0flatpak\0run\0a b\0"

    def test_it_is_the_users_alone(self, tmp_path):
        _adapter(tmp_path).hand_over(42, "/games", ["/bin/true"], create_folder=False)

        assert stat.S_IMODE((_folder(tmp_path) / "42").stat().st_mode) == 0o600
        assert stat.S_IMODE(_folder(tmp_path).stat().st_mode) == 0o700

    def test_a_folder_left_wider_is_narrowed(self, tmp_path):
        _folder(tmp_path).mkdir(parents=True, mode=0o755)
        os.chmod(_folder(tmp_path), 0o755)

        _adapter(tmp_path).hand_over(42, "/games", ["/bin/true"], create_folder=False)

        assert stat.S_IMODE(_folder(tmp_path).stat().st_mode) == 0o700

    def test_a_hand_over_left_from_an_earlier_press_is_replaced_and_nothing_else_is_left(self, tmp_path):
        adapter = _adapter(tmp_path)
        adapter.hand_over(42, "/old", ["/bin/old"], create_folder=False)

        adapter.hand_over(42, "/new", ["/bin/new"], create_folder=False)

        assert [entry.name for entry in _folder(tmp_path).iterdir()] == ["42"]
        assert (_folder(tmp_path) / "42").read_bytes() == b"/new\0/bin/new\0"

    def test_a_path_that_is_no_valid_text_is_written_as_its_bytes(self, tmp_path):
        folder = os.fsdecode(b"/games/\xff")

        _adapter(tmp_path).hand_over(42, folder, ["/bin/true"], create_folder=False)

        assert (_folder(tmp_path) / "42").read_bytes() == b"/games/\xff\0/bin/true\0"


class TestWhatItRefuses:
    @pytest.mark.parametrize(("folder", "argv"), [("/games", ["a\0b"]), ("/ga\0mes", ["/bin/true"]), ("/games", [])])
    def test_a_value_the_record_cannot_hold_writes_nothing(self, tmp_path, folder, argv):
        with pytest.raises(ValueError, match="NUL"):
            _adapter(tmp_path).hand_over(42, folder, argv, create_folder=False)

        assert not _folder(tmp_path).exists()

    def test_a_folder_that_cannot_be_created_raises(self, tmp_path):
        _folder(tmp_path).mkdir(parents=True)
        os.chmod(_folder(tmp_path), 0o500)
        try:
            with pytest.raises(OSError):
                StartHandoverAdapter(folder=str(_folder(tmp_path) / "inner")).hand_over(
                    42, "/games", ["/bin/true"], create_folder=False
                )
        finally:
            os.chmod(_folder(tmp_path), 0o700)

    def test_a_record_that_cannot_be_put_in_place_raises_and_leaves_no_staging_file(self, tmp_path, monkeypatch):
        def refuse(_source, _target):
            raise OSError("no room")

        monkeypatch.setattr(os, "replace", refuse)

        with pytest.raises(OSError, match="no room"):
            _adapter(tmp_path).hand_over(42, "/games", ["/bin/true"], create_folder=False)

        assert list(_folder(tmp_path).iterdir()) == []


class TestTheStartFolder:
    def test_it_is_created_where_asked(self, tmp_path):
        start = tmp_path / "data" / "start-folder"

        _adapter(tmp_path).hand_over(42, str(start), ["/bin/true"], create_folder=True)

        assert start.is_dir()

    def test_it_is_left_alone_where_not_asked(self, tmp_path):
        start = tmp_path / "missing"

        _adapter(tmp_path).hand_over(42, str(start), ["/bin/true"], create_folder=False)

        assert not start.exists()


class TestTheLauncherReadsWhatItWrites:
    def test_the_launcher_runs_the_command_in_the_folder_with_its_arguments_as_written(self, tmp_path):
        start = tmp_path / "start here"
        _adapter(tmp_path).hand_over(
            42, str(start), ["/bin/sh", "-c", 'printf "%s|%s" "$PWD" "$1"', "sh", "it's one 'word'"], create_folder=True
        )

        result = subprocess.run(
            ["bash", str(_LAUNCHER), "42"],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": os.environ["PATH"], "XDG_RUNTIME_DIR": str(tmp_path)},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{start}|it's one 'word'"
        assert list(_folder(tmp_path).iterdir()) == []
