"""A deliberately small reader for the flat half of a YAML configuration.

Two of the standalone emulators RetroDECK ships keep the paths atlas answers
from in YAML — RPCS3's ``vfs.yml`` maps ``/dev_hdd0/`` to the directory its
save tree hangs off, Vita3K's ``config.yml`` carries ``pref-path`` — and atlas
ships zero runtime dependencies, so there is no YAML library to reach for.

This module reads the part of such a file that can be read *exactly*, in the
same spirit as the Cemu XML read and the melonDS legacy-INI scan: top-level
``key: value`` scalars, quoted or bare, with ``$(Name)`` substitution from
another key in the same file — which is how RPCS3 composes every device path
off ``$(EmulatorDir)``.

Everything else is refused rather than guessed, and the refusal is *named* so a
caller can tell "atlas did not read this" from "this is not set":

- a key whose value is a **nested block, a list or a multi-line scalar** is
  recorded in :attr:`YamlScalars.skipped` by name and not parsed. RPCS3's
  ``/dev_usb***/`` is one of those, and nothing atlas answers needs it, so the
  file still speaks while the reader stays honest about which key it passed
  over. Asking for a skipped key raises rather than answering a default.
- **anchors, aliases, tags and multiple documents** change meaning beyond the
  line they sit on — an alias can make a key elsewhere mean something this
  reader never saw — so they refuse the whole file, not one key.
- a **substitution cycle** refuses rather than looping.
- a key **stated more than once** is read as its first statement, the way a
  yaml-cpp lookup answers it (``node_data::get``'s ``std::find_if`` over the
  pairs, include/yaml-cpp/node/detail/impl.h:118-138 at 2f86d137), and named
  in :attr:`YamlScalars.repeated` so a caller whose program reads the file
  differently can refuse rather than answer the wrong statement.

One thing more is stated rather than read: a key whose value is a **null
node** — the valueless spelling and the four words yaml-cpp folds with it (see
:data:`_NULL_SPELLINGS`) — is named in :attr:`YamlScalars.null`. Its text is
the empty string and that is what it reads as here, but ``key:`` and
``key: ""`` are not the same line, and the program the file belongs to may
make two different values of them — yaml-cpp's *string* conversion answers a
null node with the literal ``null`` and an empty quoted scalar with the empty
string, while its *bool* conversion throws for both — so which one the file
holds is a fact this reader keeps rather than one it collapses.

What this is not: a YAML parser. It does not build a tree, it does not type
values (everything is the text as written), and it will refuse or skip a great
deal of legal YAML. That is the point — the alternative is a half-parser whose
mistakes look like answers.

Pure text in, value object out. No I/O — the machine seam supplies the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# The refusal codes this reader answers with. They name what was found, not
# what the caller wanted, so a message can say which construct stopped the read.
# A *second* document in the stream. The opening ``---`` is harmless and common
# — Vita3K's config.yml carries one — but a second document may redefine every
# key the first one set, and which of the two an emulator reads is not a
# question this reader can answer from the text.
REFUSAL_SECOND_DOCUMENT = "second-document"
REFUSAL_ANCHOR = "anchor-or-alias"
REFUSAL_TAG = "tag"
REFUSAL_SUBSTITUTION_CYCLE = "substitution-cycle"
REFUSAL_SUBSTITUTION_UNKNOWN = "substitution-unknown"
# An indented line under no key at all, or a top-level line that is not a
# ``key: value`` pair. Either way the file is not the flat mapping this reader
# reads, and no line in it can be attributed to a key with confidence.
REFUSAL_NOT_A_FLAT_MAPPING = "not-a-flat-mapping"
# All of them, in one tuple: a caller that states a refusal to its own client
# states one of these and nothing else, and enumerating them is what lets the
# refusal vocabulary be documented and tested rather than described.
REFUSAL_CODES = (
    REFUSAL_SECOND_DOCUMENT,
    REFUSAL_ANCHOR,
    REFUSAL_TAG,
    REFUSAL_SUBSTITUTION_CYCLE,
    REFUSAL_SUBSTITUTION_UNKNOWN,
    REFUSAL_NOT_A_FLAT_MAPPING,
)

# How deep a chain of ``$(Name)`` substitutions may go before the reader calls
# it a cycle. RPCS3's file is one level deep; the bound exists so a malformed
# file refuses instead of spinning.
_MAX_SUBSTITUTION_DEPTH = 8

# Every plain scalar yaml-cpp reads as a null node rather than as text: an
# empty one, ``~``, ``null``, ``Null`` and ``NULL`` (IsNullString,
# null.cpp:13-16, asked of every untagged plain scalar at
# singledocparser.cpp:96-97 — the same lines at both commits the two emulators
# build, Vita3K's external/yaml-cpp@2f86d137 and RPCS3's fork at 51a5d623).
# The case is exact, so ``nUll`` is a scalar, and quoting makes a scalar of any
# of them: ``"null"`` is the four letters it wraps. A trailing comment or
# trailing whitespace leaves the spelling standing, which is why
# :func:`_is_null_node` asks its question of the comment-stripped text — and a
# value that is nothing *but* a comment is the empty spelling, which is the one
# case where the comment is the whole of what follows the colon.
_NULL_SPELLINGS = ("", "~", "null", "Null", "NULL")


@dataclass(frozen=True, slots=True)
class YamlScalars:
    """What the reader established, and what it deliberately did not.

    Four statements about the top-level keys, and they answer four different
    questions. ``values`` holds the scalars it read, substitutions resolved.
    ``skipped`` names the keys whose value was a construct this reader does not
    read — the caller decides whether that matters for the key it wants.
    ``null`` names the keys stated as a null node — the valueless spelling and
    the four words yaml-cpp folds with it, :data:`_NULL_SPELLINGS`, with no
    block under them — in the order the file states them. ``repeated`` names
    the keys the file states more than once, once each and in that same order.

    A key in ``null`` is in ``values`` too, holding the empty string, and that
    is deliberate: what the *text* states there is nothing, which is what this
    reader has always answered, so a caller that has not asked the third
    question reads exactly what it always read. The third statement is for the
    caller that must ask, because the program reading the file need not treat
    the two spellings alike — yaml-cpp's conversion to ``std::string`` answers
    a null node with the literal ``null`` where it answers ``key: ""`` with the
    empty string, while its conversion to ``bool`` throws for both — and an
    answer built on ``values`` alone would state the one file's value of the
    other.

    The first three statements all describe a repeated key's **first**
    statement, because that is the one a yaml-cpp lookup answers with
    (``node_data::get``'s ``std::find_if``, include/yaml-cpp/node/detail/impl.h:118-138
    at 2f86d137); the later ones are not read. The fourth statement is what
    lets a caller whose program reads the file another way — RPCS3 iterates
    every pair and keeps the last it can decode — refuse instead of answering
    a statement its emulator discards.

    ``refusal`` is set exactly when the whole file was refused, and then
    ``values`` is empty: a file carrying an alias may mean something different
    from what its plain lines say, so no line from it is reported.
    """

    values: Mapping[str, str] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()
    null: tuple[str, ...] = ()
    repeated: tuple[str, ...] = ()
    refusal: str | None = None

    def get(self, key: str) -> str | None:
        """The value at *key*, or ``None`` where the file states none.

        Raises :class:`KeyError` for a key this reader skipped: the file *does*
        state something there and the reader did not read it, which is not the
        same fact as an absent key and must never collapse into one.
        """
        if key in self.skipped:
            raise KeyError(
                f"{key!r} is stated as a construct this reader does not read "
                f"(nested block, list or multi-line scalar) — its value is unread, not absent"
            )
        return self.values.get(key)


def _unquote(value: str) -> str:
    """A scalar as written: quotes come off, everything else stays verbatim.

    Only the two quote forms are handled, and only when they wrap the whole
    scalar. No escape processing: a double-quoted YAML scalar may carry
    backslash escapes, and pretending otherwise would silently change a path.
    A value that opens a quote and does not close it is left verbatim, the way
    the melonDS and marker readers leave an unterminated quote — visibly odd
    rather than invented.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _strip_comment(value: str) -> str:
    """A bare scalar's trailing comment, cut the way YAML cuts it.

    ``#`` begins a comment only where whitespace precedes it, so a ``#`` inside
    a word — a path segment, a colour — stays part of the value. *Whitespace*
    is the whole of it, not a space: a tab before the ``#`` opens a comment
    just as well, and looking only for ``" #"`` left the comment in the value.
    """
    for index in range(1, len(value)):
        if value[index] == "#" and value[index - 1].isspace():
            return value[:index]
    return value


