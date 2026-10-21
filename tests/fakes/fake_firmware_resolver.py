"""The in-memory firmware seam for service tests — one platform's demand, stated."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from domain.firmware_wants import (
    DECLARED_DIRECTORY,
    DECLARED_FILE,
    CoreFirmwareVerdict,
    FirmwareCatalogue,
    FirmwareGroup,
    FirmwareOption,
    FirmwarePlacement,
    FirmwareWant,
    FolderFile,
    FolderVerdict,
    MissingConfiguredImage,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import EllipsisType

    from domain.source_roots import SourceRoots


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

    :meth:`state_group` states a one-of group. It declares nothing on its own: a
    real reading lists every option's file as a placement too, and a test that
    wants the rows declares them beside it.

    **The resolver's verdicts are simulated, the way it reaches them.** A
    placement's ``satisfied`` follows its ``present``, ``checked``, kind and
    folder verdict as the resolver's per-file verdict does, unless
    :meth:`declare` pins it. Each emulator the fake knows of — one a want, a
    group or :meth:`describe_emulator` names — gets an entry whose
    ``requirements_met`` folds its own required placements and groups the way
    the resolver's does (any not met → ``False``, else any not established →
    ``None``, else ``True``), unless a test pins it with :meth:`record_system`.
    An emulator in ``unread_emulators`` answers ``None``; one the fake never
    heard of has no entry at all, which is an emulator missing from the answer.

    A placement's ``place`` is the declared location joined under
    ``bios_root``, unless :meth:`declare` names one. ``source`` and ``roots``
    are what the catalogue says it was read from; :meth:`every_source` answers
    this one catalogue followed by each of ``other_sources``, a test's further
    emulator sources, and :meth:`source_roots` every source's roots.
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
        source: str | None = "retrodeck",
        roots: SourceRoots | None = None,
    ) -> None:
        self.placements: list[FirmwarePlacement] = list(placements or [])
        self.source = source
        self.roots = roots
        self.other_sources: list[FakeFirmwareResolver] = []
        # What :meth:`declare` pinned as each placement's verdict, by its index.
        self._declared_satisfied: dict[tuple[str, str | None, int], bool | None | EllipsisType] = {}
        self.unread_emulators = unread_emulators
        self.resolved = resolved
        self.caveats = caveats
        self.bios_root = bios_root
        self.present_probe = present_probe
        self.emulator_verdicts: dict[str, CoreFirmwareVerdict] = {}
        # The verdicts a test pinned rather than left to be folded.
        self._pinned_met: set[str] = set()
        self.groups: list[FirmwareGroup] = []
        self.calls: list[str] = []

    def state_group(
        self,
        emulator: str,
        *options: tuple[str, tuple[str, ...], bool | None],
        unchecked_regions: tuple[str, ...] = (),
        absent_regions: tuple[str, ...] = (),
        unstated_regions: tuple[str, ...] = (),
    ) -> FirmwareGroup:
        """State that *emulator* needs one of *options* — ``(file name, regions, satisfied)`` each.

        The group's own verdict is the resolver's lift over its options: every
        option met, every one not met, or neither.
        """
        verdicts = {satisfied for _name, _regions, satisfied in options}
        group = FirmwareGroup(
            emulator=emulator,
            options=tuple(FirmwareOption(name, regions, satisfied) for name, regions, satisfied in options),
            unchecked_regions=unchecked_regions,
            absent_regions=absent_regions,
            satisfied=True if verdicts == {True} else False if verdicts == {False} else None,
            unstated_regions=unstated_regions,
        )
        self.groups.append(group)
        return group

    def record_system(
        self,
        emulator: str,
        *,
        system_firmware: str | None,
        requirements_met: bool | None = None,
    ) -> None:
        """State what the packaged table records about *emulator*'s console, and pin its verdict.

        The verdict is pinned as given — the resolver weighs the console's need
        into it, which the fold over the rows cannot.
        """
        known = self.emulator_verdicts.get(emulator, CoreFirmwareVerdict(declaration="read"))
        self.emulator_verdicts[emulator] = replace(
            known,
            system_firmware=system_firmware,
            requirements_met=requirements_met,
            world_knowledge=system_firmware is not None,
        )
        self._pinned_met.add(emulator)

    def describe_emulator(self, emulator: str, **fields: Any) -> None:
        """State what *emulator*'s own entry says beyond its files — any :class:`CoreFirmwareVerdict` field.

        Naming an emulator here is also how a test says it was read and
        declares nothing. Passing ``requirements_met`` pins the verdict.
        """
        known = self.emulator_verdicts.get(emulator, CoreFirmwareVerdict(declaration="read"))
        self.emulator_verdicts[emulator] = replace(known, **fields)
        if "requirements_met" in fields:
            self._pinned_met.add(emulator)

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
        checked: str | None = None,
        missing_configured_image: MissingConfiguredImage | None = None,
        satisfied: bool | None | EllipsisType = ...,
        place: str | None = None,
        identity_md5: str | None = None,
        identity_kind: str | None = None,
        folder_md5s: frozenset[str] = frozenset(),
        folder_files: tuple[FolderFile, ...] = (),
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

        ``checked`` is what became of the bytes, in the resolver's own words.
        Unset is the ordinary answer — a reading that asked no byte question —
        and a test spells it out only where what was DONE to the file is what it
        is pinning, which ``present`` cannot say.

        One placement is stated per declaring emulator, each that emulator's own
        requirement at the same place, as the resolver states two emulators that
        open one file; the first is returned. A test stating one name at two
        places declares it twice. ``satisfied`` left unset is the resolver's own
        per-file verdict over what the reading found (:meth:`_read`).
        """
        owners: list[str | None] = [*required_by, *optional_for] or [None]
        for owner in owners:
            self._declared_satisfied[(file_name, owner, len(self.placements))] = satisfied
            self.placements.append(
                self._placement(
                    file_name,
                    owner,
                    required_by=required_by,
                    optional_for=optional_for,
                    relative_path=relative_path,
                    description=description,
                    present=present,
                    declares_directory=declares_directory,
                    folder=folder,
                    caveats=caveats,
                    supplied_by=supplied_by,
                    checked=checked,
                    missing_configured_image=missing_configured_image,
                    place=place,
                    identity_md5=identity_md5,
                    identity_kind=identity_kind,
                    folder_md5s=folder_md5s,
                    folder_files=folder_files,
                )
            )
        return self.placements[-len(owners)]

    @staticmethod
    def _placement(
        file_name: str,
        owner: str | None,
        *,
        required_by: tuple[str, ...] | list[str],
        optional_for: tuple[str, ...] | list[str],
        relative_path: str | None | EllipsisType,
        description: str | None,
        present: bool | None,
        declares_directory: bool,
        folder: FolderVerdict | None,
        caveats: tuple[str, ...],
        supplied_by: str | None,
        checked: str | None,
        missing_configured_image: MissingConfiguredImage | None,
        place: str | None,
        identity_md5: str | None,
        identity_kind: str | None,
        folder_md5s: frozenset[str],
        folder_files: tuple[FolderFile, ...],
    ) -> FirmwarePlacement:
        """One emulator's placement as :meth:`declare` states it."""
        return FirmwarePlacement(
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
            checked=checked,
            missing_configured_image=missing_configured_image,
            emulator=owner,
            place=place,
            identity_md5=identity_md5,
            identity_kind=identity_kind,
            folder_md5s=folder_md5s,
            folder_files=folder_files,
        )

    def _read(self, index: int, placement: FirmwarePlacement) -> FirmwarePlacement:
        """The placement as the reading would answer it, looked up under ``bios_root``.

        ``replace`` rather than a fresh construction: this changes the read
        fields only, and a field-by-field copy silently drops whatever the
        placement grows next.
        """
        place = placement.place
        if place is None and self.bios_root and placement.relative_path is not None:
            place = os.path.join(self.bios_root, placement.destination)
        present = placement.present
        if present is None and place is not None:
            present = self.present_probe(place)
        pinned = self._declared_satisfied.get((placement.file_name, placement.emulator, index), ...)
        satisfied = _verdict_over(placement, present) if pinned is ... else pinned
        return replace(placement, present=present, satisfied=satisfied, place=place)

    def _verdicts(self, placements: tuple[FirmwarePlacement, ...]) -> dict[str, CoreFirmwareVerdict]:
        """Every emulator the fake knows of, with the verdict the resolver would fold for it."""
        known = dict.fromkeys(
            [
                *(want.emulator for placement in placements for want in placement.wants if want.emulator),
                *(group.emulator for group in self.groups),
                *self.emulator_verdicts,
            ]
        )
        verdicts: dict[str, CoreFirmwareVerdict] = {}
        for emulator in known:
            stated = self.emulator_verdicts.get(emulator, CoreFirmwareVerdict(declaration="read"))
            if emulator in self._pinned_met:
                verdicts[emulator] = stated
            elif emulator in self.unread_emulators:
                verdicts[emulator] = replace(stated, requirements_met=None, declaration="unreadable")
            else:
                verdicts[emulator] = replace(
                    stated,
                    requirements_met=self._fold(emulator, placements),
                    undetermined=stated.undetermined or self._plain_required(emulator, placements, None),
                    unmet=stated.unmet or self._plain_required(emulator, placements, False),
                )
        return verdicts

    @staticmethod
    def _plain_required(
        emulator: str, placements: tuple[FirmwarePlacement, ...], satisfied: bool | None
    ) -> tuple[str, ...]:
        """*emulator*'s required placements whose verdict is *satisfied*, as the resolver names them."""
        return tuple(
            placement.file_name
            for placement in placements
            if placement.emulator == emulator
            and placement.satisfied is satisfied
            and any(want.emulator == emulator and want.required for want in placement.wants)
        )

    def _fold(self, emulator: str, placements: tuple[FirmwarePlacement, ...]) -> bool | None:
        """``requirements_met`` over *emulator*'s required placements and its groups."""
        verdicts = [
            placement.satisfied
            for placement in placements
            if placement.emulator == emulator
            and any(want.emulator == emulator and want.required for want in placement.wants)
        ]
        verdicts += [group.satisfied for group in self.groups if group.emulator == emulator]
        if any(verdict is False for verdict in verdicts):
            return False
        return None if any(verdict is None for verdict in verdicts) else True

    def __call__(self, system: str | None = None) -> FirmwareCatalogue:
        self.calls.append("" if system is None else system)
        placements = tuple(self._read(index, placement) for index, placement in enumerate(self.placements))
        return FirmwareCatalogue(
            placements=placements,
            unread_emulators=self.unread_emulators,
            resolved=self.resolved,
            caveats=self.caveats,
            emulator_verdicts=self._verdicts(placements) if self.resolved else {},
            groups=tuple(self.groups),
            source=self.source if self.resolved else None,
            roots=self.roots if self.resolved else None,
        )

    def every_source(self, system: str) -> tuple[FirmwareCatalogue, ...]:
        """This catalogue and each further source's, leaving out a reading that did not happen."""
        catalogues = (self(system), *(other(system) for other in self.other_sources))
        return tuple(catalogue for catalogue in catalogues if catalogue.resolved)

    def source_roots(self) -> dict[str, SourceRoots | None]:
        """Every source's roots by kind."""
        return {
            resolver.source: resolver.roots for resolver in (self, *self.other_sources) if resolver.source is not None
        }


# What comparing the bytes established, where it settles the verdict either way.
_NOT_MET = frozenset({"mismatch", "refused"})
_NOT_ESTABLISHED = frozenset({"unchecked", "not-comparable", "unrecognised", "unread"})


def _verdict_over(placement: FirmwarePlacement, present: bool | None) -> bool | None:
    """The resolver's per-file verdict over what the reading found at the placement."""
    if present is None:
        return None
    if present is False:
        return False
    if placement.declares_directory:
        return placement.folder.satisfied if placement.folder is not None else None
    if "firmware-path-obstructed" in placement.caveats:
        return None
    if placement.checked in _NOT_MET:
        return False
    return None if placement.checked in _NOT_ESTABLISHED else True
