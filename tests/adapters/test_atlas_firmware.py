"""Tests for the atlas firmware adapters — the translation into Tender's vocabulary.

What is under test is the adapters' own work: folding the resolver's per-entry
answer into one row per file, keying everything on the emulator IDENTITY rather
than on a core file or a display label, naming the emulators that could not be
asked, deciding whether a declared location is one Tender's own BIOS root can
honour, and refusing to turn any failure into "nothing needed". The resolver's
own decisions are upstream's and are not re-tested here — including what it read
at a destination, which is carried through rather than re-derived.

Answers are built from real atlas value objects rather than mocks, so every
invariant the resolver enforces on its own shapes is enforced on the fixtures
too — a fixture that could not come off a real machine fails to construct.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from _standalone_firmware import SYSTEMS, lay_down_standalone_emulators
from _vendor.atlas import (
    CAVEAT_CORE_FILE_FOREIGN,
    CAVEAT_CORE_MODE_UNESTABLISHED,
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,
    CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE,
    CAVEAT_FIRMWARE_IDENTITY_NOT_COMPARABLE,
    CAVEAT_FIRMWARE_IMAGE_IDENTIFIED,
    CAVEAT_FIRMWARE_IMAGE_UNLISTED,
    CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
    CAVEAT_FIRMWARE_PATH_OBSTRUCTED,
    CAVEAT_FIRMWARE_SCAN_INCOMPLETE,
    CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,
    HEALTH_ISSUE_NOT_SET_UP,
)
from _vendor.atlas import LOCATING_BY_NAME as ATLAS_LOCATING_BY_NAME
from _vendor.atlas import LOCATING_BY_NAME_THEN_CONTENT as ATLAS_LOCATING_BY_NAME_THEN_CONTENT
from _vendor.atlas import LOCATING_UNESTABLISHED as ATLAS_LOCATING_UNESTABLISHED
from _vendor.atlas.firmware import CAVEAT_FIRMWARE_PATH_INACCESSIBLE as ATLAS_CAVEAT_PATH_INACCESSIBLE
from _vendor.atlas.firmware import CHECKED_UNREAD as ATLAS_CHECKED_UNREAD
from _vendor.atlas.firmware import CHECKED_UNRECOGNISED as ATLAS_CHECKED_UNRECOGNISED
from _vendor.atlas.firmware import (
    CORE_SYSTEM_FIRMWARE_STATES,
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_OPEN,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    CoreDeclarationState,
    CoreFirmware,
    CoreSystemFirmware,
    DeclaredKind,
    FirmwareAlternatives,
    FirmwareAnswer,
    FirmwareChecked,
    FirmwareNeed,
    FirmwareRequirement,
    RefusedDeclaration,
    SuppliedBy,
)
from _vendor.atlas.firmware import DECLARED_DIRECTORY as ATLAS_DECLARED_DIRECTORY
from _vendor.atlas.firmware import DECLARED_FILE as ATLAS_DECLARED_FILE
from _vendor.atlas.machine import KIND_DIRECTORY, KIND_FILE, KIND_INACCESSIBLE, KIND_MISSING, PathKind
from _vendor.atlas.placement import Caveat

from adapters.atlas_firmware import _BIOS_CHOICE_SETTING, AtlasFirmwareAdapter, AtlasPlatformFirmwareAdapter
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.firmware_wants import (
    CAVEAT_PATH_INACCESSIBLE,
    CAVEAT_SCAN_INCOMPLETE,
    CAVEATS_IDENTIFIED_BY_CONTENT,
    CHECKED_UNREAD,
    CHECKED_UNRECOGNISED,
    DECLARED_DIRECTORY,
    DECLARED_FILE,
    LOCATING_BY_NAME,
    LOCATING_BY_NAME_THEN_CONTENT,
    LOCATING_UNESTABLISHED,
    SYSTEM_FIRMWARE_STATES,
    WANTED_UNKNOWN,
    FirmwareWant,
    MissingConfiguredImage,
    classify_wanted,
)

if TYPE_CHECKING:
    from types import EllipsisType

_ROOT = "/home/deck/retrodeck/bios"

# The system every requirement here is filed under. One spelling, because the
# resolver holds a core's stated system need to the systems its own requirements
# name, so the two builders below have to agree about it.
_REQUIREMENT_SYSTEM = "gba"


def _requirement(
    *,
    core_so: str | None = "mgba_libretro.so",
    file_name: str = "gba_bios.bin",
    path: str | None = None,
    declared: str | None = None,
    need: FirmwareNeed = "required",
    description: str = "",
    regions: tuple[str, ...] | None = None,
    found: PathKind = KIND_MISSING,
    checked: FirmwareChecked | None = None,
    supplied_by: SuppliedBy | None = None,
    declared_kind: DeclaredKind = ATLAS_DECLARED_FILE,
    contents_satisfied: bool | None = None,
) -> FirmwareRequirement:
    """One (core, declared file) pair, defaulting to a flat destination with nothing there.

    ``declared`` and ``path`` default to the same flat spelling under the root
    because that is what the overwhelming majority of declarations look like;
    a test spells them apart when the divergence between the two IS the case
    under test.
    """
    return FirmwareRequirement(
        core_so=core_so,
        system=_REQUIREMENT_SYSTEM,
        system_source="systemname",
        need=need,
        file_name=file_name,
        path=path if path is not None else f"{_ROOT}/{file_name}",
        declared=declared if declared is not None else file_name,
        description=description or f"{file_name} (BIOS)",
        identity=None,
        found=found,
        checked=checked,
        regions=regions,
        supplied_by=supplied_by,
        declared_kind=declared_kind,
        contents_satisfied=contents_satisfied,
    )


def _core(
    *,
    core_so: str | None = "mgba_libretro.so",
    emulator: str | None | EllipsisType = ...,
    label: str | None = None,
    declared_index: int | None = None,
    declaration: CoreDeclarationState = "read",
    requirements: tuple[FirmwareRequirement | FirmwareAlternatives, ...] = (),
    caveats: tuple[Caveat, ...] = (),
    refused: tuple[RefusedDeclaration, ...] = (),
    system_firmware: CoreSystemFirmware | None = None,
) -> CoreFirmware:
    """One entry of an answer, identified the way the resolver identifies a libretro one.

    ``emulator`` left unset is the core file's own basename, which is what the
    resolver states for every libretro entry. A test spells it out for a
    standalone emulator, and passes ``None`` for the entry the resolver could not
    identify at all.

    ``system_firmware_needs`` is derived rather than taken, like the caveats
    above: the resolver requires ``cannot-run-without-firmware`` to name the
    systems it is about, and every id there came off a requirement of the same
    entry — which for these doubles is :func:`_requirement`'s own ``"gba"``.
    Nothing in this module reads the field, so a test that wanted a different
    id would be testing the resolver rather than the adapter.
    """
    if declaration != "read" and not caveats:
        caveats = (Caveat(code="core-info-unreadable", message="its .info could not be read"),)
    if refused and not caveats:
        caveats = (Caveat(code="firmware-declaration-leaves-root", message="leaves the root"),)
    needs = (_REQUIREMENT_SYSTEM,) if system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT else ()
    return CoreFirmware(
        core_so=core_so,
        label=label,
        emulator=core_so if emulator is ... else emulator,
        declared_index=declared_index,
        declaration=declaration,
        requirements=requirements,
        caveats=caveats,
        refused=refused,
        system_firmware=system_firmware,
        system_firmware_needs=needs,
    )


def _answer(
    *cores: CoreFirmware,
    root: str | None = _ROOT,
    caveats: tuple[Caveat, ...] = (),
) -> FirmwareAnswer:
    if root is None and not caveats:
        caveats = (Caveat(code="firmware-root-unstated", message="no system directory"),)
    return FirmwareAnswer(
        root=root,
        cores=cores,
        unclaimed=(),
        hash_checked=False,
        sources=(),
        caveats=caveats,
    )


class _Installation:
    """Stand-in for a detected installation handle — answers one prepared reading."""

    def __init__(self, answer: FirmwareAnswer | Exception, *, kind: str = "retrodeck") -> None:
        self.kind = kind
        self._answer = answer
        self.asked_for: tuple[str, bool] | None = None

    def firmware_inventory(self, *, verify: bool = False) -> FirmwareAnswer:
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer

    def firmware_for_system(self, system: str, *, verify: bool = False) -> FirmwareAnswer:
        self.asked_for = (system, verify)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


@pytest.fixture
def traces() -> list[str]:
    return []


def _detecting(*installations):
    def detect(home, machine=None):
        return list(installations)

    return detect


class _Detection:
    """The detection every adapter in this module's fixtures is handed; a test points it with :func:`_detect`."""

    def __init__(self) -> None:
        self.detect = _detecting()

    def __call__(self, home: str, machine: Any) -> list[Any]:
        return self.detect(home, machine)


_DETECTION = _Detection()


def _detect(monkeypatch, detect) -> None:
    """Make every reading this test takes detect what *detect* answers; undone at teardown."""
    monkeypatch.setattr(_DETECTION, "detect", detect)


def _sources(traces: list[str], settings: dict[str, Any] | None = None) -> EmulatorSourcesAdapter:
    return EmulatorSourcesAdapter(
        user_home="/home/deck",
        settings=settings if settings is not None else {},
        log_debug=traces.append,
        detect_installations=_DETECTION,
        machine=object(),
    )


@pytest.fixture
def adapter(traces):
    return AtlasFirmwareAdapter(sources=_sources(traces), log_debug=traces.append)


def catalogue_emulators(catalogue) -> set[str | None]:
    return {want.emulator for placement in catalogue.placements for want in placement.wants}