def _comment_after_quote(value: str) -> str:
    """A quoted scalar's trailing comment, cut only where the quote closed first.

    ``path: "/tmp/x" # note`` is a quoted path with a comment after it, and the
    quote-wrapping test alone does not see that: the value neither opens and
    closes with the same character nor is unterminated, so it came back with
    its quotes and the comment still attached — a path no emulator would ever
    write. The comment comes off only when a closing quote precedes it and
    whitespace precedes the ``#``, which is the same rule bare scalars follow;
    anything else between the closing quote and the end of the line is left
    verbatim rather than guessed at.
    """
    quote = value[0]
    end = value.find(quote, 1)
    if end == -1 or end == len(value) - 1:
        return value
    rest = value[end + 1 :]
    if rest[:1].isspace() and rest.lstrip().startswith("#"):
        return value[: end + 1]
    return value


def _scalar(raw: str) -> str:
    """One value as written — quoted scalars keep their content, bare ones lose comments."""
    stripped = raw.strip()
    if stripped[:1] in ("\"", "'"):
        return _unquote(_comment_after_quote(stripped))
    return _strip_comment(stripped).strip()


def _is_null_node(value: str) -> bool:
    """Is this value one of the plain scalars yaml-cpp folds into a null node?

    *value* is the text after the colon, stripped. The question is asked of a
    **plain** scalar only, the way the parser asks it: a quoted value is the
    text it wraps, so ``"null"`` and ``'~'`` are scalars spelling those
    characters and neither is this node. The comment comes off first, because
    ``key: ~ # note`` states the node as surely as ``key: ~`` does.

    A value that is *only* a comment is the empty spelling, and it is answered
    here rather than by :func:`_strip_comment`, which needs whitespace before
    the ``#`` and so reads ``key: #note`` as a value spelled ``#note``. A plain
    scalar cannot open with ``#`` — after ``key: `` the character opens a
    comment whatever follows it — so the whole line states nothing after the
    colon, which yaml-cpp reads as a null node at both commits above. Asking it
    here keeps every other value untouched: a quoted scalar is answered above,
    and a bare one still loses only a comment that whitespace introduces.
    """
    if value[:1] in ("\"", "'"):
        return False
    if value.startswith("#"):
        return True
    return _strip_comment(value).strip() in _NULL_SPELLINGS


