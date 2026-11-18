"""Which system a RomM platform is in an emulator source — the resolver's answer, asked through the source holder.

The one place Tender puts the platform question to the resolver, and the one
that reads its platform sources and statuses. A platform is asked about with
exactly two (source, key) pairs: RomM's slug in RomM's source, and the
platform's folder in RomM as a bare folder name — the same pairs for every
source and every question, RomM's ids never among them. Each source is asked
for itself — the answering source by default, or the source a caller names
(RetroDECK, for a download that lands in its folders) — and answers in its own
systems; what it names is forwarded as it named it, and where it names none,
no other source stands in for it. What the matches come to is
:func:`domain.platform_system.matched_state`'s.

The platform answer carries no resolution of its own: whether the resolver can
match the platform at all is the translation's intersection, read here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _vendor.atlas import (
    CAVEAT_PLATFORM_EXCLUDED,
    CAVEAT_PLATFORM_PARTS_UNESTABLISHED,
    CAVEAT_PLATFORM_SOURCE_UNKNOWN,
    CAVEAT_PLATFORM_SOURCE_VERSION_DIFFERS,
    CAVEAT_PLATFORM_UNMAPPED,
    CAVEAT_PLATFORM_VERSION_UNAPPLIED,
    FOLDER_SOURCE,
    INTERSECTION_AGREED,
    INTERSECTION_CONFLICT,
    INTERSECTION_UNKNOWN,
    PLATFORM_STATUS_ABSENT,
    PLATFORM_STATUS_DECLARED,
    PLATFORM_STATUS_DISABLED,
)

from adapters.atlas_catalogue import catalogue_refusal_reason, catalogue_refused
from domain.emulator_sources import CATALOGUE_UNAVAILABLE, NO_SOURCE_DETECTED
from domain.platform_system import (
    CONTRADICTED,
    UNASKED,
    UNMATCHED,
    PlatformSystem,
    SystemPair,
    SystemStatus,
    matched_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from adapters.emulator_sources import EmulatorSourcesAdapter
    from domain.emulator_sources import ArrangedSource, SourcesReading
    from domain.platform_system import PlatformKeys, PlatformState

# RomM's column of the resolver's platform table, keyed by RomM's slug
# (``atlas/data/SOURCES.md``, pinned at the RomM release it names). The
# resolver exports the folder source's name, not this one.
_ROMM_SOURCE = "romm"

_STATUS_OF = {
    PLATFORM_STATUS_DECLARED: SystemStatus.ON,
    PLATFORM_STATUS_DISABLED: SystemStatus.OFF,
    PLATFORM_STATUS_ABSENT: SystemStatus.NOT_HERE,
}


# Why an asked pair is one the resolver's platform table does not hold, one
# code per such pair: a key its source does not have, one the table places on
# no system on purpose, or a source the table does not carry. Whichever it is,
# a platform none of whose pairs is held is one Tender cannot match yet
# (#2751 D2); the code is logged, never worded.
_PAIR_UNKNOWN_BECAUSE = frozenset({CAVEAT_PLATFORM_UNMAPPED, CAVEAT_PLATFORM_EXCLUDED, CAVEAT_PLATFORM_SOURCE_UNKNOWN})

# Notes beside a platform answer that change nothing Tender reads: a version a
# bare folder pair cannot apply and one the table does not pin (Tender states
# no version), and pairs that meet only through different parts of a system,
# which then answer no match of their own.
_NOTES = frozenset(
    {CAVEAT_PLATFORM_VERSION_UNAPPLIED, CAVEAT_PLATFORM_SOURCE_VERSION_DIFFERS, CAVEAT_PLATFORM_PARTS_UNESTABLISHED}
)


class _Unasked(Exception):
    """The platform's system is not established: the question raised, the source's catalogue was refused, or the
    answer is in a word Tender does not read. ``reason`` says why, in the vocabulary of
    :mod:`domain.emulator_sources`."""

    def __init__(self, reason: str = CATALOGUE_UNAVAILABLE) -> None:
        super().__init__(reason)
        self.reason = reason


class UnreadAnswer(Exception):
    """A platform answer in a word Tender does not read: an intersection status or a match status it does not know."""


def read_platform_answer(answer: Any) -> tuple[PlatformState, tuple[SystemPair, ...]]:
    """A platform answer in Tender's words: the state its matches come to, and the systems that state names.

    Where the resolver knows none of the pairs the state is
    :data:`~domain.platform_system.UNMATCHED`, and where they name different
    systems, of which it picks neither,
    :data:`~domain.platform_system.CONTRADICTED` (#2751 D2).
    Raises :class:`UnreadAnswer` for a status Tender does not read, which
    establishes no system rather than reading as none (#2195 D19).
    """
    intersection = answer.translation.intersection_status
    if intersection == INTERSECTION_UNKNOWN:
        return UNMATCHED, ()
    if intersection == INTERSECTION_CONFLICT:
        return CONTRADICTED, ()
    unread = sorted({match.status for match in answer.matches} - set(_STATUS_OF))
    if intersection != INTERSECTION_AGREED or unread:
        raise UnreadAnswer(f"intersection {intersection!r}, match statuses {unread}")
    return matched_state(
        (SystemPair(match.system[0], match.system[1]), _STATUS_OF[match.status]) for match in answer.matches
    )


def platform_pairs(keys: PlatformKeys) -> tuple[SystemPair, ...]:
    """The pairs a platform is asked about with: RomM's slug, and its folder in RomM where the caller names one."""
    pairs = [SystemPair(_ROMM_SOURCE, keys.slug)]
    if keys.folder is not None:
        pairs.append(SystemPair(FOLDER_SOURCE, keys.folder))
    return tuple(pairs)


class AtlasPlatformSystemsAdapter:
    """Answers a platform's system in one source from the resolver.

    Implements the ``SourcePlatformSystems`` Protocol structurally.
    """

    def __init__(self, *, sources: EmulatorSourcesAdapter, log_debug: Callable[[str], None]) -> None:
        self._sources = sources
        self._log_debug = log_debug

    def platform_system(
        self,
        keys: PlatformKeys,
        *,
        platform_name: str,
        source: str | None = None,
        reading: SourcesReading | None = None,
    ) -> PlatformSystem:
        """The system the platform *keys* names is in *source* — the answering source where it is ``None``.

        A named source is asked whatever its switch, because the switch is the
        folder's question (``adapters/retrodeck_folders.py``), not the
        platform's. Where no source can be asked the answer is
        :data:`~domain.platform_system.UNASKED`, and ``unasked`` says why.
        """
        reading = reading if reading is not None else self._sources.read()
        unasked = self._unasked(reading, source)
        arranged = self._arranged(reading, source)
        if unasked is not None or arranged is None:
            return PlatformSystem(UNASKED, keys, platform_name, unasked=unasked or reading.no_answer_reason())
        installation = next(i for i in reading.installations if i.kind == arranged.kind)
        pairs = platform_pairs(keys)
        try:
            answer = reading.remember(
                ("systems_for_platforms", installation.kind, pairs), lambda: self._ask(installation, pairs)
            )
        except _Unasked as unasked_because:
            return PlatformSystem(UNASKED, keys, platform_name, source=arranged, unasked=unasked_because.reason)
        state, named = answer
        self._log_debug(f"[platforms] {pairs} in {arranged.kind}: {state} {[tuple(n) for n in named]}")
        return PlatformSystem(state, keys, platform_name, source=arranged, named=named, asked=pairs)

    def asked_source(
        self, *, source: str | None = None, reading: SourcesReading | None = None
    ) -> ArrangedSource | None:
        """The source :meth:`platform_system` would ask for *source*, or ``None`` where there is none."""
        return self._arranged(reading if reading is not None else self._sources.read(), source)

    @staticmethod
    def _arranged(reading: SourcesReading, source: str | None) -> ArrangedSource | None:
        if source is None:
            return reading.answering
        return next((s for s in reading.sources if s.kind == source), None)

    @staticmethod
    def _unasked(reading: SourcesReading, source: str | None) -> str | None:
        """Why the named *source* cannot be asked; ``None`` where it can, or where none is named."""
        if source is None:
            return None
        if reading.detection_failed:
            return CATALOGUE_UNAVAILABLE
        return None if any(s.kind == source for s in reading.sources) else NO_SOURCE_DETECTED

    def _ask(self, installation: Any, pairs: tuple[SystemPair, ...]) -> tuple[PlatformState, tuple[SystemPair, ...]]:
        """What *installation* answers for *pairs*: the state its matches come to, and the systems it names."""
        try:
            answer = installation.systems_for_platforms(pairs)
        except Exception as exc:
            # Deliberately broad, for the reason the holder's own ``_ask`` gives.
            self._log_debug(f"[platforms] {installation.kind}: resolver failed on {pairs}: {exc!r}")
            raise _Unasked from exc
        codes = {caveat.code for caveat in answer.caveats}
        self._log_debug(
            f"[platforms] {installation.kind}: {pairs} {answer.translation.intersection_status}, unknown pairs "
            f"because {sorted(codes & _PAIR_UNKNOWN_BECAUSE)}, notes {sorted(codes & _NOTES)}, "
            f"other caveats {sorted(codes - _PAIR_UNKNOWN_BECAUSE - _NOTES)}"
        )
        if catalogue_refused(answer):
            # Matches over a catalogue that was not read, or not read whole, say
            # nothing of what this machine has: every system would read as one
            # it has not set up.
            raise _Unasked(catalogue_refusal_reason(answer))
        try:
            return read_platform_answer(answer)
        except UnreadAnswer as exc:
            self._log_debug(f"[platforms] {installation.kind}: {exc} unread on {pairs}")
            raise _Unasked from exc
