"""The collection incremental skip's rule: what a collection's completion stamp records and what RomM must answer.

The collection follows the platform skip's rules (#2477 D1): a stamp records
when the completing apply's fetch started, on Tender's clock, and a checksum of
the ``rom_ids`` the collection listing carried (D2, D4). The skip needs the
listing to match the stamp — the same checksum, and for a standard or smart
collection the same ``updated_at`` — and then a scoped ``updated_after`` probe
keyed off the fetch start less the platform skip's margin to answer no ROM. What
RomM does with a hidden member is docs/architecture/backend-architecture.md,
"Incremental skip".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.platform_skip import rom_id_checksum, skip_reference

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable
    from datetime import datetime

    from domain.collection_sync_state import CollectionSyncState


@dataclass(frozen=True)
class CollectionVerdict:
    """Whether RomM's answer matches a collection's stamp, what decided it, and whether a fresh stamp settles a refusal.

    ``restamp_settles`` is false where nothing was refused, and where the
    refusal is one a stamp the apply writes would not settle: an answer that
    could not be read.
    """

    unchanged: bool
    reason: str
    restamp_settles: bool = False


@dataclass(frozen=True)
class CollectionSkip:
    """The collection skip's answer for one unit (#2477 D3).

    ``member_rom_ids`` are the stamp's members the skip replays, or ``None`` when
    the collection is fetched. ``restamp_settles`` is whether a stamp the apply
    writes settles the refusal; the preview counts a collection for re-stamping
    from it, never by judging the stamp again.
    """

    member_rom_ids: list[int] | None
    restamp_settles: bool = False


_UNREADABLE = CollectionVerdict(unchanged=False, reason="RomM's answer could not be read")


def recorded_updated_at(collection_kind: str, listing_updated_at: str | None) -> str | None:
    """The ``updated_at`` a collection's completion stamp records, or ``None`` when its apply writes no stamp.

    A virtual collection's ``updated_at`` says nothing, so its stamp records an
    empty one. A standard or smart collection whose listing carried none is
    never stamped: the skip could not compare it.
    """
    if collection_kind == "virtual":
        return ""
    return listing_updated_at or None


def apply_stamps(collection_kind: str | None, listing_updated_at: str | None, member_rom_ids: Collection[int]) -> bool:
    """Whether an apply of the collection writes it a stamp: it has members and a recordable ``updated_at``."""
    return (
        bool(member_rom_ids)
        and collection_kind is not None
        and recorded_updated_at(collection_kind, listing_updated_at) is not None
    )


def membership_verdict(
    stamp: CollectionSyncState, *, listing_updated_at: str | None, listing_rom_ids: Iterable[int]
) -> CollectionVerdict:
    """Judge the collection listing against *stamp*: unchanged when it lists the stamp's ids and ``updated_at``.

    A stamp without a fetch start or a checksum — every stamp written before
    migration 032 — never matches. A virtual collection's ``updated_at`` is not
    compared. A fresh stamp settles every refusal here.
    """
    if skip_reference(stamp) is None or not stamp.listing_rom_id_checksum:
        return CollectionVerdict(
            unchanged=False, reason="the stamp records no fetch start or checksum", restamp_settles=True
        )
    if stamp.collection_kind != "virtual" and stamp.updated_at != listing_updated_at:
        return CollectionVerdict(
            unchanged=False,
            reason=f"collection updated_at changed ({stamp.updated_at!r} -> {listing_updated_at!r})",
            restamp_settles=True,
        )
    distinct = set(listing_rom_ids)
    same_ids = rom_id_checksum(distinct) == stamp.listing_rom_id_checksum
    noun = "listed id" if len(distinct) == 1 else "listed ids"
    return CollectionVerdict(
        unchanged=same_ids,
        reason=f"{len(distinct)} {noun}, checksum {'matches' if same_ids else 'differs from'} the stamp's",
        restamp_settles=not same_ids,
    )


def probe_verdict(answer: object, reference: datetime) -> CollectionVerdict:
    """Judge RomM's scoped ``updated_after`` probe answer: unchanged when it holds no ROM updated after *reference*.

    *answer* is RomM's ``/api/roms`` list answer for the collection with
    ``updated_after`` set to *reference* and ``limit=1``; its ``total`` counts
    the members updated after it. An answer that is not a JSON object, or whose
    ``total`` is not an integer, establishes nothing, so it does not match, and
    a fresh stamp would not settle it. A fresh stamp settles a member updated
    after *reference* only once the edit is older than the margin at the Apply's
    fetch start; one inside it is counted again on the next preview.
    """
    if not isinstance(answer, dict):
        return _UNREADABLE
    total: Any = answer.get("total")
    if type(total) is not int:
        return _UNREADABLE
    after = f"after the reference {reference.isoformat()}"
    if total:
        return CollectionVerdict(unchanged=False, reason=f"{total} member(s) updated {after}", restamp_settles=True)
    return CollectionVerdict(unchanged=True, reason=f"no member updated {after}")
