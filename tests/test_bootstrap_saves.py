"""The save service's wiring: every part built once, in ``bootstrap/saves.py``, and handed to its holders."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.services.saves._helpers import make_parts

if TYPE_CHECKING:
    from bootstrap import SaveParts


@pytest.fixture
def parts(tmp_path) -> SaveParts:
    built, _fake = make_parts(tmp_path)
    return built


# (holder, the attribute it keeps a collaborator in, the part that must be there)
_HELD = [
    ("service", "_rom_info", "rom_info"),
    ("service", "_sync_engine", "sync_engine"),
    ("service", "_status", "status"),
    ("service", "_local_saves", "local_saves"),
    ("service", "_versions", "versions"),
    ("service", "_copies", "copies"),
    ("service", "_slot_listing", "slot_listing"),
    ("service", "_slot_switcher", "slot_switcher"),
    ("service", "_setup_wizard", "setup_wizard"),
    ("service", "_slot_deleter", "slot_deleter"),
    ("save_transfer", "_backup", "save_backup"),
    ("matrix", "_rom_info", "rom_info"),
    ("matrix", "_transfer", "save_transfer"),
    ("sync_engine", "_rom_info", "rom_info"),
    ("sync_engine", "_matrix", "matrix"),
    ("status", "_sync_engine", "sync_engine"),
    ("status", "_matrix", "matrix"),
    ("status", "_rom_info", "rom_info"),
    ("status", "_save_transfer", "save_transfer"),
    ("local_saves", "_sync_engine", "sync_engine"),
    ("versions", "_sync_engine", "sync_engine"),
    ("versions", "_matrix", "matrix"),
    ("versions", "_save_transfer", "save_transfer"),
    ("copies", "_sync_engine", "sync_engine"),
    ("copies", "_matrix", "matrix"),
    ("copies", "_save_transfer", "save_transfer"),
    ("slot_listing", "_sync_engine", "sync_engine"),
    ("slot_switcher", "_sync_engine", "sync_engine"),
    ("slot_switcher", "_status_service", "status"),
    ("slot_switcher", "_save_backup", "save_backup"),
    ("slot_switcher", "_save_transfer", "save_transfer"),
    ("setup_wizard", "_sync_engine", "sync_engine"),
    ("setup_wizard", "_save_backup", "save_backup"),
    ("setup_wizard", "_save_transfer", "save_transfer"),
    ("slot_deleter", "_sync_engine", "sync_engine"),
    ("prune_support", "_sync_engine", "sync_engine"),
    ("prune_support", "_save_backup", "save_backup"),
]


class TestEveryPartIsBuiltOnce:
    @pytest.mark.parametrize(("holder", "attribute", "part"), _HELD)
    def test_a_holder_keeps_the_part_wire_saves_built(self, parts, holder, attribute, part):
        assert getattr(getattr(parts, holder), attribute) is getattr(parts, part)


class TestTheEnginesOwnPartsTakeTheLoopOnce:
    def test_the_device_registry_holds_the_engines_loop(self, parts):
        engine = parts.sync_engine
        assert engine._devices._loop is engine._loop

    def test_the_rollback_holds_the_engines_loop(self, parts):
        engine = parts.sync_engine
        assert engine._rollback._loop is engine._loop
