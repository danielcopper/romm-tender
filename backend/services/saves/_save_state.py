"""The saves sub-services' one short read and one short write of a ROM's save state.

Each call opens and closes its own Unit of Work over the ``RomSaveSyncState``
aggregate, through the factory the caller passes in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.rom_save_sync_state import RomSaveSyncState
    from services.protocols import UnitOfWorkFactory


def read_save_state(uow_factory: UnitOfWorkFactory, rom_id: int) -> RomSaveSyncState | None:
    """Return the stored save state for *rom_id*, or ``None`` when there is none."""
    with uow_factory() as uow:
        return uow.rom_save_sync_states.get(rom_id)


def write_save_state(uow_factory: UnitOfWorkFactory, rom_id: int, save_state: RomSaveSyncState) -> None:
    """Persist *save_state* for *rom_id*."""
    with uow_factory() as uow:
        uow.rom_save_sync_states.save(rom_id, save_state)
