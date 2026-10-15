#!/usr/bin/env python3
"""Background-task creation gate.

The event loop keeps only a weak reference to a task, so a task nothing holds
can be collected before it ends, and a task's exception is looked at by nobody.
``BackgroundTasks`` (``backend/lib/background_tasks.py``) holds every task it
starts until it ends, logs one that fails, and cancels and awaits what it still
holds at its owner's shutdown. A task is therefore created only inside that
module.

This check fails on a call that creates a task anywhere else under
``backend/``:

- a call named ``create_task`` — ``loop.create_task(...)``,
  ``asyncio.create_task(...)``, a ``TaskGroup``'s, or a bare ``create_task(...)``;
- a call named ``ensure_future``, in the same shapes;
- ``asyncio.Task(...)``, and a bare ``Task(...)`` in a module that imports
  ``Task`` from ``asyncio``.

Exempt: the helper's own module, ``backend/host/`` (it uses the standard
library only, and its call tasks end with their connection),
``backend/_vendor/`` and ``backend/native/``. There is no pragma.

It is an AST call-name check, NOT dataflow analysis. It does not see a task
created under another name: an alias (``ct = loop.create_task``,
``from asyncio import create_task as start``, ``from asyncio import Task as T``),
a ``getattr(loop, "create_task")``, a ``Task`` reached through another module
(``import asyncio as aio``; ``aio.Task(...)``), or anything reached through a
helper that creates the task for its caller. A task that ``asyncio.shield``,
``asyncio.gather`` or ``asyncio.wait_for`` creates from a bare coroutine is not
a call it looks for either.

Exit 0 when no module creates a task outside the helper, 1 (one line per
offending call) otherwise.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

# The one module allowed to create a task.
HELPER = BACKEND_DIR / "lib" / "background_tasks.py"

# Trees outside the rule, relative to BACKEND_DIR.
EXEMPT_DIRS = ("host", "_vendor", "native")

# Calls that create a task, by the name they are called under.
TASK_CREATING_NAMES = frozenset({"create_task", "ensure_future"})


def _iter_scanned_files(backend_dir: Path, helper: Path) -> list[Path]:
    """Every ``.py`` under the backend except the helper and the exempt trees."""
    helper_resolved = helper.resolve()
    exempt = tuple(backend_dir / name for name in EXEMPT_DIRS)
    return sorted(
        path
        for path in backend_dir.rglob("*.py")
        if path.resolve() != helper_resolved and not any(path.is_relative_to(root) for root in exempt)
    )


def _imports_task_from_asyncio(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "asyncio"
        and any(alias.name == "Task" and alias.asname is None for alias in node.names)
        for node in ast.walk(tree)
    )


def _creates_a_task(call: ast.Call, *, bare_task_is_asyncio: bool) -> str | None:
    """The name *call* creates a task under, or ``None``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr in TASK_CREATING_NAMES:
            return func.attr
        if func.attr == "Task" and isinstance(func.value, ast.Name) and func.value.id == "asyncio":
            return "asyncio.Task"
    if isinstance(func, ast.Name):
        if func.id in TASK_CREATING_NAMES:
            return func.id
        if func.id == "Task" and bare_task_is_asyncio:
            return "Task"
    return None


def find_violations(files: list[Path] | None = None) -> list[str]:
    """Return one human-readable line per call that creates a task outside the helper."""
    if files is None:
        files = _iter_scanned_files(BACKEND_DIR, HELPER)
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
        rel = path.relative_to(REPO_ROOT)
        bare_task_is_asyncio = _imports_task_from_asyncio(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _creates_a_task(node, bare_task_is_asyncio=bare_task_is_asyncio)
            if name is not None:
                findings.append(
                    f"{rel}:{node.lineno}:{node.col_offset} '{name}(...)' creates a task outside "
                    "lib/background_tasks.py — start it with BackgroundTasks.spawn."
                )
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
            "ERROR: a task is created only by BackgroundTasks.spawn (backend/lib/background_tasks.py), "
            "which holds it until it ends, logs it if it fails and cancels it at its owner's shutdown — "
            "start it there and keep the task it answers if you need a handle (CLAUDE.md → Invariant register)."
        )
        return 1
    print("OK: every task under backend/ outside host/ is created by BackgroundTasks.spawn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
