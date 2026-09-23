"""DataInventoryService — what this device holds, counted for the Data Management page.

The home for the population figures no other read answers: how many installs
this device holds and what they take, and which recovery bundles are sealed and
what each takes. Every other row on that page is already answered elsewhere —
the shortcut count by ``get_sync_stats``, the non-Steam entries by the
frontend's own scan of Steam's shortcut store — and none of those moves here.

The two figures come from two different worlds and are read apart for that
reason: the ROM size is what RomM reported, taken from the database, while the
bundle size is measured on disk. They are never read in one transaction — see
``get_data_inventory``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio

    from services.protocols import RecoveryBundleInventoryReader, UnitOfWorkFactory


@dataclass(frozen=True)
class DataInventoryServiceConfig:
    """Frozen wiring bundle handed to ``DataInventoryService.__init__``.

    Holds the loop the two readings are offloaded to, the SQLite Unit-of-Work
    factory the installed figures are read through, and the recovery-root
    reader — narrowed to the inventory question, so this service cannot seal or
    validate a bundle.
    """

    loop: asyncio.AbstractEventLoop
    uow_factory: UnitOfWorkFactory
    recovery_inventory: RecoveryBundleInventoryReader


class DataInventoryService:
    """Answers how much of each population this device holds."""

    def __init__(self, *, config: DataInventoryServiceConfig) -> None:
        self._loop = config.loop
        self._uow_factory = config.uow_factory
        self._recovery_inventory = config.recovery_inventory

    async def get_data_inventory(self) -> dict[str, Any]:
        """Report the installed-ROM and recovery-bundle populations with their sizes.

        Returns ``installed_roms`` / ``installed_bytes``,
        ``recovery_bundles`` / ``recovery_bytes``, ``recovery_root`` and
        ``recovery_bundle_list`` — one ``RecoveryBundleEntry`` per counted
        bundle, in no particular order.
        ``installed_roms`` counts INSTALLS — one per install, so a multi-disc
        game counts once and two installed versions of one game count twice —
        never files.

        ``recovery_root`` is where the bundles that were counted live. It
        crosses the wire because the folder is derived from this program's
        package name, so a panel spelling it for itself would be spelling a
        constant it cannot see, and wrong under a non-default home. Both byte figures are
        totals, and ``installed_bytes`` is an approximation the caller must
        present as one (see :meth:`_read_installed_io`).

        The two readings are two executor hops rather than one because the
        second touches the filesystem: a Unit of Work wraps database reads and
        writes and never file I/O, and ``BEGIN IMMEDIATE`` holds the write lock
        for as long as the transaction is open, so measuring a recovery root
        inside it would stall every other writer for the length of the walk.
        """
        installed_roms, installed_bytes = await self._loop.run_in_executor(None, self._read_installed_io)
        bundles = await self._loop.run_in_executor(None, self._recovery_inventory.bundle_inventory)
        return {
            "installed_roms": installed_roms,
            "installed_bytes": installed_bytes,
            "recovery_bundles": bundles["count"],
            "recovery_bytes": bundles["total_bytes"],
            "recovery_root": self._recovery_inventory.root(),
            "recovery_bundle_list": bundles["bundles"],
        }

    def _read_installed_io(self) -> tuple[int, int]:
        """Count the installs and sum the size RomM reported for their games.

        The size is the SERVER's figure (``Rom.fs_size_bytes``) and never a
        walk of the disk, so the page opens with a number instead of measuring
        for one. That makes it an approximation in two ways the caller has to
        keep: a game whose size RomM never reported contributes nothing, and
        what a game takes locally differs from what the server named whenever
        an archive was unpacked, a patch was written beside the original, or
        extras share the folder.
        """
        with self._uow_factory() as uow:
            installed_ids = {install.rom_id for install in uow.rom_installs.iter_all()}
            total = sum(
                rom.fs_size_bytes
                for rom in uow.roms.iter_all()
                if rom.rom_id in installed_ids and rom.fs_size_bytes is not None
            )
        return len(installed_ids), total
