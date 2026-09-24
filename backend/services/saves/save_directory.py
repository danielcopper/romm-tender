"""Following a game's save files when the directory its emulator answers moves.

The directory a save lives in is the resolver's answer, read live. When that
answer changes — the user flipped one of RetroArch's sort flags, or anything else
moved it — the files already on disk stay where the old answer put them, and the
emulator no longer looks there. This module notices per game and carries them.

It notices by comparison, never by computation: each ROM's
``AnsweredSaveDirectory`` holds the directory the resolver last answered for it,
and a later answer that differs is the whole signal. The recorded directory is
read only as the source of that one move — never where a sync or a probe looks.

**Nothing is overwritten or removed here.** A name present in both directories
is a collision, and the older copy goes through the save-backup funnel. The
record moves on only once every file has arrived, so a move that fails part-way
is picked up again at the next sync.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from domain.answered_save_directory import AnsweredSaveDirectory
from domain.save_answer import ROOT_CONTENT_DIRECTORY, SORTED_DIR_MISSING

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from domain.save_answer import SaveAnswer
    from services.protocols import DebugLogger, SaveFileStore, UnitOfWorkFactory
    from services.saves.rom_info import RomInfoService


class SaveDirectoryFollower:
    """Keeps one game's save files in the directory its emulator answers today.

    Every method is a synchronous worker for ``run_in_executor``. The caller
    holds ``SyncEngine.rom_lock(rom_id)``, so a record's read, the move and its
    write are never interleaved with another follow of the same ROM; no Unit of
    Work is open across any file operation or resolver reading here.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        rom_info: RomInfoService,
        save_file_store: SaveFileStore,
        quarantine: Callable[[str, str], bool],
        logger: logging.Logger,
        log_debug: DebugLogger,
    ) -> None:
        self._uow_factory = uow_factory
        self._rom_info = rom_info
        self._save_file_store = save_file_store
        self._quarantine = quarantine
        self._logger = logger
        self._log_debug = log_debug

    def do_follow(self, rom_id: int, answer: SaveAnswer) -> None:
        """Bring this ROM's save files to *answer*'s directory, then record it.

        An answer that places no directory, or places it beside the content,
        moves nothing and records nothing (:func:`_followable_directory`). No
        record yet: this is the first sight, so the answer is recorded and
        nothing moves. The same directory: nothing to do.
        """
        answered = _followable_directory(answer)
        if answered is None:
            return
        recorded = self._recorded(rom_id)
        if recorded == answered:
            return
        if recorded is not None:
            info = self._rom_info.get_rom_save_info(rom_id, save_answer=answer)
            if info is None:
                return
            if not self._move(rom_id, recorded, answered, answer, info["file_path"]):
                return
        self._record(rom_id, answered)

    def do_record_if_absent(self, rom_id: int) -> None:
        """Record this ROM's answered save directory where nothing is recorded yet.

        The one-time backfill's step: a game whose saves predate the record
        is covered from its first start after this, so a later move of its
        directory is noticed rather than taken for a first sight.
        """
        if not self._rom_info.is_content_installed(rom_id) or self._recorded(rom_id) is not None:
            return
        answered = _followable_directory(self._rom_info.save_answer(rom_id))
        if answered is not None:
            self._record(rom_id, answered)

    def do_rerecord(self, rom_id: int) -> None:
        """Replace this ROM's record with the directory the resolver answers now.

        The home migration's step, once it has moved the files: it owns the new
        location, and the record it replaces names a home the user may have
        chosen to leave files behind in. Where the answer is one the follow
        would not act on, the record is deleted instead — kept, it would still
        name the old home, and a later answer would carry the left-behind copy
        from there; gone, that later answer is a first sight and moves nothing.
        """
        if not self._rom_info.is_content_installed(rom_id):
            return
        answered = _followable_directory(self._rom_info.save_answer(rom_id))
        if answered is None:
            with self._uow_factory() as uow:
                uow.answered_save_directories.delete(rom_id)
            return
        self._record(rom_id, answered)

    def _recorded(self, rom_id: int) -> str | None:
        with self._uow_factory() as uow:
            record = uow.answered_save_directories.get(rom_id)
        return record.directory if record is not None else None

    def _record(self, rom_id: int, directory: str) -> None:
        with self._uow_factory() as uow:
            uow.answered_save_directories.save(AnsweredSaveDirectory.record(rom_id=rom_id, directory=directory))

    def _move(self, rom_id: int, recorded: str, answered: str, answer: SaveAnswer, file_path: str) -> bool:
        """Carry this game's files from *recorded* to *answered*.

        Returns whether every file arrived — only then may the record move on.
        Two spellings of one directory (a symlinked home, a trailing component
        resolved differently) move nothing: a file "colliding" with itself would
        otherwise send the only copy to the backup folder.
        """
        if self._save_file_store.canonical_path(recorded) == self._save_file_store.canonical_path(answered):
            return True
        present = [
            name
            for name in self._names(answer, recorded, file_path)
            if self._save_file_store.is_file(os.path.join(recorded, name))
        ]
        if not present:
            return True
        self._logger.info("Save directory for rom %d moved: %s -> %s; carrying %s", rom_id, recorded, answered, present)
        try:
            # Created here, or RetroArch may later revert to the unsorted root
            # and not look where the carried files are (see SORTED_DIR_MISSING).
            if SORTED_DIR_MISSING in answer.caveats or not self._save_file_store.is_dir(answered):
                self._save_file_store.make_dirs(answered)
        except OSError as e:
            self._logger.error("Could not create save directory %s: %s", answered, e)
            return False
        arrived = True
        for name in present:
            arrived = self._carry(recorded, answered, name) and arrived
        return arrived

    def _carry(self, recorded: str, answered: str, name: str) -> bool:
        """Move one file, backing up the older copy where both directories hold the name."""
        source = os.path.join(recorded, name)
        target = os.path.join(answered, name)
        try:
            if self._save_file_store.exists(target) and not self._save_file_store.is_file(target):
                # A folder or a link under the save's name: the backup funnel
                # moves regular files only, and a move onto a folder would put
                # the save inside it.
                self._logger.error("Not carrying %s: %s is in the way and is not a file", source, target)
                return False
            if self._save_file_store.exists(target):
                # Nothing is overwritten and nothing removed: the older copy goes
                # to the backup folder of the directory it sits in. A tie keeps
                # the copy already where the emulator looks.
                if self._save_file_store.get_mtime(target) >= self._save_file_store.get_mtime(source):
                    self._quarantine(recorded, name)
                    self._logger.info("Kept newer %s; backed up the older %s", target, source)
                    return True
                self._quarantine(answered, name)
                self._logger.info("Backed up the older %s before carrying %s", target, source)
            self._save_file_store.move(source, target)
        except (OSError, ValueError) as e:
            self._logger.error("Could not carry %s to %s: %s", source, answered, e)
            return False
        return True

    def _names(self, answer: SaveAnswer, recorded: str, file_path: str) -> list[str]:
        """Which files in *recorded* this game's move has to carry.

        **A move is not a sync.** The answer may refuse to say what a save
        consists of, but the files are already on the user's disk and the move
        only relocates them — leaving one behind where the emulator will not
        look is worse than moving one this plugin would never upload. So:

        - The answer names files: carry exactly those, configuration included.
          Moving a Saturn ``.bkr`` and leaving its ``.smpc`` behind would split
          one save across two directories.
        - The answer refuses: carry whatever *recorded* holds under this ROM's
          name, anchored on ``<stem>.`` so ``Sonic`` does not drag ``Sonic 2``'s
          files along. Never in the content's own directory, where ``<stem>.*``
          is the game itself and its disc images.
        """
        if answer.syncable:
            return [component.name for component in answer.owned_files]
        if recorded == os.path.dirname(file_path):
            self._log_debug(f"save directory move: not carrying files by name out of the content directory {recorded}")
            return []
        prefix = f"{os.path.splitext(os.path.basename(file_path))[0]}."
        found = sorted(name for name in self._save_file_store.listdir(recorded) if name.startswith(prefix))
        if found:
            self._logger.info(
                "The emulator answers %s, so carrying the %d file(s) named after the game in %s: %s",
                answer.state,
                len(found),
                recorded,
                found,
            )
        return found


def _followable_directory(answer: SaveAnswer) -> str | None:
    """The directory a follow compares and carries into, or ``None`` where it may do neither.

    ``None`` where the answer places no directory, and where it anchors the save
    in the content's own directory — beside the content file or inside it: for
    a multi-file game that is the game's own folder, which an uninstall removes
    whole, so nothing is carried into it or recorded for it — the old record
    stays, and switching the option back finds it unchanged. The root is read
    here rather than :attr:`SaveAnswer.in_content_directory`, which leaves out a
    save inside the content file.
    """
    if answer.root_kind == ROOT_CONTENT_DIRECTORY:
        # Holds until the content-directory gate is lifted.
        return None
    return answer.directory
