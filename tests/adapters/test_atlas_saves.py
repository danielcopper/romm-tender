"""Tests for the atlas savefile adapter — the translation into Tender's vocabulary.

What is under test is the adapter's own work: putting the question to the entry
Tender resolved rather than to a bare core, restating a placement in
Tender's save vocabulary, and turning every way the question cannot be put into
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
import pwd
import sys
from typing import Any, cast

import pytest
from _vendor.atlas import (
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_EMULATOR_CONFIG_MISSING,
    CAVEAT_EMULATOR_CONFIG_UNREADABLE,
    CAVEAT_FILE_NAMES_UNESTABLISHED,
    CAVEAT_FILENAMES_CONTENT_CONDITIONAL,
    CAVEAT_SAVE_INSIDE_CONTENT,
    CAVEAT_SAVE_INSIDE_IMAGE,
    CAVEAT_SORTED_DIR_MISSING,
    GRANULARITY_PER_GAME_FILE,
    GRANULARITY_PER_GAME_FILES,
    GRANULARITY_SHARED_CARD,
    GRANULARITY_SHARED_FILE,
    HEALTH_ISSUE_CATALOGUE_INVALID,
    HEALTH_ISSUE_COMPANION_CONFIG_MISSING,
    HEALTH_ISSUE_CONFIG_UNREADABLE,
    HEALTH_ISSUE_MARKER_INVALID,
    HEALTH_ISSUE_MARKER_MISSING,
    HEALTH_ISSUE_MARKER_UNREADABLE,
    HEALTH_ISSUE_NOT_SET_UP,
    HEALTH_ISSUE_SAVES_ROOT_MISSING,
    HOLE_CWD,
    HOLE_REGION,
    HOLE_SAVE_ID,
    PLACE_NOT_READ_CODES,
    PROVENANCE_ASSUMED,
    PROVENANCE_READ,
    ROLE_BATTERY,
    ROLE_HIGH_SCORE,
    ROLE_NOTES,
    ROLE_SETTINGS,
    ROLE_UNKNOWN,
    ROLES,
    ROOT_CONTENT_DIRECTORY,
    ROOT_NAME_SAVES,
    SAVE_ID_SPELLING_PATH_SEGMENT,
    STATE_ROOT_CONTENT_DIRECTORY,
    SavestateAbsence,
    SavestatePlacement,
    Unresolved,
    detect,
)
from _vendor.atlas.installations import EmuDeck, Health, InstallationRoot, RetroDeck, RootsAnswer
from _vendor.atlas.placement import (
    FILE_SET_DECLARED,
    FILE_SET_OBSERVED,
    FILE_SET_UNKNOWN,
    TEMPLATE_CWD,
    TEMPLATE_REGION,
    TEMPLATE_SAVE_ID,
    Caveat,
    FileGroup,
    FileSet,
    Granularity,
    SavefilePlacement,
)

from adapters.atlas_saves import AtlasSaveLocationAdapter, describe_core_probe_interpreter
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.save_answer import (
    CONFIGURATION_ROLES,
    HOLE_START_FOLDER,
    SAVE_STATE_HOLE,
    SAVE_STATE_INSIDE_CONTENT,
    SAVE_STATE_PER_GAME_FILES,
    SAVE_STATE_PLACE_NOT_READ,
    SAVE_STATE_SAVES_ROOT_MISSING,
    SAVE_STATE_SHARED,
    SAVE_STATE_UNESTABLISHED,
    UNESTABLISHED_DIRECTORY_KNOWN,
    UNESTABLISHED_NOT_ASKED,
    UNESTABLISHED_NOTHING,
    save_shape_message,
)
from domain.savestate_location import NoSavestates, SavestateLocation

_SAVES = "/saves/gba"
_CONTENT = "/roms/gba/Game Title.gba"


def _caveat(code: str) -> Caveat:
    return Caveat(code=code, message="prose that may change freely", data={})


# What fills a ``<save_id>`` hole, stated the way the resolver states it for
# Flycast's per-game cards: atlas's ``SavefilePlacement`` raises on a hole
# without it (``_check_save_id_stated``).
_SAVE_ID_STATED = Caveat(
    code=CAVEAT_FILENAMES_CONTENT_CONDITIONAL,
    message="prose that may change freely",
    data={"save_id": "the disc's product number", "save_id_spelling": SAVE_ID_SPELLING_PATH_SEGMENT},
)


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
    stated: tuple[Caveat, ...] = (),
    physical_dir: str | None = None,
    root_kind: str = "savefile_directory",
    fallback_dir: str | None = None,
    directory: str = _SAVES,
) -> SavefilePlacement:
    """One resolved savefile placement, as the resolver hands it over."""
    return SavefilePlacement(
        dir=directory,
        root_kind=cast("Any", root_kind),
        needs=needs,
        file_set=FileSet(
            state=cast("Any", state),
            files=files,
            provenance="test",
            complete=state != FILE_SET_UNKNOWN,
            groups=groups,
        ),
        sources=(),
        caveats=(*(_caveat(code) for code in caveats), *stated),
        granularity=None if granularity is None else _granularity(granularity),
        physical_dir=physical_dir,
        fallback_dir=fallback_dir,
    )


def _states_placement(
    *,
    root_kind: str = "savestate_directory",
    fallback_dir: str | None = None,
    directory: str = "/states/gba",
    needs: tuple[str, ...] = (),
) -> Any:
    """One resolved savestate placement, as the resolver hands it over."""
    return SavestatePlacement(
        dir=directory,
        root_kind=cast("Any", root_kind),
        needs=needs,
        file_set=FileSet(state=FILE_SET_OBSERVED, files=(), provenance="test", complete=False, groups=()),
        sources=(),
        caveats=(),
        fallback_dir=fallback_dir,
    )


class _Entry:
    """A catalogue entry that answers with whatever the test handed it."""

    def __init__(self, label: str, answer: Any, *, states: Any = None) -> None:
        self.label = label
        self._answer = answer
        self._states = states
        self.asked: list[str | None] = []
        self.states_asked: list[str | None] = []

    def savefile_location(self, *, content_path: str | None = None) -> Any:
        self.asked.append(content_path)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer

    def savestate_location(self, *, content_path: str | None = None) -> Any:
        self.states_asked.append(content_path)
        if isinstance(self._states, Exception):
            raise self._states
        return self._states


class _Catalogue:
    """A catalogue answer carrying the entries and the caveat codes a test named."""

    def __init__(self, entries: tuple[_Entry, ...], caveats: tuple[str, ...] = ()) -> None:
        self.entries = entries
        self.caveats = tuple(_caveat(code) for code in caveats)


class _Installation:
    """An installation that hands back one catalogue and its health, recording how it was asked."""

    def __init__(
        self,
        entries: tuple[_Entry, ...],
        *,
        raises: Exception | None = None,
        caveats: tuple[str, ...] = (),
        kind: str = "retrodeck",
        health: tuple[str | Caveat, ...] | Exception = (),
        roots: RootsAnswer | Exception | None = None,
    ) -> None:
        self.kind = kind
        self._entries = entries
        self._raises = raises
        self._caveats = caveats
        self._health = health
        self._roots = roots if roots is not None else _roots()
        self.asked: list[tuple[str, str | None]] = []

    def health(self) -> Health:
        if isinstance(self._health, Exception):
            raise self._health
        return Health(tuple(code if isinstance(code, Caveat) else _caveat(code) for code in self._health))

    def roots(self) -> RootsAnswer:
        if isinstance(self._roots, Exception):
            raise self._roots
        return self._roots

    def emulators_for(self, system: str, *, content_path: str | None = None) -> Any:
        self.asked.append((system, content_path))
        if self._raises is not None:
            raise self._raises
        return _Catalogue(self._entries, self._caveats)


_SAVES_ROOT = "/run/media/sd/saves"


def _roots(*, provenance: str = PROVENANCE_READ, findings: tuple[Caveat, ...] = ()) -> RootsAnswer:
    """A roots answer whose saves root is ``_SAVES_ROOT``, from where *provenance* says, beside *findings*."""
    return RootsAnswer(
        roots=(
            InstallationRoot(root=ROOT_NAME_SAVES, path=_SAVES_ROOT, provenance=cast("Any", provenance), setting=None),
        ),
        caveats=findings,
    )


@pytest.fixture
def traces() -> list[str]:
    return []


def _sources(
    detect: Any, traces: list[str], *, settings: dict[str, Any] | None = None, machine: Any = None
) -> EmulatorSourcesAdapter:
    """The real sources holder over a detection a test supplies."""
    return EmulatorSourcesAdapter(
        user_home="/home/deck",
        settings=settings if settings is not None else {},
        log_debug=traces.append,
        detect_installations=detect,
        machine=machine if machine is not None else object(),
    )


def _adapter(installation: Any, traces: list[str]) -> AtlasSaveLocationAdapter:
    detected = [] if installation is None else [installation]
    return AtlasSaveLocationAdapter(sources=_sources(lambda home, machine: detected, traces), log_debug=traces.append)


def _ask(answer: Any, traces: list[str], *, label: str = "mGBA", emulator: str | None = "mGBA"):
    entry = _Entry(label, answer)
    adapter = _adapter(_Installation((entry,)), traces)
    return adapter.resolve_save_answer(
        system="gba", content_path=_CONTENT, emulator_label=emulator, content_installed=True
    )


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
        # There is no separate file, so Tender would search forever for a
        # name that cannot exist — which is what it did for Amiga before this.
        answer = _ask(_placement(files=(), caveats=(code,)), traces)

        assert answer.state == SAVE_STATE_INSIDE_CONTENT
        assert answer.syncable is False

    def test_a_hole_in_the_name_refuses(self, traces):
        answer = _ask(
            _placement(
                files=("<save_id>.A1.bin",),
                needs=("save_id",),
                stated=(_SAVE_ID_STATED,),
                granularity=GRANULARITY_PER_GAME_FILES,
            ),
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
                stated=(_SAVE_ID_STATED,),
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


def _retrodeck_tree(
    tmp_path, *, marker: str | None = None, saves: bool = True, command: str, system: str, extension: str
) -> tuple[Any, Any]:
    """A RetroDECK the real resolver detects, with one system whose one command is *command*.

    *marker* replaces ``retrodeck.json``'s text (a damaged one, say); left out,
    the marker names the home and a saves root under it, which exists unless
    *saves* is ``False``. Returns the home and the saves root.
    """
    home = tmp_path / "home"
    saves_root = home / "retrodeck" / "saves"
    marker_path = home / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck" / "retrodeck.json"
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text(
        marker
        if marker is not None
        else json.dumps({"paths": {"rd_home_path": str(home / "retrodeck"), "saves_path": str(saves_root)}}),
        encoding="utf-8",
    )
    (home / "retrodeck").mkdir()
    if saves:
        saves_root.mkdir()
    systems = (
        home
        / ".local/share/flatpak/app/net.retrodeck.retrodeck/current/active/files"
        / "retrodeck/components/es-de/share/es-de/resources/systems/linux/es_systems.xml"
    )
    systems.parent.mkdir(parents=True)
    systems.write_text(
        f'<?xml version="1.0"?>\n<systemList><system><name>{system}</name><extension>.{extension}</extension>'
        f"{command}</system></systemList>\n",
        encoding="utf-8",
    )
    return home, saves_root


_MGBA = '<command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>'


def _real_answer(home: Any, traces: list[str], *, system: str, extension: str, label: str):
    """The real resolver's save answer for one game of *system* under *home*'s RetroDECK."""
    sources = EmulatorSourcesAdapter(user_home=str(home), settings={}, log_debug=traces.append)
    return AtlasSaveLocationAdapter(sources=sources, log_debug=traces.append).resolve_save_answer(
        system=system,
        content_path=str(home / "retrodeck" / "roms" / system / f"g.{extension}"),
        emulator_label=label,
        content_installed=True,
    )


@pytest.fixture
def _no_host_deploy(tmp_path, monkeypatch):
    """Never resolve ``/app`` out of the dev box's own RetroDECK deploy."""
    monkeypatch.setattr(
        "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
    )