def _is_skipping_value(value: str) -> bool:
    """Does this value open a construct the reader records instead of reading?

    Four of them. A ``|`` or ``>`` opens a multi-line scalar; ``- `` opens a
    list; ``[`` and ``{`` open a flow collection — Vita3K writes ``[]`` for an
    empty module list, and reporting that as the two-character string ``"[]"``
    would be a value nothing configured. An empty value with indented lines
    under it is the fifth, and only the caller can see what follows.
    """
    return value in ("|", ">", "|-", ">-", "|+", ">+") or value.startswith(("- ", "[", "{"))


def _refusal_in(value: str) -> str | None:
    """The whole-file refusal this value triggers, if any."""
    if value.startswith(("&", "*")):
        return REFUSAL_ANCHOR
    if value.startswith("!"):
        return REFUSAL_TAG
    return None


def _split_key(line: str) -> tuple[str, str] | None:
    """``key: value`` split at the first colon-space, or a bare ``key:``.

    A key may itself contain a colon — RPCS3's ``/dev_usb***/`` does not, but
    ``$(EmulatorDir)`` sits left of one — so the split is on ``": "`` first and
    on a trailing colon second, which is what YAML's own plain-key rule comes
    down to for these files.
    """
    if line.endswith(":"):
        return line[:-1].strip(), ""
    marker = line.find(": ")
    if marker == -1:
        return None
    return line[:marker].strip(), line[marker + 2 :]


