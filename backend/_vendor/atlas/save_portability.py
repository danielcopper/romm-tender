"""Whether one entry's save can be read by another — the save-portability question (#577).

A client that switches a game from one emulator to another needs to know
whether the save carries over. The two entries' save answers already say where
each keeps the game's save and which files make it up; this answer adds what
those cannot: whether the target reads what the source wrote.

The question is directed — ``target`` reads what ``source`` wrote — and is
answered **per part** of the save (#577 D2): a memory card in each slot, the
console's backup RAM, a backup cartridge. Each part names its
memory with a word of its own (:data:`~atlas.save_formats.SAVE_MEMORIES`) and,
where the system has several, its slot, beside the file group each entry's
save answer states for it (D6); the summary is the most severe part (D9).

Nothing here re-derives a placement. Both save answers are asked as they
stand, with the live mode each one read, and the packaged format records
(:mod:`atlas.save_formats`) label their file groups — so a live mode (Kronos's
Beetle-saves switch, LRPS2's card mode, SwanStation's twenty slot
combinations) arrives through the save answer, never through a reading of
this module's own. A verdict built on a source holds only where the machine names the build
the source was read at (D5); elsewhere the part answers ``unestablished`` and
says why. Where a part's layout differs by the kind of cartridge, which atlas
does not read, the part answers ``unestablished`` and states the verdict for
each kind (D15, D16); where the two entries size the part per game in
different ways, which atlas cannot tell apart without the game code, it
answers ``unestablished`` and names each side's way (D19).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Protocol

from ._agreement import Agreement, agreement
from .placement import (
    CAVEAT_SAVE_FILES_COMPRESSED,
    CAVEAT_SAVE_RECORD_AMBIGUOUS,
    CAVEAT_SAVE_RECORD_PARTIAL,
    CAVEAT_SAVE_RECORD_UNRECORDED,
    GRANULARITY_SHARED_CARD,
    GRANULARITY_SHARED_FILE,
    ROLE_UNKNOWN,
    Caveat,
    DataValue,
    FileGroup,
    SavefilePlacement,
    Unresolved,
)
from .firmware import spelled
from .platforms import ESDE_SOURCE, SystemPair
from .portability_codes import (
    CAVEAT_ENTRY_LABEL_UNKNOWN,
    CAVEAT_SAVE_ANSWER_UNRESOLVED,
    CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED,
    CAVEAT_SAVE_FORMAT_UNRECORDED,
    CAVEAT_SAVE_PART_UNESTABLISHED,
    PART_NO_GAME_DATA,
    PORTABILITY_CONVERTIBLE,
    PORTABILITY_NOT_READABLE,
    PORTABILITY_SAME_DATA,
    PORTABILITY_SAME_FILES,
    PORTABILITY_UNESTABLISHED,
    PORTABILITY_VERDICTS,
    REASON_BUILD_UNREAD,
    REASON_LAYOUT_DIFFERS_BY_CARTRIDGE,
    REASON_NO_CONVERSION_STATED,
    REASON_OTHER_BUILD,
    REASON_REGION_NOT_IN_CONTENT,
    REASON_SLOTS_UNESTABLISHED,
    REASON_SAVE_LENGTH_DIFFERS_BY_GAME,
    REASON_TARGET_FILE_UNNAMED,
    SIDE_SOURCE,
    SIDE_TARGET,
)
from .save_formats import (
    CARTRIDGE_KINDS,
    CLOCK_CARTRIDGE_KINDS,
    CONVERSION_RULES,
    MEMORY_CARTRIDGE_CLOCK,
    SAVE_MEMORIES,
    SAVE_REGIONS,
    EmulatorFormats,
    PartRule,
    SaveFormats,
    SaveLength,
    SystemFormats,
    save_formats,
)


class PortabilityEntry(Protocol):
    """What the computation reads off a catalogue entry — :class:`atlas.installations.EmulatorEntry` is one."""

    @property
    def label(self) -> str: ...

    @property
    def core_so(self) -> str | None: ...

    @property
    def emulator(self) -> str | None: ...

SaveMove = Literal["directory", "name", "granularity"]
MOVE_DIRECTORY: SaveMove = "directory"
"""The target keeps the part in another directory."""
MOVE_NAME: SaveMove = "name"
"""The target keeps the part under other file names."""
MOVE_GRANULARITY: SaveMove = "granularity"
"""One side keeps the part shared by every game and the other per game, so copying one game's file over a shared one
overwrites every other game's saves in it (#577 D11)."""
# What differs between two parts of the same data, the vocabulary of ``moves``.
SAVE_MOVES = (MOVE_DIRECTORY, MOVE_NAME, MOVE_GRANULARITY)


@dataclass(frozen=True, slots=True)
class Conversion:
    """A conversion a source pins as a plain rule (#577 D4); atlas states it and converts nothing."""

    rule: str
    """The rule, one of :data:`~atlas.save_formats.CONVERSION_RULES`."""
    length: int
    """The byte count the rule names."""

    def __post_init__(self) -> None:
        if self.rule not in CONVERSION_RULES:
            raise ValueError(f"Conversion: rule must be one of {CONVERSION_RULES}, got {self.rule!r}")