class TestAMissingSavesRoot:
    """While the answering source's saves root, read from its settings, does not exist, no save is synced."""

    _MISSING = Caveat(code=HEALTH_ISSUE_SAVES_ROOT_MISSING, message="prose", data={"path": _SAVES_ROOT})

    def _answer(self, traces, placement: Any = None, **installation: Any):
        entry = _Entry("mGBA", _placement() if placement is None else placement)
        adapter = _adapter(_Installation((entry,), **installation), traces)
        answer = adapter.resolve_save_answer(
            system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
        )
        return answer, entry

    @pytest.mark.parametrize("kind", ["retrodeck", "emudeck"])
    def test_every_source_s_missing_saves_root_refuses_and_names_the_folder(self, traces, kind: str):
        answer, entry = self._answer(traces, kind=kind, roots=_roots(findings=(self._MISSING,)))

        assert answer.state == SAVE_STATE_SAVES_ROOT_MISSING
        assert answer.syncable is False
        assert answer.missing_saves_root == _SAVES_ROOT
        assert answer.emulator == "mGBA"
        assert answer.source == kind
        # The emulator is still asked: asking creates nothing.
        assert entry.asked == [_CONTENT]

    def test_a_saves_root_the_source_only_assumed_refuses_nothing(self, traces):
        # Why an assumed root refuses nothing: docs/architecture/save-sync-coverage.md,
        # "The seven save states".
        answer, _entry = self._answer(traces, roots=_roots(provenance=PROVENANCE_ASSUMED, findings=(self._MISSING,)))

        assert answer.state == SAVE_STATE_PER_GAME_FILES

    def test_a_finding_in_the_health_alone_is_not_read(self, traces):
        answer, _entry = self._answer(traces, health=(self._MISSING,))

        assert answer.state == SAVE_STATE_PER_GAME_FILES

    def test_roots_that_cannot_be_asked_claim_nothing_missing(self, traces):
        answer, _entry = self._answer(traces, roots=RuntimeError("roots raised"))

        assert answer.state == SAVE_STATE_PER_GAME_FILES

    def test_a_shared_card_is_named_before_a_missing_saves_root(self, traces):
        answer, _entry = self._answer(
            traces,
            _placement(files=("Mcd001.ps2",), granularity=GRANULARITY_SHARED_CARD),
            roots=_roots(findings=(self._MISSING,)),
        )

        assert answer.state == SAVE_STATE_SHARED

    def test_a_missing_saves_root_is_named_before_a_place_not_read(self, traces):
        answer, _entry = self._answer(
            traces,
            _placement(caveats=(CAVEAT_EMULATOR_CONFIG_UNREADABLE,)),
            roots=_roots(findings=(self._MISSING,)),
        )

        assert answer.state == SAVE_STATE_SAVES_ROOT_MISSING

    def test_it_is_named_where_no_entry_could_be_asked(self, traces):
        # A sealed catalogue answers nothing about the emulator; the missing
        # root is still a fact about the source.
        entry = _Entry("mGBA", _placement())
        adapter = _adapter(
            _Installation(
                (entry,),
                kind="emudeck",
                caveats=(CAVEAT_EMULATOR_CATALOGUE_SEALED,),
                roots=_roots(findings=(self._MISSING,)),
            ),
            traces,
        )

        answer = adapter.resolve_save_answer(
            system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
        )

        assert answer.state == SAVE_STATE_SAVES_ROOT_MISSING
        assert entry.asked == []

    @pytest.mark.usefixtures("_no_host_deploy")
    def test_the_real_resolver_s_finding_refuses_and_creates_no_folder(self, tmp_path, traces):
        home, saves = _retrodeck_tree(tmp_path, saves=False, command=_MGBA, system="gba", extension="gba")

        answer = _real_answer(home, traces, system="gba", extension="gba", label="mGBA")

        assert answer.state == SAVE_STATE_SAVES_ROOT_MISSING
        assert answer.missing_saves_root == str(saves)
        assert not saves.exists()

    @pytest.mark.usefixtures("_no_host_deploy")
    def test_a_damaged_retrodeck_json_does_not_refuse_for_its_assumed_saves_root(self, tmp_path, traces):
        home, saves = _retrodeck_tree(
            tmp_path, marker="{not json", saves=False, command=_MGBA, system="gba", extension="gba"
        )

        answer = _real_answer(home, traces, system="gba", extension="gba", label="mGBA")

        assert answer.state != SAVE_STATE_SAVES_ROOT_MISSING
        assert not saves.exists()

    @pytest.mark.usefixtures("_no_host_deploy")
    def test_the_real_resolver_s_emudeck_finding_refuses_and_creates_no_folder(self, tmp_path, traces):
        home = tmp_path / "home"
        emulation = tmp_path / "card" / "Emulation"
        settings = home / ".config" / "EmuDeck" / "settings.sh"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            f'romsPath="{emulation}/roms"\nbiosPath="{emulation}/bios"\nsavesPath="{emulation}/saves"\n',
            encoding="utf-8",
        )
        sources = EmulatorSourcesAdapter(user_home=str(home), settings={}, log_debug=traces.append)

        answer = AtlasSaveLocationAdapter(sources=sources, log_debug=traces.append).resolve_save_answer(
            system="gba",
            content_path=str(emulation / "roms" / "gba" / "g.gba"),
            emulator_label="mGBA",
            content_installed=True,
        )

        assert answer.state == SAVE_STATE_SAVES_ROOT_MISSING
        assert answer.missing_saves_root == str(emulation / "saves")
        assert not (emulation / "saves").exists()


