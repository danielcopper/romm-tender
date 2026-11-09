#!/usr/bin/env python3
"""Hold the invariant register to its size, and every entry to one home.

The register's entries live in one file per area under
``docs/architecture/invariants/``; ``CLAUDE.md`` keeps a register section with
the map from area to file and the few rules its criterion keeps there. Three
limits (#2757 D4):

* the register section of ``CLAUDE.md`` — from its ``## Invariant register``
  heading to the next ``## `` heading — at most ``SECTION_LIMIT`` characters;
* an entry's bold headline at most ``HEADLINE_LIMIT`` characters (the rest of a
  longer rule stands in the paragraph directly under it, which has no limit);
* one area file at most ``AREA_FILE_LIMIT`` characters, beyond which the area is
  split.

Beside them it holds the one-home rule: no headline stands twice across the
area files, and no area file's headline stands anywhere in ``CLAUDE.md``. A
second ``## Invariant register`` heading in ``CLAUDE.md`` fails, and so does an
entry-shaped item anywhere outside the section: a bold list item at the left
margin, under any marker, whose headline is followed by `` — `` (or `` (…) — ``)
and one of the tier words ``check``, ``test`` or ``prompt-only``.

An entry is a list item that starts at the left margin with ``- **``; its
headline runs to the first ``**`` followed by `` — ``, `` (`` or the end of the
paragraph that leaves an even number of ``**`` inside the headline, so a bold
word within it (``**and**``) stays part of it. Lines the formatter wrapped are
joined with one space before anything is measured. Lines inside a fenced code
block are not read; a fence closes only on a run of its own character at least
as long as the one that opened it, with nothing after it, and a file that ends
inside an open fence fails. A bold list item under another marker (``* **``,
``+ **``, ``1. **``) fails as malformed, and so does any file in the area folder
but a ``.md`` file directly in it. Characters are Python ``str`` characters,
not bytes. A missing section, a missing area folder and an area folder with no
entries fail rather than pass.

What it does not see is listed at its entry in
``docs/architecture/invariants/cross-cutting.md``, the one home of that list.

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

CRITERION_POINTER = (
    "Only a rule that meets the criterion CLAUDE.md's register section states stays there; every other entry "
    "goes to its area's file under docs/architecture/invariants/."
)

_CLOSE_CANDIDATE = re.compile(r"\*\*(?= — | \(|$)")
_ITEM = re.compile(r"^(?P<marker>[-*+]|\d+[.)]) (?=\*\*)")
_TIER_AFTER = re.compile(r"^(?: \([^)]*\))? — (?:check|test|prompt-only)\b")
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<run>`{3,}|~{3,})")


@dataclass(frozen=True)
class Entry:
    """One register entry: where it stands and its bold headline, wraps joined."""

    place: str
    line: int
    headline: str
    after: str


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _unfenced(lines: list[str]) -> tuple[list[bool], int | None]:
    """For each line, whether it stands outside a fenced code block; and the index of a fence never closed."""
    outside: list[bool] = []
    opened: int | None = None
    run = ""
    for index, line in enumerate(lines):
        if opened is None:
            match = _FENCE_OPEN.match(line)
            if match:
                opened, run = index, match.group("run")
            outside.append(match is None)
            continue
        outside.append(False)
        stripped = line.strip()
        if stripped and set(stripped) == {run[0]} and len(stripped) >= len(run) and len(line) - len(line.lstrip()) <= 3:
            opened = None
    return outside, opened


def _close(joined: str) -> int | None:
    """Where the headline in ``joined`` (which opens with ``**``) closes, if it does."""
    for candidate in _CLOSE_CANDIDATE.finditer(joined, 2):
        if joined[2 : candidate.start()].count("**") % 2 == 0:
            return candidate.start()
    return None


def register_sections(text: str) -> list[tuple[int, str]]:
    """Every register section of ``CLAUDE.md``, each with its first line number."""
    lines = text.splitlines(keepends=True)
    outside, _ = _unfenced([line.rstrip("\n") for line in lines])
    sections: list[tuple[int, str]] = []
    for start, line in enumerate(lines):
        if not (outside[start] and line.startswith(SECTION_HEADING)):
            continue
        end = next(
            (i for i in range(start + 1, len(lines)) if outside[i] and lines[i].startswith("## ")),
            len(lines),
        )
        sections.append((start + 1, "".join(lines[start:end])))
    return sections


def _items(text: str, place: str, first_line: int) -> tuple[list[tuple[Entry | None, str, int]], list[str]]:
    """Every bold list item at the left margin — its entry if its headline closes, its marker, its line — and the
    findings about the text's fences."""
    lines = text.splitlines()
    outside, opened = _unfenced(lines)
    items: list[tuple[Entry | None, str, int]] = []
    for index, line in enumerate(lines):
        match = _ITEM.match(line) if outside[index] else None
        if match is None:
            continue
        paragraph = [line[match.end() :]]
        for following in lines[index + 1 :]:
            if not following.startswith(" ") or not following.strip():
                break
            paragraph.append(following.strip())
        joined = " ".join(paragraph)
        close = _close(joined)
        number = first_line + index
        entry = None if close is None else Entry(place, number, joined[2:close], joined[close + 2 :])
        items.append((entry, match.group("marker"), number))
    fences = [] if opened is None else [f"{place}:{first_line + opened}: a fenced code block that never closes"]
    return items, fences


