"""Classify ES-DE ``<command>`` strings into safely-bakeable emulator options.

Given the label + command text of one ES-DE ``<command>``, decide whether
Tender can bake it into a Steam shortcut's ``-e`` override — a real emulator
invocation ending in ``%ROM%`` that RetroDECK's ``run_game.sh`` starts. That
rule is Tender's own, and it follows the script's handling of a command rather
than ES-DE's. The system-layer default is the first
*safely-bakeable* command in document order; the emulator picker offers every
command annotated with its bakeability and, when it cannot be baked, the reason.

What an entry launches, the core it loads and where that core lies, whether it
is installed, and whether ``run_game.sh`` would run anything for it are not
decided here: they are the resolver's answer, which
:mod:`adapters.atlas_catalogue` reads and hands in, in the words below.

Pure compute only — takes the ``(label, text)`` pairs of one catalogue entry,
which :mod:`adapters.atlas_catalogue` reads off ``es_systems.xml``, and returns
value objects. The launch-bake itself (``EmulatorInvocation`` → the ``-e``
string) lives in :mod:`domain.shortcut_data`; this module only decides *which*
command becomes that invocation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from domain.shortcut_data import EmulatorInvocation

_PLACEHOLDER_RE = re.compile(r"%[^%]+%")

# Placeholders ES-DE's ``run_game.sh`` resolves that a baked ``-e`` override can
# safely carry verbatim. ``%EMULATOR_*%`` (any emulator binary token, e.g.
# ``%EMULATOR_RPCS3%``) is accepted by prefix, not listed here. ``%STARTDIR%``
# is "known" (so it is not flagged as an unknown placeholder) but is rejected on
# its own by the dedicated startdir rule — run_game.sh parses-but-drops it, so a
# baked command that relies on it would launch from the wrong directory.
# ``%CORE_RETROARCH%`` is known but never reaches a core entry's bake, because
# run_game.sh writes it as an empty folder (docs/architecture/
# core-emulator-selection.md, "Application"): the reference is written as the
# resolver's core path instead (:func:`_with_core_path`).
_KNOWN_PLACEHOLDERS = frozenset(
    {
        "%ROM%",
        "%CORE_RETROARCH%",
        "%GAMEDIR%",
        "%GAMEDIRRAW%",
        "%ROMPATH%",
        "%BASENAME%",
        "%FILENAME%",
        "%ROMRAW%",
        "%STARTDIR%",
    }
)

# What the resolver states about whether an entry's program is there, in
# Tender's words. ``NOT_INSTALLED`` withholds the entry whatever its command.
# The rest say the resolver could not establish it, each naming the cause where
# the reading knows it and ``NOT_ESTABLISHED`` where it does not; such an entry
# stays selectable with a note and is never the default by itself (#2195 D9).
# The panel words each of them (``frontend/src/utils/emulatorInstallation.ts``),
# and ``tests/domain/test_emulator_commands.py`` holds the two lists equal.
NOT_INSTALLED = "not_installed"
NOT_ESTABLISHED = "not_established"
NOT_ESTABLISHED_NO_FIND_RULE = "no_find_rule"
NOT_ESTABLISHED_FIND_RULES_UNREAD = "find_rules_unread"
NOT_ESTABLISHED_LOOKUP_UNSUPPORTED = "lookup_unsupported"
NOT_ESTABLISHED_PATH_UNSEEN = "path_unseen"

# Why an entry the resolver describes cannot be baked, beside the reasons a
# command's own text earns. ``CORE_UNLOCATED``: a core entry the resolver gives
# no core path for, so there is nothing to write where the command names the
# core. ``START_SCRIPT_RUNS_NOTHING``: the resolver states RetroDECK's
# ``run_game.sh`` would run nothing for the entry's emulator. The panel words
# both (``reasonCopy`` in ``frontend/src/utils/emulatorMenu.tsx``).
CORE_UNLOCATED = "core_unlocated"
START_SCRIPT_RUNS_NOTHING = "start_script_runs_nothing"

# What an entry launches, as the resolver states it, in Tender's words: a core
# RetroArch loads on this machine, an emulator program of its own, or a
# RetroArch launch naming a core this machine cannot load.
CORE = "core"
PROGRAM = "program"
FOREIGN_CORE = "foreign_core"


@dataclass(frozen=True)
class EmulatorOption:
    """One ES-DE ``<command>`` classified for launch-bakeability.

    ``label`` is ES-DE's display label — the pick key the per-game/per-platform
    override stores. ``kind`` is :data:`CORE`, :data:`PROGRAM` or
    :data:`FOREIGN_CORE`, the resolver's statement of what the entry launches.
    ``core_so`` is the core a :data:`CORE` entry loads, its bare name (no
    ``.so``), and ``core_path`` the file the resolver states ES-DE loads it
    from, spelled where RetroDECK runs it; both are ``None`` on every other
    entry, and ``core_path`` also where the resolver found no core file.
    ``command`` is the raw ES-DE command text, retained so the option can be
    rendered into an ``EmulatorInvocation``; it is not exposed on the frontend
    payload.

    ``status`` is the bake verdict:

    - ``"bakeable"`` — a real emulator invocation ending in ``%ROM%`` Tender
      can bake into a shortcut ``-e`` override (``reason`` is ``None``).
    - ``"needs_setup"`` — an entry that is not launchable from Steam as it
      stands: a ``%INJECT%`` form, which injects a file's contents into the
      command and which Tender cannot start (reason ``"inject"``), or one whose
      program the resolver states is not installed (:data:`NOT_INSTALLED`),
      which stands whatever the command is, ahead of every other reason.
    - ``"unbakeable"`` — cannot be baked; ``reason`` says why.

    ``reason`` is ``None`` when bakeable, else one of ``"inject"``,
    ``"not_installed"``, :data:`START_SCRIPT_RUNS_NOTHING`,
    :data:`CORE_UNLOCATED`, ``"shortcut_script"``, ``"no_rom_target"``,
    ``"quoting"``, ``"startdir"``, ``"unknown_placeholder"``.

    ``not_established`` is the note an entry carries where the resolver could
    not establish whether its program is there — :data:`NOT_ESTABLISHED` or a
    named cause — and ``None`` otherwise. It leaves ``status`` to the command:
    the entry stays a pick that resolves, and is never the default by itself
    (:func:`select_default_option`).

    ``emulator`` is the resolver's identity for the emulator behind this row, and
    it is what a firmware answer is joined and scoped on — the one field that
    names a standalone emulator, which ``core_so`` cannot. It is not derivable
    from ``label``: ES-DE lists one ``pcsx2_libretro.so`` as both ``LRPS2`` and
    ``PCSX2``, so two labels can be one emulator. ``None`` is an emulator the
    resolver could not identify, and nothing may be scoped to it.

    ``launcher`` is the path inside RetroDECK's sandbox the resolver states ES-DE
    runs for this entry — what the folder-boot bake execs directly (ADR-0019).
    ``None`` where the reading hands none on; which launchers it does is
    ``adapters/atlas_catalogue.py::_sandbox_launcher``'s rule.
    """

    label: str
    kind: str
    core_so: str | None
    command: str
    status: str
    reason: str | None
    emulator: str | None
    launcher: str | None = None
    not_established: str | None = None
    core_path: str | None = None


class LaunchingEmulator(Protocol):
    """The one emulator a set of firmware answers is about, carried as ONE value.

    Both projections a surface needs come off it: ``emulator`` is the identity
    every firmware answer is joined and scoped on, ``label`` the name a sentence
    prints. They travel together so that no caller can hand the filter one
    emulator's identity and the sentence beside it another's name — the pane
    that displayed a just-picked PCSX ReARMed while judging the platform by the
    libretro default was two calls agreeing by coincidence until an override was
    set. There is no pair here to get wrong: the pick IS the value, and each
    projection is read off it.

    A structural type rather than a class of its own, because both resolutions
    that answer this question already state both fields — :class:`EmulatorOption`
    for a platform's pick, :class:`~domain.shortcut_data.EmulatorInvocation` for
    a ROM's — and a wrapper would be a third spelling of what they already are.

    **Those two answer two different questions**, and this type does not merge
    them: it carries the pick the CALLER's question is about — a platform's, for
    a question about a platform; a ROM's, for a question about a ROM — never "the
    platform's pick" as such. A parameter typed with it therefore takes its
    caller's question and chooses nothing itself; where the caller has no pick to
    state, ``None`` says so and the callee falls back in its own terms. The
    distinction is the one ``ActiveCoreResolver`` exists to make (a per-game pin
    over the per-platform override over the es_systems default), and a common
    type is exactly where it would go quiet: a ROM's pick handed to a
    platform-scoped answer, or the reverse, is well typed and wrong.

    ``label`` is presentation, and a resolution may carry none. ``emulator`` is
    ``None`` for an emulator the resolver could not identify, and nothing may be
    scoped to it.
    """

    @property
    def emulator(self) -> str | None: ...

    @property
    def label(self) -> str | None: ...


def classify_command(
    label: str,
    text: str,
    *,
    kind: str,
    core_so: str | None = None,
    core_path: str | None = None,
    emulator: str | None = None,
    installation: str | None = None,
    launcher: str | None = None,
    start_script_runs_nothing: bool = False,
) -> EmulatorOption:
    """Classify a single ES-DE ``<command>`` (``label`` + ``text``).

    Applies the bake-verdict rules in order (the first that matches wins),
    returning a fully-populated :class:`EmulatorOption`. Pure — no I/O,
    deterministic in its inputs.

    *kind*, *core_so*, *core_path*, *emulator* and *launcher* are the
    resolver's statements about the row, carried through unexamined: this
    function judges only whether the command can be baked, and what the entry
    launches, which core it loads from where, who the emulator is and what
    ES-DE runs for it are read off the catalogue entry beside it rather than
    parsed out of the command text.

    *installation* is what the resolver states where it does not state the
    entry startable, ``None`` where it does. :data:`NOT_INSTALLED` takes the
    place of the bake verdict, so the entry is neither the default nor a pick
    that resolves, and the picker shows it disabled. Any other word is the
    entry's ``not_established`` note, beside the bake verdict its command earns.
    After it, *start_script_runs_nothing* refuses the bake
    (:data:`START_SCRIPT_RUNS_NOTHING`), and so does a :data:`CORE` entry
    without a *core_path* (:data:`CORE_UNLOCATED`); every other entry is judged
    by its command, a core entry's with its core path written in.
    """
    if installation == NOT_INSTALLED:
        status, reason = "needs_setup", NOT_INSTALLED
    elif start_script_runs_nothing:
        status, reason = "unbakeable", START_SCRIPT_RUNS_NOTHING
    elif kind == CORE and core_path is None:
        status, reason = "unbakeable", CORE_UNLOCATED
    else:
        status, reason = _bake_verdict(_with_core_path(text, kind, core_so, core_path))
    return EmulatorOption(
        label=label,
        kind=kind,
        core_so=core_so,
        command=text.strip(),
        status=status,
        reason=reason,
        emulator=emulator,
        launcher=launcher,
        not_established=installation if installation not in (None, NOT_INSTALLED) else None,
        core_path=core_path,
    )


def _with_core_path(text: str, kind: str, core_so: str | None, core_path: str | None) -> str:
    """*text* with a :data:`CORE` entry's ``%CORE_RETROARCH%/<core>.so`` written as *core_path*, else unchanged.

    The path is the resolver's, so the bake loads the file ES-DE's own lookup
    finds rather than one Tender places itself.
    """
    if kind != CORE or core_so is None or core_path is None:
        return text.strip()
    return text.strip().replace(f"%CORE_RETROARCH%/{core_so}.so", core_path)


def _bake_verdict(text: str) -> tuple[str, str | None]:
    """Return ``(status, reason)`` for a command, first matching rule wins.

    Order matters: an ``%INJECT%`` command is *needs-setup* even though it also
    lacks a ``%ROM%`` target, and a ``.desktop``/OS-shell form is flagged as a
    script before the ``%ROM%``-ending check. ``%STARTDIR%`` is deliberately
    checked *after* the unknown-placeholder sweep (it is a known placeholder)
    so it surfaces its own ``startdir`` reason rather than being lumped in.
    """
    t = text.strip()
    if "%INJECT%" in t:
        return ("needs_setup", "inject")
    if "%ENABLESHORTCUTS%" in t or "%EMULATOR_OS-SHELL%" in t:
        return ("unbakeable", "shortcut_script")
    if not t.endswith("%ROM%"):
        return ("unbakeable", "no_rom_target")
    if '"' in t or "\\;" in t:
        return ("unbakeable", "quoting")
    for token in _PLACEHOLDER_RE.findall(t):
        if token not in _KNOWN_PLACEHOLDERS and not token.startswith("%EMULATOR_"):
            return ("unbakeable", "unknown_placeholder")
    if "%STARTDIR%" in t:
        return ("unbakeable", "startdir")
    return ("bakeable", None)


def select_default_option(options: list[EmulatorOption]) -> EmulatorOption | None:
    """Return the first *bakeable* option in document order, or ``None``.

    ES-DE lists a system's emulators in preference order; the first one
    Tender can bake is the system-layer default. Skips ``needs_setup`` and
    ``unbakeable`` options (the ``%INJECT%`` / shortcut / quoting forms), and an
    option whose installation the resolver could not establish: it is a pick a
    user may make, never one Tender makes for them. When nothing qualifies the
    caller bakes the plain RetroDECK launch and lets RetroDECK resolve the
    emulator itself.
    """
    for option in options:
        if option.status == "bakeable" and option.not_established is None:
            return option
    return None


def option_to_invocation(option: EmulatorOption | None) -> EmulatorInvocation | None:
    """Render a *bakeable* option into an :class:`EmulatorInvocation`, else ``None``.

    Every bakeable option is baked as its command, a core entry's with its core
    path written in, through ``run_game.sh -e``: a core entry whose command
    passes RetroArch more than its core — FinalBurn Neo's ``--subsystem neocd``
    on Neo Geo CD — keeps all of it. The invocation carries the option's
    ``core_so``, and an emulator program's ``launcher`` for the folder-boot
    fold, which execs a program and never RetroArch with a core. A ``None``
    option, or one that is not bakeable, resolves to ``None`` (the plain-launch
    signal).
    """
    if option is None or option.status != "bakeable" or not option.command:
        return None
    return EmulatorInvocation.run_game(
        _with_core_path(option.command, option.kind, option.core_so, option.core_path),
        option.label,
        option.emulator,
        core_so=option.core_so,
        launcher=option.launcher if option.kind == PROGRAM else None,
    )


def _pinned_option(options: list[EmulatorOption], label: str) -> EmulatorOption | None:
    """The option a pinned *label* still names, or ``None`` where it no longer does.

    The first option carrying *label*, and only while that option can be baked —
    a label no option carries and one whose option is not bakeable both answer
    ``None``, and a caller treats both as "this pin no longer resolves". The
    search stops at the first label match rather than looking past it for a
    bakeable namesake: two
    commands sharing a display label are one emulator to the user, and picking the
    second because the first is un-bakeable would resolve a pin to something the
    picker never offered under that name.
    """
    for option in options:
        if option.label == label:
            return option if option_to_invocation(option) is not None else None
    return None


def label_to_invocation(options: list[EmulatorOption], label: str) -> EmulatorInvocation | None:
    """Resolve a picked *label* to its :class:`EmulatorInvocation`, else ``None``.

    Finds the option carrying *label* and renders it via
    :func:`option_to_invocation`. Returns ``None`` when no option matches the
    label OR the matched option is not bakeable — the caller treats both as
    "this pin no longer resolves" and degrades to the next layer.
    """
    return option_to_invocation(_pinned_option(options, label))


def resolve_platform_option(options: list[EmulatorOption], override: str | None) -> EmulatorOption | None:
    """Return the emulator a platform resolves to, or ``None``.

    The platform-level projection of the read-path precedence
    (``ActiveCoreResolver`` without the per-game layer): the per-platform
    override (``settings.json`` ``platform_cores``) when its label is set and
    still names a bakeable emulator, else the es_systems default (the first
    bakeable command). A stale / no-longer-installed override degrades to the
    default — never fatal — mirroring the launch-bake resolver so what a surface
    shows and what actually launches agree. A pinned option whose installation
    the resolver could not establish still resolves. ``None`` when nothing is
    pinned and the platform has no option :func:`select_default_option` takes —
    which is not the same as having none, and not a failure: an empty menu, an
    unreadable ``es_systems.xml``, and a menu whose only entries are
    ``needs_setup`` (an ``%INJECT%`` form, or an emulator the resolver states is
    not installed) or not established all answer ``None`` alike. What follows
    is written at :func:`select_default_option`: the caller bakes the plain
    RetroDECK launch and RetroDECK resolves the emulator itself, so the games
    still start.

    **One pick, because a surface must not be able to answer for two
    emulators.** The option is returned rather than one projection of it so that
    the name a surface displays and the identity it scopes on
    (``EmulatorOption.emulator``, the one field that names a standalone emulator,
    where ``core_so`` is ``None`` for every standalone pick) come off the same
    choice. Resolving them separately is what let a pane name PCSX ReARMed and
    judge the platform by the system default beside it. The BIOS answers take
    this pick through :func:`resolve_firmware_option`, which goes one rung
    further where it is ``None``.
    """
    pinned = _pinned_option(options, override) if override is not None else None
    return pinned if pinned is not None else select_default_option(options)


def resolve_firmware_option(options: list[EmulatorOption], override: str | None) -> EmulatorOption | None:
    """Return the emulator a platform's BIOS answer is about, or ``None``.

    The pick (:func:`resolve_platform_option`) where there is one. Where nothing
    is bakeable the platform's games launch through RetroDECK's plain launch,
    which starts the emulator RetroDECK declares first — installed or not —
    unless an alternative emulator is set in ES-DE; that first entry is the one
    the answer is about, since an emulator that is not installed still declares
    the firmware it would need. ``None`` only where no emulator is
    listed at all.
    """
    return resolve_platform_option(options, override) or (options[0] if options else None)


def resolve_platform_label(options: list[EmulatorOption], override: str | None) -> str | None:
    """Return the platform-layer active-emulator display label, or ``None``.

    The name half of :func:`resolve_platform_option`. ``None`` where the platform
    resolves to no bakeable emulator at all — a surface states THAT; it is never
    a name, and least of all "Default", which says Tender picked one.
    """
    option = resolve_platform_option(options, override)
    return option.label if option is not None else None


def options_to_payload(options: list[EmulatorOption]) -> list[dict[str, Any]]:
    """Project options into the frontend emulator-picker payload.

    One entry per launch command (:func:`same_command_groups`): RetroDECK lists
    ``pcsx2_libretro.so`` as LRPS2 and as PCSX2 under one command, and two
    labels that start the same thing are one choice. Each entry is ``{label,
    labels, name, kind, core_so, emulator, is_default, bakeable, reason}``.
    ``labels`` are every label of the group in declared order and ``name`` is
    them joined " / ", what a surface prints; ``label`` is the first of them,
    the one a pick stores, and an override stored under any of ``labels`` reads
    as this entry. The other fields are the group's first option's, which its
    members share because they share the command. ``is_default`` marks the group
    holding the single option :func:`select_default_option` picks (the first
    bakeable one); ``bakeable`` is ``True`` only for a fully bakeable option
    (``needs_setup`` reads as ``bakeable: False`` with its ``reason`` —
    ``"inject"`` or ``"not_installed"`` — so the picker can disable it with a
    distinct message). A bakeable option's ``reason`` carries its
    ``not_established`` note, where it has one, so a surface can say so beside
    a pick it still offers; an option that cannot be baked keeps the reason it
    cannot. The raw ``command`` text is intentionally dropped from the wire
    payload.

    ``kind`` is Tender's word for what the entry launches (:data:`CORE`,
    :data:`PROGRAM`, :data:`FOREIGN_CORE`). ``emulator`` is the identity, and it
    travels beside ``core_so`` rather than replacing it because the two answer
    different questions: ``core_so`` is the libretro core a surface may still
    need to name, the identity is what a firmware row's per-emulator entries
    are keyed on. Without it on the wire a surface holding both answers has
    nothing to join them by for a standalone emulator. It is not what entries
    are grouped by: one emulator can be launched several ways (RPCS3 by game
    serial, or by its folder or ISO), and each way is its own choice.
    """
    default = select_default_option(options)
    payload: list[dict[str, Any]] = []
    for group in same_command_groups(options):
        first = group[0]
        labels = list(dict.fromkeys(option.label for option in group))
        payload.append(
            {
                "label": first.label,
                "labels": labels,
                "name": " / ".join(labels),
                "kind": first.kind,
                "core_so": first.core_so,
                "emulator": first.emulator,
                "is_default": any(option is default for option in group),
                "bakeable": first.status == "bakeable",
                "reason": first.reason if first.reason is not None else first.not_established,
            }
        )
    return payload


def same_command_groups(options: list[EmulatorOption]) -> list[list[EmulatorOption]]:
    """*options* grouped by identical command text, each group where its first member stands.

    The command is compared as :func:`classify_command` keeps it, stripped and
    otherwise verbatim, so two labels share a group only where they start
    exactly the same thing. An option with no command text gets a group of its own.
    """
    groups: dict[str, list[EmulatorOption]] = {}
    ordered: list[list[EmulatorOption]] = []
    for option in options:
        group = groups.get(option.command) if option.command else None
        if group is None:
            group = []
            ordered.append(group)
            if option.command:
                groups[option.command] = group
        group.append(option)
    return ordered