@dataclass(frozen=True, slots=True)
class SavePart:
    """One part of the source's save, and whether the target reads it."""

    memory: str
    """What this part holds, one of :data:`~atlas.save_formats.SAVE_MEMORIES`."""
    slot: str | None
    """The port the emulators name this part's memory by (``"1"``, ``"2"``), and ``None`` where the system has one."""
    verdict: str
    """Whether the target reads this part, one of :data:`PORTABILITY_VERDICTS`."""
    source_group: FileGroup
    """The source save answer's file group for this part, narrowed to the files that are this part."""
    target_group: FileGroup | None
    """The target save answer's file group for the same memory and slot, narrowed the same way, and ``None`` where
    that answer states none."""
    moves: tuple[SaveMove, ...] = ()
    """On a same-data verdict, what differs between the two groups, each one of :data:`SAVE_MOVES`; empty on every
    other verdict."""
    conversion: Conversion | None = None
    """On a convertible verdict whose conversion a source pins as a plain rule, that rule; ``None`` everywhere else."""
    region: str | None = None
    """The region the emulators keep this memory apart for, one of :data:`~atlas.save_formats.SAVE_REGIONS` (Dolphin's
    ``GC/USA/Card A`` and ``MemoryCardA.USA.raw``), and ``None`` where they keep one for every region (#577 D21)."""

    def __post_init__(self) -> None:
        if self.memory not in SAVE_MEMORIES:
            raise ValueError(f"SavePart: memory must be one of {SAVE_MEMORIES}, got {self.memory!r}")
        if self.region is not None and self.region not in SAVE_REGIONS:
            raise ValueError(f"SavePart: region must be None or one of {SAVE_REGIONS}, got {self.region!r}")
        if self.verdict not in PORTABILITY_VERDICTS:
            raise ValueError(f"SavePart: verdict must be one of {PORTABILITY_VERDICTS}, got {self.verdict!r}")
        if any(move not in SAVE_MOVES for move in self.moves):
            raise ValueError(f"SavePart: moves must be drawn from {SAVE_MOVES}, got {self.moves!r}")
        if bool(self.moves) != (self.verdict == PORTABILITY_SAME_DATA):
            raise ValueError("SavePart: moves stand exactly where the verdict is same-data")
        if self.conversion is not None and self.verdict != PORTABILITY_CONVERTIBLE:
            raise ValueError("SavePart: a conversion stands only on a convertible verdict")


# --- the computation ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortabilityOutcome[EntryT: PortabilityEntry]:
    """What :func:`portability_answer` settles, before the installation states it as an answer.

    The answer type itself lives beside the catalogue entry it names
    (:class:`atlas.installations.SavePortabilityAnswer`), as every answer that
    names an entry does; this carries the same fields over the entry type the
    computation was handed.
    """

    verdict: str
    source: EntryT | None
    target: EntryT | None
    parts: tuple[SavePart, ...] = ()
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()


@dataclass(frozen=True, slots=True)
class _Labelled:
    """One part of one side's save: what holds it, the narrowed group the answer states for it, and its region."""

    rule: PartRule
    group: FileGroup
    region: str | None = None


@dataclass(frozen=True, slots=True)
class _Side:
    """Everything one side contributes: its answer's parts, its record, and whether its build is confirmed."""

    name: str
    placement: SavefilePlacement
    record: SystemFormats | None
    parts: tuple[_Labelled, ...]
    confirmed: bool
    caveats: tuple[Caveat, ...]
    sources: tuple[str, ...]
    identity: _Identity
    # What the side's record was keyed over, where that is narrower than the
    # names asked (#784 D50): a statement of scope, which leaves the verdict
    # settled, unlike :attr:`caveats`.
    scope: tuple[Caveat, ...] = ()

    @property
    def mode(self) -> str | None:
        return _mode(self.placement)


def _core_key(core_so: str) -> str:
    """A core's short name, the key its records and cards are filed under."""
    return core_so.rsplit("/", 1)[-1].removesuffix(".so").removesuffix("_libretro")


def _mode(placement: SavefilePlacement) -> str | None:
    """The live mode the save answer read, or ``None`` where it states none."""
    return placement.granularity.mode if placement.granularity is not None else None


def _readings(placement: SavefilePlacement) -> dict[str, str]:
    """The answer's option readings that state a value, keyed by the option."""
    if placement.granularity is None:
        return {}
    return {r.key: r.value for r in placement.granularity.readings if r.value is not None}


def _claims(
    rules: tuple[PartRule, ...],
    name: str,
    readings: Mapping[str, str],
    *,
    folder: bool,
    slots: tuple[str, ...] | None = (),
) -> tuple[tuple[PartRule, str | None], ...]:
    """The rules that claim *name*, each with the region it read: every closest match, so two slots on one card file
    are both its parts. Where the save answer states the *slots* that read the file, a slot's rule claims it only for
    one of them (#577 D23), whatever the name spells; where it leaves them unestablished, every slot's rule may, and
    the part is judged unestablished (#577 D24)."""
    scored = [
        (claim[0], rule, claim[1])
        for rule in rules
        if rule.folder == folder
        and (not slots or rule.slot is None or rule.slot in slots)
        and (claim := rule.claim(name, readings)) is not None
    ]
    if not scored:
        return ()
    # The closest match per slot: a file two slots read is a part of each,
    # whichever of their rules spells it more literally.
    best: dict[str | None, int] = {}
    for score, rule, _ in scored:
        best[rule.slot] = max(score, best.get(rule.slot, score))
    return tuple((rule, region) for score, rule, region in scored if score == best[rule.slot])


