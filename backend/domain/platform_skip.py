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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from domain.iso_time import parse_iso

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from domain.collection_sync_state import CollectionSyncState
    from domain.platform_sync_state import PlatformSyncState

# #743 D1; what it covers and what it costs is docs/architecture/backend-architecture.md, "Incremental skip".
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


def skip_reference(stamp: PlatformSyncState | CollectionSyncState) -> datetime | None:
    """The stamp's fetch start less the margin, or ``None`` without one: a ROM updated after it means a change."""
    started = parse_romm_timestamp(stamp.fetch_started_at)
    return started - SKIP_REFERENCE_MARGIN if started is not None else None


def stamp_can_match(stamp: PlatformSyncState) -> bool:
    """Whether *stamp* records both things RomM's answer is compared with; one that does not never skips."""
    return skip_reference(stamp) is not None and bool(stamp.rom_id_checksum)


@dataclass(frozen=True)
class ProbeVerdict:
    """Whether RomM's answer matches a stamp, and what decided it, worded for the sync log."""

    unchanged: bool
    reason: str


_UNREADABLE = ProbeVerdict(unchanged=False, reason="RomM's answer could not be read")


def _compare(stamp: PlatformSyncState, newest_updated_at: datetime | None, rom_ids: Iterable[int]) -> ProbeVerdict:
    reference = skip_reference(stamp)
    if reference is None or not stamp.rom_id_checksum:
        return ProbeVerdict(unchanged=False, reason="the stamp records no fetch start or checksum")
    newer = newest_updated_at is not None and newest_updated_at > reference
    if newest_updated_at is None:
        updated = "no visible ROMs"
    else:
        relation = "is after" if newer else "is not after"
        updated = f"newest updated_at {newest_updated_at.isoformat()} {relation} the reference {reference.isoformat()}"
    distinct = set(rom_ids)
    same_ids = rom_id_checksum(distinct) == stamp.rom_id_checksum
    noun = "visible id" if len(distinct) == 1 else "visible ids"
    ids = f"{len(distinct)} {noun}, checksum {'matches' if same_ids else 'differs from'} the stamp's"
    return ProbeVerdict(unchanged=same_ids and not newer, reason=f"{updated}; {ids}")


def probe_verdict(stamp: PlatformSyncState, answer: object) -> ProbeVerdict:
    """Judge RomM's skip-probe answer against *stamp*: unchanged when nothing updated after its reference, same ids.

    *answer* is RomM's ``/api/roms`` list answer for one platform ordered by
    ``updated_at`` descending with ``limit=1``: ``items[0]`` is the most
    recently updated ROM and ``rom_id_index`` every visible id. An answer this
    cannot read — not a mapping, no id index, an id that is not an int, a first
    item without a readable ``updated_at`` — establishes nothing, so it does
    not match. A platform with no ROMs answers no items and an empty index.
    """
    if not isinstance(answer, dict):
        return _UNREADABLE
    items: Any = answer.get("items")
    rom_ids: Any = answer.get("rom_id_index")
    if not isinstance(items, list) or not isinstance(rom_ids, list):
        return _UNREADABLE
    if any(type(rom_id) is not int for rom_id in rom_ids):
        return _UNREADABLE
    newest = None
    if items:
        first: Any = items[0]
        newest = parse_romm_timestamp(first.get("updated_at")) if isinstance(first, dict) else None
        if newest is None:
            return _UNREADABLE
    return _compare(stamp, newest, rom_ids)


def restamp_due(stamp: PlatformSyncState | None, fetched_roms: Sequence[Mapping[str, Any]]) -> bool:
    """Whether *stamp* fails to match *fetched_roms*, a full fetch of its platform, so only a fresh stamp lets it skip.

    True for no stamp (the caller passes ``stamp_for_skip``'s answer, so a
    revoked one too), a stamp with no checksum or no fetch start, a checksum
    other than the fetched ids', and a fetched ROM updated after the stamp's
    reference — every way the skip refuses a stamp that a fresh one would cure.
    A fetched ROM without a readable ``updated_at`` cannot be cleared, so it
    counts as updated, and one without an int ``id`` cannot be checked against
    the checksum, so it counts as another id set.
    """
    if stamp is None:
        return True
    newest: datetime | None = None
    rom_ids: list[int] = []
    for rom in fetched_roms:
        updated = parse_romm_timestamp(rom.get("updated_at"))
        rom_id = rom.get("id")
        if updated is None or type(rom_id) is not int:
            return True
        rom_ids.append(rom_id)
        if newest is None or updated > newest:
            newest = updated
    return not _compare(stamp, newest, rom_ids).unchanged
