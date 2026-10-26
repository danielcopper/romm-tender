"""Whether what a download needs still fits in what the card has left.

Pure arithmetic over numbers the caller measures — the size the server states,
the free space it read, the bytes sibling downloads have already claimed, and
the bytes a resumable transfer already wrote. Reading them is the download
service's concern; what they add up to, and the two figures a refusal shows the
user, are decided here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

# Headroom a download must leave unclaimed. An emulator writes saves, states and
# shader caches beside the ROM it just fetched, so a transfer that fills the card
# to its last byte breaks the thing it was fetched for.
_HEADROOM_BYTES = 100 * 1024 * 1024

_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class DiskSpaceVerdict:
    """Whether a download fits, what it claims while it runs, and what a refusal says.

    *needed_bytes* is the figure the caller reserves against concurrent
    pre-flights, so what a download claims is exactly what it was weighed
    against. The megabyte figures are what the refusal is read in, derived from
    the same numbers as *fits* — a refusal can never quote a free space that
    would have passed.
    """

    fits: bool
    needed_bytes: int
    free_mb: int

    @property
    def needed_mb(self) -> int:
        """*needed_bytes* in the unit the refusal states it in."""
        return self.needed_bytes // _BYTES_PER_MB


def disk_space_verdict(
    *,
    file_size: int,
    free_space: int,
    reserved_bytes: int,
    multi_file: bool,
    already_on_disk: int = 0,
    unpacked_size: int = 0,
) -> DiskSpaceVerdict:
    """Decide whether *file_size* still fits once every other claim is counted.

    A multi-file ROM is counted **twice**: it arrives as a ZIP and is extracted
    beside it, so both exist at once. A single zip that will be unpacked is
    counted as itself plus *unpacked_size*, the sum of the member sizes RomM
    lists, for the same reason. *reserved_bytes* is what sibling downloads
    have claimed but not yet written — without it two concurrent pre-flights
    could each pass on space only one of them fits (#1053). *already_on_disk* is
    what a resumable transfer already wrote, which is not needed a second time,
    so a near-complete resume is not refused for the full size.

    A ROM whose size the server did not state (*file_size* of 0) always fits:
    there is nothing to weigh, and refusing on an absent number would be a claim
    Tender cannot make.
    """
    arriving = file_size * 2 if multi_file else file_size + unpacked_size
    needed = max(arriving + _HEADROOM_BYTES - already_on_disk, 0)
    available = free_space - reserved_bytes
    return DiskSpaceVerdict(
        fits=not file_size or available >= needed,
        needed_bytes=needed,
        free_mb=max(available, 0) // _BYTES_PER_MB,
    )


def unpack_space_verdict(*, unpacked_size: int, free_space: int, reserved_bytes: int) -> DiskSpaceVerdict:
    """Decide whether a downloaded zip's contents still fit beside it.

    *unpacked_size* is the uncompressed total the zip's own central directory
    states, weighed by the rule the check before the download uses: free space
    less what other downloads have claimed and not yet written, with the same
    headroom left over.
    """
    needed = unpacked_size + _HEADROOM_BYTES
    available = free_space - reserved_bytes
    return DiskSpaceVerdict(
        fits=available >= needed,
        needed_bytes=needed,
        free_mb=max(available, 0) // _BYTES_PER_MB,
    )


def bytes_reserved_by_others(
    reserved_bytes: Mapping[int, int], entries: Mapping[int, Mapping[str, Any]], rom_id: int
) -> int:
    """What downloads other than *rom_id*'s have claimed and not yet written, which free space does not lack yet.

    *reserved_bytes* is each admitted download's claim and *entries* the queue's
    entries, from which :func:`written_since_admitted` reads what each has written.
    """
    return sum(
        max(reserved - written_since_admitted(entries.get(other) or {}), 0)
        for other, reserved in reserved_bytes.items()
        if other != rom_id
    )


def written_since_admitted(entry: Mapping[str, Any]) -> int:
    """Bytes a download has put on disk since admitted: its transfer, and what it has unpacked so far."""
    transferred = max(entry.get("_transferred", 0) - entry.get("_on_disk_at_start", 0), 0)
    unpacked = entry.get("bytes_downloaded", 0) if entry.get("status") == "extracting" else 0
    return transferred + unpacked


class NotEnoughSpaceToUnpack(Exception):
    """A completed download whose contents do not fit on its drive.

    Distinct from every other failure because of what it keeps: the downloaded
    file stays where it is, so a retry once space is freed unpacks it without
    fetching it again.
    """

    def __init__(self, verdict: DiskSpaceVerdict) -> None:
        super().__init__(f"Not enough space to unpack ({verdict.free_mb}MB free, need {verdict.needed_mb}MB)")
