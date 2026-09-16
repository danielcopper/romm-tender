#!/usr/bin/env python3
"""Coverage-exclusion gate — an exclusion names a PROPERTY of the code, never a place.

Coverage exclusions live in two lists that must agree: ``coverage.exclude`` in
``frontend/vitest.config.ts`` (what the local and CI coverage report leaves out)
and ``sonar.coverage.exclusions`` in ``sonar-project.properties`` (what the
SonarCloud quality gate leaves out). Both are hand-maintained, and neither knows
the other exists.

**The two spell the same entry differently, and that is not drift.** Sonar runs
from the repository root; Vitest runs from ``frontend/``, so its paths are
relative to that package — ``src/types/**`` there is ``frontend/src/types/**``
here. Every declaration below is written repo-relative, and each Vitest entry is
normalised onto that footing before anything is compared (:func:`repo_relative`).
An entry that is already repo-relative on the Vitest side fails the comparison
loudly rather than passing, which is the direction that wants to be loud.

Two things went wrong before this gate, and both were silent.

The first is the one the rule is named for. ``src/patches/**`` excluded a FOLDER,
so when ``metadataPatches.ts`` was moved out of it the exclusion stayed behind
with the folder and nothing said so — a file's coverage obligation changed
because of where it was filed. An exclusion earns its place by naming something
true of the code (a CSS payload has nothing to assert; a monkey-patch into
Steam's router has no subject outside the device), and that reason therefore
belongs to the file, not to its parent directory.

The second is drift between the two lists: ``steamShortcuts.ts`` sat on Sonar's
list and not on Vitest's, under a comment in the Vitest config claiming the two
were aligned.

So the reason each excluded file carries is written IN that file, as a marker
comment in its first five lines:

    // coverage-exempt: <why this file has nothing a test could assert>

and this gate asserts:

1. every frontend-scoped entry appears in BOTH lists — the backend- and
   config-scoped entries are Sonar-only by design and the frontend test glob is
   Vitest-only, each declared below with its reason, so the asymmetry is stated
   rather than tolerated;
2. every SHARED entry names a path that exists — the declared asymmetries are
   checked for membership in their list only, never for existence, so a
   Sonar-only entry naming a directory that has been deleted passes green;
3. every listed FILE carries the marker;
4. every file carrying the marker is listed — the direction that catches a
   marked file dropped from a list, or moved to where a folder glob no longer
   reaches it.

A folder entry is admitted only from ``FOLDER_ENTRIES`` below, where membership
in the folder IS the property; anything else must name a file.

What this gate deliberately does NOT do, and the first one is the gate's own
subject: **it never verifies MEMBERSHIP in a folder entry's directory.** The
two admitted folders are checked to exist and their contents are not read at
all, so a real module moved into ``frontend/src/types/`` or
``frontend/src/test-utils/`` is exempted by its PLACE — no marker asked for, no
failure — which is the accident named above, still live for those two
directories. That is a declared blind spot rather than an oversight: "is this
really only a type declaration" is not a cheap check, and a half-check
(no-functions, say) would exempt on a property nobody stated and read as
enforcement. Review is what closes it. Beyond that, it does not read the
marker's reason, so a marker whose sentence is false passes green — only review
catches that too, and writing a true one is the whole point of putting it where
the code is. It does not check that an excluded file is in fact hard to test (no
coverage run happens here), and it does not look at ``sonar.exclusions`` —
dropping a file from analysis altogether is a different decision from dropping
it from the coverage ratio, and this list is the second one.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

VITEST_CONFIG = Path("frontend/vitest.config.ts")
SONAR_PROPERTIES = Path("sonar-project.properties")

# What the Vitest config's paths are relative to. Its entries name paths inside
# the frontend package because that is the directory Vitest runs in; every
# declaration in this file, and Sonar's whole list, is repo-relative.
VITEST_ROOT = "frontend/"

MARKER = "// coverage-exempt:"
# The marker rides above the file's own docstring/imports, so it is found
# without reading the whole file — and a diff that touches the head of the file
# cannot miss it.
MARKER_WINDOW = 5

# Entries a folder glob may legitimately carry, because membership in the folder
# IS the property being excluded. Everything else names a file. Membership
# itself is NOT checked — see the module docstring: a module filed here is
# exempted by its place, and only review notices.
FOLDER_ENTRIES = {
    "frontend/src/types/**": (
        "type declarations and the literal constants their types are derived from — "
        "no functions and no branches, so there is nothing to assert"
    ),
    "frontend/src/test-utils/**": (
        "shared test harnesses, reached only from the test run — by the tests and by "
        "frontend/src/test-setup.ts, which wires the api/host mock in"
    ),
}

# Declared asymmetries. Each key must appear in the named list and must NOT be
# expected in the other one.
SONAR_ONLY = {
    "**/tests/**": "the Python suite — Vitest's coverage scope is frontend/src only",
    "**/conftest.py": "pytest fixtures — Python, as above",
    "backend/_vendor/**": "vendored third-party Python — not our code, and not TypeScript",
    "frontend/*.config.js": "the frontend package's build/test/lint config — outside Vitest's src include",
    "frontend/*.config.ts": "the frontend package's build/test/lint config — outside Vitest's src include",
}
VITEST_ONLY = {
    "frontend/src/**/*.{test,spec}.{ts,tsx}": (
        "the frontend tests themselves — Sonar takes them out of the coverage ratio "
        "through sonar.test.inclusions instead of through this list"
    ),
}

# Where a marked file may live. The sweep is repo-wide rather than scoped to
# frontend/src, because a marked file that MOVED is exactly the accident this
# gate exists for.
MARKER_SUFFIXES = (".ts", ".tsx", ".js", ".jsx")
MARKER_SKIP = ("node_modules/", "backend/_vendor/", "dist/", "site/", "coverage/")


def repo_relative(entry: str) -> str:
    """A Vitest exclusion entry on the same footing as everything else here.

    Vitest runs in ``frontend/``, so its entries are relative to that package
    and this prepends the package prefix unconditionally. An entry that already
    carries the prefix is NOT special-cased here: doing so would make a
    repo-relative entry — which excludes nothing, because Vitest would resolve
    it to ``frontend/frontend/...`` — compare equal to Sonar's and pass in
    silence. :func:`misspelled_vitest_entries` reports that case by name
    instead.
    """
    return VITEST_ROOT + entry


def misspelled_vitest_entries(raw: list[str]) -> list[str]:
    """Vitest entries written repo-relative, which match nothing.

    The two lists spell shared entries differently on purpose, so this is the
    one confusion the difference invites: ``frontend/src/types/**`` looks right
    beside Sonar's copy and excludes nothing at all.
    """
    return [e for e in raw if e.startswith(VITEST_ROOT)]


def vitest_exclusions(text: str) -> list[str]:
    """The string entries of ``coverage.exclude``, exactly as the file spells them."""
    coverage = text.split("coverage:", 1)
    if len(coverage) != 2:
        raise SystemExit(f"ERROR: {VITEST_CONFIG} has no `coverage:` block")
    block = re.search(r"exclude:\s*\[(.*?)\]", coverage[1], re.DOTALL)
    if block is None:
        raise SystemExit(f"ERROR: {VITEST_CONFIG} `coverage:` block has no `exclude: [...]` array")
    return re.findall(r'"([^"]+)"', block.group(1))


def sonar_exclusions(text: str) -> list[str]:
    """The comma-separated entries of ``sonar.coverage.exclusions``."""
    for raw in text.splitlines():
        if raw.startswith("sonar.coverage.exclusions="):
            value = raw.split("=", 1)[1]
            return [e.strip() for e in value.split(",") if e.strip()]
    raise SystemExit("ERROR: sonar-project.properties has no `sonar.coverage.exclusions=` line")


def tracked_sources(root: Path) -> list[str]:
    """Repo-relative paths of the tracked source files the marker sweep reads."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [
        name
        for name in listed
        if name.endswith(MARKER_SUFFIXES)
        and not any(s in name for s in MARKER_SKIP)
        and (root / name).is_file()
    ]


def carries_marker(path: Path) -> bool:
    head = path.read_text(encoding="utf-8").splitlines()[:MARKER_WINDOW]
    return any(line.strip().startswith(MARKER) for line in head)


def collect_errors(root: Path, source_files: list[str]) -> list[str]:
    """Every way the two lists, the paths they name and the markers disagree."""
    vitest_raw = vitest_exclusions((root / VITEST_CONFIG).read_text(encoding="utf-8"))
    vitest = [repo_relative(e) for e in vitest_raw]
    sonar_text = (root / SONAR_PROPERTIES).read_text(encoding="utf-8")
    sonar = sonar_exclusions(sonar_text)
    errors: list[str] = []

    for entry in misspelled_vitest_entries(vitest_raw):
        errors.append(
            f"'{entry}' in {VITEST_CONFIG} is spelled repo-relative, so it excludes nothing — "
            f"Vitest runs in {VITEST_ROOT.rstrip('/')}/, so drop the '{VITEST_ROOT}' prefix"
        )

    # 1. The two lists agree once the declared asymmetries are taken out.
    for entry, reason in SONAR_ONLY.items():
        if entry not in sonar:
            errors.append(f"'{entry}' is declared Sonar-only ({reason}) but is not in sonar.coverage.exclusions")
        # Both spellings, because normalising alone would miss one: a Sonar-only
        # entry written package-relative (`*.config.js`) is caught by the
        # normalised list, and one written repo-relative (`**/conftest.py`,
        # which no prefix would change) by the raw one. Either way it is this
        # error and not the vaguer drift message below.
        if entry in vitest or entry in vitest_raw:
            errors.append(f"'{entry}' is declared Sonar-only but appears in {VITEST_CONFIG} — update the declaration")
    for entry, reason in VITEST_ONLY.items():
        if entry not in vitest:
            errors.append(f"'{entry}' is declared Vitest-only ({reason}) but is not in {VITEST_CONFIG}")
        # Read against the raw line: Sonar's list is comma-separated, so a glob
        # holding a comma (`{test,spec}`) cannot survive the split — which is
        # also why such an entry cannot be expressed on that side at all.
        if entry in sonar_text:
            errors.append(f"'{entry}' is declared Vitest-only but appears in sonar.coverage.exclusions")

    shared_vitest = [e for e in vitest if e not in VITEST_ONLY and e not in SONAR_ONLY]
    shared_sonar = [e for e in sonar if e not in SONAR_ONLY and e not in VITEST_ONLY]
    for entry in sorted(set(shared_vitest) - set(shared_sonar)):
        errors.append(f"'{entry}' is excluded from the Vitest report but not from Sonar's — the two gates disagree")
    for entry in sorted(set(shared_sonar) - set(shared_vitest)):
        errors.append(f"'{entry}' is excluded from Sonar's gate but not from the Vitest report — the two gates disagree")

    # 2-3. Every shared entry names something that exists, and a file entry
    #      carries its reason where the file can take it along.
    listed_files: set[str] = set()
    for entry in sorted(set(shared_vitest) | set(shared_sonar)):
        if entry in FOLDER_ENTRIES:
            if not (root / entry[: -len("/**")]).is_dir():
                errors.append(f"'{entry}' names a directory that does not exist")
            continue
        if "*" in entry:
            errors.append(
                f"'{entry}' is a glob but not a declared folder entry — an exclusion names a property of the code, "
                "so name the file, or add the folder to FOLDER_ENTRIES with the property its members share"
            )
            continue
        path = root / entry
        if not path.is_file():
            errors.append(f"'{entry}' names a file that does not exist")
            continue
        listed_files.add(entry)
        if not carries_marker(path):
            errors.append(
                f"{entry} is excluded from coverage but carries no '{MARKER}' comment in its first "
                f"{MARKER_WINDOW} lines — the reason belongs in the file, so it moves with it"
            )

    # 4. And nothing carries the marker without being listed.
    marked = {name for name in source_files if carries_marker(root / name)}
    for name in sorted(marked - listed_files):
        errors.append(
            f"{name} carries '{MARKER}' but is on neither exclusion list — either list it in both, "
            "or drop the marker"
        )
    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errors = collect_errors(root, tracked_sources(root))
    if errors:
        print("ERROR: coverage exclusions are out of order:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK: both exclusion lists agree, and every excluded file carries its reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
