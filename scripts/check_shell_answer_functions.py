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

**The reading is hand-written, so a construct it gets wrong drops real code in
silence** — the failure is a function that is never collected or a body that
ends early, and either way the ``exit`` below it is simply not there. Eleven
such shapes are read for by name:

* a closing ``}`` judged by what FOLLOWS it — an unquoted ``${x}`` or a
  ``find … -exec rm {} \\;`` ended the enclosing function — and a ``}`` written
  as an ARGUMENT (``echo }``), which is why a block brace is decided by what
  stands before it;
* a brace group opened after ``!`` — ``if ! { exec 3< /dev/tty; }`` — where the
  word before is punctuation rather than an identifier, so the ``{`` was not a
  block's while its ``}`` was;
* ``$(( 1 << 3 ))`` read as a heredoc, which blanked the rest of the file, and a
  ``$(( … ))`` span that ended one parenthesis short of the pair it opened;
* a parameter expansion naming a function read as a call to it;
* a ``case`` arm's ``)`` ending the substitution it sits in; the POSIX arm
  written ``(a)``, whose leading parenthesis groups nothing; and ``;&`` and
  ``;;&``, which end an arm as ``;;`` does, so a pattern follows them too;
* a ``( … )`` subshell inside a substitution, whose closing parenthesis would
  otherwise be read as the substitution's own;
* a backtick substitution inside double quotes read as string text.

**The blind spots named here** are the ones left, deliberately or for want of a
reason to close them:

* a function reached through a **variable or ``eval``** — ``cmd=f; x="$($cmd)"``
  names no function this scan can resolve. ``step`` in ``install.sh`` is the
  live example: it reaches its command through ``"$@"``;
* ``( f )`` and ``f | cmd`` — a subshell and a pipeline swallow an ``exit`` the
  same way a substitution does, and neither is checked here. This gate is about
  the shape that has actually gone wrong in this repository; the other two are a
  wider rule nobody has needed yet;
* ``exit`` written inside a **heredoc or a string** — masked with the rest of
  the text, so a heredoc that emits a script containing ``exit`` is not a
  finding, which is right, and a function whose ``exit`` is somehow produced by
  expansion is missed, which is the price. The same goes for a ``$(f)`` written
  inside ``$(( … ))`` or ``${ … }``, both of which are masked whole;
* a function **defined inside another function** — only top-level definitions
  are collected, so a nested one is neither a node in the graph nor a name a
  substitution can be matched against;
* a function **defined twice at the top level** — the last body wins, so a
  definition guarded by an ``if`` is judged by whichever branch is written last
  rather than by both;
* two ``}`` **arguments in a row** — ``echo } }`` — where the second is read as
  a block's, because a ``}`` is also how a block ends with no separator after
  another (``{ { echo one; } }``). One of the two readings has to give, and the
  block is the one that occurs here;
* ``exit`` reached through an **external command or a sourced file** — the graph
  spans one file, and nothing here follows ``source``.

**A call written inside ``$( )`` is not an edge in the call graph**, which is
the gate's own premise read backwards: an ``exit`` there ends the subshell, not
the function around it. So ``outer`` calling ``$(inner)`` does not inherit
``inner``'s exit, and a nested pair is reported once — at the inner
substitution, which is the site to fix.

Exit 0 when no answer-function ends the run, 1 with one line per finding.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent

# The shell this repository ships and runs. The named files are the ones the
# glob cannot see: the two a USER executes, and the launcher, which is bash in
# `bin/` under a name with no extension. The globs pick up the developer scripts
# beside them.
NAMED_FILES: tuple[str, ...] = ("install.sh", "scripts/package.sh", "bin/tender-rom-launcher")
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


