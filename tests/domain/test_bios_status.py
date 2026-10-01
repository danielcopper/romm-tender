"""Tests for the BIOS readiness rule — the level, the token, and the third axis.

The classification is pure, so it is tested here without a machine and without
the resolver's types. What ties the console-firmware spellings to the resolver's
own constants is ``tests/adapters/test_atlas_firmware.py``; what is under test
here is the RULE — which answer the console's disjunctive demand produces, and
what the verdict does with each of them.
"""

from __future__ import annotations

import dataclasses

import pytest

from domain.bios_status import (
    BIOS_LABEL_MISSING,
    BIOS_LABEL_UNKNOWN,
    BIOS_LEVEL_MISSING,
    BIOS_LEVEL_OK,
    BIOS_LEVEL_PARTIAL,
    BIOS_LEVEL_UNKNOWN,
    SYSTEM_IMAGE_ABSENT,
    SYSTEM_IMAGE_HELD,
    SYSTEM_IMAGE_NOT_DEMANDED,
    SYSTEM_IMAGE_UNSETTLED,
    BiosFileEntry,
    BiosStatus,
    build_file_entry,
    classify_system_image,
    compute_bios_label,
    compute_bios_level,
    count_required,
    count_required_partial,
    count_required_withheld,
    count_wanted,
)
from domain.firmware_groups import GROUP_MET, GROUP_PARTIAL, GROUP_UNKNOWN, GROUP_UNMET, GroupVerdict
from domain.firmware_wants import (
    DECLARED_DIRECTORY,
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_OPEN,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    WANTED_OPTIONAL,
    WANTED_UNKNOWN,
    CoreFirmwareVerdict,
    FirmwareGroup,
    FirmwareOption,
    FirmwarePlacement,
    FirmwareWant,
    FolderVerdict,
    MissingConfiguredImage,
)

_CORE = "swanstation_libretro"
_ALTERNATIVE_CORE = "pcsx_rearmed_libretro"


def _image(name: str, *, satisfied: bool | None, core: str = _CORE) -> BiosFileEntry:
    """One PlayStation BIOS row as SwanStation declares it — optional, always.

    ``optional`` is not a simplification: a libretro ``.info`` has no way to say
    the console will not boot without one of these, so every image the core
    declares carries that flag and the file counts read "nothing required".
    """
    return BiosFileEntry(
        file_name=name,
        downloaded=bool(satisfied),
        local_path=f"/bios/{name}",
        declared_path=name,
        description=f"{name} (PS1 BIOS)",
        wanted=WANTED_OPTIONAL,
        required_by_active=False,
        cores={core: {"required": False}},
        used_by_active=True,
        satisfied=satisfied,
    )


def _withheld_folder_row() -> BiosFileEntry:
    """The LRPS2 folder row nothing could judge — required by the launching core.

    ``required_by_active`` is what carries the active core onto the row: the
    plugin sets it from that core's own entry in ``cores``, so such a row is
    always one of the rows the console's disjunction is read over.
    """
    return BiosFileEntry(
        file_name="pcsx2/bios",
        downloaded=True,
        local_path="/bios/pcsx2/bios",
        declared_path="pcsx2/bios",
        description="PS2 BIOS folder",
        wanted=WANTED_OPTIONAL,
        required_by_active=True,
        cores={_CORE: {"required": True}},
        used_by_active=True,
        satisfied=None,
    )


def _status(files: tuple[BiosFileEntry, ...] = (), **overrides) -> BiosStatus:
    kwargs = {
        "platform_slug": "psx",
        "server_count": len(files),
        "local_count": sum(1 for f in files if f.downloaded),
        "all_downloaded": all(f.downloaded for f in files),
        "required_count": 0,
        "required_downloaded": 0,
        "files": files,
        "known_count": len(files),
        "unknown_count": 0,
    }
    kwargs.update(overrides)
    return BiosStatus(**kwargs)  # type: ignore[arg-type]


