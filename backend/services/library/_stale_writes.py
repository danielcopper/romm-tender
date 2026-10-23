"""What the apply's end-of-run stale removal records on the rows it judged.

Called by :class:`~services.library.reporter.SyncReporter` inside the
finalize's write UoW, beside the unbinds, so the run's verdicts commit with
them. The preview records nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from domain.rom import GoneEvidence
    from services.protocols import UnitOfWork


def write_gone_marks(uow: UnitOfWork, gone_marks: Mapping[int, GoneEvidence | None]) -> None:
    """Record on each row what the run established about RomM still serving it: a mark, or a clear for ``None``."""
    for rom_id, evidence in gone_marks.items():
        rom = uow.roms.get(rom_id)
        if rom is None or rom.gone_from_romm == evidence:
            continue
        if evidence is None:
            rom.clear_gone_from_romm()
        else:
            rom.mark_gone_from_romm(evidence)
        uow.roms.save(rom)


def write_group_keys(uow: UnitOfWork, group_keys: Mapping[int, str]) -> None:
    """Write onto each row the group key its fetched siblings name it under (``sync_diff.regrouped_keys``)."""
    for rom_id, key in group_keys.items():
        rom = uow.roms.get(rom_id)
        if rom is None or rom.sibling_group_key == key:
            continue
        rom.adopt_sibling_group_key(key)
        uow.roms.save(rom)
