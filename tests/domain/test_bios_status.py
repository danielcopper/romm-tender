"""Tests for the BIOS readiness rule — the level, the token, and the region rule.

The classification is pure, so it is tested here without a machine and without
the resolver's types. What is under test is that the level IS the resolver's
verdict for the launching emulator, that the one exception is read
off the resolver's own statements, and what the token says under
each.
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
    BiosFileEntry,
    BiosStatus,
    build_file_entry,
    classify_content_search,
    compute_bios_label,
    compute_bios_level,
    count_required,
    count_required_withheld,
    region_decides,
)
from domain.firmware_groups import GROUP_MET, GROUP_PARTIAL, GROUP_UNKNOWN, GROUP_UNMET, GroupVerdict
from domain.firmware_wants import (
    DECLARED_DIRECTORY,
    WANTED_NEEDED,
    WANTED_OPTIONAL,
    CoreFirmwareVerdict,
    FirmwareGroup,
    FirmwareOption,
    FirmwarePlacement,
    FirmwareWant,
    FolderVerdict,
    MissingConfiguredImage,
    RefusedDeclaration,
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

    ``required_by_active`` is what carries the active core onto the row:
    Tender sets it from that core's own entry in ``cores``, so such a row is
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


_MET = CoreFirmwareVerdict(requirements_met=True, declaration="read")
_NOT_MET = CoreFirmwareVerdict(requirements_met=False, declaration="read")
_OPEN = CoreFirmwareVerdict(requirements_met=None, declaration="read")


class TestTheLevelIsTheResolversVerdict:
    """The colour is ``requirements_met`` for the launching emulator — never a count."""

    @pytest.mark.parametrize(
        ("verdict", "level"),
        [(_MET, BIOS_LEVEL_OK), (_NOT_MET, BIOS_LEVEL_MISSING), (_OPEN, BIOS_LEVEL_UNKNOWN)],
    )
    def test_the_level_is_the_verdict(self, verdict, level):
        assert compute_bios_level(_status(verdict=verdict)) == level

    def test_the_same_rows_and_counts_take_every_colour_the_verdict_gives(self):
        """No colour is computed from the rows: identical counts read green, red or grey by the verdict alone."""
        files = (dataclasses.replace(_withheld_folder_row(), satisfied=True),)
        status = _status(files, required_count=1, required_downloaded=1)

        levels = [compute_bios_level(dataclasses.replace(status, verdict=v)) for v in (_MET, _NOT_MET, _OPEN)]

        assert levels == [BIOS_LEVEL_OK, BIOS_LEVEL_MISSING, BIOS_LEVEL_UNKNOWN]

    def test_an_emulator_missing_from_the_answer_counts_as_not_read(self):
        """No entry is not an all-clear, whatever the rows of other emulators say."""
        files = (dataclasses.replace(_image("GameIndex.yaml", satisfied=True), required_by_active=False),)

        assert compute_bios_level(_status(files, verdict=None)) == BIOS_LEVEL_UNKNOWN
        assert compute_bios_label(_status(files, verdict=None)) == BIOS_LABEL_UNKNOWN

    def test_a_refused_optional_declaration_leaves_the_verdict_as_the_resolver_gives_it(self):
        """Only a refused REQUIRED declaration withholds anything, and the resolver already did that."""
        refused = RefusedDeclaration(declared="../outside.bin", required=False, reason="firmware-path-escapes-root")
        verdict = dataclasses.replace(_MET, refused=(refused,))

        assert compute_bios_level(_status(verdict=verdict)) == BIOS_LEVEL_OK

    def test_a_not_met_verdict_counts_what_is_there(self):
        """A required file missing reads red as "2 of 3 … in place", and the token still counts."""
        files = (
            dataclasses.replace(_withheld_folder_row(), file_name="a.bin", satisfied=True),
            dataclasses.replace(_withheld_folder_row(), file_name="b.bin", satisfied=True),
            dataclasses.replace(_withheld_folder_row(), file_name="c.bin", satisfied=False),
        )
        status = _status(files, verdict=_NOT_MET)

        assert compute_bios_level(status) == BIOS_LEVEL_MISSING
        assert compute_bios_label(status) == "2/3 required"

    def test_a_not_met_verdict_with_every_plain_file_in_place_is_missing(self):
        """What is missing is no plain file — the console's own image, or a group — so there is no ratio to state."""
        files = (_image("scph5500.bin", satisfied=False), _image("scph5501.bin", satisfied=False))

        assert compute_bios_label(_status(files, verdict=_NOT_MET)) == BIOS_LABEL_MISSING


