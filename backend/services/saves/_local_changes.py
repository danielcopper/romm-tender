"""Which local save files changed since their last sync — the saves sub-services' one comparison."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from domain.rom_save_sync_state import FileSyncState
    from services.protocols import SaveFileStore


def changed_since_last_sync(
    local_files: list[dict[str, str]],
    files_state: Mapping[str, FileSyncState],
    save_file_store: SaveFileStore,
) -> Iterator[str]:
    """Yield the filename of each local save file whose content differs from its last sync.

    *local_files* is the ``[{"path", "filename"}]`` list a save-file discovery
    answers; *files_state* is the ROM's per-file sync state
    (``RomSaveSyncState.files``). Each file is hashed with the store's
    zip-aware ``content_hash`` — the scheme the baseline is recorded with — and
    compared to its ``last_sync_hash``. A file with no recorded hash is never
    hashed and never yielded: there is no synced content for it to differ from.

    Lazy, so a caller that needs only whether any file changed stops hashing at
    the first one. A ``content_hash`` failure propagates to the caller.
    """
    for local_file in local_files:
        file_state = files_state.get(local_file["filename"])
        if file_state is None or file_state.last_sync_hash is None:
            continue
        if save_file_store.content_hash(local_file["path"]) != file_state.last_sync_hash:
            yield local_file["filename"]
