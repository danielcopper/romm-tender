"""What the panel can ask about the process hosting it.

Contract: the facts about this backend's own run that no service knows and no
event announces. It is a record the runtime fills in and a callable reads — one
number each, read when the panel opens.

Two of them exist because the alternative is a fault that lives only in a log
file. A start-up routine that fails on every start would otherwise never be
noticed by anyone who did not go looking, and a panel and backend that disagree
about the protocol would show as nothing at all — the messages are dropped and
the connection is deliberately kept. Neither is worth an event: an event is a
statement about a moment, and both of these are states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def _no_messages_dropped() -> int:
    """Before a server exists, nothing has been dropped."""
    return 0


@dataclass
class HostStatus:
    """The host's own answer about this run. Filled in as start-up proceeds."""

    port: int = 0
    failed_startup_steps: list[str] = field(default_factory=list)

    # Read rather than stored, because the count goes on changing for as long as
    # the connection lives: a number copied in here at start-up would answer 0
    # for the rest of the run, which is exactly the reading this counter exists
    # to contradict.
    count_dropped_messages: Callable[[], int] = _no_messages_dropped

    def record_failed_step(self, name: str) -> None:
        """Note that the start-up step *name* did not finish."""
        self.failed_startup_steps.append(name)
