"""Emulator sources — the one place the resolver's installations are detected and held.

Every adapter that puts a question to the vendored
`emu-atlas <https://github.com/danielcopper/emu-atlas>`_ resolver gets its
installation from here and never detects one itself. A question goes to the
source :func:`domain.emulator_sources.answering_source` names; the order and
the switches it decides by are the user's, read from the live settings.

**Detected per reading, never at construction.** A reading
(:class:`DetectedSourcesReading`) is taken per question a panel call puts, or
once per run that asks the same questions for many games, so a source installed
later appears on the next one. Building this adapter detects nothing, which is
what lets the pre-install check build it unchanged.

**One machine for the process.** ``atlas.detect`` builds a fresh
``RealMachine`` whenever it is handed none, and that object is where the
resolver remembers a libretro core's probe — a subprocess that loads the core,
which a fresh machine runs again on every save question. The memory is keyed
on the core file's path, modification time and size (``RealMachine.read_core``,
atlas 0.21.0), so keeping one machine keeps every answer live while the probe
of an unchanged core runs once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _vendor.atlas import (
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    HEALTH_ISSUE_MARKER_INVALID,
    HEALTH_ISSUE_MARKER_MISSING,
    HEALTH_ISSUE_MARKER_UNREADABLE,
    detect,
)
from _vendor.atlas.machine import RealMachine

from adapters.atlas_catalogue import catalogue_refused
from domain.emulator_sources import (
    CATALOGUE_READ,
    CATALOGUE_SEALED,
    CATALOGUE_UNAVAILABLE,
    ORDER_SETTING,
    SWITCHED_OFF_SETTING,
    ArrangedSource,
    SourceFinding,
    SourceReport,
    answering_source,
    arrange_sources,
    no_answering_source_reason,
    stored_kinds,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Mapping

    from domain.emulator_sources import SourcesReading

# While a source's settings file is missing, unreadable or damaged, the root
# the resolver gives is its default (``~/retrodeck`` for RetroDECK), not where
# the source lies, so it is not shown.
_ROOT_IS_A_DEFAULT = frozenset(
    {HEALTH_ISSUE_MARKER_MISSING, HEALTH_ISSUE_MARKER_UNREADABLE, HEALTH_ISSUE_MARKER_INVALID}
)


class DetectedSourcesReading:
    """One reading of the emulator sources (:class:`domain.emulator_sources.SourcesReading`).

    Holds the installations detected for it, the user's arrangement of them as
    it stood when the reading was taken, and a memory of the questions put
    through :meth:`remember`. The memory lives exactly as long as the reading,
    so a reading is kept no longer than the call or the run that took it.
    """

    def __init__(self, *, installations: tuple[Any, ...], sources: tuple[ArrangedSource, ...]) -> None:
        self._detected = installations
        self._installations = {installation.kind: installation for installation in installations}
        self._sources = sources
        self._answering = answering_source(sources)
        self._answers: dict[Hashable, Any] = {}

    @property
    def installations(self) -> tuple[Any, ...]:
        """The resolver's handles detected for this reading, in its probe order."""
        return self._detected

    @property
    def sources(self) -> tuple[ArrangedSource, ...]:
        """Every detected source, in the user's order."""
        return self._sources

    @property
    def answering(self) -> ArrangedSource | None:
        """The source a game's questions go to, or ``None`` where none answers."""
        return self._answering

    @property
    def answering_kind(self) -> str | None:
        """The kind of :attr:`answering`, or ``None``."""
        return self._answering.kind if self._answering is not None else None

    def no_answer_reason(self) -> str:
        """Why no source answers: ``no_source`` or ``switched_off``."""
        return no_answering_source_reason(self._sources)

    def answering_installation(self) -> Any | None:
        """The resolver's handle for :attr:`answering`, or ``None``."""
        return self._installations[self._answering.kind] if self._answering is not None else None

    def installation(self, kind: str) -> Any | None:
        """The resolver's handle for the detected source of *kind*, or ``None``."""
        return self._installations.get(kind)

    def remember[T](self, question: Hashable, ask: Callable[[], T]) -> T:
        """The answer to *question* through this reading, asked on its first use only."""
        if question not in self._answers:
            self._answers[question] = ask()
        return self._answers[question]