class TestPlacements:
    def test_each_core_that_declares_a_file_has_a_placement_of_its_own(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so="mednafen_psx_libretro.so", requirements=(_requirement(file_name="scph5501.bin"),)),
            _core(
                core_so="swanstation_libretro.so",
                requirements=(_requirement(file_name="scph5501.bin", need="optional"),),
            ),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        catalogue = adapter()

        assert [(p.file_name, p.emulator) for p in catalogue.placements] == [
            ("scph5501.bin", "mednafen_psx_libretro.so"),
            ("scph5501.bin", "swanstation_libretro.so"),
        ]
        for placement in catalogue.placements:
            assert {(w.emulator, w.required) for w in placement.wants} == {
                ("mednafen_psx_libretro.so", True),
                ("swanstation_libretro.so", False),
            }
            assert placement.required_by_any is True

    def test_the_identifier_is_the_resolvers_own_spelling(self, adapter, monkeypatch):
        """One identity per emulator, stated by the resolver and never rewritten here.

        Tender used to strip the ``.so`` and key on the bare basename, which
        left a standalone emulator with no name at all. The identity is now the
        one field that carries both kinds, so it travels verbatim.
        """
        answer = _answer(_core(core_so="mgba_libretro.so", requirements=(_requirement(),)))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert catalogue_emulators(adapter()) == {"mgba_libretro.so"}

    def test_a_standalone_emulator_owns_its_declaration(self, adapter, monkeypatch):
        """No ``core_so``, and still an owner: the identity is what names it."""
        answer = _answer(
            _core(
                core_so=None,
                emulator="DUCKSTATION",
                label="DuckStation (Standalone)",
                declaration="packaged",
                requirements=(_requirement(core_so=None, file_name="scph5501.bin"),),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        catalogue = adapter()

        assert catalogue_emulators(catalogue) == {"DUCKSTATION"}
        assert catalogue.placements[0].required_by_any is True

    def test_one_emulator_under_two_catalogue_rows_is_one_owner(self, adapter, monkeypatch):
        """ES-DE lists ``pcsx2_libretro.so`` as both ``LRPS2`` and ``PCSX2``.

        Two rows, one emulator, one declaration read twice — so the file's
        declaring set must carry it once, or every count over that set is off by
        the number of labels the frontend happens to use.
        """
        answer = _answer(
            _core(
                core_so="pcsx2_libretro.so",
                label="LRPS2",
                declared_index=1,
                requirements=(_requirement(core_so="pcsx2_libretro.so", file_name="GameIndex.yaml"),),
            ),
            _core(
                core_so="pcsx2_libretro.so",
                label="PCSX2",
                declared_index=2,
                requirements=(_requirement(core_so="pcsx2_libretro.so", file_name="GameIndex.yaml"),),
            ),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        placement = adapter().placements[0]

        assert [(w.emulator, w.required) for w in placement.wants] == [("pcsx2_libretro.so", True)]

    def test_a_rows_declaration_is_the_one_the_description_came_from(self, adapter, monkeypatch):
        """The register a row's prose is written in, read off the entry that wrote it.

        Two emulators declare one PlayStation image and their descriptions are
        two kinds of writing: a packaged card explains the requirement in whole
        sentences, a libretro ``.info`` labels the file. The placement keeps one
        description — the first pair's — so it has to keep that pair's word for
        how it was written; taking the word off any other declaring entry would
        describe prose this row does not carry.
        """
        packaged_prose = (
            "a PlayStation BIOS image — the console runs it before any disc, and DuckStation starts "
            "nothing without one — found by the search, not named by any setting"
        )
        answer = _answer(
            _core(
                core_so=None,
                emulator="DUCKSTATION",
                declaration="packaged",
                requirements=(_requirement(core_so=None, file_name="scph1001.bin", description=packaged_prose),),
            ),
            _core(
                core_so="swanstation_libretro.so",
                requirements=(
                    _requirement(
                        core_so="swanstation_libretro.so",
                        file_name="scph1001.bin",
                        description="scph1001.bin (PS1 US BIOS)",
                    ),
                ),
            ),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        placement = adapter().placements[0]

        assert placement.description == packaged_prose
        assert placement.declaration == "packaged"

    def test_a_rows_bytes_answer_comes_from_the_entry_the_description_did(self, adapter, monkeypatch):
        """``checked`` rides with the destination half, off the same pair as the description.

        Two emulators declare one image and each entry carries its own reading of
        the bytes at it. The placement describes ONE destination, so it keeps one
        answer, and it has to be the first pair's — the same rule the description
        and its declaration follow. Taking a later entry's would report a read of
        a place this row does not describe.
        """
        answer = _answer(
            _core(
                core_so=None,
                emulator="DUCKSTATION",
                declaration="packaged",
                requirements=(
                    _requirement(core_so=None, file_name="scph1001.bin", found=KIND_FILE, checked="unrecognised"),
                ),
            ),
            _core(
                core_so="swanstation_libretro.so",
                requirements=(
                    _requirement(
                        core_so="swanstation_libretro.so",
                        file_name="scph1001.bin",
                        found=KIND_FILE,
                        checked="verified",
                    ),
                ),
            ),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].checked == "unrecognised"

    def test_a_row_whose_reading_asked_no_byte_question_carries_no_answer(self, adapter, monkeypatch):
        """``None`` is the ordinary answer, and it is carried as such.

        A file that is simply absent has no bytes to read, and the resolver says
        so by answering nothing. That is not a value to invent a word for.
        """
        answer = _answer(_core(requirements=(_requirement(),)))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].checked is None

    def test_a_declaration_with_no_location_under_the_root_carries_no_byte_answer(self, adapter, monkeypatch):
        """The destination half goes silent together, and this is part of it.

        A standalone emulator's own XDG tree holding the file says nothing about
        the BIOS root the caller writes to, so the reading is dropped rather than
        travelling on to describe a place the caller will never look at.
        """
        answer = _answer(
            _core(
                requirements=(
                    _requirement(
                        declared="/opt/elsewhere/gba_bios.bin",
                        path="/opt/elsewhere/gba_bios.bin",
                        found=KIND_FILE,
                        checked="verified",
                    ),
                )
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        placement = adapter().placements[0]
        assert placement.relative_path is None
        assert placement.checked is None

    def test_an_entry_the_resolver_could_not_identify_still_owns_its_file(self, adapter, monkeypatch):
        """EmuDeck's ``n3ds`` rows: a launch atlas classifies standalone and cannot name.

        The file is genuinely declared, so dropping the want would understate the
        demand — but nothing can be scoped to an entry with no name, so the want
        carries none and every identity-keyed reading passes it over.
        """
        answer = _answer(
            _core(core_so=None, emulator=None, declaration="packaged", requirements=(_requirement(core_so=None),))
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        catalogue = adapter()

        assert catalogue_emulators(catalogue) == {None}
        assert catalogue.placements[0].required_by_any is True
        assert catalogue.unread_emulators == frozenset()

    def test_a_subdirectory_destination_survives_as_a_relative_placement(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so="dolphin_libretro.so",
                requirements=(
                    _requirement(
                        file_name="codehandler.bin",
                        declared="dolphin-emu/Sys/codehandler.bin",
                        path=f"{_ROOT}/dolphin-emu/Sys/codehandler.bin",
                    ),
                ),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path == "dolphin-emu/Sys/codehandler.bin"

    def test_a_destination_outside_the_root_has_no_placement_to_honour(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so="melonds_libretro.so",
                requirements=(_requirement(file_name="bios7.bin", path="/home/deck/.local/share/melonDS/bios7.bin"),),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path is None

    def test_a_declaration_the_root_symlinks_onto_itself_keeps_its_subdirectory(self, adapter, monkeypatch):
        """LRPS2 on RetroDECK: ``pcsx2/bios`` is a link back to the BIOS root.

        The resolved destination collapses onto the root, so deriving the
        location from it yields ``.`` and loses the folder the emulator will
        actually open. What the emulator spelled survives that.
        """
        answer = _answer(
            _core(
                core_so="pcsx2_libretro.so",
                requirements=(_requirement(file_name="bios", declared="pcsx2/bios", path=_ROOT),),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path == "pcsx2/bios"

    def test_an_absolute_declaration_has_no_placement_under_our_root(self, adapter, monkeypatch):
        """A location stated as an address is not one Tender can join under its own root."""
        answer = _answer(
            _core(
                core_so="duckstation_libretro.so",
                requirements=(
                    _requirement(file_name="scph5501.bin", declared=f"{_ROOT}/scph5501.bin", path=f"{_ROOT}/x.bin"),
                ),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path is None

    def test_a_declaration_that_climbs_out_of_the_root_has_no_placement_under_it(self, adapter, monkeypatch):
        """A relative spelling can escape too, and the resolved path does not show it.

        ``pcsx2/../..`` names the root's grandparent while the resolved
        destination stays inside — RetroDECK's link makes the two disagree — so
        the declaration is checked on its own terms rather than trusted for
        having landed somewhere acceptable.
        """
        answer = _answer(
            _core(
                core_so="pcsx2_libretro.so",
                requirements=(_requirement(file_name="bios", declared="pcsx2/../..", path=_ROOT),),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path is None

    def test_a_file_an_option_names_inside_a_folder_goes_in_that_folder_under_its_own_name(self, adapter, monkeypatch):
        """LRPS2 with ``pcsx2_bios`` set: the declaration stays the folder, the file is the setting's.

        The resolver keeps ``declared`` as the ``.info``'s folder path and states
        the requirement as the FILE the option names. Placed at the declaration,
        a download of that file would be written where the folder is.
        """
        answer = _answer(
            _core(
                core_so="pcsx2_libretro.so",
                requirements=(
                    _requirement(
                        core_so="pcsx2_libretro.so",
                        file_name="scph10000.bin",
                        declared="pcsx2/bios",
                        path=f"{_ROOT}/scph10000.bin",
                        found=KIND_FILE,
                        checked="verified",
                    ),
                ),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        placement = adapter().placements[0]
        assert placement.relative_path == "pcsx2/bios/scph10000.bin"
        assert placement.destination == "pcsx2/bios/scph10000.bin"
        assert placement.declared_kind == DECLARED_FILE

    def test_a_spelling_the_core_reached_belongs_where_its_info_names_the_image(self, adapter, monkeypatch):
        """Beetle PSX tries several spellings of one image and names the one it reached.

        ``file_name`` is that spelling and ``declared`` the ``.info``'s. The two
        differ and neither is a folder, so the image belongs at the declared
        name — joining them would make the declared file a folder.
        """
        answer = _answer(
            _core(
                core_so="mednafen_psx_libretro.so",
                requirements=(
                    FirmwareAlternatives(
                        options=(
                            _option(
                                "SCPH-5501.bin",
                                ("ntsc-u",),
                                declared="scph5501.bin",
                                path=f"{_ROOT}/SCPH-5501.bin",
                                found=KIND_FILE,
                                checked="unchecked",
                            ),
                        )
                    ),
                ),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path == "scph5501.bin"


class TestDestinationReadings:
    """What the resolver read AT the destination, carried instead of re-derived."""

    def _placement(self, adapter, monkeypatch, requirement):
        answer = _answer(_core(core_so="mgba_libretro.so", requirements=(requirement,)))
        _detect(monkeypatch, _detecting(_Installation(answer)))
        return adapter().placements[0]

    def test_a_file_at_the_destination_is_present(self, adapter, monkeypatch):
        placement = self._placement(adapter, monkeypatch, _requirement(found=KIND_FILE, checked="unchecked"))

        assert placement.present is True
        assert placement.declared_kind == DECLARED_FILE
        assert placement.folder is None

    def test_nothing_at_the_destination_is_absent(self, adapter, monkeypatch):
        assert self._placement(adapter, monkeypatch, _requirement()).present is False

    def test_a_destination_that_could_not_be_looked_at_is_neither(self, adapter, monkeypatch):
        """ "Could not look" is not "not there", and the placement keeps them apart."""
        placement = self._placement(adapter, monkeypatch, _requirement(found=KIND_INACCESSIBLE))

        assert placement.present is None

    def test_a_directory_a_core_declares_is_named_by_the_declaration(self, adapter, monkeypatch):
        """The kind is what the core OPENS the path at, never what was found there."""
        placement = self._placement(
            adapter,
            monkeypatch,
            _requirement(
                file_name="bios",
                declared="pcsx2/bios",
                found=KIND_DIRECTORY,
                checked="unchecked",
                declared_kind=ATLAS_DECLARED_DIRECTORY,
            ),
        )

        assert placement.declared_kind == DECLARED_DIRECTORY
        assert placement.present is True

    def test_the_supplying_distribution_travels_as_the_resolver_writes_it(self, adapter, monkeypatch):
        """The resolver's own display form — Tender never maps an identifier itself."""
        placement = self._placement(
            adapter,
            monkeypatch,
            _requirement(
                file_name="codehandler.bin",
                declared="dolphin-emu/Sys/codehandler.bin",
                path=f"{_ROOT}/dolphin-emu/Sys/codehandler.bin",
                found=KIND_FILE,
                checked="unchecked",
                supplied_by=SuppliedBy(distribution="retrodeck", source="/app/retrodeck/x", card_version="1"),
            ),
        )

        assert placement.supplied_by == "RetroDECK"

    def test_a_reading_at_a_destination_we_cannot_honour_does_not_travel(self, adapter, monkeypatch):
        """An emulator keeping its firmware in its own tree: that file is not Tender's.

        With no location to honour the caller places the file by its own flat
        default, and what was read in the emulator's XDG tree says nothing
        about the BIOS root.
        """
        placement = self._placement(
            adapter,
            monkeypatch,
            _requirement(
                file_name="bios7.bin",
                path="/home/deck/.local/share/melonDS/bios7.bin",
                found=KIND_FILE,
                checked="unchecked",
                supplied_by=SuppliedBy(distribution="retrodeck", source="/app/x", card_version="1"),
            ),
        )

        assert placement.relative_path is None
        assert placement.present is None
        assert placement.folder is None
        assert placement.caveats == ()
        assert placement.supplied_by is None

    def test_no_stated_provenance_claims_nothing(self, adapter, monkeypatch):
        placement = self._placement(adapter, monkeypatch, _requirement(found=KIND_FILE, checked="unchecked"))

        assert placement.supplied_by is None

    def test_per_region_alternatives_all_reach_the_catalogue(self, adapter, monkeypatch):
        """Which region a launch picks is unknowable here, so every option is a declared want."""
        group = FirmwareAlternatives(
            options=(
                _requirement(file_name="scph5501.bin", regions=("NTSC-U",)),
                _requirement(file_name="scph5502.bin", regions=("PAL",)),
            )
        )
        answer = _answer(_core(core_so="duckstation_libretro.so", requirements=(group,)))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert [p.file_name for p in adapter().placements] == ["scph5501.bin", "scph5502.bin"]

    def test_a_core_that_declares_nothing_contributes_nothing(self, adapter, monkeypatch):
        """gearboy, gearsystem and geargrafx: read, and asking for nothing."""
        answer = _answer(
            _core(core_so="gearboy_libretro.so"),
            _core(core_so="gearsystem_libretro.so"),
            _core(core_so="geargrafx_libretro.so"),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        catalogue = adapter()
        assert catalogue.placements == ()
        assert catalogue.resolved is True
        assert catalogue.unread_emulators == frozenset()


def _option(
    file_name: str, regions: tuple[str, ...], *, core_so: str | None = "mednafen_psx_libretro.so", **kwargs
) -> FirmwareRequirement:
    """One option of a one-of group — a requirement naming the regions whose launch it serves."""
    return _requirement(core_so=core_so, file_name=file_name, regions=regions, **kwargs)


def _region_caveat(code: str, regions: tuple[str, ...], core_so: str = "mednafen_psx_libretro.so") -> Caveat:
    """A statement an entry makes beside its group, naming regions under ``regions``."""
    return Caveat(code=code, message="about some regions", data={"core_so": core_so, "regions": list(regions)})


class TestOneOfGroups:
    """A one-of group is carried as a group, and its options are rows that borrow nothing from it.

    The group is the requirement — one file of these, the region decides which —
    so it is stated beside the placements. Each option is still a file the
    emulator declared, so it is a row; what it must never do is make that row a
    prerequisite of every launch.
    """

    def _catalogue(self, adapter, monkeypatch, *cores):
        _detect(monkeypatch, _detecting(_Installation(_answer(*cores))))
        return adapter()

    def _beetle(self, *, caveats: tuple[Caveat, ...] = (), present: tuple[str, ...] = ()) -> CoreFirmware:
        """Beetle PSX's shape under 0.21: the three images as optional rows AND as the group's options.

        *present* names the regions whose image is in place and verified.
        """
        names = (("scph5500.bin", "ntsc-j"), ("scph5501.bin", "ntsc-u"), ("scph5502.bin", "pal"))
        rows = tuple(
            _requirement(core_so="mednafen_psx_libretro.so", file_name=name, need="optional") for name, _ in names
        )
        group = FirmwareAlternatives(
            options=tuple(
                _option(name, (region,), found=KIND_FILE, checked="verified")
                if region in present
                else _option(name, (region,))
                for name, region in names
            )
        )
        return _core(core_so="mednafen_psx_libretro.so", requirements=(*rows, group), caveats=caveats)

    def test_the_group_reaches_the_catalogue_with_each_options_regions_and_verdict(self, adapter, monkeypatch):
        catalogue = self._catalogue(adapter, monkeypatch, self._beetle(present=("ntsc-u",)))

        (group,) = catalogue.groups
        assert group.emulator == "mednafen_psx_libretro.so"
        assert [(o.file_name, o.regions, o.satisfied) for o in group.options] == [
            ("scph5500.bin", ("ntsc-j",), False),
            ("scph5501.bin", ("ntsc-u",), True),
            ("scph5502.bin", ("pal",), False),
        ]
        assert catalogue.groups_for("mednafen_psx_libretro.so") == (group,)

    def test_a_file_that_is_a_row_and_an_option_is_one_row_and_stays_optional(self, adapter, monkeypatch):
        """The option's ``required`` speaks for the group, never for the row it shares a name with."""
        catalogue = self._catalogue(adapter, monkeypatch, self._beetle())

        assert [p.file_name for p in catalogue.placements] == ["scph5500.bin", "scph5501.bin", "scph5502.bin"]
        assert all(p.wants == (FirmwareWant("mednafen_psx_libretro.so", required=False),) for p in catalogue.placements)

    def test_an_option_with_no_row_of_its_own_borrows_no_requirement(self, adapter, monkeypatch):
        """SwanStation's search find: the image is only ever an option, and still not each launch's prerequisite."""
        found = FirmwareAlternatives(
            options=(
                _option(
                    "scph1001.bin",
                    ("ntsc-j", "ntsc-u", "pal"),
                    core_so="swanstation_libretro.so",
                    found=KIND_FILE,
                    checked="verified",
                ),
            )
        )
        catalogue = self._catalogue(
            adapter, monkeypatch, _core(core_so="swanstation_libretro.so", requirements=(found,))
        )

        (placement,) = catalogue.placements
        assert placement.file_name == "scph1001.bin"
        assert placement.required_by_any is False
        assert catalogue.groups[0].regions_of("scph1001.bin") == ("ntsc-j", "ntsc-u", "pal")

    def test_a_plain_required_row_keeps_its_requirement_beside_an_option_of_the_same_file(self, adapter, monkeypatch):
        """Whichever of the two comes first, the plain declaration is what the row's need is read off."""
        group = FirmwareAlternatives(options=(_option("scph1001.bin", ("ntsc-u",), core_so="duck_libretro.so"),))
        plain = _requirement(core_so="duck_libretro.so", file_name="scph1001.bin", need="required")
        catalogue = self._catalogue(
            adapter, monkeypatch, _core(core_so="duck_libretro.so", requirements=(group, plain))
        )

        assert catalogue.placements[0].wants == (FirmwareWant("duck_libretro.so", required=True),)

    def test_one_file_under_two_options_serves_both_options_regions(self, adapter, monkeypatch):
        """``psxonpsp660.bin`` stands for two regions through two keys — one row, its regions merged."""
        group = FirmwareAlternatives(
            options=(
                _option("psxonpsp660.bin", ("ntsc-j",)),
                _option("scph5501.bin", ("ntsc-u",)),
                _option("psxonpsp660.bin", ("pal",)),
            )
        )
        catalogue = self._catalogue(
            adapter, monkeypatch, _core(core_so="mednafen_psx_libretro.so", requirements=(group,))
        )

        assert [p.file_name for p in catalogue.placements] == ["psxonpsp660.bin", "scph5501.bin"]
        assert catalogue.groups[0].regions_of("psxonpsp660.bin") == ("ntsc-j", "pal")

    def test_the_regions_an_entry_names_beside_its_group_reach_the_group(self, adapter, monkeypatch):
        """Regions with no option: unchecked where a read settled nothing, absent where nothing boots."""
        catalogue = self._catalogue(
            adapter,
            monkeypatch,
            self._beetle(
                caveats=(
                    _region_caveat(CAVEAT_FIRMWARE_SCAN_INCOMPLETE, ("ntsc-k",)),
                    _region_caveat(CAVEAT_FIRMWARE_SEARCH_UNVERIFIED, ("ntsc-c",)),
                    _region_caveat(CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE, ("pal-m",)),
                )
            ),
        )

        (group,) = catalogue.groups
        assert group.unchecked_regions == ("ntsc-k", "ntsc-c")
        assert group.absent_regions == ("pal-m",)

    def test_the_regions_the_groups_own_options_serve_are_not_read_as_unchecked(self, adapter, monkeypatch):
        """``core-mode-unestablished`` names the regions the group DOES carry an option for."""
        caveat = _region_caveat(CAVEAT_CORE_MODE_UNESTABLISHED, ("ntsc-j", "ntsc-u", "pal"))
        catalogue = self._catalogue(adapter, monkeypatch, self._beetle(caveats=(caveat,)))

        assert catalogue.groups[0].unchecked_regions == ()
        assert catalogue.groups[0].absent_regions == ()

    def test_another_emulators_region_statement_stays_off_the_group(self, adapter, monkeypatch):
        caveat = _region_caveat(CAVEAT_FIRMWARE_SCAN_INCOMPLETE, ("ntsc-k",), core_so="swanstation_libretro.so")
        catalogue = self._catalogue(adapter, monkeypatch, self._beetle(caveats=(caveat,)))

        assert catalogue.groups[0].unchecked_regions == ()

    @staticmethod
    def _decided_by_disc(*, unstated: tuple[str, ...], reason: str = "region-decided-by-disc", **attribution):
        """The statement ``_undecided_region_caveat`` makes beside a group, in its data's shape."""
        return Caveat(
            code=CAVEAT_CORE_MODE_UNESTABLISHED,
            message="the region is the disc's — and nothing is stated for a pal console",
            data={
                **(attribution or {"core_so": "mednafen_psx_libretro.so"}),
                "reason": reason,
                "dir": _ROOT,
                "regions": ["ntsc-j", "ntsc-u"],
                "unstated": list(unstated),
            },
        )

    def _two_regions(self, *caveats: Caveat) -> CoreFirmware:
        """A group carrying options for Japan and USA only."""
        group = FirmwareAlternatives(
            options=(_option("scph5500.bin", ("ntsc-j",)), _option("scph5501.bin", ("ntsc-u",)))
        )
        return _core(core_so="mednafen_psx_libretro.so", requirements=(group,), caveats=caveats)

    def test_the_regions_the_group_states_nothing_for_reach_it_from_unstated(self, adapter, monkeypatch):
        catalogue = self._catalogue(adapter, monkeypatch, self._two_regions(self._decided_by_disc(unstated=("pal",))))

        (group,) = catalogue.groups
        assert group.unstated_regions == ("pal",)
        assert group.unchecked_regions == ()
        assert group.absent_regions == ()
        assert group.regions == ("ntsc-j", "ntsc-u", "pal")

    def test_unstated_is_read_only_off_the_region_statement(self, adapter, monkeypatch):
        """Another reason of the same code says nothing about the group's regions."""
        other = self._decided_by_disc(unstated=("pal",), reason="emulator-build-unestablished")
        catalogue = self._catalogue(adapter, monkeypatch, self._two_regions(other))

        assert catalogue.groups[0].unstated_regions == ()

    def test_another_emulators_unstated_stays_off_the_group(self, adapter, monkeypatch):
        foreign = self._decided_by_disc(unstated=("pal",), core_so="swanstation_libretro.so")
        catalogue = self._catalogue(adapter, monkeypatch, self._two_regions(foreign))

        assert catalogue.groups[0].unstated_regions == ()

    def test_unstated_is_spelled_as_the_resolver_spells_it(self):
        """The key the regions are read off; a rename upstream turns this red."""
        import inspect

        from _vendor.atlas import firmware as atlas_firmware

        source = inspect.getsource(atlas_firmware._undecided_region_caveat)
        assert '"reason": REASON_REGION_DECIDED_BY_DISC' in source
        assert '"unstated": list(unstated)' in source

    def test_an_entry_with_no_identity_states_no_group(self, adapter, monkeypatch):
        group = FirmwareAlternatives(options=(_option("scph5501.bin", ("ntsc-u",), core_so=None),))
        catalogue = self._catalogue(adapter, monkeypatch, _core(core_so=None, emulator=None, requirements=(group,)))

        assert catalogue.groups == ()
        assert [p.file_name for p in catalogue.placements] == ["scph5501.bin"]

    def test_one_emulator_under_two_rows_states_its_group_once(self, adapter, monkeypatch):
        catalogue = self._catalogue(
            adapter, monkeypatch, self._beetle(), replace(self._beetle(), label="Beetle PSX (again)")
        )

        assert len(catalogue.groups) == 1

    def test_a_first_row_stating_no_group_does_not_hide_a_later_rows_group(self, adapter, monkeypatch):
        """One emulator under two rows, only the second carrying the group: the group still reaches the catalogue."""
        plain = _core(
            core_so="mednafen_psx_libretro.so",
            label="Beetle PSX (plain)",
            requirements=(_requirement(core_so="mednafen_psx_libretro.so", file_name="scph5501.bin", need="optional"),),
        )
        catalogue = self._catalogue(adapter, monkeypatch, plain, self._beetle())

        assert [group.emulator for group in catalogue.groups] == ["mednafen_psx_libretro.so"]


class TestUnreadEmulators:
    def test_an_unreadable_core_is_named(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so="mgba_libretro.so", requirements=(_requirement(),)),
            _core(core_so="fbalpha_libretro.so", declaration="unreadable"),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"fbalpha_libretro.so"})

    def test_a_refused_declaration_counts_as_unread(self, adapter, monkeypatch):
        """The core does want something; the resolver just would not place it."""
        answer = _answer(
            _core(
                core_so="odd_libretro.so",
                refused=(RefusedDeclaration(declared="../escape.bin", need="required", reason="leaves-root"),),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"odd_libretro.so"})

    def test_an_unsupported_emulator_counts_as_unread(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so=None, emulator="DOLPHIN", label="Dolphin (Standalone)", declaration="unsupported")
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"DOLPHIN"})

    def test_a_packaged_card_that_named_nothing_counts_as_unread(self, adapter, monkeypatch):
        """A card exists and this query established nothing — an unasked question.

        A card may identify its image by CONTENT, so ``packaged`` with an empty
        requirement list is not "this emulator needs no firmware": it is the same
        silence a missing ``.info`` leaves. PCSX2 answers exactly this when its
        own ini names no BIOS file, and DuckStation does until the bytes are read.
        """
        answer = _answer(
            _core(core_so=None, emulator="PCSX2", label="PCSX2 (Standalone)", declaration="packaged"),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"PCSX2"})

    def test_a_packaged_card_that_named_a_file_is_read(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so=None,
                emulator="MELONDS",
                label="melonDS (Standalone)",
                declaration="packaged",
                requirements=(_requirement(core_so=None, file_name="bios7.bin"),),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset()

    def test_a_read_core_that_declares_nothing_is_never_named(self, adapter, monkeypatch):
        """``read`` and empty is the one pairing that means "this emulator needs none"."""
        answer = _answer(_core(core_so="gearboy_libretro.so"))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset()

    def test_one_emulator_declared_twice_is_read_when_either_row_stated(self, adapter, monkeypatch):
        """The set answers about an emulator, and a caller asks about one.

        Marked unread per row, an identity would land here while its declaration
        sat in the placements under the same name — a file wanted by an emulator
        just called unaskable.
        """
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", label="LRPS2", declaration="unreadable"),
            _core(
                core_so="pcsx2_libretro.so",
                label="PCSX2",
                requirements=(_requirement(core_so="pcsx2_libretro.so", file_name="GameIndex.yaml"),),
            ),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        catalogue = adapter()

        assert catalogue.unread_emulators == frozenset()
        assert catalogue_emulators(catalogue) == {"pcsx2_libretro.so"}

    def test_one_emulator_declared_twice_is_unread_when_neither_row_stated(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", label="LRPS2", declaration="unreadable"),
            _core(core_so="pcsx2_libretro.so", label="PCSX2", declaration="unreadable"),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"pcsx2_libretro.so"})

    def test_an_entry_with_no_identity_names_nobody(self, adapter, monkeypatch):
        """There is no name to put in the set, and no caller could ask about one."""
        answer = _answer(_core(core_so=None, emulator=None, declaration="unsupported"))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset()

    def test_a_retroarch_launch_of_a_foreign_core_adds_nothing(self, adapter, monkeypatch):
        """EmuDeck's ``n3ds`` rows hand RetroArch a Windows core: no identity, nothing declared.

        The entry sits beside a read core of the same system, and the catalogue
        is exactly what that core alone would have made of it — no placement,
        no unread name, no verdict, no group.
        """
        foreign = _core(
            core_so=None,
            emulator=None,
            declaration="absent",
            caveats=(
                Caveat(
                    code=CAVEAT_CORE_FILE_FOREIGN,
                    message="a core file this host cannot load",
                    data={"core_file": "citra_libretro.dll", "label": "Citra", "system": "n3ds"},
                ),
            ),
        )
        read = _core(core_so="mgba_libretro.so", requirements=(_requirement(),))
        _detect(monkeypatch, _detecting(_Installation(_answer(read))))
        alone = adapter()
        _detect(monkeypatch, _detecting(_Installation(_answer(read, foreign))))

        catalogue = adapter()
        assert catalogue.placements == alone.placements
        assert catalogue.unread_emulators == frozenset()
        assert catalogue.emulator_verdicts == alone.emulator_verdicts
        assert catalogue.groups == ()

    def test_a_read_core_is_never_named(self, adapter, monkeypatch):
        answer = _answer(_core(core_so="mgba_libretro.so", requirements=(_requirement(),)))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset()


class TestDegradation:
    def test_a_raising_resolver_answers_unresolved_not_empty(self, adapter, monkeypatch, traces):
        """The one failure mode that must never read as 'this platform needs none'."""
        _detect(
            monkeypatch,
            _detecting(_Installation(ValueError("FirmwareRequirement: need must be one of ..."))),
        )

        catalogue = adapter()

        assert catalogue.resolved is False
        assert catalogue.placements == ()
        assert catalogue.reading_complete_for(["mgba_libretro"]) is False
        assert any("resolver failed" in trace for trace in traces)

    def test_a_raising_detect_answers_unresolved(self, adapter, monkeypatch):
        def detect(home, machine=None):
            raise OSError("no such home")

        _detect(monkeypatch, detect)

        assert adapter().resolved is False

    def test_no_installation_answers_unresolved(self, adapter, monkeypatch, traces):
        _detect(monkeypatch, _detecting())

        catalogue = adapter()

        assert catalogue.resolved is False
        assert any("no emulator source answers (no_source)" in trace for trace in traces)

    def test_an_answer_without_a_root_is_unresolved(self, adapter, monkeypatch):
        """No firmware root means no destination to resolve anything against."""
        _detect(monkeypatch, _detecting(_Installation(_answer(root=None))))

        catalogue = adapter()

        assert catalogue.resolved is False
        assert catalogue.caveats == ("firmware-root-unstated",)

    def test_an_answer_beside_a_sealed_catalogue_is_not_established(self, monkeypatch, traces):
        """EmuDeck's overlay entries are an incomplete list; its firmware answer built on them says so (#2188 D21)."""
        sealed = Caveat(code=CAVEAT_EMULATOR_CATALOGUE_SEALED, message="the catalogue is sealed")
        answer = _answer(_core(requirements=(_requirement(file_name="overlay.bin"),)), caveats=(sealed,))
        _detect(monkeypatch, _detecting(_Installation(answer, kind="emudeck")))
        adapter = AtlasFirmwareAdapter(sources=_sources(traces), log_debug=traces.append)
        platform = AtlasPlatformFirmwareAdapter(sources=_sources(traces), log_debug=traces.append)

        for catalogue in (adapter(), platform("psx")):
            assert catalogue.resolved is False
            assert catalogue.placements == ()
            assert CAVEAT_EMULATOR_CATALOGUE_SEALED in catalogue.caveats

    def test_a_retrodeck_that_is_not_set_up_answers_nothing_established(self, tmp_path, monkeypatch, traces):
        """Its empty answer is not "this platform needs none" (#2265 D2), over the real resolver."""
        # The deploy alone, with no marker, as before RetroDECK's first run.
        monkeypatch.setattr(
            "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
        )
        home = tmp_path / "home"
        (home / ".local" / "share" / "flatpak" / "app" / "net.retrodeck.retrodeck" / "current" / "active").mkdir(
            parents=True
        )
        sources = EmulatorSourcesAdapter(user_home=str(home), settings={}, log_debug=traces.append)
        adapter = AtlasFirmwareAdapter(sources=sources, log_debug=traces.append)
        platform = AtlasPlatformFirmwareAdapter(sources=sources, log_debug=traces.append)

        assert sources.read().answering_kind == "retrodeck"
        for catalogue in (adapter(), platform("psx")):
            assert catalogue.resolved is False
            assert catalogue.placements == ()
            assert HEALTH_ISSUE_NOT_SET_UP in catalogue.caveats
            assert classify_wanted(None, catalogue.reading_complete_for("mednafen_psx_hw_libretro")) == WANTED_UNKNOWN

    def test_an_answer_without_the_sealed_caveat_is_read(self, monkeypatch, traces):
        other = Caveat(code="firmware-path-obstructed", message="a directory is in the way")
        answer = _answer(_core(requirements=(_requirement(file_name="read.bin"),)), caveats=(other,))
        _detect(monkeypatch, _detecting(_Installation(answer, kind="emudeck")))
        platform = AtlasPlatformFirmwareAdapter(sources=_sources(traces), log_debug=traces.append)

        catalogue = platform("psx")

        assert catalogue.resolved is True
        assert [placement.file_name for placement in catalogue.placements] == ["read.bin"]

    def test_retrodeck_answers_with_emudeck_first_in_the_order(self, monkeypatch, traces):
        """The source a game starts through answers, never a merge."""
        retrodeck = _Installation(_answer(_core(requirements=(_requirement(file_name="retrodeck.bin"),))))
        emudeck = _Installation(_answer(_core(requirements=(_requirement(file_name="emudeck.bin"),))), kind="emudeck")
        _detect(monkeypatch, _detecting(retrodeck, emudeck))
        settings = {"emulator_source_order": ["emudeck", "retrodeck"]}
        adapter = AtlasFirmwareAdapter(sources=_sources(traces, settings), log_debug=traces.append)
        platform = AtlasPlatformFirmwareAdapter(sources=_sources(traces, settings), log_debug=traces.append)

        assert [p.file_name for p in adapter().placements] == ["retrodeck.bin"]
        assert [p.file_name for p in platform("psx").placements] == ["retrodeck.bin"]
        assert emudeck.asked_for is None

    def test_without_retrodeck_the_first_enabled_source_answers(self, monkeypatch, traces):
        native = _Installation(
            _answer(_core(requirements=(_requirement(file_name="native.bin"),))), kind="bare_retroarch_native"
        )
        emudeck = _Installation(_answer(_core(requirements=(_requirement(file_name="emudeck.bin"),))), kind="emudeck")
        _detect(monkeypatch, _detecting(emudeck, native))
        settings = {"emulator_source_order": ["bare_retroarch_native", "emudeck"]}
        adapter = AtlasFirmwareAdapter(sources=_sources(traces, settings), log_debug=traces.append)

        assert [p.file_name for p in adapter().placements] == ["native.bin"]


class TestCaveats:
    def test_stable_codes_travel_from_the_answer_and_from_each_core(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so="fbalpha_libretro.so",
                declaration="unreadable",
                caveats=(Caveat(code="core-info-unreadable", message="whatever this says may change"),),
            ),
            caveats=(Caveat(code="firmware-path-obstructed", message="a directory is in the way"),),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert set(adapter().caveats) == {"firmware-path-obstructed", "core-info-unreadable"}

    def test_the_trace_names_the_codes_and_the_arrangement(self, adapter, monkeypatch, traces):
        answer = _answer(_core(requirements=(_requirement(),)))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        adapter()

        assert any("machine" in trace and "entries=1" in trace for trace in traces)


class TestVocabularyConformance:
    """The words ``domain/`` spells for itself are the resolver's own.

    ``domain/`` may not import the vendored resolver, so its copies of the
    declaration kinds and the caveat codes are a second spelling. Held equal
    here rather than trusted, so an upstream rename is a red test instead of a
    rule that quietly stops firing.
    """

    def test_the_declaration_kinds_match(self):
        assert (DECLARED_FILE, DECLARED_DIRECTORY) == (ATLAS_DECLARED_FILE, ATLAS_DECLARED_DIRECTORY)

    def test_the_locating_words_match(self):
        assert (LOCATING_BY_NAME, LOCATING_BY_NAME_THEN_CONTENT, LOCATING_UNESTABLISHED) == (
            ATLAS_LOCATING_BY_NAME,
            ATLAS_LOCATING_BY_NAME_THEN_CONTENT,
            ATLAS_LOCATING_UNESTABLISHED,
        )

    def test_the_system_firmware_states_match(self):
        assert SYSTEM_FIRMWARE_STATES == CORE_SYSTEM_FIRMWARE_STATES

    def test_the_unreadable_place_code_matches(self):
        assert CAVEAT_PATH_INACCESSIBLE == ATLAS_CAVEAT_PATH_INACCESSIBLE

    def test_the_incomplete_listing_code_matches(self):
        assert CAVEAT_SCAN_INCOMPLETE == CAVEAT_FIRMWARE_SCAN_INCOMPLETE

    def test_the_unread_bytes_word_matches(self):
        assert CHECKED_UNREAD == ATLAS_CHECKED_UNREAD

    def test_the_unrecognised_bytes_word_matches(self):
        assert CHECKED_UNRECOGNISED == ATLAS_CHECKED_UNRECOGNISED

    def test_the_identified_by_content_codes_match(self):
        # Through a local name: ruff's SIM300 reads an upper-case name as more constant
        # than a set literal and moves the name to the right, the reverse of this file's order.
        identified_by_content = CAVEATS_IDENTIFIED_BY_CONTENT
        assert identified_by_content == {CAVEAT_FIRMWARE_IMAGE_IDENTIFIED, CAVEAT_FIRMWARE_IMAGE_UNLISTED}


class TestEmulatorVerdicts:
    """What is recorded about an emulator's CONSOLE, carried per emulator and never per file.

    The resolver reads it off a packaged table rather than off this machine, and
    a ``None`` on it means nobody has looked at that console — a distinction the
    adapter has to carry intact, because downstream it is the difference between
    a grey answer and a green one.
    """

    def test_each_verdict_is_keyed_on_the_emulators_identity(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so="swanstation_libretro.so",
                requirements=(_requirement(core_so="swanstation_libretro.so", need="optional"),),
                system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            ),
            _core(
                core_so="pcsx_rearmed_libretro.so",
                requirements=(_requirement(core_so="pcsx_rearmed_libretro.so", need="optional"),),
                system_firmware=SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
            ),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        catalogue = adapter()

        assert catalogue.verdict_for("swanstation_libretro.so").system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert catalogue.verdict_for("swanstation_libretro.so").system_needs_an_image is True
        assert catalogue.verdict_for("pcsx_rearmed_libretro.so").system_needs_an_image is False

    @pytest.mark.parametrize(
        "state", [SYSTEM_FIRMWARE_RUNS_WITHOUT, SYSTEM_FIRMWARE_OPEN, SYSTEM_FIRMWARE_CORE_ALTERNATIVE]
    )
    def test_no_other_recorded_state_demands_an_image(self, adapter, monkeypatch, state):
        answer = _answer(_core(requirements=(_requirement(),), system_firmware=state))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        verdict = adapter().verdict_for("mgba_libretro.so")

        assert verdict.system_firmware == state
        assert verdict.system_needs_an_image is False

    def test_a_core_the_table_records_nothing_about_carries_the_absence(self, adapter, monkeypatch):
        """``None`` is an unasked question, and it must arrive as one."""
        answer = _answer(_core(requirements=(_requirement(),)))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        verdict = adapter().verdict_for("mgba_libretro.so")

        assert verdict.system_firmware is None
        assert verdict.system_needs_an_image is False

    def test_the_resolvers_own_verdict_travels_beside_it(self, adapter, monkeypatch):
        """``requirements_met`` is atlas's, not ours — carried as it stands.

        It is the readiness verdict every BIOS colour is taken from,
        so this layer passes it through and never rebuilds it from the rows.
        """
        answer = _answer(
            _core(
                core_so="swanstation_libretro.so",
                requirements=(_requirement(core_so="swanstation_libretro.so", need="optional"),),
                system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().verdict_for("swanstation_libretro.so").requirements_met is False

    @pytest.mark.parametrize(
        ("system_firmware", "present", "met"),
        [
            (SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, False, False),
            (SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, True, True),
            (None, False, None),
        ],
    )
    def test_the_resolvers_console_level_verdict_travels_beside_it(
        self, adapter, monkeypatch, system_firmware, present, met
    ):
        """``system_firmware_met`` is atlas's own answer whether the console has an image; carried as it stands."""
        image = _requirement(
            core_so="swanstation_libretro.so",
            need="optional",
            found=KIND_FILE if present else KIND_MISSING,
            checked="verified" if present else None,
        )
        answer = _answer(
            _core(core_so="swanstation_libretro.so", requirements=(image,), system_firmware=system_firmware)
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().verdict_for("swanstation_libretro.so").system_firmware_met is met

    def test_a_standalone_emulator_is_keyed_like_any_other(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so=None,
                emulator="DUCKSTATION",
                label="DuckStation (Standalone)",
                declaration="packaged",
                requirements=(_requirement(core_so=None),),
                system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().verdict_for("DUCKSTATION").system_needs_an_image is True

    def test_an_unidentified_entry_is_recorded_for_nobody(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so=None,
                emulator=None,
                declaration="packaged",
                requirements=(_requirement(core_so=None),),
                system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            )
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().emulator_verdicts == {}

    def test_a_caller_with_no_emulator_to_name_is_answered_for_nobody(self, adapter, monkeypatch):
        answer = _answer(_core(requirements=(_requirement(),), system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT))
        _detect(monkeypatch, _detecting(_Installation(answer)))

        assert adapter().verdict_for(None) is None

    def test_a_reading_that_did_not_happen_records_nothing(self, adapter, monkeypatch):
        _detect(monkeypatch, _detecting(_Installation(ValueError("nope"))))

        assert adapter().emulator_verdicts == {}

    def test_the_trace_names_the_emulators_whose_console_needs_an_image(self, adapter, monkeypatch, traces):
        answer = _answer(
            _core(
                core_so="swanstation_libretro.so",
                requirements=(_requirement(core_so="swanstation_libretro.so", need="optional"),),
                system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            ),
            _core(core_so="mgba_libretro.so", requirements=(_requirement(),)),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        adapter()

        assert any("system-firmware-needed=['swanstation_libretro.so']" in trace for trace in traces)


class TestFolderVerdicts:
    """A folder declaration's verdict is what the folder HOLDS, and it always stands.

    Whether a reading settles that is the resolver's own three-valued answer, not
    a shape this adapter enumerates. What matters here is that the answer is
    carried whatever it is — with the codes that speak for it — and that presence
    never stands in for it: RetroDECK links LRPS2's ``pcsx2/bios`` onto the BIOS
    root, so the folder is there on every install.
    """

    def _placement(self, adapter, monkeypatch, requirement, *caveats):
        answer = _answer(_core(core_so="pcsx2_libretro.so", requirements=(requirement,)), caveats=caveats)
        _detect(monkeypatch, _detecting(_Installation(answer)))
        return adapter().placements[0]

    def _folder(self, **kwargs):
        return _requirement(
            file_name="bios",
            declared="pcsx2/bios",
            path=f"{_ROOT}/pcsx2/bios",
            declared_kind=ATLAS_DECLARED_DIRECTORY,
            **kwargs,
        )

    def test_an_absent_folder_is_unmet_without_reading_a_byte(self, adapter, monkeypatch):
        placement = self._placement(adapter, monkeypatch, self._folder(found=KIND_MISSING))

        assert placement.declared_kind == DECLARED_DIRECTORY
        assert placement.folder is not None
        assert placement.folder.satisfied is False

    def test_a_file_where_the_core_lists_a_folder_is_unmet_too(self, adapter, monkeypatch):
        """A regular file has no inside, so the listing the core makes reaches nothing."""
        placement = self._placement(adapter, monkeypatch, self._folder(found=KIND_FILE, checked="unknown"))

        assert placement.folder is not None
        assert placement.folder.satisfied is False

    def test_a_settled_folder_carries_what_the_listing_found_in_it(self, adapter, monkeypatch):
        """A folder holding no file of a size the core would even open, said in its own words.

        The code saying so names the folder as ``dir`` rather than as ``path``.
        Dropped, the row renders red with no word at all, which is the ordinary
        state of a RetroDECK holding no PS2 image.
        """
        placement = self._placement(
            adapter,
            monkeypatch,
            self._folder(found=KIND_DIRECTORY, checked="unknown", contents_satisfied=False),
            Caveat(
                code=CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE,
                message="holds no file of a size this core accepts",
                data={"dir": f"{_ROOT}/pcsx2/bios", "core_so": "pcsx2_libretro.so", "need": "required"},
            ),
        )

        assert placement.folder is not None
        assert placement.folder.satisfied is False
        assert placement.caveats == (CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE,)

    def test_a_folder_nothing_settled_says_so_rather_than_holding_nothing(self, adapter, monkeypatch):
        """``None`` is the third value, and it must survive as one.

        There is no second reading to replace this answer, so the statement that
        the contents were not checked is the whole of what the row has to say —
        and folding the verdict into ``False`` would claim an absence nothing
        established.
        """
        placement = self._placement(
            adapter,
            monkeypatch,
            self._folder(found=KIND_DIRECTORY, checked="unknown"),
            Caveat(
                code=CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,
                message="which of them is a BIOS is a question about their bytes",
                data={"dir": f"{_ROOT}/pcsx2/bios", "candidates": "3", "core_so": "pcsx2_libretro.so"},
            ),
        )

        assert placement.folder is not None
        assert placement.folder.satisfied is None
        assert placement.caveats == (CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,)

    def test_a_file_declaration_never_picks_up_a_listings_findings(self, adapter, monkeypatch):
        """On a linked root the listed folder IS the root, and so is this file's destination.

        RetroDECK points ``<bios>/pcsx2/bios`` back at ``<bios>``, so a
        declaration collapsing onto the root resolves to the same place the
        folder was listed at. Only a folder declaration is ever listed, which is
        what keeps a statement about that listing off this row.
        """
        placement = self._placement(
            adapter,
            monkeypatch,
            _requirement(core_so="pcsx2_libretro.so", file_name="stray.bin", path=_ROOT, found=KIND_MISSING),
            Caveat(
                code=CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE,
                message="holds no file of a size this core accepts",
                data={"dir": _ROOT, "core_so": "pcsx2_libretro.so", "need": "required"},
            ),
        )

        assert placement.declared_kind == DECLARED_FILE
        assert placement.caveats == ()

    def test_another_emulators_search_of_the_same_directory_stays_off_the_row(self, adapter, monkeypatch):
        """A standalone emulator's SEARCH directory carries a ``dir`` too.

        DuckStation ranks its images in the BIOS root, which is where a
        declaration that collapses onto the root also resolves — so keyed by
        place alone the folder row would word another emulator's search as its
        own verdict's cause.
        """
        placement = self._placement(
            adapter,
            monkeypatch,
            self._folder(found=KIND_DIRECTORY, checked="unknown", contents_satisfied=True),
            Caveat(
                code=CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,
                message="DuckStation would rank these",
                data={"dir": f"{_ROOT}/pcsx2/bios", "candidates": "13", "token": "DUCKSTATION"},
            ),
        )

        assert placement.folder is not None
        assert placement.folder.satisfied is True
        assert placement.caveats == ()

    def test_an_unowned_statement_about_the_place_stays_on_the_row(self, adapter, monkeypatch):
        """A listing that failed names no emulator, and it is exactly the cause a row needs."""
        placement = self._placement(
            adapter,
            monkeypatch,
            self._folder(found=KIND_DIRECTORY, checked="unknown"),
            Caveat(
                code="firmware-scan-incomplete",
                message="could not be listed",
                data={"dir": f"{_ROOT}/pcsx2/bios", "unreadable": ("sub",)},
            ),
        )

        assert placement.caveats == ("firmware-scan-incomplete",)

    def test_a_statement_naming_the_cores_short_name_reaches_its_row(self, adapter, monkeypatch):
        """The resolver names a core by its libretro short name under ``core`` — ``pcsx2``.

        That is no identity Tender keys on, and matched against the
        identity alone the statement fell off the very folder row it is about.
        """
        placement = self._placement(
            adapter,
            monkeypatch,
            self._folder(found=KIND_DIRECTORY, checked="unknown", contents_satisfied=True),
            Caveat(
                code=CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,
                message="the configured name is stale",
                data={"core": "pcsx2", "key": "pcsx2_bios", "name": "scph10000.bin", "dir": f"{_ROOT}/pcsx2/bios"},
            ),
        )

        assert placement.caveats == (CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,)

    def test_a_stale_configured_name_rides_with_the_emulators_label_and_leaves_the_verdict_alone(
        self, adapter, monkeypatch
    ):
        """The core lists the folder instead, so the folder verdict stands exactly as the resolver gave it."""
        answer = _answer(
            _core(
                core_so="pcsx2_libretro.so",
                label="LRPS2",
                requirements=(self._folder(found=KIND_DIRECTORY, checked="unknown", contents_satisfied=True),),
            ),
            caveats=(
                Caveat(
                    code=CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,
                    message="the configured name is stale",
                    data={"core": "pcsx2", "key": "pcsx2_bios", "name": "scph10000.bin", "dir": f"{_ROOT}/pcsx2/bios"},
                ),
            ),
        )
        _detect(monkeypatch, _detecting(_Installation(answer)))

        placement = adapter().placements[0]
        assert placement.missing_configured_image == MissingConfiguredImage("LRPS2", "scph10000.bin")
        assert placement.folder is not None
        assert placement.folder.satisfied is True

    def test_a_row_with_no_stale_setting_names_none(self, adapter, monkeypatch):
        placement = self._placement(adapter, monkeypatch, self._folder(found=KIND_DIRECTORY, checked="unknown"))

        assert placement.missing_configured_image is None

    def test_another_cores_short_name_keeps_its_statement_off_the_row(self, adapter, monkeypatch):
        placement = self._placement(
            adapter,
            monkeypatch,
            self._folder(found=KIND_DIRECTORY, checked="unknown", contents_satisfied=True),
            Caveat(
                code=CAVEAT_FIRMWARE_CONFIGURED_IMAGE_MISSING,
                message="the configured name is stale",
                data={"core": "play", "key": "x", "name": "y.bin", "dir": f"{_ROOT}/pcsx2/bios"},
            ),
        )

        assert placement.caveats == ()


def _not_comparable(file_name: str) -> Caveat:
    """One core's report that the identity at the shared destination settles nothing."""
    return Caveat(
        code=CAVEAT_FIRMWARE_IDENTITY_NOT_COMPARABLE,
        message=f"{file_name}'s bytes differ from the pinned ones",
        data={
            "path": f"{_ROOT}/gba_bios.bin",
            "file_name": file_name,
            "archive_reason": "romset",
            "table_version": "6.0.0",
        },
    )


class TestDestinationCaveats:
    """What else the reading found at a row's own destination, in the resolver's codes."""

    def _placement(self, adapter, monkeypatch, requirement, *caveats):
        answer = _answer(_core(core_so="mgba_libretro.so", requirements=(requirement,)), caveats=caveats)
        _detect(monkeypatch, _detecting(_Installation(answer)))
        return adapter().placements[0]

    def test_a_caveat_naming_the_rows_destination_travels_with_the_row(self, adapter, monkeypatch):
        placement = self._placement(
            adapter,
            monkeypatch,
            _requirement(found=KIND_DIRECTORY, checked="unknown"),
            Caveat(
                code=CAVEAT_FIRMWARE_PATH_OBSTRUCTED,
                message="a directory is in the way",
                data={"path": f"{_ROOT}/gba_bios.bin"},
            ),
        )

        assert placement.caveats == (CAVEAT_FIRMWARE_PATH_OBSTRUCTED,)

    def test_a_caveat_about_another_destination_does_not(self, adapter, monkeypatch):
        placement = self._placement(
            adapter,
            monkeypatch,
            _requirement(found=KIND_FILE, checked="unchecked"),
            Caveat(code="firmware-unreadable", message="elsewhere", data={"path": f"{_ROOT}/other.bin"}),
        )

        assert placement.caveats == ()

    def test_two_caveats_at_one_destination_list_their_code_once(self, adapter, monkeypatch):
        """The row carries codes, so two findings about one place must not list one twice.

        Two cores whose declarations spell one file differently resolve to the
        same destination, and each states the identity it could not compare —
        one ``path``, two ``file_name`` values, so the resolver keeps both.
        """
        placement = self._placement(
            adapter,
            monkeypatch,
            _requirement(found=KIND_FILE, checked="not-comparable"),
            _not_comparable("gba_bios.bin"),
            _not_comparable("bios/gba_bios.bin"),
        )

        assert placement.caveats == (CAVEAT_FIRMWARE_IDENTITY_NOT_COMPARABLE,)


_FOLDER = f"{_ROOT}/pcsx2/bios"


def _image(name: str, description: str, *, code: str = CAVEAT_FIRMWARE_IMAGE_IDENTIFIED) -> Caveat:
    """One caveat of the folder read's image family, as the resolver states it."""
    return Caveat(
        code=code,
        message=f"{name} reads as a PS2 BIOS",
        data={
            "path": f"{_FOLDER}/{name}",
            "image": name,
            "md5": "d333558cc14561c1fdc334c75d5f37b7",
            "table": "6.0.0",
            "core_so": "pcsx2_libretro.so",
            "description": description,
        },
    )


def _folder_requirement(*, contents_satisfied: bool | None, found: PathKind = KIND_DIRECTORY):
    return _requirement(
        core_so="pcsx2_libretro.so",
        file_name="bios",
        declared="pcsx2/bios",
        path=_FOLDER,
        found=found,
        checked="unknown" if found in (KIND_DIRECTORY, KIND_FILE) else None,
        declared_kind=ATLAS_DECLARED_DIRECTORY,
        contents_satisfied=contents_satisfied,
    )


@pytest.fixture
def platform_adapter(traces):
    return AtlasPlatformFirmwareAdapter(sources=_sources(traces), log_debug=traces.append)


def _standalone_catalogue(tmp_path, monkeypatch, traces, system: str, *images: str):
    """*system*'s answer from the real resolver, over a RetroDECK with DuckStation, PCSX2 and xemu set up."""
    monkeypatch.setattr(
        "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
    )
    home = tmp_path / "home"
    rd_home = tmp_path / "retrodeck"
    systems = (
        home
        / ".local/share/flatpak/app/net.retrodeck.retrodeck/current/active/files/retrodeck/components"
        / "es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    systems.parent.mkdir(parents=True)
    systems.write_text(SYSTEMS, encoding="utf-8")
    marker = home / ".var/app/net.retrodeck.retrodeck/config/retrodeck/retrodeck.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps({"paths": {"rd_home_path": str(rd_home), "bios_path": str(rd_home / "bios")}}), encoding="utf-8"
    )
    lay_down_standalone_emulators(str(home), str(rd_home / "bios"), *images)
    sources = EmulatorSourcesAdapter(user_home=str(home), settings={}, log_debug=traces.append)
    return AtlasPlatformFirmwareAdapter(sources=sources, log_debug=traces.append)(system)


class TestDuckStationOverItsBiosFolder:
    """DuckStation as the real resolver answers it, over a RetroDECK laid down under ``tmp_path``.

    It names no BIOS file and searches its folder, so over a folder holding
    nothing of a size it accepts the answer states no requirement at all —
    and still a verdict, not met, with the console's image shown absent.
    """

    def _catalogue(self, tmp_path, monkeypatch, traces, *images: str):
        return _standalone_catalogue(tmp_path, monkeypatch, traces, "psx", *images)

    def test_an_empty_folder_is_not_met_with_the_console_image_shown_absent(self, tmp_path, monkeypatch, traces):
        catalogue = self._catalogue(tmp_path, monkeypatch, traces)

        verdict = catalogue.verdict_for("DUCKSTATION")
        assert verdict is not None
        assert catalogue.placements == ()
        assert verdict.declaration == "packaged"
        assert verdict.requirements_met is False
        assert verdict.system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert verdict.system_firmware_met is False
        assert verdict.world_knowledge is True

    def test_its_search_is_a_note_with_the_folder_and_no_count(self, tmp_path, monkeypatch, traces):
        catalogue = self._catalogue(tmp_path, monkeypatch, traces)

        verdict = catalogue.verdict_for("DUCKSTATION")
        assert verdict is not None
        (note,) = verdict.notes
        assert (note.kind, note.dir, note.candidates) == (
            "search-found-nothing",
            str(tmp_path / "retrodeck" / "bios"),
            None,
        )
        assert set(note.regions) == {"ntsc-u", "ntsc-j", "pal"}

    def test_a_packaged_card_with_no_requirement_still_reads_as_not_read(self, tmp_path, monkeypatch, traces):
        """Its verdict is stated; whether a file nobody declares is needed is still not (the panel words the first)."""
        catalogue = self._catalogue(tmp_path, monkeypatch, traces)

        assert catalogue.reading_complete_for("DUCKSTATION") is False

    def test_a_file_its_search_keeps_is_a_requirement_and_no_absence(self, tmp_path, monkeypatch, traces):
        """The control: an image its table does not know still boots, so nothing is shown absent."""
        catalogue = self._catalogue(tmp_path, monkeypatch, traces, "unknown.bin")

        verdict = catalogue.verdict_for("DUCKSTATION")
        assert verdict is not None
        assert [placement.file_name for placement in catalogue.placements] == ["unknown.bin"]
        assert verdict.system_firmware_met is not False
        assert verdict.notes == ()


class TestWhichEmptySettingIsASearch:
    """``firmware-path-names-no-file`` on real answers, a search told apart from an empty setting by its fields.

    A search states ``dir`` with ``candidates`` or ``regions`` — DuckStation's
    states ``regions``. PCSX2's empty BIOS setting states ``key``
    ``Filenames/BIOS`` and a ``dir`` with neither; xemu's empty file settings
    state no ``dir``. Only a search becomes the search note.
    """

    @staticmethod
    def _notes(tmp_path, monkeypatch, traces, system: str, emulator: str):
        verdict = _standalone_catalogue(tmp_path, monkeypatch, traces, system).verdict_for(emulator)
        assert verdict is not None
        return [(note.kind, note.setting, note.candidates) for note in verdict.notes]

    def test_duckstations_empty_search_is_a_search(self, tmp_path, monkeypatch, traces):
        assert self._notes(tmp_path, monkeypatch, traces, "psx", "DUCKSTATION") == [
            ("search-found-nothing", None, None)
        ]

    def test_pcsx2s_empty_bios_setting_is_no_bios_chosen_and_no_search(self, tmp_path, monkeypatch, traces):
        assert self._notes(tmp_path, monkeypatch, traces, "ps2", "PCSX2") == [("bios-unchosen", "Filenames/BIOS", None)]

    def test_xemus_empty_file_settings_are_neither(self, tmp_path, monkeypatch, traces):
        assert self._notes(tmp_path, monkeypatch, traces, "xbox", "XEMU") == []


class TestPlatformAdapter:
    """The per-system reading: what one platform's emulators want, contents and all."""

    def _catalogue(self, platform_adapter, monkeypatch, answer, system="ps2"):
        installation = _Installation(answer)
        _detect(monkeypatch, _detecting(installation))
        return platform_adapter(system), installation

    def test_the_question_is_asked_of_the_system_with_verification_on(self, platform_adapter, monkeypatch):
        """Two answers depend on reading bytes, so an unverified reading is not this question.

        A packaged card that identifies its image by content names no file until
        one is read, and a folder declaration is satisfied by a file inside the
        folder rather than by the folder being there.
        """
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", requirements=(_folder_requirement(contents_satisfied=True),))
        )

        _, installation = self._catalogue(platform_adapter, monkeypatch, answer)

        assert installation.asked_for == ("ps2", True)

    def test_an_identified_image_satisfies_the_folder_and_is_named(self, platform_adapter, monkeypatch):
        """The two halves count differently: a description per image, the code once."""
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", requirements=(_folder_requirement(contents_satisfied=True),)),
            caveats=(
                _image("ps2-0200e-20040614.bin", "Europe  v02.00(14/06/2004)  Console 20040614-100914"),
                _image("ps2-0200j-20040614.bin", "Japan   v02.00(14/06/2004)  Console 20040614-100905"),
            ),
        )

        catalogue, _ = self._catalogue(platform_adapter, monkeypatch, answer)
        placement = catalogue.by_file_name()["bios"]

        assert placement.folder is not None
        assert placement.folder.satisfied is True
        assert placement.folder.images == (
            "Europe  v02.00(14/06/2004)  Console 20040614-100914",
            "Japan   v02.00(14/06/2004)  Console 20040614-100905",
        )
        assert placement.caveats == (CAVEAT_FIRMWARE_IMAGE_IDENTIFIED,)

    def test_an_image_the_packaged_table_does_not_list_counts_all_the_same(self, platform_adapter, monkeypatch):
        """The table lists what System.dat lists; the core's own test is the verdict."""
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", requirements=(_folder_requirement(contents_satisfied=True),)),
            caveats=(_image("dump.bin", "USA v01.60", code=CAVEAT_FIRMWARE_IMAGE_UNLISTED),),
        )

        catalogue, _ = self._catalogue(platform_adapter, monkeypatch, answer)

        assert catalogue.by_file_name()["bios"].folder.images == ("USA v01.60",)

    def test_a_folder_holding_no_image_is_unmet_and_says_which_code(self, platform_adapter, monkeypatch):
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", requirements=(_folder_requirement(contents_satisfied=False),)),
            caveats=(
                Caveat(
                    code="firmware-directory-holds-no-image",
                    message="none of them reads as a PS2 BIOS",
                    data={"dir": _FOLDER, "candidates": "1", "core_so": "pcsx2_libretro.so"},
                ),
            ),
        )

        catalogue, _ = self._catalogue(platform_adapter, monkeypatch, answer)
        placement = catalogue.by_file_name()["bios"]

        assert placement.folder.satisfied is False
        assert placement.caveats == ("firmware-directory-holds-no-image",)
        assert placement.folder.images == ()

    def test_a_contradiction_withholds_the_verdict_and_names_no_image(self, platform_adapter, monkeypatch):
        """The table names the bytes and the core's own test denies them."""
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", requirements=(_folder_requirement(contents_satisfied=None),)),
            caveats=(
                Caveat(
                    code="firmware-image-contradicted",
                    message="the two reads disagree",
                    data={"path": f"{_FOLDER}/odd.bin", "image": "odd.bin", "core_so": "pcsx2_libretro.so"},
                ),
            ),
        )

        catalogue, _ = self._catalogue(platform_adapter, monkeypatch, answer)
        placement = catalogue.by_file_name()["bios"]

        assert placement.folder.satisfied is None
        assert placement.caveats == ("firmware-image-contradicted",)
        assert placement.folder.images == ()

    def test_a_caveat_about_another_folder_stays_out(self, platform_adapter, monkeypatch):
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", requirements=(_folder_requirement(contents_satisfied=True),)),
            caveats=(Caveat(code="firmware-scan-incomplete", message="elsewhere", data={"dir": f"{_ROOT}/other"}),),
        )

        catalogue, _ = self._catalogue(platform_adapter, monkeypatch, answer)

        assert catalogue.by_file_name()["bios"].caveats == ()

    def test_a_file_declaration_gets_no_folder_verdict(self, platform_adapter, monkeypatch):
        """A verdict about contents nobody listed would be a state that lies."""
        answer = _answer(
            _core(
                core_so="pcsx2_libretro.so",
                requirements=(
                    _requirement(
                        core_so="pcsx2_libretro.so",
                        file_name="GameIndex.yaml",
                        found=KIND_FILE,
                        checked="unchecked",
                    ),
                ),
            )
        )

        catalogue, _ = self._catalogue(platform_adapter, monkeypatch, answer)

        assert catalogue.by_file_name()["GameIndex.yaml"].folder is None

    def test_a_standalone_card_reaches_the_platform(self, platform_adapter, monkeypatch):
        """The whole point: a system whose only emulator ships no libretro core.

        A whole-machine reading enumerates installed cores and carries no entry
        for CEMU at all, so the platform read "nothing could answer" over a
        requirement sitting in the vendored data.
        """
        answer = _answer(
            _core(
                core_so=None,
                emulator="CEMU",
                label="Cemu (Standalone)",
                declaration="packaged",
                requirements=(_requirement(core_so=None, file_name="keys.txt"),),
            )
        )

        catalogue, _ = self._catalogue(platform_adapter, monkeypatch, answer, system="wiiu")

        assert catalogue.resolved is True
        assert catalogue.reading_complete_for("CEMU") is True
        assert catalogue.by_file_name()["keys.txt"].required_by_any is True

    def test_a_raising_resolver_answers_unresolved_rather_than_an_empty_catalogue(
        self, platform_adapter, monkeypatch, traces
    ):
        _detect(
            monkeypatch,
            _detecting(_Installation(ValueError("FirmwareRequirement: need must be one of ..."))),
        )

        catalogue = platform_adapter("ps2")

        assert catalogue.resolved is False
        assert catalogue.placements == ()
        assert any("resolver failed for ps2" in trace for trace in traces)

    def test_no_installation_answers_unresolved(self, platform_adapter, monkeypatch):
        _detect(monkeypatch, _detecting())

        assert platform_adapter("ps2").resolved is False

    def test_an_answer_without_a_root_answers_unresolved(self, platform_adapter, monkeypatch):
        _detect(monkeypatch, _detecting(_Installation(_answer(root=None))))

        catalogue = platform_adapter("ps2")

        assert catalogue.resolved is False
        assert catalogue.placements == ()


class TestWhatTheResolverSaysAboutEachEmulator:
    """The facts the BIOS sentences word, carried off the entry as the resolver states them and never rebuilt."""

    @staticmethod
    def _verdict(adapter, monkeypatch, core: CoreFirmware, identity: str = "mgba_libretro.so"):
        _detect(monkeypatch, _detecting(_Installation(_answer(core))))
        return adapter().verdict_for(identity)

    def test_how_it_finds_its_firmware_travels_as_the_resolver_spells_it(self, adapter, monkeypatch):
        core = replace(_core(requirements=(_requirement(),)), locating=ATLAS_LOCATING_BY_NAME)

        assert self._verdict(adapter, monkeypatch, core).locating == LOCATING_BY_NAME

    def test_a_word_it_does_not_know_reads_unestablished(self, adapter, monkeypatch):
        """A later resolver's new word; set past the constructor, which knows only today's three."""
        core = _core(requirements=(_requirement(),))
        object.__setattr__(core, "locating", "by-smell")

        assert self._verdict(adapter, monkeypatch, core).locating == LOCATING_UNESTABLISHED

    def test_its_refused_declarations_travel_with_their_need_and_reason(self, adapter, monkeypatch):
        core = _core(
            requirements=(_requirement(),),
            refused=(
                RefusedDeclaration(declared="../a.bin", need="required", reason="firmware-path-escapes-root"),
                RefusedDeclaration(declared="../b.bin", need="optional", reason="firmware-path-unresolvable"),
            ),
        )

        verdict = self._verdict(adapter, monkeypatch, core)

        assert [(r.declared, r.required, r.reason) for r in verdict.refused] == [
            ("../a.bin", True, "firmware-path-escapes-root"),
            ("../b.bin", False, "firmware-path-unresolvable"),
        ]
        assert [r.declared for r in verdict.refused_required] == ["../a.bin"]

    def test_the_plain_required_files_it_could_not_judge_are_named_and_no_group_option(self, adapter, monkeypatch):
        group = FirmwareAlternatives(
            options=(_requirement(file_name="scph5500.bin", regions=("ntsc-j",), found=KIND_FILE, checked="unchecked"),)
        )
        core = _core(
            requirements=(
                _requirement(file_name="gba_bios.bin", found=KIND_FILE, checked="not-comparable"),
                _requirement(file_name="opt.bin", need="optional", found=KIND_FILE, checked="unchecked"),
                group,
            )
        )

        assert self._verdict(adapter, monkeypatch, core).undetermined == ("gba_bios.bin",)

    def test_the_plain_required_files_shown_not_met_are_named(self, adapter, monkeypatch):
        """A game's own region decides only once nothing outside the group is shown not met."""
        core = _core(
            requirements=(
                _requirement(file_name="gba_bios.bin"),
                _requirement(file_name="opt.bin", need="optional"),
            )
        )

        assert self._verdict(adapter, monkeypatch, core).unmet == ("gba_bios.bin",)

    def test_the_region_note_is_read_off_its_reason(self, adapter, monkeypatch):
        decided = Caveat(
            code=CAVEAT_CORE_MODE_UNESTABLISHED,
            message="region from the disc",
            data={"core_so": "mgba_libretro.so", "reason": "region-decided-by-disc", "regions": ["ntsc-j"]},
        )
        other = Caveat(
            code=CAVEAT_CORE_MODE_UNESTABLISHED, message="another mode", data={"reason": "emulator-build-unestablished"}
        )

        assert self._verdict(adapter, monkeypatch, _core(caveats=(decided,))).region_decided_by_disc is True
        assert self._verdict(adapter, monkeypatch, _core(caveats=(other,))).region_decided_by_disc is False

    def test_a_bios_nobody_chose_is_a_note(self, adapter, monkeypatch):
        """PCSX2 with ``[Filenames] BIOS`` empty: the setting names no file, so no game boots until one is chosen."""
        empty = Caveat(
            code=CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
            message="no BIOS chosen",
            data={"token": "PCSX2", "key": "Filenames/BIOS", "declared": "", "need": "required"},
        )
        core = _core(core_so=None, emulator="PCSX2", declaration="packaged", caveats=(empty,))

        notes = self._verdict(adapter, monkeypatch, core, "PCSX2").notes

        assert [(n.kind, n.setting) for n in notes] == [("bios-unchosen", "Filenames/BIOS")]

    def test_a_bios_nobody_chose_is_a_note_on_the_answer_pcsx2_really_sends(self, adapter, monkeypatch):
        """PCSX2 (Standalone) on a machine with ``[Filenames] BIOS`` empty, as the resolver answers it.

        A packaged card with no rows and an open verdict, so the emulator reads as
        not read — and the caveat carries the searched ``dir`` beside the setting,
        but no ``candidates``: it is the setting's statement, not a search's.
        """
        empty = Caveat(
            code=CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
            message="PCSX2 (Standalone) has no BIOS image chosen — [Filenames] BIOS is empty",
            data={
                "label": "PCSX2 (Standalone)",
                "token": "PCSX2",
                "key": "Filenames/BIOS",
                "declared": "",
                "need": "required",
                "dir": "/run/media/deck/Emulation/retrodeck/bios",
            },
        )
        packaged = Caveat(code="firmware-packaged-declaration", message="card", data={"token": "PCSX2"})
        core = _core(core_so=None, emulator="PCSX2", declaration="packaged", caveats=(packaged, empty))
        _detect(monkeypatch, _detecting(_Installation(_answer(core))))
        catalogue = adapter()

        assert [(n.kind, n.setting) for n in catalogue.verdict_for("PCSX2").notes] == [
            ("bios-unchosen", "Filenames/BIOS")
        ]
        # Not read: which is why the panel's "nothing established" must not word over it.
        assert catalogue.reading_complete_for("PCSX2") is False

    @pytest.mark.parametrize("key", ["sys.files/bootrom_path", "sys.files/flashrom_path", "sys.files/hdd_path"])
    def test_an_empty_xemu_file_setting_is_no_bios_nobody_chose(self, adapter, monkeypatch, key):
        """xemu states its empty ``[sys.files]`` keys with the same code and shape.

        Two of the three do not hold the launch, so a "no BIOS chosen" sentence
        over them would sit beside a green xemu.
        """
        empty = Caveat(
            code=CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
            message="names no file",
            data={"token": "XEMU", "key": key, "declared": "", "need": "required"},
        )
        core = _core(core_so=None, emulator="XEMU", declaration="packaged", caveats=(empty,))

        assert self._verdict(adapter, monkeypatch, core, "XEMU").notes == ()

    @pytest.mark.parametrize("candidates", [0, 2])
    def test_a_search_that_reached_no_image_is_a_note_with_its_folder_and_candidates(
        self, adapter, monkeypatch, candidates
    ):
        """A settled search of the BIOS folder that found nothing to boot (``_search_found_nothing``)."""
        nothing = Caveat(
            code=CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
            message="no image to boot",
            data={
                "core_so": "swanstation_libretro.so",
                "dir": "/home/deck/retrodeck/bios",
                "need": "required",
                "candidates": str(candidates),
                "regions": ["ntsc-j", "ntsc-u", "pal"],
            },
        )

        notes = self._verdict(adapter, monkeypatch, _core(caveats=(nothing,))).notes

        assert [(n.kind, n.dir, n.candidates, n.regions) for n in notes] == [
            ("search-found-nothing", "/home/deck/retrodeck/bios", candidates, ("ntsc-j", "ntsc-u", "pal"))
        ]

    def test_the_search_statement_is_spelled_as_the_resolver_spells_it(self):
        """``dir`` and ``candidates`` are the keys the note is read off; a rename upstream turns this red."""
        import inspect

        from _vendor.atlas import firmware as atlas_firmware

        source = inspect.getsource(atlas_firmware._search_found_nothing)
        # The keys of the settled search's own statement — not of the
        # unverified one before it, which carries the same two keys.
        settled = source[source.index("CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,") :]
        assert '"dir": directory' in settled
        assert '"candidates": str(len(searched.kept))' in settled

    def test_the_bios_choice_setting_is_spelled_as_the_resolver_spells_it(self):
        """The PCSX2 card's caveat names the setting under ``key``; a rename upstream turns this red."""
        import inspect

        from _vendor.atlas import firmware as atlas_firmware

        assert f'"key": "{_BIOS_CHOICE_SETTING}"' in inspect.getsource(atlas_firmware)

    def test_a_region_nothing_boots_for_is_no_bios_nobody_chose(self, adapter, monkeypatch):
        """The same code with ``regions`` is a one-of group's statement, and the group carries it."""
        absent = Caveat(
            code=CAVEAT_FIRMWARE_PATH_NAMES_NO_FILE,
            message="nothing for pal",
            data={"core_so": "mgba_libretro.so", "declared": "", "regions": ["pal"]},
        )

        assert self._verdict(adapter, monkeypatch, _core(caveats=(absent,))).notes == ()

    def test_images_that_rank_alike_are_a_note_with_their_count_and_regions(self, adapter, monkeypatch):
        tied = Caveat(
            code="firmware-image-ambiguous",
            message="two rank alike",
            data={"core_so": "mgba_libretro.so", "tied": "2", "regions": ["pal"]},
        )

        notes = self._verdict(adapter, monkeypatch, _core(caveats=(tied,))).notes

        assert [(n.kind, n.tied, n.regions) for n in notes] == [("images-tied", 2, ("pal",))]

    def test_the_world_knowledge_mark_travels(self, adapter, monkeypatch):
        marked = Caveat(
            code="system-firmware-world-knowledge", message="table", data={"system": "gba", "evidence": "verified"}
        )
        core = _core(
            requirements=(_requirement(need="optional"),),
            caveats=(marked,),
            system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
        )

        verdict = self._verdict(adapter, monkeypatch, core)

        assert (verdict.world_knowledge, verdict.system_needs_an_image) == (True, True)

    def test_the_entrys_own_caveat_codes_travel_once_each(self, adapter, monkeypatch):
        """What a search of the BIOS folder established is worded off them."""
        incomplete = Caveat(code=CAVEAT_FIRMWARE_SCAN_INCOMPLETE, message="listing broke off", data={"dir": f"{_ROOT}"})
        core = _core(caveats=(incomplete, incomplete))

        assert self._verdict(adapter, monkeypatch, core).caveats == (CAVEAT_FIRMWARE_SCAN_INCOMPLETE,)


class TestEachPlacementIsOneEmulatorsRequirement:
    """A placement carries its emulator, its own place and the resolver's verdict over it."""

    def test_two_places_for_one_name_stay_two_placements(self, adapter, monkeypatch):
        kronos = _core(
            core_so="kronos_libretro.so",
            requirements=(
                _requirement(
                    core_so="kronos_libretro.so",
                    file_name="saturn_bios.bin",
                    declared="kronos/saturn_bios.bin",
                    path=f"{_ROOT}/kronos/saturn_bios.bin",
                    found=KIND_FILE,
                    checked="verified",
                ),
            ),
        )
        yaba = _core(
            core_so="yabasanshiro_libretro.so",
            requirements=(_requirement(core_so="yabasanshiro_libretro.so", file_name="saturn_bios.bin"),),
        )
        _detect(monkeypatch, _detecting(_Installation(_answer(kronos, yaba))))

        catalogue = adapter()

        kronos_row = catalogue.placements_for("kronos_libretro.so")["saturn_bios.bin"]
        yaba_row = catalogue.placements_for("yabasanshiro_libretro.so")["saturn_bios.bin"]
        assert (kronos_row.relative_path, kronos_row.satisfied) == ("kronos/saturn_bios.bin", True)
        assert (yaba_row.relative_path, yaba_row.satisfied) == ("saturn_bios.bin", False)

    def test_a_wrong_image_is_not_met_though_it_is_there(self, adapter, monkeypatch):
        core = _core(requirements=(_requirement(found=KIND_FILE, checked="mismatch"),))
        _detect(monkeypatch, _detecting(_Installation(_answer(core))))

        placement = adapter().placements[0]

        assert (placement.present, placement.satisfied, placement.emulator) == (True, False, "mgba_libretro.so")

    def test_a_place_it_could_not_look_at_is_not_established(self, adapter, monkeypatch):
        core = _core(requirements=(_requirement(found=KIND_INACCESSIBLE),))
        _detect(monkeypatch, _detecting(_Installation(_answer(core))))

        assert adapter().placements[0].satisfied is None

    def test_the_groups_own_verdict_travels(self, adapter, monkeypatch):
        group = FirmwareAlternatives(
            options=(
                _requirement(file_name="scph5500.bin", regions=("ntsc-j",), found=KIND_FILE, checked="verified"),
                _requirement(file_name="scph5501.bin", regions=("ntsc-u",), found=KIND_FILE, checked="verified"),
            )
        )
        mixed = FirmwareAlternatives(
            options=(
                _requirement(file_name="scph5500.bin", regions=("ntsc-j",), found=KIND_FILE, checked="verified"),
                _requirement(file_name="scph5501.bin", regions=("ntsc-u",)),
            )
        )
        _detect(monkeypatch, _detecting(_Installation(_answer(_core(requirements=(group,))))))
        assert adapter().groups[0].satisfied is True
        _detect(monkeypatch, _detecting(_Installation(_answer(_core(requirements=(mixed,))))))
        assert adapter().groups[0].satisfied is None