_MARKERS = (HEALTH_ISSUE_MARKER_MISSING, HEALTH_ISSUE_MARKER_UNREADABLE, HEALTH_ISSUE_MARKER_INVALID)
_SETTINGS_FILE_CODES = tuple(code for code in PLACE_NOT_READ_CODES if code not in _MARKERS)


def _stated(code: str, **data: str) -> Caveat:
    return Caveat(code=code, message="prose that may change freely", data=data)


class TestAPlaceNotReadFromTheSettings:
    """A save place the resolver did not read from the emulator's own settings is never synced."""

    def _answer(self, traces, answer: Any, *, kind: str = "retrodeck", caveats: tuple[str, ...] = ()):
        entry = _Entry("Cemu (Standalone)", answer)
        adapter = _adapter(_Installation((entry,), kind=kind, caveats=caveats), traces)
        return adapter.resolve_save_answer(
            system="wiiu", content_path=_CONTENT, emulator_label="Cemu (Standalone)", content_installed=True
        )

    @pytest.mark.parametrize("kind", ["retrodeck", "emudeck", "bare_retroarch_flatpak"])
    @pytest.mark.parametrize("code", _SETTINGS_FILE_CODES)
    def test_every_settings_file_finding_refuses_for_every_source(self, traces, code: str, kind: str):
        answer = self._answer(traces, _placement(stated=(_stated(code, config="/cfg/settings.xml"),)), kind=kind)

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.syncable is False
        assert answer.sync_directory is None
        assert answer.owned_files == ()
        assert answer.finding is not None
        assert answer.finding.code == code
        assert answer.finding.data == {"config": "/cfg/settings.xml"}
        assert answer.source == kind

    def test_the_directory_the_resolver_named_is_kept(self, traces):
        # The rename on adoption still carries a game's files there.
        answer = self._answer(traces, _placement(caveats=(CAVEAT_EMULATOR_CONFIG_MISSING,)))

        assert answer.directory == _SAVES

    @pytest.mark.parametrize("kind", ["retrodeck", "emudeck"])
    @pytest.mark.parametrize("code", _MARKERS)
    def test_retrodeck_s_and_emudeck_s_marker_finding_alone_refuses_nothing(self, traces, code: str, kind: str):
        # Why: docs/architecture/save-sync-coverage.md, "The seven save states".
        answer = self._answer(traces, _placement(caveats=(code,)), kind=kind)

        assert answer.state == SAVE_STATE_PER_GAME_FILES

    @pytest.mark.parametrize("kind", ["bare_retroarch_flatpak", "bare_retroarch_native"])
    @pytest.mark.parametrize("code", _MARKERS)
    def test_a_bare_retroarch_s_marker_finding_refuses(self, traces, code: str, kind: str):
        # Why: docs/architecture/save-sync-coverage.md, "The seven save states".
        answer = self._answer(traces, _placement(caveats=(code,)), kind=kind)

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.source == kind

    def test_a_marker_finding_beside_a_settings_file_finding_names_the_settings_file(self, traces):
        answer = self._answer(traces, _placement(caveats=(HEALTH_ISSUE_MARKER_INVALID, CAVEAT_EMULATOR_CONFIG_MISSING)))

        assert answer.finding is not None
        assert answer.finding.code == CAVEAT_EMULATOR_CONFIG_MISSING

    def test_a_refusal_for_a_missing_settings_file_refuses_with_its_finding(self, traces):
        declined = Unresolved(
            code=CAVEAT_EMULATOR_CONFIG_MISSING, message="prose", data={"token": "XEMU", "config": "/x/xemu.toml"}
        )

        answer = self._answer(traces, declined)

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.finding is not None
        assert answer.finding.data == {"token": "XEMU", "config": "/x/xemu.toml"}
        assert answer.directory is None

    def test_a_refusal_for_another_reason_still_establishes_nothing(self, traces):
        answer = self._answer(traces, Unresolved(code="standalone-unsupported", message="prose", data={}))

        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.finding is None

    def test_a_catalogue_refused_for_a_source_not_set_up_names_that_finding(self, traces):
        answer = self._answer(traces, _placement(), caveats=(HEALTH_ISSUE_NOT_SET_UP,))

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.finding is not None
        assert answer.finding.code == HEALTH_ISSUE_NOT_SET_UP

    def test_no_other_finding_on_a_refused_catalogue_is_lent_to_the_entry(self, traces):
        # Whether a finding concerns the entry's place depends on the entry,
        # which a refused catalogue never named.
        answer = self._answer(
            traces,
            _placement(),
            kind="emudeck",
            caveats=(CAVEAT_EMULATOR_CATALOGUE_SEALED, HEALTH_ISSUE_COMPANION_CONFIG_MISSING),
        )

        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.unestablished == UNESTABLISHED_NOT_ASKED

    def test_a_finding_in_the_source_s_health_alone_is_not_read(self, traces):
        # Why: docs/architecture/save-sync-coverage.md, "The seven save states".
        entry = _Entry("Cemu (Standalone)", _placement())
        adapter = _adapter(
            _Installation(
                (entry,), kind="emudeck", health=(HEALTH_ISSUE_CONFIG_UNREADABLE, HEALTH_ISSUE_COMPANION_CONFIG_MISSING)
            ),
            traces,
        )

        answer = adapter.resolve_save_answer(
            system="wiiu", content_path=_CONTENT, emulator_label="Cemu (Standalone)", content_installed=True
        )

        assert answer.state == SAVE_STATE_PER_GAME_FILES

    @pytest.mark.parametrize(
        ("placement", "state"),
        [
            pytest.param(
                _placement(granularity=GRANULARITY_SHARED_CARD, caveats=(CAVEAT_EMULATOR_CONFIG_MISSING,)),
                SAVE_STATE_SHARED,
                id="shared-card",
            ),
            pytest.param(
                _placement(files=(), caveats=(CAVEAT_SAVE_INSIDE_CONTENT, CAVEAT_EMULATOR_CONFIG_MISSING)),
                SAVE_STATE_INSIDE_CONTENT,
                id="inside-content",
            ),
        ],
    )
    def test_a_shape_never_synced_is_named_before_the_finding(self, traces, placement: Any, state: str):
        assert self._answer(traces, placement).state == state

    @pytest.mark.parametrize(
        "placement",
        [
            pytest.param(
                _placement(
                    files=("<save_id>.bin",),
                    needs=("save_id",),
                    stated=(_SAVE_ID_STATED, _stated(CAVEAT_EMULATOR_CONFIG_MISSING, config="/c")),
                ),
                id="hole",
            ),
            pytest.param(
                _placement(files=(), caveats=(CAVEAT_FILE_NAMES_UNESTABLISHED, CAVEAT_EMULATOR_CONFIG_MISSING)),
                id="names-unestablished",
            ),
        ],
    )
    def test_the_finding_is_named_before_what_cannot_be_established(self, traces, placement: Any):
        assert self._answer(traces, placement).state == SAVE_STATE_PLACE_NOT_READ


