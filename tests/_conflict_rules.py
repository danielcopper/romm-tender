"""Which endpoints declare a conflict rule, read from the use cases that check it.

A rule is declared by a ``hold("<endpoint>", update=…, migration=…, sync=…,
prune=…)`` or ``hold_start("<endpoint>", update=…, migration=…, sync=…)`` call at the entry of the use
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
    "hold": frozenset({"update", "migration", "sync", "prune"}),
    "hold_start": frozenset({"update", "migration", "sync"}),
}
_IMPLIED_RULE = {"hold_start": "exclusive_start"}


def _held_rules() -> dict[str, set[str]]:
    """Every ``async with <x>.hold(...)`` or ``<x>.hold_start(...)`` under ``backend/services/``: label → its rules."""
    held: dict[str, set[str]] = {}
    for (_where, label), rules in _call_sites().items():
        held.setdefault(label, set()).update(rules)
    return held


def _call_sites() -> dict[tuple[str, str], set[str]]:
    """The same calls one by one: ``(<file>:<line>, label)`` → the rules that one call names."""
    sites: dict[tuple[str, str], set[str]] = {}
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
                rules = sites.setdefault((where, call.args[0].value), set())
                if method in _IMPLIED_RULE:
                    rules.add(_IMPLIED_RULE[method])
                allowed = _RULE_KEYWORDS[method]
                for keyword in call.keywords:
                    assert keyword.arg in allowed and isinstance(keyword.value, ast.Constant), (
                        f"{where}: {method}'s rules must be literal keywords among {sorted(allowed)}"
                    )
                    if keyword.value.value is True:
                        rules.add(keyword.arg)
    return sites


def call_sites_with_rule(rule: str) -> set[tuple[str, str]]:
    """Every ``(<file>:<line>, label)`` whose one ``hold`` or ``hold_start`` call names *rule*."""
    return {site for site, rules in _call_sites().items() if rule in rules}


def labels_with_rule_only_where_the_other_is_not(rule: str, other: str) -> set[str]:
    """Every label of a ``hold`` or ``hold_start`` call that names *rule* and not *other*, one call at a time."""
    return {label for (_where, label), rules in _call_sites().items() if rule in rules and other not in rules}


def endpoints_with_rule(rule: str) -> set[str]:
    """Every label whose ``hold`` or ``hold_start`` names *rule*.

    A label is not filtered against the endpoints: one that names no endpoint
    lands in the set and makes an equality with an endpoint list fail.
    """
    return {label for label, rules in _held_rules().items() if rule in rules}


# The attribute names a direct check of either rule is read through, outside
# ``ConflictRuleSet``: the migration service's own reader and the update
# install's, under the names the services that hold them give them.
_DIRECT_MIGRATION_CHECKS = frozenset({"is_retrodeck_migration_pending", "_is_retrodeck_migration_pending"})
_DIRECT_UPDATE_CHECKS = frozenset({"is_update_in_progress", "_is_update_in_progress", "_update_in_progress"})


def functions_checking_migration_without_update() -> set[str]:
    """Every function under ``backend/services/`` that reads the migration check directly and not the update one.

    Read by attribute name alone, called or handed on: ``<x>._is_retrodeck_migration_pending()`` and
    ``run_in_executor(None, <x>._is_retrodeck_migration_pending)`` both count. A nested function is its own
    function. Not seen: a check reached under another name, through a local alias, or through a helper.
    """
    return {where for where, names in _direct_checks().items() if not names & _DIRECT_UPDATE_CHECKS}


def functions_checking_migration() -> set[str]:
    """Every function the read above sees reading the migration check directly, the update one or not, by name."""
    return {where.split(" ", 1)[1] for where in _direct_checks()}


def _direct_checks() -> dict[str, set[str]]:
    """``<file>:<line> <function>`` → every attribute name it reads, for each function reading the migration check."""
    found: dict[str, set[str]] = {}
    for path in sorted(_SERVICES.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names = {
                inner.attr
                for inner in ast.walk(node)
                if isinstance(inner, ast.Attribute) and _enclosing_function(node, inner)
            }
            if names & _DIRECT_MIGRATION_CHECKS:
                found[f"{path.relative_to(_SERVICES.parent)}:{node.lineno} {node.name}"] = names
    return found


def _enclosing_function(function: ast.FunctionDef | ast.AsyncFunctionDef, target: ast.AST) -> bool:
    """Whether *target* sits in *function* itself rather than in a function nested inside it."""
    return any(_contains_outside_nested(child, target) for child in ast.iter_child_nodes(function))


def _contains_outside_nested(node: ast.AST, target: ast.AST) -> bool:
    if node is target:
        return True
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    return any(_contains_outside_nested(child, target) for child in ast.iter_child_nodes(node))
