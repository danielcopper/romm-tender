"""Atlas catalogue adapter — the seam through which emulator questions reach the resolver.

The single place the vendored `emu-atlas <https://github.com/danielcopper/emu-atlas>`_
resolver is asked what a frontend's catalogue declares for a system: the emulator
list behind the picker, the system-layer default the launch bakes, the libretro
core the firmware filter keys on, the accept-list a candidate file is matched
against, and whether one game's file starts with the entry Tender starts it
through. Services see :class:`domain.emulator_commands.EmulatorOption` values and
plain sets and never an atlas type — ``domain/`` may not import ``_vendor`` at
all (the ``domain-stdlib-only`` contract), so the vocabulary and the resolver
have to meet at an adapter.

Three properties of the resolver decide this module's shape.

**It answers per installation, and picking one is not its job.** ``detect``
returns every arrangement it found and never a winner, so every question goes
to the source a reading of :mod:`adapters.emulator_sources` names as the
answering one. A panel call takes a fresh reading per question; a run that asks
the same questions for many games hands its one reading in, and every answer
is then asked once for the whole run.

**Its entry order is the EFFECTIVE one, and the default is the declared
first.** A gamelist ``<altemulator>`` or a system-level ``<alternativeEmulator>``
promotes an entry to the front of the answer. The rows are listed in that order,
with the promoted entry marked as the user's choice in ES-DE, while the default
is the entry the source ships first, read off ``declared_index``, which
promotion never touches — a choice made in ES-DE never moves Tender's launch
(ADR-0012). Never ``entries[0]`` for the default, and never an assumption that
index 0 exists: upstream mirrors ES-DE's own walk, where an empty-text
``<command>`` holds a position without yielding an entry and a duplicate label
takes none.

**It never logs, and it raises on its own invariant violations** rather than
returning a degraded answer. Caveats are its whole degradation channel, and their
``code`` is the stable half of that contract while ``message`` is prose that may
change freely: the codes reaching the log go through the injected debug logger,
and nothing here parses a message. Every resolver call is wrapped, and a failure
becomes the same answer an unreadable catalogue gives — "unavailable", never an
empty list a caller would read as "this frontend knows no emulator".

An empty entry list is several different facts and the codes tell them apart, so
:data:`_CATALOGUE_REFUSALS` is what decides "unavailable". An empty ``caveats``
is NOT that test: a broken installation states its health findings on every
answer it gives, so "read, and it declares nothing" routinely arrives with a
caveat list that is not empty.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from _vendor.atlas import (
    AVAILABILITY_NOT_INSTALLED,
    AVAILABILITY_STARTABLE,
    CAVEAT_ARCHIVE_CONTENTS_UNREAD,
    CAVEAT_ARRANGEMENT_VERSION_DRIFTED,
    CAVEAT_COMMAND_REWRITTEN_AT_START,
    CAVEAT_CORE_FILE_FOREIGN,
    CAVEAT_CORE_NOT_INSTALLED,
    CAVEAT_CORE_RULES_MISSING,
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
    CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED,
    CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
    CAVEAT_EMULATOR_NOT_FOUND,
    CAVEAT_EMULATOR_RULES_MISSING,
    CAVEAT_ENTRY_FORMAT_UNCLAIMED,
    CAVEAT_ENTRY_FORMAT_UNESTABLISHED,
    CAVEAT_FIND_RULES_REWRITTEN_AT_START,
    CAVEAT_FIND_RULES_UNREADABLE,
    CAVEAT_LAUNCH_COMMAND_LABEL_UNKNOWN,
    CAVEAT_LAUNCH_PATH_ASSUMED,
    CAVEAT_LAUNCH_PATH_UNESTABLISHED,
    CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED,
    CAVEAT_LAUNCHER_SCRIPT_MISSING,
    CAVEAT_PROGRAM_NOT_FOUND,
    CAVEAT_ROM_PATH_UNDECLARED,
    CAVEAT_RUN_GAME_DIFFERS,
    CAVEAT_SYSTEM_UNKNOWN,
    HEALTH_ISSUE_CATALOGUE_INVALID,
    HEALTH_ISSUE_NOT_SET_UP,
    KIND_LIBRETRO,
    KIND_RETROARCH_FOREIGN_CORE,
    KIND_STANDALONE,
    VERDICT_ENTRY_NOT_ACCEPTED,
    VERDICT_LAUNCHABLE,
    VERDICT_NEEDS_INSTALLATION,
    VERDICT_NOT_ACCEPTED,
)

from adapters.atlas_identity import emulator_identity
from domain.emulator_commands import (
    CORE,
    FOREIGN_CORE,
    NOT_ESTABLISHED,
    NOT_ESTABLISHED_FIND_RULES_UNREAD,
    NOT_ESTABLISHED_LOOKUP_UNSUPPORTED,
    NOT_ESTABLISHED_NO_FIND_RULE,
    NOT_ESTABLISHED_PATH_UNSEEN,
    NOT_INSTALLED,
    PROGRAM,
    classify_command,
    default_option,
    option_to_invocation,
)
from domain.emulator_sources import (
    CATALOGUE_INVALID,
    CATALOGUE_NOT_SET_UP,
    CATALOGUE_SEALED,
    CATALOGUE_UNAVAILABLE,
)
from domain.launchability import (
    CATALOGUE_UNREAD,
    ENTRY_UNKNOWN,
    NEEDS_INSTALLING,
    NO_SOURCE,
    NOT_A_LAUNCH_FILE,
    NOT_THIS_EMULATOR,
    STARTS,
    SYSTEM_UNKNOWN,
    Launchability,
)
from domain.launchability import NOT_ESTABLISHED as LAUNCH_NOT_ESTABLISHED
from domain.shortcut_data import RETRODECK_APP_ID

if TYPE_CHECKING:
    from collections.abc import Callable

    from adapters.emulator_sources import EmulatorSourcesAdapter
    from domain.emulator_commands import EmulatorOption
    from domain.emulator_sources import SourcesReading
    from domain.shortcut_data import EmulatorInvocation

# The codes on which Tender declines to answer from a catalogue. The first
# four are the resolver's own "nobody could answer" family: the arrangement ships
# none, atlas has not established where it keeps one, the one it has could not be
# read, or part of it sits where atlas does not open. Only their absence makes an
# empty entry list a statement about the machine — "the catalogue was read and
# this frontend knows no emulator for this system" — and that is the one empty
# the picker may render as an empty list.
#
# ``catalogue-invalid`` is Tender's own addition, and it reads the same
# answer differently from the resolver on purpose. It means a catalogue file
# ES-DE refuses its whole load on — one that does not parse, or one carrying no
# document-level ``<systemList>`` — so ES-DE runs with no systems at all, and
# atlas states that truthfully as an empty enumeration. Taken at face value
# Tender would report "this frontend knows no emulator" for every platform, and
# ``is_known_system`` would answer a positive False that the candidate search
# reads as a denial. What the user actually has is one typo in one file, most
# likely their own ``custom_systems/es_systems.xml`` — a file the deleted parser
# never read, so this is new exposure — and "could not establish" is the honest
# word for a state a single edit fixes. It is also what the deleted parser
# answered: an unparsable file and a wrong root tag both yielded "unavailable".
#
# ``not-set-up`` is a RetroDECK the resolver found by its Flatpak deploy with no
# ``retrodeck.json``: it has not been started for this home, or its first-run
# setup stopped before the end. The resolver answers every question about it
# with that finding and an empty enumeration, which says nothing about the
# emulators it will offer once set up — read as a list, it would be "knows no
# emulator" and the same positive False from ``is_known_system``.
#
# ``emulator-catalogue-exclusive`` is deliberately NOT here: it says a custom
# es_systems.xml declared itself the whole catalogue, so the answer is COMPLETE
# and merely small.
_CATALOGUE_REFUSALS = frozenset(
    {
        CAVEAT_EMULATOR_CATALOGUE_UNAVAILABLE,
        CAVEAT_EMULATOR_CATALOGUE_UNESTABLISHED,
        CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
        CAVEAT_EMULATOR_CATALOGUE_SEALED,
        HEALTH_ISSUE_CATALOGUE_INVALID,
        HEALTH_ISSUE_NOT_SET_UP,
    }
)

# Why the resolver could not establish whether what an entry launches is there,
# each in Tender's word for it. An entry states exactly one reason, ahead of its
# notes; a reason this map does not hold reads as plain ``NOT_ESTABLISHED``.
_NOT_ESTABLISHED_BECAUSE = {
    CAVEAT_EMULATOR_RULES_MISSING: NOT_ESTABLISHED_NO_FIND_RULE,
    CAVEAT_CORE_RULES_MISSING: NOT_ESTABLISHED_NO_FIND_RULE,
    CAVEAT_FIND_RULES_UNREADABLE: NOT_ESTABLISHED_FIND_RULES_UNREAD,
    CAVEAT_LAUNCH_RESOLUTION_UNSUPPORTED: NOT_ESTABLISHED_LOOKUP_UNSUPPORTED,
    CAVEAT_LAUNCH_PATH_UNESTABLISHED: NOT_ESTABLISHED_PATH_UNSEEN,
}

# The reasons beside ``not-installed``, deliberately not told apart: each says
# what the entry launches is not there to start it, and the panel words that
# one way.
_NOT_INSTALLED_BECAUSE = frozenset(
    {CAVEAT_EMULATOR_NOT_FOUND, CAVEAT_PROGRAM_NOT_FOUND, CAVEAT_LAUNCHER_SCRIPT_MISSING, CAVEAT_CORE_NOT_INSTALLED}
)

# Notes beside a verdict. Only ``run-game-differs`` naming nothing is acted on
# (:func:`_start_script_runs_nothing`); naming a path, it says RetroDECK's
# ``run_game.sh`` would run that rather than what ES-DE runs, which changes
# nothing here. The other three state assumptions of a frontend on the host
# (EmuDeck's), which starts no game through Tender.
_LAUNCH_NOTES = frozenset(
    {
        CAVEAT_RUN_GAME_DIFFERS,
        CAVEAT_LAUNCH_PATH_ASSUMED,
        CAVEAT_FIND_RULES_REWRITTEN_AT_START,
        CAVEAT_COMMAND_REWRITTEN_AT_START,
    }
)

_CORE_SO_SUFFIX = ".so"

# The resolver's word for what an entry launches, in Tender's. A word this map
# does not hold reads as a program of its own.
_KINDS = {KIND_LIBRETRO: CORE, KIND_STANDALONE: PROGRAM, KIND_RETROARCH_FOREIGN_CORE: FOREIGN_CORE}

# The launchability verdicts, each in Tender's word. A verdict this map does not
# hold reads as not established — the resolver's own rule for a value a client
# does not know, and its ``unknown`` is exactly that.
_LAUNCH_STATES = {
    VERDICT_LAUNCHABLE: STARTS,
    VERDICT_NEEDS_INSTALLATION: NEEDS_INSTALLING,
    VERDICT_NOT_ACCEPTED: NOT_A_LAUNCH_FILE,
    VERDICT_ENTRY_NOT_ACCEPTED: NOT_THIS_EMULATOR,
}

# Why a verdict could not be established, in Tender's word, the first code
# found winning. A label the system's entries do not carry is the caller's own
# question going wrong, so it is named before what the catalogue says of the
# system; ``rom-path-undeclared`` beside ``system-unknown`` says the same
# system is not one the frontend declares.
_NOT_ESTABLISHED_LAUNCH_BECAUSE = (
    (CAVEAT_LAUNCH_COMMAND_LABEL_UNKNOWN, ENTRY_UNKNOWN),
    (CAVEAT_SYSTEM_UNKNOWN, SYSTEM_UNKNOWN),
    (CAVEAT_ROM_PATH_UNDECLARED, SYSTEM_UNKNOWN),
)

# Codes a launchability answer carries that change nothing Tender says.
# ``entry-format-unclaimed`` stands on a verdict that lets the file start:
# RetroArch hands a direct load to the core whatever it claims. A foreign core's
# refusal (``core-file-foreign``) is the entry-not-accepted verdict it stands
# beside, worded as that verdict. ``arrangement-version-drifted`` says the
# installation's version is not the one the reading is pinned to, on every
# answer, and is no statement about the file.
_LAUNCH_NOTES_UNWORDED = frozenset(
    {CAVEAT_ENTRY_FORMAT_UNCLAIMED, CAVEAT_CORE_FILE_FOREIGN, CAVEAT_ARRANGEMENT_VERSION_DRIFTED}
)


def _unavailable(reason: str, source: dict[str, Any] | None) -> dict[str, Any]:
    """The picker's "emulator list unavailable" answer, built fresh each call.

    A module-level constant would hand every caller the same ``options`` list,
    which a caller is free to mutate.
    """
    return {"available": False, "options": [], "reason": reason, "source": source}


def _source_payload(reading: SourcesReading) -> dict[str, Any] | None:
    """The answering source as the picker answer names it: its kind, and whether Tender starts games through it."""
    answering = reading.answering
    if answering is None:
        return None
    return {"kind": answering.kind, "starts_games": answering.starts_games}


def _named_source_payload(reading: SourcesReading, kind: str) -> dict[str, Any] | None:
    """The detected source of *kind* as the picker answer names it, or ``None`` where none is detected."""
    named = next((source for source in reading.sources if source.kind == kind), None)
    if named is None:
        return None
    return {"kind": named.kind, "starts_games": named.starts_games}


def _refusal_reason(answer: Any) -> str:
    """Which refusal a refused catalogue answer is, as the page words it."""
    codes = set(_caveat_codes(answer))
    if HEALTH_ISSUE_NOT_SET_UP in codes:
        return CATALOGUE_NOT_SET_UP
    if HEALTH_ISSUE_CATALOGUE_INVALID in codes:
        return CATALOGUE_INVALID
    if CAVEAT_EMULATOR_CATALOGUE_SEALED in codes:
        return CATALOGUE_SEALED
    return CATALOGUE_UNAVAILABLE


def _own_core_so(core_so: str) -> str:
    """Atlas's ``mgba_libretro.so`` in Tender's own identifier space.

    Every core identifier Tender holds — the resolved active core, an ES-DE
    ``<command>``'s core — is the bare ``.so`` basename without its extension.
    Comparing the two spellings without normalising here would silently match
    nothing.
    """
    return core_so[: -len(_CORE_SO_SUFFIX)] if core_so.endswith(_CORE_SO_SUFFIX) else core_so


def _caveat_codes(answer: Any) -> tuple[str, ...]:
    """Every stable caveat code the answer states."""
    return tuple(caveat.code for caveat in answer.caveats)


def _installation(entry: Any) -> str | None:
    """Tender's word for what the resolver states about *entry*'s program, ``None`` where it states it startable.

    Anything but ``startable`` and ``not-installed`` reads as not established —
    the resolver's own rule for a value a client does not know.
    """
    if entry.availability == AVAILABILITY_STARTABLE:
        return None
    if entry.availability == AVAILABILITY_NOT_INSTALLED:
        return NOT_INSTALLED
    return next(
        (_NOT_ESTABLISHED_BECAUSE[code] for code in _caveat_codes(entry) if code in _NOT_ESTABLISHED_BECAUSE),
        NOT_ESTABLISHED,
    )


def _start_script_runs_nothing(entry: Any) -> bool:
    """Whether the resolver states RetroDECK's ``run_game.sh`` would run nothing for *entry*.

    That is ``run-game-differs`` with an empty ``run_game_path``: the script
    would run nothing for the command's ``%EMULATOR_X%`` token, so a shortcut
    baked through its ``-e`` starts nothing.
    """
    return any(
        caveat.code == CAVEAT_RUN_GAME_DIFFERS and caveat.data.get("run_game_path") == "" for caveat in entry.caveats
    )


def _sandbox_launcher(entry: Any) -> str | None:
    """The path inside RetroDECK's sandbox the resolver states ES-DE runs for *entry*, or ``None``.

    The folder-boot bake execs it with ``flatpak run --command=`` in RetroDECK's
    sandbox, so a launcher spelled anywhere else is none. Nor is a launcher
    whose rule entry carries a replacement command: ES-DE then runs that
    command, and the path only selected it.
    """
    launcher = entry.launcher
    if launcher is None or launcher.app_id != RETRODECK_APP_ID or launcher.replacement_command is not None:
        return None
    return launcher.path


def _classify_entry(entry: Any, *, chosen_in_frontend: bool) -> EmulatorOption:
    """*entry* classified for bakeability, with everything the resolver states about it in Tender's words."""
    return classify_command(
        entry.label,
        entry.command,
        kind=_KINDS.get(entry.kind, PROGRAM),
        core_so=_own_core_so(entry.core_so) if entry.core_so else None,
        core_path=entry.core_path,
        emulator=emulator_identity(entry),
        installation=_installation(entry),
        launcher=_sandbox_launcher(entry),
        start_script_runs_nothing=_start_script_runs_nothing(entry),
        declared_index=entry.declared_index,
        chosen_in_frontend=chosen_in_frontend,
    )