@pytest.mark.usefixtures("_no_host_deploy")
class TestTheRealResolverStatesWhereAPlaceWasNotRead:
    """The resolver's own findings, over a RetroDECK built in a temporary home."""

    _CEMU = '<command label="Cemu (Standalone)">%EMULATOR_CEMU% %ROM%</command>'
    _XEMU = '<command label="xemu (Standalone)">%EMULATOR_XEMU% %ROM%</command>'
    _MELONDS = '<command label="melonDS (Standalone)">%EMULATOR_MELONDS% %ROM%</command>'

    @staticmethod
    def _melonds_settings(home: Any, text: str) -> None:
        toml = home / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "melonDS" / "melonDS.toml"
        toml.parent.mkdir(parents=True)
        toml.write_text(text, encoding="utf-8")

    def test_retroarch_s_missing_settings_file_refuses_a_core_s_save(self, tmp_path, traces):
        home, _saves = _retrodeck_tree(tmp_path, command=_MGBA, system="gba", extension="gba")

        answer = _real_answer(home, traces, system="gba", extension="gba", label="mGBA")

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.finding is not None
        assert answer.finding.code == CAVEAT_EMULATOR_CONFIG_MISSING
        assert answer.finding.data["config"].endswith(os.path.join("retroarch", "retroarch.cfg"))
        assert answer.source == "retrodeck"

    def test_a_standalone_emulator_s_missing_settings_file_refuses(self, tmp_path, traces):
        home, _saves = _retrodeck_tree(tmp_path, command=self._CEMU, system="wiiu", extension="wud")

        answer = _real_answer(home, traces, system="wiiu", extension="wud", label="Cemu (Standalone)")

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.finding is not None
        assert answer.finding.data["token"] == "CEMU"

    def test_a_missing_settings_file_the_resolver_refuses_on_refuses_too(self, tmp_path, traces):
        home, _saves = _retrodeck_tree(tmp_path, command=self._XEMU, system="xbox", extension="iso")

        answer = _real_answer(home, traces, system="xbox", extension="iso", label="xemu (Standalone)")

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.finding is not None
        assert answer.finding.data["token"] == "XEMU"

    def test_a_settings_file_that_does_not_parse_refuses_although_the_defaults_answer(self, tmp_path, traces):
        # melonDS runs on its factory settings over a file it cannot parse, and
        # the resolver answers those defaults.
        home, _saves = _retrodeck_tree(tmp_path, command=self._MELONDS, system="nds", extension="nds")
        self._melonds_settings(home, "[[[ not toml")

        answer = _real_answer(home, traces, system="nds", extension="nds", label="melonDS (Standalone)")

        assert answer.state == SAVE_STATE_PLACE_NOT_READ
        assert answer.finding is not None
        assert answer.finding.code == CAVEAT_EMULATOR_CONFIG_UNREADABLE

    def test_a_damaged_retrodeck_json_still_syncs_a_save_read_from_the_emulator_s_settings(self, tmp_path, traces):
        home, saves = _retrodeck_tree(
            tmp_path, marker="{not json", command=self._MELONDS, system="nds", extension="nds"
        )
        self._melonds_settings(home, f'[Instance0]\nSaveFilePath = "{saves / "nds"}"\n')

        answer = _real_answer(home, traces, system="nds", extension="nds", label="melonDS (Standalone)")

        assert HEALTH_ISSUE_MARKER_INVALID in answer.caveats
        assert answer.state == SAVE_STATE_PER_GAME_FILES
        assert answer.sync_directory == str(saves / "nds")
        assert answer.synced_names == ("g.sav",)


class TestTheTwoShapesAnAnswerReaches:
    """State five holds three shapes; these are the two a real answer produces.

    The third, ``not_asked``, is reached by never putting the question at all —
    :class:`TestEveryWayTheQuestionCannotBePut` owns it, because no placement
    can produce it.
    """

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


