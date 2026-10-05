#!/usr/bin/env python3
"""Failure-shape dialect gate — canonical-response enforcement.

Endpoints that return a plain ``dict`` and can fail use the canonical
failure shape ``{"success": False, "reason": ErrorCode | str, "message": str}``
(plus per-endpoint payload extras). The convention — documented in
``backend/lib/list_result.py`` and ``.claude/rules/endpoints.md`` → "Endpoint
response shapes" — forbids a second ``error`` field and the legacy
``error_code`` key.

This check walks ``backend/services/`` and classifies every failure-shaped
``return`` against a **required-key rule**: a failure shape must carry both
``reason`` and ``message`` and must NOT carry ``error`` or ``error_code``.

Classification of each failure-shaped return (a ``return`` of a dict literal
with a falsy ``success`` entry):

  * CANONICAL — has ``reason`` and ``message``, no ``error`` / ``error_code``.
    (Extra/additive payload keys are allowed.)
  * ERROR_CODE_DIALECT — carries the forbidden ``error_code`` key.
  * ERROR_KEY_DIALECT — carries the forbidden ``error`` key.
  * AD_HOC — ``success: False`` but missing ``reason`` and/or ``message``
    (an extra-keys-only or message-only stray with no routing slug).
  * CARVE_OUT_CANDIDATE — a documented carve-out shape, pattern-exempt and
    never flagged: a discriminated-status union (``status`` discriminant, no
    ``success``) or a partial-success payload (a full data payload alongside
    an additive failure flag from :data:`PARTIAL_SUCCESS_FLAGS`).

Two modes:

  * report (default) — print every site grouped by classification with a count
    summary, then exit 0. Report mode never fails; it is the inventory.
  * ``--check`` — enforce mode. Exit 1 on any ERROR_CODE_DIALECT,
    ERROR_KEY_DIALECT, or AD_HOC finding (the three collapsed dialects).
    CANONICAL and the pattern-exempt CARVE_OUT_CANDIDATE sites pass.

The dict-literal heuristic is intentionally conservative: it only inspects
``return {...}`` literals. A failure dict built across several statements
(``resp = {...}; resp["x"] = y; return resp``) or returned from a helper is not
caught — a guardrail, not a prover. A ``**spread`` or computed key hides the
full key set; such returns are flagged for manual review (AD_HOC) unless they
look like a carve-out, so the heuristic never silently passes an unknown shape.

Converted modules
-----------------

A module on :data:`CONVERTED_MODULES` raises its refusals (``lib.errors.Refused``)
and leaves the failure shape to the entrypoint, ``main.Endpoints``. In such a
module ``--check`` also fails on any dict literal with a falsy ``success``
entry, wherever it stands and whatever keys it carries, on any
``error_response(...)`` call, and on a ``**spread`` of a call to a refusal
helper (a callee whose name contains ``refusal`` or ``failure``). A listed path
that is not a file, or does not parse, fails too, so a renamed module cannot
drop off the list unnoticed.

**The list only grows.** Each conversion adds its modules and none is ever
taken off; a module that falls back to building a failure dict fails the
check instead.

What it cannot see in a listed module: a failure dict built without a literal
(``dict(success=False, ...)``, ``answer["success"] = False``), a ``success``
value that is not a literal, a dict a helper in another module builds, a
spread of a variable that holds a refusal, and a refusal helper named any
other way. Nothing outside the list is read by these rules.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = REPO_ROOT / "backend" / "services"

REASON_KEY = "reason"
MESSAGE_KEY = "message"
SUCCESS_KEY = "success"
STATUS_KEY = "status"
ERROR_CODE_KEY = "error_code"
ERROR_KEY = "error"

# Classification labels (also the report group order).
CANONICAL = "CANONICAL"
ERROR_CODE_DIALECT = "ERROR_CODE_DIALECT"
ERROR_KEY_DIALECT = "ERROR_KEY_DIALECT"
CARVE_OUT_CANDIDATE = "CARVE_OUT_CANDIDATE"
AD_HOC = "AD_HOC"

REPORT_ORDER = (
    ERROR_CODE_DIALECT,
    ERROR_KEY_DIALECT,
    AD_HOC,
    CARVE_OUT_CANDIDATE,
    CANONICAL,
)

# Findings in these classifications fail enforce mode (``--check``).
VIOLATION_CLASSES = frozenset({ERROR_CODE_DIALECT, ERROR_KEY_DIALECT, AD_HOC})

# Repo-relative paths of the modules converted to raising their refusals; the
# list only grows (module docstring, "Converted modules").
CONVERTED_MODULES: tuple[str, ...] = (
    "backend/adapters/steam_config.py",
    "backend/services/game_process.py",
)

# What the paths above are relative to — a name of its own, apart from
# ``REPO_ROOT``, which the services walk's own tests move.
CONVERTED_ROOT = REPO_ROOT

ERROR_RESPONSE = "error_response"
REFUSAL_HELPER_MARKERS = ("refusal", "failure")

# Additive failure-flag keys that mark a partial-success payload (carve-out 2).
# A return carrying one of these alongside a full payload keeps the flag.
PARTIAL_SUCCESS_FLAGS = frozenset({"server_query_failed", "recommended_action"})


@dataclass(frozen=True)
class Finding:
    """One failure-shaped return site and its classification."""

    path: Path
    lineno: int
    classification: str
    keys: tuple[str, ...]
    detail: str

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(REPO_ROOT))

    def render(self) -> str:
        key_set = "{" + ", ".join(self.keys) + "}" if self.keys else "{}"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"{self.rel}:{self.lineno}  {key_set}{suffix}"


def _is_falsy_success(value: ast.expr) -> bool:
    """Return True when *value* is a literal falsy ``success`` value (False/0/None)."""
    if isinstance(value, ast.Constant):
        return value.value in (False, 0, None)
    return False


def _literal_keys(node: ast.Dict) -> list[str] | None:
    """Return the string keys of a dict literal, or None if any key is non-constant.

    A ``**spread`` entry (key is None) or a computed key means the literal's full
    key set is unknown at parse time — return None so the caller treats it as
    unclassifiable-by-keys rather than guessing.
    """
    keys: list[str] = []
    for key in node.keys:
        if key is None:  # ``**other`` spread
            return None
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        keys.append(key.value)
    return keys


def _classify_keys(keys: list[str]) -> str:
    """Classify a failure-shaped dict literal by its key set (required-key rule).

    Precondition: the caller has already confirmed this dict is failure-shaped
    (a falsy ``success`` entry, a ``status`` discriminant, or a partial-success
    flag). Order matters: carve-outs are recognised first so they never fall
    through to the dialect/ad-hoc checks.
    """
    key_set = set(keys)

    # Carve-out 1: discriminated-status union (status discriminant, no success).
    if STATUS_KEY in key_set and SUCCESS_KEY not in key_set:
        return CARVE_OUT_CANDIDATE
    # Carve-out 2: partial-success payload (additive failure flag).
    if key_set & PARTIAL_SUCCESS_FLAGS:
        return CARVE_OUT_CANDIDATE

    # Forbidden keys — the two legacy dialects.
    if ERROR_CODE_KEY in key_set:
        return ERROR_CODE_DIALECT
    if ERROR_KEY in key_set:
        return ERROR_KEY_DIALECT

    # Required-key rule: a canonical failure carries both reason and message.
    if REASON_KEY in key_set and MESSAGE_KEY in key_set:
        return CANONICAL

    # success: False but missing reason and/or message — an ad-hoc stray.
    return AD_HOC


def _has_falsy_success_entry(node: ast.Dict) -> bool:
    """Return True when the dict literal has a ``"success"`` key with a falsy value."""
    for key, value in zip(node.keys, node.values, strict=True):
        if isinstance(key, ast.Constant) and key.value == SUCCESS_KEY and _is_falsy_success(value):
            return True
    return False


def _has_status_entry(node: ast.Dict) -> bool:
    """Return True when the dict literal has a literal ``"status"`` key."""
    return any(isinstance(key, ast.Constant) and key.value == STATUS_KEY for key in node.keys)


def _has_partial_success_flag(node: ast.Dict) -> bool:
    """Return True when the dict literal carries an additive partial-success flag."""
    return any(isinstance(key, ast.Constant) and key.value in PARTIAL_SUCCESS_FLAGS for key in node.keys)


def _finding_for_return(path: Path, node: ast.Return) -> Finding | None:
    """Classify one ``return`` statement. None = not a failure-shaped return."""
    value = node.value
    if not isinstance(value, ast.Dict):
        return None

    keys = _literal_keys(value)
    is_failure = _has_falsy_success_entry(value)
    is_status = _has_status_entry(value)
    is_partial = _has_partial_success_flag(value)

    if not (is_failure or is_status or is_partial):
        return None

    if keys is None:
        # A ``**spread`` or computed key hid the full key set. Flag it so a
        # reviewer can eyeball it; treat an obvious carve-out shape as exempt,
        # everything else as AD_HOC (an unknown shape never silently passes).
        looks_like_carve_out = (is_status and not is_failure) or is_partial
        classification = CARVE_OUT_CANDIDATE if looks_like_carve_out else AD_HOC
        return Finding(
            path=path,
            lineno=node.lineno,
            classification=classification,
            keys=("<dynamic keys>",),
            detail="dict with spread/computed keys — verify by hand",
        )

    classification = _classify_keys(keys)
    return Finding(
        path=path,
        lineno=node.lineno,
        classification=classification,
        keys=tuple(keys),
        detail="",
    )


def scan_file(path: Path) -> list[Finding]:
    """Parse *path* and return every failure-shaped return finding in it."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return):
            finding = _finding_for_return(path, node)
            if finding is not None:
                findings.append(finding)
    return findings


