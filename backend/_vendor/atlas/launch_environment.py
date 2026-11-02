"""What a RetroDECK component launcher reads from RetroDECK's own start, pinned launcher by launcher (#574).

ES-DE inside RetroDECK runs an emulator through a component launcher under
``/app/retrodeck/components/<component>/component_launcher.sh``, and the
launcher reads variables RetroDECK's start exports before ES-DE runs:
``bin/retrodeck`` sources ``global.sh``, which sources ``all_vars.sh`` under
``allexport`` (``rd_shared_libs``, ``DEFAULT_LD_LIBRARY_PATH``), runs
``conf_read``, which exports every key of ``retrodeck.json``'s ``paths``
(``bios_path`` …), and sources every component's ``component_functions.sh``
under ``allexport`` too (``xroar_config``). A caller that starts the launcher itself starts it outside
that start, so what the launcher reads it has to pass itself.

Which variables a launcher reads is read off its text at one release and
packaged per launcher (``atlas/data/retrodeck_launchers.json``), keyed by the
git blob hash of its deployed copy; the reading holds together with the start
file that defines each variable it reads, pinned by its own blob hash. The
values are never packaged: a ``line`` variable is read off its assignment in
the start file (:func:`start_line`), expanding the values the sandbox starts
with that atlas reads on the machine (:func:`start_environment`), a ``paths``
variable off RetroDECK's configuration.

Left out, because no caller can pass them or none has to: a variable no start
provides (``ffmpeg_path``, which five launchers read and nothing defines), the
``log`` function the start exports (a launcher without it prints an error and
goes on), and the session's own variables (``HOME``, ``DISPLAY``,
``WAYLAND_DISPLAY``, ``XDG_RUNTIME_DIR``, ``DBUS_SESSION_BUS_ADDRESS`` and the
like), which any start in a user's session carries.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from ._data import packaged_text
from .launch_command import DistributionVariable

LAUNCH_ENVIRONMENT_SCHEMA = 1

VariableSource = Literal["line", "paths"]
SOURCE_LINE: VariableSource = "line"
SOURCE_PATHS: VariableSource = "paths"
_SOURCES = (SOURCE_LINE, SOURCE_PATHS)

# Where every pinned path lives: the deploy's /app tree, as the sandbox spells it.
_APP_ROOT = "/app/"


@dataclass(frozen=True, slots=True)
class StartVariable:
    """One variable RetroDECK's start provides: the start file that defines it, and how its value is read there.

    ``line`` is the line of the start file that sets a ``paths`` variable
    (``conf_read``'s assignment, which sets every key under its own name);
    a ``line`` variable's line is found in the start file itself.
    """

    defined_in: str
    source: VariableSource
    line: int | None


@dataclass(frozen=True, slots=True)
class LauncherRead:
    """One variable a launcher reads, and the lines that read it."""

    name: str
    citation: str


@dataclass(frozen=True, slots=True)
class EnvironmentRule:
    """What one pinned launcher reads from the start, in the order it first reads it."""

    launcher: str
    blob: str
    reads: tuple[LauncherRead, ...]


@dataclass(frozen=True, slots=True)
class EnvironmentRules:
    """Every pinned launcher by its blob hash, the start files and the variables they define, and the release read."""

    version: str
    start_files: Mapping[str, str]
    variables: Mapping[str, StartVariable]
    by_blob: Mapping[str, EnvironmentRule]

    def pins(self, rule: EnvironmentRule) -> Mapping[str, str]:
        """The start files *rule*'s reading rests on, each with its pin, in the order the launcher first needs them."""
        pinned: dict[str, str] = {}
        for read in rule.reads:
            start_file = self.variables[read.name].defined_in
            pinned.setdefault(start_file, self.start_files[start_file])
        return pinned


# An assignment at the start of a line, as a start file sourced at the top
# level writes it; the last one of a name is the one that stands.
_ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z_]\w*)=(?P<rest>.*)$", re.ASCII)
# One piece of an assignment's value, as bash reads the word: a double-quoted
# run, a single-quoted run, a ``$NAME`` or ``${NAME}`` reference, or bare
# characters no shell reads specially inside an assignment word (``#`` and
# ``=`` among them: neither is special there). A double-quoted run is taken
# whole — any character but ``"``, a backtick and ``\`` — and its ``$`` are
# read afterwards (:func:`_expanded`): every one must start a reference, so
# the pattern has one way to match a run and no nested repetition to
# backtrack through. Then the end of the word: blanks, and a comment only
# after one.
_PIECE = re.compile(
    r""""(?P<double>[^"`\\]*)"|'(?P<single>[^']*)'"""
    r"""|\$(?P<plain>[A-Za-z_]\w*)|\$\{(?P<braced>[A-Za-z_]\w*)\}|(?P<bare>[\w./:@%+,#=-]+)""",
    re.ASCII,
)
_REFERENCE = re.compile(r"\$(?:(?P<plain>[A-Za-z_]\w*)|\{(?P<braced>[A-Za-z_]\w*)\})", re.ASCII)
_WORD_END = re.compile(r"(?:[ \t]+(?:#.*)?)?$")