class TestAnArcadeSaveIsSyncedWhole:
    """FinalBurn Neo's shape: the battery file, the EEPROM and the high-score table, each named after the game."""

    def test_the_eeprom_and_the_high_scores_are_synced_beside_the_battery_file(self, traces):
        answer = _ask(
            _placement(
                files=("sf2.fs", "sf2.nv", "sf2.hi"),
                groups=(
                    FileGroup(dir=_SAVES, files=("sf2.fs",), granularity="per-game-file", role=ROLE_BATTERY),
                    FileGroup(dir=_SAVES, files=("sf2.nv",), granularity="per-game-file", role=ROLE_BATTERY),
                    FileGroup(dir=_SAVES, files=("sf2.hi",), granularity="per-game-file", role=ROLE_HIGH_SCORE),
                ),
            ),
            traces,
            label="FinalBurn Neo",
            emulator="FinalBurn Neo",
        )

        assert answer.synced_names == ("sf2.fs", "sf2.nv", "sf2.hi")


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
    """All of them refuse — never "nothing to sync" — and the shape says which kind.

    ``not_asked`` where nothing reached the resolver, so the emulator is not
    implicated. ``nothing_established`` where it WAS asked and could not answer.
    Rendering those as one sentence would tell a user their emulator is a
    mystery when the truth is that Tender never asked.
    """

    def _assert_refused(self, answer, shape: str) -> None:
        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.unestablished == shape
        assert answer.syncable is False

    def test_no_emulator_resolved_asks_nobody(self, traces):
        entry = _Entry("mGBA", _placement())
        installation = _Installation((entry,))
        adapter = _adapter(installation, traces)

        answer = adapter.resolve_save_answer(
            system="gba", content_path=_CONTENT, emulator_label=None, content_installed=True
        )

        self._assert_refused(answer, UNESTABLISHED_NOT_ASKED)
        assert installation.asked == []
        assert entry.asked == []

    def test_no_installation_detected(self, traces):
        adapter = _adapter(None, traces)

        self._assert_refused(
            adapter.resolve_save_answer(
                system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
            ),
            UNESTABLISHED_NOT_ASKED,
        )
        assert any("no emulator source answers (no_source)" in line for line in traces)

    def test_detection_is_reported_both_ways(self, traces):
        assert _adapter(None, traces).installation_detected() is False
        assert _adapter(_Installation(()), traces).installation_detected() is True

    def test_a_health_finding_other_than_not_set_up_is_still_detected(self, traces):
        installation = _Installation((), health=(HEALTH_ISSUE_SAVES_ROOT_MISSING,))
        assert _adapter(installation, traces).installation_detected() is True

    def test_an_installation_that_is_not_set_up_is_not_detected(self, traces):
        # It answers, and every answer refuses with its finding: nothing to ask yet.
        installation = _Installation((), health=(HEALTH_ISSUE_NOT_SET_UP,))
        assert _adapter(installation, traces).installation_detected() is False

    def test_an_installation_whose_health_cannot_be_read_is_not_detected(self, traces):
        installation = _Installation((), health=ValueError("invariant"))

        assert _adapter(installation, traces).installation_detected() is False
        assert any("resolver failed on health" in line for line in traces)

    def test_a_retrodeck_that_is_not_set_up_is_not_detected_by_the_real_resolver(self, tmp_path, monkeypatch, traces):
        # The deploy alone, with no marker, as before RetroDECK's first run.
        monkeypatch.setattr(
            "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
        )
        home = tmp_path / "home"
        (home / ".local" / "share" / "flatpak" / "app" / "net.retrodeck.retrodeck" / "current" / "active").mkdir(
            parents=True
        )
        sources = EmulatorSourcesAdapter(user_home=str(home), settings={}, log_debug=traces.append)

        assert sources.read().answering_kind == "retrodeck"
        assert AtlasSaveLocationAdapter(sources=sources, log_debug=traces.append).installation_detected() is False

    def test_the_catalogue_offers_no_entry_under_that_label(self, traces):
        answer = _ask(_placement(), traces, label="Beetle Saturn", emulator="mGBA")

        self._assert_refused(answer, UNESTABLISHED_NOT_ASKED)
        assert answer.emulator == "mGBA"

    def test_the_entry_declines(self, traces):
        declined = Unresolved(code="standalone-unsupported", message="not resolvable yet", data={})

        answer = _ask(declined, traces)

        self._assert_refused(answer, UNESTABLISHED_NOTHING)
        assert any("standalone-unsupported" in line for line in traces)

    def test_the_resolver_raises(self, traces):
        answer = _ask(AssertionError("an invariant of its own"), traces)

        self._assert_refused(answer, UNESTABLISHED_NOTHING)
        assert any("resolver failed" in line for line in traces)

    def test_the_catalogue_read_raises(self, traces):
        adapter = _adapter(_Installation((), raises=RuntimeError("packaged data")), traces)

        self._assert_refused(
            adapter.resolve_save_answer(
                system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
            ),
            UNESTABLISHED_NOT_ASKED,
        )

    @pytest.mark.parametrize("refusal", [CAVEAT_EMULATOR_CATALOGUE_SEALED, HEALTH_ISSUE_CATALOGUE_INVALID])
    def test_an_entry_beside_a_refused_catalogue_is_never_asked(self, traces, refusal):
        entry = _Entry("mGBA", _placement(), states=_states_placement())
        adapter = _adapter(_Installation((entry,), caveats=(refusal,)), traces)

        answer = adapter.resolve_save_answer(
            system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
        )
        states = adapter.resolve_savestate_location(system="gba", content_path=_CONTENT, emulator_label="mGBA")

        self._assert_refused(answer, UNESTABLISHED_NOT_ASKED)
        assert states is None
        assert entry.asked == []
        assert entry.states_asked == []
        assert any("the catalogue was refused" in line and refusal in line for line in traces)

    def test_an_entry_beside_a_caveat_that_is_no_refusal_is_asked(self, traces):
        entry = _Entry("mGBA", _placement())
        adapter = _adapter(_Installation((entry,), caveats=(HEALTH_ISSUE_SAVES_ROOT_MISSING,)), traces)

        adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True)

        assert entry.asked == [_CONTENT]

    def test_detection_itself_raises(self, traces):
        def boom(home: str, machine: Any) -> list[Any]:
            raise RuntimeError("probe failed")

        adapter = AtlasSaveLocationAdapter(sources=_sources(boom, traces), log_debug=traces.append)

        self._assert_refused(
            adapter.resolve_save_answer(
                system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
            ),
            UNESTABLISHED_NOT_ASKED,
        )


