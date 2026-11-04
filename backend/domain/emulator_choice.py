"""Which entry a game or a platform starts with, and what stands in its way.

The precedence is Tender's own: the per-game pick over the per-platform pick
over the default (:func:`domain.emulator_commands.default_option`). A stored
pick stands while its label still names an entry, whatever that entry's state;
only a label that no longer exists gives way to the next layer, and the answer
names it so a surface can say so rather than switch silently (#2195 D5, D23).

Pure compute — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.emulator_commands import NOT_INSTALLED, default_option, option_for_label, shipped_default
from domain.start_command import StartRefused

if TYPE_CHECKING:
    from domain.emulator_commands import EmulatorOption
    from domain.emulator_pick import StoredPick
    from domain.start_command import StartAnswer

# The layer an entry was taken from.
GAME_PICK = "game"
PLATFORM_PICK = "platform"
DEFAULT = "default"

# What a game or a platform starting with its entry comes to, as a surface reads
# it: ``STARTS`` — nothing stands in its way; ``CANNOT_START_STATE`` — it cannot
# start; ``CHOOSE`` — no default, the shipped entry's installation is not
# established; ``NO_ENTRY`` — no entry.
STARTS = "starts"
CANNOT_START_STATE = "cannot_start"
CHOOSE = "choose"
NO_ENTRY = "no_entry"


@dataclass(frozen=True, slots=True)
class VanishedPick:
    """A stored pick whose label no longer names an entry, and the layer it was stored on."""

    pick: StoredPick
    layer: str


@dataclass(frozen=True, slots=True)
class EmulatorChoice:
    """The entry one scope starts with, the layer it came from, and the pick it replaced.

    ``option`` is ``None`` where nothing is picked and there is no default.
    ``vanished`` is the topmost stored pick passed over because its label no
    longer exists, ``None`` where none was.
    """

    option: EmulatorOption | None
    layer: str | None
    vanished: VanishedPick | None


@dataclass(frozen=True, slots=True)
class ResolvedEmulator:
    """One ROM's emulator: the choice, the options it was made among, and the source that answered them.

    ``source`` is the emulator-options answer's ``{kind, starts_games}``,
    ``None`` where no source answered.
    """

    choice: EmulatorChoice
    options: list[EmulatorOption]
    source: dict[str, Any] | None = None


def choose_emulator(
    options: list[EmulatorOption], *, game: StoredPick | None, platform: StoredPick | None
) -> EmulatorChoice:
    """The entry a scope starts with: *game*'s pick, else *platform*'s, else the default.

    A platform's own question passes ``game=None``.
    """
    vanished: VanishedPick | None = None
    for pick, layer in ((game, GAME_PICK), (platform, PLATFORM_PICK)):
        if pick is None:
            continue
        option = option_for_label(options, pick.label)
        if option is not None:
            return EmulatorChoice(option=option, layer=layer, vanished=vanished)
        if vanished is None:
            vanished = VanishedPick(pick=pick, layer=layer)
    default = default_option(options)
    return EmulatorChoice(option=default, layer=DEFAULT if default is not None else None, vanished=vanished)


def start_payload(
    choice: EmulatorChoice, options: list[EmulatorOption], command: StartAnswer | None = None
) -> dict[str, Any]:
    """What starting with *choice* comes to, as ``{state, reason, entry}`` and, beside a start, ``remarks``.

    ``entry`` is the label of the entry it is about — the chosen one, or for
    :data:`CHOOSE` the shipped default — ``None`` for :data:`NO_ENTRY`.
    ``reason`` is why it cannot start: :data:`domain.emulator_commands.NOT_INSTALLED`,
    which the resolver states about the entry itself, or, where *command* is the
    resolver's launch command for the game and gives no complete command, its
    reason (:class:`domain.start_command.StartRefused`, #2221 D2); for
    :data:`CHOOSE` the shipped entry's not-established note; ``None`` otherwise.
    ``remarks`` are what is said beside a start the command lets go ahead
    (:class:`domain.start_command.StartNote`), present only where there is any. A
    *command* refused because the answering source starts no game through
    Tender yet (:func:`domain.start_command.source_refusal`) is said ahead of
    anything about the entry, and carries that source's kind as ``source``
    (#2221 D21). Without a *command* nothing is said of the command — a
    platform's page asks none (#2221 D20). Whether the game's file starts with the entry is the launch
    verdict's (:mod:`domain.launchability`), a folder the entry does not take
    included (#2195 D21).
    """
    option = choice.option
    if option is None:
        shipped = shipped_default(options)
        if shipped is None:
            return _start(NO_ENTRY, None, None)
        return _start(CHOOSE, shipped.not_established, shipped.label)
    if isinstance(command, StartRefused) and command.source is not None:
        return {**_start(CANNOT_START_STATE, command.reason, option.label), "source": command.source}
    if option.not_installed:
        return _start(CANNOT_START_STATE, NOT_INSTALLED, option.label)
    if isinstance(command, StartRefused):
        return _start(CANNOT_START_STATE, command.reason, option.label)
    remarks = [note.payload() for note in command.notes] if command is not None else []
    return _start(STARTS, None, option.label, remarks)


def _start(
    state: str, reason: str | None, entry: str | None, remarks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The payload; ``remarks`` only where there is something to say beside the start."""
    payload: dict[str, Any] = {"state": state, "reason": reason, "entry": entry}
    if remarks:
        payload["remarks"] = remarks
    return payload


def unspoken_start(entry: str | None) -> dict[str, Any]:
    """The start of a default that comes from a source games do not start through: nothing is said of it.

    Until such a source starts games, whether its entry could start them is not
    a question a page asks (#2195 D24).
    """
    return _start(STARTS, None, entry)


def vanished_payload(choice: EmulatorChoice) -> dict[str, Any] | None:
    """The pick a surface says no longer exists, as ``{label, source, layer, used, used_layer}``, or ``None``.

    ``source`` is the kind of the source the pick was chosen from (#2195 D20).
    ``used`` is the label of the entry used instead and ``used_layer`` the
    layer it came from, both ``None`` where there is none.
    """
    vanished = choice.vanished
    if vanished is None:
        return None
    return {
        "label": vanished.pick.label,
        "source": vanished.pick.source,
        "layer": vanished.layer,
        "used": choice.option.label if choice.option is not None else None,
        "used_layer": choice.layer if choice.option is not None else None,
    }
