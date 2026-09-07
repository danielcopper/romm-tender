"""Tests for the atlas savefile adapter — the translation into plugin vocabulary.

What is under test is the adapter's own work: putting the question to the entry
the plugin resolved rather than to a bare core, restating a placement in the
plugin's save vocabulary, and turning every way the question cannot be put into
the one honest refusal. The resolver's own decisions are upstream's and are not
re-tested here.

Answers are built from real atlas value objects rather than mocks, so the field
names and shapes under test are the resolver's own and a rename upstream fails
here rather than passing against a stand-in. The vocabulary
:mod:`domain.save_answer` restates as plain strings — roles, granularities,
file-set states, caveat codes — is asserted against the resolver's exported
constants in :class:`TestTheVocabularyIsTheResolversOwn`, which is the whole
reason the domain may hold those literals at all.

:class:`TestTheRealMachineAnswers` is the exception: it drives the REAL resolver
over whatever RetroDECK this machine has, because no fabricated tree can reach a
rule-card answer — atlas loads the core's own ``.so`` to ask it, so a seeded
placeholder answers ``core-unqueryable``. Those cases skip where no installation
is detected, which means CI does not run them and a developer's pre-push gate
does.
"""

from __future__ import annotations

import json
import os
from typing import Any, cast

import pytest
from _vendor.atlas import (
    CAVEAT_FILE_NAMES_UNESTABLISHED,
    CAVEAT_SAVE_INSIDE_CONTENT,
    CAVEAT_SAVE_INSIDE_IMAGE,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_PER_GAME_FILES,
    GRANULARITY_SHARED_CARD,
    GRANULARITY_SHARED_FILE,
    ROLE_BATTERY,
    ROLE_NOTES,
    ROLE_SETTINGS,
    Unresolved,
)
from _vendor.atlas.placement import (
    FILE_SET_DECLARED,
    FILE_SET_UNKNOWN,
    Caveat,
    FileGroup,
    FileSet,
    Granularity,
    SavefilePlacement,
)

from adapters.atlas_catalogue import first_detected_installation
from adapters.atlas_saves import AtlasSaveLocationAdapter
from domain.save_answer import (
    CONFIGURATION_ROLES,
    SAVE_STATE_HOLE,
    SAVE_STATE_INSIDE_CONTENT,
    SAVE_STATE_PER_GAME_FILES,
    SAVE_STATE_SHARED,
    SAVE_STATE_UNESTABLISHED,
    UNESTABLISHED_DIRECTORY_KNOWN,
    UNESTABLISHED_NOTHING,
)

_SAVES = "/saves/gba"
_CONTENT = "/roms/gba/Game Title.gba"


def _caveat(code: str) -> Caveat:
    return Caveat(code=code, message="prose that may change freely", data={})


def _granularity(value: str) -> Granularity:
    return Granularity(value=value, mode=None, readings=(), alternatives=(), provenance="test")


def _placement(
    *,
    files: tuple[str, ...] = ("Game Title.srm",),
    groups: tuple[FileGroup, ...] = (),
    state: str = FILE_SET_DECLARED,
    needs: tuple[str, ...] = (),
    granularity: str | None = GRANULARITY_PER_GAME_FILE,
    caveats: tuple[str, ...] = (),
    physical_dir: str | None = None,
) -> SavefilePlacement:
    """One resolved savefile placement, as the resolver hands it over."""
    return SavefilePlacement(
        dir=_SAVES,
        root_kind="savefile_directory",
        needs=needs,
        file_set=FileSet(
            state=cast("Any", state),
            files=files,
            provenance="test",
            complete=state != FILE_SET_UNKNOWN,
            groups=groups,
        ),
        sources=(),
        caveats=tuple(_caveat(code) for code in caveats),
        granularity=None if granularity is None else _granularity(granularity),
        physical_dir=physical_dir,
    )


class _Entry:
    """A catalogue entry that answers with whatever the test handed it."""

    def __init__(self, label: str, answer: Any) -> None:
        self.label = label
        self._answer = answer
        self.asked: list[str | None] = []

    def savefile_location(self, *, content_path: str | None = None) -> Any:
        self.asked.append(content_path)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class _Catalogue:
    """A catalogue answer carrying the entries a test named."""

    def __init__(self, entries: tuple[_Entry, ...]) -> None:
        self.entries = entries