class TestHowTheQuestionIsPut:
    """The entry Tender resolved, asked about this game."""

    def test_the_entry_is_chosen_by_tenders_own_label(self, traces):
        wanted = _Entry("Kronos", _placement(files=("Game Title.bkr",)))
        others = (_Entry("Beetle Saturn", _placement(files=("wrong.srm",))), wanted)
        adapter = _adapter(_Installation(others), traces)

        answer = adapter.resolve_save_answer(
            system="saturn", content_path=_CONTENT, emulator_label="Kronos", content_installed=True
        )

        assert answer.emulator == "Kronos"
        assert answer.synced_names == ("Game Title.bkr",)

    def test_both_the_catalogue_and_the_entry_are_asked_about_this_game(self, traces):
        entry = _Entry("mGBA", _placement())
        installation = _Installation((entry,))
        adapter = _adapter(installation, traces)

        adapter.resolve_save_answer(system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True)

        assert installation.asked == [("gba", _CONTENT)]
        assert entry.asked == [_CONTENT]

    def test_nothing_is_remembered_between_calls(self, traces):
        # Every sync path asks live: the user changes a core option in the
        # emulator's own quick menu between one launch and the next sync.
        entry = _Entry("mGBA", _placement())
        adapter = _adapter(_Installation((entry,)), traces)

        for _ in range(3):
            adapter.resolve_save_answer(
                system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
            )

        assert entry.asked == [_CONTENT, _CONTENT, _CONTENT]

    def test_a_detection_that_found_nothing_is_retried(self, traces):
        detections: list[int] = []

        def detect(home: str, machine: Any) -> list[Any]:
            detections.append(1)
            return []

        adapter = AtlasSaveLocationAdapter(sources=_sources(detect, traces), log_debug=traces.append)
        for _ in range(2):
            adapter.resolve_save_answer(
                system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
            )

        assert len(detections) == 2

    def test_every_call_detects_afresh_through_one_machine(self, traces):
        # No handle outlives a call; what keeps a repeat reading cheap is the
        # one resolver machine every detection is handed.
        machines: list[Any] = []
        entry = _Entry("mGBA", _placement())
        installation = _Installation((entry,))

        def detect(home: str, machine: Any) -> list[Any]:
            machines.append(machine)
            return [installation]

        held = object()
        adapter = AtlasSaveLocationAdapter(sources=_sources(detect, traces, machine=held), log_debug=traces.append)
        for _ in range(3):
            adapter.resolve_save_answer(
                system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
            )

        assert machines == [held, held, held]


class TestWhichSourceAnswers:
    """A game's save answer comes from the source it starts through."""

    def test_retrodeck_answers_with_emudeck_first_in_the_order(self, traces):
        retrodeck_entry = _Entry("mGBA", _placement(files=("Game Title.srm",)))
        emudeck_entry = _Entry("mGBA", _placement(files=("wrong.srm",)))
        detected = [_Installation((retrodeck_entry,)), _Installation((emudeck_entry,), kind="emudeck")]
        sources = _sources(
            lambda home, machine: detected, traces, settings={"emulator_source_order": ["emudeck", "retrodeck"]}
        )
        adapter = AtlasSaveLocationAdapter(sources=sources, log_debug=traces.append)

        answer = adapter.resolve_save_answer(
            system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
        )

        assert answer.synced_names == ("Game Title.srm",)
        assert emudeck_entry.asked == []

    def test_every_source_switched_off_leaves_nothing_to_ask(self, traces):
        entry = _Entry("mGBA", _placement())
        sources = _sources(
            lambda home, machine: [_Installation((entry,))], traces, settings={"emulator_sources_off": ["retrodeck"]}
        )
        adapter = AtlasSaveLocationAdapter(sources=sources, log_debug=traces.append)

        answer = adapter.resolve_save_answer(
            system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=True
        )

        assert answer.unestablished == UNESTABLISHED_NOT_ASKED
        assert adapter.installation_detected() is False
        assert entry.asked == []


class TestTheAnswerSaysWhetherItsSubjectIsOnDisk:
    """``content_installed`` describes the question, not the answer.

    A ROM the library holds but has not installed is asked about the path it
    WOULD occupy, so every name that comes back is a prediction. Without the
    flag a page renders three save files for a game that is not there.
    """

    def test_the_callers_word_rides_onto_a_real_answer(self, traces):
        entry = _Entry("mGBA", _placement())
        adapter = _adapter(_Installation((entry,)), traces)

        answer = adapter.resolve_save_answer(
            system="gba", content_path=_CONTENT, emulator_label="mGBA", content_installed=False
        )

        assert answer.state == SAVE_STATE_PER_GAME_FILES
        assert answer.content_installed is False

    def test_it_rides_onto_a_refusal_too(self, traces):
        # An installed game whose entry declines is installed all the same, so
        # the flag must not collapse into "nothing was established".
        declined = Unresolved(code="standalone-unsupported", message="not yet", data={})
        entry = _Entry("Ryubing (Standalone)", declined)
        adapter = _adapter(_Installation((entry,)), traces)

        answer = adapter.resolve_save_answer(
            system="switch", content_path=_CONTENT, emulator_label="Ryubing (Standalone)", content_installed=True
        )

        assert answer.state == SAVE_STATE_UNESTABLISHED
        assert answer.content_installed is True


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

    def test_the_root_the_directory_hangs_off_is_carried(self, traces):
        answer = _ask(_placement(), traces)

        assert answer.root_kind == "savefile_directory"
        assert answer.in_content_directory is False
        assert answer.sync_directory == _SAVES

    def test_a_save_beside_the_content_is_read_off_the_root_kind(self, traces):
        # RetroArch's savefiles_in_content_dir: a per-game file set Tender
        # still does not sync, because the directory is the ROM's own.
        answer = _ask(_placement(root_kind=ROOT_CONTENT_DIRECTORY), traces)

        assert answer.state == SAVE_STATE_PER_GAME_FILES
        assert answer.in_content_directory is True
        assert answer.sync_directory is None

    def test_a_conditional_placement_carries_its_fallback_root(self, traces):
        answer = _ask(_placement(caveats=(CAVEAT_SORTED_DIR_MISSING,), fallback_dir="/saves"), traces)

        assert answer.directory == _SAVES
        assert answer.fallback_directory == "/saves"
        assert CAVEAT_SORTED_DIR_MISSING in answer.caveats

    def test_an_unconditional_placement_has_no_fallback(self, traces):
        assert _ask(_placement(), traces).fallback_directory is None

    def test_a_save_place_below_the_start_folder_is_not_synced_and_names_no_folder(self, traces):
        answer = _ask(_placement(directory=f"{TEMPLATE_CWD}/saves", needs=(HOLE_CWD,)), traces)

        assert answer.state == SAVE_STATE_HOLE
        assert answer.concrete_directory is None
        assert save_shape_message(answer) == (
            "Save sync is unavailable: where this emulator saves depends on the folder the game is started from. (mGBA)"
        )

    @pytest.mark.parametrize(
        ("directory", "needs"),
        [
            (f"/dolphin-emu/GC/{TEMPLATE_REGION}/Card A", (HOLE_REGION,)),
            (f"/dolphin-emu/Wii/title/{TEMPLATE_SAVE_ID}/data", (HOLE_SAVE_ID,)),
        ],
    )
    def test_a_folder_with_a_hole_in_it_names_no_folder(self, traces, directory, needs):
        stated = (_SAVE_ID_STATED,) if HOLE_SAVE_ID in needs else ()
        answer = _ask(_placement(directory=directory, needs=needs, stated=stated), traces)

        assert answer.directory == directory
        assert answer.concrete_directory is None
        assert save_shape_message(answer) == (
            "Save sync is unavailable: this emulator files saves under an identity of the game that Tender "
            "cannot read. (mGBA)"
        )

    def test_a_hole_in_the_file_name_alone_leaves_the_folder_a_folder(self, traces):
        # Flycast's cards: the save id is in the name, the folder is the saves root's.
        answer = _ask(
            _placement(files=(f"{TEMPLATE_SAVE_ID}_1.bin",), needs=(HOLE_SAVE_ID,), stated=(_SAVE_ID_STATED,)), traces
        )

        assert answer.state == SAVE_STATE_HOLE
        assert answer.concrete_directory == _SAVES


