"""Emulator sources — their order, their switches, and which one answers a game's questions.

An emulator source is what the resolver calls an installation, named by its
kind (GLOSSARY.md, "Emulator source"). This module decides from the detected
kinds and the user's stored preferences alone; detecting and storing are the
callers'. The stored preferences are keyed by kind, never by a folder, so a
source that moves keeps its place and its switch.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from domain.refusal import DomainRefused

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Sequence

RETRODECK = "retrodeck"

# Where the order and the switched-off kinds are stored in ``settings.json``.
ORDER_SETTING = "emulator_source_order"
SWITCHED_OFF_SETTING = "emulator_sources_off"

# Every game starts through RetroDECK until Tender starts emulators itself, so
# it is the one source a game's answers come from while it is detected and
# switched on, wherever the user put it in the order.
_STARTING_KIND = RETRODECK


@dataclass(frozen=True, slots=True)
class ArrangedSource:
    """One detected source in the user's order.

    ``starts_games`` says whether Tender can start a game through this source
    today; it is a property of the kind, not of the switch.
    """

    kind: str
    enabled: bool
    starts_games: bool


class SourcesReading(Protocol):
    """One reading of the emulator sources, and the answers asked through it.

    Taken by the adapter that detects the sources — per question for a call from
    the panel, and once by a run that asks the same questions for many games and
    hands it down — and every answer asked through it is kept for as long as the
    reading is. A service reads which sources it found and their arrangement;
    the installations are the resolver's own handles, and only an adapter puts
    questions to them.
    """

    @property
    def installations(self) -> tuple[Any, ...]:
        """The resolver's handles detected for this reading, in its probe order."""
        ...

    @property
    def sources(self) -> tuple[ArrangedSource, ...]:
        """Every detected source, in the user's order."""
        ...

    @property
    def answering(self) -> ArrangedSource | None:
        """The source a game's questions go to, or ``None`` where none answers."""
        ...

    def no_answer_reason(self) -> str:
        """Why no source answers: :data:`NO_SOURCE_DETECTED` or :data:`ALL_SOURCES_SWITCHED_OFF`."""
        ...

    def answering_installation(self) -> Any:
        """The resolver's handle for :attr:`answering`, or ``None``."""
        ...

    def remember[T](self, question: Hashable, ask: Callable[[], T]) -> T:
        """The answer to *question* through this reading, asked on its first use only."""
        ...


def arrange_sources(
    *, detected: Sequence[str], stored_order: Sequence[str], switched_off: Iterable[str]
) -> tuple[ArrangedSource, ...]:
    """The detected sources in the order the user sees them, each with its switch.

    *detected* is in the resolver's probe order, which is the default order.
    A stored order ranks the kinds it names; a detected kind it does not name
    follows them, in probe order, so a source seen for the first time joins at
    the end. Switched-off is a deny-list, so a source seen for the first time is
    on. A stored kind that is not detected now keeps its place in the stored
    order but is not listed.
    """
    off = frozenset(switched_off)
    return tuple(
        ArrangedSource(kind=kind, enabled=kind not in off, starts_games=kind == _STARTING_KIND)
        for kind in _merged_order(detected=detected, stored_order=stored_order)
        if kind in detected
    )


def answering_source(sources: Sequence[ArrangedSource]) -> ArrangedSource | None:
    """The source a game's emulator, firmware and save answers come from, or ``None``.

    RetroDECK while it is listed and switched on, because every start goes
    through it; otherwise the first switched-on source in the order, which
    answers for a start through that source's own frontend
    (``starts_games`` is false on it). ``None`` when nothing is detected or every
    detected source is switched off — :func:`no_answering_source_reason` tells
    the two apart.
    """
    enabled = [source for source in sources if source.enabled]
    starting = next((source for source in enabled if source.starts_games), None)
    if starting is not None:
        return starting
    return enabled[0] if enabled else None


