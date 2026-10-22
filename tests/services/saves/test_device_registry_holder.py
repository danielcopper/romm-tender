"""No save service but ``SyncEngine`` holds the ``DeviceRegistry``.

Every save operation takes the device id it sends to RomM from
``SyncEngine.live_device_id``; a service holding the registry itself can read the
stored id around that entry. This reads the source of every module under
``backend/services/saves/`` and fails where one could: an annotation naming
``DeviceRegistry`` (a config field, a constructor parameter, an attribute)
anywhere but in ``SyncEngineConfig``, and a ``DeviceRegistry(...)`` kept on an
attribute of ``self``. ``SaveService`` builds the registry and hands it to the
sync engine without keeping it. Not seen: an unannotated parameter or attribute
holding one, and a registry reached through another object.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SAVES = Path(__file__).resolve().parents[3] / "backend" / "services" / "saves"
_HOLDER = "SyncEngineConfig"
_NAME = "DeviceRegistry"


def _names_registry(node: ast.expr | None) -> bool:
    if node is None:
        return False
    return any(
        (isinstance(sub, ast.Name) and sub.id == _NAME)
        or (isinstance(sub, ast.Attribute) and sub.attr == _NAME)
        or (isinstance(sub, ast.Constant) and isinstance(sub.value, str) and _NAME in sub.value)
        for sub in ast.walk(node)
    )


def _builds_registry(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _names_registry(node.func)


def holders(source: str, rel: str) -> list[str]:
    """Every site in *source* that holds a ``DeviceRegistry`` outside the sync engine's config."""
    found: list[str] = []
    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == _HOLDER:
            allowed.update(id(sub) for sub in ast.walk(node))
    for node in ast.walk(tree):
        if id(node) in allowed:
            continue
        if isinstance(node, ast.AnnAssign) and _names_registry(node.annotation):
            found.append(f"{rel}:{node.lineno} annotates a {_NAME}")
        elif isinstance(node, ast.arg) and _names_registry(node.annotation):
            found.append(f"{rel}:{node.lineno} takes a {_NAME} ({node.arg})")
        elif isinstance(node, ast.Assign) and _builds_registry(node.value):
            found.extend(
                f"{rel}:{node.lineno} keeps a {_NAME} on self.{target.attr}"
                for target in node.targets
                if isinstance(target, ast.Attribute) and _is_self_attribute(target)
            )
    return found


def _is_self_attribute(target: ast.expr) -> bool:
    return isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"


class TestOnlyTheSyncEngineHoldsTheRegistry:
    def test_no_save_service_but_the_sync_engine_holds_the_registry(self):
        found: list[str] = []
        for path in sorted(_SAVES.rglob("*.py")):
            found.extend(holders(path.read_text(encoding="utf-8"), str(path.relative_to(_SAVES))))
        assert found == []

    def test_the_sync_engine_config_is_read(self):
        source = (_SAVES / "sync_engine" / "engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        config = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == _HOLDER)
        assert any(isinstance(n, ast.AnnAssign) and _names_registry(n.annotation) for n in config.body)


class TestWhatCountsAsHolding:
    @pytest.mark.parametrize(
        "source",
        [
            "class CopyConfig:\n    device_registry: DeviceRegistry\n",
            "class Lister:\n    def __init__(self, *, device_registry: DeviceRegistry) -> None: ...\n",
            "class Lister:\n    def __init__(self, *, device_registry: 'DeviceRegistry | None') -> None: ...\n",
            "class Facade:\n    def __init__(self) -> None:\n        self._registry = DeviceRegistry(uow_factory=1)\n",
            "class Facade:\n    def __init__(self) -> None:\n        self._registry = devices.DeviceRegistry()\n",
        ],
    )
    def test_a_holder_is_found(self, source):
        assert len(holders(source, "mod.py")) == 1

    @pytest.mark.parametrize(
        "source",
        [
            "class SyncEngineConfig:\n    device_registry: DeviceRegistry\n",
            "class Facade:\n    def __init__(self) -> None:\n        registry = DeviceRegistry()\n",
            "class Facade:\n    def __init__(self, registry) -> None:\n        self._registry = registry\n",
        ],
    )
    def test_what_is_not_holding_or_cannot_be_seen_passes(self, source):
        assert holders(source, "mod.py") == []
