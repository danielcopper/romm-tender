"""Standalone save cards — which emulators atlas can answer the save question for.

A standalone emulator is handed nothing by a frontend: its save tree is its
own, shaped by its own configuration and its own compiled-in defaults. The
card here is the thin, versioned half of that knowledge — which emulator,
which configuration file governs it, which catalogue systems it answers for,
and the citations behind both — while the reading itself is code beside it in
:mod:`atlas.installations`, exactly the split the libretro rule cards make
(:mod:`atlas.mode_rules`): a card states what *can* be, the code reads what
*is* on this machine, and neither guesses.

The cards are keyed by the ``%EMULATOR_…%`` token the ES-DE catalogue names in
a launch command, because for a standalone entry that token is the only
identifier there is — the same key the standalone texture cards use
(:mod:`atlas.textures`). A card whose token has no resolver function
registered is a marker selecting nothing, and fails the load the way a rule
card without a rule does.

A card also states how one launch can move the emulator's saves elsewhere
without its configuration being written — :class:`SaveRedirect`, one per route
the source offers, or none. Which routes exist is world knowledge read out of
the emulator's source at the build the card pins, so it lives here beside the
rest of that knowledge; the save answer carries it unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._data import packaged_text

SAVES_SCHEMA = 3

RedirectVia = Literal["flag", "variable", "working-directory"]

REDIRECT_VIA_FLAG: RedirectVia = "flag"
"""A launch flag: ``name`` is its spelling and ``argument`` what follows it.

The caller appends it to the command line the launch runs. A flag outranks
whatever the configuration states for the same thing only where the route's
citation says so; it is never written into the configuration.
"""
REDIRECT_VIA_VARIABLE: RedirectVia = "variable"
"""An environment variable: ``name`` is the variable and ``argument`` its value.

Set for the emulator process alone, for the reason ``atlas/data/README.md``
gives under ``redirects``.
"""
REDIRECT_VIA_WORKING_DIRECTORY: RedirectVia = "working-directory"
"""The launch's working directory: ``name`` is ``None`` and ``argument`` the path the emulator looks for.

The caller starts the emulator in ``<dir>`` and the emulator takes the folder
``argument`` spells below it, where that folder exists, in place of its own
trees.
"""

REDIRECT_VIAS = ("flag", "variable", "working-directory")

RedirectScope = Literal["saves", "savestates", "configuration", "firmware", "installed-content"]

REDIRECT_MOVES_SAVES: RedirectScope = "saves"
"""The games' own saves move: memory cards, save files, the save trees a console keeps."""
REDIRECT_MOVES_SAVESTATES: RedirectScope = "savestates"
"""The emulator's savestates move."""
REDIRECT_MOVES_CONFIGURATION: RedirectScope = "configuration"
"""The emulator's configuration moves: a launch reads another settings file, or a fresh default one."""
REDIRECT_MOVES_FIRMWARE: RedirectScope = "firmware"
"""Where the emulator looks for firmware moves: BIOS files, system titles, a console's system storage."""
REDIRECT_MOVES_INSTALLED_CONTENT: RedirectScope = "installed-content"
"""Content installed into the emulator moves: installed games, updates and downloadable content."""

REDIRECT_SCOPES = ("saves", "savestates", "configuration", "firmware", "installed-content")


def _refuse_unknown_scope(scope: str) -> None:
    if scope not in REDIRECT_SCOPES:
        raise ValueError(f"SaveRedirect: moves must name values of {REDIRECT_SCOPES}, got {scope!r}")


@dataclass(frozen=True, slots=True)
class RedirectSetting:
    """One setting a route depends on or that ranks above it, named the card's way.

    ``file`` is the configuration file the card names in ``settings`` and
    ``key`` the setting in it, an ini key spelled with its section
    (``[Core] SlotA``). What a value of it means for the route is the
    resolver's to read, the same split the rest of the card keeps: the card
    names the setting, the code reads the machine.
    """

    file: str
    """The configuration file holding the setting, as the card's ``settings`` names it."""
    key: str
    """The setting, spelled with its ini section where the file has sections."""