def _group_parts(
    group: FileGroup, rules: tuple[PartRule, ...], readings: Mapping[str, str], names: tuple[str, ...]
) -> tuple[tuple[_Labelled, ...], bool]:
    """The parts one group holds among *names*, and whether a name in it is one no rule claims."""
    if group.files is None:
        claimed = _claims(rules, group.dir, readings, folder=True, slots=group.slots)
        return tuple(_Labelled(rule, group, region) for rule, region in claimed), not claimed
    by_rule: dict[tuple[PartRule, str | None], list[str]] = {}
    unmatched = False
    for name in names:
        claimed = _claims(rules, name, readings, folder=False, slots=group.slots)
        unmatched = unmatched or not claimed
        for labelled in claimed:
            by_rule.setdefault(labelled, []).append(name)
    return (
        tuple(
            _Labelled(rule, dataclasses.replace(group, files=tuple(names)), region)
            for (rule, region), names in by_rule.items()
        ),
        unmatched,
    )


@dataclass(frozen=True, slots=True)
class _Labelling:
    """One save answer's parts, whether some file stayed unlabelled, and the citations of what was set aside."""

    parts: tuple[_Labelled, ...]
    unmatched: bool
    set_aside: tuple[str, ...]


def _labelled(placement: SavefilePlacement, record: SystemFormats) -> _Labelling:
    """Every part one save answer states, labelled by its record, beside what the record sets aside and why."""
    mode = _mode(placement)
    rules = tuple(rule for rule in record.parts if rule.applies(mode))
    readings = _readings(placement)
    parts: list[_Labelled] = []
    unmatched = False
    set_aside: list[str] = []
    for group in placement.file_set.groups:
        if group.role == ROLE_UNKNOWN:
            continue
        names, cited = _kept_names(group, record)
        set_aside.extend(cited)
        if names is None:
            continue
        found, missed = _group_parts(group, rules, readings, names)
        parts.extend(found)
        unmatched = unmatched or missed
    return _Labelling(tuple(parts), unmatched, tuple(dict.fromkeys(set_aside)))


def _kept_names(group: FileGroup, record: SystemFormats) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """The names of *group* the record keeps — ``None`` where it sets the group aside whole — and the citations of what
    it set aside."""
    whole = next((ignored for ignored in record.ignore if ignored.covers(group.dir)), None)
    if whole is not None:
        return None, (whole.citation,)
    names: list[str] = []
    cited: list[str] = []
    for name in group.files or ():
        by_name = next((ignored for ignored in record.ignore if ignored.claims(name)), None)
        if by_name is None:
            names.append(name)
        else:
            cited.append(by_name.citation)
    if group.files is not None and not names:
        return None, tuple(cited)
    return tuple(names), tuple(cited)


@dataclass(frozen=True, slots=True)
class _Identity:
    """What a caveat names one side's emulator by: a core's short name, or a card's token.

    Neither is the launch identity (``emulator``), which the answer's entry
    carries itself. A standalone emulator no record covers is named by neither
    — its token is a card's only where a card states it — and the caveat's
    ``side`` points at the entry that says which it is.
    """

    core: str | None = None
    token: str | None = None

    @property
    def named(self) -> str:
        return self.core or self.token or "its emulator"


def _identity(entry: PortabilityEntry, formats: EmulatorFormats | None) -> _Identity:
    if entry.core_so is not None:
        return _Identity(core=_core_key(entry.core_so))
    return _Identity(token=formats.key if formats is not None else None)


def _unrecorded(side: str, identity: _Identity, mode: str | None) -> Caveat:
    # Each key is stored by name, so a reading of this site sees every key the
    # caveat can carry (tests/test_caveat_data_names_no_emulator_tripwire.py).
    data: dict[str, DataValue] = {"side": side}
    core, token = identity.core, identity.token
    if core is not None:
        data["core"] = core
    if token is not None:
        data["token"] = token
    if mode is not None:
        data["mode"] = mode
    return Caveat(
        CAVEAT_SAVE_FORMAT_UNRECORDED,
        f"no packaged save-format record tells apart the {side} entry's save as its answer states it "
        f"({identity.named}, mode {mode or 'none'}) — what its files hold is not established here",
        data,
    )


def _unkeyed(
    side: str,
    identity: _Identity,
    placement: SavefilePlacement,
    formats: EmulatorFormats | None,
    agreed: Agreement[SystemFormats],
    asked: tuple[SystemPair, ...],
) -> Caveat:
    """Why *side* weighs its answer against no save-format record (#784 D50, D55).

    No record for the emulator at all is a record never written; records that
    differ for the system asked are an ambiguity; records that hold none of it
    say the system is unrecorded for this emulator.
    """
    if formats is None:
        return _unrecorded(side, identity, _mode(placement))
    if agreed.differs:
        return _records_differ(side, identity, agreed)
    return _system_unrecorded(side, identity, asked)


