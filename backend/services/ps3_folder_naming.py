"""Ps3FolderNamingService — naming the PS3 games already installed as folders as ES-DE scans them.

A one-time step (#2195 D27): a download names a PS3 game kept as a folder
``<game>.ps3`` (D11), and the resolver answers a folder without that name as no
launch file, so one installed before that name came in is renamed once, with
its install record following, and keeps starting without a new download. The
step stays in every later release, so an update that skips releases still
gets it; it is safe to run again, because a folder already named for ES-DE is
never renamed.

Which installs it renames is fixed at its first pass and recorded in
``kv_config``: a folder adopted later is recorded as it stands and gets the
resolver's answer for its name (D27), even while the step is still open. Its
completion is stamped only after a pass that left none of those it could
still rename: a folder that is not there — a drive not mounted, a RetroDECK
move not yet followed — or whose move or record failed is asked about again at
the next start. A folder whose new name is taken keeps its name and counts as
done: nothing is moved over another folder. A folder already moved whose
record still names its old place — a pass stopped between the two, or a
database put back by the installer's rollback — gets its record moved after
it.

A second one-time step runs right after it, with its own subject and stamp
and the same rules (D39): a game folder found exactly one level inside an
install's folder is placed as that folder itself, since ES-DE, the resolver and
RPCS3 look at the install's folder and find nothing to boot one level above the
game. It is moved only where it is the folder's only entry; anything beside it
keeps the install as it is, so nothing is deleted or left behind. The game
folder waits in a sibling while the install's folder is emptied, and a pass
stopped at any point is finished at the next start.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.rom_files import ps3_folder_lift, ps3_folder_rename

if TYPE_CHECKING:
    import logging

    from domain.rom_files import Ps3FolderLift
    from domain.rom_install import RomInstall
    from services.protocols import DownloadFileStore, SystemSupportedExtensionsFn, UnitOfWorkFactory

# The completion stamp and the step's subject: derived markers about work this
# install does, in ``kv_config`` beside the other cross-run markers
# (GLOSSARY.md, persistence boundary). The subject is a JSON list of rom_ids.
KV_PS3_FOLDERS_NAMED = "ps3_folders_named"
KV_PS3_FOLDERS_TO_NAME = "ps3_folders_to_name"
KV_PS3_FOLDERS_LIFTED = "ps3_folders_lifted"
KV_PS3_FOLDERS_TO_LIFT = "ps3_folders_to_lift"


@dataclass(frozen=True)
class Ps3FolderNamingServiceConfig:
    """Frozen wiring bundle handed to ``Ps3FolderNamingService.__init__``.

    ``system_extensions`` is each system's accept-list, which says whether a
    folder's name already is one ES-DE scans.
    """

    uow_factory: UnitOfWorkFactory
    file_store: DownloadFileStore
    system_extensions: SystemSupportedExtensionsFn
    logger: logging.Logger


class Ps3FolderNamingService:
    """Renames the PS3 folder installs not yet named ``<game>.ps3``, once."""

    def __init__(self, *, config: Ps3FolderNamingServiceConfig) -> None:
        self._uow_factory = config.uow_factory
        self._file_store = config.file_store
        self._system_extensions = config.system_extensions
        self._logger = config.logger

    def name_ps3_folders(self) -> None:
        """Run the naming step, then the lifting step, each until its own stamp says it is done.

        Each folder is moved outside any Unit of Work, and its install record is
        relocated in a short one after the move, only while it still names the
        folder that was moved.
        """
        self._name_pass()
        self._lift_pass()

    def _name_pass(self) -> None:
        """Rename each install of the step's subject not named for ES-DE, and stamp the step done when none is left."""
        with self._uow_factory() as uow:
            if uow.kv_config.get(KV_PS3_FOLDERS_NAMED) == "1":
                return
            recorded = uow.kv_config.get(KV_PS3_FOLDERS_TO_NAME)
            installs = list(uow.rom_installs.iter_all())
        accepted: dict[str, frozenset[str]] = {}

        def rename_of(install: RomInstall) -> tuple[str, str] | None:
            if install.system not in accepted:
                accepted[install.system] = self._system_extensions(install.system)
            return ps3_folder_rename(install.rom_dir, install.file_path, accepted[install.system])

        if recorded is None:
            subject = {install.rom_id for install in installs if rename_of(install) is not None}
            with self._uow_factory() as uow:
                uow.kv_config.set(KV_PS3_FOLDERS_TO_NAME, json.dumps(sorted(subject)))
        else:
            subject = set(json.loads(recorded))
        left = 0
        for install in installs:
            rename = rename_of(install) if install.rom_id in subject else None
            if rename is not None and not self._name(install, *rename):
                left += 1
        if left == 0:
            with self._uow_factory() as uow:
                uow.kv_config.set(KV_PS3_FOLDERS_NAMED, "1")

    def _lift_pass(self) -> None:
        """Lift each install of the lifting step's subject, and stamp that step done when none is left."""
        with self._uow_factory() as uow:
            if uow.kv_config.get(KV_PS3_FOLDERS_LIFTED) == "1":
                return
            recorded = uow.kv_config.get(KV_PS3_FOLDERS_TO_LIFT)
            installs = list(uow.rom_installs.iter_all())
        if recorded is None:
            subject = {install.rom_id for install in installs if ps3_folder_lift(install.rom_dir, install.file_path)}
            with self._uow_factory() as uow:
                uow.kv_config.set(KV_PS3_FOLDERS_TO_LIFT, json.dumps(sorted(subject)))
        else:
            subject = set(json.loads(recorded))
        left = 0
        for install in installs:
            lift = ps3_folder_lift(install.rom_dir, install.file_path) if install.rom_id in subject else None
            if lift is not None and not self._lift(install, lift):
                left += 1
        if left == 0:
            with self._uow_factory() as uow:
                uow.kv_config.set(KV_PS3_FOLDERS_LIFTED, "1")

    def _lift(self, install: RomInstall, lift: Ps3FolderLift) -> bool:
        """Place one install's game folder as its folder; ``False`` where the next start has to ask again.

        Each state a pass stopped in is picked up where it stopped: the game
        folder waiting in its sibling, with the install's folder empty or gone,
        or already in place with its record still naming the folder inside.
        """
        rom_dir = install.rom_dir
        if rom_dir is None:
            return True
        store = self._file_store
        if not store.exists(install.file_path) and store.exists(lift.launch_path):
            return self._relocate(install.rom_id, rom_dir, rom_dir, lift.launch_path)
        if store.exists(lift.staging):
            return self._finish_lift(install.rom_id, rom_dir, lift)
        if not store.exists(rom_dir):
            self._logger.info(f"PS3 folder of rom_id={install.rom_id} is not at {rom_dir}; asked again later")
            return False
        names = [entry["name"] for entry in store.list_top_level_names(rom_dir)]
        if names != [os.path.basename(lift.inner)]:
            self._logger.info(f"PS3 game folder {lift.inner} is not alone in {rom_dir}; left where it is")
            return True
        try:
            store.move_dir(lift.inner, lift.staging)
        except OSError as e:
            self._logger.warning(f"PS3 folder lift of {lift.inner} failed: {e}; asked again later")
            return False
        return self._finish_lift(install.rom_id, rom_dir, lift)

    def _finish_lift(self, rom_id: int, rom_dir: str, lift: Ps3FolderLift) -> bool:
        """Remove the emptied *rom_dir*, put the waiting game folder in its place, then move the record after it."""
        store = self._file_store
        try:
            if store.exists(rom_dir):
                store.remove_empty_dir(rom_dir)
            store.move_dir(lift.staging, rom_dir)
        except OSError as e:
            self._logger.warning(f"PS3 folder lift into {rom_dir} failed: {e}; {lift.staging} kept, asked again later")
            return False
        self._logger.info(f"Lifted the PS3 game folder of rom_id={rom_id} into {rom_dir}")
        return self._relocate(rom_id, rom_dir, rom_dir, lift.launch_path)

    def _name(self, install: RomInstall, new_rom_dir: str, new_file_path: str) -> bool:
        """Name one install's folder and move its record after it; ``False`` where the next start has to ask again."""
        old_rom_dir = install.rom_dir
        if old_rom_dir is None:
            return True
        if not self._file_store.exists(old_rom_dir):
            if self._file_store.exists(new_file_path):
                return self._relocate(install.rom_id, old_rom_dir, new_rom_dir, new_file_path)
            self._logger.info(f"PS3 folder of rom_id={install.rom_id} is not at {old_rom_dir}; asked again later")
            return False
        if self._file_store.exists(new_rom_dir):
            self._logger.warning(f"PS3 folder rename skipped: {new_rom_dir} already exists; keeping {old_rom_dir}")
            return True
        try:
            self._file_store.move_dir(old_rom_dir, new_rom_dir)
        except OSError as e:
            self._logger.warning(f"PS3 folder rename of {old_rom_dir} failed: {e}; asked again later")
            return False
        self._logger.info(f"Renamed the PS3 folder of rom_id={install.rom_id} to {new_rom_dir}")
        return self._relocate(install.rom_id, old_rom_dir, new_rom_dir, new_file_path)

    def _relocate(self, rom_id: int, old_rom_dir: str, new_rom_dir: str, new_file_path: str) -> bool:
        """Move the install record after its folder; ``False`` where that failed, for the next start to heal.

        Deliberately broad: whatever the write raised, the other installs of
        the pass are still renamed, and this one's moved folder is found and its
        record healed at the next start.
        """
        try:
            with self._uow_factory() as uow:
                install = uow.rom_installs.get(rom_id)
                if install is None or install.rom_dir != old_rom_dir:
                    return True
                install.relocate(new_rom_dir, new_file_path)
                uow.rom_installs.save(install)
        except Exception as e:
            self._logger.warning(f"Moving the install record of rom_id={rom_id} to {new_rom_dir} failed: {e}")
            return False
        return True
