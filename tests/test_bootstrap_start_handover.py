"""The start hand-over the bootstrap wires: in the session's runtime directory, or none (#2221 D15, #2708 C3)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from adapters.atlas_launch import AtlasLaunchAdapter
from adapters.start_handover import StartHandoverAdapter
from bootstrap.adapters import bootstrap
from domain.app_directories import AppDirectories
from domain.update_release import UpdateSource

if TYPE_CHECKING:
    from pathlib import Path

_UPDATE_SOURCE = UpdateSource(release_api="http://127.0.0.1:9/releases/latest", installed_program=False)


def _directories(tmp_path: Path, handover_dir: str | None) -> AppDirectories:
    return AppDirectories(
        config_dir=str(tmp_path / "config"),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        state_dir=str(tmp_path / "state"),
        runtime_dir=str(tmp_path / "run"),
        code_dir=str(tmp_path / "code"),
        bin_dir=str(tmp_path / "home" / ".local" / "bin"),
        handover_dir=handover_dir,
    )


def _bootstrap(tmp_path: Path, handover_dir: str | None):
    launcher = tmp_path / "code" / "bin" / "tender-rom-launcher"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"#!/bin/bash\n")
    return bootstrap(
        directories=_directories(tmp_path, handover_dir),
        update_source=_UPDATE_SOURCE,
        user_home=str(tmp_path / "home"),
        logger=logging.getLogger("test_bootstrap_start_handover"),
    )


def test_a_start_is_handed_over_in_the_runtime_directorys_folder(tmp_path):
    folder = tmp_path / "run" / "start"

    adapters = _bootstrap(tmp_path, str(folder)).adapters

    assert isinstance(adapters.start_handover, StartHandoverAdapter)
    adapters.start_handover.hand_over(7, "/games", ["/bin/true"], create_folder=False)
    assert (folder / "7").read_bytes() == b"/games\0/bin/true\0"


def test_without_a_runtime_directory_no_start_is_handed_over(tmp_path):
    assert _bootstrap(tmp_path, None).adapters.start_handover is None


def test_the_start_command_is_read_through_the_launch_reading_module(tmp_path):
    assert isinstance(_bootstrap(tmp_path, None).adapters.start_commands, AtlasLaunchAdapter)