# Why an emulator list could not be given: no source answers at all (the first
# two), or the answering source's catalogue was refused — sealed (EmuDeck's,
# which the resolver cannot read yet), a systems file ES-DE refuses to load, or
# any other reason nobody could read one.
NO_SOURCE_DETECTED = "no_source"
ALL_SOURCES_SWITCHED_OFF = "switched_off"
CATALOGUE_SEALED = "sealed"
CATALOGUE_INVALID = "catalogue_invalid"
CATALOGUE_UNAVAILABLE = "unavailable"

# What a source's catalogue is, as the settings row reports it: read, sealed,
# or unavailable — every other refusal, a broken systems file among them.
CATALOGUE_READ = "read"


def no_answering_source_reason(sources: Sequence[ArrangedSource]) -> str:
    """Why :func:`answering_source` found none: nothing detected, or everything switched off."""
    return ALL_SOURCES_SWITCHED_OFF if sources else NO_SOURCE_DETECTED


@dataclass(frozen=True, slots=True)
class SourceFinding:
    """One health finding of a source: the resolver's stable code and the facts it established."""

    code: str
    data: dict[str, str]


@dataclass(frozen=True, slots=True)
class SourceReport:
    """What the settings list shows for one detected source.

    ``root`` is the folder the resolver gives as the source's root, for display
    only, and ``None`` where that root is a default rather than where the source
    lies. ``catalogue`` is :data:`CATALOGUE_READ`, :data:`CATALOGUE_SEALED` or
    :data:`CATALOGUE_UNAVAILABLE`.
    """

    kind: str
    enabled: bool
    starts_games: bool
    root: str | None
    findings: tuple[SourceFinding, ...]
    catalogue: str


def move_source(*, kind: str, offset: int, detected: Sequence[str], stored_order: Sequence[str]) -> tuple[str, ...]:
    """The order to store after moving *kind* one place up (``-1``) or down (``+1``).

    A move swaps *kind* with its nearest DETECTED neighbour, because only
    detected sources are listed and a swap with a kind nobody sees would look
    like nothing happened. The answer is the whole merged order, undetected
    stored kinds included, so storing it keeps their places.

    Refuses ``unknown_source`` for a kind that is not detected, and
    ``cannot_move`` for a move past either end of the list.
    """
    if offset not in (-1, 1):
        raise ValueError(f"a source moves one place at a time, not {offset}")
    if kind not in detected:
        raise DomainRefused("unknown_source", f"No emulator source of kind {kind!r} is detected.", kind=kind)
    order = list(_merged_order(detected=detected, stored_order=stored_order))
    listed = [candidate for candidate in order if candidate in detected]
    position = listed.index(kind)
    neighbour_position = position + offset
    if not 0 <= neighbour_position < len(listed):
        raise DomainRefused("cannot_move", f"The emulator source {kind!r} cannot move further.", kind=kind)
    here, there = order.index(kind), order.index(listed[neighbour_position])
    order[here], order[there] = order[there], order[here]
    return tuple(order)


def switch_source(*, kind: str, enabled: bool, detected: Sequence[str], switched_off: Sequence[str]) -> tuple[str, ...]:
    """The switched-off kinds to store after switching *kind* on or off.

    Refuses ``unknown_source`` for a kind that is not detected: only a listed
    source has a switch. Kinds already in the list keep their order.
    """
    if kind not in detected:
        raise DomainRefused("unknown_source", f"No emulator source of kind {kind!r} is detected.", kind=kind)
    remaining = tuple(candidate for candidate in switched_off if candidate != kind)
    return remaining if enabled else (*remaining, kind)


def stored_kinds(value: object) -> tuple[str, ...]:
    """A stored list of kinds, or none where the stored value is not a list; non-text entries are dropped."""
    if not isinstance(value, list):
        return ()
    return tuple(kind for kind in value if isinstance(kind, str))


def _merged_order(*, detected: Sequence[str], stored_order: Sequence[str]) -> tuple[str, ...]:
    """The stored order, deduplicated, then every detected kind it does not name, in probe order."""
    merged = list(dict.fromkeys(stored_order))
    merged.extend(kind for kind in detected if kind not in merged)
    return tuple(merged)
