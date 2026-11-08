#!/usr/bin/env python3
"""Hold the invariant register to its size, and every entry to one home.

The register's entries live in one file per area under
``docs/architecture/invariants/``; ``CLAUDE.md`` keeps only the map from area to
file and the few rules whose breach loses user data for good (#2757 D2). That
split was made once before, on 2026-09-24, as one line per rule in
``CLAUDE.md`` — and the section grew back from 39k to 117k characters in six
weeks, because nothing measured it. This gate measures it. Three limits
(#2757 D4):

* the register section of ``CLAUDE.md`` — from its ``## Invariant register``
  heading to the next ``## `` heading — at most ``SECTION_LIMIT`` characters;
* an entry's bold headline at most ``HEADLINE_LIMIT`` characters (the rest of a
  longer rule stands in the paragraph directly under it, which has no limit);
* one area file at most ``AREA_FILE_LIMIT`` characters, beyond which the area is
  split.

Beside them it holds the one-home rule: no headline stands twice, across the
area files and ``CLAUDE.md``'s section together.

An entry is a list item that starts at the left margin with ``- **``; its
headline runs to the first ``**`` followed by `` — ``, `` (`` or the end of the
paragraph, so a bold word inside a headline (``**and**``) stays part of it.
Lines the formatter wrapped are joined with one space before anything is
measured. Characters are Python ``str`` characters, not bytes.

It reads text, so it misses an entry not written as such a list item — a rule
set as a paragraph or a heading is neither measured nor compared — and two
headlines that state one rule in different words. A section heading renamed
away from ``## Invariant register`` and an area folder with no entries both
fail rather than pass, so the gate cannot go quiet by losing its input.

Usage:
    python scripts/check_invariant_register.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = "CLAUDE.md"
AREA_DIR = "docs/architecture/invariants"
SECTION_HEADING = "## Invariant register"

SECTION_LIMIT = 6_000
HEADLINE_LIMIT = 300
AREA_FILE_LIMIT = 40_000

D2_REMINDER = (
    "Only a rule whose breach loses user data for good, that any area can break and that no check holds "
    "fully stays in CLAUDE.md; every other entry goes to its area's file under docs/architecture/invariants/."
)

_HEADLINE_END = re.compile(r"\*\*(?= — | \(|$)")


@dataclass(frozen=True)
class Entry:
    """One register entry: where it stands and its bold headline, wraps joined."""

    place: str
    line: int
    headline: str


def register_section(text: str) -> str | None:
    """The register section of ``CLAUDE.md``: its heading up to the next ``## `` heading."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith(SECTION_HEADING)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "".join(lines[start:end])


def entries(text: str, place: str, first_line: int = 1) -> tuple[list[Entry], list[str]]:
    """Every entry in ``text`` with its headline, and every entry whose headline does not close."""
    lines = text.splitlines()
    found: list[Entry] = []
    malformed: list[str] = []
    for index, line in enumerate(lines):
        if not line.startswith("- **"):
            continue
        paragraph = [line[2:]]
        for following in lines[index + 1 :]:
            if not following.startswith("  ") or not following.strip():
                break
            paragraph.append(following.strip())
        joined = " ".join(paragraph)
        close = _HEADLINE_END.search(joined, 2)
        number = first_line + index
        if close is None:
            malformed.append(f"{place}:{number}: an entry whose bold headline never closes")
            continue
        found.append(Entry(place, number, joined[2 : close.start()]))
    return found, malformed


def main() -> int:
    failures: list[str] = []
    every_entry: list[Entry] = []

    claude_text = (ROOT / CLAUDE_MD).read_text(encoding="utf-8")
    section = register_section(claude_text)
    if section is None:
        failures.append(f"{CLAUDE_MD}: no '{SECTION_HEADING}' section — the gate has nothing to measure.")
    else:
        if len(section) > SECTION_LIMIT:
            failures.append(
                f"{CLAUDE_MD}: the register section is {len(section):,} characters, over its "
                f"{SECTION_LIMIT:,}-character limit. {D2_REMINDER}"
            )
        section_line = claude_text[: claude_text.index(section)].count("\n") + 1
        found, malformed = entries(section, CLAUDE_MD, section_line)
        every_entry.extend(found)
        failures.extend(malformed)

    area_files = sorted((ROOT / AREA_DIR).glob("*.md"))
    area_entries = 0
    for path in area_files:
        place = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if len(text) > AREA_FILE_LIMIT:
            failures.append(
                f"{place}: {len(text):,} characters, over the {AREA_FILE_LIMIT:,}-character limit for one "
                f"area file. Split the area into two files, and name both in CLAUDE.md's map and in "
                f"{AREA_DIR}/index.md."
            )
        found, malformed = entries(text, place)
        area_entries += len(found)
        every_entry.extend(found)
        failures.extend(malformed)
    if area_entries == 0:
        failures.append(f"{AREA_DIR}/: no register entries found — the gate has nothing to measure.")

    for entry in every_entry:
        if len(entry.headline) > HEADLINE_LIMIT:
            failures.append(
                f"{entry.place}:{entry.line}: a bold headline of {len(entry.headline)} characters, over the "
                f"{HEADLINE_LIMIT}-character limit. Keep the headline to the rule's own core and move the rest of "
                f"the rule, word for word, into the paragraph directly under it: "
                f"'{entry.headline[:80]}…'"
            )

    homes: dict[str, Entry] = {}
    for entry in every_entry:
        key = " ".join(entry.headline.split())
        first = homes.setdefault(key, entry)
        if first is not entry:
            failures.append(
                f"{entry.place}:{entry.line}: this headline also stands at {first.place}:{first.line} — an entry "
                f"has one home. {D2_REMINDER}"
            )

    if failures:
        print(
            f"ERROR: invariant-register gate failed ({len(failures)} finding(s)) — see "
            "scripts/check_invariant_register.py:",
            file=sys.stderr,
        )
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(
        f"OK: register section {len(section or ''):,}/{SECTION_LIMIT:,} characters, {len(every_entry)} entries "
        f"each in one place with a headline within {HEADLINE_LIMIT}, {len(area_files)} area files within "
        f"{AREA_FILE_LIMIT:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