class _CaseState:
    """Where one nesting level of a scan sits inside a ``case``.

    What it has to know is small: a ``case`` arm's pattern may open with a
    ``(`` and always ends with a ``)``, and neither groups anything. An arm ends
    at ``;;``, ``;&`` or ``;;&``, and the next pattern follows.

    Two walks need those RULES — the masker, which decides where a substitution
    ends, and the matcher, which decides where it closes — so they live here
    once rather than as two copies that get edited one at a time.

    **What is shared is the rules, not the granularity.** The masker keeps one
    of these per frame, so a ``case`` inside a subshell inside an arm is its own
    question; the matcher keeps ONE for the whole span it walks, so the same
    nesting leaves it answering the outer ``case``'s question with the inner
    one's state. Reaching that needs a ``case`` (or a ``for … in``) nested
    inside a group inside an arm, all within one substitution; giving the
    matcher a stack would close it.
    """

    __slots__ = ("depth", "expecting_pattern")

    def __init__(self) -> None:
        self.depth = 0
        self.expecting_pattern = False

    def step(self, text: str, index: int) -> int | None:
        """Read an arm terminator or a keyword at *index*, answering the index past it.

        ``None`` when there is neither, which leaves the caller to advance.
        """
        for terminator in (";;&", ";;", ";&"):
            if text.startswith(terminator, index):
                self.expecting_pattern = self.depth > 0
                return index + len(terminator)
        if not _starts_a_word(text, index):
            return None
        end = index
        while end < len(text) and (text[end].isalnum() or text[end] in "_-"):
            end += 1
        word = text[index:end]
        if word == "case":
            self.depth += 1
        elif word == "esac":
            self.depth = max(0, self.depth - 1)
            self.expecting_pattern = False
        elif word == "in" and self.depth:
            self.expecting_pattern = True
        return end

    def opens_a_pattern(self) -> bool:
        """Whether a ``(`` here is a pattern's leading paren rather than a subshell."""
        return self.expecting_pattern and self.depth > 0

    def closes_a_pattern(self) -> bool:
        """Whether a ``)`` here ends a pattern rather than a group. Consumes the expectation."""
        if self.expecting_pattern and self.depth > 0:
            self.expecting_pattern = False
            return True
        return False


class _Frame:
    """One nesting level of the masker, and the ``case`` state that level is in.

    Each level keeps its own, because a substitution written inside a ``case``
    arm is not itself inside that arm's pattern.

    A plain class rather than a dataclass: this module is loaded by path as
    often as it is imported, and ``dataclass`` resolves its annotations through
    ``sys.modules``, which a loader that did not register it has not filled in.
    """

    __slots__ = ("case", "kind")

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.case = _CaseState()


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
    stack: list[_Frame] = [_Frame("code")]
    pending_heredocs: list[tuple[str, bool]] = []

    def blank(position: int) -> None:
        if out[position] != "\n":
            out[position] = " "

    while index < length:
        char = source[index]
        frame = stack[-1]
        context = frame.kind

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
            if char == "$" and source.startswith("$((", index):
                index = _skip_arithmetic(source, index, blank)
                continue
            if char == "$" and source.startswith("${", index):
                index = _skip_expansion(source, index, blank)
                continue
            if char == "$" and source.startswith("$(", index):
                stack.append(_Frame("code"))
                index += 2
                continue
            if char == "`":
                # The older spelling of the same thing, and just as live inside
                # double quotes: `x="`f`"` runs f exactly as `x="$(f)"` does.
                stack.append(_Frame("bt"))
                index += 1
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
            stack.append(_Frame("sq"))
            blank(index)
            index += 1
            continue
        if char == '"':
            stack.append(_Frame("dq"))
            blank(index)
            index += 1
            continue
        if char == "`":
            if context == "bt":
                stack.pop()
            else:
                stack.append(_Frame("bt"))
            index += 1
            continue
        if char == "(":
            # A `( … )` subshell is a level of its own. Without pushing one, its
            # closing paren pops the substitution around it and everything after
            # is read as the text beside a substitution rather than as code. It
            # is pushed as "code", because that is what it holds — a kind of its
            # own would lose the escape and comment rules that kind carries. A
            # pattern's leading paren in `in (a)` opens nothing, so it is left
            # for the `)` below to consume.
            if not frame.case.opens_a_pattern():
                stack.append(_Frame("code"))
            index += 1
            continue
        # Before ``$(``, which it starts with, and before the heredoc test,
        # whose ``<<`` it can contain: ``$(( 1 << 3 ))`` used to open a heredoc
        # and blank the rest of the file.
        if source.startswith("$((", index):
            index = _skip_arithmetic(source, index, blank)
            continue
        # A parameter expansion is a word, not code: it can name a function
        # (``${step}``) without calling one, and its braces are not a block's.
        if source.startswith("${", index):
            index = _skip_expansion(source, index, blank)
            continue
        if source.startswith("$(", index):
            stack.append(_Frame("code"))
            index += 2
            continue
        if char == ")":
            # A `case` arm's pattern ends in a `)` that closes nothing. Popping
            # on it ends the substitution at the first arm, and every command
            # after that is read as the text around a substitution, not as code.
            # Asked before the kind is, so a pattern's `)` inside backticks —
            # where nothing would be popped anyway — still ends that pattern.
            ends_a_pattern = frame.case.closes_a_pattern()
            if not ends_a_pattern and context == "code" and len(stack) > 1:
                stack.pop()
            index += 1
            continue
        if _opens_a_heredoc(source, index):
            index = _note_heredoc(source, index, pending_heredocs)
            continue
        stepped = frame.case.step(source, index)
        if stepped is not None:
            index = stepped
            continue
        index += 1

    return "".join(out)


