"""Several candidates' answers as one: whether they are the same answer, and the answer they share (#784 D62–D71).

Where the asked pairs name several systems an installation holds, a question
asks every candidate as if it had been named alone
(:meth:`atlas.installations._CatalogueQueries._answered`) and hands the
answers here. Two candidates answer the same when their contract
serializations are equal with the resolution block aside, caveats included,
each candidate's own pair set aside where its answer states it (#784 D65 d);
that makes this module the one home of the comparison's dependency on the
serializers, and :mod:`atlas.installations` reaches it lazily, in one place,
since :mod:`atlas.contract` imports the answer types from there.

"Its own pair set aside" is decided per place across every candidate
(#784 D70): a statement — a caveat, or a refusal — sets the pair aside only
where every candidate's statement at that place names that candidate's own
system; anywhere else it is compared as it stands and, where alike, stated
once. An entry's ``system`` always names its own candidate. In the shared
answer a caveat set aside is stated once per candidate, in candidate order
(D65 e), a refusal set aside keeps its code and names every candidate as
``sources`` and ``keys`` (D69), a refusal that names no system comes back as
it stands (D71), and an entry names no system and asks its own questions of
every candidate's entry at its place (D66, D67).

The attributes of the compared answer types whose value does not reach the
serialization, or reaches it only in some answers, are part of the answer
all the same: the command an entry launches (D67 b) and the fields
:data:`COMPARED` lists are compared beside the serialization. The prose
:data:`JOINED` lists, which may spell a candidate's system, never decides
whether the candidates agree; the shared answer states its values as
:func:`_joined` says (D67 c). A tripwire
replaces each field of every instance of these classes the machines vectors
build with a probe, one at a time, and holds that each field whose probe does
not reach the serialization in every instance is in one of the two lists or
in its own reasoned list.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import fields, is_dataclass, replace
from typing import Any, cast, overload

from .contract import (
    catalogue_contract,
    firmware_contract,
    launch_command_contract,
    launchable_contract,
    mod_placement_contract,
    rom_placement_contract,
    save_portability_contract,
    savefile_placement_contract,
    savestate_absence_contract,
    savestate_placement_contract,
    texture_placement_contract,
    unresolved_contract,
)
from ._question import one_question
from .firmware import (
    CoreFirmware,
    FirmwareAlternatives,
    FirmwareAnswer,
    FirmwareIdentity,
    FirmwareRequirement,
    UnclaimedFile,
    spelled,
)
from .installations import (
    UNRESOLVED_SYSTEM_AMBIGUOUS,
    CatalogueAnswer,
    EmulatorEntry,
    LaunchabilityAnswer,
    LaunchCommandAnswer,
    RomPlacement,
    SavePortabilityAnswer,
)
from .placement import (
    UNRESOLVED_CORE_FILE_FOREIGN,
    UNRESOLVED_STANDALONE,
    UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED,
    Caveat,
    FileSet,
    Granularity,
    ModPlacement,
    OptionReading,
    SavefilePlacement,
    SavestateAbsence,
    SavestatePlacement,
    TexturePlacement,
    Unresolved,
)
from .platforms import SystemPair, SystemResolution, parallel_lists
from .standalone_saves import SaveRedirect

# Each answer type a candidate can give, and the serialization "the same" is
# read on. Keyed by the answer's type, which is what the comparison holds; the
# CLI and the vector runner pair a question's name with the serializer of its
# whole answer, a different key for a different caller.
_SERIALIZERS: tuple[tuple[type, Callable[[Any], dict[str, Any]]], ...] = (
    (CatalogueAnswer, catalogue_contract),
    (LaunchCommandAnswer, launch_command_contract),
    (LaunchabilityAnswer, launchable_contract),
    (FirmwareAnswer, firmware_contract),
    (RomPlacement, rom_placement_contract),
    (SavePortabilityAnswer, save_portability_contract),
    (SavefilePlacement, savefile_placement_contract),
    (SavestatePlacement, savestate_placement_contract),
    (SavestateAbsence, savestate_absence_contract),
    (TexturePlacement, texture_placement_contract),
    (ModPlacement, mod_placement_contract),
    (Unresolved, unresolved_contract),
)

# The values every walk here hands on whole instead of descending into their
# fields: a statement and an entry are handled as what they are, and the
# resolution is about the asking, not about a candidate.
_OPAQUE: tuple[type, ...] = (Caveat, Unresolved, EmulatorEntry, SystemResolution)

# Fields of the compared answer types whose value does not reach the
# serialization, or reaches it only in some answers, compared beside it: facts
# a client reads through the Python value. Some are read only for their truth
# (for_content), only through a property (the two CoreFirmware sets of
# systems, which may name the asked candidate's own system), or only in some
# contexts (a requirement's regions, inside an alternatives group). An entry's
# command is compared too (D67 b).
COMPARED: frozenset[tuple[type, str]] = frozenset(
    {
        (CoreFirmware, "absent_by_search"),
        (CoreFirmware, "system_firmware_needs"),
        (CoreFirmware, "unread"),
        (CoreFirmware, "unread_stating_a_path"),
        (CoreFirmware, "claims"),
        (FirmwareAlternatives, "for_content"),
        (FirmwareIdentity, "archive_reason"),
        (FirmwareIdentity, "known_as"),
        (FirmwareIdentity, "table_version"),
        (FirmwareRequirement, "contents_satisfied"),
        (FirmwareRequirement, "regions"),
        (FirmwareRequirement, "description"),
        (SaveRedirect, "outranked_by"),
    }
)
# Prose no serializer writes, which may spell a candidate's system: the
# comparison passes it by, and the shared answer states its values as
# :func:`_joined` says (#784 D67 c). Every field here is a string or a tuple
# of strings. The two
# ``message`` entries are joined in :func:`_statement`, since the walks hand a
# statement on whole; they stand here for the tripwire that holds this list.
JOINED: frozenset[tuple[type, str]] = frozenset(
    {
        (Caveat, "message"),
        (Unresolved, "message"),
        (FileSet, "provenance"),
        (Granularity, "provenance"),
        (OptionReading, "provenance"),
        (SaveRedirect, "citation"),
        *(
            (kind, "sources")
            for kind in (
                CatalogueAnswer,
                FirmwareAnswer,
                LaunchCommandAnswer,
                LaunchabilityAnswer,
                ModPlacement,
                RomPlacement,
                SavePortabilityAnswer,
                SavefilePlacement,
                SavestateAbsence,
                SavestatePlacement,
                TexturePlacement,
            )
        ),
    }
)


# The refusals whose data names the entry's own system, flat as ``source`` and
# ``key``: the ones an entry of a shared answer states once for every
# candidate, ``sources`` and ``keys`` in their place (#784 D69). A test holds
# this list against every ``Unresolved(...)`` in the package whose data is a
# dictionary literal naming both keys; data built elsewhere is not read, and
# an unlisted code fails loudly in :func:`_shared_refusal`.
OWN_SYSTEM_REFUSALS: tuple[str, ...] = (
    UNRESOLVED_STANDALONE,
    UNRESOLVED_STANDALONE_VARIANT_UNESTABLISHED,
    UNRESOLVED_CORE_FILE_FOREIGN,
)


class _Apart(Exception):
    """Two candidates' answers have different shapes at one place, so they are not the same answer."""


