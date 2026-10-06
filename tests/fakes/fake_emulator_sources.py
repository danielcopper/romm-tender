"""In-memory ``EmulatorSourcesReader`` implementation for service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from domain.emulator_sources import ArrangedSource, answering_source, no_answering_source_reason

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable

    from domain.emulator_sources import SourceReport

_T = TypeVar("_T")


class FakeSourcesReading:
    """A reading over fixed sources, with the same answer memory the real one keeps."""

    def __init__(self, sources: tuple[ArrangedSource, ...], installations: dict[str, Any]) -> None:
        self.sources = sources
        self._installations = installations
        self.installations = tuple(installations.values())
        self.answering = answering_source(sources)
        self._answers: dict[Hashable, Any] = {}

    def no_answer_reason(self) -> str:
        return no_answering_source_reason(self.sources)

    def answering_installation(self) -> Any:
        return self._installations.get(self.answering.kind) if self.answering is not None else None

    def remember(self, question: Hashable, ask: Callable[[], _T]) -> _T:
        if question not in self._answers:
            self._answers[question] = ask()
        return self._answers[question]


class FakeEmulatorSources:
    """Hands out readings over the sources a test named and counts them.

    ``sources`` defaults to one RetroDECK, switched on. ``installations`` maps a
    kind to the object a reading answers as that source's installation.
    ``reports`` is what :meth:`describe` answers. ``reads`` counts every reading
    taken, so a test can tell one reading per run from one per question.
    """

    def __init__(
        self,
        *,
        sources: tuple[ArrangedSource, ...] = (ArrangedSource(kind="retrodeck", enabled=True, starts_games=True),),
        installations: dict[str, Any] | None = None,
        reports: tuple[SourceReport, ...] = (),
    ) -> None:
        self.sources = sources
        self.installations: dict[str, Any] = installations if installations is not None else {}
        self.reports = reports
        self.reads = 0
        self.taken: list[FakeSourcesReading] = []

    def read(self) -> FakeSourcesReading:
        self.reads += 1
        reading = FakeSourcesReading(self.sources, self.installations)
        self.taken.append(reading)
        return reading

    def describe(self, reading: Any = None) -> tuple[SourceReport, ...]:
        return self.reports
