"""RetroDECK's folders — every one of them the resolver's answer, asked through the source holder.

The single place Tender asks where RetroDECK keeps a system's ROMs, its ROM
root, its BIOS folder and its saves root. Each question takes one reading of
:mod:`adapters.emulator_sources` and asks RetroDECK's handle in it, because
until Tender keeps downloads in a library of its own, RetroDECK is the one
source Tender downloads into and removes from: a system's
folder is the handle's ``rom_location(system).dir``, and the ROM root (ES-DE's
``ROMDirectory``), the home, the BIOS folder and the saves root are the
handle's ``roots()``. Tender reads no RetroDECK file of its own and builds no
folder from a root.

**Every folder is symlink-resolved.** The folders are handed to the path
guards as safe roots — a system's own ROM folder for a game's files, the BIOS
folder and saves root for theirs — and a ROM path those guards are asked about
is recorded resolved wherever ``lib.path_safety.safe_join`` built it — so a
folder left as the resolver spells it makes one directory look like two on any
system where ``/home`` is a link to ``/var/home``, and a ROM recorded inside it
is refused as outside it.

**A download creates a folder only below a root that exists.** A system's ROM
folder that is not there yet is created by the download, as ES-DE would create
it; a root that is not there — a drive or an SD card that is out — is never
created, because the folder would land on internal storage and the drive would
hide it once it is back.

**A root the resolver only assumed is used for nothing.** Which root may be
used is :mod:`domain.source_roots`'s rule; each question here asks it about the
one root it needs, and a system's folder goes with the ROM root it lies below.

**RetroDECK's roots are asked together, up front.** Every question first reads
the roots answer — home, ROM root, BIOS folder and saves root, and the findings
beside them — in one go, so a raise there, or from the detection of the
sources, establishes none of them: every question then answers that
RetroDECK's folders could not be established, the removal's bounds as well as a
download's, the move code sees no move, and its migrate press is refused. Only a
download refused for the switch keeps saying so. A system's own ROM folder is not
a root and is asked only by the questions that need it, so its raise refuses
that system's answer alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _vendor.atlas import HEALTH_ISSUE_ROOT_MISSING

from adapters.emulator_sources import source_roots
from domain.emulator_sources import RETRODECK
from domain.retrodeck_folders import (
    GAME_DOWNLOAD,
    FolderRefused,
    MoveRoots,
    finding_refusal,
    no_bios_root,
    no_rom_folder,
    no_rom_root,
    not_installed,
    rom_root_missing,
    switched_off,
    system_unanswered_refusal,
    unanswered_refusal,
    uninstall_not_installed,
)
from domain.source_roots import (
    ROOT_BIOS,
    ROOT_INSTALLATION,
    ROOT_ROMS,
    ROOT_SAVES,
    SourceRoots,
    counted_findings,
    unusable_reason,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from adapters.emulator_sources import EmulatorSourcesAdapter


class _Unasked(Exception):
    """A resolver question raised; the folder it was for is not named."""


@dataclass(frozen=True, slots=True)
class _RetroDeck:
    """RetroDECK in one reading: its handle, its switch, and its roots — ``None`` where the question raised.

    The handle is ``None`` where the detection itself raised.
    """

    installation: Any
    switched_off: bool
    roots: SourceRoots | None


@dataclass(frozen=True, slots=True)
class _Usable:
    """RetroDECK's handle and roots as a download or a removal may ask them."""

    installation: Any
    roots: SourceRoots


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
        root = _root(retrodeck.roots, ROOT_ROMS)
        if isinstance(root, FolderRefused):
            return root
        try:
            placement = self._ask(retrodeck.installation, f"rom_location({system!r})", lambda h: h.rom_location(system))
        except _Unasked:
            return unanswered_refusal()
        if placement.dir is None or root is None:
            self._log_debug(
                f"[folders] no ROM folder for {system!r}: caveats={sorted({c.code for c in placement.caveats})}"
            )
            return no_rom_folder(system)
        folder, root = os.path.realpath(placement.dir), os.path.realpath(root)
        if os.path.isdir(folder) or (_inside(folder, root) and os.path.isdir(root)):
            return folder
        return rom_root_missing(root if _inside(folder, root) else folder)

    def rom_root(self) -> str | FolderRefused:
        """RetroDECK's ROM root — or why a removal of installed content may not go ahead at all."""
        retrodeck = self._for_removal()
        if retrodeck is None:
            return uninstall_not_installed()
        return retrodeck if isinstance(retrodeck, FolderRefused) else _removal_rom_root(retrodeck)

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
        root = _removal_rom_root(retrodeck)
        if isinstance(root, FolderRefused):
            return root
        return {system: self._removal_folder(retrodeck.installation, system) for system in set(systems)}

    def _removal_folder(self, installation: Any, system: str) -> str | FolderRefused:
        """The *system*'s own ROM folder as a removal is bounded by it, or that system's refusal."""
        try:
            placement = self._ask(installation, f"rom_location({system!r})", lambda h: h.rom_location(system))
        except _Unasked:
            return system_unanswered_refusal()
        return no_rom_root() if placement.dir is None else os.path.realpath(placement.dir)

    def bios_folder(self) -> str | FolderRefused | None:
        """The BIOS folder a removal is bounded by; ``None`` where no RetroDECK is detected.

        A detected RetroDECK that names no BIOS folder, and no finding says
        why, answers :func:`no_bios_root`.
        """
        retrodeck = self._for_removal()
        if retrodeck is None or isinstance(retrodeck, FolderRefused):
            return retrodeck
        path = _root(retrodeck.roots, ROOT_BIOS)
        if path is None:
            return no_bios_root()
        return path if isinstance(path, FolderRefused) else os.path.realpath(path)

    def saves_root(self) -> str | FolderRefused | None:
        """The saves root a removal is bounded by; ``None`` where RetroDECK names none."""
        return self._removal_root(ROOT_SAVES)

    def move_roots(self) -> MoveRoots | FolderRefused | None:
        """RetroDECK's home, BIOS and saves roots, ``None`` without RetroDECK — or the refusal where one is not usable.

        A refusal also stands where they could not be established; the move
        code then moves nothing.
        """
        retrodeck = self._for_removal()
        if retrodeck is None or isinstance(retrodeck, FolderRefused):
            return retrodeck
        paths: list[str] = []
        for name in (ROOT_INSTALLATION, ROOT_BIOS, ROOT_SAVES):
            path = _root(retrodeck.roots, name)
            if path is None:
                return unanswered_refusal()
            if isinstance(path, FolderRefused):
                return path
            paths.append(os.path.realpath(path))
        home, bios, saves = paths
        return MoveRoots(home=home, bios=bios, saves=saves)

    def _removal_root(self, name: str) -> str | FolderRefused | None:
        """The root *name* a removal is bounded by, resolved; ``None`` where it has none or no RetroDECK is detected."""
        retrodeck = self._for_removal()
        if retrodeck is None or isinstance(retrodeck, FolderRefused):
            return retrodeck
        path = _root(retrodeck.roots, name)
        return path if path is None or isinstance(path, FolderRefused) else os.path.realpath(path)

    def _for_download(self, purpose: str) -> _Usable | FolderRefused:
        """RetroDECK as a download uses it: detected, switched on, its roots established, its home not missing."""
        retrodeck = self._retrodeck()
        if retrodeck is None:
            return not_installed(purpose)
        if retrodeck.switched_off:
            return switched_off(purpose)
        if retrodeck.roots is None:
            return unanswered_refusal()
        roots = retrodeck.roots
        # A download also stops while the home RetroDECK's settings name is
        # missing: everything it would land in lies below a folder that is not there.
        missing = next(
            (f for f in counted_findings(roots, roots.findings) if f.code == HEALTH_ISSUE_ROOT_MISSING), None
        )
        if missing is not None:
            return finding_refusal(missing.code, missing.data)
        return _Usable(installation=retrodeck.installation, roots=roots)

    def _for_removal(self) -> _Usable | FolderRefused | None:
        """RetroDECK as a removal uses it: detected, whatever its switch, its roots established."""
        retrodeck = self._retrodeck()
        if retrodeck is None:
            return None
        if retrodeck.roots is None:
            return unanswered_refusal()
        return _Usable(installation=retrodeck.installation, roots=retrodeck.roots)

    def _retrodeck(self) -> _RetroDeck | None:
        """RetroDECK in a fresh reading, its roots asked up front; ``None`` where it is not detected."""
        reading = self._sources.read()
        switched = reading.switched_off(RETRODECK)
        if reading.detection_failed:
            return _RetroDeck(installation=None, switched_off=switched, roots=None)
        installation = reading.installation(RETRODECK)
        if installation is None:
            return None
        try:
            roots = source_roots(self._ask(installation, "roots", lambda h: h.roots()))
        except _Unasked:
            roots = None
        return _RetroDeck(installation=installation, switched_off=switched, roots=roots)

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


def _removal_rom_root(retrodeck: _Usable) -> str | FolderRefused:
    """The ROM root a removal of installed content is bounded by, resolved — or why there is none."""
    roms = _root(retrodeck.roots, ROOT_ROMS)
    if isinstance(roms, FolderRefused):
        return roms
    return no_rom_root() if not roms else os.path.realpath(roms)


def _root(roots: SourceRoots, name: str) -> str | FolderRefused | None:
    """The root *name*'s path where it may be used, the refusal where a finding says why not, else ``None``."""
    root = roots.root(name)
    if root.usable:
        return root.path
    reason = unusable_reason(roots, name)
    return None if reason is None else finding_refusal(reason.code, reason.data)


def _inside(path: str, root: str) -> bool:
    """Whether the resolved *path* is *root* or lies below it."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
