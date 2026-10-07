"""RetroDECK's folders — every one of them the resolver's answer, asked through the source holder.

The single place Tender asks where RetroDECK keeps a system's ROMs, its ROM
root, its BIOS folder and its saves root. Each question takes one reading of
:mod:`adapters.emulator_sources` and asks RetroDECK's handle in it, because
RetroDECK is the one source Tender downloads into and removes from: a system's
folder is the handle's ``rom_location(system).dir``, the ROM root ES-DE's
``ROMDirectory`` (``roms_dir()``), and the BIOS folder and saves root the
handle's own ``bios_dir()`` and ``saves_root()``. Tender reads no RetroDECK
file of its own and builds no folder from a root.

**Every folder is symlink-resolved.** The roots are handed to the path guards
as safe roots, and a ROM path those guards are asked about is recorded resolved
wherever ``lib.path_safety.safe_join`` built it — so a root left as the
resolver spells it makes one directory look like two on any system where
``/home`` is a link to ``/var/home``, and a ROM recorded inside the root is
refused as outside it.

**A download creates a folder only below a root that exists.** A system's ROM
folder that is not there yet is created by the download, as ES-DE would create
it, and so is a BIOS folder inside RetroDECK's own folder; a root that is not
there — a drive or an SD card that is out — is never created, because the folder
would land on internal storage and the drive would hide it once it is back.

A resolver call that raises is logged and read as RetroDECK not being there to
ask: every answer here is then the one a machine without RetroDECK gets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _vendor.atlas import HEALTH_ISSUE_ROOT_MISSING

from adapters.emulator_sources import ROOTS_ARE_DEFAULTS
from domain.emulator_sources import RETRODECK
from domain.retrodeck_folders import (
    BIOS_DOWNLOAD,
    GAME_DOWNLOAD,
    FolderRefused,
    MoveRoots,
    bios_folder_missing,
    finding_refusal,
    no_rom_folder,
    no_rom_root,
    not_installed,
    rom_root_missing,
    switched_off,
    uninstall_not_installed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from adapters.emulator_sources import EmulatorSourcesAdapter

# A download also stops while RetroDECK's own folder is missing: everything it
# would land in lies below a folder that is not there.
_REFUSES_DOWNLOADS = ROOTS_ARE_DEFAULTS | {HEALTH_ISSUE_ROOT_MISSING}


class _Unasked(Exception):
    """A resolver question raised; the folder it was for is not named."""


@dataclass(frozen=True, slots=True)
class _RetroDeck:
    """RetroDECK's handle in one reading, its switch, and its health findings by code."""

    installation: Any
    enabled: bool
    findings: dict[str, dict[str, str]]

    def refusal(self, codes: frozenset[str]) -> FolderRefused | None:
        """The refusal for the first finding among *codes*, or ``None``."""
        code = next((code for code in self.findings if code in codes), None)
        return None if code is None else finding_refusal(code, self.findings[code])


