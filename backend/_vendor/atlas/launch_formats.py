"""Install-first formats — the launchability question's world knowledge (issue #36).

A launchability 'no' has more than one meaning, and the accept-list can only
state one of them: the frontend will not scan this file. For some files the
honest continuation is "because it is an installer" — a PSN ``.pkg`` is the
distribution form of the content itself, and the emulator has to install it
before anything can launch. That fact is written nowhere on the machine, so
it lives here: marked, versioned, cited, keyed by the atlas system id and the
exact extension token ES-DE would derive. The resolver consults it only where
the extension is already outside the machine's own accept-list — a read is
never overridden by a table. Each record names the emulators that install the
format (issue #717), so the verdict can name them as data rather than prose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ._data import packaged_text

_ROM = "%ROM%"

FORMATS_SCHEMA = 3


@dataclass(frozen=True, slots=True)
class InstallFirstFormat:
    """One format that needs an installation step before anything can launch.

    ``installers`` are the emulators that install it, each by the token ES-DE's
    own commands spell it with (the ``X`` of ``%EMULATOR_X%``), in the
    record's order. Empty is a statement of its own: the format has to be
    installed, and nothing a supported distribution sets up installs it.
    """

    system: str
    extension: str
    statement: str
    installers: tuple[str, ...]
    source: str


@dataclass(frozen=True, slots=True)
class StandaloneLaunchCard:
    """What one standalone emulator's own loader reads (issue #66).

    ``accepts`` are extension tokens in their dotted lowercase form, matched
    case-insensitively — the gate here is the emulator's loader, not ES-DE's
    case-exact scan. ``archives`` says whether the loader opens archive
    containers at all: a standalone gets the file itself, with no RetroArch
    in front of it to pick a matching entry out of a zip.

    Three statements are optional (issue #618). ``arguments`` are the
    argument lists the card was read for — the words a command hands the
    emulator after its token — and ``None`` where the card states none.
    ``content_checked`` says the loader reads the file's content once the
    extension has passed, which the card does not. ``build_dependent`` pairs
    each extension the builds the card serves disagree on with the citation
    naming them.
    """

    token: str
    accepts: tuple[str, ...]
    archives: bool
    source: str
    arguments: tuple[tuple[str, ...], ...] | None = None
    content_checked: bool = False
    build_dependent: tuple[tuple[str, str], ...] = ()

    def takes(self, extension: str) -> bool:
        return extension.lower() in self.accepts

    def covers(self, arguments: tuple[str, ...] | None) -> bool:
        """Whether the card answers for a command handing the emulator *arguments* after its token.

        ``None`` is a command whose arguments could not be told apart; a card
        that states no ``arguments`` answers for every command naming its token.
        """
        return self.arguments is None or arguments in self.arguments

    def build_note(self, extension: str) -> str | None:
        """The citation naming the builds that disagree on *extension*, or ``None`` where they agree."""
        folded = extension.lower()
        return next((note for token, note in self.build_dependent if token == folded), None)


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def load_launch_formats(text: str | None = None) -> tuple[InstallFirstFormat, ...]:
    """Load the packaged install-first formats (or *text* when supplied, for tests)."""
    if text is None:
        text = _packaged_text()
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != FORMATS_SCHEMA:
        raise ValueError(
            f"launch_formats: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {FORMATS_SCHEMA})"
        )
    systems = raw.get("systems", {})
    if not isinstance(systems, dict):
        raise ValueError(f"launch_formats: systems must be an object, got {systems!r}")
    formats: list[InstallFirstFormat] = []
    for system, entries in systems.items():
        where = f"launch format system {system!r}"
        if not isinstance(entries, dict) or not entries:
            raise ValueError(f"{where}: expected a non-empty object, got {entries!r}")
        for extension, entry in entries.items():
            formats.append(_format(system, extension, entry, f"{where}: {extension!r}"))
    return tuple(formats)


def _format(system: str, extension: str, entry: Any, at: str) -> InstallFirstFormat:
    """One record — validated, never coerced."""
    if not extension.startswith(".") or extension == ".":
        # The key must be a token esde_extension() can ever answer: every
        # derived extension starts with the dot it was cut at, and the
        # bare-dot sentinel names "no extension", which is not a format.
        raise ValueError(f"{at}: an extension token starts with '.' and names one")
    if not isinstance(entry, dict) or set(entry) != {"statement", "installers", "source"}:
        raise ValueError(
            f"{at}: an entry names exactly 'statement', 'installers' and 'source', got {entry!r}"
        )
    return InstallFirstFormat(
        system=system,
        extension=extension,
        statement=_expect_str(entry["statement"], f"{at}: statement"),
        installers=_installers(entry["installers"], f"{at}: installers"),
        source=_expect_str(entry["source"], f"{at}: source"),
    )


def _installers(value: Any, where: str) -> tuple[str, ...]:
    """The installing emulators' tokens — a list, possibly empty, each named once."""
    if not isinstance(value, list):
        raise ValueError(f"{where}: expected a list of emulator tokens, got {value!r}")
    tokens = tuple(_expect_str(token, where) for token in value)
    if len(set(tokens)) != len(tokens):
        # A repeated token would state one installer twice in the verdict.
        raise ValueError(f"{where}: a token is listed twice in {list(tokens)!r}")
    return tokens


def load_standalone_launch(text: str | None = None) -> tuple[StandaloneLaunchCard, ...]:
    """Load the packaged standalone launch cards (or *text* when supplied, for tests)."""
    if text is None:
        text = _packaged_text()
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != FORMATS_SCHEMA:
        raise ValueError(
            f"launch_formats: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {FORMATS_SCHEMA})"
        )
    emulators = raw.get("emulators", {})
    if not isinstance(emulators, dict):
        raise ValueError(f"launch_formats: emulators must be an object, got {emulators!r}")
    return tuple(_launch_card(token, entry) for token, entry in emulators.items())


_CARD_KEYS = frozenset({"accepts", "archives", "source"})
_CARD_OPTIONAL_KEYS = frozenset({"arguments", "content_checked", "build_dependent"})


def _launch_card(token: str, entry: Any) -> StandaloneLaunchCard:
    where = f"standalone launch card {token!r}"
    if not isinstance(entry, dict) or not _CARD_KEYS <= set(entry) <= _CARD_KEYS | _CARD_OPTIONAL_KEYS:
        raise ValueError(
            f"{where}: a card names 'accepts', 'archives' and 'source', "
            "and may also name 'arguments', 'content_checked' and 'build_dependent'"
        )
    accepts = entry["accepts"]
    if not isinstance(accepts, list) or not accepts:
        raise ValueError(f"{where}: accepts must be a non-empty list")
    for token_ext in accepts:
        _card_extension(token_ext, f"{where}: accepts")
    if not isinstance(entry["archives"], bool):
        raise ValueError(f"{where}: archives must be a JSON boolean")
    content_checked = entry.get("content_checked", False)
    if not isinstance(content_checked, bool):
        raise ValueError(f"{where}: content_checked must be a JSON boolean")
    build_dependent = _build_dependent(entry["build_dependent"], accepts, where) if "build_dependent" in entry else ()
    return StandaloneLaunchCard(
        token=token,
        accepts=tuple(accepts),
        archives=entry["archives"],
        source=_expect_str(entry["source"], f"{where}: source"),
        arguments=_arguments(entry["arguments"], where) if "arguments" in entry else None,
        content_checked=content_checked,
        build_dependent=build_dependent,
    )


def _card_extension(value: Any, where: str) -> str:
    """One extension token of a card — dotted, naming one, recorded lowercase."""
    if not isinstance(value, str) or not value.startswith(".") or value == ".":
        raise ValueError(f"{where}[{value!r}] — an extension token starts with '.' and names one")
    if value != value.lower():
        # The match is case-insensitive by lowercasing the file's token, so
        # a card token carrying upper case could never be hit.
        raise ValueError(f"{where}[{value!r}] must be recorded lowercase")
    return value


def _arguments(value: Any, where: str) -> tuple[tuple[str, ...], ...]:
    """The argument lists a card was read for — each a list of words handing the emulator ``%ROM%``."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: arguments must be a non-empty list of argument lists")
    shapes: list[tuple[str, ...]] = []
    for shape in value:
        if not isinstance(shape, list) or not all(isinstance(word, str) and word for word in shape):
            raise ValueError(f"{where}: arguments[{shape!r}] must be a list of non-empty words")
        if shape.count(_ROM) != 1:
            # The card judges the file a command hands its emulator, so a
            # shape that hands none, or hands it twice, is not one it read.
            raise ValueError(f"{where}: arguments[{shape!r}] must hand the emulator {_ROM} exactly once")
        if tuple(shape) in shapes:
            raise ValueError(f"{where}: arguments[{shape!r}] is listed twice")
        shapes.append(tuple(shape))
    return tuple(shapes)


def _build_dependent(value: Any, accepts: list[str], where: str) -> tuple[tuple[str, str], ...]:
    """The extensions the builds a card serves disagree on, each with its citation."""
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{where}: build_dependent must be a non-empty object")
    notes: list[tuple[str, str]] = []
    for extension, note in value.items():
        _card_extension(extension, f"{where}: build_dependent")
        if extension in accepts:
            # One extension, two statements: taken by every build, and taken
            # by some. The card states one.
            raise ValueError(f"{where}: build_dependent[{extension!r}] is also in accepts")
        notes.append((extension, _expect_str(note, f"{where}: build_dependent[{extension!r}]")))
    return tuple(notes)


_PACKAGED: tuple[InstallFirstFormat, ...] | None = None
_PACKAGED_CARDS: tuple[StandaloneLaunchCard, ...] | None = None


def _packaged_text() -> str:
    return packaged_text("launch_formats.json")


def lookup_install_first(system: str, extension: str) -> InstallFirstFormat | None:
    """The packaged record for one (system, extension) — exact match, or ``None``."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_launch_formats()
    return next(
        (f for f in _PACKAGED if f.system == system and f.extension == extension), None
    )


def lookup_standalone_launch(token: str | None) -> StandaloneLaunchCard | None:
    """The packaged card for one emulator token, or ``None`` — no fuzzy matching."""
    global _PACKAGED_CARDS
    if _PACKAGED_CARDS is None:
        _PACKAGED_CARDS = load_standalone_launch()
    if token is None:
        return None
    return next((card for card in _PACKAGED_CARDS if card.token == token), None)