def _system_unrecorded(side: str, identity: _Identity, asked: tuple[SystemPair, ...]) -> Caveat:
    """That the system asked is none the *side* entry's save-format records hold (#784 D55)."""
    # Each key is stored by name, as in :func:`_unrecorded`.
    data: dict[str, DataValue] = {"side": side}
    core, token = identity.core, identity.token
    if core is not None:
        data["core"] = core
    if token is not None:
        data["token"] = token
    if len(asked) == 1:
        data["source"] = asked[0][0]
        data["key"] = asked[0][1]
    else:
        data["sources"] = tuple(source for source, _ in asked)
        data["keys"] = tuple(key for _, key in asked)
    return Caveat(
        CAVEAT_SAVE_RECORD_UNRECORDED,
        f"the system asked ({', '.join(spelled(pair) for pair in asked)}) is none the {side} entry's "
        f"save-format records ({identity.named}) hold — what its files hold is not established here",
        data,
    )


def _records_differ(side: str, identity: _Identity, agreed: Agreement[SystemFormats]) -> Caveat:
    """Why the system both entries belong to keyed no save-format record on *side* (#784 D50)."""
    held = agreed.held
    # Each key is stored by name, as in :func:`_unrecorded`.
    data: dict[str, DataValue] = {"side": side}
    core, token = identity.core, identity.token
    if core is not None:
        data["core"] = core
    if token is not None:
        data["token"] = token
    data["sources"] = tuple(ESDE_SOURCE for _ in held)
    data["keys"] = held
    return Caveat(
        CAVEAT_SAVE_RECORD_AMBIGUOUS,
        f"the system asked stands for ES-DE's {', '.join(held)}{', among others' if agreed.uncovered else ''}, "
        f"and the {side} entry's save-format records ({identity.named}) differ between them — no record is "
        "consulted rather than one picked",
        data,
    )


def _records_partial(side: str, identity: _Identity, agreed: Agreement[SystemFormats]) -> Caveat:
    """The ES-DE names the system was asked as that *side*'s save-format records do not cover (#784 D50)."""
    uncovered = agreed.uncovered
    # Each key is stored by name, as in :func:`_unrecorded`.
    data: dict[str, DataValue] = {"side": side}
    core, token = identity.core, identity.token
    if core is not None:
        data["core"] = core
    if token is not None:
        data["token"] = token
    data["sources"] = tuple(ESDE_SOURCE for _ in uncovered)
    data["keys"] = uncovered
    return Caveat(
        CAVEAT_SAVE_RECORD_PARTIAL,
        f"the system asked stands for ES-DE's {', '.join((*agreed.held, *uncovered))}, and the "
        f"{side} entry's save-format records ({identity.named}) cover {', '.join(agreed.held)} but not "
        f"{', '.join(uncovered)} — the record consulted is theirs, and for the others it states nothing",
        data,
    )


def _build_statement(
    formats: EmulatorFormats, running: str | None, side: str, identity: _Identity
) -> tuple[bool, Caveat | None, str]:
    """Whether the machine names the build the record was read at, the caveat where it does not, and the sentence."""
    build = formats.build
    if running is not None and running in build.pins:
        kind = {
            "commit": f"which names the commit {build.revision} the record was read at",
            "release": f"a release string only, which names no commit; the record was read at {build.revision}",
            "channel": f"a channel name only, which follows the newest build; the record was read at {build.revision}",
        }[build.names]
        return True, None, f"{formats.key}: this machine names the build {running!r}, {kind}"
    data: dict[str, DataValue] = {"side": side}
    core, token = identity.core, identity.token
    if core is not None:
        data["core"] = core
    if token is not None:
        data["token"] = token
    data["pinned"] = build.pins
    data["reason"] = REASON_BUILD_UNREAD if running is None else REASON_OTHER_BUILD
    if running is not None:
        data["running"] = running
    message = (
        f"nothing here names the build of {formats.key!r}, so the {side} side's save formats, read at "
        f"{', '.join(build.pins)}, are not confirmed on this machine"
        if running is None
        else f"this machine runs {formats.key!r} {running!r}, and its save formats were read at "
        f"{', '.join(build.pins)} — a verdict is not carried across the difference"
    )
    return False, Caveat(CAVEAT_SAVE_FORMAT_BUILD_UNCONFIRMED, message, data), ""


def _side(
    name: str,
    entry: PortabilityEntry,
    placement: SavefilePlacement,
    *,
    systems: tuple[str, ...],
    asked: tuple[SystemPair, ...],
    table: SaveFormats,
    running: Callable[[], str | None],
    token: Callable[[], str | None],
) -> _Side:
    """One side's answer, labelled and weighed against its record and its build.

    A core's record is filed under its short name, a standalone emulator's under
    the card token the arrangement's own reading of the command gives — the
    token its save answer found the card by.
    """
    key = _core_key(entry.core_so) if entry.core_so is not None else token()
    formats = table.emulators.get(key or "")
    # Several ES-DE names for one system key a record where the ones it holds
    # state it alike; the ones it does not hold are named (#784 D50).
    agreed = agreement(formats.systems if formats is not None else {}, systems, lambda record: record)
    record = agreed.entry
    identity = _identity(entry, formats)
    if formats is None or record is None:
        refused = _unkeyed(name, identity, placement, formats, agreed, asked)
        return _Side(name, placement, None, (), False, (refused,), (), identity)
    labelling = _labelled(placement, record)
    confirmed, unconfirmed, sentence = _build_statement(formats, running(), name, identity)
    caveats = tuple(
        caveat
        for caveat in (
            unconfirmed,
            _unrecorded(name, identity, _mode(placement)) if labelling.unmatched else None,
        )
        if caveat is not None
    )
    # What the record sets aside is said where the answer says what it rests
    # on: a part left out is never a silent drop.
    sources = (*((sentence,) if sentence else ()), *labelling.set_aside)
    scope = (_records_partial(name, identity, agreed),) if agreed.uncovered else ()
    return _Side(name, placement, record, labelling.parts, confirmed, caveats, sources, identity, scope)