class _Installation:
    """An installation that hands back one catalogue, recording how it was asked."""

    def __init__(self, entries: tuple[_Entry, ...], *, raises: Exception | None = None) -> None:
        self._entries = entries
        self._raises = raises
        self.asked: list[tuple[str, str | None]] = []

    def emulators_for(self, system: str, *, content_path: str | None = None) -> Any:
        self.asked.append((system, content_path))
        if self._raises is not None:
            raise self._raises
        return _Catalogue(self._entries)


@pytest.fixture
def traces() -> list[str]:
    return []


def _adapter(installation: Any, traces: list[str]) -> AtlasSaveLocationAdapter:
    return AtlasSaveLocationAdapter(choose_installation=lambda: installation, log_debug=traces.append)


def _ask(answer: Any, traces: list[str], *, label: str = "mGBA", emulator: str | None = "mGBA"):
    entry = _Entry(label, answer)
    adapter = _adapter(_Installation((entry,)), traces)
    return adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label=emulator)


class TestTheFiveStates:
    """Every ROM lands in exactly one state, and only the first may be synced."""

    def test_named_files_with_no_hole_are_the_one_syncable_state(self, traces):
        answer = _ask(_placement(files=("Game Title.srm", "Game Title.rtc")), traces)

        assert answer.state == SAVE_STATE_PER_GAME_FILES
        assert answer.unestablished is None
        assert answer.syncable is True
        assert answer.synced_names == ("Game Title.srm", "Game Title.rtc")

    def test_a_shared_card_refuses(self, traces):
        # One file many games write: a per-game sync would carry another game's
        # progress onto this ROM's record and back out to every other device.
        answer = _ask(
            _placement(files=("Mcd001.ps2",), granularity=GRANULARITY_SHARED_CARD),
            traces,
            label="PCSX2",
            emulator="PCSX2",
        )

        assert answer.state == SAVE_STATE_SHARED
        assert answer.syncable is False
        assert answer.synced_names == ()

    def test_a_shared_file_refuses_for_the_same_reason(self, traces):
        answer = _ask(_placement(files=("scd_E.brm",), granularity=GRANULARITY_SHARED_FILE), traces)

        assert answer.state == SAVE_STATE_SHARED
        assert answer.syncable is False

    @pytest.mark.parametrize("code", [CAVEAT_SAVE_INSIDE_CONTENT, CAVEAT_SAVE_INSIDE_IMAGE])
    def test_a_save_inside_the_game_file_refuses(self, traces, code: str):
        # There is no separate file, so the plugin would search forever for a
        # name that cannot exist — which is what it did for Amiga before this.
        answer = _ask(_placement(files=(), caveats=(code,)), traces)

        assert answer.state == SAVE_STATE_INSIDE_CONTENT
        assert answer.syncable is False

    def test_a_hole_in_the_name_refuses(self, traces):
        answer = _ask(
            _placement(files=("<save_id>.A1.bin",), needs=("save_id",), granularity=GRANULARITY_PER_GAME_FILES),
            traces,
        )

        assert answer.state == SAVE_STATE_HOLE
        assert answer.needs == ("save_id",)
        assert answer.syncable is False

    def test_a_hole_outranks_a_shared_group_beside_it(self, traces):
        # Flycast states four per-game cards AND a shared battery. The answer
        # cannot be completed either way, and naming the hole says more.
        answer = _ask(
            _placement(
                files=("<save_id>.A1.bin",),
                needs=("save_id",),
                granularity=GRANULARITY_PER_GAME_FILES,
                groups=(
                    FileGroup(
                        dir=_SAVES,
                        files=("<save_id>.A1.bin",),
                        granularity="per-game-files",
                        role="memory-card",
                    ),
                    FileGroup(
                        dir="/saves/dreamcast", files=("dc_nvmem.bin",), granularity="shared-card", role=ROLE_BATTERY
                    ),
                ),
            ),
            traces,
        )

        assert answer.state == SAVE_STATE_HOLE


