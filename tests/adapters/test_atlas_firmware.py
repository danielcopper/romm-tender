"""Tests for the atlas firmware adapters — the translation into plugin vocabulary.

What is under test is the adapters' own work: folding the resolver's per-entry
answer into one row per file, keying everything on the emulator IDENTITY rather
than on a core file or a display label, naming the emulators that could not be
asked, deciding whether a declared location is one the plugin's own BIOS root can
honour, and refusing to turn any failure into "nothing needed". The resolver's
own decisions are upstream's and are not re-tested here — including what it read
at a destination, which is carried through rather than re-derived.

Answers are built from real atlas value objects rather than mocks, so every
invariant the resolver enforces on its own shapes is enforced on the fixtures
too — a fixture that could not come off a real machine fails to construct.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from _vendor.atlas import (
    CAVEAT_FIRMWARE_DIRECTORY_HOLDS_NO_CANDIDATE,
    CAVEAT_FIRMWARE_IDENTITY_NOT_COMPARABLE,
    CAVEAT_FIRMWARE_IMAGE_IDENTIFIED,
    CAVEAT_FIRMWARE_IMAGE_UNLISTED,
    CAVEAT_FIRMWARE_PATH_OBSTRUCTED,
    CAVEAT_FIRMWARE_SEARCH_UNVERIFIED,
)
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

from adapters.atlas_firmware import AtlasFirmwareAdapter, AtlasPlatformFirmwareAdapter
from domain.firmware_wants import (
    CAVEAT_PATH_OBSTRUCTED,
    DECLARED_DIRECTORY,
    DECLARED_FILE,
    SYSTEM_FIRMWARE_STATES,
)

if TYPE_CHECKING:
    from types import EllipsisType

_ROOT = "/home/deck/retrodeck/bios"


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
        system="gba",
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
    """
    if declaration != "read" and not caveats:
        caveats = (Caveat(code="core-info-unreadable", message="its .info could not be read"),)
    if refused and not caveats:
        caveats = (Caveat(code="firmware-declaration-leaves-root", message="leaves the root"),)
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

    kind = "retrodeck"

    def __init__(self, answer: FirmwareAnswer | Exception) -> None:
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


@pytest.fixture
def adapter(traces):
    return AtlasFirmwareAdapter(user_home="/home/deck", log_debug=traces.append)


def _detecting(*installations):
    def detect(home, machine=None):
        return list(installations)

    return detect


def catalogue_emulators(catalogue) -> set[str | None]:
    return {want.emulator for placement in catalogue.placements for want in placement.wants}