class EmulatorSourcesAdapter:
    """Detects the emulator sources and arranges them by the user's settings.

    Implements the ``EmulatorSourcesReader`` Protocol structurally. *settings*
    is the live settings dict, read at every :meth:`read`, so a switch or a move
    written through the settings' owner takes effect on the next reading.
    """

    def __init__(
        self,
        *,
        user_home: str,
        settings: Mapping[str, Any],
        log_debug: Callable[[str], None],
        detect_installations: Callable[[str, Any], list[Any]] = detect,
        machine: Any = None,
    ) -> None:
        self._user_home = user_home
        self._settings = settings
        self._log_debug = log_debug
        self._detect = detect_installations
        self._machine = machine if machine is not None else RealMachine()

    def read(self) -> DetectedSourcesReading:
        """A fresh reading: detect the sources, and arrange them as the settings stand now."""
        try:
            installations = tuple(self._detect(self._user_home, self._machine))
        except Exception as exc:
            # Deliberately broad: detection is the resolver's own, and the
            # honest answer to "could not detect" is the same whatever raised.
            self._log_debug(f"[sources] detection failed, answering with none: {exc!r}")
            installations = ()
        reading = self._arranged(installations)
        self._log_debug(
            f"[sources] detected={[source.kind for source in reading.sources]} "
            f"off={[source.kind for source in reading.sources if not source.enabled]} "
            f"answering={reading.answering_kind}"
        )
        return reading

    def describe(self, reading: SourcesReading | None = None) -> tuple[SourceReport, ...]:
        """Every detected source as the settings list shows it.

        Over the sources *reading* detected, arranged as the settings stand now,
        so a write's answer describes what the write found without detecting
        again; without one, from a fresh reading.
        """
        arranged = self._arranged(reading.installations) if reading is not None else self.read()
        return tuple(self._report(source, arranged.installation(source.kind)) for source in arranged.sources)

    def _arranged(self, installations: tuple[Any, ...]) -> DetectedSourcesReading:
        sources = arrange_sources(
            detected=tuple(installation.kind for installation in installations),
            stored_order=stored_kinds(self._settings.get(ORDER_SETTING)),
            switched_off=stored_kinds(self._settings.get(SWITCHED_OFF_SETTING)),
        )
        return DetectedSourcesReading(installations=installations, sources=sources)

    def _report(self, source: ArrangedSource, installation: Any) -> SourceReport:
        findings = self._findings(source.kind, installation)
        codes = {finding.code for finding in findings}
        return SourceReport(
            kind=source.kind,
            enabled=source.enabled,
            starts_games=source.starts_games,
            root=None if codes & _ROOT_IS_A_DEFAULT else self._ask(source.kind, "root", installation.root),
            findings=findings,
            catalogue=self._catalogue_state(source.kind, installation),
        )

    def _findings(self, kind: str, installation: Any) -> tuple[SourceFinding, ...]:
        health = self._ask(kind, "health", installation.health)
        if health is None:
            return ()
        return tuple(
            SourceFinding(code=issue.code, data={str(key): str(value) for key, value in issue.data.items()})
            for issue in health.issues
        )

    def _catalogue_state(self, kind: str, installation: Any) -> str:
        answer = self._ask(kind, "systems", installation.systems)
        if answer is None:
            return CATALOGUE_UNAVAILABLE
        if any(caveat.code == CAVEAT_EMULATOR_CATALOGUE_SEALED for caveat in answer.caveats):
            return CATALOGUE_SEALED
        return CATALOGUE_UNAVAILABLE if catalogue_refused(answer) else CATALOGUE_READ

    def _ask(self, kind: str, subject: str, question: Callable[[], Any]) -> Any:
        try:
            return question()
        except Exception as exc:
            self._log_debug(f"[sources] {kind}: resolver failed on {subject}: {exc!r}")
            return None
