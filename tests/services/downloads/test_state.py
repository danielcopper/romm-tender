"""Tests for services/downloads/_state.py — what an admitted download registers, and who gives it back."""

from __future__ import annotations

from unittest.mock import MagicMock

from services.downloads._state import DownloadControl, DownloadsStateBox


def _admit(box: DownloadsStateBox, rom_id: int, *, reserved: int = 100) -> DownloadControl:
    control = DownloadControl()
    box.claim(rom_id)
    box.admit(
        rom_id, entry={"rom_id": rom_id, "status": "queued"}, task=MagicMock(), reserved=reserved, control=control
    )
    return control


class TestTheClaim:
    def test_a_claim_taken_is_held_until_it_is_dropped(self):
        box = DownloadsStateBox()

        box.claim(42)
        held = set(box.in_progress)
        box.drop_claim(42)

        assert held == {42}
        assert box.in_progress == set()


class TestForgetTasks:
    def test_forgets_every_task_and_nothing_else(self):
        box = DownloadsStateBox()
        control = _admit(box, 42)

        box.forget_tasks()

        assert box.tasks == {}
        assert box.reserved_bytes == {42: 100}
        assert box.control_tokens == {42: control}
        assert box.in_progress == {42}


class TestRelease:
    def test_the_attempt_that_registered_a_download_gives_back_all_of_it_but_its_entry(self):
        box = DownloadsStateBox()
        control = _admit(box, 42)

        box.release(42, control)

        assert box.tasks == {}
        assert box.reserved_bytes == {}
        assert box.control_tokens == {}
        assert box.in_progress == set()
        assert box.entries == {42: {"rom_id": 42, "status": "queued"}}

    def test_a_superseded_attempt_gives_back_nothing_of_the_newer_one(self):
        box = DownloadsStateBox()
        superseded = _admit(box, 42, reserved=100)
        newer = _admit(box, 42, reserved=200)

        box.release(42, superseded)

        assert 42 in box.tasks
        assert box.reserved_bytes == {42: 200}
        assert box.control_tokens == {42: newer}
        assert box.in_progress == {42}

    def test_another_roms_download_is_left_alone(self):
        box = DownloadsStateBox()
        control = _admit(box, 42)
        _admit(box, 7)

        box.release(42, control)

        assert set(box.tasks) == {7}
        assert box.in_progress == {7}


class TestBytesReservedByOthers:
    def test_counts_every_other_downloads_claim_less_what_it_has_written(self):
        box = DownloadsStateBox()
        _admit(box, 42, reserved=100)
        _admit(box, 7, reserved=300)
        box.entries[7]["_transferred"] = 50

        assert box.bytes_reserved_by_others(42) == 250
