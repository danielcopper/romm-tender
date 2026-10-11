#!/usr/bin/env python3
"""Backend↔frontend wire-reason parity gate.

An endpoint's failure answer carries a ``reason`` the backend decides and the
panel branches on. The backend spells each reason where it refuses; the panel
spells the whole set once, as the ``WireReason`` union in
``frontend/src/types/reasons.ts``. Nothing ties the two together at build
time, so a reason renamed or added on one side reaches the other as a string
no branch matches. This check derives both sets from source and fails when
they differ, in either direction, naming each reason on one side only.

What it reads on the backend, by ``ast`` over ``backend/`` less ``_vendor/``
and ``native/``:

  * the reason of every construction of a refusal class that takes it as an
    argument — ``Refused``, ``DomainRefused`` and every subclass of either
    outside the named branches (e.g. ``FolderRefused`` and its subclasses):
    the first positional argument, or ``reason=``;
  * the ``reason`` class attribute of every subclass of ``NamedRefused`` and
    ``NamedDomainRefused``;
  * the reason of every construction of ``PartialFailure`` or a subclass:
    ``reason=``, or the first positional argument;
  * every reason ``classify_error`` (``lib/errors.py``) returns — the first
    element of each returned tuple.

A refusal class is found by name — imported by name, under an ``import … as``
alias, or through its module (``lib.errors.Refused``, ``errs.Refused`` after
``from lib import errors as errs``), at a call and in a class's bases alike. A
name reached through a backend module that does not define it FAILS, since
whether it is a refusal is then unknown.

A reason it reads is a string literal, a module-level string constant (in the
module, or imported by name from another backend module), a member of a
backend ``StrEnum`` (``Enum.MEMBER`` or ``Enum.MEMBER.value``), or
``<expr>.<field>.value`` where every backend class declaring ``<field>``
annotates it with the same ``StrEnum`` — which stands for every member of that
enum. A reason passed on from another refusal is accepted unread only where
``PASS_THROUGHS`` names it, by file and source text, with why its values are
already counted elsewhere. Anything else — an f-string, a call, a parameter,
``*args``, a pass-through not on that list — FAILS, and so does a
``PASS_THROUGHS`` entry no site matches any more, a named refusal class whose
chain declares no ``reason``, a refusal class taking the reason positionally
that defines its own ``__init__`` (where the reason sits is then unknown), and
a ``classify_error`` return that is not a tuple.

On the frontend it reads the one declaration ``export type WireReason = …;``
in ``frontend/src/types/reasons.ts``, comments stripped: each member is a
string literal or the name of another ``export type`` union in the same file
whose members are string literals. Any other member fails. It also holds
``RommErrorReason`` equal to ``classify_error``'s reasons, which is what that
sub-union claims to be.

What it does not see:

  * a refusal class reached under another name — an assignment alias
    (``R = Refused``), ``getattr``, a class passed as a value and called later
    (``cls(...)``);
  * another site in a ``PASS_THROUGHS`` file whose reason has the same source
    text as a listed one — it is accepted unread too;
  * a module constant shadowed by a local of the same name — the constant is
    read;
  * whether a reason the backend spells can reach the wire at all (a
    refusal caught and handled before the entrypoint is counted);
  * whether a frontend type that narrows ``WireReason`` names the reasons its
    endpoint really sends — only the union as a whole is held.

What stays out of ``WireReason``, and so out of this check: reasons inside a
refusal's details; the reasons the panel invents itself (``request_failed``,
``exception``, the launch gate's verdicts), which the site that uses one
declares beside the union; and every other vocabulary the panel reads under a
``reason`` key — transport reasons (``host/protocol.py``), status unions,
update wait reasons, emulator option reasons, prune frames.

``EXEMPT`` holds reasons deliberately kept out of the check. It is empty; a
genuine drift is a finding to triage, never an exemption.

Exit 0 when nothing above fails, 1 (one line per discrepancy) otherwise.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
UNION_FILE = REPO_ROOT / "frontend" / "src" / "types" / "reasons.ts"

UNION_NAME = "WireReason"
CLASSIFIER_UNION_NAME = "RommErrorReason"

# Reasons deliberately excluded from the parity check. Empty; add an entry only
# as a conscious decision (see the module docstring), never to silence a drift.
EXEMPT: frozenset[str] = frozenset()

# A reason passed on from another refusal, by (file under backend/, the
# expression's source text) → why its values are already counted where they
# are first spelled.
PASS_THROUGHS: dict[tuple[str, str], str] = {
    ("domain/platform_system.py", "self.unasked"): (
        "classify_error's reason for the failed RomM listing read (services/platform_systems.py), kept on the "
        "platform as a value with its message and counted in the classifier's table"
    ),
    ("services/rom_adoption/service.py", "refused.reason"): (
        "the reason of the refusal that stopped the removal, kept on the partial result that reports the saves "
        "already renamed"
    ),
}

# The roots of the hierarchies, as (module, class).
_POSITIONAL_ROOTS = frozenset({("lib.errors", "Refused"), ("domain.refusal", "DomainRefused")})
_NAMED_ROOTS = frozenset({("lib.errors", "NamedRefused"), ("domain.refusal", "NamedDomainRefused")})
_PARTIAL_ROOT = ("lib.partial_failure", "PartialFailure")
_CLASSIFIER = ("lib.errors", "classify_error")
_STR_ENUM_BASES = frozenset({"StrEnum"})

_SKIPPED_DIRS = ("_vendor", "native")


@dataclass
class _Module:
    name: str
    path: Path
    rel: str
    tree: ast.Module
    source: str
    constants: dict[str, ast.expr] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    module_imports: dict[str, str] = field(default_factory=dict)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    defined: set[str] = field(default_factory=set)


@dataclass
class BackendReasons:
    """What the backend side read: every reason, and every site it could not read."""

    reasons: set[str] = field(default_factory=set)
    classifier: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)


def _iter_backend_files(backend_dir: Path) -> list[Path]:
    return sorted(
        p for p in backend_dir.rglob("*.py") if not any(d in p.relative_to(backend_dir).parts for d in _SKIPPED_DIRS)
    )


def _module_name(backend_dir: Path, path: Path) -> str:
    parts = list(path.relative_to(backend_dir).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _load_modules(backend_dir: Path) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for path in _iter_backend_files(backend_dir):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        name = _module_name(backend_dir, path)
        module = _Module(name, path, path.relative_to(backend_dir).as_posix(), tree, source)
        is_package = path.name == "__init__.py"
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                module.constants[node.targets[0].id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                module.constants[node.target.id] = node.value
            elif isinstance(node, ast.ClassDef):
                module.classes[node.name] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                source_module = _import_source(name, is_package, node)
                for alias in node.names:
                    module.imports[alias.asname or alias.name] = (source_module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname is not None:
                        module.module_imports[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        module.module_imports[top] = top
        module.defined = _bound_names(tree.body) | set(module.imports) | set(module.module_imports)
        modules[name] = module
    return modules


def _bound_names(body: list[ast.stmt]) -> set[str]:
    """Every name *body* binds at its own level, looking into ``if`` / ``try`` / ``with`` / loops but not into defs."""
    names: set[str] = set()
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                names.add(child.id)
            elif isinstance(child, (ast.Tuple, ast.List)):
                names |= {n.id for n in ast.walk(child) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        for nested in ("body", "orelse", "finalbody", "handlers"):
            block = getattr(node, nested, None)
            if isinstance(block, list):
                names |= _bound_names([b for b in block if isinstance(b, ast.stmt)])
                for handler in block:
                    if isinstance(handler, ast.ExceptHandler):
                        names |= _bound_names(handler.body)
    return names


def _import_source(module_name: str, is_package: bool, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = module_name.split(".") if is_package else module_name.split(".")[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


class _Resolver:
    """Reads names and reason expressions across the backend's modules."""

    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules
        self.packages = {".".join(m.split(".")[:i]) for m in modules for i in range(1, m.count(".") + 1)}
        self.unresolved: list[tuple[str, ast.Attribute, str]] = []
        self.class_index: dict[tuple[str, str], ast.ClassDef] = {
            (m.name, cname): cdef for m in modules.values() for cname, cdef in m.classes.items()
        }
        self.enums = {key: cdef for key, cdef in self.class_index.items() if self._is_str_enum(key)}
        self.field_enums = self._field_enums()

    def symbol(self, module: str, name: str, depth: int = 0) -> tuple[str, str] | None:
        """The (module, name) a name in *module* is defined under, following imports."""
        mod = self.modules.get(module)
        if mod is None or depth > 10:
            return None
        if name in mod.classes or name in mod.constants:
            return (module, name)
        if name in mod.imports:
            target_module, target_name = mod.imports[name]
            return self.symbol(target_module, target_name, depth + 1)
        return None

    def module_of(self, module: str, expr: ast.expr) -> str | None:
        """The backend module or package *expr* in *module* names (``lib``, ``lib.errors``, an alias of one)."""
        if isinstance(expr, ast.Name):
            mod = self.modules.get(module)
            if mod is None:
                return None
            if expr.id in mod.module_imports:
                candidate = mod.module_imports[expr.id]
            elif expr.id in mod.imports:
                source, name = mod.imports[expr.id]
                candidate = f"{source}.{name}" if source else name
            else:
                return None
        elif isinstance(expr, ast.Attribute):
            base = self.module_of(module, expr.value)
            if base is None:
                return None
            candidate = f"{base}.{expr.attr}"
        else:
            return None
        return candidate if candidate in self.modules or candidate in self.packages else None

    def class_of(self, module: str, expr: ast.expr) -> tuple[str, str] | None:
        if isinstance(expr, ast.Name):
            key = self.symbol(module, expr.id)
        elif isinstance(expr, ast.Attribute):
            owner = self.module_of(module, expr.value)
            if owner is None:
                key = self.symbol(module, expr.attr)
            elif owner in self.modules:
                key = self.symbol(owner, expr.attr)
                if expr.attr not in self.modules[owner].defined and self.module_of(module, expr) is None:
                    self.unresolved.append((module, expr, owner))
            else:
                key = None
        else:
            return None
        return key if key is not None and key in self.class_index else None

    def ancestors(self, key: tuple[str, str]) -> set[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        stack = [key]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            cdef = self.class_index.get(current)
            if cdef is None:
                continue
            for base in cdef.bases:
                base_key = self.class_of(current[0], base)
                if base_key is not None:
                    stack.append(base_key)
        return seen

    def _is_str_enum(self, key: tuple[str, str]) -> bool:
        cdef = self.class_index[key]
        return any(
            (isinstance(b, ast.Name) and b.id in _STR_ENUM_BASES)
            or (isinstance(b, ast.Attribute) and b.attr in _STR_ENUM_BASES)
            for b in cdef.bases
        )

    def enum_members(self, key: tuple[str, str]) -> dict[str, str]:
        members: dict[str, str] = {}
        for node in self.enums[key].body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                members[node.targets[0].id] = node.value.value
        return members

    def _field_enums(self) -> dict[str, set[tuple[str, str] | None]]:
        """Every annotated class field, by name → the StrEnum each declaration names (None: something else)."""
        fields: dict[str, set[tuple[str, str] | None]] = {}
        for (module, _cname), cdef in self.class_index.items():
            for node in cdef.body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    key = self.class_of(module, node.annotation)
                    fields.setdefault(node.target.id, set()).add(key if key in self.enums else None)
        return fields

    def resolve(self, module: str, expr: ast.expr, depth: int = 0) -> set[str] | None:
        """The reason values *expr* in *module* can be, or None where it cannot be read."""
        if depth > 10:
            return None
        if isinstance(expr, ast.Constant):
            return {expr.value} if isinstance(expr.value, str) else None
        if isinstance(expr, ast.Name):
            key = self.symbol(module, expr.id)
            if key is None or key[1] not in self.modules[key[0]].constants:
                return None
            return self.resolve(key[0], self.modules[key[0]].constants[key[1]], depth + 1)
        if isinstance(expr, ast.Attribute):
            if expr.attr == "value" and isinstance(expr.value, ast.Attribute):
                inner = expr.value
                member = self._enum_member(module, inner)
                if member is not None:
                    return {member}
                return self._field_enum_values(inner.attr)
            member = self._enum_member(module, expr)
            return {member} if member is not None else None
        return None

    def _enum_member(self, module: str, expr: ast.Attribute) -> str | None:
        if not isinstance(expr.value, ast.Name):
            return None
        key = self.symbol(module, expr.value.id)
        if key is None or key not in self.enums:
            return None
        return self.enum_members(key).get(expr.attr)

    def _field_enum_values(self, field_name: str) -> set[str] | None:
        declared = self.field_enums.get(field_name, set())
        if len(declared) != 1:
            return None
        (key,) = declared
        if key is None:
            return None
        return set(self.enum_members(key).values())


def _reason_argument(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "reason":
            return keyword.value
    if call.args and not isinstance(call.args[0], ast.Starred):
        return call.args[0]
    return None


def _segment(module: _Module, expr: ast.expr) -> str:
    return ast.get_source_segment(module.source, expr) or ast.dump(expr)


def parse_backend_reasons() -> BackendReasons:
    """Read every wire reason under ``BACKEND_DIR``, and every site it could not read."""
    pass_throughs = PASS_THROUGHS
    modules = _load_modules(BACKEND_DIR)
    resolver = _Resolver(modules)
    found = BackendReasons()
    matched_pass_throughs: set[tuple[str, str]] = set()

    def read(module: _Module, expr: ast.expr | None, where: str, line: int) -> set[str]:
        if expr is None:
            found.problems.append(f"{module.rel}:{line}: {where} carries no reason the gate can read")
            return set()
        values = resolver.resolve(module.name, expr)
        if values is not None:
            return values
        text = _segment(module, expr)
        if (module.rel, text) in pass_throughs:
            matched_pass_throughs.add((module.rel, text))
            return set()
        found.problems.append(
            f"{module.rel}:{line}: {where} has the reason `{text}`, which is neither a literal, a module "
            f"constant, a StrEnum value nor on PASS_THROUGHS"
        )
        return set()

    positional: set[tuple[str, str]] = set()
    named: set[tuple[str, str]] = set()
    partial: set[tuple[str, str]] = set()
    for key in resolver.class_index:
        lineage = resolver.ancestors(key)
        if lineage & _NAMED_ROOTS:
            named.add(key)
        elif lineage & _POSITIONAL_ROOTS:
            positional.add(key)
        if _PARTIAL_ROOT in lineage:
            partial.add(key)

    for key in sorted(positional - _POSITIONAL_ROOTS):
        cdef = resolver.class_index[key]
        if any(isinstance(n, ast.FunctionDef) and n.name == "__init__" for n in cdef.body):
            module = modules[key[0]]
            found.problems.append(
                f"{module.rel}:{cdef.lineno}: {key[1]} takes its reason as an argument but defines its own "
                f"__init__, so the gate cannot tell which argument is the reason"
            )

    for key in sorted(named - _NAMED_ROOTS):
        cdef = resolver.class_index[key]
        module = modules[key[0]]
        own = _class_reason(cdef)
        if own is not None:
            found.reasons |= read(module, own, f"{key[1]}.reason", cdef.lineno)
        elif not any(
            _class_reason(resolver.class_index[a]) is not None for a in resolver.ancestors(key) - {key} - _NAMED_ROOTS
        ):
            found.problems.append(f"{module.rel}:{cdef.lineno}: {key[1]} is a named refusal that declares no reason")

    for module in modules.values():
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            key = resolver.class_of(module.name, node.func)
            if key is None:
                continue
            if key in positional:
                found.reasons |= read(module, _reason_argument(node), f"{key[1]}(...)", node.lineno)
            elif key in partial:
                found.reasons |= read(module, _reason_argument(node), f"{key[1]}(...)", node.lineno)

    classifier_module = modules.get(_CLASSIFIER[0])
    classifier = None
    if classifier_module is not None:
        classifier = next(
            (
                n
                for n in classifier_module.tree.body
                if isinstance(n, ast.FunctionDef) and n.name == _CLASSIFIER[1]
            ),
            None,
        )
    if classifier_module is None or classifier is None:
        found.problems.append(f"{_CLASSIFIER[0]}.{_CLASSIFIER[1]} not found")
    else:
        for node in ast.walk(classifier):
            if not isinstance(node, ast.Return):
                continue
            if not (isinstance(node.value, ast.Tuple) and node.value.elts):
                found.problems.append(
                    f"{classifier_module.rel}:{node.lineno}: {_CLASSIFIER[1]} returns something other than a tuple"
                )
                continue
            found.classifier |= read(classifier_module, node.value.elts[0], _CLASSIFIER[1], node.lineno)
        found.reasons |= found.classifier

    for module_name, expr, owner in resolver.unresolved:
        module = modules[module_name]
        problem = (
            f"{module.rel}:{expr.lineno}: `{_segment(module, expr)}` names {expr.attr}, which {owner} does not "
            f"define, so the gate cannot tell whether it is a refusal"
        )
        if problem not in found.problems:
            found.problems.append(problem)

    for rel, text in sorted(set(pass_throughs) - matched_pass_throughs):
        found.problems.append(
            f"PASS_THROUGHS names `{text}` in {rel}, which no refusal or partial failure there passes on any more"
        )
    return found


def _class_reason(cdef: ast.ClassDef) -> ast.expr | None:
    for node in cdef.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "reason"
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "reason"
            and node.value is not None
        ):
            return node.value
    return None


# ── Frontend ────────────────────────────────────────────────────────


def _strip_comments(text: str) -> str:
    """*text* with ``//`` and ``/* */`` comments blanked; string literals kept verbatim."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            end = i + 1
            while end < n and text[end] != ch:
                end += 2 if text[end] == "\\" else 1
            out.append(text[i : end + 1])
            i = end + 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _type_declarations(text: str) -> dict[str, str]:
    """Every ``export type Name = …;`` in *text*, by name → its right-hand side."""
    declarations: dict[str, str] = {}
    needle = "export type "
    i = 0
    while True:
        start = text.find(needle, i)
        if start == -1:
            return declarations
        eq = text.find("=", start)
        end = text.find(";", eq)
        if eq == -1 or end == -1:
            return declarations
        name = text[start + len(needle) : eq].strip()
        declarations[name] = text[eq + 1 : end]
        i = end + 1


def _union_members(declarations: dict[str, str], name: str, problems: list[str], depth: int = 0) -> set[str]:
    members: set[str] = set()
    for raw in declarations[name].split("|"):
        member = raw.strip()
        if not member:
            continue
        if len(member) >= 2 and member[0] == member[-1] and member[0] in "\"'":
            members.add(member[1:-1])
        elif member in declarations and depth < 10:
            members |= _union_members(declarations, member, problems, depth + 1)
        else:
            problems.append(f"{name}: member `{member}` is neither a string literal nor a union declared in this file")
    return members


@dataclass
class FrontendReasons:
    """What the frontend side read: the union's members, its RomM sub-union's, and what could not be read."""

    reasons: set[str] = field(default_factory=set)
    classifier: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)


def parse_frontend_reasons() -> FrontendReasons:
    """Read ``WireReason`` and ``RommErrorReason`` from ``UNION_FILE``."""
    union_file = UNION_FILE
    found = FrontendReasons()
    if not union_file.is_file():
        found.problems.append(f"{union_file} not found")
        return found
    declarations = _type_declarations(_strip_comments(union_file.read_text(encoding="utf-8")))
    for name in (UNION_NAME, CLASSIFIER_UNION_NAME):
        if name not in declarations:
            found.problems.append(f"{union_file.name}: no `export type {name} = …;`")
    if UNION_NAME in declarations:
        found.reasons = _union_members(declarations, UNION_NAME, found.problems)
    if CLASSIFIER_UNION_NAME in declarations:
        found.classifier = _union_members(declarations, CLASSIFIER_UNION_NAME, found.problems)
    return found


def find_discrepancies(backend: BackendReasons, frontend: FrontendReasons, exempt: frozenset[str]) -> list[str]:
    """One line per discrepancy: an unreadable site, or a reason on one side only."""
    findings = [*backend.problems, *frontend.problems]
    backend_reasons = backend.reasons - exempt
    frontend_reasons = frontend.reasons - exempt
    findings.extend(
        f'"{r}": the backend answers with it but {UNION_NAME} does not hold it — add it to the union'
        for r in sorted(backend_reasons - frontend_reasons)
    )
    findings.extend(
        f'"{r}": {UNION_NAME} holds it but no backend refusal, partial failure or classify_error answers with it'
        for r in sorted(frontend_reasons - backend_reasons)
    )
    findings.extend(
        f'"{r}": classify_error answers with it but {CLASSIFIER_UNION_NAME} does not hold it'
        for r in sorted(backend.classifier - frontend.classifier)
    )
    findings.extend(
        f'"{r}": {CLASSIFIER_UNION_NAME} holds it but classify_error never answers with it'
        for r in sorted(frontend.classifier - backend.classifier)
    )
    return findings


def main(argv: list[str]) -> int:
    if any(a in {"-h", "--help"} for a in argv):
        print(__doc__)
        return 0
    backend = parse_backend_reasons()
    frontend = parse_frontend_reasons()
    findings = find_discrepancies(backend, frontend, EXEMPT)
    if findings:
        for line in findings:
            print(line)
        print()
        print(
            f"ERROR: the backend's wire reasons and the panel's {UNION_NAME} union "
            f"(frontend/src/types/reasons.ts) have drifted."
        )
        return 1
    print(f"OK: backend↔frontend wire-reason parity matches ({len(backend.reasons)} reasons).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
