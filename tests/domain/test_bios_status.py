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
    count_wanted,
)
from domain.firmware_wants import (
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_OPEN,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    WANTED_OPTIONAL,
    WANTED_UNKNOWN,
    CoreFirmwareVerdict,
    FirmwarePlacement,
    FirmwareWant,
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
        "active_core": _CORE,
        "active_core_label": "SwanStation",
        "available_cores": (),
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
        # The one decline the ordering really decides against, and it is
        # reachable: the library's own rows all went unanswered under an
        # incomplete reading, while the images the core declares are rows the
        # library does not hold and every one of them is absent. A demonstration
        # is a claim, so the level is 'missing' rather than 'unknown'.
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


class TestWhatACoresEntryOnARowSays:
    """Per core: its own word about the file, and the table's word about its console.

    Two speakers, two keys, and the pair is what a surface listing several
    emulators has to be able to word — a core that marks the file ``optional``
    while its console will not start without one of the five images it declares
    is the informative case, and it is the deployed catalogue's SwanStation.
    """

    _PLACEMENT = FirmwarePlacement(
        file_name="scph5501.bin",
        relative_path="scph5501.bin",
        description="PlayStation BIOS",
        wants=(
            FirmwareWant(core_so=_CORE, required=False),
            FirmwareWant(core_so=_ALTERNATIVE_CORE, required=False),
            FirmwareWant(core_so=None, required=True),
        ),
    )

    def _entry(self, cores_needing_one_of=None, *, active_core_so: str | None = _CORE) -> BiosFileEntry:
        return build_file_entry(
            "scph5501.bin",
            False,
            "/bios/scph5501.bin",
            self._PLACEMENT,
            True,
            active_core_so,
            cores_needing_one_of=cores_needing_one_of if cores_needing_one_of is not None else {},
        )

    def _cores(self, cores_needing_one_of=None) -> dict[str, dict[str, object]]:
        return self._entry(cores_needing_one_of).cores

    def test_the_declaration_and_the_consoles_demand_ride_side_by_side(self):
        cores = self._cores({_CORE: 5})

        assert cores[_CORE] == {"required": False, "needs_one_of": 5}
        assert cores[_ALTERNATIVE_CORE] == {"required": False, "needs_one_of": None}

    def test_the_declaration_is_carried_unaltered(self):
        """The core's own word never moves with the console's demand."""
        for cores_needing_one_of in ({}, {_CORE: 5}):
            assert self._cores(cores_needing_one_of)[_CORE]["required"] is False

    def test_a_caller_that_names_no_core_claims_nothing_for_any_of_them(self):
        """The default is silence, not a demand — an unasked question is not an answer."""
        assert all(core["needs_one_of"] is None for core in self._cores().values())

    def test_an_emulator_with_no_core_of_its_own_keeps_its_row_out(self):
        """A standalone emulator names no ``.so``, so there is no key to answer under."""
        assert set(self._cores({_CORE: 5})) == {_CORE, _ALTERNATIVE_CORE}

    def test_the_row_is_a_candidate_where_the_launching_core_states_the_disjunction(self):
        """The row flag and the per-core entry are one answer read twice."""
        entry = self._entry({_CORE: 5})

        assert entry.system_image_candidate is True
        assert entry.cores[_CORE]["needs_one_of"] == 5

    def test_a_row_the_launching_core_does_not_state_a_disjunction_for_is_not_a_candidate(self):
        """PCSX ReARMed declares the same file and carries its own substitute."""
        entry = self._entry({_CORE: 5}, active_core_so=_ALTERNATIVE_CORE)

        assert entry.system_image_candidate is False
        assert entry.cores[_ALTERNATIVE_CORE]["needs_one_of"] is None

    def test_a_caller_with_no_core_to_name_claims_no_candidate(self):
        """An unresolvable active core is not a licence to answer for one."""
        assert self._entry({_CORE: 5}, active_core_so=None).system_image_candidate is False

    def test_a_candidate_that_is_there_answers_the_console(self):
        """Candidate + met implies ``held``, which is what lets a row say so alone.

        The candidates are a subset of the rows :func:`classify_system_image`
        weighs, so a satisfied one cannot leave the console's own answer
        anywhere else — a surface may therefore draw such a row "this starts the
        system" without consulting the platform value beside it.
        """
        rows = tuple(
            build_file_entry(
                name,
                downloaded,
                f"/bios/{name}",
                self._PLACEMENT,
                True,
                _CORE,
                cores_needing_one_of={_CORE: 5},
            )
            for name, downloaded in (("scph5500.bin", False), ("scph5501.bin", True))
        )

        assert all(row.system_image_candidate for row in rows)
        assert classify_system_image(CoreFirmwareVerdict(SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT), rows, _CORE) == (
            SYSTEM_IMAGE_HELD
        )

    def test_a_row_the_launching_core_does_not_declare_is_not_a_candidate(self):
        """Only the images that core opens can answer its console."""
        entry = build_file_entry(
            "gba_bios.bin",
            False,
            "/bios/gba_bios.bin",
            None,
            True,
            _CORE,
            cores_needing_one_of={_CORE: 5},
        )

        assert entry.system_image_candidate is False
