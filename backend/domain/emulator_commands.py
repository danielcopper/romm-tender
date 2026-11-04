"""The entries of one system's emulator list, as Tender offers them for a pick.

Each ES-DE ``<command>`` the resolver lists is one :class:`EmulatorOption`,
carrying what the resolver states about it in Tender's words: what it launches,
the core it loads, the emulator behind it and whether its program is there.
Whether an entry starts a game is not decided here: the command a game starts
with is the resolver's, asked at the start (:mod:`adapters.atlas_launch`,
#2221 D7), and an entry it gives no complete command for is not startable
through Tender yet (D2).

Which entry is the default is no judgement of Tender's: it is the entry the
source ships first, the lowest ``declared_index`` the resolver states, whatever
that entry's state (:func:`shipped_default`). A choice the user made inside
ES-DE never becomes it.

Pure compute only — takes what :mod:`adapters.atlas_catalogue` reads off the
resolver's catalogue answer and returns value objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

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

# What an entry launches, as the resolver states it, in Tender's words: a core
# RetroArch loads on this machine, an emulator program of its own, or a
# RetroArch launch naming a core this machine cannot load.
CORE = "core"
PROGRAM = "program"
FOREIGN_CORE = "foreign_core"


@dataclass(frozen=True)
class EmulatorOption:
    """One ES-DE ``<command>`` of a system, as the resolver states it.

    ``label`` is ES-DE's display label — the pick key the per-game/per-platform
    override stores. ``kind`` is :data:`CORE`, :data:`PROGRAM` or
    :data:`FOREIGN_CORE`, the resolver's statement of what the entry launches.
    ``core_so`` is the core a :data:`CORE` entry loads, its bare name (no
    ``.so``), ``None`` on every other entry.

    ``not_installed`` is whether the resolver states the entry's program is not
    there; such an entry is not offered as a pick, and as the default or a
    stored pick it cannot start. ``not_established`` is the note an entry
    carries where the resolver could not establish whether its program is
    there — :data:`NOT_ESTABLISHED` or a named cause — and ``None`` otherwise;
    such an entry stays a pick and is never the default by itself
    (:func:`default_option`, #2195 D9).

    ``emulator`` is the resolver's identity for the emulator behind this row, and
    it is what a firmware answer is joined and scoped on — the one field that
    names a standalone emulator, which ``core_so`` cannot. It is not derivable
    from ``label``: ES-DE lists one ``pcsx2_libretro.so`` as both ``LRPS2`` and
    ``PCSX2``, so two labels can be one emulator. ``None`` is an emulator the
    resolver could not identify, and nothing may be scoped to it.

    ``declared_index`` is the entry's place in the order the source ships, the
    resolver's own value, ``None`` for an entry no layer declared. With
    ``emulator`` it is the join key of a row: two rows of one system can run
    one emulator. ``chosen_in_frontend`` marks the entry the user chose inside
    ES-DE, where the resolver states such a choice put it first.
    """

    label: str
    kind: str
    core_so: str | None
    emulator: str | None
    not_installed: bool = False
    not_established: str | None = None
    declared_index: int | None = None
    chosen_in_frontend: bool = False


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
    for a platform's pick and for a ROM's — and a wrapper would be a second
    spelling of what it already is.

    **Those two answer two different questions**, and this type does not merge
    them: it carries the pick the CALLER's question is about — a platform's, for
    a question about a platform; a ROM's, for a question about a ROM — never "the
    platform's pick" as such. A parameter typed with it therefore takes its
    caller's question and chooses nothing itself; where the caller has no pick to
    state, ``None`` says so and the callee falls back in its own terms. The
    distinction is the one ``ActiveCoreResolver`` exists to make (a per-game pin
    over the per-platform override over the default), and a common
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


def emulator_option(
    label: str,
    *,
    kind: str,
    core_so: str | None = None,
    emulator: str | None = None,
    installation: str | None = None,
    declared_index: int | None = None,
    chosen_in_frontend: bool = False,
) -> EmulatorOption:
    """One catalogue entry as an :class:`EmulatorOption`, from what the resolver states about it.

    Every argument is the resolver's statement about the row, carried through
    unexamined. *installation* is what the resolver states where it does not
    state the entry startable, ``None`` where it does: :data:`NOT_INSTALLED`
    marks the entry ``not_installed``, any other word is its
    ``not_established`` note.
    """
    return EmulatorOption(
        label=label,
        kind=kind,
        core_so=core_so,
        emulator=emulator,
        not_installed=installation == NOT_INSTALLED,
        not_established=installation if installation not in (None, NOT_INSTALLED) else None,
        declared_index=declared_index,
        chosen_in_frontend=chosen_in_frontend,
    )


def shipped_default(options: list[EmulatorOption]) -> EmulatorOption | None:
    """The entry the source ships as the system's default, or ``None`` where no entry is declared.

    The lowest ``declared_index``, read off the resolver's statement rather than
    off the list's order: the list is in the order the resolver gives, which
    puts the user's choice in ES-DE first. The numbering may start above 0 and
    skip a value, so it is the lowest that counts, not the 0. An entry with no
    declared position never is the default.
    """
    declared = [option for option in options if option.declared_index is not None]
    return min(declared, key=lambda option: option.declared_index or 0, default=None)


def default_option(options: list[EmulatorOption]) -> EmulatorOption | None:
    """The default Tender takes: the shipped one, unless its installation is not established.

    An entry whose installation the resolver could not establish never becomes
    the default by itself (#2195 D9): the game then has no default, and the user
    picks one. Every other state of the shipped entry stands, one that cannot
    start included — nothing later in the list is taken instead.
    """
    shipped = shipped_default(options)
    return shipped if shipped is not None and shipped.not_established is None else None


def option_for_label(options: list[EmulatorOption], label: str) -> EmulatorOption | None:
    """The option *label* names, or ``None`` where no option carries it.

    A label is unique within one system's catalogue, which is what ES-DE stores
    a choice by; whether the option it names can start is not asked here — a
    pick stands whatever its state (#2195 D23).
    """
    return next((option for option in options if option.label == label), None)


def resolve_firmware_option(options: list[EmulatorOption], override: str | None) -> EmulatorOption | None:
    """Return the emulator a platform's BIOS answer is about, or ``None``.

    The per-platform pick while its label still names an option, else the
    shipped default whatever its state: an emulator whose installation is not
    established, or that is not installed, still declares the firmware it would
    need. ``None`` only where no entry is declared.
    """
    pinned = option_for_label(options, override) if override is not None else None
    return pinned if pinned is not None else shipped_default(options)


def options_to_payload(options: list[EmulatorOption]) -> list[dict[str, Any]]:
    """Project options into the frontend emulator-picker payload, one row per option.

    The rows keep the order the resolver gives and are never merged: two labels
    that run one command are two entries in ES-DE, which stores a choice by its
    label (#2195 D8). Each row is ``{label, kind, core_so, emulator,
    declared_index, is_default, chosen_in_frontend, reason}``. ``is_default``
    marks the option :func:`default_option` takes; ``chosen_in_frontend`` the
    one the user chose inside ES-DE. ``reason`` is the option's
    ``not_established`` note where it has one, so a surface says so beside a
    pick it offers (#2195 D9), :data:`NOT_INSTALLED` for an entry that is not
    offered, ``None`` otherwise.

    ``kind`` is Tender's word for what the entry launches (:data:`CORE`,
    :data:`PROGRAM`, :data:`FOREIGN_CORE`). ``emulator`` is the identity, and it
    travels beside ``core_so`` rather than replacing it because the two answer
    different questions: ``core_so`` is the libretro core a surface may still
    need to name, the identity is what a firmware row's per-emulator entries
    are keyed on. With ``declared_index`` it is the row's join key.
    """
    default = default_option(options)
    return [
        {
            "label": option.label,
            "kind": option.kind,
            "core_so": option.core_so,
            "emulator": option.emulator,
            "declared_index": option.declared_index,
            "is_default": option is default,
            "chosen_in_frontend": option.chosen_in_frontend,
            "reason": _reason(option),
        }
        for option in options
    ]


def _reason(option: EmulatorOption) -> str | None:
    """What a picker row says about *option*'s program: its note, that it is not installed, or nothing."""
    if option.not_established is not None:
        return option.not_established
    return NOT_INSTALLED if option.not_installed else None