@dataclass(frozen=True, slots=True)
class SaveRedirect:
    """One way a launch can move an emulator's saves without writing its configuration.

    A route counts when it is a launch flag, an environment variable or the
    launch's working directory, and when it moves the saves or the savestates;
    a route that only works through the emulator's own configuration file is
    no redirect, and one that moves the configuration alone is that route.
    ``moves`` states everything the route moves, not only the saves: a route
    that moves the emulator's whole data directory moves its configuration,
    its firmware lookup or its installed content with it, and a caller that
    sees that can judge whether copying the saves in and out is the better
    way.
    """

    via: RedirectVia
    """How the launch carries the route — one of :data:`REDIRECT_VIAS`."""
    name: str | None
    """The flag's exact spelling or the variable's name, and ``None`` for a working-directory route."""
    argument: str
    """What the route is given, with ``<dir>`` or ``<file>`` where the caller's path goes."""
    moves: tuple[RedirectScope, ...]
    """Everything the route moves, in vocabulary order — values of :data:`REDIRECT_SCOPES`."""
    citation: str
    """The source lines behind the route at the card's pinned build, and its limits where the source states them."""
    outranked_by: tuple[RedirectSetting, ...] = ()
    """The settings the route depends on or that rank above it, as the citation states them.

    The resolver compares each with the configuration it reads for the save
    answer, and where that configuration outranks the route or makes it not
    apply, the answer carries ``redirect-outranked`` naming the route and the
    setting. Empty where nothing in the configuration can, as for a route
    that moves the configuration with everything else.
    """

    def __post_init__(self) -> None:
        if self.via not in REDIRECT_VIAS:
            raise ValueError(f"SaveRedirect: via must be one of {REDIRECT_VIAS}, got {self.via!r}")
        if (self.name is None) != (self.via == REDIRECT_VIA_WORKING_DIRECTORY):
            raise ValueError(
                "SaveRedirect: a working-directory route names nothing, and every other route "
                f"names its flag or variable — got via {self.via!r} with name {self.name!r}"
            )
        for scope in self.moves:
            _refuse_unknown_scope(scope)
        if list(self.moves) != sorted(set(self.moves), key=REDIRECT_SCOPES.index):
            raise ValueError(
                f"SaveRedirect: moves must name each value once, in {REDIRECT_SCOPES} order, "
                f"got {self.moves!r}"
            )
        if REDIRECT_MOVES_SAVES not in self.moves and REDIRECT_MOVES_SAVESTATES not in self.moves:
            raise ValueError(
                f"SaveRedirect: a route that moves neither saves nor savestates is no save redirect, "
                f"got {self.moves!r}"
            )


@dataclass(frozen=True, slots=True)
class StandaloneBuild:
    """One build a card's readings were taken at, as the deploy names it and the build stamps it.

    ``release`` is the name the deploy writes for the build it ships — what
    RetroDECK's component recipe writes as ``component_version``: the tag, or
    for a component extracted from a Flathub app, that app's commit — which is
    what a resolver whose rule turns on the build reads to pick this
    one. ``revision`` is the short commit hash the card's citations were read
    at. ``stamp`` is the identity the build itself carries: the whole string
    constant the deployed binary holds, NUL-delimited, which
    ``tests/test_standalone_build_tripwire.py`` searches the binary for so a
    deployed build is held against the revision the card was written from
    with nothing run. What that constant says is the emulator's own choice —
    Vita3K's names the commit in its version string, DuckStation's legacy build
    carries no commit hash at all and states the commit's date instead — so
    the stamp is stated beside the revision rather than derived from it. A
    build that moved past every pin fails the search rather than letting the
    card describe source the machine no longer runs. ``citation`` is how the
    release and the stamp were tied to the commit, and ``provenance`` the
    sentence an answer read under this build carries in its ``sources`` — the
    card-level one being what an answer carries where no pinned build applied.

    A sibling of :class:`atlas.core_firmware.CoreFirmwareBuild` rather than that
    class: the two pin the same kind of fact and read it through different
    seams, and one shared class would tie a save card's shape to what a libretro
    entry happens to need.
    """

    release: str
    revision: str
    stamp: str
    citation: str
    provenance: str


