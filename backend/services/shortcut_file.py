"""ShortcutFileService — what Steam's shortcut file holds for a shortcut.

Answers the frontend's confirm of a launch-options write when Steam's own
reports have not shown it: the file Steam rewrites from memory is a
second witness to a write that already happened. Read only — the frontend owns
every shortcut mutation — and each call reads the file afresh; nothing read here
is kept for the next call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio

    from services.protocols import SteamConfigStore


@dataclass(frozen=True)
class ShortcutFileServiceConfig:
    """Frozen wiring bundle handed to ``ShortcutFileService.__init__``."""

    steam_config: SteamConfigStore
    loop: asyncio.AbstractEventLoop


class ShortcutFileService:
    """Reads shortcut fields off Steam's ``shortcuts.vdf``."""

    def __init__(self, *, config: ShortcutFileServiceConfig) -> None:
        self._steam_config = config.steam_config
        self._loop = config.loop

    async def get_shortcut_launch_options(self, app_ids: list[int | str]) -> dict[str, Any]:
        """Report the launch options the file holds for *app_ids*.

        Returns a discriminated status union (``.claude/rules/endpoints.md``):
        ``{"status": "read", "launch_options": [{"app_id", "launch_options"}]}``
        with an entry for each requested app ID the file holds and none for one
        it does not, or ``{"status": "unreadable", "message"}`` when the file
        could not be read. An entry of *app_ids* that is not a number is
        dropped, as it can name no shortcut.
        """
        wanted: set[int] = set()
        for raw in app_ids:
            try:
                wanted.add(int(raw))
            except (TypeError, ValueError):
                continue
        held = await self._loop.run_in_executor(None, self._steam_config.read_shortcut_launch_options)
        if held is None:
            return {"status": "unreadable", "message": "Steam's shortcut file could not be read."}
        return {
            "status": "read",
            "launch_options": [
                {"app_id": app_id, "launch_options": value} for app_id, value in held.items() if app_id in wanted
            ],
        }
