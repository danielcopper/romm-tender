#!/usr/bin/env python3
"""Shell answer-function gate: a function whose value is taken with ``$(...)`` never exits.

``exit`` inside a command substitution ends the SUBSHELL the substitution runs
in, not the script. So a shell function that answers with a value — ``x="$(f)"``
— and that ends the run on a bad input does neither: it prints its message,
returns a status nobody reads, and the caller carries straight on with an empty
answer. What follows is a second complaint about the emptiness, or a request
built out of it. Both exit non-zero in the end, which is why it survives a test
that only checks the status.

The rule: **a function whose value is taken with ``$(...)`` answers, and its
caller aborts.** This gate holds the files to it.

``exit`` is not hard-coded to any one helper. A function "ends the run" if it
runs ``exit`` itself or calls another function of the same file that does, so
the abort helper each script happens to have is DERIVED rather than listed, and
a second one added tomorrow is covered on the day it is written.

What is scanned is :data:`NAMED_FILES` plus every ``*.sh`` under
:data:`GLOB_ROOTS` — the shell this repository ships and runs, rather than every
shell file that exists. The scan is surface syntax over a hand-written lexer
(quotes, comments, heredocs, ``$( )`` and backtick nesting), not a bash parser.

**What it cannot see**, and does not pretend to:

* a function reached through a **variable or ``eval``** — ``cmd=f; x="$($cmd)"``
  names no function this scan can resolve;
* ``( f )`` and ``f | cmd`` — a subshell and a pipeline swallow an ``exit`` the
  same way a substitution does, and neither is checked here. This gate is about
  the shape that has actually gone wrong in this repository; the other two are a
  wider rule nobody has needed yet;
* ``exit`` written inside a **heredoc or a string** — masked with the rest of
  the text, so a heredoc that emits a script containing ``exit`` is not a
  finding, which is right, and a function whose ``exit`` is somehow produced by
  expansion is missed, which is the price;
* a function **defined inside another function** — only top-level definitions
  are collected, so a nested one is neither a node in the graph nor a name a
  substitution can be matched against;
* ``exit`` reached through an **external command or a sourced file** — the graph
  spans one file, and nothing here follows ``source``.

Exit 0 when no answer-function ends the run, 1 with one line per finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The shell this repository ships and runs. The two named files are the ones a
# USER executes; the globs pick up the developer scripts beside them.
NAMED_FILES: tuple[str, ...] = ("install.sh", "scripts/package.sh")
GLOB_ROOTS: tuple[str, ...] = ("scripts", "bin")
GLOB_PATTERN = "*.sh"

# Words that stand in front of a command without being the command: a following
# word is still in command position. ``local`` and its relatives are here
# because ``local x="$(f)"`` is the shape this gate is about.
TRANSPARENT: frozenset[str] = frozenset(
    {
        "!",
        "declare",
        "do",
        "elif",
        "else",
        "export",
        "if",
        "local",
        "readonly",
        "then",
        "time",
        "typeset",
        "until",
        "while",
    }
)

# A command position opens after any of these. ``&`` covers ``&&`` and ``|``
# covers ``||``, since one character of each is enough to end a command.
COMMAND_OPENERS = ";&|(){}\n"


class Masked:
    """A script with everything that is not shell CODE blanked out.

    Comments, heredoc bodies and single-quoted strings become spaces. Text
    inside double quotes becomes spaces too — with the exception that a ``$( )``
    opened there is code again, because that is exactly where this repository's
    substitutions live (``x="$(f)"``).

    Offsets and line numbers are preserved: every masked character is replaced
    by a space and every newline is kept, so an index into :attr:`text` is an
    index into the original.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.text = _mask(source)

    def line_of(self, index: int) -> int:
        return self.source.count("\n", 0, index) + 1


