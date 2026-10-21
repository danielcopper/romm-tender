"""SteamGridDB artwork: the API key, the artwork cache, the game a ROM's artwork comes from, and each slot's image.

The package's public API is the :class:`SteamGridService` façade; its modules
share the artwork cache and its index of what the cache holds.
"""

from services.steamgrid.service import SteamGridService, SteamGridServiceConfig

__all__ = ["SteamGridService", "SteamGridServiceConfig"]
