"""The platform incremental skip's rule: what a completion stamp records and what RomM must answer to match it.

A stamp records two things about the fetch that wrote it (#743 D1, D2): when
that fetch started, on Tender's clock, and a checksum of the ROM ids it
returned. The skip asks RomM once per platform for the most recently updated
ROM and the whole visible id set (#743 D3), and skips only when nothing was
updated after the stamp's reference and the id set's checksum is the stamp's.
Which RomM changes move ``updated_at`` and which do not is
docs/architecture/backend-architecture.md, "Incremental skip".
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from domain.iso_time import parse_iso

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from domain.platform_sync_state import PlatformSyncState

# Covers a device clock ahead of the server's and RomM's whole-second
# timestamps (#743 D1); the price is one more full fetch of a platform edited
# within this window before the fetch that stamped it.
SKIP_REFERENCE_MARGIN = timedelta(minutes=10)


def rom_id_checksum(rom_ids: Iterable[int]) -> str:
    """The checksum a stamp records over a fetch's ROM ids: sha256 hex of the sorted distinct ids, comma-joined."""
    joined = ",".join(str(rom_id) for rom_id in sorted(set(rom_ids)))
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


def parse_romm_timestamp(value: object) -> datetime | None:
    """Parse a RomM ``updated_at`` to an aware datetime, or ``None`` when it is not a readable ISO timestamp.

    RomM writes ``updated_at`` from a UTC clock (5.3.0 ``models/base.py``), so
    a value without an offset is read as UTC.
    """
    if not isinstance(value, str):
        return None
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def skip_reference(stamp: PlatformSyncState) -> datetime | None:
    """The instant a ROM updated after makes the stamp's platform changed, or ``None`` when the stamp records none."""
    started = parse_romm_timestamp(stamp.fetch_started_at)
    return started - SKIP_REFERENCE_MARGIN if started is not None else None


def stamp_can_match(stamp: PlatformSyncState) -> bool:
    """Whether *stamp* records both things RomM's answer is compared with; one that does not never skips."""
    return skip_reference(stamp) is not None and bool(stamp.rom_id_checksum)


def _matches(stamp: PlatformSyncState, newest_updated_at: datetime | None, rom_ids: Iterable[int]) -> bool:
    reference = skip_reference(stamp)
    if reference is None or not stamp.rom_id_checksum:
        return False
    if newest_updated_at is not None and newest_updated_at > reference:
        return False
    return rom_id_checksum(rom_ids) == stamp.rom_id_checksum


def probe_says_unchanged(stamp: PlatformSyncState, answer: object) -> bool:
    """Whether RomM's skip-probe answer matches *stamp*: nothing updated after its reference, the same id set.

    *answer* is RomM's ``/api/roms`` list answer for one platform ordered by
    ``updated_at`` descending with ``limit=1``: ``items[0]`` is the most
    recently updated ROM and ``rom_id_index`` every visible id. An answer this
    cannot read — not a mapping, no id index, an id that is not an int, a first
    item without a readable ``updated_at`` — establishes nothing, so it does
    not match. A platform with no ROMs answers no items and an empty index.
    """
    if not isinstance(answer, dict):
        return False
    items: Any = answer.get("items")
    rom_ids: Any = answer.get("rom_id_index")
    if not isinstance(items, list) or not isinstance(rom_ids, list):
        return False
    if any(type(rom_id) is not int for rom_id in rom_ids):
        return False
    newest = None
    if items:
        first: Any = items[0]
        newest = parse_romm_timestamp(first.get("updated_at")) if isinstance(first, dict) else None
        if newest is None:
            return False
    return _matches(stamp, newest, rom_ids)


def restamp_due(stamp: PlatformSyncState | None, fetched_roms: Sequence[Mapping[str, Any]]) -> bool:
    """Whether re-stamping a platform from *fetched_roms*, a full fetch of it, would let its next sync skip.

    True for no stamp (the caller passes ``stamp_for_skip``'s answer, so a
    revoked one too), a stamp with no checksum or no fetch start, a checksum
    other than the fetched ids', and a fetched ROM updated after the stamp's
    reference — every way the skip refuses a stamp that a fresh one would cure.
    A fetched ROM without a readable ``updated_at`` cannot be cleared, so it
    counts as updated.
    """
    if stamp is None:
        return True
    newest: datetime | None = None
    for rom in fetched_roms:
        updated = parse_romm_timestamp(rom.get("updated_at"))
        if updated is None:
            return True
        if newest is None or updated > newest:
            newest = updated
    return not _matches(stamp, newest, (int(rom["id"]) for rom in fetched_roms))
