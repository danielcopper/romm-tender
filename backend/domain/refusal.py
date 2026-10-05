"""Refusals the domain raises itself.

Contract: the base for a refusal a domain rule decides on its own, as an
aggregate refusing an operation its state does not allow. It mirrors
``lib.errors.Refused``, the base the service layer raises, and is a second
class rather than that one because the domain imports nothing from ``lib``
(``.importlinter``). The entrypoint (``main.Endpoints``) translates both into
the same wire answer.
"""

from __future__ import annotations

from typing import Any


class DomainRefused(Exception):
    """An operation a domain rule refuses; ``reason`` is a literal at the raise site.

    The first argument, or the class attribute of a named subclass, which
    passes its own ``reason`` on. ``details`` are written beside ``reason`` and
    ``message`` on the wire. A broken invariant is not a refusal: it raises
    ``ValueError`` and its kind.
    """

    reason: str

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details