def _group(*options: tuple[str, tuple[str, ...], bool | None], unchecked: tuple[str, ...] = ()) -> FirmwareGroup:
    verdicts = {satisfied for _name, _regions, satisfied in options}
    return FirmwareGroup(
        emulator=_CORE,
        options=tuple(FirmwareOption(name, regions, satisfied) for name, regions, satisfied in options),
        unchecked_regions=unchecked,
        satisfied=True if verdicts == {True} else False if verdicts == {False} else None,
    )


_JAPAN_ONLY = _group(
    ("scph5500.bin", ("ntsc-j",), True),
    ("scph5501.bin", ("ntsc-u",), False),
    ("scph5502.bin", ("pal",), False),
)
_REGION_OPEN = dataclasses.replace(_OPEN, region_decided_by_disc=True)


class TestTheRegionDecides:
    """Amber only where the verdict is open because of the disc's region alone."""

    def test_a_group_with_one_image_met_and_the_others_not_is_the_region_alone(self):
        """Beetle PSX with only the Japanese image: the platform reads partial (amber)."""
        assert region_decides(_REGION_OPEN, (_JAPAN_ONLY,)) is True
        assert compute_bios_level(_status(verdict=_REGION_OPEN, region_decides=True)) == BIOS_LEVEL_PARTIAL

    def test_an_image_not_established_beside_a_met_one_is_grey(self):
        group = _group(("scph5500.bin", ("ntsc-j",), True), ("scph5501.bin", ("ntsc-u",), None))

        assert region_decides(_REGION_OPEN, (group,)) is False

    def test_a_required_file_not_established_beside_the_group_is_grey(self):
        verdict = dataclasses.replace(_REGION_OPEN, undetermined=("scph1001.bin",))

        assert region_decides(verdict, (_JAPAN_ONLY,)) is False

    def test_a_refused_required_declaration_is_grey(self):
        refused = RefusedDeclaration(declared="../x.bin", required=True, reason="firmware-path-escapes-root")

        assert region_decides(dataclasses.replace(_REGION_OPEN, refused=(refused,)), (_JAPAN_ONLY,)) is False

    def test_a_refused_optional_declaration_changes_nothing(self):
        refused = RefusedDeclaration(declared="../x.bin", required=False, reason="firmware-path-escapes-root")

        assert region_decides(dataclasses.replace(_REGION_OPEN, refused=(refused,)), (_JAPAN_ONLY,)) is True

    def test_without_the_resolvers_note_that_the_disc_decides_it_is_grey(self):
        assert region_decides(_OPEN, (_JAPAN_ONLY,)) is False

    def test_a_region_resting_on_a_read_that_settled_nothing_is_grey(self):
        group = dataclasses.replace(_JAPAN_ONLY, unchecked_regions=("pal",))

        assert region_decides(_REGION_OPEN, (group,)) is False

    def test_a_region_the_group_states_nothing_for_is_grey(self):
        """The resolver's ``unstated``: a region no option serves is something open, never the disc's alone."""
        group = _group(("scph5500.bin", ("ntsc-j",), True), ("scph5501.bin", ("ntsc-u",), False))

        assert region_decides(_REGION_OPEN, (group,)) is True
        assert region_decides(_REGION_OPEN, (dataclasses.replace(group, unstated_regions=("pal",)),)) is False

    def test_another_group_nothing_settled_is_grey(self):
        other = _group(("a.bin", ("ntsc-j",), None), ("b.bin", ("pal",), None))

        assert region_decides(_REGION_OPEN, (_JAPAN_ONLY, other)) is False

    def test_a_met_group_beside_the_mixed_one_changes_nothing(self):
        met = _group(("a.bin", ("ntsc-j", "ntsc-u", "pal"), True))

        assert region_decides(_REGION_OPEN, (_JAPAN_ONLY, met)) is True

    @pytest.mark.parametrize("verdict", [_MET, _NOT_MET, None])
    def test_only_an_open_verdict_can_be_the_regions(self, verdict):
        assert region_decides(verdict, (_JAPAN_ONLY,)) is False

    def test_no_group_is_no_region_to_decide(self):
        assert region_decides(_REGION_OPEN, ()) is False


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

    def test_an_unknown_group_is_withheld(self):
        groups = (self._verdict(GROUP_UNKNOWN), self._verdict(GROUP_PARTIAL), self._verdict(GROUP_MET))

        assert count_required_withheld((), groups) == 1