def _frontend_choice(entries: tuple[Any, ...]) -> Any:
    """The entry the user chose inside ES-DE where it is not the one the source ships first, or ``None``.

    A choice puts the entry it names first in the answer and states it in that
    entry's ``selection``; choosing the shipped default changes nothing a
    surface needs to show.
    """
    if not entries or entries[0].selection is None:
        return None
    declared = [entry.declared_index for entry in entries if entry.declared_index is not None]
    first = entries[0]
    return None if declared and first.declared_index == min(declared) else first


def _launch_reason(answer: Any) -> str | None:
    """Why *answer*'s verdict could not be established, in Tender's word, or ``None`` where Tender words none."""
    codes = set(_caveat_codes(answer))
    reason = next((reason for code, reason in _NOT_ESTABLISHED_LAUNCH_BECAUSE if code in codes), None)
    if reason is None and catalogue_refused(answer):
        return CATALOGUE_UNREAD
    return reason


def _installer_name(installer: Any) -> str:
    """The emulator that installs a file: the system's entry that runs it, else the emulator as the resolver says it."""
    return installer.entry.label if installer.entry is not None else installer.emulator


def _launchability(answer: Any, *, system: str, content_path: str, label: str | None) -> Launchability:
    """*answer* in Tender's words."""
    state = _LAUNCH_STATES.get(answer.verdict, LAUNCH_NOT_ESTABLISHED)
    codes = set(_caveat_codes(answer))
    return Launchability(
        state=state,
        entry=label,
        system=system,
        file=os.path.basename(content_path),
        extension=answer.extension,
        starts_from=tuple(answer.accepted),
        other_entries=tuple(answer.alternatives),
        install_in=None if answer.installers is None else tuple(_installer_name(i) for i in answer.installers),
        reason=_launch_reason(answer) if state == LAUNCH_NOT_ESTABLISHED else None,
        contents_unread=CAVEAT_ARCHIVE_CONTENTS_UNREAD in codes,
        format_unestablished=CAVEAT_ENTRY_FORMAT_UNESTABLISHED in codes,
    )