def _shared(group: FileGroup) -> bool:
    """Whether a group holds every game's data rather than one game's."""
    return group.granularity in (GRANULARITY_SHARED_CARD, GRANULARITY_SHARED_FILE)


def _moves(source: FileGroup, target: FileGroup) -> tuple[SaveMove, ...]:
    """What differs between two groups of the same data, in :data:`SAVE_MOVES` order."""
    differences: tuple[tuple[SaveMove, bool], ...] = (
        (MOVE_DIRECTORY, source.dir != target.dir),
        (MOVE_NAME, source.files != target.files),
        (MOVE_GRANULARITY, _shared(source) != _shared(target)),
    )
    return tuple(move for move, differs in differences if differs)


def _keeps(target: _Side, part: _Labelled) -> bool:
    """Whether the target's live mode keeps this memory and slot, whatever its answer happens to name."""
    if target.record is None:
        return False
    return any(
        rule.memory == part.rule.memory and rule.slot == part.rule.slot and rule.applies(target.mode)
        for rule in target.record.parts
    )


@dataclass(frozen=True, slots=True)
class _Judgement:
    """One part's verdict with what rides it: the moves, why it is unestablished, the verdict per cartridge kind or
    each side's length where that is why, and the extra citations."""

    verdict: str
    moves: tuple[SaveMove, ...] = ()
    unestablished: str | None = None
    cited: tuple[str, ...] = ()
    cartridges: tuple[tuple[str, str], ...] = ()
    other_cartridges: str | None = None
    lengths: tuple[SaveLength, SaveLength] | None = None


def _compressed(side: _Side, part: _Labelled) -> bool:
    """Whether RetroArch wrote this part compressed: its file is RetroArch's and the save answer says it compresses."""
    return part.rule.frontend and any(c.code == CAVEAT_SAVE_FILES_COMPRESSED for c in side.placement.caveats)


def _kinds_judged(part: PartRule, twin: PartRule | None, *sides: _Side) -> tuple[str | None, ...]:
    """The cartridge kinds a part is judged for apart — those either rule names or either side does not run, every
    clock kind for a clock — and ``None`` for every other kind."""
    named = {
        kind
        for rule in (part, *((twin,) if twin is not None else ()))
        for kind in (*(kind for kind, _ in rule.kinds), *(kind for group in rule.unread for kind in group.kinds))
    }
    named.update(
        kind
        for side in sides
        if side.record is not None
        for statement in side.record.not_run
        for kind in statement.kinds
    )
    if part.memory == MEMORY_CARTRIDGE_CLOCK:
        named.update(CLOCK_CARTRIDGE_KINDS)
    return (*(kind for kind in CARTRIDGE_KINDS if kind in named), None)


def _format_judgement(
    part: _Labelled, twin: _Labelled | None, formats: tuple[str, str | None], *, source: _Side, table: SaveFormats
) -> _Judgement:
    """The verdict between the two formats one cartridge kind's part takes on each side."""
    held, read = formats
    if twin is None or read is None:
        # The target keeps no such part, for this kind or at all (#577 D8).
        return _Judgement(PORTABILITY_NOT_READABLE)
    sized_apart = _lengths_apart(part.rule, twin.rule)
    if sized_apart is not None:
        # The two size one game's save each their own way, so it may be cut or
        # padded on the way whatever its layout, for games atlas cannot name
        # without the game code (#577 D19).
        return sized_apart
    if held != read:
        conversion = table.conversion(held, read)
        if conversion is not None:
            return _Judgement(PORTABILITY_CONVERTIBLE, cited=(conversion.citation,))
        if twin.rule.frontend:
            # RetroArch loads the file into the target's buffer byte for
            # byte, so the target reads the source's layout as its own.
            return _Judgement(PORTABILITY_NOT_READABLE, cited=(table.loads_raw,))
        return _Judgement(PORTABILITY_UNESTABLISHED, unestablished=REASON_NO_CONVERSION_STATED)
    if _compressed(source, part) and not twin.rule.frontend:
        # RetroArch reads its own RZIP stream back and an uncompressed file
        # too, so a core that RetroArch loads the part for reads it either
        # way; an emulator that opens the file itself reads compressed bytes.
        return _Judgement(PORTABILITY_CONVERTIBLE, cited=(table.compressed,))
    moves = _moves(part.group, twin.group)
    return _Judgement(PORTABILITY_SAME_DATA, moves) if moves else _Judgement(PORTABILITY_SAME_FILES)