@dataclass(frozen=True, slots=True)
class StandaloneSaveCard:
    """One standalone emulator's save knowledge: config file, systems, citations.

    ``settings`` names the configuration file the resolver reads the way the
    emulator does — by **name**, with its address stated once in
    ``atlas/data/emulator_settings.json``, because the save, texture, mod and
    firmware answers of one emulator open the same file and each carrying its
    own copy is how two of them came to disagree about it. It is ``None`` for
    an emulator whose save tree is fixed by the build rather than by any file
    (PPSSPP's Linux memstick is a compiled-in XDG join): naming a file the
    resolver never reads would state a governing config that does not govern.
    ``systems`` is the closed list of catalogue systems this card answers
    for: an emulator can serve several with different trees (Dolphin keeps
    GameCube cards and a Wii NAND), and a system outside the list is a
    question the card does not answer, stated rather than stretched.

    There is deliberately no ``flatpak`` field, since #288. Which app id an
    arrangement installs an emulator as is a property of the **installation**
    and of no single question, so it is stated once in
    ``atlas/data/emulator_settings.json``, beside the directory spellings that
    are keyed by that very id. It sat here until then, which is why an
    emulator with no save card could reach no trees of its own at all: MAME's
    savestate card is complete and its save card does not exist, and the
    savestate answer refused a launch it could have read.

    ``citations`` are the emulator's own source references the **resolver**
    speaks — the line ranges that go into an answer's caveats and readings —
    keyed by the slot the code asks for. They live on the card because a
    resolver can be shared by two emulators that are not the same source:
    PrimeHack is a Dolphin fork with Dolphin's save shape, read by Dolphin's
    resolver, and every file it inherits sits at different lines than in the
    Dolphin release beside it. A shared reading with one hard-coded citation
    would state one build's evidence for the other's answer.

    A citation belongs to the **build** rather than to the emulator, which is
    why the reserved ``installations`` key states one set per flatpak app id,
    exactly as the directory name does: the PrimeHack revision RetroDECK
    builds and the one Flathub ships are three years apart, and all seven of
    the lines a save answer names differ between them.
    """

    token: str
    settings: str | None
    systems: tuple[str, ...]
    provenance: str
    citations: Mapping[str, str] = field(default_factory=dict)
    citation_installations: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    redirects: tuple[SaveRedirect, ...] | None = None
    """The routes one launch can move this emulator's saves by — empty where its source
    offers none, and ``None`` for a card that states no redirect block at all, which no
    packaged card is: ``tests/test_standalone_saves.py`` holds every one to stating it.
    """
    redirects_provenance: str | None = None
    """What was searched for the routes, and every route left out with the reason it was."""
    builds: tuple[StandaloneBuild, ...] = ()
    """The builds the card's readings were taken at, where the card pins any.

    Empty is a card that pins no build, which no packaged card is today: the
    pin is only worth stating where something can read the deployed build's
    own answer back and compare it, and every deployed standalone binary
    carries a stamp that can. The tripwire's ``UNPINNED`` set is what keeps an
    absence deliberate, with the reason its binary carries no stamp. More
    than one is a card whose emulator is in the field at builds that answer
    differently — Vita3K picks its user by one rule at 3996 and another at
    4103 — and whose resolver reads which one runs.
    """

    def build_for(self, release: str | None) -> StandaloneBuild | None:
        """The pinned build a deploy's *release* names, or ``None`` — no fuzzy matching."""
        return next((build for build in self.builds if build.release == release), None)

    def cite(self, slot: str, *, flatpak: str | None) -> str:
        """The card's citation for one slot, in the build this launch runs.

        *flatpak* has no default for the same reason ``user_directory``'s has
        none: a reading that forgot it would name the arrangement's own
        build's lines for an answer about somebody else's, and look exactly
        like a verified one. Raises for a slot the card does not state — the
        card and the code shipped out of step.
        """
        stated = self.citation_installations.get(flatpak or "", self.citations)
        citation = stated.get(slot)
        if citation is None:
            raise ValueError(
                f"standalone save card {self.token!r} states no {slot!r} citation for "
                f"{flatpak or 'the arrangement own build'} — the resolver reading it names "
                "that source in its answer, and the card and the code shipped out of step"
            )
        return citation


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _stated(value: Any, where: str) -> str:
    """A string something can be held against — the blank one refused too.

    Stricter than the ``_expect_str`` the rest of this file shares, and used
    for the pinned builds and the redirect routes: a stamp of spaces is a run
    any binary may hold between two NULs, so a card pinning one could pass the
    tripwire against a build it never described, and a revision of spaces is
    a substring of every citation there is — a pin that reads as checked and
    checks nothing, which is worse than the absent pin it was meant to
    replace. A citation, a provenance, a flag or an argument of spaces is one
    nobody can follow or pass, the same reason the core-firmware table
    refuses a blank citation.
    """
    stated = _expect_str(value, where)
    if not stated.strip():
        raise ValueError(f"{where}: expected a non-blank string, got {value!r}")
    return stated


