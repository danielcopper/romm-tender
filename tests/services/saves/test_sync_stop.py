"""Tests for SyncStop — the stop of one sync, checked at its fixed points."""

from services.saves._sync_stop import SyncStop


def test_a_stop_not_asked_stops_nothing():
    stop = SyncStop()
    assert stop.stops_here() is False
    assert stop.stopped_the_sync() is False


def test_an_asked_stop_is_remembered_only_once_a_check_honoured_it():
    stop = SyncStop()
    stop.request()
    assert stop.stopped_the_sync() is False
    assert stop.stops_here() is True
    assert stop.stopped_the_sync() is True
