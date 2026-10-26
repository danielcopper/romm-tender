#!/usr/bin/env python3
"""The resolver's answer is read only in the module that asks for it.

Each kind of answer emu-atlas gives — firmware, saves, the catalogue, the
folders and the sources — is read in one module, the adapter that asks the
resolver for it (:data:`READING_MODULES`). That module turns the answer into
Tender's own view, and every later layer reads the view. Two scans hold it,
both over ``backend/`` less ``_vendor/`` and ``native/``:

* **Imports.** Only a reading module, or a module :data:`IMPORT_GRANTS` names,
  imports the resolver, and only names in ``atlas.__all__``. A name from a
  submodule, or read off the package that ``__all__`` does not hold, is an
  internal: :data:`INTERNALS_GRANTED` names the ones a module may hold, with
  the reason, and :data:`INTERNALS_TO_REMOVE` the ones still held today.
* **Vocabulary.** Outside the reading modules, no string literal equals one of
  the resolver's codes (every string constant in ``atlas.__all__`` whose name
  starts with one of :data:`CODE_FAMILIES`, taken from the vendored resolver
  when the check runs) and no code reads one of :data:`WATCHED_NAMES`, the
  answer's distinctive field names — as an attribute, a keyword argument, a
  ``getattr`` constant, a subscript or ``.get`` constant, or a dict literal's
  key. Docstrings are not read. :data:`READERS_TODAY` lists what each module
  reads today; :data:`NOT_THE_RESOLVERS` the names that mean something else in
  a module, with the reason.

:data:`READERS_TODAY` and :data:`INTERNALS_TO_REMOVE` only shrink: an entry
nothing needs any more fails, as does an exception or grant nothing carries,
and ``tests/scripts/test_check_resolver_reading.py`` fails on an entry either
list did not hold when it was written.

The panel is held by ESLint (``tender/no-resolver-vocabulary`` in
``frontend/eslint.config.js``), over the codes and names this script writes to
:data:`FRONTEND_VOCABULARY`; the check fails when that file is not what
``--write`` would write today.

What it does not see:

* a code or name built at run time (``"marker-" + kind``, ``getattr(x, name)``),
  or held in a constant another module defines;
* a watched name read through ``dataclasses.asdict``, ``vars`` or ``**``
  unpacking, or written as an attribute (``x.satisfied = …``);
* an import through ``importlib`` or ``__import__`` whose module name is not a
  string constant;
* which of a module's sites reads a listed name: once a module is listed for a
  name, a second site in it reading that name passes;
* a fact the reading module passes on under a name of Tender's own, which a
  later layer may still judge again.

Exit 0 when nothing outside the lists reads the resolver, 1 (one line per
finding) otherwise. ``--write`` rewrites :data:`FRONTEND_VOCABULARY` and exits 0.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
FRONTEND_VOCABULARY = REPO_ROOT / "frontend" / "resolver-vocabulary.json"

RESOLVER = "_vendor.atlas"

# The adapter that asks the resolver for each kind of answer.
READING_MODULES = frozenset(
    {
        "adapters/atlas_catalogue.py",
        "adapters/atlas_firmware.py",
        "adapters/atlas_saves.py",
        "adapters/emulator_sources.py",
        "adapters/retrodeck_folders.py",
    }
)

# Modules that read no answer and import the resolver anyway.
IMPORT_GRANTS: dict[str, str] = {
    "adapters/atlas_zstd.py": (
        "hands the resolver the vendored zstd codec through register_zstd_provider, the resolver's own "
        "grant for a codec under a name it never tries, and reads back zstd_provider for the start-up log"
    ),
}

# (module, qualified name) -> why the module may hold a name atlas.__all__ does not export.
INTERNALS_GRANTED: dict[tuple[str, str], str] = {
    ("adapters/emulator_sources.py", "_vendor.atlas.machine.RealMachine"): (
        "the one machine the process keeps, which every question is asked through; the resolver "
        "exports no constructor for it"
    ),
}

# Internals a module holds today. Only shrinks.
INTERNALS_TO_REMOVE = frozenset(
    {
        ("adapters/atlas_firmware.py", "_vendor.atlas.firmware.core_short_name"),
        ("adapters/atlas_firmware.py", "_vendor.atlas.firmware.declared_directory_of"),
        ("adapters/atlas_firmware.py", "_vendor.atlas.firmware.load_hashes"),
    }
)

CODE_FAMILIES = ("CAVEAT_", "HEALTH_ISSUE_", "UNRESOLVED_", "REASON_")

# The answer's field names that state a fact: its verdicts, their causes, the
# provenance and granularity of what it read, and the console's own firmware.
# A name only a generic meaning would match (``path``, ``label``, ``system``,
# ``regions``, ``role``, ``verdict``) is not watched.
WATCHED_NAMES = frozenset(
    {
        "absent_by_search",
        "availability",
        "caveats",
        "checked",
        "contents_satisfied",
        "declaration",
        "declared",
        "declared_kind",
        "file_set",
        "granularity",
        "hash_checked",
        "issues",
        "known_as",
        "locating",
        "need",
        "needs",
        "present",
        "provenance",
        "refused",
        "region_source",
        "requirements_met",
        "root_kind",
        "satisfied",
        "size_fixed",
        "supplied_by",
        "system_firmware",
        "system_firmware_met",
        "system_firmware_needs",
        "undetermined",
        "unmet",
        "unread",
        "unread_stating_a_path",
        "weighed_options",
    }
)

# Module -> the codes and names it reads today, outside a reading module. Only shrinks.
READERS_TODAY: dict[str, frozenset[str]] = {
    "domain/bios_status.py": frozenset(
        {
            "caveats",
            "checked",
            "declaration",
            "declared_kind",
            "locating",
            "requirements_met",
            "satisfied",
            "supplied_by",
            "system_firmware_met",
            "undetermined",
            "unmet",
        }
    ),
    "domain/firmware_groups.py": frozenset({"satisfied"}),
    "domain/firmware_rows.py": frozenset({"present"}),
    "domain/firmware_wants.py": frozenset(
        {
            "caveats",
            "declared_kind",
            "firmware-image-identified",
            "firmware-image-unlisted",
            "firmware-path-inaccessible",
            "firmware-scan-incomplete",
            "refused",
            "system_firmware",
        }
    ),
    "domain/save_answer.py": frozenset(
        {
            "caveats",
            "companion-config-missing",
            "config-unreadable",
            "emulator-config-missing",
            "emulator-config-unreadable",
            "file-names-unestablished",
            "granularity",
            "marker-invalid",
            "marker-missing",
            "marker-unreadable",
            "needs",
            "not-set-up",
            "root_kind",
            "save-inside-content",
            "save-inside-image",
            "saves-root-missing",
            "size_fixed",
            "sorted-dir-missing",
        }
    ),
    "domain/source_roots.py": frozenset(
        {
            "bios-root-missing",
            "marker-invalid",
            "marker-missing",
            "marker-unreadable",
            "not-set-up",
            "provenance",
            "root-missing",
            "saves-root-missing",
        }
    ),
    "services/firmware/downloads.py": frozenset({"satisfied"}),
    "services/firmware/emulator_answers.py": frozenset(
        {
            "declaration",
            "declared",
            "locating",
            "refused",
            "requirements_met",
        }
    ),
    "services/firmware/replacement.py": frozenset({"present", "satisfied"}),
    "services/firmware/rows.py": frozenset({"caveats", "checked", "declared_kind", "present", "satisfied"}),
    "services/firmware/service.py": frozenset({"known_as"}),
    "services/firmware/status.py": frozenset({"declaration", "present", "requirements_met", "satisfied"}),
    "services/firmware/store.py": frozenset({"present", "unread"}),
    "services/saves/save_directory.py": frozenset({"caveats", "root_kind"}),
    "services/saves/status/service.py": frozenset({"caveats", "granularity", "needs"}),
    "services/saves/sync_engine/matrix.py": frozenset({"size_fixed"}),
}

# Module -> {name: why it is not the resolver's there}.
NOT_THE_RESOLVERS: dict[str, dict[str, str]] = {
    "adapters/gavel_native.py": {
        "size_fixed": "a field of the save-sync core's own input, which states its own contract",
    },
    "host/server.py": {
        "refused": "the admission verdict on a connection to the backend",
    },
    "services/rom_adoption/service.py": {
        "unread": "an outcome of the adoption's own content check, keying its wording",
    },
    "services/saves/status/service.py": {
        "checked": "whether Tender's own local-save check ran",
    },
}


def resolver_codes() -> dict[str, str]:
    """Every code the vendored resolver exports, by its constant's name."""
    atlas = _vendored_atlas()
    codes: dict[str, str] = {}
    for name in atlas.__all__:
        value = getattr(atlas, name)
        if name.startswith(CODE_FAMILIES) and isinstance(value, str):
            codes[name] = value
    return codes