def serialized(answer: Any) -> dict[str, Any]:
    """*answer* in the contract form its type serializes to."""
    for kind, serialize in _SERIALIZERS:
        if isinstance(answer, kind):
            return serialize(answer)
    raise TypeError(f"no serializer compares a {type(answer).__name__}")


def _names_system(statement: Caveat | Unresolved, system: SystemPair) -> bool:
    """Whether *statement*'s data names *system*, flat as ``source`` and ``key`` (#784 D47)."""
    return statement.data.get("source") == system[0] and statement.data.get("key") == system[1]


def _own(statements: Sequence[Any], systems: Sequence[SystemPair]) -> bool:
    """Whether every candidate's statement at one place names that candidate's own system."""
    return all(
        isinstance(statement, (Caveat, Unresolved)) and _names_system(statement, system)
        for statement, system in zip(statements, systems, strict=True)
    )


def _fields_of(value: Any) -> Iterator[tuple[str, Any]]:
    """The fields a walk descends into: an answer dataclass's own, none of an opaque value's."""
    if isinstance(value, (*_OPAQUE, type)) or not is_dataclass(value):
        return
    for part in fields(value):
        yield part.name, getattr(value, part.name)


# --- the comparison -----------------------------------------------------------


class _AlignedEntry(EmulatorEntry):
    """One candidate's entry as the comparison reads it: its system and statements stated as the first candidate's.

    The comparison's view only, never part of an answer: what is set aside
    reads alike in every candidate, and everything else is the entry's own.
    """

    def __init__(self, entry: EmulatorEntry, system: SystemPair, caveats: tuple[Caveat, ...]) -> None:
        super().__init__(entry._installation, entry._spec, entry._caveats, entry._launch)
        self._aligned_system = system
        self._aligned_caveats = caveats

    @property
    def system(self) -> SystemPair | None:
        """The first candidate's system, which every candidate's entry is compared under."""
        return self._aligned_system

    @property
    def caveats(self) -> tuple[Caveat, ...]:
        """The entry's caveats, a statement set aside naming the first candidate's system."""
        return self._aligned_caveats