class TestClassifySystemImage:
    """The console's own demand on the launching core — disjunctive, never counted."""

    def test_a_console_needing_an_image_with_none_held_is_absent(self):
        # The defect this axis exists for: SwanStation marks all five images
        # optional, the counts say nothing is required, and no PlayStation game
        # starts.
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=False)
        files = (_image("scph5500.bin", satisfied=False), _image("scph5501.bin", satisfied=False))

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_ABSENT

    def test_one_held_image_answers_the_whole_disjunction(self):
        # One of these, not each of these: a single satisfied row settles it and
        # the absent rows beside it do not unsettle it.
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=None)
        files = (
            _image("scph5500.bin", satisfied=False),
            _image("scph5501.bin", satisfied=True),
            _image("scph5502.bin", satisfied=False),
        )

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_HELD

    def test_a_row_nothing_could_judge_leaves_the_demand_unsettled(self):
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=None)
        files = (_image("scph5500.bin", satisfied=False), _image("scph5501.bin", satisfied=None))

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_UNSETTLED

    def test_a_console_with_no_row_at_all_is_unsettled_never_absent(self):
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=None)

        assert classify_system_image(verdict, (), _CORE) == SYSTEM_IMAGE_UNSETTLED

    @pytest.mark.parametrize("requirements_met", [True, False, None])
    def test_a_held_image_is_held_whatever_the_resolvers_own_verdict_says(self, requirements_met):
        # The axis is a reading and carries no conflict logic: demand from the
        # system table, presence from the rows. ``requirements_met`` is not a
        # second opinion on the same question — ignorance there is ``None``, so a
        # ``False`` states that something ELSE is unmet (another required file,
        # or wrong bytes), and both of those are already red on a row of their
        # own, by name.
        verdict = CoreFirmwareVerdict(
            system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=requirements_met
        )
        files = (_image("scph5500.bin", satisfied=False), _image("scph5501.bin", satisfied=True))

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_HELD

    @pytest.mark.parametrize(
        "state", [SYSTEM_FIRMWARE_CORE_ALTERNATIVE, SYSTEM_FIRMWARE_RUNS_WITHOUT, SYSTEM_FIRMWARE_OPEN, None]
    )
    def test_every_other_recording_leaves_the_rows_to_speak(self, state):
        # Including ``None`` — nothing recorded about this console. It changes
        # nothing here, and what it must never do is downstream: be spent as a
        # green claim that the console needs nothing.
        verdict = CoreFirmwareVerdict(system_firmware=state, requirements_met=None)
        files = (_image("scph5500.bin", satisfied=False),)

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_NOT_DEMANDED

    def test_a_core_nothing_was_recorded_for_is_answered_for_nobody(self):
        assert classify_system_image(None, (_image("scph5500.bin", satisfied=False),), _CORE) == (
            SYSTEM_IMAGE_NOT_DEMANDED
        )

    def test_an_unresolvable_active_core_is_no_licence_to_answer_for_one(self):
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=False)

        assert classify_system_image(verdict, (_image("scph5500.bin", satisfied=False),), None) == (
            SYSTEM_IMAGE_NOT_DEMANDED
        )

    def test_a_withheld_required_row_cannot_hold_with_an_absent_image(self):
        # The combination ``compute_bios_level``'s ordering guards against does
        # not arise, and the prose about that ordering rests on this: a
        # ``required_by_active`` row always carries the active core, so it is one
        # of the rows the disjunction is read over, and one nothing could judge
        # leaves the answer unsettled rather than absent.
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=False)
        files = (_image("scph5500.bin", satisfied=False), _withheld_folder_row())

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_UNSETTLED

    def test_the_disjunction_spans_only_the_launching_cores_own_images(self):
        # A file three other cores declare is not this launch's prerequisite —
        # the same scoping the required counts take, one axis over.
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=False)
        files = (
            _image("scph5500.bin", satisfied=False),
            _image("gb_bios.bin", satisfied=True, core="gambatte_libretro"),
        )

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_ABSENT


