"""Which endpoints declare a conflict rule, read from the use cases that check it.

A rule is declared by a ``hold("<endpoint>", migration=…, sync=…, prune=…)`` or
``hold_start("<endpoint>", migration=…, sync=…)`` call at the entry of the use
case the endpoint calls, under ``backend/services/``. The calls are read from
the source by AST, since a use case's rules are not visible on the object.
``hold_start`` declares the ``exclusive_start`` rule beside the rules its
keywords name. A call whose label or rule keywords are not literals fails the
read instead of being skipped.

What the read does not see:

- a ``hold`` or ``hold_start`` reached any way but as the direct context
  expression of an ``async with`` — through a local alias, an
  ``AsyncExitStack``, or a helper that calls it;
- any rule-set call outside ``backend/services/`` — one written in ``main.py``,
  say;
- which object the call is made on: every ``async with <x>.hold(...)`` or
  ``<x>.hold_start(...)`` there counts, whatever ``<x>`` is;
- a rule keyword whose literal is anything but ``True`` (``prune=1`` names no
  rule);
- whether the endpoint the label names reaches that use case at all: a label in
  a method nothing calls still counts.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SERVICES = Path(__file__).resolve().parent.parent / "backend" / "services"

# The rules each rule-set method takes, each as a keyword named after the rule,
# and the rule the method declares by being called at all.
_RULE_KEYWORDS = {
    "hold": frozenset({"migration", "sync", "prune"}),
    "hold_start": frozenset({"migration", "sync"}),
}
_IMPLIED_RULE = {"hold_start": "exclusive_start"}


def _held_rules() -> dict[str, set[str]]:
    """Every ``async with <x>.hold(...)`` or ``<x>.hold_start(...)`` under ``backend/services/``: label → its rules."""
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
                method = call.func.attr
                if method not in _RULE_KEYWORDS:
                    continue
                where = f"{path.relative_to(_SERVICES.parent)}:{call.lineno}"
                assert call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str), (
                    f"{where}: {method}'s label must be a string literal"
                )
                rules = held.setdefault(call.args[0].value, set())
                if method in _IMPLIED_RULE:
                    rules.add(_IMPLIED_RULE[method])
                allowed = _RULE_KEYWORDS[method]
                for keyword in call.keywords:
                    assert keyword.arg in allowed and isinstance(keyword.value, ast.Constant), (
                        f"{where}: {method}'s rules must be literal keywords among {sorted(allowed)}"
                    )
                    if keyword.value.value is True:
                        rules.add(keyword.arg)
    return held


def endpoints_with_rule(rule: str) -> set[str]:
    """Every label whose ``hold`` or ``hold_start`` names *rule*.

    A label is not filtered against the endpoints: one that names no endpoint
    lands in the set and makes an equality with an endpoint list fail.
    """
    return {label for label, rules in _held_rules().items() if rule in rules}
