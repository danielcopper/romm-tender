"""The event sink — where a backend event goes when there is a panel, and when there is not.

Contract: the one place an event leaves this process, and the one that answers
**did this event reach anybody?**

**Nothing is buffered.** An event with no panel listening is dropped, with a log
line, and is not held for the next connection. The events this backend sends are
all statements about *now* — a sync finished, a download failed, a path changed
— and "sync finished" delivered three hours later is worse than never: it lands
in a session that never started one.

Dropping is exactly why the answer is returned. One caller acts on it: the
funnel in ``main.py`` that attaches a prune claim to the events whose Steam-side
work outlives the backend's. A claim handed to a panel that is not there is held
against every later operation until it expires, so the funnel releases it the
moment the sink says nobody heard. That case cannot arise today — of the nine
start-up routines only two send anything, and neither carries a claim — and the
answer exists for the day a claim-bearing event fires at start-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from host.protocol import encode_event

if TYPE_CHECKING:
    import logging


class EventSenderFn(Protocol):
    """Puts one encoded message on a live connection; answers whether it went."""

    async def __call__(self, text: str) -> bool: ...


class EventSink:
    """Sends events to the connected panel, or reports that there is none."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._sender: EventSenderFn | None = None
        self._dropped = 0

    @property
    def dropped(self) -> int:
        """How many events have been dropped for want of a listener."""
        return self._dropped

    @property
    def connected(self) -> bool:
        """Is a panel currently attached?"""
        return self._sender is not None

    def attach(self, sender: EventSenderFn) -> None:
        """Make *sender* the connection events go to, displacing any earlier one."""
        self._sender = sender

    def detach(self, sender: EventSenderFn) -> None:
        """Clear *sender* — but only if it is still the current one.

        The identity check is what keeps a dying connection from taking a newer
        one down with it. The newest connection always wins, so an older one's
        teardown runs *after* its replacement has already attached, and an
        unconditional clear there would leave the live panel receiving nothing.
        """
        if self._sender is sender:
            self._sender = None

    async def emit(self, name: str, *args: Any) -> bool:
        """Send the event *name* carrying one payload; answer whether it arrived.

        The signature keeps the variadic shape every service already emits
        through, and refuses more than one argument rather than inventing a wire
        form for it: a message carries exactly one ``payload``, every call site
        in this backend passes exactly one, and a second argument would be a
        protocol decision this raise makes someone take deliberately.
        """
        if len(args) > 1:
            raise TypeError(f"an event carries one payload; {name!r} was given {len(args)}")
        payload = args[0] if args else None

        sender = self._sender
        if sender is None:
            self._dropped += 1
            self._logger.info(f"host: no panel connected, dropping event {name!r}")
            return False

        delivered = await sender(encode_event(name, payload))
        if not delivered:
            self._dropped += 1
            self._logger.info(f"host: the connection went while sending event {name!r}")
        return delivered
