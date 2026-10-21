"""Tests for the stranding verdict over the local-save check's two facts."""

from __future__ import annotations

import pytest

from domain.save_stranding import stranding_verdict


class TestStrandingVerdict:
    @pytest.mark.parametrize(
        ("drifted", "only_here"),
        [(True, False), (False, True), (True, True)],
        ids=["changed-since-last-sync", "no-recorded-sync", "both"],
    )
    def test_either_fact_holds_the_gates(self, drifted, only_here):
        assert stranding_verdict(save_sync_on=True, checked=True, drifted=drifted, only_here=only_here) == "unsynced"

    def test_neither_fact_holds_nothing(self):
        assert stranding_verdict(save_sync_on=True, checked=True, drifted=False, only_here=False) == "none"

    def test_a_check_that_could_not_read_is_its_own_answer(self):
        assert stranding_verdict(save_sync_on=True, checked=False, drifted=False, only_here=False) == "unchecked"

    @pytest.mark.parametrize(
        ("checked", "drifted", "only_here"),
        [(True, True, False), (True, False, True), (False, False, False)],
        ids=["changed", "no-recorded-sync", "unchecked"],
    )
    def test_with_save_sync_off_nothing_holds(self, checked, drifted, only_here):
        assert stranding_verdict(save_sync_on=False, checked=checked, drifted=drifted, only_here=only_here) == "none"
