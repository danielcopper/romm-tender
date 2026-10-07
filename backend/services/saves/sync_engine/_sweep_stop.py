"""What a whole-library save sweep answers when the server stops it partway.

It lives beside the engine rather than inside it because nothing is added to
the engine's own module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.partial_failure import PartialFailure


@dataclass(frozen=True)
class SaveSweepIncomplete(PartialFailure):
    """A sweep stopped by the server's per-device sync switch.

    It carries what the sweep had synced and how many ROMs it had reached when
    it stopped.
    """

    synced: int
    conflicts: int
    conflicts_list: list[dict[str, Any]]
    roms_checked: int
    errors: list[str]
