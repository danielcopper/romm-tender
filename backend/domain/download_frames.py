"""The payloads a download sends the frontend — pure projections, no I/O.

What a ``download_progress`` frame, the ``download_failed`` payload and the
download queue's answer carry is a wire contract rather than an implementation
detail of whoever emits them: the frontend store reads every one of them by its
keys, and a terminal frame is the last thing it hears about a ROM. Anything that
builds one belongs here; deciding *when* to send one is the download queue's
and the transfer's concern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def progress_frame(
    rom_id: int,
    rom_name: str,
    platform_name: str,
    file_name: str,
    *,
    status: str,
    progress: float,
    bytes_downloaded: int,
    total_bytes: int,
    resumable: bool,
) -> dict[str, Any]:
    """Build a ``download_progress`` payload: the download it is about, and where it stands."""
    return {
        "rom_id": rom_id,
        "rom_name": rom_name,
        "platform_name": platform_name,
        "file_name": file_name,
        "status": status,
        "progress": progress,
        "bytes_downloaded": bytes_downloaded,
        "total_bytes": total_bytes,
        "resumable": resumable,
    }


def failed_frame(rom_id: int, rom_name: str, platform_name: str, error_message: str) -> dict[str, Any]:
    """Build the ``download_failed`` payload for a download that never landed.

    Named rather than assembled at each raise site: every failure the frontend
    is told about carries the same four keys, whether it was refused before a
    byte moved or died mid-transfer.
    """
    return {
        "rom_id": rom_id,
        "rom_name": rom_name,
        "platform_name": platform_name,
        "error_message": error_message,
    }


def cancelled_frame(rom_id: int, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Build the terminal ``cancelled`` ``download_progress`` payload from a queue entry."""
    return progress_frame(
        rom_id,
        entry.get("rom_name", ""),
        entry.get("platform_name", ""),
        entry.get("file_name", ""),
        status="cancelled",
        progress=entry.get("progress", 0),
        bytes_downloaded=entry.get("bytes_downloaded", 0),
        total_bytes=entry.get("total_bytes", 0),
        resumable=entry.get("resumable", False),
    )


def queue_answer(entries: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """Build the download queue's answer from its entries.

    An entry's ``_``-prefixed keys (``_target_path``, kept on a paused entry for
    the cleanup of a cancel) are the backend's own and never cross the wire to
    the frontend's ``DownloadItem`` shape.
    """
    return {"downloads": [{k: v for k, v in entry.items() if not k.startswith("_")} for entry in entries.values()]}