def _opens_a_comment(source: str, index: int) -> bool:
    """A ``#`` starts a comment only at the start of a word."""
    return index == 0 or source[index - 1] in " \t\n;&|()"


def _opens_a_heredoc(source: str, index: int) -> bool:
    """Whether a heredoc starts at *index*.

    ``<<``, not ``<<<``, and only where a redirection may begin: after
    whitespace, after another command's end, or after a file descriptor number.
    """
    if not source.startswith("<<", index) or source.startswith("<<<", index):
        return False
    before = source[index - 1] if index else "\n"
    return before in " \t\n;&|()" or before.isdigit()


def _skip_arithmetic(source: str, index: int, blank: Callable[[int], None]) -> int:
    """Blank a ``$(( … ))`` and answer the index past it.

    Blanked whole rather than read: what is inside is arithmetic, and the one
    thing it could carry that this gate wants — a ``$(f)`` in a default — is
    rare enough to name as a blind spot rather than to parse for.

    The walk starts at the FIRST of the two parens, not the second. Starting one
    later leaves the closing pair one short, and the stray ``)`` then pops the
    substitution the arithmetic sits in (a silent miss) or opens a command
    position beside it (a false alarm).
    """
    depth = 0
    position = index + 1
    while position < len(source):
        if source[position] == "(":
            depth += 1
        elif source[position] == ")":
            depth -= 1
            if depth == 0:
                break
        position += 1
    end = min(position + 1, len(source))
    for cursor in range(index, end):
        blank(cursor)
    return end


def _skip_expansion(source: str, index: int, blank: Callable[[int], None]) -> int:
    """Blank a ``${ … }`` and answer the index past it."""
    depth = 0
    position = index + 1
    while position < len(source):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                break
        position += 1
    end = min(position + 1, len(source))
    for cursor in range(index, end):
        blank(cursor)
    return end


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


def _skip_heredoc(source: str, index: int, delimiter: str, strip_tabs: bool, blank: Callable[[int], None]) -> int:
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
    """Whether the brace at *index* opens or closes a block rather than something else.

    A block brace is a word in COMMAND position: bash requires it, and it is
    what none of the others can be — the `}` of ``find … -exec rm {} \\;`` is
    inside a word, and the `}` of ``echo }`` is an argument. An opening brace
    additionally has to be followed by a separator, which is what keeps the
    ``{`` of ``{}`` out. (``${var}`` never reaches here: the masker blanks a
    parameter expansion whole.)

    The two admit different neighbours, and the difference is exactly ``{}``: an
    opening brace may follow an OPENING one — ``{ { echo; }; }`` — and a closing
    one may not, or the `}` of ``rm {}`` would close whatever was open.
    """
    if text[index] == "{":
        after = text[index + 1] if index + 1 < len(text) else "\n"
        if after not in " \t\n":
            return False
        # A function header's brace follows a NAME rather than a separator —
        # `function reader {` — so the header is asked about directly.
        return _in_command_position(text, index, after_brace=True) or _definition_name(text, index) is not None
    return _in_command_position(text, index, after_brace=False)


# What a command can end with, so the next word begins one. `}` is among them:
# `{ { echo one; } }` closes two blocks with no separator between them.
_COMMAND_ENDERS = ";&|()\n}"