@dataclass(frozen=True, slots=True)
class _KindVerdicts:
    """A part's verdict for each cartridge kind it was judged for, and what was cited on the way.

    ``empty`` are the kinds the part holds no game data for — no file of the
    source's, or a clock file a cartridge without a clock still gets (#577
    D16) — so nothing is lost with them.
    """

    judged: tuple[tuple[str | None, _Judgement], ...]
    empty: tuple[str | None, ...]
    cited: tuple[str, ...]


def _by_kind(
    part: _Labelled, twin: _Labelled | None, *, source: _Side, target: _Side, table: SaveFormats
) -> _KindVerdicts:
    """The part's verdict for each kind the source runs and keeps it for — not-readable where the target does not run
    the kind, and against a target that keeps no such part at all where *twin* is ``None``."""
    judged: list[tuple[str | None, _Judgement]] = []
    empty: list[str | None] = []
    cited: list[str] = []
    for kind in _kinds_judged(part.rule, twin.rule if twin is not None else None, source, target):
        unwritten = _not_run(source, kind)
        if unwritten is not None:
            # No game of this kind runs on the source's side, so no save of
            # it exists there to carry.
            cited.append(unwritten)
            continue
        held = part.rule.format_for(kind)
        if held is None or (part.rule.memory == MEMORY_CARTRIDGE_CLOCK and kind not in CLOCK_CARTRIDGE_KINDS):
            # The part holds no game data for this kind: the source keeps no
            # such part for it, or a cartridge of it has no clock (#577 D16).
            empty.append(kind)
            continue
        judged.append((kind, _kind_judgement(part, twin, kind, held, source=source, target=target, table=table)))
    return _KindVerdicts(tuple(judged), tuple(empty), tuple(cited))


def _kind_judgement(
    part: _Labelled,
    twin: _Labelled | None,
    kind: str | None,
    held: str,
    *,
    source: _Side,
    target: _Side,
    table: SaveFormats,
) -> _Judgement:
    """The verdict for one kind whose part the source holds game data in."""
    refused = _not_run(target, kind)
    if refused is not None:
        # The target does not run such a cartridge, so it reads none of its
        # save (#577 D15).
        return _Judgement(PORTABILITY_NOT_READABLE, cited=(refused,))
    read = twin.rule.format_for(kind) if twin is not None else None
    unread = part.rule.unread_for(kind) or (twin.rule.unread_for(kind) if twin is not None else None)
    if read is not None and unread is not None:
        # No read establishes one side's layout for this kind, so no verdict
        # is stated for it (#577 D15).
        return _Judgement(PORTABILITY_UNESTABLISHED, cited=(unread,))
    mirrors = _mirrors_apart(kind, source, target) if held == read else None
    if mirrors is not None:
        # The two keep a write through a mirror at different offsets, and a
        # kind this small gives every game the chance to (#577 D18).
        return _Judgement(PORTABILITY_UNESTABLISHED, cited=mirrors)
    return _format_judgement(part, twin, (held, read), source=source, table=table)


def _lengths_apart(source: PartRule, target: PartRule) -> _Judgement | None:
    """The unestablished judgement where both rules state what decides the length and it differs, else ``None``."""
    if source.length is None or target.length is None or source.length.agrees_with(target.length):
        return None
    return _Judgement(
        PORTABILITY_UNESTABLISHED,
        unestablished=REASON_SAVE_LENGTH_DIFFERS_BY_GAME,
        cited=(source.length.citation, target.length.citation),
        lengths=(source.length, target.length),
    )


def _mirrors_apart(kind: str | None, source: _Side, target: _Side) -> tuple[str, str] | None:
    """Both sides' mirror statements for *kind* where they handle a mirror write differently, else ``None``."""
    if kind is None or source.record is None or target.record is None:
        return None
    held, read = source.record.mirrors_for(kind), target.record.mirrors_for(kind)
    if held is None or read is None or held.handling == read.handling:
        return None
    return held.citation, read.citation


def _not_run(side: _Side, kind: str | None) -> str | None:
    """The citation saying the side's emulator does not run *kind*, or ``None``."""
    if kind is None or side.record is None:
        return None
    refusal = side.record.does_not_run(kind)
    return refusal.citation if refusal is not None else None


def _over_kinds(verdicts: _KindVerdicts) -> _Judgement:
    """One judgement for the part from its verdicts per kind (#577 D15, D16).

    Where every kind the part holds game data for answers alike, that is the
    part's verdict — kept also where some kinds hold none, so long as the files
    still carry over for them; otherwise the part is unestablished, and what
    holds per kind — its verdict, or that the part holds no game data for it —
    rides its caveat.
    """
    distinct = {(j.verdict, j.moves, j.unestablished, j.lengths) for _, j in verdicts.judged}
    cited = tuple(dict.fromkeys((*verdicts.cited, *(c for _, j in verdicts.judged for c in j.cited))))
    if len(distinct) == 1:
        first = verdicts.judged[0][1]
        lossless = first.verdict in (PORTABILITY_SAME_FILES, PORTABILITY_SAME_DATA)
        if lossless or not verdicts.empty:
            return _Judgement(
                first.verdict,
                moves=first.moves,
                unestablished=first.unestablished,
                cited=cited,
                cartridges=first.cartridges,
                other_cartridges=first.other_cartridges,
                lengths=first.lengths,
            )
    stated = {
        **{kind: judgement.verdict for kind, judgement in verdicts.judged},
        **dict.fromkeys(verdicts.empty, PART_NO_GAME_DATA),
    }
    other = stated.get(None)
    cartridges = tuple(
        (kind, stated[kind]) for kind in CARTRIDGE_KINDS if kind in stated and stated[kind] != other
    )
    return _Judgement(
        PORTABILITY_UNESTABLISHED,
        unestablished=REASON_LAYOUT_DIFFERS_BY_CARTRIDGE,
        cited=cited,
        cartridges=cartridges,
        other_cartridges=other,
    )


