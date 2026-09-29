"""Contract test — ``get_host_status`` answers what the hosting process recorded about its own run.

The harness builds its ``Endpoints`` over a ``HostStatus`` nothing has written to,
so the cases that need a recorded run build their own over the same real
application, the way the entry point hands one in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from host import HostStatus
from main import Endpoints

if TYPE_CHECKING:
    from tests.contract._harness import ContractHarness


def _recorded_run() -> HostStatus:
    status = HostStatus()
    status.port = 43210
    status.record_failed_step("prune_orphaned_cover_cache")
    status.count_dropped_messages = lambda: 3
    return status


async def test_a_run_nothing_went_wrong_in_answers_so(harness: ContractHarness) -> None:
    assert harness.endpoints.get_host_status() == {"port": 0, "failed_startup_steps": [], "dropped_messages": 0}


async def test_it_answers_the_port_the_failed_steps_and_the_dropped_count(harness: ContractHarness) -> None:
    endpoints = Endpoints(harness.app, _recorded_run())

    assert endpoints.get_host_status() == {
        "port": 43210,
        "failed_startup_steps": ["prune_orphaned_cover_cache"],
        "dropped_messages": 3,
    }


async def test_a_step_that_fails_later_is_not_in_an_answer_already_given(harness: ContractHarness) -> None:
    status = _recorded_run()
    answer = Endpoints(harness.app, status).get_host_status()

    status.record_failed_step("cleanup_leftover_tmp_files")

    assert answer["failed_startup_steps"] == ["prune_orphaned_cover_cache"]


async def test_the_dropped_count_is_read_when_asked(harness: ContractHarness) -> None:
    """A number copied at start-up would answer 0 for the rest of the run."""
    status = _recorded_run()
    endpoints = Endpoints(harness.app, status)
    status.count_dropped_messages = lambda: 7

    assert endpoints.get_host_status()["dropped_messages"] == 7
