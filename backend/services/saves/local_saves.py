"""What this device holds of a game's saves, read without RomM.

The SAVES tab's read while save sync is off: nothing here registers the
device, lists a save on RomM or writes a save state, so it answers with no
server configured at all. Which files a game's save consists of is the save
answer's, read live, and a game whose saves no sync would carry answers that
refusal instead of files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.iso_time import epoch_to_iso
from services.saves.sync_engine._shape_refusal import sync_refusal

if TYPE_CHECKING:
    import asyncio

    from domain.save_answer import SaveAnswer
    from services.protocols import SaveFileStore
    from services.saves.rom_info import RomInfoService
    from services.saves.sync_engine import SyncEngine


@dataclass(frozen=True)
class LocalSaveFilesConfig:
    """Frozen wiring bundle handed to ``LocalSaveFiles.__init__``.

    Holds the peer save sub-services (rom_info, whose save answer names the
    files, and sync_engine, whose ``rom_lock`` the read holds and whose
    directory follow it calls), the save-file store the sizes and modification
    times are read from, and the backend's event loop.
    """

    rom_info: RomInfoService
    sync_engine: SyncEngine
    save_file_store: SaveFileStore
    loop: asyncio.AbstractEventLoop


class LocalSaveFiles:
    """This device's save files for one game, or the refusal its sync would raise, read without RomM."""

    def __init__(self, *, config: LocalSaveFilesConfig) -> None:
        self._rom_info = config.rom_info
        self._sync_engine = config.sync_engine
        self._save_file_store = config.save_file_store
        self._loop = config.loop

    async def get_local_save_files(self, rom_id: int) -> dict[str, Any]:
        """This device's save files for *rom_id*, or the refusal a sync of it is refused with.

        ``available`` is whether a sync would carry the game's saves, worded as
        ``get_save_sync_availability`` words it: where it would not, the
        refusal's ``reason`` and ``message``, and the finding, source kind and
        emulator label of a refusal that carries them, stand beside it and
        ``files`` is empty. A game that is not installed is asked
        about the path it would be downloaded to and lists no files;
        ``installed`` says which of the two was asked. Each file carries its
        ``filename``, the ``directory`` it lies in, its ``size`` in bytes and
        its ``modified_at`` as an ISO-8601 time.

        One live reading of the save answer, under the ROM's ``rom_lock``. A
        moved save directory is followed before the files are looked for, as
        the sync's paths do; the follow does nothing while save sync is off, so
        there the files listed are those in the directory the emulator names
        now, and saves left in a directory recorded before a sort change are
        not listed.
        """
        rom_id = int(rom_id)
        async with self._sync_engine.rom_lock(rom_id):
            answer = await self._loop.run_in_executor(None, self._rom_info.save_answer, rom_id)
            base = {"available": True, "installed": answer.content_installed}
            refusal = sync_refusal(answer)
            if refusal is not None:
                return {**base, "available": False, "files": [], **refusal}
            if not answer.content_installed:
                return {**base, "files": []}
            await self._sync_engine.follow_save_directory(rom_id, answer)
            files = await self._loop.run_in_executor(None, self._files_io, rom_id, answer)
        return {**base, "files": files}

    def _files_io(self, rom_id: int, answer: SaveAnswer) -> list[dict[str, Any]]:
        return [
            {
                "filename": found["filename"],
                "directory": os.path.dirname(found["path"]),
                "size": self._save_file_store.get_size(found["path"]),
                "modified_at": epoch_to_iso(self._save_file_store.get_mtime(found["path"])),
            }
            for found in self._rom_info.find_save_files(rom_id, save_answer=answer)
        ]
