"""RomInstallRecorder — the one writer of an install record.

Owns the step between "the ROM's files are in place" and "Tender knows them":
the ``rom_installs`` upsert. Nothing about the start is written here: the
shortcut carries its appId, and each start asks for the command with the
install it finds.

Both routes to an installed ROM go through here — a completed download and an
adoption of content Tender did not download — so the row an adoption produces
is derived by exactly the rules a download's is (ADR-0028), not by a second
implementation free to drift.

Every method is synchronous and opens its own short write Unit of Work
(ADR-0006); callers on the event loop offload via ``loop.run_in_executor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.game_files import download_size_bytes
from domain.rom_install import RomInstall

if TYPE_CHECKING:
    from collections.abc import Callable

    from services.protocols import Clock, UnitOfWorkFactory


@dataclass(frozen=True)
class RomInstallRecorderConfig:
    """Frozen wiring bundle handed to ``RomInstallRecorder.__init__``."""

    clock: Clock
    uow_factory: UnitOfWorkFactory


class RomInstallRecorder:
    """Writes the ``rom_installs`` row an install is."""

    def __init__(self, *, config: RomInstallRecorderConfig) -> None:
        self._clock = config.clock
        self._uow_factory = config.uow_factory

    def do_record_install(
        self,
        *,
        rom_id: int,
        rom_detail: dict[str, Any],
        file_path: str,
        rom_dir: str | None,
        system: str,
        cleanup: Callable[[], None],
    ) -> tuple[str | None, str | None]:
        """Build the ``RomInstall`` aggregate and persist it in a short write UoW.

        The filesystem work (rename, extraction, launch-file detection) has
        already run outside any transaction; only the upsert is wrapped here
        (ADR-0006). ``rom_dir`` is the game's own directory where it lands in a
        folder of its own, or ``None`` for a game RomM holds as one file, kept
        as it arrived (which owns no folder). If the RomM
        data fails the aggregate's invariant (non-positive ``rom_id``), nothing
        is persisted, *cleanup* removes the just-placed artifact, and a failure
        message is returned. The row records the size of the files a download of
        the game fetches (``download_size_bytes``; an adoption records the same
        figure); ``Rom.fs_size_bytes`` is written with RomM's size of the whole
        game.

        Returns ``(file_path, None)`` on success or ``(None, error)`` when the
        invariant rejects the data.
        """
        try:
            install = RomInstall.mark_installed(
                rom_id=int(rom_id),
                file_path=file_path,
                rom_dir=rom_dir,
                platform_slug=rom_detail.get("platform_slug", ""),
                system=system,
                installed_at=self._clock.now().isoformat(),
                fetched_size_bytes=_stated(download_size_bytes(rom_detail)),
            )
        except ValueError as e:
            cleanup()
            return None, f"Invalid install metadata: {e}"

        with self._uow_factory() as uow:
            uow.rom_installs.save(install)
            # A size RomM did not state never replaces one already recorded.
            size = rom_detail.get("fs_size_bytes")
            if size and size > 0:
                rom = uow.roms.get(int(rom_id))
                if rom is not None:
                    rom.record_fs_size_bytes(size)
                    uow.roms.set_fs_size_bytes(rom.rom_id, rom.fs_size_bytes)
        return file_path, None


def _stated(size: int) -> int | None:
    """*size*, or ``None`` where it states nothing (zero or less)."""
    return size if size > 0 else None