class TestTheSizeClaim:
    """Whether the emulator writes a file at a fixed size rides from its group onto the file (#2538)."""

    @staticmethod
    def _group(name: str, **size: Any) -> FileGroup:
        return FileGroup(dir=_SAVES, files=(name,), granularity=GRANULARITY_PER_GAME_FILE, role=ROLE_BATTERY, **size)

    @pytest.mark.parametrize("claim", [True, False, None])
    def test_a_groups_claim_rides_onto_its_file(self, traces, claim):
        answer = _ask(_placement(groups=(self._group("Game Title.srm", size_fixed=claim),)), traces)

        assert [component.size_fixed for component in answer.synced_files] == [claim]

    def test_a_group_that_states_nothing_is_no_claim(self, traces):
        answer = _ask(_placement(groups=(self._group("Game Title.srm"),)), traces)

        assert [component.size_fixed for component in answer.synced_files] == [None]


class TestTheResolverStatesEachFileOnce:
    """The resolver refuses a save answer that states one directory and file name twice.

    Each component takes its own group's role and size claim, with nothing
    merged across groups, because of this guarantee (#2565 D1, D2). A release
    that loosens it fails here, at the bump.
    """

    @staticmethod
    def _group(directory: str, name: str) -> FileGroup:
        return FileGroup(dir=directory, files=(name,), granularity=GRANULARITY_PER_GAME_FILE, role=ROLE_BATTERY)

    def test_a_name_repeated_in_the_answers_own_directory_is_refused(self):
        with pytest.raises(ValueError):
            _placement(
                files=("Game Title.srm", "Game Title.srm"),
                groups=(self._group(_SAVES, "Game Title.srm"), self._group(_SAVES, "Game Title.srm")),
            )

    def test_a_name_repeated_in_another_directory_is_refused(self):
        # Groups outside the answer's own directory are not held to its file
        # list, so only the one-entry rule can refuse this repeat.
        with pytest.raises(ValueError):
            _placement(
                groups=(
                    self._group(_SAVES, "Game Title.srm"),
                    self._group("/elsewhere", "Game Title.ram"),
                    self._group("/elsewhere", "Game Title.ram"),
                ),
            )

    def test_one_name_in_two_directories_is_two_files(self, traces):
        answer = _ask(
            _placement(groups=(self._group(_SAVES, "Game Title.srm"), self._group("/elsewhere", "Game Title.srm"))),
            traces,
        )

        assert [(c.directory, c.name) for c in answer.components] == [
            (_SAVES, "Game Title.srm"),
            ("/elsewhere", "Game Title.srm"),
        ]


class TestTheStandardRulesGroupsChangeNoSyncedFile:
    """RetroArch's standard rule answers one battery group per file it writes.

    Against the same files answered without groups, the groups add a role and a
    size claim and change nothing about which files a sync carries or where
    (#2538 D5).
    """

    _FILES = ("Game Title.srm", "Game Title.rtc")

    def _before(self) -> SavefilePlacement:
        return _placement(files=self._FILES, groups=())

    def _after(self) -> SavefilePlacement:
        return _placement(
            files=self._FILES,
            groups=tuple(
                FileGroup(
                    dir=_SAVES, files=(name,), granularity=GRANULARITY_PER_GAME_FILE, role=ROLE_BATTERY, size_fixed=True
                )
                for name in self._FILES
            ),
        )

    def test_the_same_files_are_synced_from_the_same_directory(self, traces):
        before, after = _ask(self._before(), traces), _ask(self._after(), traces)

        assert (after.state, after.synced_names, after.sync_directory) == (
            before.state,
            before.synced_names,
            before.sync_directory,
        )
        assert after.synced_names == self._FILES

    def test_the_groups_role_and_claim_ride_along(self, traces):
        answer = _ask(self._after(), traces)

        assert [(c.role, c.size_fixed) for c in answer.synced_files] == [(ROLE_BATTERY, True), (ROLE_BATTERY, True)]


class TestTheSavestateQuestion:
    """Where the same entry keeps the game's savestates — three outcomes, kept apart."""

    def _ask_states(self, states: Any, traces: list[str], *, emulator: str | None = "mGBA") -> Any:
        entry = _Entry("mGBA", _placement(), states=states)
        adapter = _adapter(_Installation((entry,)), traces)
        answer = adapter.resolve_savestate_location(system="gba", content_path=_CONTENT, emulator_label=emulator)
        return answer, entry

    def test_a_placement_names_its_directory_root_and_fallback(self, traces):
        answer, entry = self._ask_states(_states_placement(fallback_dir="/states"), traces)

        assert answer == SavestateLocation(
            directory="/states/gba", root_kind="savestate_directory", fallback_directory="/states"
        )
        assert entry.states_asked == [_CONTENT]

    def test_states_beside_the_content_say_so(self, traces):
        answer, _entry = self._ask_states(_states_placement(root_kind=STATE_ROOT_CONTENT_DIRECTORY), traces)

        assert answer.root_kind == ROOT_CONTENT_DIRECTORY

    def test_an_emulator_with_no_savestates_is_an_answer(self, traces):
        absence = SavestateAbsence(token="cemu", citation="no serializer ships", sources=())

        answer, _entry = self._ask_states(absence, traces)

        assert answer == NoSavestates()

    def test_a_place_with_a_hole_still_in_it_establishes_nothing(self, traces):
        # A relative savestate folder answers below the folder the game is
        # started from, which Tender does not name.
        answer, _entry = self._ask_states(
            _states_placement(directory=f"{TEMPLATE_CWD}/states", needs=(HOLE_CWD,)), traces
        )

        assert answer is None
        assert any("needs ['cwd']" in line for line in traces)

    def test_a_refusal_establishes_nothing(self, traces):
        answer, _entry = self._ask_states(Unresolved(code="standalone-unsupported", message="no", data={}), traces)

        assert answer is None
        assert any("standalone-unsupported" in line for line in traces)

    def test_a_raising_resolver_establishes_nothing(self, traces):
        answer, _entry = self._ask_states(AssertionError("an invariant of its own"), traces)

        assert answer is None
        assert any("resolver failed" in line for line in traces)

    def test_no_emulator_resolved_asks_nobody(self, traces):
        answer, entry = self._ask_states(_states_placement(), traces, emulator=None)

        assert answer is None
        assert entry.states_asked == []

    def test_no_entry_under_that_label_establishes_nothing(self, traces):
        entry = _Entry("Beetle Saturn", _placement(), states=_states_placement())
        adapter = _adapter(_Installation((entry,)), traces)

        assert adapter.resolve_savestate_location(system="gba", content_path=_CONTENT, emulator_label="mGBA") is None
        assert entry.states_asked == []


