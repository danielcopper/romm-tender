"""The caveat codes of the save-portability question, and the closed vocabularies their data keys take.

They live apart from :mod:`atlas.save_portability`, which raises them, because
:data:`atlas.placement.ENUMERATED_DATA` registers the ``side``, ``reason`` and
verdict vocabularies under these codes, and :mod:`atlas.placement` cannot
import the module that imports its :class:`~atlas.placement.Caveat`.
"""

from __future__ import annotations

from typing import Final, Literal

# atlas could not confirm on this machine the build one entry's save formats
# were read at (#577 D5), so every part resting on that entry answers
# unestablished. ``data``: ``side``, the record's emulator as ``core`` (a
# libretro core's short name) or ``token`` (a standalone emulator's card
# token), ``pinned`` (the builds the record was read at), ``running`` (the
# build this machine names, absent where none was read) and ``reason``.
CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED: Final = "save-format-build-unconfirmed"
# No packaged format record covers one side's save as its answer states it: an
# emulator or system the delivered records do not cover, a live mode no part
# rule names, or a file group no rule recognises. ``data``: ``side``, the
# side's ``core`` where it runs a libretro core or its record's ``token``
# where a record names its standalone emulator (neither for a standalone
# emulator no record covers: the answer's entry names it), and ``mode``
# (absent on an answer that states no mode).
CAVEAT_SAVE_FORMAT_UNRECORDED: Final = "save-format-unrecorded"
# One side's save question refused (an :class:`~atlas.placement.Unresolved`),
# so there are no files to compare. ``data``: ``side`` and ``code``, the
# refusal's own code.
CAVEAT_SAVE_ANSWER_UNRESOLVED: Final = "save-answer-unresolved"
# No catalogue entry of the system carries the label one side names. ``data``:
# ``system``, ``side`` and ``label``.
CAVEAT_ENTRY_LABEL_UNKNOWN: Final = "entry-label-unknown"
# Both builds are confirmed and both records cover the pair, and one part still
# answers unestablished: why is the ``reason``. ``data``: ``memory``, ``slot``
# (absent for a memory a system has one of), ``region`` (a word of
# :data:`~atlas.save_formats.SAVE_REGIONS`, absent for a memory kept for
# every region, #577 D21) and ``reason``. Where the reason is
# that the part's layout differs by the cartridge's kind (#577 D15), the
# caveat also states what holds for each kind, one word of
# :data:`CARTRIDGE_STATEMENTS` each: ``cartridges`` maps each cartridge kind
# whose word differs from every other kind's to its word, and
# ``other_cartridges`` is the word for every other kind — ``no-game-data``
# where the part holds nothing of a game for them, as a clock does for a
# cartridge without one (#577 D16). Where the reason is that the two entries
# size the part per game in different ways (#577 D19), ``source_length`` and
# ``target_length`` name what decides the length on each side, one word of
# :data:`~atlas.save_formats.SAVE_LENGTH_RULES` each, and
# ``source_length_revision`` and ``target_length_revision`` the build whose
# list it is, where the rule is one a build's list decides (absent
# otherwise); the citations that say so ride the answer's sources.
CAVEAT_SAVE_PART_UNESTABLISHED: Final = "save-part-unestablished"

PortabilityVerdict = Literal["same-files", "same-data", "convertible", "unestablished", "not-readable"]
PORTABILITY_SAME_FILES: PortabilityVerdict = "same-files"
"""The target reads the source's files where they lie, under their names: nothing has to move."""
PORTABILITY_SAME_DATA: PortabilityVerdict = "same-data"
"""The target reads the same data once it is placed and named as the target's own save answer names it; ``moves``
says what differs."""
PORTABILITY_CONVERTIBLE: PortabilityVerdict = "convertible"
"""The target reads the data only after a conversion a source states; ``conversion`` carries the rule where it is
plain, and is ``null`` where a program performs it without one."""
PORTABILITY_UNESTABLISHED: PortabilityVerdict = "unestablished"
"""Nothing atlas read at a build it confirmed here settles whether the target reads it; the caveats say why."""
PORTABILITY_NOT_READABLE: PortabilityVerdict = "not-readable"
"""This part of the save does not carry over: the target keeps no such part in its live mode, or RetroArch loads the
source's bytes as they are into a part the target keeps in another layout."""
# The verdicts a part and a pair take, from the mildest to the most severe —
# the order the summary is taken in (#577 D9). They live here, beside the
# codes, because ``save-part-unestablished`` states one per cartridge kind and
# :data:`atlas.placement.ENUMERATED_DATA` closes those words.
PORTABILITY_VERDICTS = (
    PORTABILITY_SAME_FILES,
    PORTABILITY_SAME_DATA,
    PORTABILITY_CONVERTIBLE,
    PORTABILITY_UNESTABLISHED,
    PORTABILITY_NOT_READABLE,
)

