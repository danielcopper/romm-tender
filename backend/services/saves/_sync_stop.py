"""A stop asked of one running sync: the sync ends at the first of its checks that sees it.

Where the sync checks it is ``docs/architecture/save-file-sync-architecture.md``'s,
"Pre-launch sync".
"""

from __future__ import annotations


class SyncStopped(Exception):
    """A sync reached one of its checks after its stop was asked, and stopped there."""


class SyncStop:
    """The stop of one sync: asked on the event-loop thread, checked there and on the worker thread its files move on.

    Plain bools — not ``threading.Event`` — because the import-linter
    ``no-stdlib-io-in-services`` contract forbids ``threading`` in services, and
    under the GIL a one-way set-once flip needs no synchronisation.
    """

    __slots__ = ("_honored", "_requested")

    def __init__(self) -> None:
        self._requested = False
        self._honored = False

    def request(self) -> None:
        """Ask the sync to stop at its next check."""
        self._requested = True

    def stops_here(self) -> bool:
        """Whether the sync stops at this check; remembers that it did."""
        if self._requested:
            self._honored = True
        return self._requested

    def stopped_the_sync(self) -> bool:
        """Whether a check stopped the sync, rather than the sync ending before any check saw the stop."""
        return self._honored