class TestThePlayRowTokenCountsPlainFilesOnly:
    """The badge token words the verdict over the plain files: their ratio, or "Missing" where none is short.

    The resolver's verdict is stated here the way it would answer these rows —
    a group with nothing in place or a plain file not in place is not met, a
    group or row nothing settled leaves it open — so what is under test is the
    token, not a verdict of Tender's own. The system is invented — its slug,
    core, regions and rows — because the token reads nothing about a console.
    """

    _CORE = "arcadia_libretro"

    @classmethod
    def _plain(cls, name: str, *, here: bool | None) -> BiosFileEntry:
        """A file the invented core requires: in place, shown missing, or nothing could judge it (``None``)."""
        return BiosFileEntry(
            file_name=name,
            downloaded=bool(here),
            local_path=f"/bios/arcadia/{name}",
            declared_path=f"arcadia/{name}",
            description=f"{name} (Arcadia system ROM)",
            wanted=WANTED_NEEDED,
            required_by_active=True,
            cores={cls._CORE: {"required": True}},
            used_by_active=True,
            satisfied=here,
        )

    @staticmethod
    def _verdict(state: str) -> GroupVerdict:
        covered = ("north",) if state in {GROUP_MET, GROUP_PARTIAL} else ()
        missing = ("south",) if state in {GROUP_PARTIAL, GROUP_UNMET} else ()
        unchecked = ("east",) if state == GROUP_UNKNOWN else ()
        return GroupVerdict(state=state, covered=covered, missing=missing, unchecked=unchecked)

    def _label(self, *states: str, plain: tuple[bool | None, ...] = ()) -> str:
        files = tuple(self._plain(f"plain{index}.rom", here=here) for index, here in enumerate(plain))
        groups = tuple(self._verdict(state) for state in states)
        required, done = count_required(files, groups)
        if GROUP_UNMET in states or False in plain:
            met: bool | None = False
        elif GROUP_UNKNOWN in states or None in plain:
            met = None
        else:
            met = True
        return compute_bios_label(
            _status(
                files,
                platform_slug="arcadia",
                required_count=required,
                required_downloaded=done,
                groups=groups,
                verdict=CoreFirmwareVerdict(requirements_met=met),
                region_decides=met is None and GROUP_PARTIAL in states,
            )
        )

    def test_a_group_that_does_not_cover_the_game_beside_a_short_plain_file_counts_the_files(self):
        assert self._label(GROUP_UNMET, plain=(True, False)) == "1/2 required"

    def test_a_group_that_does_not_cover_the_game_beside_every_plain_file_is_missing(self):
        assert self._label(GROUP_UNMET, plain=(True,)) == BIOS_LABEL_MISSING

    def test_a_covering_group_leaves_the_count_to_the_plain_files(self):
        assert self._label(GROUP_MET, plain=(True, False)) == "1/2 required"

    def test_a_covering_group_with_no_plain_file_in_place_still_counts(self):
        """An image is there, so "Missing" would overstate it."""
        assert self._label(GROUP_MET, plain=(False,)) == "0/1 required"
        assert self._label(GROUP_PARTIAL, plain=(False,)) == "0/1 required"

    def test_a_partial_group_with_every_plain_file_in_place_is_ok(self):
        assert self._label(GROUP_PARTIAL, plain=(True,)) == "OK"

    def test_without_a_group_the_token_is_the_plain_files_ratio(self):
        assert self._label(plain=(False, False)) == "0/2 required"
        assert self._label(plain=(True, False)) == "1/2 required"
        assert self._label(plain=(True,)) == "OK"

    def test_a_plain_file_shown_missing_is_counted_beside_a_group_nothing_could_check(self):
        assert self._label(GROUP_UNKNOWN, plain=(False,)) == "0/1 required"
        assert self._label(GROUP_UNKNOWN, plain=(True, False)) == "1/2 required"

    def test_a_plain_file_shown_missing_is_counted_beside_a_row_nothing_could_judge(self):
        """The row nothing could judge is one of the plain files, so it is in the second number."""
        assert self._label(plain=(None, False)) == "0/2 required"

    def test_with_nothing_shown_missing_a_declined_verdict_stays_unknown(self):
        assert self._label(GROUP_UNKNOWN, plain=(True,)) == BIOS_LABEL_UNKNOWN
        assert self._label(GROUP_UNKNOWN) == BIOS_LABEL_UNKNOWN
        assert self._label(plain=(None, True)) == BIOS_LABEL_UNKNOWN


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
        emulator="pcsx2_libretro.so",
        satisfied=True,
    )

    def test_the_row_carries_the_setting_and_keeps_its_verdict(self):
        entry = build_file_entry("bios", True, "/bios/pcsx2/bios", self._FOLDER, True, "pcsx2_libretro.so")

        assert entry.missing_configured_image == {"emulator_label": "LRPS2", "file_name": "scph10000.bin"}
        assert entry.satisfied is True

    def test_the_folder_is_counted_in_place(self):
        entry = build_file_entry("bios", True, "/bios/pcsx2/bios", self._FOLDER, True, "pcsx2_libretro.so")

        assert count_required((entry,)) == (1, 1)