def catalogue_refused(answer: Any) -> bool:
    """Whether the answer carries one of the catalogue refusals (:data:`_CATALOGUE_REFUSALS`).

    That is, no catalogue could be read, or what was read cannot be taken as the
    whole list: a sealed one, a systems file ES-DE refuses to load, or the empty
    answer of an installation that has not been set up. Public for
    :mod:`adapters.atlas_saves` and :mod:`adapters.emulator_sources`, which decline
    on the same codes: entries beside a refusal (EmuDeck's overlay beside
    ``emulator-catalogue-sealed``) are an incomplete list, and an incomplete
    list would look complete.
    """
    return not _CATALOGUE_REFUSALS.isdisjoint(_caveat_codes(answer))


class AtlasCatalogueAdapter:
    """Resolves the emulators a frontend catalogue declares, live, through the vendored resolver.

    Implements the ``CoreInfoProvider`` Protocol structurally, plus the three
    call-shaped system questions (:class:`services.protocols.SystemM3uSupportFn`,
    ``SystemSupportedExtensionsFn``, ``SystemKnownFn``) as bound methods.

    Resolution is system-layer only; Tender's own per-platform and per-game
    selections are layered on top by
    :class:`services.active_core_resolver.ActiveCoreResolver`, not here — and
    ES-DE's own selections are shown beside the default, never taken for it.

    Keeps no answer of its own. Every answer is asked through a reading of the
    emulator sources and kept by that reading alone: the one a caller hands in,
    or a fresh one taken for the call.
    """

    def __init__(
        self,
        *,
        sources: EmulatorSourcesAdapter,
        log_debug: Callable[[str], None],
    ) -> None:
        self._sources = sources
        self._log_debug = log_debug

    # -- public API ----------------------------------------------------------

    def get_active_core(
        self, system_name: str, *, reading: SourcesReading | None = None
    ) -> tuple[str | None, str | None]:
        """Resolve the system-layer libretro active core for a system.

        The libretro entry with the lowest declared position, as ``(core_so, label)``.
        Libretro-only by design — this feeds the firmware layer's system-level
        BIOS filter, which keys on a RetroArch core, not the launch-layer default
        (which may be a standalone emulator). ``(None, None)`` when the system
        offers no libretro entry, when the catalogue could not be read, or when
        no source answers.

        Reads the resolver's own ``kind``, not the bake classifier's: whether a
        command loads a core is a fact about the command, where bakeability is a
        fact about Tender's ``-e`` override. A libretro command Tender
        cannot bake still names the core the BIOS filter is about.

        **Carried, and currently read by nothing in production.** The firmware
        surfaces take the platform's own pick instead
        (:func:`domain.emulator_commands.resolve_firmware_option`), so what a
        pane names and what it judges by come off one choice. The silence is
        deliberate rather than an oversight: this is the only reading that
        answers with a libretro core where the platform's pick is a standalone
        emulator, and that is a different question from "which emulator is this
        platform about".
        """
        answer = self._catalogue_answer(system_name, self._reading(reading))
        if answer is None or catalogue_refused(answer):
            return (None, None)
        cores = [
            entry
            for entry in answer.entries
            if entry.kind == KIND_LIBRETRO and entry.core_so and entry.declared_index is not None
        ]
        first = min(cores, key=lambda entry: entry.declared_index, default=None)
        return (None, None) if first is None else (_own_core_so(first.core_so), first.label)

    def get_default_emulator(
        self, system_name: str, *, reading: SourcesReading | None = None
    ) -> EmulatorInvocation | None:
        """Resolve the system-layer default **emulator** (libretro OR standalone).

        The default (:func:`domain.emulator_commands.default_option`, the entry
        the source ships first) rendered into an :class:`EmulatorInvocation` — a
        libretro core or a standalone emulator. Returns ``None`` when that entry
        cannot be baked, when there is no default, or when the catalogue could not
        be read; the caller bakes the plain RetroDECK launch.

        Keeps the read-path/launch-path invariant: the resolved emulator is both
        what the ROM launches with and what derived values key on.
        """
        result = self.get_emulator_options(system_name, reading=reading)
        if not result["available"]:
            return None
        return option_to_invocation(default_option(result["options"]))

    def get_emulator_options(
        self,
        system_name: str,
        *,
        reading: SourcesReading | None = None,
        content_path: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Return every catalogue entry for a system, classified for bakeability.

        Returns ``{"available", "options", "reason", "source"}``. ``available``
        is ``False`` when no source answers, when the answer carries one of the
        six catalogue refusals, or when the resolver could not be asked at all —
        the caller surfaces that as "emulator list unavailable" rather than
        seeing an empty list it cannot distinguish from a system the frontend
        knows no emulator for. ``reason`` says which, and is ``None`` where the
        list is available: ``no_source`` / ``switched_off`` where no source
        answers, ``not_set_up`` / ``catalogue_invalid`` / ``sealed`` /
        ``unavailable`` for a refusal. ``source`` is the answering source's ``{"kind",
        "starts_games"}``, ``None`` where none answers.

        ``options`` is in the order the resolver gives: a choice the user made
        inside ES-DE first, marked ``chosen_in_frontend``, the rest as declared.
        Asked with *content_path*, a game's own choice in ES-DE counts too. The
        shipped default is the entry with the lowest ``declared_index``. What an
        entry launches, the core it loads and that core's path are the
        resolver's ``kind``, ``core_so`` and ``core_path``, and an entry it
        states ``run_game.sh`` would run nothing for is not bakeable. Whether
        what an entry launches is there is the resolver's ``availability``,
        libretro cores included: one it states not installed is
        ``needs_setup`` with ``not_installed``, so it bakes into no shortcut
        and, as the default, cannot start; one it could not
        establish carries a ``not_established`` note and is never the default
        by itself. Each entry's launcher is the one the resolver states. A
        system the catalogue does not declare yields ``available: True`` with
        an empty list.

        *source* names a detected source to ask instead of the answering one,
        for a surface that shows every source's own default; ``source`` in the
        answer is then that one, and a kind not detected answers
        ``unavailable`` with no source.
        """
        reading = self._reading(reading)
        payload = _source_payload(reading) if source is None else _named_source_payload(reading, source)
        if payload is None:
            return _unavailable(reading.no_answer_reason() if source is None else CATALOGUE_UNAVAILABLE, None)
        answer = self._catalogue_answer(system_name, reading, content_path=content_path, source=source)
        if answer is None:
            return _unavailable(CATALOGUE_UNAVAILABLE, payload)
        if catalogue_refused(answer):
            return _unavailable(_refusal_reason(answer), payload)
        chosen = _frontend_choice(answer.entries)
        options = [_classify_entry(entry, chosen_in_frontend=entry is chosen) for entry in answer.entries]
        return {"available": True, "options": options, "reason": None, "source": payload}

    def sources_reading(self) -> SourcesReading:
        """A fresh reading of the emulator sources, for a call that asks every source in the user's order."""
        return self._sources.read()

    def system_supports_m3u(self, system_name: str) -> bool:
        """True iff the catalogue lists ``.m3u`` as a supported extension for *system_name*.

        Reads the same per-system accept-list ES-DE uses to decide
        directory-collapse, so the answer can never disagree with ES-DE. Returns
        ``False`` when the system is unknown or the catalogue could not be read
        (default-safe: a missing playlist only degrades; a wrong one breaks the
        launch).
        """
        return ".m3u" in self.get_supported_extensions(system_name)

    def is_known_system(self, system_name: str) -> bool | None:
        """Whether the frontend's catalogue declares *system_name*.

        ``None`` when the catalogue could not be read at all — the caller must not
        read a source that did not answer as a denial, which is the same
        default-safe rule :meth:`get_supported_extensions` follows by returning an
        empty set. ``False`` is therefore a positive statement: this catalogue was
        read, and it does not name this system.
        """
        answer = self._systems_answer(self._sources.read())
        if answer is None or catalogue_refused(answer):
            return None
        return system_name in answer.systems

    def get_supported_extensions(self, system_name: str, *, reading: SourcesReading | None = None) -> frozenset[str]:
        """Return the extensions the catalogue accepts for *system_name*, verbatim.

        Reads the same per-system declaration ES-DE consults, so a caller can
        intersect it with the disc-image set and never offer a disc the emulator
        cannot launch. The tokens are ES-DE's own, both cases listed where the
        file lists both, and ES-DE compares them case-exactly. Returns an empty
        frozenset for an unknown system or when the catalogue could not be read
        (default-safe: the caller falls back to the full disc set).
        """
        placement = self._rom_location(system_name, self._reading(reading))
        if placement is None:
            return frozenset()
        return frozenset(placement.extensions)

    def launchability(
        self, system_name: str, content_path: str, *, label: str, reading: SourcesReading | None = None
    ) -> Launchability:
        """Whether *content_path* starts as *system_name*'s content with the entry *label* names.

        The verdict is the answering source's, asked with the entry Tender
        starts the game through, so it is about that entry rather than the one
        ES-DE would choose. Where no source answers, the verdict is not
        established for that reason; where the resolver could not be asked, it
        is not established with no reason named.
        """
        reading = self._reading(reading)
        installation = self._installation(reading)
        if installation is None:
            return Launchability(
                state=LAUNCH_NOT_ESTABLISHED,
                entry=label,
                system=system_name,
                file=os.path.basename(content_path),
                extension="",
                reason=NO_SOURCE,
            )

        def ask() -> Any:
            subject = f"launchable({system_name!r}, label={label!r})"
            answer = self._ask(lambda: installation.launchable(system_name, content_path, label=label), subject)
            if answer is not None:
                self._trace(system_name, f"verdict={answer.verdict}", answer)
            return answer

        answer = reading.remember(("launchable", system_name, content_path, label), ask)
        if answer is None:
            return Launchability(
                state=LAUNCH_NOT_ESTABLISHED,
                entry=label,
                system=system_name,
                file=os.path.basename(content_path),
                extension="",
            )
        return _launchability(answer, system=system_name, content_path=content_path, label=label)

    # -- helpers -------------------------------------------------------------

    def _reading(self, reading: SourcesReading | None) -> SourcesReading:
        """The reading handed in, or a fresh one for this call."""
        return reading if reading is not None else self._sources.read()

    def _trace(self, subject: str, shape: str, answer: Any) -> None:
        """Trace one answer's shape and the stable caveat codes it states.

        Every answer the resolver gives is traced, not only the catalogue's: the
        resolver never logs, so a code that reaches no log reaches nobody.
        """
        self._log_debug(f"[catalogue] {subject}: {shape} caveats={sorted(set(_caveat_codes(answer)))}")

    def _trace_undeclared(self, system_name: str, entries: tuple[Any, ...]) -> None:
        """Say when a listing carries entries no layer declared a position for.

        Such an entry has no shipped place to sort by and cannot become the
        default, so the picker's list is one nothing chose an order for. That is
        a statement worth having in the log beside the codes, because it is the
        one shape where "no default" is not a bug.
        """
        undeclared = [entry.label for entry in entries if entry.declared_index is None]
        if undeclared:
            self._log_debug(
                f"[catalogue] {system_name}: {len(undeclared)} of {len(entries)} entries carry no declared "
                f"position and can hold no default: {undeclared}"
            )

    def _ask(self, question: Callable[[], Any], subject: str) -> Any:
        """Put one question to the resolver, or answer ``None`` where it could not be asked.

        Deliberately broad: the resolver's failure modes are its own invariant
        assertions and its packaged-data loaders, neither of which is an
        exception type this adapter should enumerate. The honest answer to "we
        could not ask" is the same whatever raised, and every caller of this
        already has a ``None`` branch — the one an unreadable catalogue takes.
        """
        try:
            return question()
        except Exception as exc:
            self._log_debug(f"[catalogue] resolver failed on {subject}: {exc!r}")
            return None

    def _installation(self, reading: SourcesReading, source: str | None = None) -> Any:
        """The installation of *source*, or of the answering source; ``None`` where there is none."""
        if source is not None:
            return reading.installation(source)
        installation = reading.answering_installation()
        if installation is None:
            self._log_debug(f"[catalogue] no emulator source answers ({reading.no_answer_reason()})")
        return installation

    def _catalogue_answer(
        self, system_name: str, reading: SourcesReading, *, content_path: str | None = None, source: str | None = None
    ) -> Any:
        """The catalogue's answer for *system_name* through *reading*, or ``None`` with nothing to ask.

        Asked with *content_path* where a caller names one game: the answer then
        states that game's own choice in ES-DE (``<altemulator>``) as well.
        """
        installation = self._installation(reading, source)
        if installation is None:
            return None
        subject = f"emulators_for({system_name!r})"

        def ask() -> Any:
            answer = self._ask(lambda: installation.emulators_for(system_name, content_path=content_path), subject)
            if answer is not None:
                self._trace(system_name, f"entries={len(answer.entries)}", answer)
                self._trace_undeclared(system_name, answer.entries)
            return answer

        return reading.remember(("emulators_for", system_name, content_path, source), ask)

    def _systems_answer(self, reading: SourcesReading) -> Any:
        """Every system the catalogue declares through *reading*, or ``None`` with nothing to ask."""
        installation = self._installation(reading)
        if installation is None:
            return None

        def ask() -> Any:
            answer = self._ask(installation.systems, "systems")
            if answer is not None:
                self._trace("systems", f"declared={len(answer.systems)}", answer)
            return answer

        return reading.remember(("systems",), ask)

    def _rom_location(self, system_name: str, reading: SourcesReading) -> Any:
        """Where *system_name*'s content lives and what it accepts, through *reading*, or ``None``."""
        installation = self._installation(reading)
        if installation is None:
            return None

        def ask() -> Any:
            placement = self._ask(lambda: installation.rom_location(system_name), f"rom_location({system_name!r})")
            if placement is not None:
                self._trace(system_name, f"extensions={len(placement.extensions)}", placement)
            return placement

        return reading.remember(("rom_location", system_name), ask)