class TestTheTwoShapesOfNotEstablished:
    """State five says two different things and must keep saying both."""

    def test_a_file_set_the_resolver_refuses_to_state_is_nothing_established(self, traces):
        answer = _ask(_placement(files=(), state=FILE_SET_UNKNOWN, granularity=None), traces)

        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.unestablished == UNESTABLISHED_NOTHING

    def test_an_unaudited_core_that_names_nothing_is_nothing_established(self, traces):
        answer = _ask(_placement(files=(), granularity=None, caveats=("core-unaudited",)), traces)

        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.unestablished == UNESTABLISHED_NOTHING

    def test_a_known_directory_with_unnamed_contents_is_the_other_shape(self, traces):
        answer = _ask(
            _placement(files=(), caveats=(CAVEAT_FILE_NAMES_UNESTABLISHED,), granularity=GRANULARITY_PER_GAME_FILES),
            traces,
        )

        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.unestablished == UNESTABLISHED_DIRECTORY_KNOWN
        assert answer.directory == _SAVES

    def test_a_group_that_states_a_directory_and_no_names_is_the_same_shape(self, traces):
        # ``files=None`` means "there is save data here and the names could not
        # be established" — never "this directory is empty".
        answer = _ask(
            _placement(
                files=(),
                groups=(FileGroup(dir=_SAVES, files=None, granularity="per-game-files", role=ROLE_BATTERY),),
            ),
            traces,
        )

        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.unestablished == UNESTABLISHED_DIRECTORY_KNOWN

    def test_the_two_shapes_are_not_the_same_value(self):
        assert UNESTABLISHED_NOTHING != UNESTABLISHED_DIRECTORY_KNOWN


class TestAConfigurationFileIsOfferedAndNeverSynced:
    """The Saturn ``.smpc`` case: visible on the answer, absent from the sync."""

    def _saturn(self, traces):
        return _ask(
            _placement(
                files=("Game Title.bkr", "Game Title.bcr", "Game Title.smpc"),
                groups=(
                    FileGroup(dir=_SAVES, files=("Game Title.bkr",), granularity="per-game-file", role=ROLE_BATTERY),
                    FileGroup(dir=_SAVES, files=("Game Title.bcr",), granularity="per-game-file", role=ROLE_BATTERY),
                    FileGroup(dir=_SAVES, files=("Game Title.smpc",), granularity="per-game-file", role=ROLE_SETTINGS),
                ),
            ),
            traces,
            label="Beetle Saturn",
            emulator="Beetle Saturn",
        )

    def test_the_configuration_file_is_on_the_answer(self, traces):
        names = [component.name for component in self._saturn(traces).components]

        assert names == ["Game Title.bkr", "Game Title.bcr", "Game Title.smpc"]

    def test_the_configuration_file_is_not_in_the_synced_set(self, traces):
        answer = self._saturn(traces)

        assert answer.synced_names == ("Game Title.bkr", "Game Title.bcr")

    def test_a_directory_move_still_carries_the_configuration_file(self, traces):
        # Splitting one save across two directories would break the game as
        # surely as leaving the battery file behind.
        answer = self._saturn(traces)

        assert [component.name for component in answer.owned_files] == [
            "Game Title.bkr",
            "Game Title.bcr",
            "Game Title.smpc",
        ]

    @pytest.mark.parametrize("role", [ROLE_SETTINGS, ROLE_NOTES])
    def test_both_configuration_roles_are_excluded(self, traces, role: str):
        answer = _ask(
            _placement(
                files=("Game Title.cfg",),
                groups=(FileGroup(dir=_SAVES, files=("Game Title.cfg",), granularity="per-game-file", role=role),),
            ),
            traces,
        )

        assert answer.synced_names == ()


