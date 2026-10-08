"""RetroDECK's folders as Tender may use them, and the refusal where it may not.

Every folder Tender uses in RetroDECK is the resolver's answer; this module
holds the refusals that stand in for a folder the answer cannot give, and the
value the move code's folders come in. Which question a folder answers, and
which rule decides it, is the adapter's (``adapters/retrodeck_folders.py``);
what each refusal says is decided here, once.

A download (a game, or a BIOS file) needs RetroDECK detected and switched on,
because until Tender keeps downloads in a library of its own, RetroDECK is the
one source Tender downloads into. Removing what Tender
put there — an uninstall, the removed-game cleanup, the start-up removal of
leftover ``.tmp`` files — needs it only detected, whatever its switch. Both
need its settings file in order: while RetroDECK reports one of the findings
that make its folders defaults, Tender uses none of them — nor where a question
about them failed, since then nothing established that they are not defaults.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from domain.refusal import DomainRefused

# The reasons a folder is refused with.
RETRODECK_NOT_INSTALLED = "retrodeck_not_installed"
RETRODECK_SWITCHED_OFF = "retrodeck_switched_off"
NO_ROM_FOLDER = "no_rom_folder"
NO_ROM_ROOT = "no_rom_root"
ROM_ROOT_MISSING = "rom_root_missing"
BIOS_FOLDER_MISSING = "bios_folder_missing"
RETRODECK_UNANSWERED = "retrodeck_unanswered"
# The panel words this one from the finding it carries, with the sentence the
# finding's notice shows, so the press and the notice say the same thing.
RETRODECK_FINDING = "retrodeck_finding"

# What a refused download is for, which names it in the sentence.
GAME_DOWNLOAD = "Downloads"
BIOS_DOWNLOAD = "BIOS downloads"


class FolderRefused(DomainRefused):
    """A folder Tender may not use, and why — handed back as a value and raised by the caller that needed it."""


class EveryFolderRefused(FolderRefused):
    """A folder refused because none of RetroDECK's folders may be used — a removal's bound no more than a download's.

    Its holders refuse every press that needs one of RetroDECK's folders, the
    start of the removed-game cleanup included, where a narrower refusal stops
    only the press it was asked for.
    """


class FindingRefused(EveryFolderRefused):
    """A folder refused because of a finding in RetroDECK's health, which the refusal carries as ``finding``."""


@dataclass(frozen=True, slots=True)
class MoveRoots:
    """RetroDECK's home, BIOS folder and saves root as the move code reads them."""

    home: str
    bios: str
    saves: str


def not_installed(purpose: str) -> FolderRefused:
    """The refusal of a *purpose* download where no RetroDECK is detected."""
    return FolderRefused(RETRODECK_NOT_INSTALLED, f"{purpose} need RetroDECK, which is not installed.")


def switched_off(purpose: str) -> FolderRefused:
    """The refusal of a *purpose* download while RetroDECK is switched off."""
    return FolderRefused(
        RETRODECK_SWITCHED_OFF, f"{purpose} need RetroDECK, which is switched off in Settings › Emulator sources."
    )


def no_rom_folder(system: str) -> FolderRefused:
    """The refusal of a download where RetroDECK names no folder for *system*."""
    return FolderRefused(
        NO_ROM_FOLDER,
        f"RetroDECK names no ROM folder for {system}, so Tender cannot download this game.",
        system=system,
    )


def rom_root_missing(path: str) -> FolderRefused:
    """The refusal of a download whose ROM folder lies below *path*, which does not exist and no finding names."""
    return FolderRefused(
        ROM_ROOT_MISSING,
        f"RetroDECK's ROM folder {path} does not exist. If it is on an SD card or another drive, insert it.",
        path=path,
    )


def bios_folder_missing(path: str) -> FolderRefused:
    """The refusal of a BIOS download while RetroDECK's BIOS folder *path*, outside its own folder, does not exist."""
    return FolderRefused(
        BIOS_FOLDER_MISSING,
        f"RetroDECK's BIOS folder {path} does not exist. If it is on an SD card or another drive, insert it.",
        path=path,
    )


def uninstall_not_installed() -> FolderRefused:
    """The refusal of a removal of installed content where no RetroDECK is detected to bound it."""
    return FolderRefused(RETRODECK_NOT_INSTALLED, "Uninstalling needs RetroDECK, which is not installed.")


def moving_not_installed() -> FolderRefused:
    """The refusal of the move code's migrate press where no RetroDECK is detected."""
    return FolderRefused(RETRODECK_NOT_INSTALLED, "Moving needs RetroDECK, which is not installed.")


def no_rom_root() -> FolderRefused:
    """The refusal of a removal of installed content where RetroDECK names no ROM folder to bound it."""
    return FolderRefused(NO_ROM_ROOT, "RetroDECK names no ROM folder, so Tender cannot uninstall this game.")


def unanswered_refusal() -> EveryFolderRefused:
    """The refusal where a question about RetroDECK or one of its folders failed, so nothing established them."""
    return EveryFolderRefused(
        RETRODECK_UNANSWERED,
        "RetroDECK's folders could not be established, so Tender downloads into and removes from none of them.",
    )


def system_unanswered_refusal() -> FolderRefused:
    """The refusal where the question about one system's own ROM folder failed, which stops only what needs that folder.

    Its sentence is the one every folder refused for a failed question answers with.
    """
    return FolderRefused(RETRODECK_UNANSWERED, unanswered_refusal().message)


def folder_of(folders: dict[str, str | FolderRefused] | FolderRefused, system: str) -> str | FolderRefused:
    """*system*'s folder out of an answer about several systems' folders, or the refusal that stands for it."""
    return folders if isinstance(folders, FolderRefused) else folders[system]


def finding_refusal(code: str, data: dict[str, Any]) -> FindingRefused:
    """The refusal while RetroDECK's finding *code* stands in the way of its folders.

    The message is the wording a finding falls back to where the panel has none
    of its own; the panel words the refusal from ``finding`` instead.
    """
    return FindingRefused(
        RETRODECK_FINDING,
        f"Problem with RetroDECK: {code}",
        finding={"code": code, "data": data},
    )
