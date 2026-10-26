"""DownloadQueue — the download queue's entries and every frame that reports one.

An entry is what the frontend is shown about one ROM's download, from its start
until it is cleared; a ``download_progress`` frame is how it hears that an entry
changed. The entries themselves live in the package's state box
(``_state.py``), because an admitted download registers its entry there with
its task, its reservation and its control token; every later change to an
entry, what an entry becomes on the wire, and how often a running download
reports, is decided here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from domain.download_frames import cancelled_frame, progress_frame, queue_answer
from lib.background_tasks import BackgroundTasks

if TYPE_CHECKING:
    import logging

    from services.downloads._state import DownloadsStateBox
    from services.protocols import Clock, EventEmitter

_DOWNLOAD_QUEUE_MAX_TERMINAL = 50

# A download in one of these statuses has run to a terminal end — it is no
# longer active/queued/paused/extracting. The queue prune trims the oldest of
# these over the cap, and "Clear Completed" evicts all of them (#149). In normal
# flow only completed/failed actually LINGER: a cancel is an explicit discard and
# evicts its entry on the spot (#149 downloads-round), so "cancelled" stays in
# this classification defensively (a stray cancelled row is still prune/clearable)
# and as the transient status the terminal cancel FRAME still carries.
_TERMINAL_DOWNLOAD_STATUSES = ("completed", "failed", "cancelled")

_ProgressPhase = Literal["downloading", "extracting"]


@dataclass(frozen=True)
class DownloadQueueConfig:
    """Frozen wiring bundle handed to ``DownloadQueue.__init__``.

    ``state`` is the package's shared state box, whose ``entries`` this queue
    reports; ``loop`` is the one a worker thread marshals each progress tick
    onto; ``clock`` throttles the ticks.
    """

    state: DownloadsStateBox
    loop: asyncio.AbstractEventLoop
    emit: EventEmitter
    clock: Clock
    logger: logging.Logger


class DownloadQueue:
    """The queue's entries and the ``download_progress`` frames that report them.

    A frame sent without being awaited runs as a task of this queue's own,
    which :meth:`shutdown` cancels and waits for.
    """

    def __init__(self, *, config: DownloadQueueConfig) -> None:
        self._state = config.state
        self._loop = config.loop
        self._emit = config.emit
        self._clock = config.clock
        self._logger = config.logger
        self._tasks = BackgroundTasks(logger=config.logger)

    async def shutdown(self) -> None:
        """Cancel the progress frames still being sent, and wait until they have ended."""
        await self._tasks.shutdown()

    def prune(self) -> None:
        """Remove oldest terminal items when over the limit.

        Keeps all non-terminal (queued/downloading/paused/extracting) items.
        Retains up to _DOWNLOAD_QUEUE_MAX_TERMINAL terminal items, removing the
        oldest (by insertion order) when the count exceeds the limit. In practice
        the retained terminals are completed/failed — a cancel evicts its own
        entry immediately (#149 downloads-round), so it rarely reaches the cap.
        """
        terminal_ids = self._terminal_rom_ids()
        excess = len(terminal_ids) - _DOWNLOAD_QUEUE_MAX_TERMINAL
        if excess <= 0:
            return
        # Dict preserves insertion order (Python 3.7+), so the first
        # entries in terminal_ids are the oldest.
        for rid in terminal_ids[:excess]:
            del self._state.entries[rid]

    def _terminal_rom_ids(self) -> list[int]:
        return [rid for rid, item in self._state.entries.items() if item.get("status") in _TERMINAL_DOWNLOAD_STATUSES]

    def make_progress_callback(
        self, rom_id, rom_name, platform_name, file_name, control=None, *, phase: _ProgressPhase = "downloading"
    ):
        """Build a throttled progress callback for one *phase* of a download.

        The callback logs a one-line summary at most every 30s and emits at
        most every 0.5s, except that the final tick (``done >= total``) always
        emits. ``last_emit`` starts at 0.0 so the first extracting tick emits
        immediately and the UI switches to the "extracting" phase promptly once
        the byte transfer finishes.

        *control*, when given, is polled on every tick. The extracting phase is
        handed none: extraction is not cancellable.
        """
        last_emit = [0.0]  # mutable container for closure
        last_log = [0.0]

        def progress_callback(done, total):
            if control is not None and (control.cancelled or control.paused):
                # Abort the in-flight transfer thread (#144). CancelledError is a
                # BaseException, so it propagates untouched through the adapter's
                # Exception-only retry/translate — no retry, no error translation.
                # Both cancel and pause unwind through here; the terminal handling
                # in ``DownloadTransfer.run`` branches on which flag was set.
                raise asyncio.CancelledError()
            now = self._clock.monotonic()
            if now - last_log[0] >= 30.0:
                last_log[0] = now
                self._log_progress(phase, rom_name, done, total)
            if now - last_emit[0] < 0.5 and done < total:
                return
            last_emit[0] = now
            progress = done / total if total else 0

            # This callback runs on a ``run_in_executor`` worker thread. Both the
            # queue-dict mutation and the emit-scheduling must happen on the loop
            # thread, so marshal them across via ``call_soon_threadsafe`` (#973).
            self._loop.call_soon_threadsafe(
                self._apply_progress,
                phase,
                rom_id,
                rom_name,
                platform_name,
                file_name,
                progress,
                done,
                total,
            )

        return progress_callback

    def _log_progress(self, phase: _ProgressPhase, rom_name, done, total):
        """Log a throttled one-line human-readable progress summary (MB + %)."""
        label = "Extract" if phase == "extracting" else "Download"
        mb_done = done / (1024 * 1024)
        mb_total = total / (1024 * 1024) if total else 0
        pct = (done / total * 100) if total else 0
        self._logger.info(f"{label} progress: {rom_name} — {mb_done:.1f}/{mb_total:.1f} MB ({pct:.0f}%)")

    def _live_entry(self, rom_id) -> dict[str, Any] | None:
        # Every applier the worker queues with ``call_soon_threadsafe`` reads the entry
        # here. None for an evicted entry (never resurrected) and for an ended one: a
        # callback queued before the end can run after the terminal frame, because from
        # 3.13 asyncio settles an executor future that finished before the loop chained
        # it without yielding (``asyncio.futures._chain_future``).
        entry = self._state.entries.get(rom_id)
        return None if entry is None or entry.get("status") in _TERMINAL_DOWNLOAD_STATUSES else entry

    def _apply_progress(self, phase: _ProgressPhase, rom_id, rom_name, platform_name, file_name, progress, done, total):
        """Update the live queue entry and schedule a ``download_progress`` emit for *phase*.

        Runs on the loop thread, marshaled from the worker via ``call_soon_threadsafe``;
        an evicted or ended entry is left alone and no frame is sent (``_live_entry``).
        Extracting flips the entry to "extracting" and reports ``resumable: False``,
        as extraction never resumes; downloading keeps the status and its ``resumable``.
        """
        entry = self._live_entry(rom_id)
        if entry is None:
            return
        extracting = phase == "extracting"
        if extracting:
            entry["status"] = "extracting"
        else:
            entry["_transferred"] = done
        entry.update(
            {
                "progress": progress,
                "bytes_downloaded": done,
                "total_bytes": total,
            }
        )
        self.spawn_frame(
            progress_frame(
                rom_id,
                rom_name,
                platform_name,
                file_name,
                status=phase,
                progress=progress,
                bytes_downloaded=done,
                total_bytes=total,
                resumable=False if extracting else entry.get("resumable", False),
            )
        )

    def make_on_meta(self, rom_id, rom_name, platform_name, file_name):
        """Build the one-shot resumability callback the adapter fires when the
        download's response headers arrive (before the body streams).

        It records the server's ``range_supported`` verdict on the queue entry
        and emits a ``download_progress`` frame carrying it, so the frontend can
        flip Pause/Cancel live DURING the transfer instead of only learning the
        verdict at the end. Runs on the executor transfer thread, so it hops back
        to the loop via ``call_soon_threadsafe`` like the progress callback (#973).
        """

        def on_meta(range_supported: bool) -> None:
            def _apply() -> None:
                entry = self._live_entry(rom_id)
                if entry is None:
                    return
                entry["resumable"] = range_supported
                self.spawn_frame(
                    progress_frame(
                        rom_id,
                        rom_name,
                        platform_name,
                        file_name,
                        status=entry.get("status", "downloading"),
                        progress=entry.get("progress", 0),
                        bytes_downloaded=entry.get("bytes_downloaded", 0),
                        total_bytes=entry.get("total_bytes", 0),
                        resumable=range_supported,
                    )
                )

            self._loop.call_soon_threadsafe(_apply)

        return on_meta

    def spawn_frame(self, frame: dict[str, Any]) -> None:
        """Send *frame* as a ``download_progress`` event without awaiting it. Call on the loop thread."""
        self._tasks.spawn(self._emit("download_progress", frame), "download_progress")

    async def report_queued(self, rom_id, rom_name, platform_name, file_name, *, total_bytes) -> None:
        """Mark *rom_id*'s entry queued and report it: its download waits for a transfer slot (#1053)."""
        entry = self._state.entries[rom_id]
        entry["status"] = "queued"
        await self._emit(
            "download_progress",
            progress_frame(
                rom_id,
                rom_name,
                platform_name,
                file_name,
                status="queued",
                progress=0,
                bytes_downloaded=0,
                total_bytes=total_bytes,
                resumable=entry.get("resumable", False),
            ),
        )

    def mark_downloading(self, rom_id: int) -> None:
        """Mark *rom_id*'s entry downloading: its download holds a transfer slot."""
        self._state.entries[rom_id]["status"] = "downloading"

    async def report_paused(self, rom_id, rom_name, platform_name, file_name, target_path) -> None:
        """Mark *rom_id*'s entry paused and report it.

        The entry stays in the queue (:meth:`prune` keeps every entry that has
        not ended). It records *target_path* (``_target_path``, never sent to the
        frontend) so a later cancel of this task-less download can delete the
        partial without fetching the ROM's detail again.
        """
        entry = self._state.entries[rom_id]
        entry["status"] = "paused"
        entry["_target_path"] = target_path
        frame = self._frame_from_entry(rom_id, rom_name, platform_name, file_name, status="paused")
        await self._emit("download_progress", frame)

    def mark_cancelled(self, rom_id: int) -> None:
        """Mark *rom_id*'s entry cancelled, the status its terminal frame carries (:meth:`report_cancelled`)."""
        self._state.entries[rom_id]["status"] = "cancelled"

    async def report_cancelled(self, rom_id, rom_name, platform_name, file_name) -> None:
        """Send *rom_id*'s terminal ``cancelled`` frame, then evict its entry.

        A cancel is an explicit discard, so no "cancelled" row lingers in the
        queue view or the Quick Access summary (#149 downloads-round); the store
        listener drops it on the frame. The frame goes BEFORE the evict so it
        carries the entry's final progress values.
        """
        frame = self._frame_from_entry(rom_id, rom_name, platform_name, file_name, status="cancelled")
        await self._emit("download_progress", frame)
        self.evict(rom_id)

    def cancel_paused(self, rom_id: int, entry: dict[str, Any]) -> None:
        """Send a paused download's terminal ``cancelled`` frame without awaiting it, then evict its entry."""
        self.spawn_frame(cancelled_frame(rom_id, entry))
        self.evict(rom_id)

    def mark_failed(self, rom_id: int, error: str) -> None:
        """Mark *rom_id*'s entry failed with *error*."""
        entry = self._state.entries[rom_id]
        entry["status"] = "failed"
        entry["error"] = error

    def mark_completed(self, rom_id: int) -> dict[str, Any]:
        """Mark *rom_id*'s entry completed and answer it."""
        entry = self._state.entries[rom_id]
        entry["status"] = "completed"
        entry["progress"] = 1.0
        return entry

    def _frame_from_entry(self, rom_id, rom_name, platform_name, file_name, *, status: str) -> dict[str, Any]:
        entry = self._state.entries[rom_id]
        return progress_frame(
            rom_id,
            rom_name,
            platform_name,
            file_name,
            status=status,
            progress=entry.get("progress", 0),
            bytes_downloaded=entry.get("bytes_downloaded", 0),
            total_bytes=entry.get("total_bytes", 0),
            resumable=entry.get("resumable", False),
        )

    def get_download_queue(self) -> dict[str, Any]:
        """The queue's answer: every entry, as the frontend's ``DownloadItem`` shape."""
        return queue_answer(self._state.entries)

    def clear_completed_downloads(self) -> dict[str, Any]:
        """Evict every terminal (completed/failed/cancelled) entry from the queue.

        The frontend "Clear Completed" action calls this so a dismissed download
        stays cleared across a QAM reopen or menu switch (#149): the backend queue
        is the source ``get_download_queue`` re-seeds the frontend from on every
        mount, so a purely-local hide reappeared on the next reopen. Active,
        queued, paused, and extracting entries are left untouched — only terminal
        ones are removed. In normal flow those are completed/failed only, since a
        cancel already evicted its own entry (#149 downloads-round); a stray
        cancelled row would still clear here. Idempotent: an empty queue or one
        with no terminal entries clears nothing and reports ``cleared: 0``. A ROM
        re-downloaded after being cleared re-enters the queue via ``start_download``.
        """
        terminal_ids = self._terminal_rom_ids()
        for rid in terminal_ids:
            del self._state.entries[rid]
        return {"success": True, "cleared": len(terminal_ids)}

    def evict_if_paused(self, rom_id: int) -> None:
        """Drop a superseded sibling's queue entry when it is paused (#1298 S1).

        A paused download whose install a supersede just removed is stale — its
        "paused" row (and partial ``.tmp``) would otherwise linger and could be
        resumed into a second installed version. Only a paused row is evicted; a
        live or terminal row for the sibling is left to its own lifecycle.
        """
        entry = self._state.entries.get(rom_id)
        if entry is not None and entry.get("status") == "paused":
            self.evict(rom_id)

    def evict(self, rom_id: int) -> None:
        """Remove the queue entry for *rom_id* if present. Idempotent."""
        self._state.entries.pop(int(rom_id), None)

    def clear(self) -> None:
        """Remove all entries from the download queue."""
        self._state.entries.clear()