def _aligned(values: Sequence[Any], systems: Sequence[SystemPair]) -> list[Any]:
    """Each candidate's value at one place, its own pair set aside: stated as the first candidate's where own."""
    first = values[0]
    if any(type(value) is not type(first) for value in values):
        raise _Apart
    if isinstance(first, (Caveat, Unresolved)):
        if not _own(values, systems):
            return list(values)
        source, key = systems[0]
        return [replace(statement, data={**statement.data, "source": source, "key": key}) for statement in values]
    if isinstance(first, EmulatorEntry):
        entries = cast(Sequence[EmulatorEntry], values)
        # An entry stands for its own candidate's system; one that does not is
        # no answer the others give.
        if any(entry.system != system for entry, system in zip(entries, systems, strict=True)):
            raise _Apart
        caveats = _aligned([entry.caveats for entry in entries], systems)
        return [_AlignedEntry(entry, systems[0], caveats[i]) for i, entry in enumerate(entries)]
    if isinstance(first, tuple):
        tuples = cast(Sequence[tuple[Any, ...]], values)
        if any(len(value) != len(first) for value in tuples):
            raise _Apart
        places = [_aligned([value[place] for value in tuples], systems) for place in range(len(first))]
        return [tuple(place[i] for place in places) for i in range(len(values))]
    # Prose is passed by: it never decides whether the candidates agree (#784 D67 c).
    parts = [(name, part) for name, part in _fields_of(first) if (type(first), name) not in JOINED]
    if not parts:
        return list(values)
    aligned = {name: _aligned([getattr(value, name) for value in values], systems) for name, _ in parts}
    return [
        replace(value, **{name: aligned[name][i] for name, _ in parts if aligned[name][i] is not getattr(value, name)})
        for i, value in enumerate(values)
    ]


def _unserialized(value: Any) -> Iterator[Any]:
    """What *value* states that no serializer writes and the comparison holds: commands and :data:`COMPARED`."""
    if isinstance(value, EmulatorEntry):
        yield value.command
        return
    if isinstance(value, tuple):
        for item in cast(tuple[Any, ...], value):
            yield from _unserialized(item)
        return
    for name, part in _fields_of(value):
        if (type(value), name) in COMPARED:
            yield (type(value).__name__, name, part)
        else:
            yield from _unserialized(part)


def _compared(answer: Any) -> tuple[dict[str, Any], list[Any]]:
    """*answer* as candidates are compared (#784 D65 d, D67 b): its serialization, resolution aside, and the rest."""
    form = serialized(answer)
    form.pop("resolution", None)
    return form, list(_unserialized(answer))