class TestEveryWayTheQuestionCannotBePut:
    """All of them are the same honest refusal — never "nothing to sync"."""

    def _assert_refused(self, answer) -> None:
        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.unestablished == UNESTABLISHED_NOTHING
        assert answer.syncable is False

    def test_no_emulator_resolved_asks_nobody(self, traces):
        entry = _Entry("mGBA", _placement())
        installation = _Installation((entry,))
        adapter = _adapter(installation, traces)

        answer = adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label=None)

        self._assert_refused(answer)
        assert installation.asked == []
        assert entry.asked == []

    def test_no_installation_detected(self, traces):
        adapter = _adapter(None, traces)

        self._assert_refused(adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA"))
        assert any("no emulator installation detected" in line for line in traces)

    def test_the_catalogue_offers_no_entry_under_that_label(self, traces):
        answer = _ask(_placement(), traces, label="Beetle Saturn", emulator="mGBA")

        self._assert_refused(answer)
        assert answer.emulator == "mGBA"

    def test_the_entry_declines(self, traces):
        declined = Unresolved(code="standalone-unsupported", message="not resolvable yet", data={})

        answer = _ask(declined, traces)

        self._assert_refused(answer)
        assert any("standalone-unsupported" in line for line in traces)

    def test_the_resolver_raises(self, traces):
        answer = _ask(AssertionError("an invariant of its own"), traces)

        self._assert_refused(answer)
        assert any("resolver failed" in line for line in traces)

    def test_the_catalogue_read_raises(self, traces):
        adapter = _adapter(_Installation((), raises=RuntimeError("packaged data")), traces)

        self._assert_refused(adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA"))

    def test_detection_itself_raises(self, traces):
        def boom() -> Any:
            raise RuntimeError("probe failed")

        adapter = AtlasSaveLocationAdapter(choose_installation=boom, log_debug=traces.append)

        self._assert_refused(adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA"))


class TestHowTheQuestionIsPut:
    """The entry the plugin resolved, asked about this game."""

    def test_the_entry_is_chosen_by_the_plugins_own_label(self, traces):
        wanted = _Entry("Kronos", _placement(files=("Game Title.bkr",)))
        others = (_Entry("Beetle Saturn", _placement(files=("wrong.srm",))), wanted)
        adapter = _adapter(_Installation(others), traces)

        answer = adapter.resolve_save_answer(system="saturn", content_path=_CONTENT, emulator_label="Kronos")

        assert answer.emulator == "Kronos"
        assert answer.synced_names == ("Game Title.bkr",)

    def test_both_the_catalogue_and_the_entry_are_asked_about_this_game(self, traces):
        entry = _Entry("mGBA", _placement())
        installation = _Installation((entry,))
        adapter = _adapter(installation, traces)

        adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA")

        assert installation.asked == [("gba", _CONTENT)]
        assert entry.asked == [_CONTENT]

    def test_nothing_is_remembered_between_calls(self, traces):
        # Every sync path asks live: the user changes a core option in the
        # emulator's own quick menu between one launch and the next sync.
        entry = _Entry("mGBA", _placement())
        adapter = _adapter(_Installation((entry,)), traces)

        for _ in range(3):
            adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA")

        assert entry.asked == [_CONTENT, _CONTENT, _CONTENT]

    def test_a_detection_that_found_nothing_is_retried(self, traces):
        chooses: list[int] = []

        def choose() -> Any:
            chooses.append(1)
            return None

        adapter = AtlasSaveLocationAdapter(choose_installation=choose, log_debug=traces.append)
        for _ in range(2):
            adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA")

        assert len(chooses) == 2

    def test_the_installation_handle_is_memoised(self, traces):
        # Worth 170 ms against 490 ms per reading on the reference machine, and
        # nothing this plugin writes can invalidate it.
        chooses: list[int] = []
        entry = _Entry("mGBA", _placement())
        installation = _Installation((entry,))

        def choose() -> Any:
            chooses.append(1)
            return installation

        adapter = AtlasSaveLocationAdapter(choose_installation=choose, log_debug=traces.append)
        for _ in range(3):
            adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA")

        assert len(chooses) == 1


class TestWhatTheAnswerCarries:
    """The fields a later rendering needs, carried verbatim."""

    def test_the_directory_the_emulator_opens_and_the_one_it_resolves_to(self, traces):
        answer = _ask(_placement(physical_dir="/backing/gba"), traces)

        assert answer.directory == _SAVES
        assert answer.backing_directory == "/backing/gba"

    def test_caveat_codes_are_carried_and_messages_are_not_parsed(self, traces):
        answer = _ask(_placement(caveats=("unverified-version", "sorted-dir-missing")), traces)

        assert answer.caveats == ("unverified-version", "sorted-dir-missing")

    def test_a_component_keeps_its_own_directory_where_the_groups_span_roots(self, traces):
        answer = _ask(
            _placement(
                files=("Game Title.srm",),
                groups=(
                    FileGroup(dir="/elsewhere", files=("Game Title.srm",), granularity="per-game-file", role="battery"),
                ),
            ),
            traces,
        )

        assert answer.components[0].directory == "/elsewhere"

    def test_a_placement_with_no_groups_still_names_its_files(self, traces):
        answer = _ask(_placement(files=("Game Title.srm", "Game Title.rtc"), groups=()), traces)

        assert [component.name for component in answer.components] == ["Game Title.srm", "Game Title.rtc"]
        assert all(component.role is None for component in answer.components)


class TestTheVocabularyIsTheResolversOwn:
    """``domain/`` restates these as plain strings because it may not import ``_vendor``.

    A rename upstream has to fail here. Without this class it would land as a
    silent reclassification: every save would become a refusal, and every test
    that asserts a refusal would still pass.
    """

    def test_the_configuration_roles(self):
        assert frozenset({ROLE_SETTINGS, ROLE_NOTES}) == CONFIGURATION_ROLES

    def test_the_shared_granularities(self):
        from domain.save_answer import _SHARED_GRANULARITIES

        assert frozenset({GRANULARITY_SHARED_CARD, GRANULARITY_SHARED_FILE}) == _SHARED_GRANULARITIES

    def test_the_inside_content_caveats(self):
        from domain.save_answer import _INSIDE_CONTENT_CAVEATS

        assert frozenset({CAVEAT_SAVE_INSIDE_CONTENT, CAVEAT_SAVE_INSIDE_IMAGE}) == _INSIDE_CONTENT_CAVEATS

    def test_the_unnamed_contents_caveat(self):
        from domain.save_answer import _FILE_NAMES_UNESTABLISHED

        assert _FILE_NAMES_UNESTABLISHED == CAVEAT_FILE_NAMES_UNESTABLISHED

    def test_the_refused_file_set_state(self):
        from domain.save_answer import _FILE_SET_UNKNOWN

        assert _FILE_SET_UNKNOWN == FILE_SET_UNKNOWN


# --- The real resolver, over whatever this machine has ------------------------

_PLATFORM_MAP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "defaults", "config.json")

# What each system answers TODAY, so an emu-atlas bump that changes one is loud
# instead of silently changing what this plugin syncs. The key carries the
# CONTENT EXTENSION the answer was measured with, because the answer turns on it
# — the same system answers differently for a disc image and for a raw dump, and
# a pin that did not say which one it asked with would be pinning nothing. Each
# entry is ``(state, synced names)``.
#
# The rows that differ by extension are the point of the exercise, not noise:
# PUAE puts an Amiga .adf's save inside the disk image, states a directory it
# cannot name the contents of for a .lha, and establishes nothing for an .hdf;
# Genesis Plus GX keeps a Sega CD .chd on a shared BRAM card and a .bin in a
# per-game .srm.
_PINNED: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("amiga", ".adf"): (SAVE_STATE_INSIDE_CONTENT, ()),
    ("amiga", ".lha"): (SAVE_STATE_UNESTABLISHED, ()),
    ("amiga", ".hdf"): (SAVE_STATE_UNESTABLISHED, ()),
    ("amigacd32", ".chd"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.nvr",)),
    ("amigacd32", ".bin"): (SAVE_STATE_UNESTABLISHED, ()),
    ("segacd", ".chd"): (SAVE_STATE_SHARED, ()),
    ("segacd", ".bin"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.srm",)),
    ("megacd", ".chd"): (SAVE_STATE_SHARED, ()),
    ("megacd", ".bin"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.srm",)),
    ("nds", ".nds"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.dsv",)),
    ("saturn", ".chd"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.bkr", "Game Title.bcr")),
    ("ngp", ".ngp"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.flash",)),
    ("ngpc", ".ngc"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.flash",)),
    ("pokemini", ".min"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.eep",)),
    ("3do", ".chd"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.0.srm",)),
    ("gba", ".gba"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.srm",)),
    ("gb", ".gb"): (SAVE_STATE_PER_GAME_FILES, ("Game Title.srm", "Game Title.rtc")),
    ("ps2", ".chd"): (SAVE_STATE_SHARED, ()),
}

# The two shapes of "not established" the rows above reach, kept apart here too:
# a .lha states a directory whose names PUAE does not list, an .hdf states
# nothing at all.
_PINNED_SHAPES: dict[tuple[str, str], str] = {
    ("amiga", ".lha"): UNESTABLISHED_DIRECTORY_KNOWN,
    ("amiga", ".hdf"): UNESTABLISHED_NOTHING,
    ("amigacd32", ".bin"): UNESTABLISHED_NOTHING,
}

# No ES-DE system declares either of these, so the catalogue offers no entry to
# ask and the platform map produces a system nothing can answer for. Tracked
# separately; listed here so the coverage assertion below stays exact.
_UNDECLARED_SYSTEMS = frozenset({"atarijaguarcd", "xbox360"})


@pytest.fixture(scope="module")
def machine() -> Any:
    installation = first_detected_installation(os.path.expanduser("~"))
    if installation is None:
        pytest.skip("no emulator installation on this machine — the real-resolver tier cannot run")
    return installation


class TestTheRealMachineAnswers:
    """The pinned per-system answers, and the platform map's coverage.

    Skipped where no RetroDECK is installed, so CI does not run this and a
    developer's pre-push gate does. No fabricated tree can stand in: atlas loads
    a core's own ``.so`` to ask it what it writes, and a seeded placeholder
    answers ``core-unqueryable``.
    """

    def _answer(self, machine: Any, system: str, extension: str, traces: list[str]):
        from adapters.atlas_catalogue import _declared_order
        from domain.emulator_commands import classify_command, select_default_option

        content_path = f"/tmp/Games/{system}/Game Title{extension}"
        entries = _declared_order(machine.emulators_for(system, content_path=content_path).entries)
        default = select_default_option([classify_command(entry.label, entry.command) for entry in entries])
        adapter = AtlasSaveLocationAdapter(choose_installation=lambda: machine, log_debug=traces.append)
        return adapter.resolve_save_answer(
            system=system,
            content_path=content_path,
            emulator_label=default.label if default is not None else None,
        )

    @pytest.mark.parametrize(("system", "extension"), sorted(_PINNED), ids=lambda value: value.lstrip("."))
    def test_each_system_still_answers_what_it_answered(self, machine, traces, system: str, extension: str):
        state, names = _PINNED[(system, extension)]

        answer = self._answer(machine, system, extension, traces)

        assert (answer.state, answer.synced_names) == (state, names)

    @pytest.mark.parametrize(("system", "extension"), sorted(_PINNED_SHAPES), ids=lambda value: value.lstrip("."))
    def test_the_two_shapes_of_not_established_are_pinned_per_extension(
        self, machine, traces, system: str, extension: str
    ):
        answer = self._answer(machine, system, extension, traces)

        assert answer.unestablished == _PINNED_SHAPES[(system, extension)]

    def test_one_system_answers_three_different_states_by_extension(self, machine, traces):
        """The whole reason a pin names its extension: Amiga is three answers.

        A table keyed by system could state only one of them, which is how the
        retired one came to search forever for a ``.nvr`` no core writes.
        """
        states = {ext: self._answer(machine, "amiga", ext, traces).state for ext in (".adf", ".lha", ".hdf")}

        assert states[".adf"] == SAVE_STATE_INSIDE_CONTENT
        assert states[".lha"] == SAVE_STATE_UNESTABLISHED
        assert len({states[".adf"], states[".lha"]}) == 2

    def test_the_platform_map_produces_only_systems_the_resolver_knows(self, machine, traces):
        with open(_PLATFORM_MAP, encoding="utf-8") as handle:
            systems = sorted(set(json.load(handle)["platform_map"].values()))

        offers_nothing = {system for system in systems if not machine.emulators_for(system).entries}

        assert offers_nothing == _UNDECLARED_SYSTEMS

    def test_a_configuration_file_the_machine_states_is_still_excluded(self, machine, traces):
        # The Saturn ``.smpc`` on a real reading, not a constructed one.
        answer = self._answer(machine, "saturn", ".chd", traces)

        names = [component.name for component in answer.components]
        if "Game Title.smpc" not in names:
            pytest.skip("this machine's Saturn default does not state a settings file")
        assert "Game Title.smpc" not in answer.synced_names