def resolver_exports() -> frozenset[str]:
    return frozenset(_vendored_atlas().__all__)


def _vendored_atlas():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from _vendor import atlas

    return atlas


def scanned_modules(backend: Path) -> list[Path]:
    return sorted(
        path for path in backend.rglob("*.py") if not {"_vendor", "native"} & set(path.relative_to(backend).parts)
    )


# --- Imports ---------------------------------------------------------------


def _names_resolver(node: ast.AST) -> bool:
    """Whether *node* is ``atlas`` or ``_vendor.atlas``."""
    if isinstance(node, ast.Name):
        return node.id == "atlas"
    return isinstance(node, ast.Attribute) and node.attr == "atlas" and isinstance(node.value, ast.Name)


def _imported_module(node: ast.Call) -> str | None:
    """The module an ``importlib.import_module`` or ``__import__`` call names by a constant."""
    func = node.func
    named = (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
        isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}
    )
    if named and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return None


def resolver_imports(tree: ast.AST, exports: frozenset[str]) -> list[tuple[int, str | None]]:
    """``(line, internal)`` for each import of the resolver in *tree*.

    ``internal`` is the qualified name of what was imported when it is not in
    ``atlas.__all__``, and ``None`` when it is.
    """
    found: list[tuple[int, str | None]] = []
    holds_package = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "_vendor" and any(alias.name == "atlas" for alias in node.names):
                holds_package = True
                found.append((node.lineno, None))
            elif module == RESOLVER:
                found.extend(
                    (node.lineno, None if alias.name in exports or alias.name == "*" else f"{module}.{alias.name}")
                    for alias in node.names
                )
            elif module.startswith(f"{RESOLVER}."):
                found.extend((node.lineno, f"{module}.{alias.name}") for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RESOLVER:
                    holds_package = True
                    found.append((node.lineno, None))
                elif alias.name.startswith(f"{RESOLVER}."):
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.Call):
            module = _imported_module(node)
            if module == RESOLVER:
                found.append((node.lineno, None))
            elif module is not None and module.startswith(f"{RESOLVER}."):
                found.append((node.lineno, module))
    if holds_package:
        found.extend(
            (node.lineno, f"{RESOLVER}.{node.attr}")
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and _names_resolver(node.value) and node.attr not in exports
        )
    return found