def _in_file_order(answer: Any) -> Any:
    """*answer* in the order the comparison and the shared answer state, where it promises none (#784 D63).

    A firmware answer's cores: by core file name, an emulator without one
    after them by the identity its command states, then by label, and two
    alike on all three by their serialized form, so the order never falls back
    on one candidate's.
    """
    if not isinstance(answer, FirmwareAnswer):
        return answer

    def key(core: CoreFirmware) -> tuple[bool, str, str, str]:
        alone = firmware_contract(_with_cores(answer, (core,), unclaimed=()))["cores"][0]
        return (core.core_so is None, core.core_so or core.emulator or "", core.label or "", json.dumps(alone, sort_keys=True))

    return _with_cores(answer, tuple(sorted(answer.cores, key=key)), unclaimed=answer.unclaimed)


def _with_cores(
    answer: FirmwareAnswer, cores: tuple[CoreFirmware, ...], *, unclaimed: tuple[UnclaimedFile, ...]
) -> FirmwareAnswer:
    """*answer* with *cores* and *unclaimed* — ``dataclasses.replace`` behind a concrete signature.

    The one cast is for Sonar's type engine, which types ``replace`` as a bare
    dataclass instance rather than carrying its generic; basedpyright resolves
    it, and the signature makes it true (the pattern of the caveat-replacement
    helpers in :mod:`atlas.installations`).
    """
    return cast(FirmwareAnswer, replace(answer, cores=cores, unclaimed=unclaimed))


def alike(answers: Sequence[Any], systems: Sequence[SystemPair]) -> bool:
    """Whether every candidate gives the same answer (#784 D62, D65 d, D67 b, D69)."""
    try:
        aligned = _aligned(answers, systems)
    except _Apart:
        return False
    first = _compared(aligned[0])
    return all(_compared(answer) == first for answer in aligned[1:])


# --- the shared answer --------------------------------------------------------


@overload
def _joined(values: Sequence[str]) -> str: ...
@overload
def _joined(values: Sequence[tuple[str, ...]]) -> tuple[str, ...]: ...
def _joined(values: Sequence[str] | Sequence[tuple[str, ...]]) -> str | tuple[str, ...]:
    """The prose the candidates state (#784 D67 c): each distinct value once, in candidate order; where equal, that one.

    A string is joined by ``"; "`` and a tuple of strings is their union. The
    walks hand prose over untyped, so anything else is refused here.
    """
    first = cast(object, values[0])
    if all(value == first for value in values):
        return values[0]
    if isinstance(first, str):
        return "; ".join(dict.fromkeys(cast(Sequence[str], values)))
    if isinstance(first, tuple):
        return tuple(dict.fromkeys(item for value in cast(Sequence[tuple[str, ...]], values) for item in value))
    raise TypeError(f"prose is a string or a tuple of strings, got {type(first).__name__}")


def _shared_refusal(refusals: Sequence[Unresolved], systems: Sequence[SystemPair]) -> Unresolved:
    """The refusal every candidate's entry states for its own system, naming every candidate (#784 D69).

    Its code and every other datum are the candidates' own refusal's, as its
    construction site wrote them; ``sources`` and ``keys`` stand in place of
    ``source`` and ``key``, and the message states every candidate's. Only the
    codes :data:`OWN_SYSTEM_REFUSALS` lists name the entry's own system; any
    other would be a refusal nobody reviewed for this shape, and fails loudly.
    """
    first = refusals[0]
    if first.code not in OWN_SYSTEM_REFUSALS:
        raise AssertionError(f"{first.code} is no refusal that names the entry's own system (OWN_SYSTEM_REFUSALS)")
    rest = {name: value for name, value in first.data.items() if name not in ("source", "key")}
    sources, keys = parallel_lists(systems)
    return Unresolved(
        first.code,
        f"the entries of {', '.join(spelled(system) for system in systems)} all refuse this question for the same "
        f"reason ({first.code}): " + _joined([refusal.message for refusal in refusals]),
        {**rest, "sources": sources, "keys": keys},
    )


def _statement(values: Sequence[Any], systems: Sequence[SystemPair]) -> Any:
    """One statement stated once: alike in every candidate but its prose, which states every candidate's.

    A refusal naming each candidate's own system names them all (#784 D69);
    one naming none comes back as it stands (D71). A caveat naming each
    candidate's own system is stated per candidate, which only a tuple holds
    (:func:`_merged_items`).
    """
    if _own(values, systems):
        if isinstance(values[0], Caveat):
            raise AssertionError(
                "a caveat naming each candidate's own system stands where no tuple holds it, so it cannot be "
                "stated once per candidate"
            )
        return _shared_refusal(cast(Sequence[Unresolved], values), systems)
    message = _joined([value.message for value in values])
    return values[0] if message is values[0].message else replace(values[0], message=message)


