"""Per-ROM save answers and local save-file discovery.

Asks the resolver where an installed ROM's emulator keeps its save and what
the save consists of, and enumerates the matching local save files. The
directory is the resolver's answer and nothing else: this module holds no
knowledge of how any emulator lays its saves out. No RomM I/O, no state
mutation. Shared by SlotsService, SyncEngine, and StatusService.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from domain.save_answer import UNESTABLISHED_NOT_ASKED, unestablished_answer

if TYPE_CHECKING:
    import logging

    from domain.save_answer import SaveAnswer
    from services.protocols import (
        ActiveCoreReader,
        RetroDeckPaths,
        SaveFileStore,
        SaveLocationReader,
        SystemResolver,
        UnitOfWorkFactory,
    )


@dataclass(frozen=True)
class RomInfoServiceConfig:
    """Frozen wiring bundle handed to ``RomInfoService.__init__``.

    Holds the Unit-of-Work factory (the ``rom_installs`` aggregate is the
    source of truth for installed-ROM file records — WS3), the Protocol-typed
    filesystem adapter, the RetroDECK runtime-path accessor, the per-ROM
    active-core resolver, the
    save-location reader that answers what a game's save consists of, the
    platform-slug-to-system resolver (which, with ``roms.fs_name``, builds the
    path a ROM the library knows but has not installed WOULD occupy — a save
    answer turns on the content file's extension, so omitting the path asks a
    different question), and the standard-library logger.
    """

    uow_factory: UnitOfWorkFactory
    save_file_store: SaveFileStore
    retrodeck_paths: RetroDeckPaths
    active_core: ActiveCoreReader
    save_locations: SaveLocationReader
    resolve_system: SystemResolver
    logger: logging.Logger


class RomInfoService:
    """Resolves per-ROM save paths and discovers local save files on disk."""

    def __init__(self, *, config: RomInfoServiceConfig) -> None:
        self._config = config
        self._uow_factory = config.uow_factory
        self._save_file_store = config.save_file_store
        self._retrodeck_paths = config.retrodeck_paths
        self._active_core = config.active_core
        self._save_locations = config.save_locations
        self._resolve_system = config.resolve_system
        self._logger = config.logger

    def get_rom_save_info(self, rom_id: int, *, save_answer: SaveAnswer | None = None) -> dict[str, Any] | None:
        """Save-related info for an installed ROM, or ``None`` when it is not installed.

        Returns a dict with keys ``system``, ``rom_name``, ``saves_dir``,
        ``platform_slug``, ``file_path`` and ``save_answer``. ``saves_dir`` is
        the resolver's answer for where this ROM's emulator keeps the save, and
        is ``None`` wherever no placement could be resolved — never replaced by
        a guess, so every reader has to take its refusal there.

        *save_answer* is this ROM's reading where the caller already took one
        in the same operation; absent it, one is taken here.
        """
        installed = self._install_row(rom_id)
        if installed is None:
            return None
        system, file_path, platform_slug = installed
        answer = save_answer if save_answer is not None else self._installed_answer(rom_id, system, file_path)
        return {
            "system": system,
            "rom_name": os.path.splitext(os.path.basename(file_path))[0],
            "saves_dir": answer.directory,
            "platform_slug": platform_slug,
            "file_path": file_path,
            "save_answer": answer,
        }

    def _install_row(self, rom_id: int) -> tuple[str, str, str] | None:
        """``(system, file_path, platform_slug)`` off the install record, or ``None``."""
        with self._uow_factory() as uow:
            installed = uow.rom_installs.get(int(rom_id))
        if not installed or not installed.system or not installed.file_path:
            return None
        return (installed.system, installed.file_path, installed.platform_slug)

    def is_content_installed(self, rom_id: int) -> bool:
        """Whether this ROM's content is on disk, without asking where its saves go.

        The install-row question :meth:`get_rom_save_info` asks first, minus the
        live reading of the machine behind it. Callers that need only to know
        whether the ROM is installed ask here, so that costs one row read.
        """
        return self._install_row(rom_id) is not None

    def save_answer(self, rom_id: int) -> SaveAnswer:
        """What this ROM's save consists of and whether it may be synced at all.

        Read live off the machine on every call, through the emulator this ROM
        would launch with, and always against the ROM's own content path — the
        answer turns on the content file's EXTENSION, so it is a property of the
        ROM and never of its platform. On this machine PUAE answers
        ``save-inside-content`` for an Amiga ``.adf`` and states nothing at all
        for an ``.hdf``, and Genesis Plus GX answers a shared ``scd_*.brm`` for a
        Sega CD ``.chd`` and a per-game ``.srm`` for a ``.bin``. Asking without
        the real path would answer a different question and look like an answer
        to this one.

        An installed ROM is asked about its ``file_path``; a ROM the library
        knows but has not installed is asked about the path it WOULD occupy,
        built from ``roms.fs_name``. Where no path can be formed at all — no
        row, no name, no platform — the answer is ``unestablished``, which is a
        refusal and never a guess.
        """
        installed = self._install_row(rom_id)
        if installed is not None:
            return self._installed_answer(rom_id, installed[0], installed[1])
        return self._uninstalled_answer(rom_id)

    def _uninstalled_answer(self, rom_id: int) -> SaveAnswer:
        """The answer for a ROM the library holds but the disk does not.

        Its ``fs_name`` carries the extension the answer turns on, so the
        question can still be put — about the path the ROM would occupy once
        installed. Nothing is probed for either way: :meth:`synced_save_names`
        pairs an uninstalled ROM with no directory, because a state is a
        statement about an emulator and a probe needs a file.
        """
        with self._uow_factory() as uow:
            rom = uow.roms.get(int(rom_id))
        if rom is None or not rom.fs_name or not rom.platform_slug:
            # No name, so no extension, so no question — not a statement about
            # any emulator.
            return unestablished_answer(shape=UNESTABLISHED_NOT_ASKED)
        system = self._resolve_system(rom.platform_slug)
        content_path = os.path.join(self._retrodeck_paths.roms_path(), system, rom.fs_name)
        return self._ask_resolver(rom_id, system, content_path, installed=False)

    def _installed_answer(self, rom_id: int, system: str, file_path: str) -> SaveAnswer:
        """The answer for an installed ROM, asked about the file on disk.

        The system is the NORMALIZED one the install record carries, never the
        raw RomM ``platform_slug`` beside it (ADR-0010): the slug names no
        system any emulator declares, so asking with it answers about nothing.
        One of the two places in THIS service that decide a system and a path —
        :meth:`_uninstalled_answer` is the other — so the leak has two sites to
        guard here rather than one per caller.
        """
        return self._ask_resolver(rom_id, system, file_path, installed=True)

    def _ask_resolver(self, rom_id: int, system: str, content_path: str, *, installed: bool) -> SaveAnswer:
        """Put the question to the emulator this ROM would launch with.

        *installed* says whether *content_path* is a file on disk or the path
        the ROM would occupy, and rides onto the answer so a surface never
        renders a prediction as an observation.
        """
        emulator = self._active_core.active_emulator_for_rom(int(rom_id))
        return self._save_locations.resolve_save_answer(
            system=system,
            content_path=content_path,
            emulator_label=emulator.label if emulator is not None else None,
            content_installed=installed,
        )

    def synced_save_names(self, rom_id: int, *, save_answer: SaveAnswer | None = None) -> tuple[list[str], str | None]:
        """The basenames a sync may carry for this ROM, and the directory they sit in.

        Empty names with a ``None`` directory whenever the ROM's save may not be
        synced — every refusing state, a save written beside the content, and an
        uninstalled ROM. That pairing is what makes a refusal cost no probe:
        there is nothing to look for and nowhere to look.

        *save_answer* is this ROM's reading where the caller already took one in
        the same operation, and it is used instead of taking a second. Live is a
        property of operations rather than of layers, and a reading costs real
        machine I/O on a path that runs at every launch and every exit.
        """
        info = self.get_rom_save_info(rom_id, save_answer=save_answer)
        if not info:
            return ([], None)
        answer: SaveAnswer = info["save_answer"]
        if answer.sync_directory is None:
            return ([], None)
        return (list(answer.synced_names), answer.sync_directory)

    def find_save_files(self, rom_id: int, *, save_answer: SaveAnswer | None = None) -> list[dict[str, str]]:
        """Find local save files for a ROM.

        Returns list of ``{"path": str, "filename": str}``. *save_answer* passes
        a reading the caller already holds through to :meth:`synced_save_names`.
        """
        return self.probe_save_files(*self.synced_save_names(rom_id, save_answer=save_answer))

    def probe_save_files(self, names: list[str], saves_dir: str | None) -> list[dict[str, str]]:
        """Which of *names* are actually on disk under *saves_dir*.

        Public (peer-called): the sync matrix already holds the answer's names —
        it needs them to group server saves onto their canonical targets — and
        asking the resolver a second time for the same ROM costs a live reading
        of the machine. A ``None`` directory is the refusing answer's pairing and
        probes nothing.

        Returns a list of ``{"path", "filename"}``.
        """
        if saves_dir is None or not self._save_file_store.is_dir(saves_dir):
            return []
        results = []
        for name in names:
            save_path = os.path.join(saves_dir, name)
            if self._save_file_store.is_file(save_path):
                results.append({"path": save_path, "filename": name})
        return results

    def expected_save_files(self, rom_id: int) -> list[dict[str, str]]:
        """Project exact save paths for one installed ROM without broad scanning."""
        names, saves_dir = self.synced_save_names(rom_id)
        if saves_dir is None:
            return []
        return [
            {
                "path": os.path.join(saves_dir, name),
                "filename": name,
                "saves_dir": saves_dir,
            }
            for name in names
        ]