def _in_command_position(text: str, index: int, *, after_brace: bool) -> bool:
    """Whether the token at *index* begins a command rather than continuing one.

    Read BACKWARDS, over the spaces in between: what stands before decides,
    because a word that continues a command — ``echo }`` — reads the same
    forwards as one that opens a block. :data:`TRANSPARENT` answers the rest,
    being the words this module already keeps for "a command follows".
    """
    cursor = index - 1
    while cursor >= 0 and text[cursor] in " \t":
        cursor -= 1
    if cursor < 0:
        return True
    char = text[cursor]
    # `!` is the one entry in TRANSPARENT that is not word-shaped, so the word
    # read below can never reach it — and `if ! { exec 3< /dev/tty; }` opens a
    # block right after one. Without it that `{` is not a block brace while its
    # `}` is, and the enclosing function ends at the inner brace.
    if char in _COMMAND_ENDERS or char == "!" or (after_brace and char == "{"):
        return True
    end = cursor + 1
    start = end
    while start and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    return text[start:end] in TRANSPARENT


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
        after = text[index] if index < length else "\n"
        if not at_command:
            continue
        if after == ")":
            # A `case` pattern (`abort) …`) or the end of a subshell, neither of
            # which calls anything. A substitution's own body never ends in `)`,
            # because the scan hands over the text inside it.
            at_command = False
            continue
        if "=" in word and word.split("=", 1)[0].replace("_", "a").isalnum():
            continue  # an assignment prefix; the next word still opens the command
        if word in TRANSPARENT:
            continue
        words.append((word, offset + start))
        at_command = False
    return words


def _substitutions(text: str) -> list[tuple[int, int, int]]:
    """Every ``$( )`` and backtick substitution: ``(index, body_start, body_end)``."""
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
    """Index of the ``)`` closing the ``(`` at *opening*, or ``None``.

    A ``case`` arm's pattern ends in a ``)`` that closes nothing, so counting
    parentheses alone ends a substitution at the first arm of any ``case``
    inside it and everything after that goes unread. What tells the two apart is
    position: a pattern's ``)`` follows ``in`` or ``;;`` within a ``case``, and
    the scan tracks exactly that much.
    """
    depth = 0
    case = _CaseState()
    index = opening
    length = len(text)
    while index < length:
        char = text[index]
        if char == "(":
            # A pattern's leading paren in `in (a)` groups nothing, so counting
            # it leaves the depth one high and the substitution never closes.
            if not case.opens_a_pattern():
                depth += 1
            index += 1
            continue
        if char == ")":
            if case.closes_a_pattern():
                index += 1
                continue
            depth -= 1
            if depth == 0:
                return index
            index += 1
            continue
        stepped = case.step(text, index)
        if stepped is not None:
            index = stepped
            continue
        index += 1
    return None


def _starts_a_word(text: str, index: int) -> bool:
    """Whether an identifier begins at *index* rather than continuing one.

    ``$`` counts as continuing, so a variable named ``$in`` is not read as the
    keyword it spells.
    """
    char = text[index]
    if not (char.isalpha() or char == "_"):
        return False
    before = text[index - 1] if index else " "
    return not (before.isalnum() or before in "_-$")


def _without_substitutions(text: str) -> str:
    """*text* with every substitution's body blanked, same length.

    A call written inside ``$( )`` cannot end the function around it — that is
    the whole premise of this gate, read in the other direction — so those words
    are not edges in the call graph and an ``exit`` among them is not this
    function's. Each such substitution is judged as a site of its own anyway.
    """
    out = list(text)
    for _index, body_start, body_end in _substitutions(text):
        for cursor in range(body_start, body_end):
            if out[cursor] != "\n":
                out[cursor] = " "
    return "".join(out)


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
        body = _without_substitutions(masked.text[body_start:body_end])
        for word, _at in _command_words(body, body_start):
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
    for _index, body_start, body_end in _substitutions(masked.text):
        for word, at in _command_words(masked.text[body_start:body_end], body_start):
            if word not in functions:
                continue
            chain = _exit_chain(word, graph, exits)
            if chain is None:
                continue
            route = " -> ".join([*chain, "exit"])
            line = masked.line_of(at)
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
