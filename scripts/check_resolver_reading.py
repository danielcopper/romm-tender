#!/usr/bin/env python3
"""The resolver's answer is read only in the module that asks for it.

Each kind of answer emu-atlas gives — firmware, saves, the catalogue, the
folders and the sources — is read in one module, the adapter that asks the
resolver for it (:data:`READING_MODULES`). That module turns the answer into
Tender's own view, and every later layer reads the view. Two scans hold it,
both over ``backend/`` less ``_vendor/`` and ``native/``. ``tests/`` is not
scanned: tests build the resolver's machines and answers rather than read an
answer for display.

* **Imports.** Only a reading module, or a module :data:`IMPORT_GRANTS` names,
  imports the resolver, and only names in ``atlas.__all__``. A name from a
  submodule, or read off the package that ``__all__`` does not hold, is an
  internal: :data:`INTERNALS_GRANTED` names the ones a module may hold, with
  the reason, and :data:`INTERNALS_TO_REMOVE` the ones still held today.
* **Vocabulary.** Outside the reading modules, no string literal equals one of
  the resolver's codes (every string constant in ``atlas.__all__`` whose name
  starts with one of :data:`CODE_FAMILIES`) or one of the values of its other
  closed vocabularies (every other string constant ``atlas.__all__`` holds, and
  every string in a tuple, a frozenset, a ``Literal`` or a union of ``Literal``
  types it exports, less :data:`GENERIC_VALUES`) — both taken from the
  vendored resolver when the check runs — and no code reads one of
  :data:`WATCHED_NAMES`, the answer's distinctive field names — as an
  attribute, a keyword argument, a ``getattr`` constant, a subscript or
  ``.get`` constant, or a dict literal's key. Docstrings are not read.
  :data:`READERS_TODAY` lists what each module reads today;
  :data:`NOT_THE_RESOLVERS` the names that mean something else in a module,
  with the reason.

An entry nothing needs any more fails on every list but
:data:`READING_MODULES`: a reader that reads nothing, a grant or internal no
module holds, a homonym no module speaks, and a generic word Tender's own code
no longer speaks or the resolver no longer states. A word is spoken where a
backend module outside the reading modules holds it as a string constant, or
a code line of a panel source file quotes it. :data:`READERS_TODAY`,
:data:`INTERNALS_TO_REMOVE`, :data:`NOT_THE_RESOLVERS` and
:data:`GENERIC_VALUES` only shrink, and
``tests/scripts/test_check_resolver_reading.py`` fails on an entry one of them
did not hold when it was written; it holds :data:`READING_MODULES`,
:data:`IMPORT_GRANTS` and :data:`INTERNALS_GRANTED` to exactly what was
decided.

The panel is held by ESLint (``tender/no-resolver-vocabulary`` in
``frontend/eslint.config.js``), over the codes, values and names this script writes to
:data:`FRONTEND_VOCABULARY`; the check fails when that file is not what
``--write`` would write today.

What it does not see:

* a code or name built at run time (``"marker-" + kind``, ``getattr(x, name)``),
  or held in a constant another module defines;
* a watched name read through ``hasattr``, ``operator.attrgetter``, an ``in``
  test (``"satisfied" in row``), a ``match`` class or mapping pattern,
  ``setdefault`` or ``pop``, ``dataclasses.asdict``, ``vars`` or ``**``
  unpacking, or written as an attribute (``x.satisfied = …``);
* a code or value spelled as a name rather than a string — an attribute
  (``obj.libretro``), a keyword argument, a parameter or a field — which is
  seen only where it is also on :data:`WATCHED_NAMES` and read as a name;
* a value on :data:`GENERIC_VALUES`, nor whether a generic word is still
  spoken with a meaning of Tender's own rather than read from an answer; and a word of Tender's own that it
  speaks inside one of the resolver's vocabularies (``CHECKED_REJECTED =
  "rejected"`` in ``domain/firmware_wants.py``), which the resolver never
  states;
* a watched value handed on through a parameter or a field of Tender's own
  name, which a later layer may still judge again;
* an import through ``importlib`` or ``__import__`` whose module name is not a
  string constant, and a module object such a call answers;
* which of a module's sites reads a name it is listed or excepted for: once a
  module stands on :data:`READERS_TODAY` or :data:`NOT_THE_RESOLVERS` for a
  name, a second site in it reading that name passes, whatever it reads.

Exit 0 when nothing outside the lists reads the resolver, 1 (one line per
finding) otherwise. ``--write`` rewrites :data:`FRONTEND_VOCABULARY` and exits 0.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import types
import typing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
FRONTEND_VOCABULARY = REPO_ROOT / "frontend" / "resolver-vocabulary.json"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

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

# Values of the resolver's closed vocabularies that Tender's own code also
# speaks with a meaning of its own, too widely to except module by module, and
# so are not watched: where else each is spoken. Only shrinks.
GENERIC_VALUES: dict[str, str] = {
    "absent": "the running-apps reader's word for an app Steam does not list",
    "answered": "the panel's word for a read that came back",
    "bios": "the BIOS folder and tab, by name",
    "directory": "the word for a folder, in adoption, saves and the panel",
    "empty": "the artwork and update-output words for nothing there",
    "file": "the word for a file, in adoption, deletion and the panel",
    "igdb": "a metadata database RomM keys platforms and collections by",
    "list": "the panel's id for a page's list pane",
    "missing": "the word for a file or folder not there, in adoption, migration and the panel",
    "none": "the word for nothing chosen, across artwork, saves, prune and the panel",
    "ok": "the word for a step that worked, in saves, the host and the panel",
    "open": "the crash watchdog's word for an unresolved injection",
    "optional": "the kind of Tender's own firmware wanted mark",
    "read": "the shortcut file reader's word for a file it read",
    "refused": "an artwork slot's outcome, and the endpoint answers' refusals",
    "region": "an ARIA role, and a game's region in RomM's vocabulary",
    "required": "the panel's need word, and a key of Tender's own firmware payloads",
    "roms": "the ROM folder, by name, in recovery bundles and the library",
    "saves": "the saves folder and tab, by name",
    "settings": "the settings page, by name",
    "slug": "a platform's slug, RomM's key for it",
    "title": "the title of a page, a toast or an achievement",
    "unchecked": "the save-stranding and launch-prompt words for a check not made",
    "unknown": "the word for anything not known, in a dozen modules and the panel",
    "unread": "the adoption content check's and the artwork tab's word for a file not read",
    "unreadable": "the shortcut file reader's and the Steam Input writer's word for a file they could not read",
    "unsupported": "the word for an action a game or a server cannot take, in saves, discs and the panel",
}

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
    "adapters/es_find_rules.py": frozenset({"staticpath"}),
    "adapters/repositories/rom_install.py": frozenset({"launchable"}),
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
    "domain/emulator_commands.py": frozenset({"libretro", "standalone"}),
    "domain/firmware_groups.py": frozenset({"ntsc-j", "ntsc-u", "pal", "satisfied"}),
    "domain/firmware_rows.py": frozenset({"present"}),
    "domain/firmware_wants.py": frozenset(
        {
            "archive",
            "by-name",
            "by-name-then-content",
            "cannot-run-without-firmware",
            "caveats",
            "core-supplies-an-alternative",
            "declared_kind",
            "firmware-image-identified",
            "firmware-image-unlisted",
            "firmware-path-inaccessible",
            "firmware-scan-incomplete",
            "refused",
            "runs-without-firmware",
            "system_firmware",
            "unestablished",
            "unrecognised",
            "verified",
        }
    ),
    "domain/platform_system.py": frozenset({"declared", "disabled", "libretro", "screenscraper", "thegamesdb"}),
    "domain/save_answer.py": frozenset(
        {
            "caveats",
            "companion-config-missing",
            "config-unreadable",
            "content_directory",
            "cwd",
            "emulator-config-missing",
            "emulator-config-unreadable",
            "file-names-unestablished",
            "granularity",
            "marker-invalid",
            "marker-missing",
            "marker-unreadable",
            "needs",
            "not-set-up",
            "notes",
            "root_kind",
            "save-inside-content",
            "save-inside-image",
            "saves-root-missing",
            "shared-card",
            "shared-file",
            "size_fixed",
            "sorted-dir-missing",
        }
    ),
    "domain/shortcut_data.py": frozenset({"libretro", "standalone"}),
    "domain/source_roots.py": frozenset(
        {
            "assumed",
            "bios-root-missing",
            "frontend-default",
            "installation",
            "marker-invalid",
            "marker-missing",
            "marker-unreadable",
            "not-set-up",
            "provenance",
            "root-missing",
            "saves-root-missing",
        }
    ),
    "services/active_core_resolver.py": frozenset({"standalone"}),
    "services/downloads/service.py": frozenset({"launchable"}),
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
    "adapters/flatpak_install.py": {
        "flatpak": "a folder of Flatpak's own per-user install tree",
    },
    "adapters/gavel_native.py": {
        "native": "the folder the compiled save-sync core ships in",
        "size_fixed": "a field of the save-sync core's own input, which states its own contract",
    },
    "domain/rom_adoption.py": {
        "mismatch": "an outcome of the adoption's own content check against RomM's checksums",
    },
    "domain/save_answer.py": {
        "unestablished": "the save answer's own state for a save nothing established",
    },
    "host/inject/bundles.py": {
        "standalone": "the panel bundle's own kind",
    },
    "host/server.py": {
        "refused": "the admission verdict on a connection to the backend",
    },
    "services/emulator_sources.py": {
        "catalogue": "the key of a source report's own catalogue state",
    },
    "services/firmware/emulator_answers.py": {
        "setting": "the empty emulator setting Tender's own bios-unchosen note names, not the region source",
    },
    "services/rom_adoption/service.py": {
        "mismatch": "an outcome of the adoption's own content check, keying its wording",
        "unread": "an outcome of the adoption's own content check, keying its wording",
    },
    "services/saves/status/service.py": {
        "checked": "whether Tender's own local-save check ran",
        "unestablished": "the key of the save answer's own unestablished state",
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


def resolver_vocabulary_values() -> frozenset[str]:
    """Every value of the resolver's closed vocabularies that is no code.

    A string constant ``atlas.__all__`` holds, and a string in a tuple, a
    frozenset, a ``Literal`` or a union of ``Literal`` types it exports.
    """
    atlas = _vendored_atlas()
    found: set[str] = set()
    for name in atlas.__all__:
        value = getattr(atlas, name)
        if name.startswith("__"):
            continue
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, tuple | frozenset):
            found.update(member for member in value if isinstance(member, str))
        else:
            found.update(_literal_strings(value))
    return frozenset(found) - frozenset(resolver_codes().values())


def _literal_strings(annotation: object) -> set[str]:
    """The strings a ``Literal`` type admits, followed through a union of them."""
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return {member for member in typing.get_args(annotation) if isinstance(member, str)}
    if origin is typing.Union or origin is types.UnionType:
        return {member for arm in typing.get_args(annotation) for member in _literal_strings(arm)}
    return set()


def resolver_values() -> frozenset[str]:
    """The values of the resolver's closed vocabularies that are watched."""
    return resolver_vocabulary_values() - set(GENERIC_VALUES)


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


