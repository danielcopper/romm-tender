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

from domain.emulator_commands import (
    CANNOT_START,
    FOLDER_ENTRY_LABEL,
    FOLDER_INSTALL,
    default_option,
    option_for_label,
    shipped_default,
)

if TYPE_CHECKING:
    from domain.emulator_commands import EmulatorOption
    from domain.emulator_pick import StoredPick
    from domain.shortcut_data import EmulatorInvocation

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
    """One ROM's emulator: the choice, the options it was made among, and what it bakes to.

    ``invocation`` is ``None`` for the plain launch. ``folder_unstartable``
    says the ROM is installed as a folder that ``invocation`` does not start
    directly, so nothing it bakes can start it (#2195 D21).
    """

    choice: EmulatorChoice
    options: list[EmulatorOption]
    invocation: EmulatorInvocation | None
    folder_unstartable: bool


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
    choice: EmulatorChoice, options: list[EmulatorOption], *, folder_unstartable: bool = False
) -> dict[str, Any]:
    """What starting with *choice* comes to, as ``{state, reason, entry, folder_entry}``.

    ``entry`` is the label of the entry it is about — the chosen one, or for
    :data:`CHOOSE` the shipped default — ``None`` for :data:`NO_ENTRY`.
    ``reason`` is why it cannot start (:data:`domain.emulator_commands.NOT_INSTALLED`,
    :data:`~domain.emulator_commands.START_SCRIPT_RUNS_NOTHING`,
    :data:`~domain.emulator_commands.FOLDER_INSTALL`), or, for :data:`CHOOSE`,
    the shipped entry's not-established note; ``None`` otherwise.
    *folder_unstartable* says the game is installed as a folder its entry
    cannot start (#2195 D21); ``folder_entry`` then names the entry that can.
    """
    option = choice.option
    if option is None:
        shipped = shipped_default(options)
        if shipped is None:
            return {"state": NO_ENTRY, "reason": None, "entry": None, "folder_entry": None}
        return {"state": CHOOSE, "reason": shipped.not_established, "entry": shipped.label, "folder_entry": None}
    if option.reason in CANNOT_START:
        return {"state": CANNOT_START_STATE, "reason": option.reason, "entry": option.label, "folder_entry": None}
    if folder_unstartable:
        return {
            "state": CANNOT_START_STATE,
            "reason": FOLDER_INSTALL,
            "entry": option.label,
            "folder_entry": FOLDER_ENTRY_LABEL,
        }
    return {"state": STARTS, "reason": None, "entry": option.label, "folder_entry": None}


def unspoken_start(entry: str | None) -> dict[str, Any]:
    """The start of a default that comes from a source games do not start through: nothing is said of it.

    Until such a source starts games, whether its entry could start them is not
    a question a page asks (#2195 D24).
    """
    return {"state": STARTS, "reason": None, "entry": entry, "folder_entry": None}


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
