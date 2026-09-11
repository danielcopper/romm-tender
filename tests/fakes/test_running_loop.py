"""What the sync-fixture loop placeholder promises the services that hold it."""

import asyncio

import pytest

from fakes.running_loop import running_loop


async def test_it_forwards_to_the_loop_the_test_is_running_on():
    """The hop a service actually makes — ``run_in_executor`` — lands on the test's loop."""
    assert await running_loop().run_in_executor(None, lambda: "done") == "done"
    assert running_loop().create_future().get_loop() is asyncio.get_running_loop()


def test_one_placeholder_answers_for_whichever_loop_is_running():
    """Held once at construction, resolved per use.

    A sync fixture hands out the placeholder before any loop exists and the
    service keeps that one object for its lifetime, so the question it has to
    answer is not "which loop was there at construction" but "which loop is
    running now" — the shape a sync test driving its own ``run_until_complete``
    produces, where the loop is not the one pytest-asyncio would have made.
    """
    placeholder = running_loop()

    async def which_loop():
        return placeholder.create_future().get_loop()

    for _ in range(2):
        loop = asyncio.new_event_loop()
        try:
            assert loop.run_until_complete(which_loop()) is loop
        finally:
            loop.close()


def test_it_refuses_when_no_loop_is_running():
    """No loop is no answer. Silently standing in for one is what the ban is against."""
    with pytest.raises(RuntimeError, match="no running event loop"):
        running_loop().create_future()


def test_its_repr_resolves_nothing():
    """Printing it must be safe where using it is not — a failing assertion renders its arguments."""
    assert "running loop" in repr(running_loop())
