"""Shared mutable state of the download package.

Built once by the composition root and handed to the façade, the queue and the
transfer, which coordinate through it instead of through each other's fields.
A start takes the ROM's in-progress claim (:meth:`DownloadsStateBox.claim`), and
an admitted download then holds four registrations here — its queue entry, its
task, its byte reservation and its control token
(:meth:`DownloadsStateBox.admit`). Its task gives back the claim and every
registration but the entry, which outlives it (:meth:`DownloadsStateBox.release`),
so the rule that only the attempt which registered a download releases it lives
in one place. The claim, the task, the reservation and the token change only
through the box's verbs; the entries are the queue's to change once admitted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from domain.disk_space import bytes_reserved_by_others


class DownloadControl:
    """Per-download cooperative-control flags. Set on the event-loop thread by
    ``cancel_download`` / ``pause_download``; polled on the executor worker
    thread by the progress callback, which raises ``CancelledError`` to abort
    the in-flight HTTP transfer when EITHER flag is set (#144).

    ``cancelled`` and ``paused`` differ only in the terminal handling: a cancel
    deletes the partial ``.tmp``; a pause keeps it so the transfer can resume
    from where it stopped. The abort mechanism (raise to unwind the executor
    transfer) is identical.

    Plain bools — not ``threading.Event`` — because the import-linter
    ``no-stdlib-io-in-services`` contract forbids ``threading`` in services, and
    under the GIL a one-way set-once bool flip needs no synchronisation.
    """

    __slots__ = ("cancelled", "paused")

    def __init__(self) -> None:
        self.cancelled = False
        self.paused = False


def _download_slots() -> asyncio.Semaphore:
    # At most two ROMs transfer at once; the rest wait as "queued" in FIFO order.
    return asyncio.Semaphore(2)


@dataclass
class DownloadsStateBox:
    """The downloads this process knows about, and what each has claimed.

    ``entries`` is the queue the frontend is shown, by rom id. ``in_progress``
    holds every rom id whose download has started and not ended — a paused one
    is not in it. ``entries``, ``tasks``, ``reserved_bytes`` and
    ``control_tokens`` hold the registrations of an admitted download;
    ``semaphore`` bounds how many transfer at once.
    """

    entries: dict[int, dict[str, Any]] = field(default_factory=dict)
    in_progress: set[int] = field(default_factory=set)
    tasks: dict[int, asyncio.Task[None]] = field(default_factory=dict)
    reserved_bytes: dict[int, int] = field(default_factory=dict)
    control_tokens: dict[int, DownloadControl] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(default_factory=_download_slots)

    def claim(self, rom_id: int) -> None:
        """Take *rom_id*'s in-progress claim: its start has begun."""
        self.in_progress.add(rom_id)

    def drop_claim(self, rom_id: int) -> None:
        """Give back *rom_id*'s in-progress claim: its start ended without admitting a download."""
        self.in_progress.discard(rom_id)

    def admit(
        self,
        rom_id: int,
        *,
        entry: dict[str, Any],
        task: asyncio.Task[None],
        reserved: int,
        control: DownloadControl,
    ) -> None:
        """Register an admitted download: its queue entry, its task, its byte reservation and its control token."""
        self.entries[rom_id] = entry
        self.tasks[rom_id] = task
        self.reserved_bytes[rom_id] = reserved
        self.control_tokens[rom_id] = control

    def release(self, rom_id: int, control: DownloadControl) -> None:
        """Give back *rom_id*'s in-progress claim and its registrations, when *control* is still the token registered.

        A re-download or a resume can register a fresh attempt before an older,
        superseded task ends; gating every release on the token's identity keeps
        that task from releasing the newer attempt's task, claim, reservation or
        token. The queue entry is not released here: it outlives its task.
        """
        if self.control_tokens.get(rom_id) is control:
            self.tasks.pop(rom_id, None)
            self.in_progress.discard(rom_id)
            self.reserved_bytes.pop(rom_id, None)
            del self.control_tokens[rom_id]

    def forget_tasks(self) -> None:
        """Forget every download's task: the shutdown has cancelled them and waited until each ended."""
        self.tasks.clear()

    def bytes_reserved_by_others(self, rom_id: int) -> int:
        """What other downloads have claimed and not yet written. Read on the loop, where both change."""
        return bytes_reserved_by_others(self.reserved_bytes, self.entries, rom_id)
