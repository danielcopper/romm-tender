"""A result that did part of its work before it failed.

Contract: the typed answer of an operation that stopped partway, carrying a
``reason`` and a ``message`` like a refusal and, beside them, what it did
before it stopped. It is a result rather than a raised ``lib.errors.Refused``
because what was done is part of the answer: a backend caller reads it as
typed fields, where a refusal's details are untyped and reached only by
catching.

A subclass declares what was done as its own fields; ``main.Endpoints``
serializes the result, and a service returning it never builds the failure
dict itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PartialFailure:
    """Why the operation stopped; a subclass adds what it did before that."""

    reason: str
    message: str
