"""In-memory ``RetroDeckFolders`` implementation for service tests."""

from __future__ import annotations

import os

from domain.retrodeck_folders import FolderRefused, MoveRoots, no_rom_folder, no_rom_root


class FakeRetroDeckFolders:
    """In-memory ``RetroDeckFolders`` for tests.

    Each root is a mutable attribute so tests can flip individual folders
    without rebuilding the whole bundle; an empty one is a folder RetroDECK
    does not name. A system's folder is ``<roms>/<system>`` unless
    ``system_dirs`` names it, and an empty entry there is a system RetroDECK
    names no folder for — as is a name that is no single path component.

    ``refusal`` stands for RetroDECK's folders refused as a whole — reported
    as defaults, or a question about them raised: every question answers it
    (an ``EveryFolderRefused``). ``download_refusal``
    stands for RetroDECK being absent or switched off, which only a download
    asks about. ``rom_root`` answers the uninstall's refusal where ``roms`` is
    empty.
    """

    def __init__(
        self,
        *,
        saves: str = "",
        roms: str = "",
        bios: str = "",
        home: str = "",
        system_dirs: dict[str, str] | None = None,
        refusal: FolderRefused | None = None,
        download_refusal: FolderRefused | None = None,
    ) -> None:
        self.saves = saves
        self.roms = roms
        self.bios = bios
        self.home = home
        self.system_dirs = system_dirs if system_dirs is not None else {}
        self.refusal = refusal
        self.download_refusal = download_refusal

    def download_folder(self, system: str) -> str | FolderRefused:
        refused = self.refusal or self.download_refusal
        if refused is not None:
            return refused
        # A name that is no single path component is no system ES-DE declares,
        # so the resolver names no folder for it.
        declared = system and os.sep not in system and system not in {".", ".."}
        path = self.system_dirs.get(system, os.path.join(self.roms, system) if self.roms and declared else "")
        if not path:
            return no_rom_folder(system)
        return path

    def bios_download_folder(self) -> str | FolderRefused:
        refused = self.refusal or self.download_refusal
        if refused is not None:
            return refused
        return self.bios

    def rom_root(self) -> str | FolderRefused:
        return self.refusal or self.roms or no_rom_root()

    def bios_folder(self) -> str | FolderRefused | None:
        return self.refusal or self.bios or None

    def saves_root(self) -> str | FolderRefused | None:
        return self.refusal or self.saves or None

    def move_roots(self) -> MoveRoots | None:
        if self.refusal is not None or not (self.home or self.bios or self.saves):
            return None
        return MoveRoots(home=self.home, bios=self.bios, saves=self.saves)
