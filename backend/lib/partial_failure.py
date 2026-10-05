"""A result that did part of its work before it failed.

Contract: the typed answer of an operation that stopped partway, carrying a
``reason`` and a ``message`` like a refusal and, beside them, what it did
before it stopped. It is a result rather than a raised ``lib.errors.Refused``
because an exception would discard the record of what was done, which the
panel shows and a backend caller reads.

A subclass declares what was done as its own fields. The entrypoint
(``main.Endpoints``) serializes the result into the wire's
``{"success": False, "reason", "message", **fields}`` answer; a service
returning it never builds that dict itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PartialFailure:
    """Why the operation stopped; a subclass adds what it did before that."""

    reason: str
    message: str
