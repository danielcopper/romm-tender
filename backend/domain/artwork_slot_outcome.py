"""ArtworkSlotOutcome — what Tender last found for one ROM's wide grid, logo, hero or icon, or set as its cover.

``set`` (an image was set, with SteamGridDB's image id and a hash of the bytes),
``none`` (SteamGridDB has no candidate) or ``refused`` (Steam's
``SetCustomArtworkForApp``, or ``SetShortcutIcon`` for the icon, failed on the
image), the ROM's SteamGridDB id at that time, and when. Keyed by
``(rom_id, slot)`` — by ROM, not app id, because a re-created shortcut gets a
new app id.

A thin record built whole and upserted, so it carries one constructor per
outcome and no verb-named mutators.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from domain._aggregate import cosmic_aggregate
from domain.artwork_slot import RECORDED_SLOTS, SET_RECORDED_SLOTS, ArtworkSlot, is_slot_filled
from domain.shortcut_icon import is_icon_filled, shows_no_icon

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

# How long a ``none`` or ``refused`` outcome keeps its slot from being asked
# again; why a week is "What was found is recorded" in
# docs/architecture/steam-non-steam-shortcuts.md.
RULED_OUT_SECONDS = 7 * 24 * 60 * 60


class SlotOutcome(StrEnum):
    """What was found for a slot."""

    SET = "set"
    NONE = "none"
    REFUSED = "refused"


@cosmic_aggregate
class ArtworkSlotOutcome:
    """What was last found for one ROM's wide grid, logo, hero or shortcut icon."""

    rom_id: int
    slot: ArtworkSlot
    outcome: SlotOutcome
    sgdb_id: int | None
    recorded_at: float
    image_id: int | None = None
    image_sha256: str | None = None

    @classmethod
    def record_set(
        cls,
        *,
        rom_id: int,
        slot: ArtworkSlot,
        sgdb_id: int | None,
        image_id: int,
        image_sha256: str,
        recorded_at: float,
    ) -> ArtworkSlotOutcome:
        """Record that SteamGridDB's image *image_id*, whose bytes hash to *image_sha256*, was set.

        Raises ``ValueError`` for a slot no set image is recorded for or an empty hash.
        """
        _require_recorded(slot, SET_RECORDED_SLOTS)
        if not image_sha256:
            raise ValueError("a set image carries its hash")
        return cls(
            rom_id=rom_id,
            slot=slot,
            outcome=SlotOutcome.SET,
            sgdb_id=sgdb_id,
            recorded_at=recorded_at,
            image_id=image_id,
            image_sha256=image_sha256,
        )

    @classmethod
    def record_none(
        cls, *, rom_id: int, slot: ArtworkSlot, sgdb_id: int | None, recorded_at: float
    ) -> ArtworkSlotOutcome:
        """Record that SteamGridDB has no candidate — or the ROM no SteamGridDB id, when *sgdb_id* is ``None``.

        Raises ``ValueError`` for a slot that is not recorded.
        """
        _require_recorded(slot)
        return cls(rom_id=rom_id, slot=slot, outcome=SlotOutcome.NONE, sgdb_id=sgdb_id, recorded_at=recorded_at)

    @classmethod
    def record_refused(
        cls, *, rom_id: int, slot: ArtworkSlot, sgdb_id: int | None, recorded_at: float
    ) -> ArtworkSlotOutcome:
        """Record that Steam refused the image set for the slot.

        Raises ``ValueError`` for a slot that is not recorded.
        """
        _require_recorded(slot)
        return cls(rom_id=rom_id, slot=slot, outcome=SlotOutcome.REFUSED, sgdb_id=sgdb_id, recorded_at=recorded_at)

    def rules_out(self, *, sgdb_id: int | None, now: float) -> bool:
        """Whether this outcome keeps the slot from being asked again at *now*.

        Only ``none`` and ``refused`` do, for ``RULED_OUT_SECONDS``, and only
        while *sgdb_id*, the ROM's id now, equals the id recorded: an id that
        changed ends it at once. A ``set`` slot whose file is gone is asked
        again.
        """
        if self.outcome is SlotOutcome.SET or self.sgdb_id != sgdb_id:
            return False
        return now - self.recorded_at < RULED_OUT_SECONDS


def slot_waits(
    file_names: Iterable[str],
    app_id: int,
    slot: ArtworkSlot,
    recorded: ArtworkSlotOutcome | None,
    *,
    sgdb_id: int | None,
    now: float,
) -> bool:
    """Whether the shortcut artwork job asks for *app_id*'s wide grid or logo at *now*.

    It does while no file in Steam's grid directory fills the slot
    (:func:`domain.artwork_slot.is_slot_filled`) and *recorded*, the ROM's
    outcome for it, does not rule it out for *sgdb_id*, the ROM's SteamGridDB id
    now.
    """
    if is_slot_filled(file_names, app_id, slot):
        return False
    return _not_ruled_out(recorded, sgdb_id=sgdb_id, now=now)


def icon_waits(
    icon: str,
    recorded: ArtworkSlotOutcome | None,
    *,
    grid_dir: str,
    file_names: Collection[str] | None,
    sgdb_id: int | None,
    now: float,
) -> bool:
    """Whether a shortcut's icon is asked for at *now*.

    It is while *icon*, the shortcut's icon path in ``shortcuts.vdf``, does
    not fill the slot (:func:`domain.shortcut_icon.is_icon_filled`, over
    *grid_dir* and its listing *file_names*) and *recorded*, the ROM's icon
    outcome, does not rule it out for *sgdb_id*, the ROM's SteamGridDB id now.
    """
    if is_icon_filled(icon, grid_dir, file_names):
        return False
    return _not_ruled_out(recorded, sgdb_id=sgdb_id, now=now)


def logo_icon_waits(icon: str, recorded: ArtworkSlotOutcome | None, *, sgdb_id: int | None, now: float) -> bool:
    """Whether the shortcut artwork job gives a shortcut Tender's logo icon at *now*, without asking SteamGridDB.

    It does while *icon* shows no icon — empty or the placeholder — and
    *recorded*, the ROM's icon outcome, is a ``none`` that still rules the slot
    out for *sgdb_id*: the week rules out asking again, not showing the logo, so
    a logo icon that never reached Steam is given at the next run.
    """
    if not shows_no_icon(icon) or recorded is None or recorded.outcome is not SlotOutcome.NONE:
        return False
    return recorded.rules_out(sgdb_id=sgdb_id, now=now)


def _not_ruled_out(recorded: ArtworkSlotOutcome | None, *, sgdb_id: int | None, now: float) -> bool:
    return recorded is None or not recorded.rules_out(sgdb_id=sgdb_id, now=now)


def _require_recorded(slot: ArtworkSlot, recorded: frozenset[ArtworkSlot] = RECORDED_SLOTS) -> None:
    if slot not in recorded:
        raise ValueError(f"no outcome is recorded for the {slot.value} slot")
