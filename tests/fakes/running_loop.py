"""The event loop a *synchronous* fixture hands a service at construction time.

A service takes its loop in a frozen ``*ServiceConfig``, so a fixture has to name
one before any test is running — and a sync fixture body has no running loop to
name. ``asyncio.get_event_loop()`` used to answer there with the thread's default
loop, which pytest-asyncio then happened to run the test on; 1.4.0 dropped that
implicit loop and the call raises (#806).

What this hands over instead is not a loop but a forwarder: every attribute is
resolved against ``asyncio.get_running_loop()`` at the moment the service reaches
for it, so the service lands on the loop its test actually runs — the one
pytest-asyncio built for an async test, or the one a sync test drives through
``run_until_complete``. A sync test that never touches the loop never resolves
anything. Nothing is created here, so nothing is left unclosed.

Only the construction-time hand-off needs this. Code that is already inside the
loop — an async fixture, an async test, a sync helper an async test calls — asks
``asyncio.get_running_loop()`` directly and gets the loop itself.
"""

import asyncio
from typing import Any, cast


class _RunningLoop:
    """Forwards every attribute to the loop running when it is asked for."""

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio.get_running_loop(), name)

    def __repr__(self) -> str:
        return "<running loop — resolved on use>"


# One shared instance: it holds no state, and every service constructed by a sync
# fixture used to receive one and the same thread-default loop object, so sharing
# keeps the identity those fixtures always had.
_RUNNING_LOOP = _RunningLoop()


def running_loop() -> asyncio.AbstractEventLoop:
    """The loop a sync fixture passes as ``loop=``, resolved when the service uses it.

    Typed as the loop it stands in for: the seam takes an
    ``asyncio.AbstractEventLoop`` and every call that reaches this object is
    answered by a real one.
    """
    return cast("asyncio.AbstractEventLoop", _RUNNING_LOOP)
