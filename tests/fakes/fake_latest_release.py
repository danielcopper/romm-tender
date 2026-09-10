"""In-memory ``LatestReleaseFn`` implementation for service and contract tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.update_release import LatestRelease


class FakeLatestRelease:
    """Canned ``LatestReleaseFn`` — answers what it was given, and counts the asks.

    ``answer`` is what the production adapter would return: a
    :class:`~domain.update_release.LatestRelease`, or ``None`` for the whole
    class of failures the adapter swallows (offline, unreadable body, a payload
    naming no version). ``calls`` is what a throttle test asserts on.

    Set ``raises`` to make the seam misbehave the way its Protocol says it never
    will — the service's own silence has to hold against that too.
    """

    def __init__(self, answer: LatestRelease | None = None, raises: Exception | None = None) -> None:
        self.answer = answer
        self.raises = raises
        self.calls = 0

    def __call__(self) -> LatestRelease | None:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.answer
