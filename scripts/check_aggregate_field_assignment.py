#!/usr/bin/env python3
"""Aggregate field-assignment ban — Cosmic Python enforcement.

An aggregate root — a class in ``backend/domain/`` decorated with
``@cosmic_aggregate`` — changes its state only through its own verb-named
methods. Code outside them must not assign, delete or mutate a field
directly, or it bypasses the invariants those methods keep.

The check reads every ``@cosmic_aggregate`` class under ``backend/domain/``
(the decorator by its bare name or as an attribute ending in it) and collects
its fields: the names its own class body annotates. A field annotated
``list``, ``dict`` or ``set`` — bare, subscripted, or inside a ``| None``
union — is a container field. It then scans every ``.py`` under ``backend/``
but ``_vendor/`` and ``native/``, ``domain/`` included, and fails on:

- a store or ``del`` on ``<receiver>.<field>`` — assignment, augmented
  assignment, an annotated assignment with a value, a ``for`` or ``with``
  target — for any receiver but bare ``self``;
- ``setattr`` / ``delattr`` — builtin, a class-qualified ``__setattr__`` /
  ``__delattr__`` (``object.__setattr__(rom, ...)``,
  ``type(rom).__setattr__(rom, ...)``) or a bound one
  (``rom.__setattr__(...)``) — whose field name is a string literal, on any
  receiver but bare ``self``;
- an item store, item ``del``, attribute store or mutating method call
  (``append``, ``update``, ``pop``, …: ``MUTATING_METHODS``) reached through
  ``<receiver>.<container field>``, for any receiver but bare ``self``.

It matches by field NAME, not by type: it cannot know what a receiver holds,
so a finding names the aggregates that declare the field. A non-aggregate
value whose attribute shares a field name takes a trailing comment on the
flagged line::

    saves[index].id = save_id  # pragma: no aggregate-check

What it does not see is listed in the invariant register's entry for this
rule (``CLAUDE.md``), the one home of that list.

Exit 0 on no findings, exit 1 if any findings (one line per finding).
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DOMAIN_DIR = BACKEND_DIR / "domain"
DECORATOR_NAME = "cosmic_aggregate"
ESCAPE_HATCH = "pragma: no aggregate-check"

# Upstream copies, relative to BACKEND_DIR.
EXEMPT_DIRS = ("_vendor", "native")

CONTAINER_TYPES = frozenset({"list", "dict", "set"})

# Every method of list, dict and set that changes the container in place.
MUTATING_METHODS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "remove",
        "clear",
        "sort",
        "reverse",
        "update",
        "setdefault",
        "popitem",
        "add",
        "discard",
        "difference_update",
        "intersection_update",
        "symmetric_difference_update",
    }
)


@dataclass(frozen=True)
class AggregateFields:
    """The aggregates found, and which of them declare each field name."""

    classes: frozenset[str]
    owners: dict[str, frozenset[str]]
    containers: frozenset[str]


def _decorator_matches(node: ast.expr) -> bool:
    """Return True when ``node`` is ``cosmic_aggregate`` or ``...cosmic_aggregate``."""
    if isinstance(node, ast.Name):
        return node.id == DECORATOR_NAME
    if isinstance(node, ast.Attribute):
        return node.attr == DECORATOR_NAME
    if isinstance(node, ast.Call):
        return _decorator_matches(node.func)
    return False


def _is_container_annotation(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return False
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _is_container_annotation(annotation.left) or _is_container_annotation(annotation.right)
    if isinstance(annotation, ast.Subscript):
        annotation = annotation.value
    return isinstance(annotation, ast.Name) and annotation.id in CONTAINER_TYPES


def collect_aggregate_fields(domain_dir: Path) -> AggregateFields:
    """Walk ``domain_dir`` and return every ``@cosmic_aggregate`` class with its field names."""
    classes: set[str] = set()
    owners: dict[str, set[str]] = {}
    containers: set[str] = set()
    paths = sorted(domain_dir.rglob("*.py")) if domain_dir.is_dir() else []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ClassDef) and any(_decorator_matches(d) for d in node.decorator_list)):
                continue
            classes.add(node.name)
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    owners.setdefault(statement.target.id, set()).add(node.name)
                    if _is_container_annotation(statement.annotation):
                        containers.add(statement.target.id)
    return AggregateFields(
        classes=frozenset(classes),
        owners={name: frozenset(owning) for name, owning in owners.items()},
        containers=frozenset(containers),
    )


def _is_bare_self(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "self"


def _container_in_chain(node: ast.expr, containers: frozenset[str]) -> ast.Attribute | None:
    """The first ``<receiver>.<container field>`` in ``node``'s attribute/subscript chain, receiver not bare self."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Attribute) and node.attr in containers and not _is_bare_self(node.value):
            return node
        node = node.value
    return None


