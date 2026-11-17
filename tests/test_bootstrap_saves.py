"""The save service's wiring: every part built once, in ``bootstrap/saves.py``, and handed to its holders."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import pytest

from services.saves.save_directory import SaveDirectoryFollower
from services.saves.sync_engine.negotiate import NegotiateEnvelope
from services.saves.sync_engine.rollback import RollbackOrchestrator
from tests.services.saves._helpers import make_parts

if TYPE_CHECKING:
    from bootstrap import SaveParts


@pytest.fixture
def parts(tmp_path) -> SaveParts:
    built, _fake = make_parts(tmp_path)
    return built


def _reachable(parts: SaveParts) -> list[Any]:
    """Every object of the save package reachable from the parts through their attributes, each once."""
    seen: dict[int, Any] = {}
    stack = [getattr(parts, field.name) for field in dataclasses.fields(parts)]
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen[id(obj)] = obj
        stack.extend(
            value
            for value in getattr(obj, "__dict__", {}).values()
            if type(value).__module__.startswith("services.saves")
        )
    return list(seen.values())


def _strays(parts: SaveParts) -> list[str]:
    """Each attribute that holds an object of a part's type other than the part ``wire_saves`` built."""
    built = {type(getattr(parts, field.name)): getattr(parts, field.name) for field in dataclasses.fields(parts)}
    return [
        f"{type(holder).__name__}.{name}"
        for holder in _reachable(parts)
        for name, value in getattr(holder, "__dict__", {}).items()
        if type(value) in built and value is not built[type(value)]
    ]


class TestEveryPartIsBuiltOnce:
    def test_every_holder_keeps_the_part_wire_saves_built(self, parts):
        assert _strays(parts) == []

    def test_the_walk_reaches_the_engines_own_parts(self, parts):
        reached = {type(obj) for obj in _reachable(parts)}
        assert {RollbackOrchestrator, SaveDirectoryFollower, NegotiateEnvelope} <= reached


class TestTheEnginesOwnPartsTakeTheLoopOnce:
    def test_the_device_registry_holds_the_engines_loop(self, parts):
        engine = parts.sync_engine
        assert engine._devices._loop is engine._loop

    def test_the_rollback_holds_the_engines_loop(self, parts):
        engine = parts.sync_engine
        assert engine._rollback._loop is engine._loop