def _substitute(
    values: dict[str, str], fallbacks: Mapping[str, str]
) -> tuple[dict[str, str], str | None]:
    """Resolve ``$(Name)`` against the file's own keys, or refuse.

    RPCS3 writes ``$(EmulatorDir): /path/`` and then composes every device path
    off it — the defining key is spelled exactly like the reference, so the
    whole ``$(Name)`` token is what gets looked up, not the name inside it.

    A token the file leaves empty or does not define at all falls to
    *fallbacks*, which is where the caller puts what the emulator itself would
    use — RPCS3 takes its config directory when ``$(EmulatorDir)`` is empty
    (get_emu_dir, system_utils.cpp:146-150, called at System.cpp:395 and passed
    at :483 into vfs_config.cpp:50 at build 7c6b3dcd). Without a fallback such
    a token is a refusal rather than an empty string: the emulator would
    resolve it and atlas cannot, so answering the unexpanded text would state a
    path nothing uses.

    A token whose defining key the file states more than once resolves against
    the first statement. That is this reader's own rule rather than an
    emulator's: ``values`` holds first statements, and resolving against
    anything else would make one read contradict itself. The one program that
    writes such tokens does it differently — RPCS3 substitutes the
    ``emulator_dir`` its iteration last decoded (``fmt::replace_all``,
    vfs_config.cpp:50 over cfg::decode, Utilities/Config.cpp:477-505 at build
    7c6b3dcd) — which is why the RPCS3 route refuses wherever that token is
    stated more than once rather than answering what resolved here.
    """
    resolved: dict[str, str] = {}
    for key, value in values.items():
        current = value
        for _ in range(_MAX_SUBSTITUTION_DEPTH):
            expanded, refusal, found = _substitute_once(current, values, fallbacks)
            if refusal is not None:
                return {}, refusal
            if not found:
                break
            current = expanded
        else:
            return {}, REFUSAL_SUBSTITUTION_CYCLE
        resolved[key] = current
    return resolved, None


def _substitute_once(
    value: str, values: Mapping[str, str], fallbacks: Mapping[str, str]
) -> tuple[str, str | None, bool]:
    """Replace every token in *value* once — one link of the chain, not one token.

    The bound above counts links, and this is what makes a link a link: a value
    naming eight different keys is eight replacements at **depth one**, and
    nothing about it is a cycle. Substituting one token per bounded step
    counted the replacements instead, so such a value was refused as
    ``substitution-cycle`` — a refusal that named the wrong thing about a file
    that was perfectly resolvable.

    Returns ``(text, refusal, found)``; ``found`` is ``False`` when the value
    holds no complete ``$(…)`` token, which is what ends the chain. An
    unterminated ``$(`` is not a token and ends it too, leaving the text as
    written the way an unterminated quote is left as written.
    """
    out: list[str] = []
    index = 0
    found = False
    while True:
        start = value.find("$(", index)
        if start == -1:
            break
        end = value.find(")", start)
        if end == -1:
            break
        token = value[start : end + 1]
        replacement = values.get(token) or fallbacks.get(token)
        if replacement is None:
            return "", REFUSAL_SUBSTITUTION_UNKNOWN, False
        out.append(value[index:start])
        out.append(replacement)
        index = end + 1
        found = True
    out.append(value[index:])
    return "".join(out), None, found