class TestWhatAContentSearchEstablished:
    """What an emulator that searches the BIOS folder by content found, read off the resolver's words."""

    _SEARCHER = CoreFirmwareVerdict(requirements_met=None, declaration="packaged", locating="by-name-then-content")

    @staticmethod
    def _row(*, satisfied: bool | None, checked: str | None = None, caveats: tuple[str, ...] = ()) -> BiosFileEntry:
        return dataclasses.replace(
            _withheld_folder_row(), file_name="scph5500.bin", satisfied=satisfied, checked=checked, caveats=caveats
        )

    def test_every_required_file_met_and_identified_by_its_contents_is_found(self):
        row = self._row(satisfied=True, caveats=("firmware-image-identified",))

        assert classify_content_search(self._SEARCHER, (row,)) == "found"

    def test_a_file_met_by_its_configured_name_is_no_search_finding(self):
        """Opened by name, not found by content: the answer says nothing of a search."""
        assert classify_content_search(self._SEARCHER, (self._row(satisfied=True),)) is None

    def test_a_listing_that_did_not_finish_is_incomplete(self):
        verdict = dataclasses.replace(self._SEARCHER, caveats=("firmware-scan-incomplete",))

        assert classify_content_search(verdict, ()) == "incomplete"

    def test_a_file_whose_bytes_did_not_come_back_is_unreadable(self):
        assert classify_content_search(self._SEARCHER, (self._row(satisfied=None, checked="unread"),)) == "unreadable"

    @pytest.mark.parametrize(
        ("satisfied", "checked"), [(False, None), (None, "unrecognised")], ids=["not-met", "unrecognised"]
    )
    def test_a_required_file_not_met_or_unrecognised_says_nothing_of_the_search(self, satisfied, checked):
        """A search that found nothing is the emulator's own note, never read off a verdict."""
        row = self._row(satisfied=satisfied, checked=checked)

        assert classify_content_search(self._SEARCHER, (row,)) is None

    def test_nothing_the_resolver_stated_is_no_answer(self):
        """No row, no caveat: nothing is reached by elimination."""
        assert classify_content_search(self._SEARCHER, ()) is None

    def test_an_emulator_that_opens_by_name_has_no_search_to_word(self):
        verdict = dataclasses.replace(self._SEARCHER, locating="by-name")

        row = self._row(satisfied=True, caveats=("firmware-image-identified",))

        assert classify_content_search(verdict, (row,)) is None

    def test_a_group_speaks_for_the_search_instead(self):
        group = GroupVerdict(state=GROUP_MET, covered=("pal",), missing=(), unchecked=())

        row = self._row(satisfied=True, caveats=("firmware-image-identified",))

        assert classify_content_search(self._SEARCHER, (row,), groups=(group,)) is None
