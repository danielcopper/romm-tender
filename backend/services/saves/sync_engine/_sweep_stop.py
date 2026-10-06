"""What a whole-library save sweep answers when the server stops it partway.

It lives beside the engine rather than inside it because the engine is already
at its decomposition ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.partial_failure import PartialFailure


@dataclass(frozen=True)
class SaveSweepIncomplete(PartialFailure):
    """A sweep stopped by the server's per-device sync switch, with the totals of the ROMs before the stop."""

    synced: int
    conflicts: int
    conflicts_list: list[dict[str, Any]]
    roms_checked: int
    errors: list[str]
