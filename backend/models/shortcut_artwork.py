"""What one SteamGridDB fetch for a ROM's wide grid, logo or icon answered, and an icon Tender wrote for a shortcut."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SlotAnswer(StrEnum):
    """The five answers the artwork job acts on differently for a wide grid, a logo or an icon."""

    READY = "ready"
    """The artwork cache holds the image: the job writes an icon itself, and the frontend fetches a wide grid or logo
    through ``get_sgdb_slot_artwork``."""
    NONE = "none"
    """SteamGridDB has no candidate, does not know the game, or the ROM has no SteamGridDB id; recorded."""
    RATE_LIMITED = "rate_limited"
    """SteamGridDB answered 429. Wait, then ask again."""
    KEY_REFUSED = "key_refused"
    """SteamGridDB answered 401 or 403: the API key is not accepted, so the run ends."""
    FAILED = "failed"
    """Anything else. Nothing is recorded, so the next run asks again."""


@dataclass(frozen=True)
class WrittenIcon:
    """An icon the artwork job or the game page wrote into Steam's grid directory, and what its ``set`` record carries.

    *sha256* is the hash of the bytes written, which are the downscaled ones;
    *sgdb_id* is the ROM's SteamGridDB id when the icon was written.
    """

    path: str
    image_id: int
    sha256: str
    sgdb_id: int | None
