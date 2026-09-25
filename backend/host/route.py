"""Marking a method as an endpoint — the one way a name becomes reachable.

Contract: ``@route`` sets a marker on the function it decorates and returns that
same function, so it changes nothing about how the method runs. What reads the
marker is :func:`host.dispatch.reachable_methods`, and
``scripts/check_callable_manifest.py`` reads the same decorator off the source.

It is the topmost decorator on the method. Placed beneath a gate it would still
be reachable, because ``functools.wraps`` copies the marker onto the gate's
wrapper, but the manifest gate counts only a first decorator and fails on any
other placement.
"""

from __future__ import annotations

from typing import Any

_MARK = "_host_route"


def route[F](func: F) -> F:
    """Mark *func* as an endpoint; the wire name is its own name."""
    setattr(func, _MARK, True)
    return func


def is_route(value: Any) -> bool:
    """Whether *value* carries the endpoint marker."""
    return getattr(value, _MARK, False) is True