def _judged(
    part: _Labelled, twin: _Labelled | None, *, source: _Side, target: _Side, table: SaveFormats
) -> _Judgement:
    """One part's verdict, both builds confirmed."""
    if twin is None:
        # The target's mode keeps the part but its answer names no file for it
        # (a declared set that lists it only as an observation candidate, an
        # observed one whose file is not written yet): where it would lie is
        # not established. Otherwise the target keeps none (#577 D8).
        if _keeps(target, part):
            return _Judgement(PORTABILITY_UNESTABLISHED, unestablished=REASON_TARGET_FILE_UNNAMED)
    return _over_kinds(_by_kind(part, twin, source=source, target=target, table=table))


def _part_unestablished(part: _Labelled, judgement: _Judgement) -> Caveat:
    reason = judgement.unestablished or ""
    data: dict[str, DataValue] = {"memory": part.rule.memory}
    slot = part.rule.slot
    if slot is not None:
        data["slot"] = slot
    if part.region is not None:
        data["region"] = part.region
    data["reason"] = reason
    if reason == REASON_LAYOUT_DIFFERS_BY_CARTRIDGE:
        data["cartridges"] = dict(judgement.cartridges)
        if judgement.other_cartridges is not None:
            data["other_cartridges"] = judgement.other_cartridges
    if reason == REASON_SAVE_LENGTH_DIFFERS_BY_GAME and judgement.lengths is not None:
        held, read = judgement.lengths
        data["source_length"] = held.rule
        if held.revision is not None:
            data["source_length_revision"] = held.revision
        data["target_length"] = read.rule
        if read.revision is not None:
            data["target_length_revision"] = read.revision
    return Caveat(
        CAVEAT_SAVE_PART_UNESTABLISHED,
        f"whether the target reads the source's {part.rule.memory}{f' {slot}' if slot else ''}"
        f"{f' ({part.region})' if part.region else ''} is not established "
        f"({reason})",
        data,
    )


def _regionless(part: _Labelled, sides: tuple[_Side, _Side], content_path: str | None) -> tuple[str, ...]:
    """Each side's citation saying it runs *content_path* in a fallback region, where the part is kept per region."""
    if part.region is None:
        return ()
    return tuple(
        side.record.regionless.citation
        for side in sides
        if side.record is not None
        and side.record.regionless is not None
        and side.record.regionless.covers(content_path)
    )


def _slots_unestablished(part: _Labelled, twin: _Labelled | None) -> bool:
    """Is this part kept per slot, and does either side's group leave its slots unestablished (#577 D24)?"""
    return part.rule.slot is not None and (
        part.group.slots is None or (twin is not None and twin.group.slots is None)
    )


def _part(
    part: _Labelled, source: _Side, target: _Side, table: SaveFormats, content_path: str | None = None
) -> tuple[SavePart, tuple[str, ...], Caveat | None]:
    """One source part against the target, with the citations its verdict rests on and the caveat it raises."""
    # A memory kept per region pairs with the target's for the same region
    # (#577 D21): one game runs in one region on both sides.
    twin = next(
        (
            t
            for t in target.parts
            if (t.rule.memory, t.rule.slot, t.region) == (part.rule.memory, part.rule.slot, part.region)
        ),
        None,
    )
    fallback = _regionless(part, (source, target), content_path) if twin is not None else ()
    settled = source.confirmed and target.confirmed and target.record is not None
    if settled and _slots_unestablished(part, twin):
        # Which slot reads the group is not established on one side, so which
        # of the other's slots this part pairs with is not either (#577 D24).
        judgement = _Judgement(PORTABILITY_UNESTABLISHED, unestablished=REASON_SLOTS_UNESTABLISHED)
    elif settled and fallback:
        # Content with no region in its header runs in each emulator's
        # fallback region, so which region's memory it uses is not read
        # here (#577 D22); a slot the target keeps nothing in is lost
        # whatever the region, and is judged as ever.
        judgement = _Judgement(PORTABILITY_UNESTABLISHED, unestablished=REASON_REGION_NOT_IN_CONTENT, cited=fallback)
    elif settled:
        judgement = _judged(part, twin, source=source, target=target, table=table)
    else:
        # The build caveat or the unrecorded one already says why.
        judgement = _Judgement(PORTABILITY_UNESTABLISHED)
    cited = (part.rule.citation, *((twin.rule.citation,) if twin is not None else ()), *judgement.cited)
    raised = None if judgement.unestablished is None else _part_unestablished(part, judgement)
    return (
        SavePart(
            memory=part.rule.memory,
            slot=part.rule.slot,
            verdict=judgement.verdict,
            source_group=part.group,
            target_group=twin.group if twin is not None else None,
            moves=judgement.moves,
            region=part.region,
        ),
        tuple(c for c in cited if c),
        raised,
    )


