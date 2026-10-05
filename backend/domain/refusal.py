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
    """An operation a domain rule refuses; the contract is ``lib.errors.Refused``'s, whose docstring states it."""

    reason: str

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details


class NamedDomainRefused(DomainRefused):
    """A domain refusal named for its reason; the same contract as ``lib.errors.NamedRefused``."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(type(self).reason, message, **details)
