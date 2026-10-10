#!/usr/bin/env python3
"""Failure-shape dialect gate — the wire's failure shape is built only at the entrypoint.

An endpoint that fails answers ``{"success": False, "reason": str, "message":
str}`` with any details beside them (``.claude/rules/endpoints.md`` owns the
shape). ``backend/main.py`` is the one place that builds it: a use case raises
its refusal (``lib.errors.Refused``, or ``domain.refusal.DomainRefused`` from a
domain rule) or returns a ``lib.partial_failure.PartialFailure`` for work that
stopped partway, a RomM error stays a raised ``RommApiError``, and the class
decorator that wraps every endpoint on ``main.Endpoints`` turns each into the
shape — a RomM error through ``lib.errors.classify_error``. A failure dict built
anywhere else is a second dialect of it.

The rule
========

Every ``*.py`` under ``backend/`` is read except the entrypoint
``backend/main.py`` and the upstream copies under ``backend/_vendor/`` and
``backend/native/``. It fails on:

* a dict literal whose ``success`` key is a literal falsy constant (``False``,
  ``0``, ``None``), whatever other keys it carries and wherever it stands — a
  return, an assignment, an argument, an event payload;
* a call to ``error_response(...)``, by its bare name or as an attribute;
* a ``**spread`` of a call to a refusal helper — a callee whose name contains
  ``refusal`` or ``failure``;
* a file that does not parse, since nothing in it could be checked.

A result that never reaches the wire but has a ``success`` field of its own is
built through its type — ``MutationOutcome(success=False, …)`` — which is a
call, not a dict literal, and passes.

What it does not see
====================

* A ``success`` that is computed rather than a literal: the save-sync results'
  ``"success": len(errors) == 0`` and the removed-game cleanup's
  ``prune_complete`` event, whose ``success`` is a variable.
* A failure without a ``success`` key: a status union's ``status`` (the save
  version history), a partial-success flag (``server_query_failed``,
  ``recommended_action``), and the host's ``{type, reason, message}`` error
  frame — which transport failure is not an endpoint's is
  ``.claude/rules/host.md``'s rule.
* A failure dict built without a literal (``dict(success=False, ...)``,
  ``answer["success"] = False``), under a key that is not a literal
  (``{SUCCESS_KEY: False}``), or by a helper the call goes through; a spread
  of a variable holding a refusal, and a refusal helper named any other way.

Every run enforces; ``--check``, which ``mise run lint`` and CI pass, changes
nothing.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

# Paths relative to the backend directory: the one file that builds the shape,
# and the upstream copies, which are not ours to change.
ENTRYPOINT = Path("main.py")
UPSTREAM_DIRS = frozenset({"_vendor", "native"})

SUCCESS_KEY = "success"
ERROR_RESPONSE = "error_response"
REFUSAL_HELPER_MARKERS = ("refusal", "failure")


@dataclass(frozen=True)
class Finding:
    """One place outside the entrypoint that builds the wire's failure shape."""

    path: Path
    lineno: int
    detail: str

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(REPO_ROOT))

    def render(self) -> str:
        return f"{self.rel}:{self.lineno}  {self.detail}"


def _is_falsy_constant(value: ast.expr) -> bool:
    return isinstance(value, ast.Constant) and value.value in (False, 0, None)


def _has_falsy_success_entry(node: ast.Dict) -> bool:
    """True when the dict literal binds ``"success"`` to a literal falsy value."""
    return any(
        isinstance(key, ast.Constant) and key.value == SUCCESS_KEY and _is_falsy_constant(value)
        for key, value in zip(node.keys, node.values, strict=True)
    )


def _callee_name(call: ast.Call) -> str | None:
    """The name a call is made through — ``f(...)`` or ``x.f(...)`` — or None."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_refusal_helper(call: ast.Call) -> bool:
    name = _callee_name(call)
    return name is not None and any(marker in name for marker in REFUSAL_HELPER_MARKERS)


def _findings_in(path: Path, node: ast.AST) -> list[Finding]:
    """What *node* itself does that only the entrypoint may."""
    found: list[Finding] = []
    if isinstance(node, ast.Dict):
        if _has_falsy_success_entry(node):
            found.append(Finding(path, node.lineno, "a failure dict literal"))
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None and isinstance(value, ast.Call) and _is_refusal_helper(value):
                found.append(Finding(path, value.lineno, f"a spread of {_callee_name(value)}()"))
    elif isinstance(node, ast.Call) and _callee_name(node) == ERROR_RESPONSE:
        found.append(Finding(path, node.lineno, f"a call to {ERROR_RESPONSE}()"))
    return found


def scan_file(path: Path) -> list[Finding]:
    """Every way the module at *path* builds the failure shape."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [Finding(path, exc.lineno or 0, "does not parse, so nothing in it could be checked")]
    found: list[Finding] = []
    for node in ast.walk(tree):
        found.extend(_findings_in(path, node))
    return sorted(found, key=lambda finding: finding.lineno)


def _in_scope(backend_dir: Path, path: Path) -> bool:
    rel = path.relative_to(backend_dir)
    return rel != ENTRYPOINT and rel.parts[0] not in UPSTREAM_DIRS


def collect_findings(backend_dir: Path) -> list[Finding]:
    """Scan every module under *backend_dir* but the entrypoint and the upstream copies."""
    found: list[Finding] = []
    for path in sorted(backend_dir.rglob("*.py")):
        if _in_scope(backend_dir, path):
            found.extend(scan_file(path))
    return found


def main(argv: list[str]) -> int:
    if any(a in {"-h", "--help"} for a in argv):
        print(__doc__)
        return 0

    found = collect_findings(BACKEND_DIR)
    if found:
        print(f"=== FAILURE SHAPE BUILT OUTSIDE THE ENTRYPOINT ({len(found)}) ===")
        for finding in found:
            print(f"  {finding.render()}")
        print()
        print(
            "ERROR: only backend/main.py builds the failure shape — a use case raises its refusal "
            "(lib.errors.Refused / domain.refusal.DomainRefused) or returns a "
            "lib.partial_failure.PartialFailure for partial work, and a result that never reaches the "
            "wire is built through its own type."
        )
        return 1
    rel = BACKEND_DIR.relative_to(REPO_ROOT)
    print(f"OK: no failure shape built in {rel}/ outside {rel / ENTRYPOINT}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
