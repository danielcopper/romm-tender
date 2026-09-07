"""What a game's save consists of, where it lives, and whether this plugin may sync it.

The vocabulary the resolver's savefile answer is translated into, and the rule
that turns one such answer into exactly one of five states. Adapters read the
machine; this module decides what the reading MEANS, so the decision is pure and
testable without a machine, and so ``services/`` never sees a resolver type.

The five states are the whole point. Save sync copies a per-game file to a
per-ROM record on the server, and that model is simply wrong for most of what
emulators actually write — a card many games share, a directory whose contents
nobody enumerated, a path or a name half of which is the game's own identity.
Only
:data:`SAVE_STATE_PER_GAME_FILES` is a save this plugin can carry; the other four
are refusals, and each says something different about why. Every one of them is
an honest "we are not touching this", never "there is nothing here".

**Scope is the emulator, never the platform.** Two emulators for one system
answer differently — the same PS2 game is a shared memory card under standalone
PCSX2 and could be per-game under a libretro core — so an answer names the
emulator it is about and means nothing without it.

The value vocabularies below (roles, granularities, file-set states, caveat
codes) are the resolver's own, restated here as plain strings because ``domain/``
may not import ``_vendor``. ``tests/adapters/test_atlas_saves.py`` asserts each
one against the resolver's exported constant, so an upstream rename fails there
rather than silently classifying everything as a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from domain.save_layout import SAVE_SYNC_CONTENT_DIR_REASON

if TYPE_CHECKING:
    from collections.abc import Sequence

# The five states. Exactly one holds for any ROM.
SAVE_STATE_PER_GAME_FILES = "per_game_files"
SAVE_STATE_SHARED = "shared"
SAVE_STATE_INSIDE_CONTENT = "inside_content"
SAVE_STATE_HOLE = "hole"
SAVE_STATE_UNESTABLISHED = "unestablished"

SaveState = Literal[
    "per_game_files",
    "shared",
    "inside_content",
    "hole",
    "unestablished",
]

# The three shapes inside ``unestablished``. They are kept apart because they
# are three different sentences to a user, and the fifth state is where every
# doubt lands, so collapsing them would make one message stand for all of them.
#
# ``not_asked`` is the one that is easy to lose: the question was never put, so
# the emulator is not implicated at all. RetroArch writing saves to the content
# directory reaches it, and so does a ROM with no resolvable emulator. Without
# it those payloads are byte-identical to an unaudited core's, and a page would
# tell a user their emulator is a mystery when the truth is that the plugin
# never asked.
UNESTABLISHED_NOTHING = "nothing_established"
UNESTABLISHED_DIRECTORY_KNOWN = "directory_known"
UNESTABLISHED_NOT_ASKED = "not_asked"

UnestablishedShape = Literal["nothing_established", "directory_known", "not_asked"]

# Roles that are the emulator's configuration rather than the player's progress.
# A configuration file is never synced: it is machine-local by nature, and
# carrying one between devices overwrites settings the user chose there.
CONFIGURATION_ROLES = frozenset({"settings", "notes"})

# Granularities where one file holds many games' progress.
_SHARED_GRANULARITIES = frozenset({"shared-card", "shared-file"})

# The resolver states this when it refuses to guess what the files are called.
_FILE_SET_UNKNOWN = "unknown"

# Caveats that place the save inside the game's own file, so there is no
# separate file to carry.
_INSIDE_CONTENT_CAVEATS = frozenset({"save-inside-content", "save-inside-image"})

# The caveat for a directory that is known while the names inside it are not.
_FILE_NAMES_UNESTABLISHED = "file-names-unestablished"

# Canonical ``reason`` slug for the benign-skip outcome the four refusing states
# produce. Lives here beside the states that cause it, for the same reason
# ``SAVE_SYNC_CONTENT_DIR_REASON`` lives beside the layout that causes that one:
# every service routes on the SAME value without a service-to-service import.
# It says the SHAPE of this game's save is not one the plugin can carry per
# game, which is a statement about the emulator and never about the server.
SAVE_SHAPE_UNSUPPORTED_REASON = "save_shape_unsupported"

# Every ``reason`` slug that means "the sync did not run, and that is fine".
# Both are statements about the machine, not failures, so no surface may report
# one as an error: the launch path proceeds silently and the post-exit path
# raises no toast. A reason outside this set IS a failure. Kept here, beside the
# state that produces the second one, so every consumer routes on one list
# rather than growing its own equality test — which is exactly how the
# post-exit toast came to fire for half the mapped systems.
BENIGN_SYNC_SKIP_REASONS: frozenset[str] = frozenset(
    {
        SAVE_SYNC_CONTENT_DIR_REASON,
        SAVE_SHAPE_UNSUPPORTED_REASON,
    }
)


@dataclass(frozen=True, slots=True)
class SaveGroup:
    """One directory's worth of a save, as the resolver decomposed it.

    ``files`` is ``None`` when the resolver states that save data lives in
    ``directory`` and refuses to name it — never "the directory is empty".
    """

    directory: str
    files: tuple[str, ...] | None
    role: str | None
    granularity: str | None


@dataclass(frozen=True, slots=True)
class SaveComponent:
    """One named file a save consists of, and what that file is.

    ``role`` is the resolver's word for what the file holds (``battery``,
    ``memory-card``, ``settings``, …) or ``None`` where no group claimed it.
    """

    name: str
    directory: str
    role: str | None
    granularity: str | None

    @property
    def is_progress(self) -> bool:
        """Whether this file is the player's progress rather than configuration."""
        return self.role not in CONFIGURATION_ROLES