_BUILD_KEYS = frozenset({"release", "revision", "stamp", "citation", "provenance"})


def _build(where: str, entry: Any) -> StandaloneBuild:
    """One pinned build — exactly release, revision, stamp, citation and provenance.

    A block of some other shape is refused by name rather than read for what
    can be found in it: a misspelled key would otherwise read as a build with
    no pin, and a pin nobody checks is the one failure this block exists to
    prevent.
    """
    if not isinstance(entry, dict) or frozenset(entry) != _BUILD_KEYS:
        raise ValueError(
            f"{where}: a build must state exactly release/revision/stamp/citation/provenance, "
            f"got {entry!r}"
        )
    return StandaloneBuild(
        release=_stated(entry["release"], f"{where}: build.release"),
        revision=_stated(entry["revision"], f"{where}: build.revision"),
        stamp=_stated(entry["stamp"], f"{where}: build.stamp"),
        citation=_stated(entry["citation"], f"{where}: build.citation"),
        provenance=_stated(entry["provenance"], f"{where}: build.provenance"),
    )


def _builds(where: str, entry: Any) -> tuple[StandaloneBuild, ...]:
    """The optional list of pinned builds — absent, or one build per release.

    A card that states no list pins nothing, which the tripwire's ``UNPINNED``
    set is what keeps deliberate. An empty list is refused rather than read as
    that absence, because it states a pin and pins nothing. Two builds under
    one release would leave the resolver two rules for one deploy, and two
    under one revision or one stamp would be one build stated twice — and two
    builds the search cannot tell apart — so all three are refused.
    """
    if entry is None:
        return ()
    if not isinstance(entry, list) or not entry:
        raise ValueError(f"{where}: builds must be a non-empty list, got {entry!r}")
    builds = tuple(_build(where, build) for build in entry)
    for field_name in ("release", "revision", "stamp"):
        stated = [getattr(build, field_name) for build in builds]
        if len(set(stated)) != len(stated):
            raise ValueError(f"{where}: builds state one {field_name} twice: {stated!r}")
    return builds


_REDIRECTS_KEYS = frozenset({"routes", "provenance"})
_ROUTE_KEYS = frozenset({"via", "name", "argument", "moves", "citation"})
_ROUTE_OPTIONAL_KEYS = frozenset({"outranked_by"})
_SETTING_KEYS = frozenset({"file", "key"})