def _merged(values: Sequence[Any], systems: Sequence[SystemPair], resolution: SystemResolution | None) -> Any:
    """The shared form of *values*, one per candidate in candidate order, which :func:`alike` found the same.

    The answer's resolution becomes *resolution*, the ``unanimous`` one.
    """
    first = values[0]
    if isinstance(first, SystemResolution):
        if resolution is None:
            raise AssertionError(
                "an answer that carries a resolution is merged without the unanimous one, and would keep the "
                "candidates' ambiguous resolution beside system-unanimous"
            )
        return resolution
    if isinstance(first, (Caveat, Unresolved)):
        return _statement(values, systems)
    if isinstance(first, EmulatorEntry):
        entries = cast(Sequence[EmulatorEntry], values)
        caveats = _merged([entry.caveats for entry in entries], systems, resolution)
        return SharedEntry(entries, caveats, tuple(systems))
    if isinstance(first, tuple):
        return _merged_items(cast(Sequence[tuple[Any, ...]], values), systems, resolution)
    changed: dict[str, Any] = {}
    for name, part in _fields_of(first):
        parts = [getattr(value, name) for value in values]
        merged = _joined(parts) if (type(first), name) in JOINED else _merged(parts, systems, resolution)
        if merged is not part:
            changed[name] = merged
    return replace(first, **changed) if changed else first


def _merged_items(
    values: Sequence[tuple[Any, ...]], systems: Sequence[SystemPair], resolution: SystemResolution | None
) -> tuple[Any, ...]:
    """:func:`_merged` over one tuple per candidate, place by place; a statement set aside is stated per candidate."""
    first = values[0]
    if any(len(value) != len(first) for value in values):
        raise AssertionError(
            f"the candidates' answers hold {[len(value) for value in values]} items at one place, which the "
            "comparison found alike — a field the comparison does not read, merged as one candidate's"
        )
    items: list[Any] = []
    for place in range(len(first)):
        at_place = [value[place] for value in values]
        if isinstance(at_place[0], Caveat) and _own(at_place, systems):
            items.extend(at_place)
        else:
            items.append(_merged(at_place, systems, resolution))
    return tuple(items)


def shared[A](
    answers: Sequence[A], systems: Sequence[SystemPair], resolution: SystemResolution | None = None
) -> A | None:
    """The answer every candidate gives, in its shared form under *resolution*, or ``None`` where two differ.

    *resolution* is the ``unanimous`` resolution of a system question's
    answer; an entry's own questions carry none. An answer type that carries
    a resolution needs it: merged without one, the merge raises
    ``AssertionError`` rather than keep the candidates' ``ambiguous``
    resolution beside ``system-unanimous``.
    """
    ordered = [_in_file_order(answer) for answer in answers]
    if not alike(ordered, systems):
        return None
    return cast(A, _merged(ordered, systems, resolution))


# --- an entry of a shared answer ---------------------------------------------


def _entries_differ(systems: Sequence[SystemPair]) -> Unresolved:
    """An entry of a shared answer whose candidates' entries answer its question differently (#784 D66, D67 a)."""
    sources, keys = parallel_lists(systems)
    return Unresolved(
        UNRESOLVED_SYSTEM_AMBIGUOUS,
        f"this entry stands for one entry of each of {len(systems)} systems "
        f"({', '.join(spelled(system) for system in systems)}), and their entries answer this question "
        "differently — atlas picks none; ask with the system meant",
        {"sources": sources, "keys": keys},
    )