def _is_str_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _set_or_del_attr(call: ast.Call) -> tuple[str, ast.expr, ast.expr] | None:
    """``(verb, receiver, name)`` for a ``setattr``/``delattr`` shape, or None.

    A ``__setattr__`` / ``__delattr__`` whose first argument is a string is
    bound (``rom.__setattr__("name", v)``); otherwise it is class-qualified
    (``Rom.__setattr__(rom, "name", v)``) and the receiver is that argument.
    """
    func = call.func
    if isinstance(func, ast.Name) and func.id in {"setattr", "delattr"} and len(call.args) >= 2:
        return func.id, call.args[0], call.args[1]
    if isinstance(func, ast.Attribute) and func.attr in {"__setattr__", "__delattr__"} and call.args:
        verb = func.attr.strip("_")
        if _is_str_literal(call.args[0]):
            return verb, func.value, call.args[0]
        if len(call.args) >= 2:
            return verb, call.args[0], call.args[1]
    return None


def _owners_text(owners: frozenset[str]) -> str:
    return ", ".join(sorted(owners))


def _file_findings(tree: ast.Module, fields: AggregateFields) -> list[tuple[int, int, str]]:
    """``(line, col, message)`` for every flagged node in ``tree``."""
    bare_annotations = {
        id(node.target) for node in ast.walk(tree) if isinstance(node, ast.AnnAssign) and node.value is None
    }
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if id(node) in bare_annotations:
                continue
            verb = "deleted" if isinstance(node.ctx, ast.Del) else "assigned"
            if isinstance(node, ast.Attribute) and node.attr in fields.owners and not _is_bare_self(node.value):
                found.append(
                    (
                        node.lineno,
                        node.col_offset,
                        f"'{ast.unparse(node)}' is {verb} — '{node.attr}' is a field name of "
                        f"{_owners_text(fields.owners[node.attr])}",
                    )
                )
                continue
            container = _container_in_chain(node.value, fields.containers)
            if container is not None:
                found.append(
                    (
                        node.lineno,
                        node.col_offset,
                        f"'{ast.unparse(node)}' is {verb} through '{container.attr}', a container field of "
                        f"{_owners_text(fields.owners[container.attr])}",
                    )
                )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in MUTATING_METHODS:
                container = _container_in_chain(func.value, fields.containers)
                if container is not None:
                    found.append(
                        (
                            node.lineno,
                            node.col_offset,
                            f"'{ast.unparse(func)}(...)' mutates '{container.attr}', a container field of "
                            f"{_owners_text(fields.owners[container.attr])}",
                        )
                    )
                    continue
            shape = _set_or_del_attr(node)
            if shape is None:
                continue
            verb, receiver, name = shape
            if (
                isinstance(name, ast.Constant)
                and isinstance(name.value, str)
                and name.value in fields.owners
                and not _is_bare_self(receiver)
            ):
                found.append(
                    (
                        node.lineno,
                        node.col_offset,
                        f"'{verb}({ast.unparse(receiver)}, {name.value!r}, ...)' — '{name.value}' is a field name of "
                        f"{_owners_text(fields.owners[name.value])}",
                    )
                )
    return sorted(found)


def _iter_scanned_files(backend_dir: Path) -> list[Path]:
    """Every ``.py`` under the backend except the exempt trees."""
    exempt = tuple(backend_dir / name for name in EXEMPT_DIRS)
    return sorted(path for path in backend_dir.rglob("*.py") if not any(path.is_relative_to(root) for root in exempt))


def find_violations(files: list[Path] | None = None, fields: AggregateFields | None = None) -> list[str]:
    """Return ``file:line:col message`` for every aggregate field mutated outside bare ``self``."""
    if fields is None:
        fields = collect_aggregate_fields(DOMAIN_DIR)
    if files is None:
        files = _iter_scanned_files(BACKEND_DIR)
    findings: list[str] = []
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        source_lines = source.splitlines()
        rel = path.relative_to(REPO_ROOT)
        findings.extend(
            f"{rel}:{line}:{col} {message}; call a verb-named method on the aggregate instead"
            for line, col, message in _file_findings(tree, fields)
            if ESCAPE_HATCH not in source_lines[line - 1]
        )
    return findings


def main(argv: list[str]) -> int:
    if any(a in {"-h", "--help"} for a in argv):
        print(__doc__)
        return 0
    findings = find_violations()
    if findings:
        for line in findings:
            print(line)
        print()
        print(
            "ERROR: aggregate roots change state only through their own verb-named methods "
            "(CLAUDE.md → Invariant register). A value that only shares a field name takes "
            f"'# {ESCAPE_HATCH}' on the flagged line."
        )
        return 1
    print(
        f"OK: no aggregate field is mutated outside bare self in {BACKEND_DIR.relative_to(REPO_ROOT)}/ "
        f"({' and '.join(f'{name}/' for name in EXEMPT_DIRS)} not scanned)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
