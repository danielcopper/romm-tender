"""Which system a RomM platform is in an emulator source — the resolver's answer, asked through the source holder.

The one place Tender puts ``systems_for_platform`` to the resolver, and the
one that reads its platform vocabularies and statuses: each of RomM's ids is
asked in the resolver's vocabulary for it, and each match's status is read
into Tender's :class:`~domain.platform_system.SystemStatus`. Each source is
asked for itself: the answering source by default, or the source a caller
names — RetroDECK, for a download that lands in its folders. What the answers
decide is :func:`domain.platform_system.pick_system`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _vendor.atlas import PLATFORM_STATUS_ABSENT, PLATFORM_STATUS_DECLARED, PLATFORM_STATUS_DISABLED

from domain.emulator_sources import CATALOGUE_UNAVAILABLE, NO_SOURCE_DETECTED
from domain.platform_system import UNASKED, IdAnswer, PlatformSystem, SystemMatch, SystemStatus, pick_system

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from adapters.emulator_sources import EmulatorSourcesAdapter
    from domain.emulator_sources import ArrangedSource, SourcesReading
    from domain.platform_system import PlatformIds


# The resolver's vocabulary for each field of RomM's platform Tender asks about
# (``KNOWN_PLATFORM_VOCABULARIES``, whose names it exports only as a tuple).
_VOCABULARY_OF_FIELD = {
    "igdb_id": "igdb",
    "libretro_slug": "libretro",
    "ss_id": "screenscraper",
    "tgdb_id": "thegamesdb",
}

_STATUS_OF = {
    PLATFORM_STATUS_DECLARED: SystemStatus.ON,
    PLATFORM_STATUS_DISABLED: SystemStatus.OFF,
    PLATFORM_STATUS_ABSENT: SystemStatus.NOT_HERE,
}


class _Unasked(Exception):
    """A resolver question raised; the platform's system is not established."""


class AtlasPlatformSystemsAdapter:
    """Answers a platform's system in one source from the resolver.

    Implements the ``PlatformSystemsReader`` Protocol structurally.
    """

    def __init__(self, *, sources: EmulatorSourcesAdapter, log_debug: Callable[[str], None]) -> None:
        self._sources = sources
        self._log_debug = log_debug

    def platform_system(
        self,
        ids: PlatformIds,
        *,
        platform_slug: str,
        platform_name: str,
        source: str | None = None,
        reading: SourcesReading | None = None,
    ) -> PlatformSystem:
        """The system *platform_slug* is in *source* — the answering source where it is ``None``.

        A named source is asked whatever its switch, because the switch is the
        folder's question (``adapters/retrodeck_folders.py``), not the
        platform's. Where no source can be asked the answer is
        :data:`~domain.platform_system.UNASKED`, and ``unasked`` says why.
        """
        reading = reading if reading is not None else self._sources.read()
        unasked = self._unasked(reading, source)
        arranged = self._arranged(reading, source)
        if unasked is not None or arranged is None:
            return PlatformSystem(
                UNASKED,
                platform_slug,
                platform_name,
                unasked=unasked or reading.no_answer_reason(),
            )
        installation = next(i for i in reading.installations if i.kind == arranged.kind)
        try:
            pick = pick_system(self._answers(reading, installation, ids), platform_slug)
        except _Unasked:
            return PlatformSystem(UNASKED, platform_slug, platform_name, source=arranged, unasked=CATALOGUE_UNAVAILABLE)
        self._log_debug(f"[platforms] {platform_slug} in {arranged.kind}: {pick.state} {pick.system}")
        return PlatformSystem(pick.state, platform_slug, platform_name, system=pick.system, source=arranged)

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

    def _answers(self, reading: SourcesReading, installation: Any, ids: PlatformIds) -> Iterator[IdAnswer]:
        for field, value in ids.questions():
            vocabulary = _VOCABULARY_OF_FIELD[field]
            yield reading.remember(
                ("systems_for_platform", installation.kind, vocabulary, value),
                lambda vocabulary=vocabulary, value=value: self._ask(installation, vocabulary, value),
            )

    def _ask(self, installation: Any, vocabulary: str, value: str) -> IdAnswer:
        try:
            answer = installation.systems_for_platform(vocabulary, value)
        except Exception as exc:
            # Deliberately broad, for the reason the holder's own ``_ask`` gives.
            self._log_debug(f"[platforms] {installation.kind}: resolver failed on {vocabulary} {value}: {exc!r}")
            raise _Unasked from exc
        return IdAnswer(
            platforms=tuple(answer.platforms),
            systems=tuple(answer.systems),
            matches=tuple(
                SystemMatch(system=match.system, status=_STATUS_OF[match.status], tags=tuple(match.platforms))
                for match in answer.matches
            ),
        )