def shell_value(rest: str, known: Mapping[str, str]) -> str | None:
    """The value bash assigns from *rest*, the text after an assignment's ``=`` — ``None`` where atlas does not read
    it.

    Read: double quotes, single quotes and bare characters, joined as the shell
    joins adjacent pieces of one word, and a plain ``$NAME`` or ``${NAME}``
    whose value *known* holds. Not read, so ``None``: a reference to any other
    name, a command substitution, an arithmetic expansion, a parameter
    expansion with an operator, an escape, a tilde, a glob, anything after the
    word but a comment. This is no shell evaluator; it reads the lines the
    start writes and refuses the rest.
    """
    value: list[str] = []
    position = 0
    while position < len(rest):
        piece = _PIECE.match(rest, position)
        if piece is None:
            break
        position = piece.end()
        if piece.group("single") is not None:
            value.append(piece.group("single"))
        elif piece.group("bare") is not None:
            value.append(piece.group("bare"))
        else:
            expanded = _expanded(piece, known)
            if expanded is None:
                return None
            value.append(expanded)
    return "".join(value) if _WORD_END.match(rest, position) else None


def _expanded(piece: re.Match[str], known: Mapping[str, str]) -> str | None:
    """A reference or a double-quoted run of *piece* with every reference in it replaced, ``None`` where one names
    a value *known* does not hold or a ``$`` in the run starts none (``$(``, ``$$``, ``${NAME:-…}``)."""
    name = _name(piece)
    if name is not None:
        return known.get(name)
    run = piece.group("double")
    if "$" in _REFERENCE.sub("", run):
        return None
    if any(_name(reference) not in known for reference in _REFERENCE.finditer(run)):
        return None
    return _REFERENCE.sub(lambda reference: known[cast(str, _name(reference))], run)


def _name(match: re.Match[str]) -> str | None:
    return match.group("plain") or match.group("braced")


def shell_references(rest: str) -> frozenset[str]:
    """Every name a ``$NAME`` or ``${NAME}`` of *rest* reads, up to where :func:`shell_value` stops reading — what
    *known* has to hold for the value to be stated, where it is one atlas reads at all."""
    return frozenset(name for _, name in _parts(rest) if name is not None)


def _parts(rest: str) -> list[tuple[str, str | None]]:
    """*rest* as :func:`shell_value` reads it, piece by piece up to where it stops: ``(text, None)`` for a literal,
    ``("", name)`` for a reference."""
    parts: list[tuple[str, str | None]] = []
    position = 0
    while (piece := _PIECE.match(rest, position)) is not None:
        position = piece.end()
        if piece.group("double") is not None:
            run = piece.group("double")
            if "$" in _REFERENCE.sub("", run):
                break
            start = 0
            for reference in _REFERENCE.finditer(run):
                parts.append((run[start : reference.start()], None))
                parts.append(("", _name(reference)))
                start = reference.end()
            parts.append((run[start:], None))
        elif (name := _name(piece)) is not None:
            parts.append(("", name))
        else:
            parts.append((piece.group("single") or piece.group("bare") or "", None))
    return parts


def is_search_list(rest: str) -> bool:
    """Whether the value *rest* gives is a search list (#574 D14): a reference to one of :data:`_SEARCH_LISTS`
    standing as a whole element of the colon-separated value — the value's edge or a ``:`` on either side, as in
    ``/app/lib:${LD_LIBRARY_PATH}``. ``${LD_LIBRARY_PATH}/lib`` composes a path from one and is no search list."""
    parts = _parts(rest)
    return any(
        name in _SEARCH_LISTS
        and _meets_an_edge(reversed(parts[:index]), str.endswith)
        and _meets_an_edge(parts[index + 1 :], str.startswith)
        for index, (_, name) in enumerate(parts)
    )


def _meets_an_edge(parts: Iterable[tuple[str, str | None]], touches: Callable[[str, str], bool]) -> bool:
    """Whether the text beside a reference, read away from it across *parts*, reaches a ``:`` or the value's edge
    before anything else — another reference, another character."""
    for literal, name in parts:
        if name is not None:
            return False
        if literal:
            return touches(literal, ":")
    return True