def collect_findings(services_dir: Path = SERVICES_DIR) -> list[Finding]:
    """Walk *services_dir* and return every failure-shape finding."""
    findings: list[Finding] = []
    if services_dir.is_dir():
        for path in sorted(services_dir.rglob("*.py")):
            findings.extend(scan_file(path))
    return findings


@dataclass(frozen=True)
class ConvertedFinding:
    """One way a converted module still builds the wire's failure shape."""

    rel: str
    lineno: int
    detail: str

    def render(self) -> str:
        return f"{self.rel}:{self.lineno}  {self.detail}"


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


def _converted_findings_in(rel: str, node: ast.AST) -> list[ConvertedFinding]:
    """What *node* itself does that a converted module may not."""
    found: list[ConvertedFinding] = []
    if isinstance(node, ast.Dict):
        if _has_falsy_success_entry(node):
            found.append(ConvertedFinding(rel, node.lineno, "a failure dict literal"))
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None and isinstance(value, ast.Call) and _is_refusal_helper(value):
                found.append(ConvertedFinding(rel, value.lineno, f"a spread of {_callee_name(value)}()"))
    elif isinstance(node, ast.Call) and _callee_name(node) == ERROR_RESPONSE:
        found.append(ConvertedFinding(rel, node.lineno, f"a call to {ERROR_RESPONSE}()"))
    return found


