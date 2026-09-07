"""The save-shape refusal a per-ROM sync entry point performs before it does anything else.

Four of the five save states refuse the sync (:mod:`domain.save_answer`), and
the three per-ROM entry points all handle that refusal identically: take one
live reading of the machine, and if the emulator keeps no per-game save file set
this plugin can carry, return the benign-skip shape instead of syncing.

It lives beside the engine rather than inside it because the engine is already at
its decomposition ceiling, and because these two are the whole refusal: one
reading and one result shape, with no engine state between them.

**The reading happens before the heartbeat, deliberately.** A PS2 game needs no
server to establish that its saves live on a shared card, and a device that is
offline should be told the honest thing rather than "server unreachable".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.save_answer import save_shape_message
from services.saves._messages import SAVE_SHAPE_UNSUPPORTED

if TYPE_CHECKING:
    from domain.save_answer import SaveAnswer
    from services.saves.rom_info import RomInfoService


def live_save_answer(rom_info: RomInfoService, rom_id: int) -> SaveAnswer | None:
    """This ROM's save answer, read live, or ``None`` when it is not installed.

    An uninstalled ROM answers ``None`` rather than a refusal: not installed is
    a fact about the disk, not a statement about any emulator's save shape, and
    the runner's own not-installed branch owns that case. Without this the
    launch path would report the emulator as unsupported for a game that is
    simply not there.

    Read live on every call and never remembered ACROSS operations — the user
    changes a core's options in the emulator's own quick menu between one launch
    and the next sync, and a granularity read before that change would have the
    plugin carry a shared card as though it belonged to one game. Within a
    single operation the caller hands this same reading down to the sync rather
    than taking a second: live is a property of operations, not of layers.
    """
    if rom_info.get_rom_save_info(rom_id) is None:
        return None
    return rom_info.save_answer(rom_id)


def save_shape_skip(answer: SaveAnswer) -> dict[str, Any]:
    """The benign-skip result for a save this plugin cannot carry per game.

    The same shape the ``savefiles_in_content_dir`` skip returns, and for the
    same reason — nothing went wrong, and the game still launches — carrying its
    own ``reason`` slug so a caller can tell the two skips apart and say which
    one it was.

    Single-ROM only: a whole-library sweep passes a refusing ROM over inside the
    run and reports its own totals, so it never returns this.
    """
    return {
        "success": False,
        "reason": SAVE_SHAPE_UNSUPPORTED,
        "message": save_shape_message(answer),
        "synced": 0,
        "errors": [],
        "conflicts": [],
    }
