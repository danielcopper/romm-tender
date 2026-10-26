"""SyncProgress — the ``sync_progress`` frames a run reports through, and the status read.

Every frame is written to the state box before it is emitted, so
:meth:`SyncProgress.get_sync_status` answers the latest one to a panel that
missed the event. Every part of the sync that narrates a run is handed
:class:`SyncProgress`, or its :meth:`SyncProgress.emit_progress`; the run's CANCELLED and ERROR frames are
built by their own methods, because they carry no ``subStage`` and take their
counters from the run's last frame (CANCELLED) or from nowhere (ERROR) rather
than from a caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.sync_stage import SyncStage

if TYPE_CHECKING:
    import logging
    from collections.abc import Coroutine

    from services.library._state import LibrarySyncStateBox
    from services.protocols import EventEmitter


@dataclass(frozen=True)
class SyncProgressConfig:
    """Frozen wiring bundle handed to ``SyncProgress.__init__``.

    The event emitter the frames go out through, the logger a cancelled run's
    message is written to, and the shared :class:`LibrarySyncStateBox` each
    frame is written to and the run's id and kind are read from.
    """

    emit: EventEmitter
    logger: logging.Logger
    sync_state_box: LibrarySyncStateBox


class SyncProgress:
    def __init__(self, *, config: SyncProgressConfig) -> None:
        self._emit = config.emit
        self._logger = config.logger
        self._sync_state = config.sync_state_box

    async def emit_progress(
        self, stage, current=0, total=0, message="", running=True, step=0, total_steps=0, sub_stage=""
    ):
        """Persist the progress snapshot and emit the sync_progress event.

        ``stage`` is a :class:`SyncStage` (or its string value); ``step``
        / ``total_steps`` are the coarse unit index / total units that
        drive the determinate main bar — stages without a unit index yet
        (discovering, fetching) pass ``0`` / ``0``, which the frontend
        treats as indeterminate. ``current`` / ``total`` are the fine
        within-unit counters. ``sub_stage`` discriminates the ``fetching``
        stage's phases — ``"fetch"`` (paginated ROM listing) vs ``"covers"``
        (cover download/refresh) — so the frontend can fill each phase's own
        monotonic sub-slice of the running unit's width (#1407); it is empty
        for every other frame. It rides the payload as the camelCase
        ``subStage`` key, matching the other multi-word snapshot keys
        (``totalSteps`` / ``runId``); the Python parameter stays snake_case.
        ``runKind`` rides the same way and takes no parameter at all: it is
        claimed with the run slot and read off the box here, so every frame of a
        run carries it without a call site being able to forget one. The
        snapshot is written to the box first so :meth:`get_sync_status` always
        returns the latest state even if the event never reaches a freshly
        remounted QAM — both keys therefore ride the event AND the remount
        re-seed, which is what lets a QAM reloaded mid-run learn the kind of the
        run it is watching rather than waiting for the next event.
        """
        self._sync_state.sync_progress = {
            "running": running,
            "stage": SyncStage(stage).value,
            "current": current,
            "total": total,
            "message": message,
            "step": step,
            "totalSteps": total_steps,
            "subStage": sub_stage,
            "runId": str(self._sync_state.current_sync_id or ""),
            "runKind": self._sync_state.run_kind_value(),
        }
        await self._emit("sync_progress", self._sync_state.sync_progress)

    def get_sync_status(self) -> dict[str, Any]:
        """Return the latest progress frame plus whether a run is actually in flight.

        Idle returns the default ``running: False`` snapshot; a live run
        returns the latest snapshot written by :meth:`emit_progress`.

        ``inFlight`` is the run-lifecycle state itself, not a re-reading of the
        frame, and it rides only this answer — never an emitted ``sync_progress``
        event. The two can disagree, legitimately: during a cancel drain the
        CANCELLED frame already says ``running: False`` while the slot is still
        owned, and between ``try_begin_run`` and the run's first frame the
        opposite holds. It exists because the frontend cannot tell "the backend
        has no run" from "the backend has not said anything about this run yet"
        by looking at a frame — and the difference decides whether a panel may
        retract a run it believes is live. See
        ``frontend/src/bigpicture/MainPage.tsx``.
        """
        box = self._sync_state
        return {**box.sync_progress, "inFlight": box.is_in_flight()}

    async def emit_cancelled(self, message):
        """Emit the terminal CANCELLED progress snapshot for the in-flight run.

        Emission only — the IDLE/None reset of the run-lifecycle pair is owned
        by the caller's ``finally: box.finish_run(run_id)`` so every run has a
        single, run-scoped termination point (#1202). Three neighbouring names
        end a run and none of them can stand in for another: this one tells the
        panel, ``LibrarySyncStateBox.finish_run`` releases the slot the next
        Sync press needs, and
        :meth:`SyncRunRecorder.do_mark_cancelled` writes the row the QAM's
        last-attempt line reads (``get_latest_terminal``). Note which row that
        is: the preview's baseline comes from ``get_latest_completed``, which a
        cancelled row is not and never becomes — the run loop's zero-unit
        carve-out exists because a **completed** row resets that baseline.
        """
        box = self._sync_state
        box.sync_progress = {
            "running": False,
            "stage": SyncStage.CANCELLED.value,
            "current": box.sync_progress.get("current", 0),
            "total": box.sync_progress.get("total", 0),
            "message": message,
            "step": box.sync_progress.get("step", 0),
            "totalSteps": box.sync_progress.get("totalSteps", 0),
            "runId": str(box.current_sync_id or ""),
            "runKind": box.run_kind_value(),
        }
        await self._emit("sync_progress", box.sync_progress)
        self._logger.info(message)

    def write_error_frame(self, message: str) -> Coroutine[Any, Any, bool]:
        """Write the run's terminal ERROR frame to the box now, and answer its emit for the caller to run.

        The write and the emit are apart because the orchestrator sends this
        frame as a task of its own, and it is the one part of the sync that
        starts tasks.
        """
        box = self._sync_state
        box.sync_progress = {
            "running": False,
            "stage": SyncStage.ERROR.value,
            "current": 0,
            "total": 0,
            "message": message,
            "step": 0,
            "totalSteps": 0,
            "runId": str(box.current_sync_id or ""),
            "runKind": box.run_kind_value(),
        }
        return self._emit("sync_progress", box.sync_progress)
