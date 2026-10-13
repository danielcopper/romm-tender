#!/usr/bin/env python3
"""Third-party notice gate.

Why the notices are held against the package budgets is this gate's entry in
``docs/architecture/invariants.md``.

It asserts that every package any bundle in :data:`BUDGETS` names has a heading
of its own in :data:`NOTICES`, spelled ``## <package>`` exactly. A package named
only in running text, or under a heading of another level, is not a section of
its own and counts as missing.

**What it does not see:** whether a section is TRUE — the licence, the source
or the bundles it names are read by nobody but a reviewer; and a section for a
package no bundle carries any more, which passes here because a stale notice
claims too much rather than too little; a ``## <package>`` line inside a code
fence, which counts as a section because this reads lines, not Markdown; and
content under another licence inside a package — the Font Awesome icons inside
react-icons — which no check sees at all.

Exit 0 when every budgeted package has its section, 1 otherwise, one line per
package and every package at once.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = ROOT / "frontend" / "package-budgets.json"
NOTICES = ROOT / "THIRD-PARTY-NOTICES.md"

_SECTION_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


def budgeted_packages(budgets: Path) -> set[str]:
    """Every package any bundle in ``budgets`` carries.

    Raises ``ValueError`` for a file that is not a mapping of bundles to
    mappings of packages, so a budgets file this cannot read is refused rather
    than read as one that lists nothing.
    """
    data = json.loads(budgets.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(bundle, dict) for bundle in data.values()):
        raise ValueError("not a mapping of bundles to their package budgets")
    return {package for bundle in data.values() for package in bundle}


def sections(notices: Path) -> set[str]:
    """The name of every ``## `` heading in ``notices``."""
    return set(_SECTION_RE.findall(notices.read_text(encoding="utf-8")))


def main() -> int:
    try:
        packages = budgeted_packages(BUDGETS)
        headed = sections(NOTICES)
    except (OSError, ValueError) as error:
        print(f"ERROR: cannot read the budgets or the notices — {error}", file=sys.stderr)
        return 1

    missing = sorted(packages - headed)
    if missing:
        print(f"ERROR: a bundle carries a package {NOTICES.name} has no section for:", file=sys.stderr)
        for package in missing:
            print(f"  {package}: no '## {package}' heading", file=sys.stderr)
        print(
            f"\nEvery package in {BUDGETS.name} is distributed inside a bundle,\n"
            f"so its licence notice belongs in {NOTICES.name} under a heading of its own.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: every package a bundle carries has its section in {NOTICES.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
