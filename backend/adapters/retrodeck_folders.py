"""RetroDECK's folders — every one of them the resolver's answer, asked through the source holder.

The single place Tender asks where RetroDECK keeps a system's ROMs, its ROM
root, its BIOS folder and its saves root. Each question takes one reading of
:mod:`adapters.emulator_sources` and asks RetroDECK's handle in it, because
until Tender keeps downloads in a library of its own, RetroDECK is the one
source Tender downloads into and removes from: a system's
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

**RetroDECK's roots are asked together, up front.** Every question first
reads RetroDECK's health and its four roots — home, ROM root, BIOS folder and
saves root — in one go, so a raise from any of them, or from the detection of
the sources, establishes none of them: every question then answers that
RetroDECK's folders could not be established, the removal's bounds as well as a
download's, and the move code sees no move. Only a download refused for the
switch keeps saying so. A system's own ROM folder is not a root and is asked
only by the question that needs it, so its raise refuses that answer alone.
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
    system_unanswered_refusal,
    unanswered_refusal,
    uninstall_not_installed,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from adapters.emulator_sources import EmulatorSourcesAdapter

# A download also stops while RetroDECK's own folder is missing: everything it
# would land in lies below a folder that is not there.
_REFUSES_DOWNLOADS = ROOTS_ARE_DEFAULTS | {HEALTH_ISSUE_ROOT_MISSING}


class _Unasked(Exception):
    """A resolver question raised; the folder it was for is not named."""


@dataclass(frozen=True, slots=True)
class _Roots:
    """RetroDECK's four roots, as the resolver spells them."""

    home: str
    roms: str | None
    bios: str
    saves: str


@dataclass(frozen=True, slots=True)
class _Answered:
    """RetroDECK's health findings by code, and its roots, from one up-front asking."""

    findings: dict[str, dict[str, str]]
    roots: _Roots


@dataclass(frozen=True, slots=True)
class _RetroDeck:
    """RetroDECK in one reading: its handle, its switch, and what it answered — ``None`` where a question raised.

    The handle is ``None`` where the detection itself raised.
    """

    installation: Any
    switched_off: bool
    answered: _Answered | None

    def usable(self, codes: frozenset[str]) -> _Answered | FolderRefused:
        """What RetroDECK answered — or the refusal where a question raised, or for its first finding in *codes*."""
        if self.answered is None:
            return unanswered_refusal()
        findings = self.answered.findings
        code = next((code for code in findings if code in codes), None)
        return self.answered if code is None else finding_refusal(code, findings[code])


