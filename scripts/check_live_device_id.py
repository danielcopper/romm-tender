#!/usr/bin/env python3
"""The live-device-id gate: a ``LiveDeviceId`` is built only by the entry that registers the device.

Every save operation takes the device id it sends to RomM from
``SyncEngine.live_device_id``, which registers or heals the device first, and
``RommSaveApi.list_saves`` and the sync logic take a ``LiveDeviceId`` so the
type checker refuses a stored id or ``None``. That refusal is only as good as
the type's origin: one ``LiveDeviceId(stored_id)`` anywhere else turns a stored,
possibly dead id into one the type checker trusts, and nothing fails — the
operation runs, and RomM answers it with no sync rows for this device.

It enforces a **structural** rule over every module under ``backend/`` but
``_vendor/`` and ``native/``: a call to ``LiveDeviceId(...)``, or
``typing.cast`` naming ``LiveDeviceId`` (by name or as a string), may appear
only in the body of ``SyncEngine.live_device_id`` in
``backend/services/saves/sync_engine/engine.py``; and ``LiveDeviceId`` is never
imported under another name. It is an AST check over call sites, NOT dataflow
analysis. It does not see: the name bound to a variable and called through it
(``make = LiveDeviceId``), a call reached through ``getattr``, a function that
builds one and is defined inside the entry itself, a ``# type: ignore`` or
``# pyright: ignore`` that hands a plain ``str`` to a parameter typed
``LiveDeviceId``, and ``tests/``, where building one is how a test hands an id
to the code under test.

Exit 0 when the rule holds, 1 (one line per offending site) otherwise.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

# Trees outside the rule, relative to BACKEND_DIR.
EXEMPT_DIRS = ("_vendor", "native")

# The one place a LiveDeviceId is built. The exemption is bound to the method of
# that class in that module, not to the bare name: a helper called
# ``live_device_id`` anywhere else would otherwise host its own construction.
ENTRY_MODULE = BACKEND_DIR / "services" / "saves" / "sync_engine" / "engine.py"
ENTRY_CLASS = "SyncEngine"
ENTRY_METHOD = "live_device_id"

TYPE_NAME = "LiveDeviceId"


def _iter_scanned_files(backend_dir: Path) -> list[Path]:
    """Every ``.py`` under the backend outside the exempt trees."""
    exempt = tuple(backend_dir / name for name in EXEMPT_DIRS)
    return sorted(path for path in backend_dir.rglob("*.py") if not any(path.is_relative_to(root) for root in exempt))


def _names_the_type(node: ast.expr) -> bool:
    """Whether *node* is ``LiveDeviceId``, ``<module>.LiveDeviceId`` or the string ``"LiveDeviceId"``."""
    if isinstance(node, ast.Name):
        return node.id == TYPE_NAME
    if isinstance(node, ast.Attribute):
        return node.attr == TYPE_NAME
    return isinstance(node, ast.Constant) and node.value == TYPE_NAME


def _is_cast(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "cast"
    return isinstance(func, ast.Attribute) and func.attr == "cast"


def _builds_the_type(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Name | ast.Attribute) and _names_the_type(call.func):
        return True
    return _is_cast(call.func) and bool(call.args) and _names_the_type(call.args[0])


def _entry(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The entry method itself — a direct method of the entry class, or nothing."""
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name != ENTRY_CLASS:
            continue
        for node in cls.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == ENTRY_METHOD:
                return node
    return None


def _check_tree(tree: ast.Module, *, allowed: set[int], rel: Path) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _builds_the_type(node) and id(node) not in allowed:
            findings.append(
                f"{rel}:{node.lineno}:{node.col_offset} builds a {TYPE_NAME} outside "
                f"{ENTRY_CLASS}.{ENTRY_METHOD}() — take the id from that entry, which registers or heals "
                "the device first."
            )
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] == TYPE_NAME and alias.asname not in (None, TYPE_NAME):
                    findings.append(
                        f"{rel}:{node.lineno}:{node.col_offset} imports {TYPE_NAME} as {alias.asname} — "
                        f"a renamed import hides a construction from this check."
                    )
    return findings


def find_violations(files: list[Path] | None = None) -> list[str]:
    """Return one human-readable line per construction of the type outside the entry."""
    if files is None:
        files = _iter_scanned_files(BACKEND_DIR)
    entry_module = ENTRY_MODULE.resolve()
    findings: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed: set[int] = set()
        if path.resolve() == entry_module:
            entry = _entry(tree)
            if entry is not None:
                allowed = {id(node) for node in ast.walk(entry) if isinstance(node, ast.Call)}
        findings.extend(_check_tree(tree, allowed=allowed, rel=path.relative_to(REPO_ROOT)))
    return sorted(findings)


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
            f"ERROR: a {TYPE_NAME} is built only by {ENTRY_CLASS}.{ENTRY_METHOD}() "
            "(backend/services/saves/sync_engine/engine.py), the one entry that registers or heals the device "
            "before a save operation reaches RomM (CLAUDE.md → Invariant register)."
        )
        return 1
    print(f"OK: {TYPE_NAME} is built only by {ENTRY_CLASS}.{ENTRY_METHOD}().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