def start_assignment(text: str, name: str) -> tuple[int, str] | None:
    """The line of *text* that sets *name* and the text after its ``=`` — ``None`` where no line sets it."""
    found: tuple[int, str] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = _ASSIGNMENT.match(line)
        if match is not None and match.group("name") == name:
            found = (number, match.group("rest"))
    return found


def start_line(text: str, name: str, known: Mapping[str, str] | None = None) -> tuple[int, str | None] | None:
    """The line of *text* that sets *name* and the value it gives — ``None`` for the value where the line computes
    it from anything but the values *known* holds (:func:`shell_value`), and ``None`` altogether where no line sets
    it. Without *known* only a literal is a value.
    """
    found = start_assignment(text, name)
    if found is None:
        return None
    line, rest = found
    return line, shell_value(rest, known or {})


# The variables whose value is a colon-separated list of folders to search:
# a value composed from one names folders to search, none of which has to
# exist, since the dynamic loader skips a folder that is not there (#574 D14).
_SEARCH_LISTS = frozenset({"LD_LIBRARY_PATH"})

# The start files RetroDECK's start sources before conf_read runs: global.sh
# sources all_vars.sh at :116-118 and calls conf_read at :198, :210 and :218
# (0.10.10b). Every other start file comes after it — the component
# functions files at :232-233 — or is not known to come before, and is read
# as coming after.
_BEFORE_CONF_READ = frozenset({"/app/libexec/all_vars.sh"})


def start_environment(
    defined_in: str, environment: Mapping[str, str], settings: Mapping[str, Any]
) -> Mapping[str, str]:
    """What of *environment* — the values the sandbox starts RetroDECK with — still stands when the start file
    *defined_in* runs, against RetroDECK's *settings* (``retrodeck.json``).

    conf_read exports ``version`` and every key of the settings' ``paths``
    and ``options`` under the key's own name (``printf -v "$name"`` and
    ``export``, other_functions.sh:148-190, the assignment at :156-157), so a
    key that names one of these variables replaces it for every start file
    after it. Such a name is dropped there, and so is everything where
    ``paths`` or ``options`` is no object, which conf_read does not read
    key by key.
    """
    if defined_in in _BEFORE_CONF_READ:
        return environment
    sections = [settings.get("paths", {}), settings.get("options", {})]
    if not all(isinstance(section, dict) for section in sections):
        return {}
    keys = {"version", *(key for section in sections for key in cast(dict[str, object], section))}
    return {name: value for name, value in environment.items() if name not in keys}


def line_variable(
    name: str,
    defined_in: str,
    text: str | None,
    exists: Callable[[str], bool],
    known: Mapping[str, str] | None = None,
) -> DistributionVariable:
    """*name* as the start file *defined_in* sets it, read off its *text* (``None`` where it could not be read).

    The value is what the line gives, ``None`` where the line computes it from
    anything but the values *known* holds (:func:`shell_value`). The
    installation provides it where the line is there and a path the value
    names — a value starting with ``/`` — *exists*; a value that names no path
    is provided by the line alone, and so is a search list
    (:func:`is_search_list`), which names folders to search, none of which has
    to exist (#574 D14).
    A start file without the line, or one atlas cannot read, provides nothing.
    """
    found = None if text is None else start_assignment(text, name)
    if found is None:
        return DistributionVariable(name, None, defined_in, False)
    line, rest = found
    value = shell_value(rest, known or {})
    set_by = f"{defined_in}:{line}"
    if value is None or not value.startswith("/") or is_search_list(rest):
        return DistributionVariable(name, value, set_by, True)
    return DistributionVariable(name, value, set_by, exists(value))


def paths_variable(
    name: str, variable: StartVariable, paths: object, exists: Callable[[str], bool]
) -> DistributionVariable:
    """*name* as ``conf_read`` exports it from ``retrodeck.json``'s *paths* section, under the key's own name.

    The value is the key's string, ``None`` where the section holds no string
    under it — a missing key, a section that is no object, a value of another
    type. The installation provides it where it is a string and the folder it
    names *exists*.
    """
    value: object = cast(dict[str, object], paths).get(name) if isinstance(paths, dict) else None
    set_by = f"{variable.defined_in}:{variable.line}"
    if not isinstance(value, str):
        return DistributionVariable(name, None, set_by, False)
    return DistributionVariable(name, value, set_by, bool(value) and exists(value))