def _mask(source: str) -> str:
    """Blank every non-code character of *source*, keeping its length and lines."""
    out = list(source)
    index = 0
    length = len(source)
    # Each entry is a context this scan is inside: "code", "dq" (double quotes),
    # "sq" (single quotes), "bt" (backticks — code, and ended by a backtick).
    stack: list[str] = ["code"]
    pending_heredocs: list[tuple[str, bool]] = []

    def blank(position: int) -> None:
        if out[position] != "\n":
            out[position] = " "

    while index < length:
        char = source[index]
        context = stack[-1]

        if char == "\n":
            index += 1
            while pending_heredocs:
                delimiter, strip_tabs = pending_heredocs.pop(0)
                index = _skip_heredoc(source, index, delimiter, strip_tabs, blank)
            continue

        if context == "sq":
            if char == "'":
                stack.pop()
            blank(index)
            index += 1
            continue

        if char == "\\" and context in {"code", "dq", "bt"} and index + 1 < length:
            if context == "dq":
                blank(index)
                blank(index + 1)
            index += 2
            continue

        if context == "dq":
            if char == '"':
                stack.pop()
                blank(index)
                index += 1
                continue
            if char == "$" and source.startswith("$(", index):
                stack.append("code")
                index += 2
                continue
            blank(index)
            index += 1
            continue

        # "code" or "bt" from here down.
        if char == "#" and _opens_a_comment(source, index):
            while index < length and source[index] != "\n":
                blank(index)
                index += 1
            continue
        if char == "'":
            stack.append("sq")
            blank(index)
            index += 1
            continue
        if char == '"':
            stack.append("dq")
            blank(index)
            index += 1
            continue
        if char == "`":
            if context == "bt":
                stack.pop()
            else:
                stack.append("bt")
            index += 1
            continue
        if source.startswith("$(", index):
            stack.append("code")
            index += 2
            continue
        if char == ")" and len(stack) > 1 and stack[-1] == "code":
            stack.pop()
            index += 1
            continue
        if source.startswith("<<", index) and not source.startswith("<<<", index):
            index = _note_heredoc(source, index, pending_heredocs)
            continue
        index += 1

    return "".join(out)


def _opens_a_comment(source: str, index: int) -> bool:
    """A ``#`` starts a comment only at the start of a word."""
    return index == 0 or source[index - 1] in " \t\n;&|()"


def _note_heredoc(source: str, index: int, pending: list[tuple[str, bool]]) -> int:
    """Record the delimiter of a heredoc opened at *index*, and answer where to carry on."""
    cursor = index + 2
    strip_tabs = False
    if cursor < len(source) and source[cursor] == "-":
        strip_tabs = True
        cursor += 1
    while cursor < len(source) and source[cursor] in " \t":
        cursor += 1
    quote = ""
    if cursor < len(source) and source[cursor] in "'\"":
        quote = source[cursor]
        cursor += 1
    start = cursor
    while cursor < len(source) and (source[cursor].isalnum() or source[cursor] in "_-."):
        cursor += 1
    delimiter = source[start:cursor]
    if quote and cursor < len(source) and source[cursor] == quote:
        cursor += 1
    if delimiter:
        pending.append((delimiter, strip_tabs))
    return cursor


def _skip_heredoc(source: str, index: int, delimiter: str, strip_tabs: bool, blank) -> int:
    """Blank the body of one heredoc and answer the index just past its terminator."""
    length = len(source)
    while index < length:
        end_of_line = source.find("\n", index)
        if end_of_line == -1:
            end_of_line = length
        line = source[index:end_of_line]
        if (line.lstrip("\t") if strip_tabs else line).strip() == delimiter:
            for position in range(index, min(end_of_line + 1, length)):
                blank(position)
            return min(end_of_line + 1, length)
        for position in range(index, min(end_of_line + 1, length)):
            blank(position)
        index = end_of_line + 1
    return length


def _is_block_brace(text: str, index: int) -> bool:
    """Whether the brace at *index* opens or closes a block rather than an expansion.

    A block brace is a word of its own: bash requires it, and ``${var}``'s are
    not, which is what keeps a parameter expansion out of the brace count
    without having to parse one.
    """
    before = text[index - 1] if index else "\n"
    after = text[index + 1] if index + 1 < len(text) else "\n"
    if text[index] == "{":
        return before in " \t\n;&|()" and after in " \t\n"
    return before in " \t\n;&|" or after in " \t\n;&|)"


def _find_functions(masked: Masked) -> dict[str, tuple[int, int, int]]:
    """Top-level functions of the file: ``name -> (line, body_start, body_end)``."""
    text = masked.text
    found: dict[str, tuple[int, int, int]] = {}
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "{" or not _is_block_brace(text, index):
            index += 1
            continue
        name = _definition_name(text, index)
        if name is None or any(start <= index < end for start, end in spans):
            index += 1
            continue
        end = _matching_brace(text, index)
        if end is None:
            index += 1
            continue
        found[name] = (masked.line_of(index), index + 1, end)
        spans.append((index, end))
        index = end
    return found


def _definition_name(text: str, brace: int) -> str | None:
    """The function name a ``{`` at *brace* is the body of, or ``None``."""
    head = text[:brace].rstrip()
    if head.endswith("()"):
        head = head[:-2].rstrip()
        name = _trailing_word(head)
        return name if name and name not in TRANSPARENT else None
    name = _trailing_word(head)
    if not name:
        return None
    before = _trailing_word(head[: len(head) - len(name)].rstrip())
    return name if before == "function" else None


def _trailing_word(text: str) -> str:
    """The identifier *text* ends with, or the empty string."""
    end = len(text)
    start = end
    while start and (text[start - 1].isalnum() or text[start - 1] in "_-"):
        start -= 1
    word = text[start:end]
    return word if word and not word[0].isdigit() else ""