class RetroDeckFoldersAdapter:
    """Answers RetroDECK's folders from the resolver.

    Implements the ``RetroDeckFolders`` Protocol structurally.
    """

    def __init__(self, *, sources: EmulatorSourcesAdapter, log_debug: Callable[[str], None]) -> None:
        self._sources = sources
        self._log_debug = log_debug

    def download_folder(self, system: str) -> str | FolderRefused:
        """Where a download of a *system* game lands — or why it may not."""
        retrodeck = self._for_download(GAME_DOWNLOAD)
        if isinstance(retrodeck, FolderRefused):
            return retrodeck
        handle = retrodeck.installation
        try:
            placement = self._ask(handle, f"rom_location({system!r})", lambda h: h.rom_location(system))
            root = self._ask(handle, "roms_dir", lambda h: h.roms_dir())
        except _Unasked:
            return no_rom_folder(system)
        if placement.dir is None or root is None:
            self._log_debug(
                f"[folders] no ROM folder for {system!r}: caveats={sorted({c.code for c in placement.caveats})}"
            )
            return no_rom_folder(system)
        folder, root = os.path.realpath(placement.dir), os.path.realpath(root)
        if os.path.isdir(folder) or (_inside(folder, root) and os.path.isdir(root)):
            return folder
        return rom_root_missing(root if _inside(folder, root) else folder)

    def bios_download_folder(self) -> str | FolderRefused:
        """Where a BIOS download lands — or why none may."""
        retrodeck = self._for_download(BIOS_DOWNLOAD)
        if isinstance(retrodeck, FolderRefused):
            return retrodeck
        handle = retrodeck.installation
        try:
            folder = os.path.realpath(self._ask(handle, "bios_dir", lambda h: h.bios_dir()))
            home = os.path.realpath(self._ask(handle, "root", lambda h: h.root()))
        except _Unasked:
            return not_installed(BIOS_DOWNLOAD)
        if os.path.isdir(folder) or (_inside(folder, home) and os.path.isdir(home)):
            return folder
        return bios_folder_missing(folder)

    def rom_root(self) -> str | FolderRefused:
        """The ROM root a removal of installed content is bounded by — or why there is none."""
        retrodeck = self._retrodeck()
        if retrodeck is None:
            return uninstall_not_installed()
        refused = retrodeck.refusal(ROOTS_ARE_DEFAULTS)
        if refused is not None:
            return refused
        try:
            root = self._ask(retrodeck.installation, "roms_dir", lambda h: h.roms_dir())
        except _Unasked:
            return no_rom_root()
        return no_rom_root() if not root else os.path.realpath(root)

    def bios_folder(self) -> str | FolderRefused | None:
        """The BIOS folder a removal is bounded by; ``None`` where RetroDECK names none."""
        return self._for_removal("bios_dir", lambda h: h.bios_dir())

    def saves_root(self) -> str | FolderRefused | None:
        """The saves root a removal is bounded by; ``None`` where RetroDECK names none."""
        return self._for_removal("saves_root", lambda h: h.saves_root())

    def move_roots(self) -> MoveRoots | None:
        """RetroDECK's home, BIOS folder and saves root; ``None`` without RetroDECK or while they are defaults."""
        retrodeck = self._retrodeck()
        if retrodeck is None or retrodeck.refusal(ROOTS_ARE_DEFAULTS) is not None:
            return None
        handle = retrodeck.installation
        try:
            return MoveRoots(
                home=os.path.realpath(self._ask(handle, "root", lambda h: h.root())),
                bios=os.path.realpath(self._ask(handle, "bios_dir", lambda h: h.bios_dir())),
                saves=os.path.realpath(self._ask(handle, "saves_root", lambda h: h.saves_root())),
            )
        except _Unasked:
            return None

    def _for_download(self, purpose: str) -> _RetroDeck | FolderRefused:
        """RetroDECK as a download may use it: detected, switched on, its folders neither defaults nor missing."""
        retrodeck = self._retrodeck()
        if retrodeck is None:
            return not_installed(purpose)
        if not retrodeck.enabled:
            return switched_off(purpose)
        return retrodeck.refusal(_REFUSES_DOWNLOADS) or retrodeck

    def _for_removal(self, subject: str, question: Callable[[Any], str | None]) -> str | FolderRefused | None:
        """One root as a removal may use it: RetroDECK detected, whatever its switch, its folders not defaults."""
        retrodeck = self._retrodeck()
        if retrodeck is None:
            return None
        refused = retrodeck.refusal(ROOTS_ARE_DEFAULTS)
        if refused is not None:
            return refused
        try:
            path = self._ask(retrodeck.installation, subject, question)
        except _Unasked:
            return None
        return os.path.realpath(path) if path else None

    def _retrodeck(self) -> _RetroDeck | None:
        """RetroDECK in a fresh reading, or ``None`` where it is not detected or its health could not be asked."""
        reading = self._sources.read()
        installation = reading.installation(RETRODECK)
        if installation is None:
            return None
        try:
            health = self._ask(installation, "health", lambda h: h.health())
        except _Unasked:
            return None
        findings: dict[str, dict[str, str]] = {}
        for issue in health.issues:
            findings.setdefault(issue.code, {str(key): str(value) for key, value in issue.data.items()})
        enabled = any(source.kind == RETRODECK and source.enabled for source in reading.sources)
        return _RetroDeck(installation=installation, enabled=enabled, findings=findings)

    def _ask(self, installation: Any, subject: str, question: Callable[[Any], Any]) -> Any:
        """One question to RetroDECK's handle; a raise is logged and ends the answer with :class:`_Unasked`.

        Deliberately broad, for the reason the holder's own ``_ask`` gives: the
        resolver's failures are its own, and the honest answer to "could not
        ask" is the same whatever raised.
        """
        try:
            return question(installation)
        except Exception as exc:
            self._log_debug(f"[folders] resolver failed on {subject}: {exc!r}")
            raise _Unasked from exc


def _inside(path: str, root: str) -> bool:
    """Whether the resolved *path* is *root* or lies below it."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
