"""Which ROMs the user hid in RomM, read off RomM's answer.

RomM has two kinds of hidden. A ROM an admin hid for a user or group
(``hidden_entities``) is answered 404 and listed nowhere, so nothing tells it
from a deleted one. A ROM the user hid themselves (``rom_user.hidden``) is left
out of every list but still answered by its exact id. ``GET
/api/roms?statuses=hidden`` lists only those, and its ``rom_id_index`` names
every one of them (5.3.0 ``handler/database/roms_handler.py``). What Tender
makes of such a ROM is docs/architecture/backend-architecture.md, "A game the
user hid in RomM".
"""

from __future__ import annotations

from typing import Any


def hidden_rom_ids(answer: object) -> frozenset[int] | None:
    """The id of every ROM *answer* names as hidden by the user; ``None`` when it cannot be read.

    *answer* is RomM's ``/api/roms?statuses=hidden`` list answer. An answer
    that is not a mapping, has no id index, or holds an id that is not an int
    establishes nothing, so nothing is taken for hidden.
    """
    if not isinstance(answer, dict):
        return None
    rom_ids: Any = answer.get("rom_id_index")
    if not isinstance(rom_ids, list) or any(type(rom_id) is not int for rom_id in rom_ids):
        return None
    return frozenset(rom_ids)
