"""Tender's start is the resolver's command, on every recorded launch-command machine (#2221 D1, D2).

Each vector in ``tests/adapters/atlas_vectors/`` whose expectations hold a
``launch_command`` answer is asked its own question live, and that answer is
read through Tender's reading module (:func:`adapters.atlas_launch.read_start`).
What is compared is keyed on what the resolver states as data:

- a refused answer (``command`` null) is a refused start, and so is a command
  whose distribution environment is not stated, one with a variable stated
  without a value, and one whose working folder has no host path;
- every other command starts, and Tender runs exactly its program with its
  arguments in order, sets every variable it states — the distribution's before
  the command's own — and starts in the folder it names.

Its limit: which word a refusal reads as is the reading module's table and its
own tests' (``tests/adapters/test_atlas_launch.py``); here only refused or not.
"""

from __future__ import annotations

from typing import Any

import pytest
from _atlas_machines import fixture_machine, select, vectors_asking
from _vendor import atlas
from _vendor.atlas import VARIANT_FLATPAK

from adapters.atlas_launch import read_start
from domain.start_command import StartCommand, StartRefused

_VECTORS = vectors_asking("launch_command")


def _recorded_complete(command: dict[str, Any] | None) -> bool:
    if command is None:
        return False
    variables = command["distribution_environment"]
    if variables is None or any(variable["value"] is None for variable in variables):
        return False
    folder = command["working_folder"]
    return folder is None or folder["host_path"] is not None


# The vectors whose recorded command is complete: the ones that start.
_STARTING = [(stem, v) for stem, v in _VECTORS if _recorded_complete(v["expected"]["launch_command"]["command"])]


def _asked(vector: dict[str, Any]) -> Any:
    inp = vector["input"]
    query = inp["launch_command_query"]
    installation = select(atlas.detect(inp["home"], fixture_machine(inp)), query.get("installation"))
    return installation.launch_command(query["system"], query["content_path"], label=query.get("label"))


def _complete(command: Any) -> bool:
    variables = command.distribution_environment
    if variables is None or any(variable.value is None for variable in variables):
        return False
    folder = command.working_folder
    return folder is None or folder.host_path is not None


def test_the_sweep_reaches_a_start_and_a_refusal_of_every_kind():
    shapes: set[str] = set()
    for _stem, vector in _VECTORS:
        command = vector["expected"]["launch_command"]["command"]
        if command is None:
            shapes.add("refused")
            continue
        variables = command["distribution_environment"]
        if variables is None:
            shapes.add("environment not stated")
        elif any(variable["value"] is None for variable in variables):
            shapes.add("a value not stated")
        else:
            shapes.add(f"starts as {command['program']['variant']}")

    assert len(_VECTORS) == 89
    assert len(_STARTING) == 26
    assert shapes == {
        "refused",
        "environment not stated",
        "a value not stated",
        "starts as flatpak",
        "starts as native",
        "starts as appimage",
    }


@pytest.mark.parametrize("vector", [pytest.param(v, id=f"{stem}:{v['name']}") for stem, v in _VECTORS])
class TestTendersStartIsTheResolversCommand:
    def test_the_live_answer_is_the_recorded_one(self, vector):
        answer = _asked(vector)

        assert (answer.command is None) == (vector["expected"]["launch_command"]["command"] is None)

    def test_it_starts_exactly_where_the_command_is_complete(self, vector):
        answer = _asked(vector)

        result = read_start(answer, writable=("/games",), read_only=("/store",))

        if answer.command is not None and _complete(answer.command):
            assert isinstance(result, StartCommand)
        else:
            assert isinstance(result, StartRefused)


@pytest.mark.parametrize("vector", [pytest.param(v, id=f"{stem}:{v['name']}") for stem, v in _STARTING])
class TestAStartRunsTheResolversCommand:
    def test_it_runs_the_program_with_its_arguments_in_order_and_every_variable_it_states(self, vector):
        answer = _asked(vector)
        command = answer.command

        result = read_start(answer)

        assert isinstance(result, StartCommand)
        program = command.program
        variables = [
            *((v.name, v.value) for v in command.distribution_environment),
            *((v.name, v.value) for v in command.environment),
        ]
        if program.variant == VARIANT_FLATPAK:
            tail = (f"--command={program.command}", program.app_id, *command.arguments)
            set_vars = [word.removeprefix("--env=") for word in result.argv if word.startswith("--env=")]
        else:
            tail = (program.path, *command.arguments)
            set_vars = list(result.argv[1 : -len(tail)]) if variables else []
        assert result.argv[-len(tail) :] == tail
        assert set_vars == [f"{name}={value}" for name, value in variables]
        folder = command.working_folder
        assert result.folder == (folder.host_path if folder is not None else None)