def _outranked_by(where: str, entry: Any, settings: str | None) -> tuple[RedirectSetting, ...]:
    """A route's optional ``outranked_by`` list — each setting in the file the card names.

    A setting in some other file is refused: the resolver compares only the
    configuration it reads for the answer, which is the card's own file, so a
    setting named elsewhere is one no comparison could ever reach.
    """
    if entry is None:
        return ()
    if not isinstance(entry, list) or not entry:
        raise ValueError(f"{where}: route.outranked_by must be a non-empty list, got {entry!r}")
    stated: list[RedirectSetting] = []
    for setting in entry:
        if not isinstance(setting, dict) or frozenset(setting) != _SETTING_KEYS:
            raise ValueError(f"{where}: an outranking setting must state exactly file/key, got {setting!r}")
        file = _stated(setting["file"], f"{where}: route.outranked_by.file")
        if file != settings:
            raise ValueError(
                f"{where}: route.outranked_by names {file!r}, and the card's settings file is "
                f"{settings!r} — the one file the answer reads"
            )
        stated.append(RedirectSetting(file=file, key=_stated(setting["key"], f"{where}: route.outranked_by.key")))
    if len(set(stated)) != len(stated):
        raise ValueError(f"{where}: route.outranked_by names one setting twice: {stated!r}")
    return tuple(stated)


def _route(where: str, entry: Any, settings: str | None) -> SaveRedirect:
    """One stated route — exactly via, name, argument, moves and citation, each checked.

    A value outside the vocabulary is refused rather than carried: a scope
    the contract cannot name is one a caller could not branch on, and
    :class:`SaveRedirect` refuses it the same way when built by hand.
    """
    if not isinstance(entry, dict) or not _ROUTE_KEYS <= frozenset(entry) <= _ROUTE_KEYS | _ROUTE_OPTIONAL_KEYS:
        raise ValueError(
            f"{where}: a route must state exactly via/name/argument/moves/citation, and may state "
            f"outranked_by, got {entry!r}"
        )
    via = _stated(entry["via"], f"{where}: route.via")
    if via not in REDIRECT_VIAS:
        raise ValueError(f"{where}: route.via must be one of {REDIRECT_VIAS}, got {via!r}")
    name = entry["name"]
    if name is not None:
        name = _stated(name, f"{where}: route.name")
    moves = entry["moves"]
    if not isinstance(moves, list) or not all(isinstance(scope, str) for scope in moves):
        raise ValueError(f"{where}: route.moves must be a list of strings, got {moves!r}")
    return SaveRedirect(
        via=via,
        name=name,
        argument=_stated(entry["argument"], f"{where}: route.argument"),
        moves=tuple(moves),
        citation=_stated(entry["citation"], f"{where}: route.citation"),
        outranked_by=_outranked_by(where, entry.get("outranked_by"), settings),
    )


def _redirects(
    where: str, entry: Any, settings: str | None
) -> tuple[tuple[SaveRedirect, ...], str] | tuple[None, None]:
    """The redirect block — the routes, and what was searched to state them.

    An absent block states nothing, and the card carries ``None`` for both
    rather than an empty list, because an empty list is the stated none and
    is only stated with the provenance that backs it. A block of any other
    shape is refused by name. Two routes with one spelling and one argument
    are one route stated twice, and refused.
    """
    if entry is None:
        return None, None
    if not isinstance(entry, dict) or frozenset(entry) != _REDIRECTS_KEYS:
        raise ValueError(
            f"{where}: a 'redirects' block must state exactly routes/provenance, got {entry!r}"
        )
    routes = entry["routes"]
    if not isinstance(routes, list):
        raise ValueError(f"{where}: redirects.routes must be a list, got {routes!r}")
    stated = tuple(_route(where, route, settings) for route in routes)
    spellings = [(route.via, route.name, route.argument) for route in stated]
    if len(set(spellings)) != len(spellings):
        raise ValueError(f"{where}: redirects.routes states one route twice: {spellings!r}")
    return stated, _stated(entry["provenance"], f"{where}: redirects.provenance")


