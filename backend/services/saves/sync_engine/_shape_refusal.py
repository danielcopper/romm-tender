"""The refusal a per-ROM sync entry point performs before it does anything else.

Two things refuse a sync outright, and both are read off one live answer: four
of the five save states (:mod:`domain.save_answer`), and a save the emulator
writes beside the game's content. The three per-ROM entry points all handle
them identically: take one live reading of the machine, and where either holds,
return the benign-skip shape instead of syncing.

It lives beside the engine rather than inside it because the engine is already at
its decomposition ceiling, and because these are the whole refusal: one reading
and its result shapes, with no engine state between them.

**The reading happens before the heartbeat, deliberately.** A PS2 game needs no
server to establish that its saves live on a shared card, and a device that is
offline should be told the honest thing rather than "server unreachable".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.save_answer import save_shape_message
from services.saves._messages import (
    SAVE_SHAPE_UNSUPPORTED,
    SAVE_SYNC_IN_CONTENT_DIR,
    SAVE_SYNC_IN_CONTENT_DIR_REASON,
)

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
    single operation the caller hands this same reading to the consumers that
    would otherwise take their own — the directory follow, the matrix, and the
    negotiate session's inventory on a confirmed ROM — rather than taking a
    second: live is a property of operations, not of layers.
    """
    if not rom_info.is_content_installed(rom_id):
        return None
    return rom_info.save_answer(rom_id)


def sync_refusal(answer: SaveAnswer | None) -> dict[str, Any] | None:
    """The benign-skip result *answer* refuses a single-ROM sync with, or ``None`` to sync.

    A save beside the content is checked first: its files may be a perfectly
    syncable per-game set, so the shape would let it through. ``None`` for no
    answer at all — an uninstalled ROM is the runner's own case.
    """
    if answer is None:
        return None
    if answer.in_content_directory:
        return content_dir_skip()
    if not answer.syncable:
        return save_shape_skip(answer)
    return None


def content_dir_skip() -> dict[str, Any]:
    """The benign-skip result for a save the emulator writes beside the game's content.

    Carries ``success: False`` + the ``savefiles_in_content_dir`` reason slug the
    frontend routes on (treat as skip, no error, launch proceeds) alongside
    zero/empty counts.
    """
    return {
        "success": False,
        "reason": SAVE_SYNC_IN_CONTENT_DIR_REASON,
        "message": SAVE_SYNC_IN_CONTENT_DIR,
        "synced": 0,
        "errors": [],
        "conflicts": [],
    }


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


@dataclass
class ContentDirTally:
    """What a whole-library sweep saw of saves written beside the content.

    The gate is per ROM, so the sweep passes such a ROM over inside its run —
    and its one result still has to say so, because it is the sentence a user
    who asked for a full sync reads. ``answered`` counts the ROMs the sweep
    took a reading for (those whose slot the user confirmed, or the one it asks
    about when none is), ``beside_content`` how many of them the gate held back.
    """

    answered: int = 0
    beside_content: int = 0

    def count(self, answer: SaveAnswer | None) -> None:
        """Record one ROM's reading."""
        if answer is None:
            return
        self.answered += 1
        if answer.in_content_directory:
            self.beside_content += 1

    def sweep_skip(self, *, roms_checked: int) -> dict[str, Any] | None:
        """The skip the sweep returns when every ROM it read saves beside its content, else ``None``.

        The same reason slug and message a single-ROM sync returns, in the
        sweep's own result shape.
        """
        if not self.beside_content or self.beside_content != self.answered:
            return None
        return {
            **content_dir_skip(),
            "conflicts": 0,
            "conflicts_list": [],
            "roms_checked": roms_checked,
        }

    def annotate(self, message: str) -> str:
        """*message*, with the count of ROMs held back where some were and others synced."""
        if not self.beside_content:
            return message
        return f"{message}; {self.beside_content} game(s) skipped — {SAVE_SYNC_IN_CONTENT_DIR}"