def _summary(parts: tuple[SavePart, ...], *, settled: bool) -> str:
    """The most severe part (#577 D9) — no milder than unestablished where something stayed unsettled."""
    floor = PORTABILITY_SAME_FILES if settled and parts else PORTABILITY_UNESTABLISHED
    return max((floor, *(p.verdict for p in parts)), key=PORTABILITY_VERDICTS.index)


def _label_unknown(system: SystemPair, side: str, label: str) -> Caveat:
    return Caveat(
        CAVEAT_ENTRY_LABEL_UNKNOWN,
        f"no catalogue entry of system {system[1]!r} ({system[0]}) is labelled {label!r} — the {side} side "
        "names no entry",
        {"source": system[0], "key": system[1], "side": side, "label": label},
    )


def _unresolved(side: str, outcome: Unresolved) -> Caveat:
    return Caveat(
        CAVEAT_SAVE_ANSWER_UNRESOLVED,
        f"the {side} entry's save question refused ({outcome.code}), so there are no files to compare",
        {"side": side, "code": outcome.code},
    )


def portability_answer[EntryT: PortabilityEntry](
    system: SystemPair,
    *,
    record_systems: tuple[str, ...],
    asked: tuple[SystemPair, ...] = (),
    source: str,
    target: str,
    entries: tuple[EntryT, ...],
    ask: Callable[[EntryT], SavefilePlacement | Unresolved],
    running: Callable[[EntryT], str | None],
    token: Callable[[EntryT], str | None],
    caveats: tuple[Caveat, ...] = (),
    table: SaveFormats | None = None,
    content_path: str | None = None,
) -> PortabilityOutcome[EntryT]:
    """Whether the entry labelled *target* reads what the one labelled *source* wrote.

    *ask* is the entry's own save question with the caller's arguments, and
    *system* is the system both entries belong to, as the installation names it,
    and *record_systems* the ES-DE names the save-format records are filed under
    for it: one, or several, of which a record is consulted where the ones it
    holds state it alike — the others named in ``save-record-partial``, a
    disagreement in ``save-record-ambiguous`` (#784 D50) — and none where no
    ES-DE name stands for that system, when no record is consulted. *running*
    the build the machine names for an entry — a core's
    ``library_version``, a standalone emulator's deploy record — or ``None``
    where nothing names it. *token* is the card token a standalone entry's
    command identifies on this arrangement. *caveats* are the installation's own, which every
    answer on it carries. *content_path* is the content both save questions were asked with,
    which decides whether a part kept per region rests on a fallback region (#577 D22).
    """
    table = save_formats() if table is None else table
    # The first entry a label names, as the launch command takes it.
    named: dict[str, EntryT] = {}
    for entry in entries:
        named.setdefault(entry.label, entry)
    source_entry, target_entry = named.get(source), named.get(target)
    if source_entry is None or target_entry is None:
        missing = tuple(
            _label_unknown(system, side, label)
            for side, label, entry in ((SIDE_SOURCE, source, source_entry), (SIDE_TARGET, target, target_entry))
            if entry is None
        )
        return PortabilityOutcome(PORTABILITY_UNESTABLISHED, source_entry, target_entry, caveats=(*caveats, *missing))
    source_placement, target_placement = ask(source_entry), ask(target_entry)
    if not isinstance(source_placement, SavefilePlacement) or not isinstance(target_placement, SavefilePlacement):
        refused = tuple(
            _unresolved(side, outcome)
            for side, outcome in ((SIDE_SOURCE, source_placement), (SIDE_TARGET, target_placement))
            if isinstance(outcome, Unresolved)
        )
        return PortabilityOutcome(PORTABILITY_UNESTABLISHED, source_entry, target_entry, caveats=(*caveats, *refused))
    sides = (
        _side(SIDE_SOURCE, source_entry, source_placement, systems=record_systems, asked=asked or (system,),
              table=table,
              running=lambda: running(source_entry), token=lambda: token(source_entry)),
        _side(SIDE_TARGET, target_entry, target_placement, systems=record_systems, asked=asked or (system,),
              table=table,
              running=lambda: running(target_entry), token=lambda: token(target_entry)),
    )
    judged = tuple(_part(part, sides[0], sides[1], table, content_path) for part in sides[0].parts)
    parts = tuple(part for part, _, _ in judged)
    side_caveats = (*sides[0].caveats, *sides[1].caveats, *(c for _, _, c in judged if c is not None))
    if not parts and not side_caveats:
        # The source's answer states nothing any rule of its record names — a
        # mode the save answer could not establish, or one that keeps no save
        # at all — so there is no part to judge, and the answer says so
        # rather than standing empty.
        side_caveats = (_unrecorded(SIDE_SOURCE, sides[0].identity, sides[0].mode),)
    cited = tuple(dict.fromkeys((*sides[0].sources, *sides[1].sources, *(c for _, cs, _ in judged for c in cs))))
    return PortabilityOutcome(
        verdict=_summary(parts, settled=not side_caveats),
        source=source_entry,
        target=target_entry,
        parts=parts,
        sources=cited,
        caveats=(*caveats, *sides[0].scope, *sides[1].scope, *side_caveats),
    )