class TestTheVerdictOverTheSystemImage:
    """What the level and the token do with each of the four answers."""

    def test_an_absent_image_turns_a_green_count_red(self):
        status = _status((_image("scph5500.bin", satisfied=False),), system_image=SYSTEM_IMAGE_ABSENT)

        assert compute_bios_level(status) == BIOS_LEVEL_MISSING
        assert compute_bios_label(status) == BIOS_LABEL_MISSING

    def test_an_absent_image_outranks_a_platform_nothing_could_be_established_for(self):
        # The ordering, stated over a state the real builders cannot produce:
        # an unread launching emulator declares nothing, so no row carries it and
        # `classify_system_image` answers 'unsettled' rather than 'absent'. The
        # order is therefore a guard rather than a live case — and it is pinned
        # anyway, because it is what decides which way a future builder that CAN
        # produce both would fall: a demonstration is a claim, and 'missing'
        # says more than 'unknown'.
        #
        # The counts come off the file list through the same helper the service
        # uses, so what this pins is that the state exists rather than that a
        # hand-written pair of numbers can be typed.
        unanswerable = BiosFileEntry(
            file_name="scph7003.bin",
            downloaded=False,
            local_path="/bios/scph7003.bin",
            declared_path="scph7003.bin",
            description="scph7003.bin",
            wanted=WANTED_UNKNOWN,
            required_by_active=False,
            cores={},
            used_by_active=True,
            satisfied=None,
        )
        declared = _image("scph5500.bin", satisfied=False)
        files = (dataclasses.replace(declared, on_server=False), unanswerable)
        known, unknown = count_wanted(files)
        status = _status(
            files,
            server_count=sum(1 for f in files if f.on_server),
            known_count=known,
            unknown_count=unknown,
            reading_complete=False,
            system_image=SYSTEM_IMAGE_ABSENT,
        )

        # The decline is live: take the console's answer away and this platform
        # is exactly the one nothing could be established for.
        assert compute_bios_level(dataclasses.replace(status, system_image=SYSTEM_IMAGE_NOT_DEMANDED)) == (
            BIOS_LEVEL_UNKNOWN
        )
        assert compute_bios_level(status) == BIOS_LEVEL_MISSING

    def test_an_unread_launching_emulator_declines_over_another_emulators_rows(self):
        """The rows on the page belong to somebody else, and they are not an all-clear.

        With the launching emulator unread, ``required_by_active`` is zero by
        construction — it declared nothing anyone here knows about — so the counts
        read "nothing required" over a console nobody asked about. A stock
        RetroDECK reaches this on PS2: LRPS2 declares a folder and a data file,
        and with standalone PCSX2 launching, neither row says what PCSX2 wants.
        """
        files = (dataclasses.replace(_image("GameIndex.yaml", satisfied=True), required_by_active=False),)
        known, unknown = count_wanted(files)
        status = _status(
            files,
            server_count=sum(1 for f in files if f.on_server),
            known_count=known,
            unknown_count=unknown,
            reading_complete=False,
        )

        assert compute_bios_level(status) == BIOS_LEVEL_UNKNOWN
        assert compute_bios_label(status) == BIOS_LABEL_UNKNOWN
        # The same rows under a reading that DID cover the launching emulator are
        # a finished answer, so the decline is the reading's and not the rows'.
        assert compute_bios_level(dataclasses.replace(status, reading_complete=True)) == BIOS_LEVEL_OK

    def test_an_unsettled_image_turns_a_green_count_grey(self):
        status = _status((_image("scph5500.bin", satisfied=None),), system_image=SYSTEM_IMAGE_UNSETTLED)

        assert compute_bios_level(status) == BIOS_LEVEL_UNKNOWN
        assert compute_bios_label(status) == BIOS_LABEL_UNKNOWN

    def test_an_unsettled_image_never_unsays_a_count_that_is_already_red(self):
        # Something is known to be absent; a doubt about one further file does
        # not turn that back into "nothing could be established".
        status = _status(
            (_image("scph5500.bin", satisfied=False),),
            required_count=2,
            required_downloaded=1,
            system_image=SYSTEM_IMAGE_UNSETTLED,
        )

        assert compute_bios_level(status) == BIOS_LEVEL_PARTIAL
        assert compute_bios_label(status) == "1/2 required"

    @pytest.mark.parametrize("answer", [SYSTEM_IMAGE_NOT_DEMANDED, SYSTEM_IMAGE_HELD])
    def test_the_two_quiet_answers_change_nothing(self, answer):
        status = _status((_image("scph5501.bin", satisfied=True),), system_image=answer)

        assert compute_bios_level(status) == BIOS_LEVEL_OK
        assert compute_bios_label(status) == "OK"

    def test_the_default_is_the_quiet_answer(self):
        """A caller that supplies nothing keeps the verdict it always got."""
        assert _status((_image("scph5501.bin", satisfied=True),)).system_image == SYSTEM_IMAGE_NOT_DEMANDED