@dataclass(frozen=True, slots=True)
class _KeyLine:
    """What one top-level ``key: value`` line contributes.

    Exactly one of the three says what happened: ``refusal`` stops the whole
    file, ``skip`` records the key as stated-but-unread, and otherwise
    ``value`` is the scalar to keep. ``pending`` marks a key whose meaning the
    following lines still decide — a null node is a value until an indented
    line turns it into something this reader does not read.
    """

    key: str = ""
    value: str | None = None
    skip: bool = False
    pending: bool = False
    refusal: str | None = None


def _classify(line: str) -> _KeyLine:
    """One top-level line, read the way this module reads: value, skip or refusal."""
    split = _split_key(line)
    if split is None:
        # Not a key at all — a bare scalar or a list item at the top level.
        # Nothing here can be attributed to a key.
        return _KeyLine(refusal=REFUSAL_NOT_A_FLAT_MAPPING)
    key, raw_value = split
    value = raw_value.strip()
    # The key side too: an anchor or a tag changes meaning beyond the line it
    # sits on wherever it sits, and checking only the value let `&anc key: v`
    # through as a key literally spelled "&anc key".
    refusal = _refusal_in(key) or _refusal_in(value)
    if refusal is not None:
        return _KeyLine(refusal=refusal)
    if _is_skipping_value(value):
        return _KeyLine(key=key, skip=True, pending=True)
    if _is_null_node(value):
        # A null node: nothing after the colon, or one of the words yaml-cpp
        # folds with it. The text states no value either way, so the empty
        # string is what it reads as, and the key stays pending because an
        # indented line below still changes what the file states there. What it
        # changes depends on the spelling, and no reading of it belongs to this
        # reader: under the empty spelling the key heads a nested block, under a
        # word the document does not even load (``~`` then ``  x: 1`` is an
        # illegal map value at both commits above) or the two lines are one
        # multi-line plain scalar (``~`` then ``  more`` reads as ``~ more``).
        # Naming the key unread is the honest answer to all three. A quoted
        # empty scalar does not come through here; it is a value, and
        # ``_scalar`` below reads it.
        return _KeyLine(key=key, value="", pending=True)
    return _KeyLine(key=key, value=_scalar(raw_value))


def _first_document(text: str) -> tuple[tuple[str, ...], str | None]:
    """The content lines of the first document, blanks and comments dropped.

    Document structure is settled here so the reading below sees nothing but
    lines that carry content: an opening ``---`` is just "here begins the
    document", a second one begins a document that may redefine everything
    above it, and ``...`` ends the one being read.
    """
    lines: list[str] = []
    opened = False
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith("---"):
            # A marker ends the document being read whenever one was being
            # read at all — because content has been seen (an implicit first
            # document), or because a marker already opened one. `---\n---\n`
            # is an empty first document and a second, and reading the
            # second's lines as the first's would answer from a document this
            # reader never established is the one in force.
            if lines or opened:
                return (), REFUSAL_SECOND_DOCUMENT
            opened = True
            continue
        if raw_line.startswith("..."):
            break
        lines.append(raw_line)
    return tuple(lines), None