class TestPlacements:
    def test_one_row_per_file_folds_every_core_that_declares_it(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so="mednafen_psx_libretro.so", requirements=(_requirement(file_name="scph5501.bin"),)),
            _core(
                core_so="swanstation_libretro.so",
                requirements=(_requirement(file_name="scph5501.bin", need="optional"),),
            ),
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        catalogue = adapter()

        assert [p.file_name for p in catalogue.placements] == ["scph5501.bin"]
        placement = catalogue.placements[0]
        assert {(w.emulator, w.required) for w in placement.wants} == {
            ("mednafen_psx_libretro.so", True),
            ("swanstation_libretro.so", False),
        }
        assert placement.required_by_any is True

    def test_the_identifier_is_the_resolvers_own_spelling(self, adapter, monkeypatch):
        """One identity per emulator, stated by the resolver and never rewritten here.

        The plugin used to strip the ``.so`` and key on the bare basename, which
        left a standalone emulator with no name at all. The identity is now the
        one field that carries both kinds, so it travels verbatim.
        """
        answer = _answer(_core(core_so="mgba_libretro.so", requirements=(_requirement(),)))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        placement = adapter().placements[0]

        assert [(w.emulator, w.required) for w in placement.wants] == [("pcsx2_libretro.so", True)]

    def test_an_entry_the_resolver_could_not_identify_still_owns_its_file(self, adapter, monkeypatch):
        """EmuDeck's ``n3ds`` rows: a launch atlas classifies standalone and cannot name.

        The file is genuinely declared, so dropping the want would understate the
        demand — but nothing can be scoped to an entry with no name, so the want
        carries none and every identity-keyed reading passes it over.
        """
        answer = _answer(
            _core(core_so=None, emulator=None, declaration="packaged", requirements=(_requirement(core_so=None),))
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path == "dolphin-emu/Sys/codehandler.bin"

    def test_a_destination_outside_the_root_has_no_placement_to_honour(self, adapter, monkeypatch):
        answer = _answer(
            _core(
                core_so="melonds_libretro.so",
                requirements=(_requirement(file_name="bios7.bin", path="/home/deck/.local/share/melonDS/bios7.bin"),),
            )
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path == "pcsx2/bios"

    def test_an_absolute_declaration_has_no_placement_under_our_root(self, adapter, monkeypatch):
        """A location stated as an address is not one this plugin can join under its own root."""
        answer = _answer(
            _core(
                core_so="duckstation_libretro.so",
                requirements=(
                    _requirement(file_name="scph5501.bin", declared=f"{_ROOT}/scph5501.bin", path=f"{_ROOT}/x.bin"),
                ),
            )
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().placements[0].relative_path is None


class TestDestinationReadings:
    """What the resolver read AT the destination, carried instead of re-derived."""

    def _placement(self, adapter, monkeypatch, requirement):
        answer = _answer(_core(core_so="mgba_libretro.so", requirements=(requirement,)))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))
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
        """The resolver's own display form — the plugin never maps an identifier itself."""
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
        """An emulator keeping its firmware in its own tree: that file is not the plugin's.

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert [p.file_name for p in adapter().placements] == ["scph5501.bin", "scph5502.bin"]

    def test_a_core_that_declares_nothing_contributes_nothing(self, adapter, monkeypatch):
        """gearboy, gearsystem and geargrafx: read, and asking for nothing."""
        answer = _answer(
            _core(core_so="gearboy_libretro.so"),
            _core(core_so="gearsystem_libretro.so"),
            _core(core_so="geargrafx_libretro.so"),
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        catalogue = adapter()
        assert catalogue.placements == ()
        assert catalogue.resolved is True
        assert catalogue.unread_emulators == frozenset()


class TestUnreadEmulators:
    def test_an_unreadable_core_is_named(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so="mgba_libretro.so", requirements=(_requirement(),)),
            _core(core_so="fbalpha_libretro.so", declaration="unreadable"),
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"fbalpha_libretro.so"})

    def test_a_refused_declaration_counts_as_unread(self, adapter, monkeypatch):
        """The core does want something; the resolver just would not place it."""
        answer = _answer(
            _core(
                core_so="odd_libretro.so",
                refused=(RefusedDeclaration(declared="../escape.bin", need="required", reason="leaves-root"),),
            )
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"odd_libretro.so"})

    def test_an_unsupported_emulator_counts_as_unread(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so=None, emulator="DOLPHIN", label="Dolphin (Standalone)", declaration="unsupported")
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset()

    def test_a_read_core_that_declares_nothing_is_never_named(self, adapter, monkeypatch):
        """``read`` and empty is the one pairing that means "this emulator needs none"."""
        answer = _answer(_core(core_so="gearboy_libretro.so"))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        catalogue = adapter()

        assert catalogue.unread_emulators == frozenset()
        assert catalogue_emulators(catalogue) == {"pcsx2_libretro.so"}

    def test_one_emulator_declared_twice_is_unread_when_neither_row_stated(self, adapter, monkeypatch):
        answer = _answer(
            _core(core_so="pcsx2_libretro.so", label="LRPS2", declaration="unreadable"),
            _core(core_so="pcsx2_libretro.so", label="PCSX2", declaration="unreadable"),
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset({"pcsx2_libretro.so"})

    def test_an_entry_with_no_identity_names_nobody(self, adapter, monkeypatch):
        """There is no name to put in the set, and no caller could ask about one."""
        answer = _answer(_core(core_so=None, emulator=None, declaration="unsupported"))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset()

    def test_a_read_core_is_never_named(self, adapter, monkeypatch):
        answer = _answer(_core(core_so="mgba_libretro.so", requirements=(_requirement(),)))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().unread_emulators == frozenset()


class TestDegradation:
    def test_a_raising_resolver_answers_unresolved_not_empty(self, adapter, monkeypatch, traces):
        """The one failure mode that must never read as 'this platform needs none'."""
        monkeypatch.setattr(
            "adapters.atlas_firmware.detect",
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

        monkeypatch.setattr("adapters.atlas_firmware.detect", detect)

        assert adapter().resolved is False

    def test_no_installation_answers_unresolved(self, adapter, monkeypatch, traces):
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting())

        catalogue = adapter()

        assert catalogue.resolved is False
        assert any("no emulator installation" in trace for trace in traces)

    def test_an_answer_without_a_root_is_unresolved(self, adapter, monkeypatch):
        """No firmware root means no destination to resolve anything against."""
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(_answer(root=None))))

        catalogue = adapter()

        assert catalogue.resolved is False
        assert catalogue.caveats == ("firmware-root-unstated",)

    def test_the_first_detected_installation_answers(self, adapter, monkeypatch):
        """Detection orders its finds; the plugin takes the leader, never a merge."""
        first = _Installation(_answer(_core(requirements=(_requirement(file_name="first.bin"),))))
        second = _Installation(_answer(_core(requirements=(_requirement(file_name="second.bin"),))))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(first, second))

        assert [p.file_name for p in adapter().placements] == ["first.bin"]


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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert set(adapter().caveats) == {"firmware-path-obstructed", "core-info-unreadable"}

    def test_the_trace_names_the_codes_and_the_arrangement(self, adapter, monkeypatch, traces):
        answer = _answer(_core(requirements=(_requirement(),)))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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

    def test_the_obstruction_code_matches(self):
        assert CAVEAT_PATH_OBSTRUCTED == CAVEAT_FIRMWARE_PATH_OBSTRUCTED

    def test_the_system_firmware_states_match(self):
        assert SYSTEM_FIRMWARE_STATES == CORE_SYSTEM_FIRMWARE_STATES


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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        catalogue = adapter()

        assert catalogue.verdict_for("swanstation_libretro.so").system_firmware == SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT
        assert catalogue.verdict_for("swanstation_libretro.so").system_needs_an_image is True
        assert catalogue.verdict_for("pcsx_rearmed_libretro.so").system_needs_an_image is False

    @pytest.mark.parametrize(
        "state", [SYSTEM_FIRMWARE_RUNS_WITHOUT, SYSTEM_FIRMWARE_OPEN, SYSTEM_FIRMWARE_CORE_ALTERNATIVE]
    )
    def test_no_other_recorded_state_demands_an_image(self, adapter, monkeypatch, state):
        answer = _answer(_core(requirements=(_requirement(),), system_firmware=state))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        verdict = adapter().verdict_for("mgba_libretro.so")

        assert verdict.system_firmware == state
        assert verdict.system_needs_an_image is False

    def test_a_core_the_table_records_nothing_about_carries_the_absence(self, adapter, monkeypatch):
        """``None`` is an unasked question, and it must arrive as one."""
        answer = _answer(_core(requirements=(_requirement(),)))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        verdict = adapter().verdict_for("mgba_libretro.so")

        assert verdict.system_firmware is None
        assert verdict.system_needs_an_image is False

    def test_the_resolvers_own_verdict_travels_beside_it(self, adapter, monkeypatch):
        """``requirements_met`` is atlas's, not ours — carried, and read by nothing.

        The adapter still passes it through, so a future consumer meets the
        resolver's own answer rather than one this layer invented. What no
        consumer may do is weigh it against the file rows: see
        ``domain/bios_status.py::classify_system_image``.
        """
        answer = _answer(
            _core(
                core_so="swanstation_libretro.so",
                requirements=(_requirement(core_so="swanstation_libretro.so", need="optional"),),
                system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
            )
        )
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().verdict_for("swanstation_libretro.so").requirements_met is False

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().emulator_verdicts == {}

    def test_a_caller_with_no_emulator_to_name_is_answered_for_nobody(self, adapter, monkeypatch):
        answer = _answer(_core(requirements=(_requirement(),), system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT))
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

        assert adapter().verdict_for(None) is None

    def test_a_reading_that_did_not_happen_records_nothing(self, adapter, monkeypatch):
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(ValueError("nope"))))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))

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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))
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
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(answer)))
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
    return AtlasPlatformFirmwareAdapter(user_home="/home/deck", log_debug=traces.append)


class TestPlatformAdapter:
    """The per-system reading: what one platform's emulators want, contents and all."""

    def _catalogue(self, platform_adapter, monkeypatch, answer, system="ps2"):
        installation = _Installation(answer)
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(installation))
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
        monkeypatch.setattr(
            "adapters.atlas_firmware.detect",
            _detecting(_Installation(ValueError("FirmwareRequirement: need must be one of ..."))),
        )

        catalogue = platform_adapter("ps2")

        assert catalogue.resolved is False
        assert catalogue.placements == ()
        assert any("resolver failed for ps2" in trace for trace in traces)

    def test_no_installation_answers_unresolved(self, platform_adapter, monkeypatch):
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting())

        assert platform_adapter("ps2").resolved is False

    def test_an_answer_without_a_root_answers_unresolved(self, platform_adapter, monkeypatch):
        monkeypatch.setattr("adapters.atlas_firmware.detect", _detecting(_Installation(_answer(root=None))))

        catalogue = platform_adapter("ps2")

        assert catalogue.resolved is False
        assert catalogue.placements == ()