def _matching_brace(text: str, opening: int) -> int | None:
    """Index of the ``}`` closing the block opened at *opening*, or ``None``."""
    depth = 0
    for index in range(opening, len(text)):
        char = text[index]
        if char not in "{}" or not _is_block_brace(text, index):
            continue
        depth += 1 if char == "{" else -1
        if depth == 0:
            return index
    return None


def _command_words(text: str, offset: int) -> list[tuple[str, int]]:
    """Every word in command position within *text*, as ``(word, absolute index)``."""
    words: list[tuple[str, int]] = []
    index = 0
    length = len(text)
    at_command = True
    while index < length:
        char = text[index]
        if char in COMMAND_OPENERS:
            at_command = True
            index += 1
            continue
        if char in " \t":
            index += 1
            continue
        start = index
        while index < length and text[index] not in " \t\n" and text[index] not in COMMAND_OPENERS:
            index += 1
        word = text[start:index]
        if not at_command:
            continue
        if "=" in word and word.split("=", 1)[0].replace("_", "a").isalnum():
            continue  # an assignment prefix; the next word still opens the command
        if word in TRANSPARENT:
            continue
        words.append((word, offset + start))
        at_command = False
    return words


def _substitutions(masked: Masked) -> list[tuple[int, int, int]]:
    """Every ``$( )`` and backtick substitution: ``(index, body_start, body_end)``."""
    text = masked.text
    found: list[tuple[int, int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("$(", index):
            end = _matching_paren(text, index + 1)
            if end is not None:
                found.append((index, index + 2, end))
            index += 2
            continue
        if text[index] == "`":
            end = text.find("`", index + 1)
            if end != -1:
                found.append((index, index + 1, end))
                index = end + 1
                continue
        index += 1
    return found


def _matching_paren(text: str, opening: int) -> int | None:
    """Index of the ``)`` closing the ``(`` at *opening*, or ``None``."""
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _exit_chain(name: str, graph: dict[str, set[str]], exits: set[str]) -> list[str] | None:
    """The shortest call chain from *name* to a function that runs ``exit``, or ``None``."""
    queue: list[list[str]] = [[name]]
    seen = {name}
    while queue:
        chain = queue.pop(0)
        if chain[-1] in exits:
            return chain
        for callee in sorted(graph.get(chain[-1], ())):
            if callee not in seen:
                seen.add(callee)
                queue.append([*chain, callee])
    return None


def scan(path: Path, display: str) -> list[str]:
    """One line per answer-function in *path* that can end the run."""
    masked = Masked(path.read_text(encoding="utf-8"))
    functions = _find_functions(masked)
    if not functions:
        return []

    graph: dict[str, set[str]] = {}
    exits: set[str] = set()
    for name, (_line, body_start, body_end) in functions.items():
        called: set[str] = set()
        for word, _at in _command_words(masked.text[body_start:body_end], body_start):
            if word == "exit":
                exits.add(name)
            elif word in functions and word != name:
                called.add(word)
        graph[name] = called

    # One line per (line, function) rather than per match: a nested substitution
    # is inside its parent's body as well as being one itself, so the same site
    # is reached twice. The cost of collapsing on the line is that two calls to
    # one function written on one line report once — the same line, the same
    # function and the same fix.
    findings: dict[tuple[int, str], str] = {}
    for index, body_start, body_end in _substitutions(masked):
        for word, at in _command_words(masked.text[body_start:body_end], body_start):
            if word not in functions:
                continue
            chain = _exit_chain(word, graph, exits)
            if chain is None:
                continue
            route = " -> ".join([*chain, "exit"])
            line = masked.line_of(at if at > index else index)
            findings[(line, word)] = (
                f"{display}:{line}: $({word} …) — {word} reaches exit via {route}. "
                f"A function whose value is taken with $(...) answers; its caller aborts."
            )
    return [findings[key] for key in sorted(findings)]


def files_in_scope(root: Path | None = None) -> list[tuple[Path, str]]:
    """Every script this gate reads, as ``(path, repo-relative name)``, deduplicated."""
    base = root if root is not None else REPO_ROOT
    seen: dict[str, Path] = {}
    for name in NAMED_FILES:
        path = base / name
        if path.is_file():
            seen[name] = path
    for directory in GLOB_ROOTS:
        for path in sorted((base / directory).glob(GLOB_PATTERN)):
            seen[str(path.relative_to(base))] = path
    return [(seen[name], name) for name in sorted(seen)]


def main() -> int:
    scripts = files_in_scope()
    findings: list[str] = []
    for path, display in scripts:
        findings.extend(scan(path, display))
    if findings:
        print("Answer-functions that can end the run:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print(f"OK: no $()-valued shell function reaches exit ({len(scripts)} script(s) scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
