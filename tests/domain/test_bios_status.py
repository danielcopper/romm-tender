"""Tests for the BIOS readiness rule — the level, the token, and the third axis.

The classification is pure, so it is tested here without a machine and without
the resolver's types. What ties the console-firmware spellings to the resolver's
own constants is ``tests/adapters/test_atlas_firmware.py``; what is under test
here is the RULE — which answer the console's disjunctive demand produces, and
what the verdict does with each of them.
"""

from __future__ import annotations

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
    classify_system_image,
    compute_bios_label,
    compute_bios_level,
)
from domain.firmware_wants import (
    SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT,
    SYSTEM_FIRMWARE_CORE_ALTERNATIVE,
    SYSTEM_FIRMWARE_OPEN,
    SYSTEM_FIRMWARE_RUNS_WITHOUT,
    WANTED_OPTIONAL,
    CoreFirmwareVerdict,
)

_CORE = "swanstation_libretro"


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
        # nineteen absent ones do not unsettle it.
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

    def test_the_resolvers_refusal_can_only_take_a_hold_back_to_unsettled(self):
        # ``requirements_met`` folds the per-file conjunction and the console's
        # own disjunction into one answer, so it can say the core will not start
        # and cannot say which of the two is why. Read in the narrowing direction
        # only: a disagreement with the rows is not a claim, and never a hold.
        verdict = CoreFirmwareVerdict(system_firmware=SYSTEM_FIRMWARE_CANNOT_RUN_WITHOUT, requirements_met=False)
        files = (_image("scph5501.bin", satisfied=True),)

        assert classify_system_image(verdict, files, _CORE) == SYSTEM_IMAGE_UNSETTLED

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

    def test_an_absent_image_outranks_a_withheld_required_row(self):
        # A demonstration beats an unjudged row — the same precedence the
        # resolver applies to its own two.
        withheld = BiosFileEntry(
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
        status = _status(
            (withheld,),
            required_count=1,
            required_downloaded=0,
            system_image=SYSTEM_IMAGE_ABSENT,
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
