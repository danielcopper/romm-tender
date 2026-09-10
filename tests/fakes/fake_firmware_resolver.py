"""The in-memory firmware seam for service tests — one platform's demand, stated."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from domain.firmware_wants import (
    DECLARED_DIRECTORY,
    DECLARED_FILE,
    CoreFirmwareVerdict,
    FirmwareCatalogue,
    FirmwarePlacement,
    FirmwareWant,
    FolderVerdict,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import EllipsisType


class FakeFirmwareResolver:
    """One platform's firmware demand, stated by the test instead of read off disk.

    Seed it with :meth:`declare` — one call per file, naming the emulators that
    require it and those that merely accept it, by IDENTITY: one string per
    emulator, standalone or libretro. ``unread_emulators`` names the emulators
    whose declaration could not be read, which is what decides whether a file the
    catalogue does not hold reads ``not_needed`` or ``unknown`` for a launch;
    ``resolved=False`` stands for a reading that never happened at all.

    It stands in for BOTH resolver seams, and the per-system one ignores the
    system it is asked about: a test states one platform's demand and the
    question's scope is not what it is pinning. ``calls`` records the systems it
    was asked about, in order, so a test CAN pin the scope where that is the
    point.

    ``bios_root`` and ``present_probe`` are what make the fake a stand-in for a
    resolver that reads a disk: given them, each placement's ``present`` is
    looked up under the root at call time, so a test that puts a file in its
    BIOS directory gets the same answer the real resolver would give for it.
    ``_make_firmware_service`` wires both to the service's own BIOS root and
    file store, because that is the production relationship — one directory,
    two readers. Without a root there is nowhere to take a reading, so a
    placement's ``present`` stays ``None`` — withheld, not absent, which is the
    distinction the consumers are built around — unless :meth:`declare` said
    otherwise, and a test pins a reading the files would not give by passing
    ``present`` there.

    :meth:`record_system` states the second axis — what the packaged table says
    about the console one emulator declares for. It is per emulator rather than
    per file because the same images read differently under two of them.
    """

    def __init__(
        self,
        *,
        placements: list[FirmwarePlacement] | None = None,
        unread_emulators: frozenset[str] = frozenset(),
        resolved: bool = True,
        caveats: tuple[str, ...] = (),
        bios_root: str | None = None,
        present_probe: Callable[[str], bool] = os.path.exists,
    ) -> None:
        self.placements: list[FirmwarePlacement] = list(placements or [])
        self.unread_emulators = unread_emulators
        self.resolved = resolved
        self.caveats = caveats
        self.bios_root = bios_root
        self.present_probe = present_probe
        self.emulator_verdicts: dict[str, CoreFirmwareVerdict] = {}
        self.calls: list[str] = []

    def record_system(
        self,
        emulator: str,
        *,
        system_firmware: str | None,
        requirements_met: bool | None = None,
    ) -> None:
        """State what the packaged table records about *emulator*'s console.

        An emulator no test records anything for is one the table holds no entry
        for, which is the ordinary case and the one that must change nothing.
        """
        self.emulator_verdicts[emulator] = CoreFirmwareVerdict(
            system_firmware=system_firmware, requirements_met=requirements_met
        )

    def declare(
        self,
        file_name: str,
        *,
        required_by: tuple[str, ...] | list[str] = (),
        optional_for: tuple[str, ...] | list[str] = (),
        relative_path: str | None | EllipsisType = ...,
        description: str | None = None,
        present: bool | None = None,
        declares_directory: bool = False,
        folder: FolderVerdict | None = None,
        caveats: tuple[str, ...] = (),
        supplied_by: str | None = None,
    ) -> FirmwarePlacement:
        """State that some emulators ask for *file_name*, and return the placement.

        ``relative_path`` left unset is the bare file name — the flat layout —
        so a test only spells it out when the subdirectory placement is the
        point. Passing ``None`` is the third state and a different one: there is
        no location under the firmware root to honour at all, which is what an
        emulator keeping its firmware in its own tree produces.

        ``present`` left unset defers to ``bios_root``; set it to pin a reading
        the files under that root would not give.

        ``declares_directory`` is what the EMULATOR opens the destination at, not
        what is there — a folder declaration whose folder is absent is still one.
        ``folder`` is the verdict about its contents; leaving it unset is the
        reading that established nothing about them, which is what a row nobody
        looked inside answers.
        """
        placement = FirmwarePlacement(
            file_name=file_name,
            relative_path=file_name if relative_path is ... else relative_path,
            description=description if description is not None else file_name,
            wants=tuple(
                [FirmwareWant(emulator=emulator, required=True) for emulator in required_by]
                + [FirmwareWant(emulator=emulator, required=False) for emulator in optional_for]
            ),
            present=present,
            declared_kind=DECLARED_DIRECTORY if declares_directory else DECLARED_FILE,
            caveats=caveats,
            folder=folder,
            supplied_by=supplied_by,
        )
        self.placements.append(placement)
        return placement

    def _read(self, placement: FirmwarePlacement) -> FirmwarePlacement:
        """The placement as the reading would answer it, looked up under ``bios_root``."""
        if placement.present is not None or not self.bios_root:
            return placement
        there = self.present_probe(os.path.join(self.bios_root, placement.destination))
        return FirmwarePlacement(
            file_name=placement.file_name,
            relative_path=placement.relative_path,
            description=placement.description,
            wants=placement.wants,
            present=there,
            declared_kind=placement.declared_kind,
            caveats=placement.caveats,
            folder=placement.folder,
            supplied_by=placement.supplied_by,
        )

    def __call__(self, system: str | None = None) -> FirmwareCatalogue:
        self.calls.append("" if system is None else system)
        return FirmwareCatalogue(
            placements=tuple(self._read(placement) for placement in self.placements),
            unread_emulators=self.unread_emulators,
            resolved=self.resolved,
            caveats=self.caveats,
            emulator_verdicts=dict(self.emulator_verdicts),
        )
