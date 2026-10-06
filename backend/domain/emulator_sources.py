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
from typing import TYPE_CHECKING

from domain.refusal import DomainRefused

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

RETRODECK = "retrodeck"

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


NO_SOURCE_DETECTED = "no_source"
ALL_SOURCES_SWITCHED_OFF = "switched_off"


def no_answering_source_reason(sources: Sequence[ArrangedSource]) -> str:
    """Why :func:`answering_source` found none: nothing detected, or everything switched off."""
    return ALL_SOURCES_SWITCHED_OFF if sources else NO_SOURCE_DETECTED


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


def _merged_order(*, detected: Sequence[str], stored_order: Sequence[str]) -> tuple[str, ...]:
    """The stored order, deduplicated, then every detected kind it does not name, in probe order."""
    merged = list(dict.fromkeys(stored_order))
    merged.extend(kind for kind in detected if kind not in merged)
    return tuple(merged)