def import_findings(backend: Path, exports: frozenset[str]) -> list[str]:
    findings: list[str] = []
    held: set[tuple[str, str]] = set()
    importers: set[str] = set()
    for path in scanned_modules(backend):
        module = path.relative_to(backend).as_posix()
        for line, internal in resolver_imports(ast.parse(path.read_text(encoding="utf-8")), exports):
            importers.add(module)
            where = f"backend/{module}:{line}"
            if module not in READING_MODULES and module not in IMPORT_GRANTS:
                findings.append(f"{where} imports the resolver, and is no reading module.")
            if internal is None:
                continue
            held.add((module, internal))
            if (module, internal) not in INTERNALS_GRANTED and (module, internal) not in INTERNALS_TO_REMOVE:
                findings.append(f"{where} imports {internal}, which atlas.__all__ does not export.")
    findings.extend(
        f"backend/{module} holds {internal} no longer: take it off INTERNALS_TO_REMOVE."
        for module, internal in sorted(INTERNALS_TO_REMOVE - held)
    )
    findings.extend(
        f"backend/{module} holds {internal} no longer: take it off INTERNALS_GRANTED."
        for module, internal in sorted(set(INTERNALS_GRANTED) - held)
    )
    findings.extend(
        f"backend/{module} imports the resolver no longer: take it off IMPORT_GRANTS."
        for module in sorted(set(IMPORT_GRANTS) - importers)
    )
    return findings