@dataclass(frozen=True, slots=True)
class SaveAnswer:
    """Where one ROM's save lives, what it consists of, and what may be done with it.

    ``state`` is the verdict and the only thing a sync path may act on;
    ``unestablished`` discriminates the two shapes of
    :data:`SAVE_STATE_UNESTABLISHED` and is ``None`` for every other state.
    ``emulator`` names whose answer this is. ``directory`` is the directory the
    emulator opens — the one to read and write through — while
    ``backing_directory`` is where a link resolves to and answers identity
    questions only. ``components`` are the concrete files, configuration
    included, so a caller can show a file it will not sync. ``needs`` names the
    holes a name could not be completed from, ``granularity`` is how this
    emulator groups save data, and ``caveats`` carries the resolver's stable
    codes verbatim for the log and for a later rendering.
    """

    state: SaveState
    unestablished: UnestablishedShape | None
    emulator: str | None
    directory: str | None
    backing_directory: str | None
    granularity: str | None
    needs: tuple[str, ...]
    components: tuple[SaveComponent, ...]
    caveats: tuple[str, ...]

    @property
    def syncable(self) -> bool:
        """Whether save sync may run at all for this ROM."""
        return self.state == SAVE_STATE_PER_GAME_FILES

    @property
    def owned_files(self) -> tuple[SaveComponent, ...]:
        """Every file this ROM keeps under its own name — configuration included.

        What a move of the save directory has to carry: leaving an emulator's
        per-game configuration file behind is as broken as leaving the battery
        file behind. Empty for every refusing state, where nothing was
        established that a move could act on.
        """
        return self.components if self.syncable else ()

    @property
    def synced_files(self) -> tuple[SaveComponent, ...]:
        """The components a sync may carry — progress only, and only when syncable.

        Narrower than :attr:`owned_files` by exactly the configuration files:
        those are machine-local by nature, so carrying one to another device
        overwrites settings the user chose there. Empty for every refusing
        state, which is what keeps a refusal from probing — there is nothing to
        look for.
        """
        return tuple(component for component in self.owned_files if component.is_progress)

    @property
    def synced_names(self) -> tuple[str, ...]:
        """The basenames of :attr:`synced_files`, for callers that only name files."""
        return tuple(component.name for component in self.synced_files)


# One sentence per refusing state, for the skip result's ``message``. Neutral by
# design: none of these is an error, and three of the four are permanent facts
# about the emulator rather than anything the user did.
_STATE_MESSAGES: dict[str, str] = {
    SAVE_STATE_SHARED: "Save sync is unavailable: this emulator keeps one save card that all games share.",
    SAVE_STATE_INSIDE_CONTENT: "Save sync is unavailable: this emulator writes saves inside the game file itself.",
    SAVE_STATE_HOLE: (
        "Save sync is unavailable: this emulator files saves under an identity of the game "
        "that this plugin cannot read."
    ),
    SAVE_STATE_UNESTABLISHED: "Save sync is unavailable: what this emulator writes could not be established.",
}


def save_shape_message(answer: SaveAnswer) -> str:
    """The sentence a refusal is reported with, named after the emulator it is about."""
    sentence = _STATE_MESSAGES.get(answer.state, _STATE_MESSAGES[SAVE_STATE_UNESTABLISHED])
    return f"{sentence} ({answer.emulator})" if answer.emulator else sentence


def pick_download_name(known_names: Sequence[str], computed: str) -> str:
    """Where a server save lands: the name the answer holds, else the computed one.

    *computed* is the path math's ``<rom stem>.<server file_extension>``, which
    is right only where the emulator's own name has exactly that shape. It is
    not always: Opera writes ``<stem>.0.srm``, Caprice32 ``<stem>.dsk.sav``,
    DOSBox-Pure ``<stem>.pure.zip`` — and a server row carrying extension
    ``srm`` would otherwise be downloaded to ``<stem>.srm``, a file the core
    never opens.

    So the server's extension is read as a POINTER into what the emulator
    writes: the one answer name that starts with the computed stem and ends
    with the computed extension wins. Where nothing matches, or where more than
    one does and picking would be a guess, the computed name stands — this
    corrects a name the resolver can improve on and invents none.
    """
    if computed in known_names:
        return computed
    stem, _, extension = computed.rpartition(".")
    if not stem or not extension:
        return computed
    matches = [name for name in known_names if name.startswith(f"{stem}.") and name.endswith(f".{extension}")]
    return matches[0] if len(matches) == 1 else computed


