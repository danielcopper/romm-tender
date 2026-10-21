"""ArtworkSlotChoice — the SteamGridDB image a user chose for one of a ROM's artwork slots.

A slot with no choice is automatic: Tender picks its image by its own rule.
Keyed by ``(rom_id, slot)`` like the slot's outcome, and kept apart from it,
because Refresh Artwork, the artwork job and a version switch rewrite or delete
outcomes. Nothing reads a choice yet.

A thin record built whole and upserted, so it carries no verb-named mutators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain._aggregate import cosmic_aggregate

if TYPE_CHECKING:
    from domain.artwork_slot import ArtworkSlot


@cosmic_aggregate
class ArtworkSlotChoice:
    """The SteamGridDB image *image_id* a user chose for one ROM's *slot*, at *chosen_at* (Unix-epoch seconds)."""

    rom_id: int
    slot: ArtworkSlot
    image_id: int
    chosen_at: float