# --- Vocabulary ------------------------------------------------------------


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _names_read(node: ast.AST) -> list[str]:
    """The field names *node* reads or keys by, whatever they are."""
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        return [node.attr]
    if isinstance(node, ast.keyword) and node.arg is not None:
        return [node.arg]
    if isinstance(node, ast.Subscript):
        key = _constant_string(node.slice)
        return [key] if key is not None else []
    if isinstance(node, ast.Dict):
        return [key for key in map(_constant_string, node.keys) if key is not None]
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) > 1:
            key = _constant_string(node.args[1])
            return [key] if key is not None else []
        if isinstance(func, ast.Attribute) and func.attr == "get":
            key = _constant_string(node.args[0])
            return [key] if key is not None else []
    return []


def vocabulary_sites(tree: ast.AST, codes: frozenset[str]) -> list[tuple[int, str]]:
    """``(line, code or name)`` for each site in *tree* that names the resolver's vocabulary."""
    docstrings = _docstrings(tree)
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in codes and id(node) not in docstrings:
            sites.append((node.lineno, node.value))
        line = getattr(node, "lineno", 0)
        sites.extend((line, name) for name in _names_read(node) if name in WATCHED_NAMES)
    return sites


def vocabulary_findings(backend: Path, codes: frozenset[str]) -> list[str]:
    findings: list[str] = []
    read: dict[str, set[str]] = {}
    for path in scanned_modules(backend):
        module = path.relative_to(backend).as_posix()
        if module in READING_MODULES:
            continue
        listed = READERS_TODAY.get(module, frozenset())
        excepted = NOT_THE_RESOLVERS.get(module, {})
        for line, token in vocabulary_sites(ast.parse(path.read_text(encoding="utf-8")), codes):
            read.setdefault(module, set()).add(token)
            if token in listed or token in excepted:
                continue
            what = "the resolver's code" if token in codes else "the resolver's field name"
            findings.append(
                f"backend/{module}:{line} names {what} {token!r} outside a reading module: "
                "read Tender's own view from the reading module instead."
            )
    for module, tokens in sorted(READERS_TODAY.items()):
        findings.extend(
            f"backend/{module} reads {token!r} no longer: take it off READERS_TODAY."
            for token in sorted(tokens - read.get(module, set()))
        )
    for module, names in sorted(NOT_THE_RESOLVERS.items()):
        findings.extend(
            f"backend/{module} reads {name!r} no longer: take it off NOT_THE_RESOLVERS."
            for name in sorted(set(names) - read.get(module, set()))
        )
    return findings


# --- The panel's copy ------------------------------------------------------


def frontend_vocabulary(codes: frozenset[str]) -> str:
    return (
        json.dumps(
            {
                "generated_by": "scripts/check_resolver_reading.py --write",
                "codes": sorted(codes),
                "names": sorted(WATCHED_NAMES),
            },
            indent=2,
        )
        + "\n"
    )


def frontend_findings(codes: frozenset[str]) -> list[str]:
    path = FRONTEND_VOCABULARY.relative_to(REPO_ROOT)
    if not FRONTEND_VOCABULARY.exists():
        return [f"{path} does not exist: run `python scripts/check_resolver_reading.py --write`."]
    if FRONTEND_VOCABULARY.read_text(encoding="utf-8") != frontend_vocabulary(codes):
        return [
            f"{path} is not what the vendored resolver and WATCHED_NAMES give today: "
            "run `python scripts/check_resolver_reading.py --write` and commit the result."
        ]
    return []


def main(argv: list[str]) -> int:
    if any(a in {"-h", "--help"} for a in argv):
        print(__doc__)
        return 0
    codes = frozenset(resolver_codes().values())
    if "--write" in argv:
        FRONTEND_VOCABULARY.write_text(frontend_vocabulary(codes), encoding="utf-8")
        print(f"Wrote {FRONTEND_VOCABULARY.relative_to(REPO_ROOT)}.")
        return 0
    findings = [
        *import_findings(BACKEND, resolver_exports()),
        *vocabulary_findings(BACKEND, codes),
        *frontend_findings(codes),
    ]
    if findings:
        for line in findings:
            print(line)
        print()
        print(
            "ERROR: the resolver's answer is read outside its reading module "
            "(CLAUDE.md → Invariant register; docs/architecture/invariants.md)."
        )
        return 1
    print(
        f"OK: the resolver is imported and its {len(codes)} codes and {len(WATCHED_NAMES)} field names are read "
        "only where the reading modules and the lists allow."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