def _names_resolver(node: ast.AST, bound: set[str], *, by_path: bool) -> bool:
    """Whether *node* is the resolver's package: a name it is bound to, or ``_vendor.atlas`` where *by_path*."""
    if isinstance(node, ast.Name):
        return node.id in bound
    return (
        by_path
        and isinstance(node, ast.Attribute)
        and node.attr == "atlas"
        and isinstance(node.value, ast.Name)
        and node.value.id == "_vendor"
    )


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
    # The names the package is bound to (``atlas`` or an alias), and whether
    # ``import _vendor.atlas`` lets it be reached as ``_vendor.atlas``.
    bound: set[str] = set()
    by_path = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "_vendor" and any(alias.name == "atlas" for alias in node.names):
                bound.update(alias.asname or "atlas" for alias in node.names if alias.name == "atlas")
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
                    if alias.asname is None:
                        by_path = True
                    else:
                        bound.add(alias.asname)
                    found.append((node.lineno, None))
                elif alias.name.startswith(f"{RESOLVER}."):
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.Call):
            module = _imported_module(node)
            if module == RESOLVER:
                found.append((node.lineno, None))
            elif module is not None and module.startswith(f"{RESOLVER}."):
                found.append((node.lineno, module))
    found.extend(
        (node.lineno, f"{RESOLVER}.{node.attr}")
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and _names_resolver(node.value, bound, by_path=by_path)
        and node.attr not in exports
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
            what = "the resolver's field name" if token in WATCHED_NAMES else "the resolver's code or value"
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


# --- The generic words ---------------------------------------------------

_QUOTED = re.compile(r"""["'`]([^"'`\s]+)["'`]""")


def _panel_sources(src: Path) -> list[Path]:
    """The panel's source files the ESLint rule reads: no test, no test utility."""
    return sorted(
        path
        for path in src.rglob("*.ts*")
        if path.suffix in {".ts", ".tsx"}
        and not path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
        and path.name != "test-setup.ts"
        and "test-utils" not in path.relative_to(src).parts
    )


def _spoken(backend: Path, src: Path) -> set[str]:
    """Every string Tender's own code speaks: a constant outside the reading modules, a quoted word in the panel."""
    words: set[str] = set()
    for path in scanned_modules(backend):
        if path.relative_to(backend).as_posix() in READING_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstrings(tree)
        words.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        )
    for path in _panel_sources(src):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith(("//", "*", "/*")):
                words.update(_QUOTED.findall(line))
    return words


