"""In-memory ``JournalEntriesFn`` for service and contract tests — reads no journal."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.update_output import JournalEntry


class FakeJournal:
    """Answers from ``entries``, a unit name per entry, the way ``journalctl --user`` narrows them.

    ``raises`` makes every read raise it instead, as a journal that could not
    be read does. ``reads`` records every read's arguments.
    """

    def __init__(self, entries: list[tuple[str, JournalEntry]] | None = None, raises: OSError | None = None) -> None:
        self.entries = list(entries or [])
        self.raises = raises
        self.reads: list[tuple[str | None, float | None, float | None, int | None]] = []

    def __call__(
        self,
        unit: str | None,
        *,
        since: float | None = None,
        until: float | None = None,
        last: int | None = None,
    ) -> tuple[JournalEntry, ...]:
        self.reads.append((unit, since, until, last))
        if self.raises is not None:
            raise self.raises
        found = [
            entry
            for owner, entry in sorted(self.entries, key=lambda pair: pair[1].at)
            if (unit is None or owner == unit)
            and (since is None or entry.at >= since)
            and (until is None or entry.at <= until)
        ]
        return tuple(found[-last:] if last is not None else found)