def scan_converted_module(root: Path, rel: str) -> list[ConvertedFinding]:
    """Every way the converted module at *root*/*rel* still builds the failure shape."""
    path = root / rel
    if not path.is_file():
        return [ConvertedFinding(rel, 0, "listed as converted but not a file — follow a rename, never drop the entry")]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [ConvertedFinding(rel, exc.lineno or 0, "does not parse, so nothing in it could be checked")]
    found: list[ConvertedFinding] = []
    for node in ast.walk(tree):
        found.extend(_converted_findings_in(rel, node))
    return sorted(found, key=lambda finding: finding.lineno)


def collect_converted_findings() -> list[ConvertedFinding]:
    """Scan every module on :data:`CONVERTED_MODULES`."""
    found: list[ConvertedFinding] = []
    for rel in CONVERTED_MODULES:
        found.extend(scan_converted_module(CONVERTED_ROOT, rel))
    return found


def _group_by_class(findings: list[Finding]) -> dict[str, list[Finding]]:
    by_class: dict[str, list[Finding]] = {label: [] for label in REPORT_ORDER}
    for finding in findings:
        by_class.setdefault(finding.classification, []).append(finding)
    return by_class


def _print_report(findings: list[Finding]) -> None:
    """Print findings grouped by classification with a count summary."""
    by_class = _group_by_class(findings)

    for label in REPORT_ORDER:
        group = by_class.get(label, [])
        print(f"=== {label} ({len(group)}) ===")
        for finding in group:
            print(f"  {finding.render()}")
        print()

    print("=== SUMMARY ===")
    for label in REPORT_ORDER:
        print(f"  {label}: {len(by_class.get(label, []))}")
    print(f"  TOTAL: {len(findings)}")


def _print_violations(findings: list[Finding]) -> None:
    """Print only the enforce-mode violations, grouped, with a fix hint."""
    violations = [f for f in findings if f.classification in VIOLATION_CLASSES]
    by_class = _group_by_class(violations)
    for label in REPORT_ORDER:
        group = by_class.get(label, [])
        if not group:
            continue
        print(f"=== {label} ({len(group)}) ===")
        for finding in group:
            print(f"  {finding.render()}")
        print()
    print(
        "ERROR: failure-shaped returns in backend/services/ must carry "
        "'reason' + 'message' and must not carry 'error' / 'error_code' "
        "(.claude/rules/endpoints.md → Endpoint response shapes; lib/list_result.py)."
    )


def _print_converted(found: list[ConvertedFinding]) -> None:
    """Print what the converted modules still build, with a fix hint."""
    print(f"=== CONVERTED_MODULE ({len(found)}) ===")
    for finding in found:
        print(f"  {finding.render()}")
    print()
    print(
        "ERROR: a converted module raises its refusal (lib.errors.Refused) and returns a "
        "lib.partial_failure.PartialFailure for partial work; main.Endpoints builds the failure shape."
    )


def main(argv: list[str]) -> int:
    if any(a in {"-h", "--help"} for a in argv):
        print(__doc__)
        return 0

    enforce = "--check" in argv
    findings = collect_findings(SERVICES_DIR)

    if enforce:
        violations = [f for f in findings if f.classification in VIOLATION_CLASSES]
        converted = collect_converted_findings()
        if violations:
            _print_violations(findings)
        if converted:
            _print_converted(converted)
        if violations or converted:
            return 1
        print(
            f"OK: no failure-shape dialect violations in {SERVICES_DIR.relative_to(REPO_ROOT)}, "
            f"and no failure shape built in the {len(CONVERTED_MODULES)} converted module(s)."
        )
        return 0

    _print_report(findings)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