class TestTheRegisterARowsProseIsWrittenIn:
    """The declaration state travels onto the row, or says nothing at all.

    A surface decides whether to print ``description`` from it, and the two
    registers under that one field are a packager's label and atlas's own
    sentences. Nothing here may be inferred from the row: a file no emulator
    declared has a description too — its own name — and it is not written in
    either register.
    """

    def test_the_row_carries_the_placements_declaration(self):
        entry = build_file_entry(
            "scph1001.bin",
            False,
            "/bios/scph1001.bin",
            FirmwarePlacement(
                file_name="scph1001.bin",
                relative_path="scph1001.bin",
                description="a PlayStation BIOS image — the console runs it before any disc",
                wants=(FirmwareWant(emulator="DUCKSTATION", required=True),),
                declaration="packaged",
            ),
            True,
            "DUCKSTATION",
        )

        assert entry.declaration == "packaged"

    def test_a_row_nothing_declared_states_no_declaration(self):
        """Its description is the file name, and no emulator wrote that."""
        entry = build_file_entry("stray.bin", False, "/bios/stray.bin", None, True, _CORE)

        assert (entry.description, entry.declaration) == ("stray.bin", None)


class TestWhatACoresEntryOnARowSays:
    """Per emulator: its own word about the file, and the file as an option of its group.

    Two statements, two keys, never folded: Beetle PSX marks ``scph5501.bin``
    optional and lists it as the NTSC-U option of its group, and a surface
    listing several emulators beside one file has to be able to say both.
    """

    _PLACEMENT = FirmwarePlacement(
        file_name="scph5501.bin",
        relative_path="scph5501.bin",
        description="PlayStation BIOS",
        wants=(
            FirmwareWant(emulator=_CORE, required=False),
            FirmwareWant(emulator=_ALTERNATIVE_CORE, required=False),
            FirmwareWant(emulator=None, required=True),
        ),
    )

    _GROUP = FirmwareGroup(
        emulator=_CORE,
        options=(
            FirmwareOption("scph5500.bin", ("ntsc-j",), False),
            FirmwareOption("scph5501.bin", ("ntsc-u",), False),
            FirmwareOption("scph5502.bin", ("pal",), False),
        ),
    )

    def _entry(self, groups=(), *, launching_emulator: str | None = _CORE) -> BiosFileEntry:
        return build_file_entry(
            "scph5501.bin",
            False,
            "/bios/scph5501.bin",
            self._PLACEMENT,
            True,
            launching_emulator,
            groups=groups,
        )

    def test_the_declaration_and_the_group_membership_ride_side_by_side(self):
        cores = self._entry((self._GROUP,)).cores

        assert cores[_CORE] == {"required": False, "one_of": {"regions": ["ntsc-u"], "every_region": False}}
        assert cores[_ALTERNATIVE_CORE] == {"required": False, "one_of": None}

    def test_being_an_option_never_makes_the_file_required(self):
        entry = self._entry((self._GROUP,))

        assert entry.cores[_CORE]["required"] is False
        assert entry.required_by_active is False

    def test_an_emulator_with_no_identity_keeps_its_row_out(self):
        assert set(self._entry((self._GROUP,)).cores) == {_CORE, _ALTERNATIVE_CORE}

    def test_the_row_carries_the_launching_emulators_membership(self):
        """The row field and the per-emulator entry are one answer read twice."""
        entry = self._entry((self._GROUP,))

        assert entry.one_of == {"regions": ["ntsc-u"], "every_region": False}
        assert entry.one_of == entry.cores[_CORE]["one_of"]

    def test_another_emulators_group_is_not_the_launchs(self):
        assert self._entry((self._GROUP,), launching_emulator=_ALTERNATIVE_CORE).one_of is None

    def test_a_caller_with_no_emulator_to_name_claims_no_membership(self):
        assert self._entry((self._GROUP,), launching_emulator=None).one_of is None

    def test_an_image_serving_every_region_says_so(self):
        """SwanStation's search find boots whatever the disc, which a surface words as "every region"."""
        found = FirmwareGroup(
            emulator=_CORE, options=(FirmwareOption("scph5501.bin", ("ntsc-j", "ntsc-u", "pal"), True),)
        )

        assert self._entry((found,)).one_of == {"regions": ["ntsc-j", "ntsc-u", "pal"], "every_region": True}

    def test_the_download_rule_is_carried_on_the_row(self):
        """An option of a region nothing covers is fetched by "Download required"; the row says so."""
        assert self._entry((self._GROUP,)).fetch_for_required is True
        assert self._entry(()).fetch_for_required is False