def _card(token: str, entry: Any) -> StandaloneSaveCard:
    """One emulator's card — validated, never coerced."""
    where = f"standalone save card {token!r}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object, got {entry!r}")
    saves = entry.get("saves")
    if not isinstance(saves, dict):
        raise ValueError(f"{where}: expected a 'saves' object, got {saves!r}")
    settings = saves.get("settings")
    if settings is not None:
        settings = _expect_str(settings, f"{where}: saves.settings")
    systems = saves.get("systems")
    if not isinstance(systems, list) or not systems:
        raise ValueError(f"{where}: saves.systems must be a non-empty list, got {systems!r}")
    if "flatpak" in entry:
        raise ValueError(
            f"{where}: which app id an arrangement installs this emulator as is stated once in "
            "atlas/data/emulator_settings.json — a copy here could only ever drift from it, and "
            "an emulator without a save card could not state it at all (#288)"
        )
    if "build" in entry:
        raise ValueError(
            f"{where}: a pinned build is stated in a 'builds' list since schema 3 — a lone "
            "'build' block would read as a card that pins nothing"
        )
    provenance = entry.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError(f"{where}: expected a 'provenance' object, got {provenance!r}")
    stated_citations = saves.get("citations", {})
    if not isinstance(stated_citations, dict):
        raise ValueError(f"{where}: expected a 'saves.citations' object, got {stated_citations!r}")
    # Copied rather than popped: the caller's parsed document is theirs, and a
    # loader that empties a key out of it makes a second load of the same
    # object see a card without its overrides.
    installations = stated_citations.get("installations", {})
    citations = {k: v for k, v in stated_citations.items() if k != "installations"}
    if not isinstance(installations, dict):
        raise ValueError(
            f"{where}: expected a 'saves.citations.installations' object, got {installations!r}"
        )
    for app_id, stated in installations.items():
        _expect_str(app_id, f"{where}: saves.citations.installations key")
        if not isinstance(stated, dict) or set(stated) != set(citations):
            raise ValueError(
                f"{where}: saves.citations.installations[{app_id!r}] must state the same slots "
                f"as the default set {sorted(citations)} — a partial override reads as one "
                "build's evidence and answers with another's"
            )
    redirects, redirects_provenance = _redirects(where, entry.get("redirects"), settings)
    return StandaloneSaveCard(
        token=token,
        settings=settings,
        systems=tuple(_expect_str(s, f"{where}: saves.systems[]") for s in systems),
        provenance=_expect_str(provenance.get("source"), f"{where}: provenance.source"),
        citations={
            _expect_str(slot, f"{where}: saves.citations key"): _expect_str(
                citation, f"{where}: saves.citations[{slot!r}]"
            )
            for slot, citation in citations.items()
        },
        citation_installations={
            app_id: {
                slot: _expect_str(
                    citation, f"{where}: saves.citations.installations[{app_id!r}][{slot!r}]"
                )
                for slot, citation in stated.items()
            }
            for app_id, stated in installations.items()
        },
        redirects=redirects,
        redirects_provenance=redirects_provenance,
        builds=_builds(where, entry.get("builds")),
    )


def load_standalone_saves(text: str | None = None) -> tuple[StandaloneSaveCard, ...]:
    """Load the packaged standalone save cards (or *text* when supplied, for tests)."""
    if text is None:
        text = packaged_text("standalone_saves.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != SAVES_SCHEMA:
        raise ValueError(
            f"standalone_saves: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {SAVES_SCHEMA})"
        )
    return tuple(_card(token, entry) for token, entry in raw.get("emulators", {}).items())


_PACKAGED: tuple[StandaloneSaveCard, ...] | None = None


def lookup_standalone_save_card(token: str | None) -> StandaloneSaveCard | None:
    """The packaged card for one emulator token, or ``None`` — no fuzzy matching."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_standalone_saves()
    if token is None:
        return None
    return next((card for card in _PACKAGED if card.token == token), None)
