"""The backend's half of the Steam-internals list holds what the backend spells in Steam, both ways.

What the sweep reads is ``docs/architecture/frontend-bundles.md``'s, "Every Steam
internal Tender reaches"; what it cannot see is the invariant register's entry in
``docs/architecture/invariants/startup.md``.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

from domain.steam_internals import STEAM_INTERNALS

_BACKEND = Path(__file__).resolve().parents[2] / "backend"

# The spellings that make a string one about Steam's client.
_NAMES_STEAM = re.compile(
    r"SteamClient|SteamUIStore|appStore|collectionStore|webpackChunk|steam://|steamloopback|SharedJSContext"
)

_LOGGING_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})

# What a listed expression reads that is JavaScript's own rather than Steam's.
_JAVASCRIPTS_OWN = {
    "host.inject.recovery.RUNNING_APPS_EXPRESSION": frozenset({"isArray", "iterator", "includes", "push"}),
}


def _parts(path: str) -> list[str]:
    return [part for part in re.split(r"[./:()\[\]]", path) if part]


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _logged(tree: ast.AST) -> set[int]:
    """The ids of every node inside a logging call's arguments: a message about Steam reaches no Steam."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _LOGGING_METHODS:
            for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
                found.update(id(inner) for inner in ast.walk(argument))
    return found


def _module_constants(tree: ast.Module, module: str) -> dict[int, str]:
    """Each node inside the value of a module-level assignment to one name, mapped to ``<module>.<name>``."""
    owners: dict[int, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            target, value = statement.target, statement.value
        else:
            continue
        if isinstance(target, ast.Name):
            owners.update((id(inner), f"{module}.{target.id}") for inner in ast.walk(value))
    return owners


def _strings_naming_steam(root: Path) -> list[tuple[str, str | None]]:
    """Every string under *root* that names Steam, outside docstrings and logging calls, with the constant holding it.

    The vendored trees are not ours to list, and the list spells what it names.
    A string outside a module-level assignment to one name is answered with
    ``None``.
    """
    found: list[tuple[str, str | None]] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts[0] in {"_vendor", "native"} or relative == Path("domain/steam_internals.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skipped = _docstrings(tree) | _logged(tree)
        owners = _module_constants(tree, ".".join(relative.with_suffix("").parts))
        found.extend(
            (f"{relative}:{node.lineno}", owners.get(id(node)))
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _NAMES_STEAM.search(node.value)
            and id(node) not in skipped
        )
    return found


def _value(locator: str) -> str:
    module, _, name = locator.rpartition(".")
    value = getattr(importlib.import_module(module), name)
    assert isinstance(value, str), locator
    return value


_LISTED = {locator for entry in STEAM_INTERNALS for locator in entry.spelled_in}


class TestTheList:
    def test_names_each_internal_once(self) -> None:
        paths = [entry.path for entry in STEAM_INTERNALS]
        assert len(paths) == len(set(paths))

    def test_every_string_naming_steam_is_a_constant_it_names(self) -> None:
        found = _strings_naming_steam(_BACKEND)

        assert len(found) >= len(_LISTED)
        assert [where for where, constant in found if constant not in _LISTED] == []

    def test_the_sweep_sees_a_string_outside_the_list(self, tmp_path: Path) -> None:
        # The control: a sweep that never matched would pass the test above too.
        (tmp_path / "host").mkdir()
        (tmp_path / "host" / "probe.py").write_text(
            '"""Reads ``SteamClient.Apps``."""\n'
            'PROBE = "SteamClient.Apps.RunGame()"\n'
            'logger.warning("SteamClient threw")\n'
            'evaluate("appStore.allApps")\n',
            encoding="utf-8",
        )

        assert _strings_naming_steam(tmp_path) == [("host/probe.py:2", "host.probe.PROBE"), ("host/probe.py:4", None)]

    def test_every_constant_it_names_spells_each_of_its_internals(self) -> None:
        missing = [
            f"{entry.path}: {part!r} in {locator}"
            for entry in STEAM_INTERNALS
            for locator in entry.spelled_in
            for part in _parts(entry.path)
            if part not in _value(locator)
        ]

        assert missing == []

    def test_every_name_a_listed_expression_reads_is_listed(self) -> None:
        unlisted: list[str] = []
        for locator in sorted(_LISTED):
            value = _value(locator)
            reads = re.findall(r"\.\s*([A-Za-z_$][\w$]*)", value) + re.findall(r'\[\s*"([^"]+)"\s*\]', value)
            named = {part for entry in STEAM_INTERNALS if locator in entry.spelled_in for part in _parts(entry.path)}
            allowed = named | _JAVASCRIPTS_OWN.get(locator, frozenset())
            unlisted += [f"{locator}: {read}" for read in reads if read not in allowed]

        assert unlisted == []

    def test_carries_no_javascript_name_for_a_constant_it_does_not_name(self) -> None:
        assert set(_JAVASCRIPTS_OWN) <= _LISTED
