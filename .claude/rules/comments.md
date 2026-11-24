---
paths:
  - "backend/**/*.py"
  - "frontend/src/**/*.ts"
  - "frontend/src/**/*.tsx"
  - "scripts/**/*.py"
  - "tests/**/*.py"
---

# Comments — the exception, never the default

No mechanical check exists for any of this. It holds only if it is carried at the point of writing.

This file covers every comment in Python and TypeScript alike: inline comments, Python docstrings, JSDoc and a module's
header comment. When one is written and what it may say is this file's; `python-conventions.md` adds only what is
Python's alone.

Code names the **what**. A comment that restates the code is deleted, not improved. If a comment is needed to explain
what the code does, the code is the problem — fix the code and drop the comment.

A comment earns its place only by carrying knowledge that **cannot be put into code**:

- **A fact about the outside world** — another program's behavior, a protocol quirk, a kernel or driver constraint. Name
  the source (file, symbol, version, issue) so a later reader can re-verify it rather than trust it.
- **A road not taken** — the obvious alternative and why it is wrong. Without it the next person builds it.
- **A constraint the code cannot express** — an ordering that must hold, a call that must never happen twice.

An inline comment says it in one line, unless it is named elsewhere as the home of a fact (see "One home, and no
history").

A comment that would be equally true on a `docs/` page belongs on that page, under the heading that owns the topic.
Inline is the last resort, not the first.

## Docstrings and JSDoc

**A function's or method's docstring states a contract its signature, names and types do not show** — what it raises or
rejects and when, what a caller must hold before the call, a side effect beyond the return value. Without such a
contract there is no docstring. It never restates the parameters, their types or the return value: the signature already
carries them, and a copy drifts from it unseen. An `Args:` / `Returns:` section or a `@param` / `@returns` tag that
repeats the signature is such a copy.

**A module or class docstring, or a module's header comment, is written only where the purpose is not clear from the
name and the place.** When it is, it names what belongs there, not what the file holds today — a listing of the current
contents rots when the next one lands.

- Bad: `"""Owns save_sync_state.json — persistence, migrations, default construction."""` (wrong once a fourth
  responsibility lands)
- Good: `"""Owns save_sync_state.json — single source of truth for on-disk save-sync state."""`

## One home, and no history

**A fact has one home.** State it once — on the `docs/` page that owns the topic if a person needs it, at the one site
that depends on it if only the code does — and let every other place name that home instead of repeating it. Copies are
edited one at a time and nothing compares them: one measurement stated in four files drifted to a different number in
two of them.

**A comment a `docs/` page or a rule file names as the home of a fact stays**, however long and whatever its kind — a
docstring, a multi-line comment above a line, a module's header comment. Examples: a script's blind spots, the scope of
the test isolation in `tests/conftest.py`, the convention in `lib/list_result.py`, the bounds above `PALETTES` in
`scripts/logo/gen.py`, the userinfo refusal above `ENTERED_ADDRESS_WITH_USERINFO_MESSAGE` in `backend/lib/url_host.py`,
the header of `frontend/src/utils/runningApps.ts`, what a `NamedDomainRefused` subclass declares. It is a contract no
signature shows, and cutting it strands the pointer.

**History goes in the commit, not in the file.** How a value was arrived at — the measurement behind it, the incident
that prompted it, the round that found it — is true of the moment it was written, and a commit message is dated and
attached to its diff, so it stays true. The same sentence in source is read as current truth for as long as it stands.
Keep the value the code depends on and what it means; leave the story of finding it to the commit.

The difference between a source and a history is what the reference is FOR. An issue named as the source of an
outside-world fact lets a reader re-verify the fact — keep it. An issue or PR named as where something happened, or
where an open question is tracked, stops meaning anything when that thread closes — drop it, and say what is unknown
rather than where it is filed.

Avoid entirely: "mechanical extraction from X", "during the transition", "moved from Y", "added for the Z flow", "see PR
#123", "measured in #456", "on this cut's device list" — commit-message content that rots in source.

## Where a change touches code

**When you touch a line, re-read the comment above it and the docstring of the function or class it sits in.** One that
no longer matches is corrected in the same change; one that fails this file is cut to what the code cannot show, or
deleted — except a comment named elsewhere as the home of a fact, which keeps its length and its place. A stale comment
is worse than none, because it is believed: it is read as current truth by everyone who arrives later, and nothing in
the toolchain will ever contradict it.

Comments and docstrings in code no change touches wait for the change that does — they are never removed in a sweep of
their own.
