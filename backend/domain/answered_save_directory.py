"""AnsweredSaveDirectory — the save directory the resolver last answered for one ROM.

Compared with today's answer to notice that a game's save directory moved, and
read only as the source of the move that follows (``services/saves/save_directory.py``);
never where a sync or a probe looks. Why it is an aggregate of its own rather
than a field of ``RomSaveSyncState`` is ADR-0041's.

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
