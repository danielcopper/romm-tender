"""Start-up routines run as repairs: a failure is reported, never fatal."""

from __future__ import annotations

import logging

import pytest
from bootstrap.startup import StartupSteps

LOGGER = logging.getLogger("test_startup_steps")


@pytest.fixture
def failures():
    return []


@pytest.fixture
def steps(failures):
    return StartupSteps(LOGGER, failures.append)


class TestAStepThatFinishes:
    def test_it_runs_the_step(self, steps):
        ran = []

        steps.run("detect_retrodeck_path_change", lambda: ran.append(True))

        assert ran == [True]

    def test_it_reports_success(self, steps):
        assert steps.run("reconcile_orphaned_sync_runs", lambda: None) is True

    def test_nothing_is_recorded(self, steps, failures):
        steps.run("reconcile_orphaned_sync_runs", lambda: None)

        assert failures == []


class TestAStepThatFails:
    def _raise(self):
        raise RuntimeError("the artwork sweep broke")

    def test_the_failure_does_not_escape(self, steps):
        """Hosted, an escaping failure ends the process — and the restart policy loops."""
        steps.run("prune_orphaned_cover_cache", self._raise)

    def test_it_reports_failure(self, steps):
        assert steps.run("prune_orphaned_cover_cache", self._raise) is False

    def test_the_step_is_recorded_by_name(self, steps, failures):
        steps.run("prune_orphaned_cover_cache", self._raise)

        assert failures == ["prune_orphaned_cover_cache"]

    def test_the_traceback_reaches_the_log(self, steps, caplog):
        with caplog.at_level(logging.ERROR):
            steps.run("cleanup_leftover_tmp_files", self._raise)

        assert "the artwork sweep broke" in caplog.text
        assert "cleanup_leftover_tmp_files" in caplog.text

    def test_a_later_step_still_runs(self, steps):
        ran = []

        steps.run("prune_orphaned_cover_cache", self._raise)
        steps.run("cleanup_leftover_tmp_files", lambda: ran.append(True))

        assert ran == [True]

    def test_each_failure_is_recorded_separately(self, steps, failures):
        steps.run("prune_orphaned_cover_cache", self._raise)
        steps.run("cleanup_leftover_tmp_files", self._raise)

        assert failures == ["prune_orphaned_cover_cache", "cleanup_leftover_tmp_files"]


class TestTheOneEdgeBetweenSteps:
    """``report_missing_installs`` runs only after a SUCCESSFUL detection.

    The report reads the pending homes the detection writes; if the detection
    broke off, the installs under the home RetroDECK just left whose files are
    gone are reported as missing rather than as waiting for the move.
    """

    def test_a_failed_detection_answers_false_so_the_report_can_be_skipped(self, steps):
        def detection():
            raise RuntimeError("detection broke")

        reported = []
        if steps.run("detect_retrodeck_path_change", detection):
            steps.run("report_missing_installs", lambda: reported.append(True))

        assert reported == []

    def test_a_successful_detection_lets_the_report_run(self, steps):
        reported = []
        if steps.run("detect_retrodeck_path_change", lambda: None):
            steps.run("report_missing_installs", lambda: reported.append(True))

        assert reported == [True]


class TestABaseExceptionIsNotSwallowed:
    def test_a_cancellation_passes_through(self, steps):
        """Only a step's own failure is a repair failure; a shutdown is not."""

        def cancelled():
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            steps.run("prune_orphaned_cover_cache", cancelled)