@dataclass(frozen=True, slots=True)
class _Usable:
    """RetroDECK's handle and roots as a download or a removal may use them."""

    installation: Any
    roots: _Roots


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
        try:
            placement = self._ask(retrodeck.installation, f"rom_location({system!r})", lambda h: h.rom_location(system))
        except _Unasked:
            return unanswered_refusal()
        root = retrodeck.roots.roms
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
        folder, home = os.path.realpath(retrodeck.roots.bios), os.path.realpath(retrodeck.roots.home)
        if os.path.isdir(folder) or (_inside(folder, home) and os.path.isdir(home)):
            return folder
        return bios_folder_missing(folder)

    def rom_root(self) -> str | FolderRefused:
        """RetroDECK's ROM root — or why a removal of installed content may not go ahead at all."""
        retrodeck = self._for_removal()
        if retrodeck is None:
            return uninstall_not_installed()
        if isinstance(retrodeck, FolderRefused):
            return retrodeck
        roms = retrodeck.roots.roms
        return no_rom_root() if not roms else os.path.realpath(roms)

    def rom_folders(self, systems: Iterable[str]) -> dict[str, str | FolderRefused] | FolderRefused:
        """Each of *systems*' own ROM folder, which bounds a removal of its installed content — or why none may.

        The refusals :meth:`rom_root` answers stand for every system; a system
        RetroDECK names no folder for, or whose question raised, is refused on
        its own. A folder is the system's, not the ROM root's, because a
        system's folder may be a link to another drive, where its games land.
        """
        retrodeck = self._for_removal()
        if retrodeck is None:
            return uninstall_not_installed()
        if isinstance(retrodeck, FolderRefused):
            return retrodeck
        if not retrodeck.roots.roms:
            return no_rom_root()
        return {system: self._removal_folder(retrodeck.installation, system) for system in set(systems)}

    def _removal_folder(self, installation: Any, system: str) -> str | FolderRefused:
        """The *system*'s own ROM folder as a removal is bounded by it, or that system's refusal."""
        try:
            placement = self._ask(installation, f"rom_location({system!r})", lambda h: h.rom_location(system))
        except _Unasked:
            return system_unanswered_refusal()
        return no_rom_root() if placement.dir is None else os.path.realpath(placement.dir)

    def bios_folder(self) -> str | FolderRefused | None:
        """The BIOS folder a removal is bounded by; ``None`` where RetroDECK names none."""
        retrodeck = self._for_removal()
        if retrodeck is None or isinstance(retrodeck, FolderRefused):
            return retrodeck
        bios = retrodeck.roots.bios
        return os.path.realpath(bios) if bios else None

    def saves_root(self) -> str | FolderRefused | None:
        """The saves root a removal is bounded by; ``None`` where RetroDECK names none."""
        retrodeck = self._for_removal()
        if retrodeck is None or isinstance(retrodeck, FolderRefused):
            return retrodeck
        saves = retrodeck.roots.saves
        return os.path.realpath(saves) if saves else None

    def move_roots(self) -> MoveRoots | FolderRefused | None:
        """RetroDECK's home, BIOS and saves roots; ``None`` without RetroDECK, or the refusal where they are unfit."""
        retrodeck = self._for_removal()
        if retrodeck is None or isinstance(retrodeck, FolderRefused):
            return retrodeck
        roots = retrodeck.roots
        return MoveRoots(
            home=os.path.realpath(roots.home),
            bios=os.path.realpath(roots.bios),
            saves=os.path.realpath(roots.saves),
        )

    def _for_download(self, purpose: str) -> _Usable | FolderRefused:
        """RetroDECK as a download uses it: detected, switched on, its folders established, not defaults or missing."""
        retrodeck = self._retrodeck()
        if retrodeck is None:
            return not_installed(purpose)
        if retrodeck.switched_off:
            return switched_off(purpose)
        answered = retrodeck.usable(_REFUSES_DOWNLOADS)
        if isinstance(answered, FolderRefused):
            return answered
        return _Usable(installation=retrodeck.installation, roots=answered.roots)

    def _for_removal(self) -> _Usable | FolderRefused | None:
        """RetroDECK as a removal uses it: detected, whatever its switch, its folders established and not defaults."""
        retrodeck = self._retrodeck()
        if retrodeck is None:
            return None
        answered = retrodeck.usable(ROOTS_ARE_DEFAULTS)
        if isinstance(answered, FolderRefused):
            return answered
        return _Usable(installation=retrodeck.installation, roots=answered.roots)

    def _retrodeck(self) -> _RetroDeck | None:
        """RetroDECK in a fresh reading, its health and roots asked together; ``None`` where it is not detected."""
        reading = self._sources.read()
        switched = reading.switched_off(RETRODECK)
        if reading.detection_failed:
            return _RetroDeck(installation=None, switched_off=switched, answered=None)
        installation = reading.installation(RETRODECK)
        if installation is None:
            return None
        try:
            answered = self._answered(installation)
        except _Unasked:
            answered = None
        return _RetroDeck(installation=installation, switched_off=switched, answered=answered)

    def _answered(self, installation: Any) -> _Answered:
        """RetroDECK's health and four roots; :class:`_Unasked` where any one of the questions raised."""
        health = self._ask(installation, "health", lambda h: h.health())
        roots = _Roots(
            home=self._ask(installation, "root", lambda h: h.root()),
            roms=self._ask(installation, "roms_dir", lambda h: h.roms_dir()),
            bios=self._ask(installation, "bios_dir", lambda h: h.bios_dir()),
            saves=self._ask(installation, "saves_root", lambda h: h.saves_root()),
        )
        findings: dict[str, dict[str, str]] = {}
        for issue in health.issues:
            findings.setdefault(issue.code, {str(key): str(value) for key, value in issue.data.items()})
        return _Answered(findings=findings, roots=roots)

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
