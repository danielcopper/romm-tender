"""What one SteamGridDB icon fetch for a ROM answered."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IconAnswer(StrEnum):
    """The five answers the icon job acts on differently."""

    ICON = "icon"
    """SteamGridDB had an icon; ``IconFetch.data`` holds it, downscaled."""
    NO_ICON = "no_icon"
    """No icon on SteamGridDB, or no game for the ROM: the job gives the logo, and stops asking once it is set."""
    RATE_LIMITED = "rate_limited"
    """SteamGridDB answered 429. Wait, then ask again."""
    KEY_REFUSED = "key_refused"
    """SteamGridDB answered 401 or 403: the API key is not accepted, so the run ends."""
    FAILED = "failed"
    """Anything else — a network failure, a server error, a download that broke. Asked again next run."""


@dataclass(frozen=True)
class IconFetch:
    """One fetch's answer, with the icon's bytes where there was one."""

    answer: IconAnswer
    data: bytes | None = None