def load_environment_rules(text: str | None = None) -> EnvironmentRules:
    """Load the packaged launcher table (or *text* when supplied, for tests), validated, never coerced."""
    if text is None:
        text = packaged_text("retrodeck_launchers.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != LAUNCH_ENVIRONMENT_SCHEMA:
        raise ValueError(
            f"retrodeck_launchers: unsupported schema {raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {LAUNCH_ENVIRONMENT_SCHEMA})"
        )
    if set(raw) != {"schema", "spec", "retrodeck", "start_files", "variables", "launchers"}:
        raise ValueError(f"retrodeck_launchers: unexpected top-level keys {sorted(raw)}")
    retrodeck = raw["retrodeck"]
    if not isinstance(retrodeck, dict) or not _text(retrodeck.get("version")) or not _text(retrodeck.get("start")):
        raise ValueError("retrodeck_launchers: retrodeck names the version read and cites the start")
    start_files = _start_files(raw["start_files"])
    variables = _variables(raw["variables"], start_files)
    launchers = raw["launchers"]
    if not isinstance(launchers, list) or not launchers:
        raise ValueError("retrodeck_launchers: expected a non-empty 'launchers' list")
    by_blob: dict[str, EnvironmentRule] = {}
    for item in launchers:
        rule = _rule(item, variables)
        if rule.blob in by_blob:
            raise ValueError(f"retrodeck_launchers: two launchers pinned by one blob {rule.blob}")
        by_blob[rule.blob] = rule
    return EnvironmentRules(retrodeck["version"], start_files, variables, by_blob)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def _is_app_path(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_APP_ROOT) and ".." not in value.split("/")


def _start_files(value: Any) -> Mapping[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError("retrodeck_launchers start_files: expected an object of path → blob hash")
    for path, blob in value.items():
        if not _is_app_path(path) or not _is_hash(blob):
            raise ValueError(f"retrodeck_launchers start_files: {path!r} is an /app/ path pinned by a full blob hash")
    return dict(value)


def _variables(value: Any, start_files: Mapping[str, str]) -> Mapping[str, StartVariable]:
    if not isinstance(value, dict) or not value:
        raise ValueError("retrodeck_launchers variables: expected an object of name → definition")
    return {name: _variable(f"variable {name!r}", item, start_files) for name, item in value.items()}


def _variable(where: str, item: Any, start_files: Mapping[str, str]) -> StartVariable:
    if not isinstance(item, dict) or item.get("from") not in _SOURCES:
        raise ValueError(f"retrodeck_launchers {where}: 'from' is one of {_SOURCES}")
    source: VariableSource = item["from"]
    expected = {"defined_in", "from", "line"} if source == SOURCE_PATHS else {"defined_in", "from"}
    if set(item) != expected:
        raise ValueError(f"retrodeck_launchers {where}: a {source} variable names exactly {sorted(expected)}")
    if item["defined_in"] not in start_files:
        raise ValueError(f"retrodeck_launchers {where}: defined_in is one of the pinned start_files")
    line = item.get("line")
    if source == SOURCE_PATHS and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
        raise ValueError(f"retrodeck_launchers {where}: line is the start file's line that sets it")
    return StartVariable(item["defined_in"], source, line)


_RULE_KEYS = {"launcher", "blob", "reads"}


def _rule(item: Any, variables: Mapping[str, StartVariable]) -> EnvironmentRule:
    if not isinstance(item, dict) or set(item) != _RULE_KEYS:
        raise ValueError(f"retrodeck_launchers: a launcher names exactly {sorted(_RULE_KEYS)}, got {item!r}")
    where = f"launcher {item['launcher']!r}"
    if not _is_app_path(item["launcher"]):
        raise ValueError(f"retrodeck_launchers {where}: launcher is an /app/ path")
    if not _is_hash(item["blob"]):
        raise ValueError(f"retrodeck_launchers {where}: blob is a full blob hash")
    reads = item["reads"]
    if not isinstance(reads, list):
        raise ValueError(f"retrodeck_launchers {where}: reads is a list")
    parsed: list[LauncherRead] = []
    for read in reads:
        if not isinstance(read, dict) or set(read) != {"name", "citation"}:
            raise ValueError(f"retrodeck_launchers {where}: a read names exactly name and citation, got {read!r}")
        if read["name"] not in variables:
            raise ValueError(f"retrodeck_launchers {where}: {read['name']!r} is none of the variables the start defines")
        if not _text(read["citation"]):
            raise ValueError(f"retrodeck_launchers {where}: every read carries a citation")
        parsed.append(LauncherRead(read["name"], read["citation"]))
    if len({read.name for read in parsed}) != len(parsed):
        raise ValueError(f"retrodeck_launchers {where}: each variable is read once, where the launcher first reads it")
    return EnvironmentRule(item["launcher"], item["blob"], tuple(parsed))


_PACKAGED: EnvironmentRules | None = None


def environment_rules() -> EnvironmentRules:
    """The packaged table, loaded once."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_environment_rules()
    return _PACKAGED