CartridgeStatement = Literal[
    "same-files", "same-data", "convertible", "unestablished", "not-readable", "no-game-data"
]
CARTRIDGE_SAME_FILES: CartridgeStatement = "same-files"
"""For a cartridge of this kind the target reads the source's files where they lie."""
CARTRIDGE_SAME_DATA: CartridgeStatement = "same-data"
"""For a cartridge of this kind the target reads the same data, placed or named as the part's ``moves`` say."""
CARTRIDGE_CONVERTIBLE: CartridgeStatement = "convertible"
"""For a cartridge of this kind the target reads the data only after a conversion a source states."""
CARTRIDGE_UNESTABLISHED: CartridgeStatement = "unestablished"
"""For a cartridge of this kind nothing atlas read settles whether the target reads it."""
CARTRIDGE_NOT_READABLE: CartridgeStatement = "not-readable"
"""For a cartridge of this kind the part does not carry over: the target does not run such a cartridge, keeps no such
part for it, or reads the source's bytes as a layout of its own."""
PART_NO_GAME_DATA: CartridgeStatement = "no-game-data"
"""For a cartridge of this kind the part holds no game data — no file at all, or a clock file a cartridge without a
clock still gets — so nothing of the game is lost with it (#577 D16)."""
# What ``save-part-unestablished`` states for one cartridge kind: the part's
# verdict for it, in the words of :data:`PORTABILITY_VERDICTS`, or that the
# part holds no game data for it.
CARTRIDGE_STATEMENTS = (
    CARTRIDGE_SAME_FILES,
    CARTRIDGE_SAME_DATA,
    CARTRIDGE_CONVERTIBLE,
    CARTRIDGE_UNESTABLISHED,
    CARTRIDGE_NOT_READABLE,
    PART_NO_GAME_DATA,
)

PortabilitySide = Literal["source", "target"]
SIDE_SOURCE: PortabilitySide = "source"
"""The entry that wrote the save."""
SIDE_TARGET: PortabilitySide = "target"
"""The entry that would read it."""
# Which of the two entries a caveat is about, the vocabulary of ``side``.
PORTABILITY_SIDES = (SIDE_SOURCE, SIDE_TARGET)

BuildUnconfirmedReason = Literal["other-build", "build-unread"]
REASON_OTHER_BUILD: BuildUnconfirmedReason = "other-build"
"""The machine names a build, and it is none of the builds the record was read at."""
REASON_BUILD_UNREAD: BuildUnconfirmedReason = "build-unread"
"""Nothing atlas read here names the build: a core that did not answer, or a standalone emulator with no deploy
record of its build."""
# Why a build was not confirmed, the vocabulary of ``reason``.
BUILD_UNCONFIRMED_REASONS = (REASON_OTHER_BUILD, REASON_BUILD_UNREAD)

PartUnestablishedReason = Literal[
    "target-file-unnamed",
    "no-conversion-stated",
    "layout-differs-by-cartridge",
    "save-length-differs-by-game",
    "region-not-in-content",
    "slots-unestablished",
]
REASON_TARGET_FILE_UNNAMED: PartUnestablishedReason = "target-file-unnamed"
"""The target's live mode keeps such a part, and its save answer names no file for it: a card that lists the file only
as an observation candidate, or an observed directory the file is not written to yet."""
REASON_NO_CONVERSION_STATED: PartUnestablishedReason = "no-conversion-stated"
"""The two entries keep the part in two formats, the target opens the file itself, and no source the records cite
states a conversion between them."""
REASON_LAYOUT_DIFFERS_BY_CARTRIDGE: PartUnestablishedReason = "layout-differs-by-cartridge"
"""The verdict depends on the cartridge's kind, which atlas does not read (#577 D15): the caveat's ``cartridges`` and
``other_cartridges`` state what holds for each kind."""
REASON_SAVE_LENGTH_DIFFERS_BY_GAME: PartUnestablishedReason = "save-length-differs-by-game"
"""The two entries decide the part's length per game in different ways, so a game's save may be cut or padded on the
way, and atlas reads no game code to tell which games (#577 D19): the caveat's ``source_length`` and
``target_length`` name each side's way, and ``source_length_revision`` and ``target_length_revision`` the build whose
list it is."""
REASON_REGION_NOT_IN_CONTENT: PartUnestablishedReason = "region-not-in-content"
"""The part is kept per region, and the content names no region in its header (a ``.dol`` or ``.elf``), so each
emulator runs it in a fallback region of its own settings, which atlas does not read (#577 D22)."""
REASON_SLOTS_UNESTABLISHED: PartUnestablishedReason = "slots-unestablished"
"""The part is kept per slot, and a side's save answer does not establish which slots read the group that holds it
(its ``slots`` is ``None``), so which of the other's slots it pairs with is not established (#577 D24)."""
# Why a part with both builds confirmed is still unestablished, the vocabulary
# of ``save-part-unestablished``'s ``reason``.
PART_UNESTABLISHED_REASONS = (
    REASON_TARGET_FILE_UNNAMED,
    REASON_NO_CONVERSION_STATED,
    REASON_LAYOUT_DIFFERS_BY_CARTRIDGE,
    REASON_SAVE_LENGTH_DIFFERS_BY_GAME,
    REASON_REGION_NOT_IN_CONTENT,
    REASON_SLOTS_UNESTABLISHED,
)
