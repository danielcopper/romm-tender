---
paths:
  - "backend/**/*.py"
  - "frontend/src/**/*.ts"
  - "frontend/src/**/*.tsx"
  - "scripts/**/*.py"
---

# Comments — the exception, never the default

No mechanical check exists for any of this. It holds only if it is carried at the point of writing.

This file covers every comment, including docstrings and a TypeScript module's header comment. What a Python docstring
must _contain_ — the contract it states — is `python-conventions.md`'s; what no comment may contain is this file's.

Code names the **what**. A comment that restates the code is deleted on sight, not improved. If a comment is needed to
explain what the code does, the code is the problem — fix the code and drop the comment.

A comment earns its place only by carrying knowledge that **cannot be put into code**:

- **A fact about the outside world** — another program's behavior, a protocol quirk, a kernel or driver constraint. Name
  the source (file, symbol, version, issue) so a later reader can re-verify it rather than trust it.
- **A road not taken** — the obvious alternative and why it is wrong. Without it the next person builds it.
- **A constraint the code cannot express** — an ordering that must hold, a call that must never happen twice.

A comment that would be equally true on a `docs/` page belongs on that page, under the heading that owns the topic.
Inline is the last resort, not the first.

**A fact has one home.** State it once — on the `docs/` page that owns the topic if a person needs it, at the one site
that depends on it if only the code does — and let every other place name that home instead of repeating it. Copies are
edited one at a time and nothing compares them: one measurement stated in four files drifted to a different number in
two of them.

**History goes in the commit, not in the file.** How a value was arrived at — the measurement behind it, the incident
that prompted it, the round that found it — is true of the moment it was written, and a commit message is dated and
attached to its diff, so it stays true. The same sentence in source is read as current truth for as long as it stands.
Keep the value the code depends on and what it means; leave the story of finding it to the commit.

The difference between a source and a history is what the reference is FOR. An issue named as the source of an
outside-world fact lets a reader re-verify the fact — keep it. An issue or PR named as where something happened, or
where an open question is tracked, stops meaning anything when that thread closes — drop it, and say what is unknown
rather than where it is filed.

**When you touch a line, re-read the comment above it.** If it no longer matches, correct or delete it in the same
change. A stale comment is worse than none, because it is believed: it is read as current truth by everyone who arrives
later, and nothing in the toolchain will ever contradict it.

Avoid entirely: "mechanical extraction from X", "during the transition", "moved from Y", "added for the Z flow", "see PR
#123", "measured in #456", "on this cut's device list" — commit-message content that rots in source.
