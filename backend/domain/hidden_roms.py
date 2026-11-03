"""Which ROMs the user hid in RomM, read off RomM's answer.

``GET /api/roms?statuses=hidden`` lists the ROMs the user hid themselves
(``rom_user.hidden``), and its ``rom_id_index`` names every one of them (5.3.0
``handler/database/roms_handler.py``); a ROM an admin hid is not among them.
What Tender makes of such a ROM is docs/architecture/backend-architecture.md,
"A game the user hid in RomM".
"""

from __future__ import annotations

from typing import Any


def hidden_rom_ids(answer: object) -> frozenset[int] | None:
    """The id of every ROM *answer* names as hidden by the user; ``None`` when it cannot be read.

    *answer* is RomM's ``/api/roms?statuses=hidden`` list answer. An answer
    that is not a mapping, has no item list or no id index, or holds an id that
    is not an int establishes nothing, so nothing is taken for hidden. Neither
    does one whose first item is not marked hidden: a RomM that ignored
    ``statuses`` would answer every ROM, and only that item shows which list
    came back.
    """
    if not isinstance(answer, dict):
        return None
    items: Any = answer.get("items")
    if not isinstance(items, list) or (items and not _marked_hidden(items[0])):
        return None
    rom_ids: Any = answer.get("rom_id_index")
    if not isinstance(rom_ids, list) or any(type(rom_id) is not int for rom_id in rom_ids):
        return None
    return frozenset(rom_ids)


def _marked_hidden(item: object) -> bool:
    """Whether a list item carries ``rom_user.hidden`` as true."""
    rom_user: Any = item.get("rom_user") if isinstance(item, dict) else None
    return isinstance(rom_user, dict) and rom_user.get("hidden") is True
