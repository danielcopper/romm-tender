"""Which version of a sibling group RomM marks as the user's main version.

RomM keeps the pick per user on ``rom_user.is_main_sibling``, written by
``PUT /api/roms/{id}/props``, and states it twice in a single-ROM answer
(``GET /api/roms/{id}/simple``, ``backend/endpoints/responses/rom.py`` at 5.3.0
and 5.4.0): the ROM's own ``rom_user.is_main_sibling``, and each visible
sibling's ``sibling_roms[].is_main_sibling``. Both are booleans there; a user
with no ``rom_user`` row is answered with a placeholder whose flag is false.
"""

from __future__ import annotations

from collections.abc import Mapping


def main_sibling_flag(entry: object) -> bool | None:
    """The main-version flag *entry* states, or ``None`` when it states none.

    *entry* is a ``rom_user`` object or a ``sibling_roms`` stub. Only a boolean
    ``is_main_sibling`` is an answer; an entry that is no mapping, lacks the
    key or holds anything else says nothing, so a caller keeps what it knew.
    """
    if not isinstance(entry, Mapping):
        return None
    flag = entry.get("is_main_sibling")
    return flag if isinstance(flag, bool) else None