def unestablished_answer(
    *,
    emulator: str | None = None,
    caveats: tuple[str, ...] = (),
    shape: UnestablishedShape = UNESTABLISHED_NOTHING,
) -> SaveAnswer:
    """The answer for a question that could not be put, or was put and refused.

    All of them refuse the sync; *shape* is what separates them for a reader.
    :data:`UNESTABLISHED_NOT_ASKED` where no question reached the resolver — no
    emulator resolved, nothing detected, no catalogue entry under that label,
    saves written to the content directory. :data:`UNESTABLISHED_NOTHING` where
    it was asked and could establish nothing, including where it declined or
    raised.
    """
    return SaveAnswer(
        state=SAVE_STATE_UNESTABLISHED,
        unestablished=shape,
        emulator=emulator,
        directory=None,
        backing_directory=None,
        granularity=None,
        needs=(),
        components=(),
        caveats=caveats,
    )


def build_save_answer(
    *,
    emulator: str | None,
    directory: str,
    backing_directory: str | None,
    granularity: str | None,
    needs: tuple[str, ...],
    file_set_state: str,
    files: tuple[str, ...],
    groups: tuple[SaveGroup, ...],
    caveats: tuple[str, ...],
) -> SaveAnswer:
    """Classify one resolved savefile placement into a :class:`SaveAnswer`.

    Takes the placement's fields as plain values so the rule stays free of the
    resolver's types. ``groups`` decomposes the save by directory and role and is
    the richer source; ``files`` is the placement's own primary list and is used
    where no group was given.
    """
    components = _components(directory, files, groups)
    state, shape = _classify(
        granularity=granularity,
        needs=needs,
        file_set_state=file_set_state,
        groups=groups,
        caveats=caveats,
        components=components,
    )
    return SaveAnswer(
        state=state,
        unestablished=shape,
        emulator=emulator,
        directory=directory,
        backing_directory=backing_directory,
        granularity=granularity,
        needs=needs,
        components=components,
        caveats=caveats,
    )


def _components(
    directory: str,
    files: tuple[str, ...],
    groups: tuple[SaveGroup, ...],
) -> tuple[SaveComponent, ...]:
    """The concrete files the answer names, each with what it is and where it sits.

    Built from the groups where there are any: they carry the role and their own
    directory, and in every reading taken so far they cover the placement's own
    file list and then some. A placement with no groups still names files — the
    plain per-game case — and those take the placement's directory and no role.
    """
    if not groups:
        return tuple(SaveComponent(name=name, directory=directory, role=None, granularity=None) for name in files)
    return tuple(
        SaveComponent(name=name, directory=group.directory, role=group.role, granularity=group.granularity)
        for group in groups
        for name in (group.files or ())
    )


def _classify(
    *,
    granularity: str | None,
    needs: tuple[str, ...],
    file_set_state: str,
    groups: tuple[SaveGroup, ...],
    caveats: tuple[str, ...],
    components: tuple[SaveComponent, ...],
) -> tuple[SaveState, UnestablishedShape | None]:
    """Decide the one state that holds, in a fixed precedence.

    The order is what makes "exactly one" well defined, and each step outranks
    the next for a reason:

    1. The save being inside the game's own file outranks everything — it is a
       positive statement that makes the directory answer meaningless.
    2. A file set the resolver refuses to state is nothing to reason further
       about.
    3. A hole outranks a shared card: an answer with both cannot be completed
       either way, and naming the hole says more about what is missing.
    4. Shared reads the placement's own granularity, not a group's. A group may
       be shared inside a per-game answer — MAME's emulator-wide ``default.cfg``
       sits beside per-game files — and that does not make the game's save
       shared.
    5. A stated directory whose names are not established is the second shape of
       "not established", and it outranks the bare form because it says more.

    Everything left names files with no hole, which is the one syncable state.
    """
    if not _INSIDE_CONTENT_CAVEATS.isdisjoint(caveats):
        return (SAVE_STATE_INSIDE_CONTENT, None)
    if file_set_state == _FILE_SET_UNKNOWN:
        return (SAVE_STATE_UNESTABLISHED, UNESTABLISHED_NOTHING)
    if needs:
        return (SAVE_STATE_HOLE, None)
    if granularity in _SHARED_GRANULARITIES:
        return (SAVE_STATE_SHARED, None)
    if _FILE_NAMES_UNESTABLISHED in caveats or any(group.files is None for group in groups):
        return (SAVE_STATE_UNESTABLISHED, UNESTABLISHED_DIRECTORY_KNOWN)
    if not components:
        return (SAVE_STATE_UNESTABLISHED, UNESTABLISHED_NOTHING)
    return (SAVE_STATE_PER_GAME_FILES, None)