class TestTheCoreProbeLine:
    """The one place the cause of every core answering unknown is named.

    Read through the resolver's own derivation rather than a stand-in, because
    what is under test is that the line reports what a probe would actually
    start.
    """

    def test_a_running_interpreter_is_named_as_atlas_own(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "executable", "/opt/cpython/bin/python3")

        line = describe_core_probe_interpreter()

        assert line == "atlas core probe: /opt/cpython/bin/python3 (atlas's own, from the running program)"

    def test_a_program_that_is_not_an_interpreter_is_reported_as_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # atlas spawns ``sys.executable`` only where it is plainly a Python, so a
        # program under any other name leaves it nothing to run a probe under.
        monkeypatch.setattr(sys, "executable", "/opt/tender/tender")

        line = describe_core_probe_interpreter()

        assert line == "atlas core probe: no interpreter to run under — every core answers unknown"


class TestTheVocabularyIsTheResolversOwn:
    """``domain/`` restates these as plain strings because it may not import ``_vendor``.

    A rename upstream has to fail here. Without this class it would land as a
    silent reclassification: every save would become a refusal, and every test
    that asserts a refusal would still pass.
    """

    def test_the_configuration_roles(self):
        assert frozenset({ROLE_SETTINGS, ROLE_NOTES}) == CONFIGURATION_ROLES

    def test_every_other_role_the_resolver_states_is_carried(self):
        # The rule is a denial, so a role added upstream is carried rather than
        # dropped — including ``unknown``, the resolver's own word for a file no
        # declaration describes. Written over the whole vocabulary because that
        # is what an allow-list would silently narrow.
        from domain.save_answer import SaveComponent

        carried = {role for role in ROLES if role not in CONFIGURATION_ROLES}

        assert ROLE_UNKNOWN in carried
        assert all(
            SaveComponent(name="Game.dat", directory=_SAVES, role=role, granularity=None).is_progress
            for role in carried
        )

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

    def test_the_start_folder_hole_and_how_the_resolver_spells_a_hole_in_a_path(self):
        assert HOLE_START_FOLDER == HOLE_CWD
        # SaveAnswer.concrete_directory finds a hole in a path as "<hole>".
        assert (
            f"<{HOLE_CWD}>",
            f"<{HOLE_REGION}>",
            f"<{HOLE_SAVE_ID}>",
        ) == (TEMPLATE_CWD, TEMPLATE_REGION, TEMPLATE_SAVE_ID)

    def test_the_missing_sorted_directory_caveat(self):
        from domain.save_answer import SORTED_DIR_MISSING

        assert SORTED_DIR_MISSING == CAVEAT_SORTED_DIR_MISSING

    def test_the_content_directory_root_is_one_word_on_both_questions(self):
        from domain.save_answer import ROOT_CONTENT_DIRECTORY as OURS

        assert OURS == ROOT_CONTENT_DIRECTORY == STATE_ROOT_CONTENT_DIRECTORY

    def test_the_codes_for_a_place_not_read(self):
        from domain.save_answer import PLACE_NOT_READ_CODES as OURS

        assert frozenset(PLACE_NOT_READ_CODES) == OURS

    def test_the_marker_codes(self):
        from domain.save_answer import _MARKER_CODES

        assert frozenset(_MARKERS) == _MARKER_CODES

    def test_the_missing_saves_root_code(self):
        from domain.save_answer import SAVES_ROOT_MISSING

        assert SAVES_ROOT_MISSING == HEALTH_ISSUE_SAVES_ROOT_MISSING

    def test_the_kinds_whose_marker_names_no_save_place(self):
        from domain.emulator_sources import EMUDECK, RETRODECK

        assert (RetroDeck.kind, EmuDeck.kind) == (RETRODECK, EMUDECK)


# --- The real resolver, over whatever this machine has ------------------------

# What each system answers TODAY, so an emu-atlas bump that changes one is loud
# instead of silently changing what Tender syncs. The key carries the
# CONTENT EXTENSION the answer was measured with, because the answer turns on it
# — the same system answers differently for a disc image and for a raw dump, and
# a pin that did not say which one it asked with would be pinning nothing. Each
# entry is ``(state, synced names)``.
#
# The rows that differ by extension are the point of the exercise, not noise:
# PUAE puts an Amiga .adf's save inside the disk image, places a .lha's under a
# WHDLoad.prefs this machine does not have (a place not read from its settings),
# and establishes nothing for an .hdf; Genesis Plus GX keeps a Sega CD .chd on a
# shared BRAM card and a .bin in a per-game .srm.
_PINNED: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    ("amiga", ".adf"): (SAVE_STATE_INSIDE_CONTENT, ()),
    ("amiga", ".lha"): (SAVE_STATE_PLACE_NOT_READ, ()),
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

# The shape inside "not established" for each row above in that state: an .hdf
# states nothing at all.
_PINNED_SHAPES: dict[tuple[str, str], str] = {
    ("amiga", ".hdf"): UNESTABLISHED_NOTHING,
    ("amigacd32", ".bin"): UNESTABLISHED_NOTHING,
}


@pytest.fixture(scope="module")
def machine() -> Any:
    # The suite's one named reader of the real home — see tests/conftest.py.
    installations = detect(pwd.getpwuid(os.getuid()).pw_dir)
    if not installations:
        pytest.skip("no emulator installation on this machine — the real-resolver tier cannot run")
    return installations[0]


class TestTheRealMachineAnswers:
    """The pinned per-system answers.

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
        adapter = AtlasSaveLocationAdapter(
            sources=_sources(lambda home, held: [machine], traces), log_debug=traces.append
        )
        return adapter.resolve_save_answer(
            system=system,
            content_path=content_path,
            emulator_label=default.label if default is not None else None,
            content_installed=True,
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

    def test_one_system_answers_differently_for_three_content_shapes(self, machine, traces):
        """The whole reason a pin names its extension: Amiga is three answers.

        Three different states — inside the content, a place not read from the
        settings, nothing established. A table keyed by system could state one
        of the three.
        """
        answers = {ext: self._answer(machine, "amiga", ext, traces) for ext in (".adf", ".lha", ".hdf")}

        assert answers[".adf"].state == SAVE_STATE_INSIDE_CONTENT
        assert answers[".lha"].state == SAVE_STATE_PLACE_NOT_READ
        assert answers[".hdf"].state == SAVE_STATE_UNESTABLISHED
        assert len({(a.state, a.unestablished) for a in answers.values()}) == 3

    def test_a_configuration_file_the_machine_states_is_still_excluded(self, machine, traces):
        # The Saturn ``.smpc`` on a real reading, not a constructed one.
        answer = self._answer(machine, "saturn", ".chd", traces)

        names = [component.name for component in answer.components]
        if "Game Title.smpc" not in names:
            pytest.skip("this machine's Saturn default does not state a settings file")
        assert "Game Title.smpc" not in answer.synced_names
