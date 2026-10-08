"""What one SteamGridDB icon fetch for a ROM answered."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IconAnswer(StrEnum):
    """The four answers the icon job acts on differently."""

    ICON = "icon"
    """SteamGridDB had an icon; ``IconFetch.data`` holds it."""
    NO_ICON = "no_icon"
    """No icon on SteamGridDB, or no game for the ROM: the job gives the logo, and stops asking once it is set."""
    RATE_LIMITED = "rate_limited"
    """SteamGridDB answered 429. Wait, then ask again."""
    FAILED = "failed"
    """Anything else — a network failure, a server error, a download that broke. Asked again next run."""


@dataclass(frozen=True)
class IconFetch:
    """One fetch's answer, with the icon's bytes where there was one."""

    answer: IconAnswer
    data: bytes | None = None
