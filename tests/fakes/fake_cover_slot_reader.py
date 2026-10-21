"""In-memory ``CoverSlotReader`` for the SteamGridDB service's tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.errors import Refused


class FakeCoverSlotReader:
    """Answers the covers named in ``set_in_steam`` as set in Steam, and records every RomM cover reload.

    ``refusal`` makes the reload raise it instead.
    """

    def __init__(self) -> None:
        self.set_in_steam: set[int] = set()
        self.reloaded: list[int] = []
        self.refusal: Refused | None = None

    def is_cover_set_in_steam(self, rom_id: int, app_id: int) -> bool:
        return rom_id in self.set_in_steam

    async def refresh_cover_unchecked(self, rom_id: int) -> dict[str, Any]:
        if self.refusal is not None:
            raise self.refusal
        self.reloaded.append(rom_id)
        return {"success": True, "message": "Cover refreshed", "cover_path": f"/covers/{rom_id}.png"}
