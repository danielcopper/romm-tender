"""CollectionSyncState — the per-collection "this collection fully synced" stamp.

The collection sibling of :class:`domain.platform_sync_state.PlatformSyncState`
(ADR-0023): recorded when a collection work unit finishes its **last**
apply chunk, so the incremental-skip gate can avoid re-paginating an unchanged
collection on the next sync. Keyed by ``(collection_id, collection_kind)`` — a
standard collection id and a smart collection id can collide (both are small ints on
the server), so the kind is part of the identity.

Unlike a platform, a collection has no local membership column to reconstruct its
member set from (``roms.platform_slug`` is per-platform; collection membership
lives only on the server). So the stamp additionally stores ``member_rom_ids`` —
the collection's full member set as of completion — which a skipped run replays
into the run's ``synced_rom_ids`` and Steam-collection membership map, resolving
each id to its shortcut through the registry (the same sibling-group fallback the
reporter uses at finalize).

The skip compares RomM's answer with the stamp (``domain/collection_skip.py``):
for a standard or smart collection the listing's ``updated_at``, for every kind
a checksum of the listing's ``rom_ids`` (``listing_rom_id_checksum``), and a
scoped ``updated_after`` probe keyed off ``fetch_started_at`` less the platform
skip's margin. ``member_rom_ids`` is not what the checksum covers: it adds the
ids a walk answered beyond the listing.

All three kinds carry a stamp; the skip never reads a virtual collection's
``updated_at``, so it may be empty. A thin record built whole and upserted —
never a partial field mutation — so it carries a single ``stamp`` constructor and
no verb-named mutators. Unlike a platform stamp it has no revoked state and is
deleted outright; which flows delete it, and why, is in
docs/architecture/backend-architecture.md, "Incremental skip".
"""

from __future__ import annotations

from domain._aggregate import cosmic_aggregate


@cosmic_aggregate
class CollectionSyncState:
    """One collection's last fully-completed sync, keyed by ``(collection_id, collection_kind)``."""

    collection_id: str
    collection_kind: str
    updated_at: str
    completed_at: str
    rom_count: int
    member_rom_ids: tuple[int, ...]
    fetch_started_at: str | None = None
    listing_rom_id_checksum: str | None = None

    @classmethod
    def stamp(
        cls,
        *,
        collection_id: str,
        collection_kind: str,
        updated_at: str,
        completed_at: str,
        rom_count: int,
        member_rom_ids: tuple[int, ...],
        fetch_started_at: str | None = None,
        listing_rom_id_checksum: str | None = None,
    ) -> CollectionSyncState:
        """Record that a collection fully synced with the given member set.

        ``updated_at`` is the collection's server ``updated_at`` at completion —
        the membership-stable signal the skip re-checks — and is required for a
        standard or smart collection. ``completed_at`` is our own ISO time the
        final chunk committed; the skip does not read it. ``rom_count`` is the
        server's collection ROM count as of completion, and ``member_rom_ids`` is
        the full member set the skip replays to rebuild collection membership
        without re-fetching. ``collection_kind`` is ``standard``, ``smart`` or
        ``virtual``.

        ``fetch_started_at`` (ISO, Tender's clock) and ``listing_rom_id_checksum``
        (``domain/platform_skip.rom_id_checksum`` over the listing's ``rom_ids``)
        are what the skip compares RomM's answer with; a stamp without both never
        skips.
        """
        if not collection_id:
            raise ValueError("collection_id is required")
        if collection_kind not in ("standard", "smart", "virtual"):
            raise ValueError(f"collection_kind must be 'standard', 'smart' or 'virtual', got {collection_kind!r}")
        if collection_kind != "virtual" and not updated_at:
            raise ValueError("updated_at is required")
        if not completed_at:
            raise ValueError("completed_at is required")
        if rom_count < 0:
            raise ValueError("rom_count must be non-negative")
        return cls(
            collection_id=collection_id,
            collection_kind=collection_kind,
            updated_at=updated_at,
            completed_at=completed_at,
            rom_count=rom_count,
            member_rom_ids=tuple(member_rom_ids),
            fetch_started_at=fetch_started_at,
            listing_rom_id_checksum=listing_rom_id_checksum,
        )