def _absorb_indent(
    pending_key: str | None,
    pending_repeat: bool,
    values: dict[str, str],
    skipped: list[str],
    null_keys: dict[str, None],
) -> bool:
    """Take an indented line into the key above it — ``False`` if there is none.

    An indented line makes the key above it one this reader does not read —
    the head of a nested block, or a line the emulator's own parser makes
    something else of (see :func:`_classify`) — so it stops being a scalar and
    is named as unread. Indentation under no key at all is a file this reader
    cannot attribute lines from.

    What follows the colon is then no longer nothing, so the key stops being
    one stated as a null node: it leaves ``null_keys`` here for the same reason
    it leaves ``values``.

    Unless the key above it was a *later* statement of a key already recorded,
    which is what *pending_repeat* carries: the block is then swallowed and
    nothing is written, because what this reader holds for that key is its
    first statement and a second statement's block may not take it away.
    """
    if pending_key is None:
        return False
    if pending_repeat:
        return True
    if values.pop(pending_key, None) is not None:
        skipped.append(pending_key)
        null_keys.pop(pending_key, None)
    return True


def _absorb_key(
    outcome: _KeyLine,
    values: dict[str, str],
    skipped: list[str],
    null_keys: dict[str, None],
    repeated: dict[str, None],
) -> tuple[str | None, bool]:
    """Record one key line — the pending key it leaves, and whether it repeats one.

    The twin of :func:`_absorb_indent`: that one takes a line into the key
    above it, this one takes the key itself. A pending key is one whose
    meaning is not settled by its own line — a null node is a value until
    something indented follows it.

    Only the **first** statement of a key is recorded, because that is the one
    a yaml-cpp lookup answers with: a lookup on a const node reaches
    ``node_data::get`` (``Node::operator[]``, include/yaml-cpp/node/impl.h:327-335)
    and its ``std::find_if`` returns the first pair whose key equals the one
    asked for (include/yaml-cpp/node/detail/impl.h:118-138 at 2f86d137), which
    is the lookup Vita3K makes (``update_members`` takes the node by const
    reference, config.cpp:41-44 at cb1f592c). A later
    statement writes nothing at all and only names the key in *repeated*, an
    ordered set carried as a mapping so a key stated three times is named
    once. Whether a statement is the first is read off the record itself: a
    recorded key sits in ``values`` or in ``skipped``, in exactly one of the
    two, and ``null_keys`` only ever names a key ``values`` holds.
    """
    first = outcome.key not in values and outcome.key not in skipped
    if not first:
        repeated[outcome.key] = None
    elif outcome.skip:
        skipped.append(outcome.key)
    else:
        values[outcome.key] = outcome.value or ""
        if outcome.pending:
            null_keys[outcome.key] = None
    if not outcome.pending:
        return None, False
    return outcome.key, not first


def read_scalars(text: str, *, fallbacks: Mapping[str, str] | None = None) -> YamlScalars:
    """Read the flat top-level scalars of *text*, naming what was not read.

    *fallbacks* supplies what a ``$(Name)`` token means where the file leaves
    it empty or states it nowhere — the emulator's own rule, which only the
    caller knows.
    """
    lines, refusal = _first_document(text)
    if refusal is not None:
        return YamlScalars(refusal=refusal)
    values: dict[str, str] = {}
    skipped: list[str] = []
    null_keys: dict[str, None] = {}
    repeated: dict[str, None] = {}
    pending_key: str | None = None
    pending_repeat = False
    for raw_line in lines:
        if raw_line[:1].isspace():
            if not _absorb_indent(pending_key, pending_repeat, values, skipped, null_keys):
                return YamlScalars(refusal=REFUSAL_NOT_A_FLAT_MAPPING)
            continue
        outcome = _classify(raw_line.rstrip())
        if outcome.refusal is not None:
            # A later statement of a key already recorded refuses here too: an
            # anchor, an alias or a tag changes meaning beyond its own line,
            # so the first statement is not safe from it either.
            return YamlScalars(refusal=outcome.refusal)
        pending_key, pending_repeat = _absorb_key(outcome, values, skipped, null_keys, repeated)
    resolved, refusal = _substitute(values, fallbacks or {})
    if refusal is not None:
        return YamlScalars(refusal=refusal)
    return YamlScalars(
        values=resolved,
        skipped=tuple(skipped),
        null=tuple(null_keys),
        repeated=tuple(repeated),
    )