def generic_findings(backend: Path, src: Path, vocabulary: frozenset[str]) -> list[str]:
    """A generic word the resolver no longer states, or Tender's own code no longer speaks."""
    spoken = _spoken(backend, src)
    findings: list[str] = []
    for word in sorted(GENERIC_VALUES):
        if word not in vocabulary:
            findings.append(f"GENERIC_VALUES lists {word!r}, which the resolver no longer states: take it off.")
        elif word not in spoken:
            findings.append(f"GENERIC_VALUES lists {word!r}, which Tender's own code no longer speaks: take it off.")
    return findings


# --- The panel's copy ------------------------------------------------------


def frontend_vocabulary(codes: frozenset[str], values: frozenset[str]) -> str:
    return (
        json.dumps(
            {
                "generated_by": "scripts/check_resolver_reading.py --write",
                "codes": sorted(codes),
                "values": sorted(values),
                "names": sorted(WATCHED_NAMES),
            },
            indent=2,
        )
        + "\n"
    )


def frontend_findings(codes: frozenset[str], values: frozenset[str]) -> list[str]:
    path = FRONTEND_VOCABULARY.relative_to(REPO_ROOT)
    if not FRONTEND_VOCABULARY.exists():
        return [f"{path} does not exist: run `python scripts/check_resolver_reading.py --write`."]
    if FRONTEND_VOCABULARY.read_text(encoding="utf-8") != frontend_vocabulary(codes, values):
        return [
            f"{path} is not what the vendored resolver, GENERIC_VALUES and WATCHED_NAMES give today: "
            "run `python scripts/check_resolver_reading.py --write` and commit the result."
        ]
    return []


def main(argv: list[str]) -> int:
    if any(a in {"-h", "--help"} for a in argv):
        print(__doc__)
        return 0
    codes = frozenset(resolver_codes().values())
    vocabulary = resolver_vocabulary_values()
    values = vocabulary - set(GENERIC_VALUES)
    if "--write" in argv:
        FRONTEND_VOCABULARY.write_text(frontend_vocabulary(codes, values), encoding="utf-8")
        print(f"Wrote {FRONTEND_VOCABULARY.relative_to(REPO_ROOT)}.")
        return 0
    findings = [
        *import_findings(BACKEND, resolver_exports()),
        *vocabulary_findings(BACKEND, codes | values),
        *generic_findings(BACKEND, FRONTEND_SRC, vocabulary),
        *frontend_findings(codes, values),
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
        f"OK: the resolver is imported and its {len(codes)} codes, {len(values)} closed-vocabulary values and "
        f"{len(WATCHED_NAMES)} field names are read "
        "only where the reading modules and the lists allow."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
