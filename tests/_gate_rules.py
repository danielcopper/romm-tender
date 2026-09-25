"""Which endpoints declare a conflict rule, read from wherever the rule is declared.

A rule is declared in one of two places: a gate decorator on the endpoint, or a
``hold("<endpoint>", migration=…, sync=…, prune=…)`` call at the entry of the
use case the endpoint calls, under ``backend/services/``. The decorators are
read off the loaded ``Plugin`` by their markers; the ``hold`` calls are read
from the source by AST, since a use case's rules are not visible on the object.
A ``hold`` call whose label or rule keywords are not literals fails the read
instead of being skipped.

Import as ``from _gate_rules import endpoints_with_rule`` — ``tests/`` is on the
path via the root conftest.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SERVICES = Path(__file__).resolve().parent.parent / "backend" / "services"

# Each rule's decorator marker; the rule's name is also its ``hold`` keyword.
_MARKERS = {
    "exclusive_start": "_prune_exclusive_start",
    "migration": "_migration_blocked",
    "sync": "_sync_active_blocked",
    "prune": "_prune_active_blocked",
}


def _held_rules() -> dict[str, set[str]]:
    """Every ``async with <x>.hold(...)`` under ``backend/services/``: label → the rules it names as true."""
    held: dict[str, set[str]] = {}
    for path in sorted(_SERVICES.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncWith):
                continue
            for item in node.items:
                call = item.context_expr
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                if call.func.attr != "hold":
                    continue
                where = f"{path.relative_to(_SERVICES.parent)}:{call.lineno}"
                assert call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str), (
                    f"{where}: hold's label must be a string literal"
                )
                rules = held.setdefault(call.args[0].value, set())
                for keyword in call.keywords:
                    assert keyword.arg in _MARKERS and isinstance(keyword.value, ast.Constant), (
                        f"{where}: hold's rules must be literal keywords among {sorted(_MARKERS)}"
                    )
                    if keyword.value.value is True:
                        rules.add(keyword.arg)
    return held


def endpoints_with_rule(rule: str) -> set[str]:
    """The endpoints whose gate decorator marks *rule*, plus every ``hold`` label naming it.

    A ``hold`` label is not filtered against the endpoints: one that names no
    endpoint lands in the set and makes an equality with an endpoint list fail.
    """
    from host.dispatch import reachable_methods
    from main import Plugin

    marker = _MARKERS[rule]
    decorated = {name for name, method in reachable_methods(Plugin()).items() if getattr(method, marker, False)}
    held = {label for label, rules in _held_rules().items() if rule in rules}
    return decorated | held
