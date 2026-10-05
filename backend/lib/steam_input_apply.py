"""What an attempt to write the Steam Input mode into Steam's ``localconfig.vdf`` came to.

Cross-cutting enum shared by the adapter that edits ``localconfig.vdf``
(``adapters/steam_config.py``) and the Protocol services depend on
(``services/protocols/transport.py``). It lives in ``lib/`` because both
layers import it and ``import-linter`` forbids the adapter→service and
service→adapter directions. What the user is told about each outcome is the
service's decision, not the adapter's.
"""

from __future__ import annotations

from enum import StrEnum


class SteamInputApply(StrEnum):
    """The outcome of one attempt to apply a Steam Input mode."""

    APPLIED = "applied"
    """Every shortcut named now carries the mode, whether it was written now or already so."""

    NO_STEAM_USER = "no_steam_user"
    """No Steam user folder was found under Steam's ``userdata``. Nothing was written."""

    NO_LOCALCONFIG = "no_localconfig"
    """The Steam user has no ``config/localconfig.vdf``. Nothing was written."""

    UNREADABLE = "unreadable"
    """``localconfig.vdf`` is there but could not be opened or parsed. Nothing was written."""

    WRITE_FAILED = "write_failed"
    """The mode needed writing and the new ``localconfig.vdf`` could not be written."""
