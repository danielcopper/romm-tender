"""ArtworkSlotHandover — the image Tender last handed to Steam for one of a ROM's slots, before Steam answered.

Steam's answer, ``report_sgdb_slot_artwork``, may never arrive: the apply can be
cancelled or overtaken after the image went out, or the report can be lost.
Until it arrives, a grid file holding these bytes is Tender's as much as one
holding the bytes its ``set`` outcome names (#2381 D15). A ``set`` report makes
the outcome name the image and drops this record; a ``refused`` one drops it.
Kept apart from :class:`domain.artwork_slot_outcome.ArtworkSlotOutcome`, whose
record is built whole per outcome, because a hand-over exists beside any
outcome and beside none.

Only the slots judged by the hash of their file carry one: the wide grid, the
logo, the hero and the cover. The icon is judged by its path. A thin record
built whole and upserted, so it carries no verb-named mutators.
"""

from __future__ import annotations

from domain._aggregate import cosmic_aggregate
from domain.artwork_slot import ArtworkSlot

# The slots whose image is told apart by the hash of its file in Steam's grid directory.
HANDED_SLOTS: frozenset[ArtworkSlot] = frozenset(
    {ArtworkSlot.WIDE_GRID, ArtworkSlot.LOGO, ArtworkSlot.HERO, ArtworkSlot.COVER}
)


@cosmic_aggregate
class ArtworkSlotHandover:
    """SteamGridDB's image *image_id*, whose bytes hash to *image_sha256*, handed to Steam at *handed_at*."""

    rom_id: int
    slot: ArtworkSlot
    image_id: int
    image_sha256: str
    handed_at: float

    @classmethod
    def hand_over(
        cls, *, rom_id: int, slot: ArtworkSlot, image_id: int, image_sha256: str, handed_at: float
    ) -> ArtworkSlotHandover:
        """Record that the image was handed to Steam.

        Raises ``ValueError`` for a slot not judged by its file's hash, or an empty hash.
        """
        if slot not in HANDED_SLOTS:
            raise ValueError(f"no hand-over is recorded for the {slot.value} slot")
        if not image_sha256:
            raise ValueError("a handed-over image carries its hash")
        return cls(rom_id=rom_id, slot=slot, image_id=image_id, image_sha256=image_sha256, handed_at=handed_at)
