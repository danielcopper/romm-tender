"""The event sink: what it sends, what it drops, and what it says it did."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from host.events import EventSink
from host.protocol import TYPE_EVENT

LOGGER = logging.getLogger("test_events")


class RecordingSender:
    """Stands in for a live connection, and can refuse the way a dead one does."""

    def __init__(self, *, delivers: bool = True) -> None:
        self.sent: list[dict[str, Any]] = []
        self._delivers = delivers

    async def __call__(self, text: str) -> bool:
        self.sent.append(json.loads(text))
        return self._delivers


@pytest.fixture
def sink():
    return EventSink(LOGGER)


class TestWithAPanelAttached:
    async def test_the_event_goes_out_in_the_declared_shape(self, sink):
        sender = RecordingSender()
        sink.attach(sender)

        await sink.emit("sync_complete", {"total_games": 3})

        assert sender.sent == [{"type": TYPE_EVENT, "name": "sync_complete", "payload": {"total_games": 3}}]

    async def test_it_reports_that_it_arrived(self, sink):
        sink.attach(RecordingSender())

        assert await sink.emit("sync_complete", {}) is True

    async def test_an_event_with_no_payload_carries_null(self, sink):
        sender = RecordingSender()
        sink.attach(sender)

        await sink.emit("sync_complete")

        assert sender.sent[0]["payload"] is None

    async def test_a_connection_that_went_mid_send_reports_a_miss(self, sink):
        sink.attach(RecordingSender(delivers=False))

        assert await sink.emit("sync_complete", {}) is False
        assert sink.dropped == 1


class TestWithNoPanel:
    async def test_it_reports_that_nobody_heard(self, sink):
        """One caller acts on this: the funnel releases a claim nobody can discharge."""
        assert await sink.emit("sync_complete", {"total_games": 3}) is False

    async def test_the_drop_is_counted(self, sink):
        await sink.emit("sync_complete", {})
        await sink.emit("prune_complete", {})

        assert sink.dropped == 2

    async def test_nothing_is_held_for_a_later_connection(self, sink):
        """'Sync finished' three hours later is worse than never."""
        await sink.emit("sync_complete", {"total_games": 3})

        sender = RecordingSender()
        sink.attach(sender)
        await sink.emit("prune_complete", {})

        assert [message["name"] for message in sender.sent] == ["prune_complete"]

    def test_it_knows_whether_anyone_is_attached(self, sink):
        assert sink.connected is False
        sink.attach(RecordingSender())
        assert sink.connected is True


class TestAttachAndDetach:
    async def test_the_newest_attachment_receives(self, sink):
        older, newer = RecordingSender(), RecordingSender()
        sink.attach(older)
        sink.attach(newer)

        await sink.emit("sync_complete", {})

        assert older.sent == []
        assert len(newer.sent) == 1

    async def test_detaching_a_displaced_sender_leaves_the_live_one_attached(self, sink):
        """A dying connection tears down after its replacement attached."""
        older, newer = RecordingSender(), RecordingSender()
        sink.attach(older)
        sink.attach(newer)

        sink.detach(older)

        assert await sink.emit("sync_complete", {}) is True
        assert len(newer.sent) == 1

    async def test_detaching_the_current_sender_leaves_nobody(self, sink):
        sender = RecordingSender()
        sink.attach(sender)

        sink.detach(sender)

        assert sink.connected is False
        assert await sink.emit("sync_complete", {}) is False


class TestOnePayload:
    async def test_a_second_argument_is_refused(self, sink):
        """A wire form for two payloads is a decision nobody has taken."""
        sink.attach(RecordingSender())

        with pytest.raises(TypeError, match="one payload"):
            await sink.emit("sync_complete", {"a": 1}, {"b": 2})

    async def test_the_refusal_names_the_event(self, sink):
        with pytest.raises(TypeError, match="sync_complete"):
            await sink.emit("sync_complete", 1, 2)
