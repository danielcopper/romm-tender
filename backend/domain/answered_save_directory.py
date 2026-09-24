"""AnsweredSaveDirectory — the save directory the resolver last answered for one ROM.

Compared with today's answer to notice that a game's save directory moved, and
read only as the source of the move that follows (``services/saves/save_directory.py``);
never where a save is looked for.

Its own aggregate rather than a field of ``RomSaveSyncState``: a save-sync state
row means "this ROM has been tracked for save sync", and the slot listing and the
cached game detail read its absence as "never tracked". This record is written
for games that were never synced — the one-time backfill, a first sight, an
answer the sync refuses — so it must not create that row.

Keyed by ``rom_id``. A thin record built whole and upserted, so it carries a
single ``record`` constructor and no verb-named mutators.
"""

from __future__ import annotations

from domain._aggregate import cosmic_aggregate


@cosmic_aggregate
class AnsweredSaveDirectory:
    """The directory the resolver last answered for one ROM's save."""

    rom_id: int
    directory: str

    @classmethod
    def record(cls, *, rom_id: int, directory: str) -> AnsweredSaveDirectory:
        """Record *directory* as the save directory the resolver answered for *rom_id*.

        Raises ``ValueError`` on an empty directory — an answer with no
        directory is never recorded.
        """
        if not directory:
            raise ValueError("directory is required")
        return cls(rom_id=rom_id, directory=directory)
