"""The refusal a per-ROM sync entry point performs before it syncs.

Two things refuse a sync outright, and both are read off one live answer: six
of the seven save states (:mod:`domain.save_answer`), and a save the emulator
writes beside the game's content. The three per-ROM entry points all handle
them identically: take one live reading of the machine, follow a moved save
directory with it, and where either holds, raise the refusal instead of
syncing — a benign skip, except for a missing saves root. The slot setup asks
the same refusal before it sets a slot up, and the whole-library sweep, which
passes a refused ROM over rather than raising, counts it by that refusal's
reason (:class:`SweepTally`).

It lives beside the engine rather than inside it because nothing is added to the
engine's own module, and because these are the whole refusal: one reading and the
refusals it raises, with no engine state between them.

**The reading happens before the heartbeat, deliberately.** A PS2 game needs no
server to establish that its saves live on a shared card, and a device that is
offline should be told the honest thing rather than "server unreachable".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from domain.save_answer import (
    BENIGN_SYNC_SKIP_REASONS,
    SAVE_STATE_SAVES_ROOT_MISSING,
    SAVE_SYNC_CONTENT_DIR_REASON,
    save_finding_details,
    save_shape_message,
    saves_folder_missing,
)
from lib.errors import Refused
from services.saves._messages import SAVE_SYNC_IN_CONTENT_DIR
from services.saves._refusals import SavefilesInContentDir, shape_refusal

if TYPE_CHECKING:
    from domain.save_answer import SaveAnswer
    from services.saves._sync_stop import SyncStop
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
    and the next sync, and a granularity read before that change would have
    Tender carry a shared card as though it belonged to one game. Within a
    single operation the caller hands this same reading to the consumers that
    would otherwise take their own — the directory follow, the matrix, and the
    negotiate session's inventory on a confirmed ROM — rather than taking a
    second: live is a property of operations, not of layers.
    """
    if not rom_info.is_content_installed(rom_id):
        return None
    return rom_info.save_answer(rom_id)


def refuse_unsyncable(answer: SaveAnswer | None) -> None:
    """Raise the refusal *answer* refuses a single-ROM sync with; return to sync.

    A save beside the content is checked first: its files may be a perfectly
    syncable per-game set, so the shape would let it through. No answer at all
    returns — an uninstalled ROM is the runner's own case. A save beside the
    content and an unsyncable shape are benign skips: nothing went wrong. A
    missing saves root is a failure, ``saves_root_missing``: the game's saves
    are not synced until the folder is back. What a reader is told of each is
    ``docs/architecture/save-sync-coverage.md``'s.
    """
    if answer is None:
        return
    if answer.in_content_directory:
        raise SavefilesInContentDir(SAVE_SYNC_IN_CONTENT_DIR, synced=0)
    if answer.state == SAVE_STATE_SAVES_ROOT_MISSING:
        raise Refused("saves_root_missing", save_shape_message(answer), **save_finding_details(answer), synced=0)
    if not answer.syncable:
        raise shape_refusal(answer, synced=0)


@dataclass
class SweepTally:
    """What a whole-library sweep compared with RomM, and what it passed over and why.

    The refusal is per ROM, so the sweep passes a ROM it refuses over inside
    its run — and its one result still has to say so, because it is the
    sentence a user who asked for a full sync reads. A ROM is passed over by
    the reason a sync of it would be refused with, and only a ROM no refusal
    holds back and whose slot is not confirmed counts as not set up.
    ``compared`` counts the ROMs whose saves were compared with RomM,
    ``answered`` the ROMs the sweep took a reading for.
    """

    compared: int = 0
    answered: int = 0
    not_set_up: int = 0
    cannot_sync: int = 0
    beside_content: int = 0
    missing_roots: dict[str | None, int] = field(default_factory=dict)

    def passes_over(self, answer: SaveAnswer | None) -> bool:
        """Record one ROM's reading; whether a sync of it would be refused, counted by the refusal's reason."""
        if answer is None:
            return False
        self.answered += 1
        try:
            refuse_unsyncable(answer)
        except Refused as refusal:
            if refusal.reason == SAVE_SYNC_CONTENT_DIR_REASON:
                self.beside_content += 1
            elif refusal.reason in BENIGN_SYNC_SKIP_REASONS:
                self.cannot_sync += 1
            else:
                # The one refusal that is a failure: a missing saves root.
                root = answer.missing_saves_root
                self.missing_roots[root] = self.missing_roots.get(root, 0) + 1
            return True
        return False

    def count_unconfirmed(self, answer: SaveAnswer | None) -> None:
        """Record a ROM whose slot is not confirmed: by its refusal where it has one, otherwise as not set up."""
        if answer is not None and not self.passes_over(answer):
            self.not_set_up += 1

    def count_compared(self, stop: SyncStop | None) -> None:
        """Record a ROM whose saves were compared with RomM, unless its *stop* ended the comparison."""
        if stop is None or not stop.stopped_the_sync():
            self.compared += 1

    @property
    def failed(self) -> bool:
        """Whether a ROM was passed over for a reason that fails the sweep, as it fails a single ROM's sync."""
        return bool(self.missing_roots)

    def refuse_if_all_beside_content(self, *, roms_checked: int) -> None:
        """Raise the content-directory skip when every ROM the sweep read saves beside its content.

        The same reason and message a single-ROM sync raises, with the sweep's
        own counts beside them.
        """
        if self.beside_content and self.beside_content == self.answered:
            raise SavefilesInContentDir(SAVE_SYNC_IN_CONTENT_DIR, synced=0, conflicts=0, roms_checked=roms_checked)

    def annotate(self, message: str) -> str:
        """*message*, with one clause per reason the sweep passed ROMs over for, each with its count."""
        clauses = []
        if self.not_set_up:
            clauses.append(f"{self.not_set_up} game(s) not set up for save sync")
        if self.cannot_sync:
            clauses.append(f"{self.cannot_sync} game(s) skipped — their saves cannot be synced")
        clauses.extend(
            f"{count} game(s) skipped — {saves_folder_missing(root)}" for root, count in self.missing_roots.items()
        )
        if self.beside_content:
            clauses.append(f"{self.beside_content} game(s) skipped — their saves are written beside the game file")
        return "; ".join([message, *clauses])