def entries(text: str, place: str, first_line: int = 1) -> tuple[list[Entry], list[str]]:
    """Every entry in ``text`` with its headline, and every item that looks like one but is not well formed."""
    items, malformed = _items(text, place, first_line)
    found: list[Entry] = []
    for entry, marker, number in items:
        if marker != "-":
            malformed.append(f"{place}:{number}: a bold list item under another marker than '- ' — an entry is a '- ' item.")
        elif entry is None:
            malformed.append(f"{place}:{number}: an entry whose bold headline never closes")
        else:
            found.append(entry)
    return found, malformed


def _claude_findings(claude_text: str) -> tuple[list[Entry], list[str], int]:
    """The section's entries, the findings about CLAUDE.md itself, and the section's length."""
    failures: list[str] = []
    sections = register_sections(claude_text)
    if not sections:
        return [], [f"{CLAUDE_MD}: no '{SECTION_HEADING}' section — the gate has nothing to measure."], 0
    first_line, section = sections[0]
    failures.extend(
        f"{CLAUDE_MD}:{line}: a second register section — the register has one section in CLAUDE.md."
        for line, _ in sections[1:]
    )
    if len(section) > SECTION_LIMIT:
        failures.append(
            f"{CLAUDE_MD}: the register section is {len(section):,} characters, over its "
            f"{SECTION_LIMIT:,}-character limit. {CRITERION_POINTER}"
        )
    found, malformed = entries(section, CLAUDE_MD, first_line)
    failures.extend(malformed)

    section_lines = range(first_line, first_line + section.count("\n"))
    every, fences = _items(claude_text, CLAUDE_MD, 1)
    failures.extend(fences)
    failures.extend(
        f"{CLAUDE_MD}:{entry.line}: a register entry outside the register section. {CRITERION_POINTER}"
        for entry, _, _ in every
        if entry is not None and entry.line not in section_lines and _TIER_AFTER.match(entry.after)
    )
    return found, failures, len(section)


def main() -> int:
    failures: list[str] = []

    claude_text = (ROOT / CLAUDE_MD).read_text(encoding="utf-8")
    claude_entries, claude_failures, section_length = _claude_findings(claude_text)
    failures.extend(claude_failures)
    every_entry: list[Entry] = list(claude_entries)

    folder = ROOT / AREA_DIR
    area_files: list[Path] = []
    if not folder.is_dir():
        failures.append(f"{AREA_DIR}/: no such folder — the gate has nothing to measure.")
    else:
        for path in sorted(folder.rglob("*")):
            directly_in = path.parent == folder
            if path.is_file() and directly_in and path.suffix == ".md":
                area_files.append(path)
            elif directly_in or path.is_file():
                place = path.relative_to(ROOT).as_posix()
                failures.append(f"{place}: the area folder holds only .md files, directly in it.")

    area_entries: list[Entry] = []
    for path in area_files:
        place = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if len(text) > AREA_FILE_LIMIT:
            failures.append(
                f"{place}: {len(text):,} characters, over the {AREA_FILE_LIMIT:,}-character limit for one "
                f"area file. Split the area into two files, and name both in CLAUDE.md's map, in "
                f"{AREA_DIR}/index.md and in mkdocs.yml's nav."
            )
        found, malformed = entries(text, place)
        area_entries.extend(found)
        failures.extend(malformed)
    if folder.is_dir() and not area_entries:
        failures.append(f"{AREA_DIR}/: no register entries found — the gate has nothing to measure.")
    every_entry.extend(area_entries)

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
        first = homes.setdefault(_normalise(entry.headline), entry)
        if first is not entry:
            failures.append(
                f"{entry.place}:{entry.line}: this headline also stands at {first.place}:{first.line} — an entry "
                f"has one home. {CRITERION_POINTER}"
            )
    claude_flat = _normalise(claude_text)
    failures.extend(
        f"{entry.place}:{entry.line}: this headline also stands in {CLAUDE_MD} — an entry has one home. "
        f"{CRITERION_POINTER}"
        for entry in area_entries
        if _normalise(entry.headline) in claude_flat
    )

    failures = list(dict.fromkeys(failures))
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
        f"OK: register section {section_length:,}/{SECTION_LIMIT:,} characters, {len(every_entry)} entries "
        f"each in one place with a headline within {HEADLINE_LIMIT}, {len(area_files)} files in {AREA_DIR}/ "
        f"within {AREA_FILE_LIMIT:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