class TestAOneOfGroupIsOneRequirement:
    """The group counts once, partly covered counts as not met, and the level follows it."""

    @staticmethod
    def _verdict(state: str) -> GroupVerdict:
        return GroupVerdict(state=state, covered=(), missing=(), unchecked=())

    def _status(self, *states: str, files: tuple[BiosFileEntry, ...] = ()) -> BiosStatus:
        groups = tuple(self._verdict(state) for state in states)
        required, done = count_required(files, groups)
        return _status(files, required_count=required, required_downloaded=done, groups=groups)

    @pytest.mark.parametrize(
        ("state", "counts"),
        [(GROUP_MET, (1, 1)), (GROUP_PARTIAL, (1, 0)), (GROUP_UNMET, (1, 0)), (GROUP_UNKNOWN, (1, 0))],
    )
    def test_a_group_is_one_requirement_met_only_when_met(self, state, counts):
        assert count_required((), (self._verdict(state),)) == counts

    def test_the_options_rows_add_nothing_to_the_count(self):
        """Three optional option rows and their group: one requirement, not three and not none."""
        rows = tuple(_image(name, satisfied=False) for name in ("scph5500.bin", "scph5501.bin", "scph5502.bin"))

        assert count_required(rows, (self._verdict(GROUP_PARTIAL),)) == (1, 0)

    @pytest.mark.parametrize(
        ("state", "level", "label"),
        [
            (GROUP_MET, BIOS_LEVEL_OK, "OK"),
            (GROUP_PARTIAL, BIOS_LEVEL_PARTIAL, "0/1 required"),
            (GROUP_UNMET, BIOS_LEVEL_MISSING, BIOS_LABEL_MISSING),
            (GROUP_UNKNOWN, BIOS_LEVEL_UNKNOWN, BIOS_LABEL_UNKNOWN),
        ],
    )
    def test_the_groups_state_decides_the_level(self, state, level, label):
        status = self._status(state)

        assert compute_bios_level(status) == level
        assert compute_bios_label(status) == label

    def test_an_unknown_group_is_withheld_and_a_partial_one_is_counted_apart(self):
        groups = (self._verdict(GROUP_UNKNOWN), self._verdict(GROUP_PARTIAL), self._verdict(GROUP_MET))

        assert count_required_withheld((), groups) == 1
        assert count_required_partial(groups) == 1

    def test_a_missing_required_file_beside_a_partial_group_is_still_partial(self):
        row = dataclasses.replace(_withheld_folder_row(), satisfied=False, downloaded=False)

        assert compute_bios_level(self._status(GROUP_PARTIAL, files=(row,))) == BIOS_LEVEL_PARTIAL

    def test_a_group_with_nothing_in_place_outranks_a_held_required_file(self):
        row = dataclasses.replace(_withheld_folder_row(), satisfied=True)

        assert compute_bios_level(self._status(GROUP_UNMET, files=(row,))) == BIOS_LEVEL_MISSING

    def test_where_the_emulator_states_a_group_the_system_image_stays_silent(self):
        """The group IS the console's demand, said per region; the coarser reading is not a second answer."""
        verdict = CoreFirmwareVerdict(SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT)
        rows = (_image("scph5501.bin", satisfied=False),)

        assert classify_system_image(verdict, rows, _CORE) == SYSTEM_IMAGE_ABSENT
        assert classify_system_image(verdict, rows, _CORE, groups=(self._verdict(GROUP_PARTIAL),)) == (
            SYSTEM_IMAGE_NOT_DEMANDED
        )


class TestAStaleConfiguredNameChangesNothing:
    """LRPS2 configured to open a file that is not there lists its folder instead — the verdict stands."""

    _FOLDER = FirmwarePlacement(
        file_name="bios",
        relative_path="pcsx2/bios",
        description="'pcsx2/bios' folder",
        wants=(FirmwareWant(emulator="pcsx2_libretro.so", required=True),),
        declared_kind=DECLARED_DIRECTORY,
        caveats=("firmware-configured-image-missing",),
        folder=FolderVerdict(satisfied=True, images=("SCPH-70004",)),
        missing_configured_image=MissingConfiguredImage("LRPS2", "scph10000.bin"),
    )

    def test_the_row_carries_the_setting_and_keeps_its_verdict(self):
        entry = build_file_entry("bios", True, "/bios/pcsx2/bios", self._FOLDER, True, "pcsx2_libretro.so")

        assert entry.missing_configured_image == {"emulator_label": "LRPS2", "file_name": "scph10000.bin"}
        assert entry.satisfied is True

    def test_the_level_over_it_is_the_folders(self):
        entry = build_file_entry("bios", True, "/bios/pcsx2/bios", self._FOLDER, True, "pcsx2_libretro.so")
        required, done = count_required((entry,))

        assert compute_bios_level(_status((entry,), required_count=required, required_downloaded=done)) == (
            BIOS_LEVEL_OK
        )