class SharedEntry(EmulatorEntry):
    """One entry of a shared answer: the entries at one place of every candidate's answer (#784 D62, D65 e, D66, D67).

    The candidates' answers were found alike there (:func:`alike`) — their own
    systems set aside, their commands compared — so the entry states what
    they state, except that it names no system, that its caveats are the
    merged ones (*caveats*: a caveat naming each candidate's own system stated
    once per candidate, in candidate order) and that its provenance is the
    candidates' joined as :func:`_joined` says (#784 D67 c). Its own questions
    are asked of every candidate's entry at its place (*entries*, one per
    system of *systems*): where they answer alike that is the answer, and
    where they differ the entry refuses with ``system-ambiguous`` naming the
    candidates — never as one candidate, never with an exception of its own.
    """

    def __init__(
        self, entries: Sequence[EmulatorEntry], caveats: tuple[Caveat, ...], systems: tuple[SystemPair, ...]
    ) -> None:
        if len(entries) < 2 or len(entries) != len(systems):
            raise ValueError(
                f"SharedEntry: one entry per candidate, two or more, got {len(entries)} for {len(systems)} systems"
            )
        if any(entry.system != system for entry, system in zip(entries, systems, strict=True)):
            raise ValueError("SharedEntry: each entry is its own candidate's, in candidate order")
        first = entries[0]
        super().__init__(first._installation, first._spec, first._caveats, first._launch)
        self._entries = tuple(entries)
        self._shared_caveats = caveats
        self._systems = systems

    @property
    def system(self) -> SystemPair | None:
        """``None``: the entry stands for one entry of each of several systems, and names none of them."""
        return None

    @property
    def caveats(self) -> tuple[Caveat, ...]:
        """The candidates' entries' caveats, a caveat naming a candidate's system stated once per candidate."""
        return self._shared_caveats

    @property
    def provenance(self) -> str:
        """Which catalogue layer declared the candidates' entries, joined as :func:`_joined` says (#784 D67 c)."""
        return _joined([entry.provenance for entry in self._entries])

    def _asked_of_each[A](self, ask: Callable[[EmulatorEntry], A]) -> A | Unresolved:
        """*ask* of every candidate's entry: the answer they all give, or the refusal naming them (#784 D66)."""
        answer = shared([ask(entry) for entry in self._entries], self._systems)
        return _entries_differ(self._systems) if answer is None else answer

    @one_question
    def savefile_location(
        self, *, content_path: str | None = None, cwd: str | None = None, appendconfig: Sequence[str] = ()
    ) -> SavefilePlacement | Unresolved:
        """:meth:`EmulatorEntry.savefile_location`, asked of every candidate's entry (#784 D66)."""
        return self._keyed_savefile_location(None, content_path=content_path, cwd=cwd, appendconfig=appendconfig)

    def _keyed_savefile_location(
        self,
        asked: Any,
        *,
        content_path: str | None,
        cwd: str | None,
        appendconfig: Sequence[str],
    ) -> SavefilePlacement | Unresolved:
        """:meth:`EmulatorEntry._keyed_savefile_location`, asked of every candidate's entry (#784 D66)."""
        return self._asked_of_each(
            lambda entry: entry._keyed_savefile_location(  # pyright: ignore[reportPrivateUsage]
                asked, content_path=content_path, cwd=cwd, appendconfig=appendconfig
            )
        )

    @one_question
    def savestate_location(
        self, *, content_path: str | None = None, cwd: str | None = None, appendconfig: Sequence[str] = ()
    ) -> SavestatePlacement | SavestateAbsence | Unresolved:
        """:meth:`EmulatorEntry.savestate_location`, asked of every candidate's entry — a stated absence too (#784 D66)."""
        return self._asked_of_each(
            lambda entry: entry.savestate_location(content_path=content_path, cwd=cwd, appendconfig=appendconfig)
        )

    @one_question
    def texture_pack_location(self, *, content_path: str | None = None) -> TexturePlacement | Unresolved:
        """:meth:`EmulatorEntry.texture_pack_location`, asked of every candidate's entry (#784 D66)."""
        return self._asked_of_each(lambda entry: entry.texture_pack_location(content_path=content_path))

    @one_question
    def mod_location(self, *, content_path: str | None = None) -> ModPlacement | Unresolved:
        """:meth:`EmulatorEntry.mod_location`, asked of every candidate's entry (#784 D66)."""
        return self._asked_of_each(lambda entry: entry.mod_location(content_path=content_path))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SharedEntry(systems={self._systems!r}, label={self._spec.label!r}, kind={self._spec.kind!r})"
