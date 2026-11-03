"""Whether one game's file starts, read in the catalogue's reading module (#2195 D2, D3, D11, D13).

The first class drives the REAL resolver over a fabricated RetroDECK deploy
under ``tmp_path``: what is under test is that each verdict the resolver states
arrives as its own state, with what that state's sentence names. The second
builds answers from real atlas value objects for the shapes a seeded tree does
not reach cheaply.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

import pytest
from _atlas_machines import fixture_machine, vectors_asking
from _vendor import atlas
from _vendor.atlas import (
    CAVEAT_ARCHIVE_CONTENTS_UNREAD,
    CAVEAT_ARRANGEMENT_VERSION_DRIFTED,
    CAVEAT_BOOT_CATEGORY_UNESTABLISHED,
    CAVEAT_BOOT_DISC_DIR_CONFIGURED,
    CAVEAT_BOOT_DISC_DIR_UNRESOLVED,
    CAVEAT_BOOT_DISC_KEY_MISSING,
    CAVEAT_BOOT_DISC_ROOT_MISSING,
    CAVEAT_BOOT_DRIVE_UNRESOLVED,
    CAVEAT_BOOT_FILE_UNBOOTABLE,
    CAVEAT_BOOT_INSIDE_INTERNAL_DRIVE,
    CAVEAT_BOOT_ISO_ENCRYPTION_UNREAD,
    CAVEAT_BOOT_ISO_KEY_UNTESTED,
    CAVEAT_BOOT_ISO_REGIONS_INVALID,
    CAVEAT_BOOT_KEY_FOLDER_UNRESOLVED,
    CAVEAT_BOOT_NOTHING_FOUND,
    CAVEAT_BOOT_PATH_MISSING,
    CAVEAT_BOOT_PATH_UNESTABLISHED,
    CAVEAT_BOOT_REGISTRY_UNREAD,
    CAVEAT_BOOT_SAVESTATE_UNREAD,
    CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
    CAVEAT_ENTRY_FORMAT_BUILD_DEPENDENT,
    CAVEAT_ENTRY_FORMAT_CONTENT_UNREAD,
    CAVEAT_ENTRY_FORMAT_UNESTABLISHED,
    CAVEAT_ENTRY_TAKES_NO_DIRECTORY,
    CAVEAT_OPENS_BOUND_EXCEEDED,
    CAVEAT_OPENS_CONTENT_UNREAD,
    CAVEAT_OPENS_ENTRY_IGNORED,
    CAVEAT_OPENS_FILE_MISSING,
    CAVEAT_OPENS_FILE_UNREAD,
    CAVEAT_OPENS_FORMAT_REFUSED,
    CAVEAT_OPENS_FORMAT_UNREAD,
    CAVEAT_OPENS_PATH_TOO_LONG,
    CAVEAT_OPENS_PLAYLIST_REFUSED,
    CAVEAT_OPENS_SETTING_UNREAD,
    CAVEAT_OPENS_SHEET_REFUSED,
    CAVEAT_OPENS_TRACK_REFUSED,
    CAVEAT_ROM_PATH_UNDECLARED,
    CAVEAT_SYSTEM_UNKNOWN,
    EFFECT_GOES_ON,
    EFFECT_STOPS,
    EFFECT_UNESTABLISHED,
    OPENS_EFFECTS,
    VERDICT_ENTRY_NOT_ACCEPTED,
    VERDICT_LAUNCHABLE,
    VERDICT_NEEDS_INSTALLATION,
    VERDICT_UNKNOWN,
)
from _vendor.atlas.installations import Installer, LaunchabilityAnswer
from _vendor.atlas.opens_codes import OPENS_FAILURE_CODES
from _vendor.atlas.placement import Caveat

from adapters.atlas_catalogue import AtlasCatalogueAdapter
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.launchability import (
    CATALOGUE_UNREAD,
    CONTENT_MISSING,
    CONTENT_UNCHECKED,
    CONTENT_UNREADABLE,
    CONTENTS_UNREAD,
    DISC_FOLDER_SET,
    DISC_FOLDER_UNKNOWN,
    DISC_KEY_MISSING,
    DISC_NOT_FOUND,
    EMULATOR_DRIVE_UNKNOWN,
    ENCRYPTION_UNREAD,
    ENTRY_UNKNOWN,
    FORMAT_REFUSED,
    FORMAT_UNESTABLISHED,
    GAME_KIND_UNFOLLOWED,
    GAME_LIST_UNREAD,
    GOES_ON_FILE_MISSING,
    GOES_ON_FORMAT_REFUSED,
    GOES_ON_PATH_TOO_LONG,
    GOES_ON_SHEET_REFUSED,
    GOES_ON_TRACK_REFUSED,
    IMAGE_LAYOUT_UNREAD,
    INSIDE_EMULATOR_DRIVE,
    KEY_FOLDER_UNKNOWN,
    KEY_UNTESTED,
    NEEDS_INSTALLING,
    NO_SOURCE,
    NOT_A_GAME_FILE,
    NOT_A_LAUNCH_FILE,
    NOT_ESTABLISHED,
    NOT_THIS_EMULATOR,
    NOTHING_TO_START,
    OPENED_CONTENT_UNREAD,
    OPENED_FILE_MISSING,
    OPENED_FILE_UNREAD,
    OPENED_FORMAT_UNREAD,
    OPENS_PAST_BOUND,
    PATCH_SETTING_UNREAD,
    PATH_TOO_LONG,
    PATH_TOO_LONG_UNCHECKED,
    PLAYLIST_DISCS_PAST_LIMIT,
    PLAYLIST_REFUSED,
    SAVESTATE_UNREAD,
    SHEET_REFUSED,
    STARTS,
    SYSTEM_UNKNOWN,
    TAKES_NO_FOLDER,
    TRACK_REFUSED,
    VERSION_DEPENDENT,
    LaunchStatement,
)

_LINUX_SYSTEMS_SUFFIX = os.path.join(
    "retrodeck", "components", "es-de", "share", "es-de", "resources", "systems", "linux", "es_systems.xml"
)

_CATALOGUE = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>ps3</name>
    <extension>.desktop .iso .ISO .ps3 .PS3 .ps3dir .PS3DIR</extension>
    <command label="RPCS3 Shortcut (Standalone)">%EMULATOR_OS-SHELL% %ROM%</command>
    <command label="RPCS3 Directory (Standalone)">%EMULATOR_RPCS3% --no-gui %ROM%</command>
  </system>
  <system>
    <name>dreamcast</name>
    <extension>.chd .CHD .cue .CUE .gdi .GDI</extension>
    <command label="Flycast">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/flycast_libretro.so %ROM%</command>
  </system>
  <system>
    <name>n3ds</name>
    <extension>.3ds .3DS .cci .CCI .zip .ZIP</extension>
    <command label="Azahar (Standalone)">%EMULATOR_AZAHAR% %ROM%</command>
  </system>
  <system>
    <name>gba</name>
    <extension>.gba</extension>
    <command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>
  </system>
</systemList>
"""


def _seed_retrodeck(tmp_path) -> str:
    home = tmp_path / "home"
    marker = home / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck" / "retrodeck.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"paths": {"rd_home_path": str(home / "retrodeck")}}), encoding="utf-8")
    deploy = home / ".local" / "share" / "flatpak" / "app" / "net.retrodeck.retrodeck" / "current" / "active" / "files"
    bundled = deploy / _LINUX_SYSTEMS_SUFFIX
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_text(_CATALOGUE, encoding="utf-8")
    return str(home)


@pytest.fixture(autouse=True)
def _no_host_deploy(tmp_path, monkeypatch):
    """Never resolve ``/app`` out of the dev box's own RetroDECK deploy (as in ``test_atlas_catalogue.py``)."""
    monkeypatch.setattr(
        "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
    )


@pytest.fixture
def traces() -> list[str]:
    return []


def _real(home: str, traces: list[str]) -> AtlasCatalogueAdapter:
    return AtlasCatalogueAdapter(
        sources=EmulatorSourcesAdapter(user_home=home, settings={}, log_debug=traces.append),
        log_debug=traces.append,
    )


def _roms(tmp_path) -> str:
    return os.path.join(str(tmp_path), "home", "retrodeck", "roms")


class TestTheRealResolverOverARealTree:
    def test_a_ps3_pkg_has_to_be_installed_in_the_emulator_the_resolver_names(self, tmp_path, traces):
        adapter = _real(_seed_retrodeck(tmp_path), traces)
        path = f"{_roms(tmp_path)}/ps3/Puppeteer/Puppeteer.pkg"

        answer = adapter.launchability("ps3", path, label="RPCS3 Directory (Standalone)")

        assert answer.state == NEEDS_INSTALLING
        assert answer.install_in == ("RPCS3 Directory (Standalone)",)
        assert (answer.file, answer.extension) == ("Puppeteer.pkg", ".pkg")

    def test_an_installer_the_system_has_no_entry_for_is_left_out(self, tmp_path, traces):
        # The resolver names Azahar and Azahar Plus as installing a .cia, which
        # this system's list does not take; it has an entry for Azahar alone
        # (#2195 D35).
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("n3ds", f"{_roms(tmp_path)}/n3ds/Game.cia", label="Azahar (Standalone)")

        assert answer.state == NEEDS_INSTALLING
        assert answer.install_in == ("Azahar (Standalone)",)

    def test_a_single_dreamcast_track_is_not_the_file_the_system_starts_from(self, tmp_path, traces):
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("dreamcast", f"{_roms(tmp_path)}/dreamcast/Game/track03.bin", label="Flycast")

        assert answer.state == NOT_A_LAUNCH_FILE
        assert answer.starts_from == (".chd", ".CHD", ".cue", ".CUE", ".gdi", ".GDI")
        assert answer.install_in is None

    def test_a_file_the_system_takes_starts(self, tmp_path, traces):
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("gba", f"{_roms(tmp_path)}/gba/Game.gba", label="mGBA")

        assert answer.state == STARTS
        assert answer.entry == "mGBA"

    def test_an_extension_is_compared_case_exactly(self, tmp_path, traces):
        # The system lists ``.gba`` alone, and ES-DE compares exactly (#2195 D3).
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("gba", f"{_roms(tmp_path)}/gba/Game.GBA", label="mGBA")

        assert (answer.state, answer.extension) == (NOT_A_LAUNCH_FILE, ".GBA")

    def test_a_ps3_folder_the_resolver_cannot_find_starts_with_a_note_saying_so(self, tmp_path, traces):
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("ps3", f"{_roms(tmp_path)}/ps3/Game.ps3", label="RPCS3 Directory (Standalone)")

        # The resolver looks as RetroDECK's sandbox sees the path: a folder
        # under /tmp, the sandbox's own, cannot be read, and one under /var is
        # missing — and where tmp_path lies is the machine's choice.
        assert (answer.state, answer.format_unestablished) == (STARTS, False)
        assert answer.reason in (CONTENT_MISSING, CONTENT_UNREADABLE)

    def test_a_label_the_system_does_not_carry_is_not_established_for_that_reason(self, tmp_path, traces):
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("gba", f"{_roms(tmp_path)}/gba/Game.gba", label="Snes9x")

        assert (answer.state, answer.reason) == (NOT_ESTABLISHED, ENTRY_UNKNOWN)

    def test_a_system_the_catalogue_does_not_declare_is_not_established_for_that_reason(self, tmp_path, traces):
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("snes", f"{_roms(tmp_path)}/snes/Game.sfc", label="Snes9x")

        assert (answer.state, answer.reason) == (NOT_ESTABLISHED, SYSTEM_UNKNOWN)

    def test_nothing_detected_is_not_established_because_no_source_answers(self, tmp_path, traces):
        adapter = _real(str(tmp_path / "empty-home"), traces)

        answer = adapter.launchability("gba", "/roms/gba/Game.gba", label="mGBA")

        assert (answer.state, answer.reason, answer.file) == (NOT_ESTABLISHED, NO_SOURCE, "Game.gba")


class _Installation:
    """A detected installation answering ``launchable`` with what it was prepared with, recording each question."""

    kind = "retrodeck"

    def __init__(self, answer: LaunchabilityAnswer | Exception) -> None:
        self._answer = answer
        self.questions: list[tuple[str, str, str | None]] = []

    def launchable(self, system: str, content_path: str, *, label: str | None = None) -> LaunchabilityAnswer:
        self.questions.append((system, content_path, label))
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _stand_in(installation: _Installation, traces: list[str]) -> AtlasCatalogueAdapter:
    return AtlasCatalogueAdapter(
        sources=EmulatorSourcesAdapter(
            user_home="/home/deck",
            settings={},
            log_debug=traces.append,
            detect_installations=lambda home, machine: [installation],
            machine=object(),
        ),
        log_debug=traces.append,
    )


def _caveat(code: str, **data: Any) -> Caveat:
    return Caveat(code=code, message="stated by the resolver", data=data)


def _ask(answer: LaunchabilityAnswer | Exception, traces: list[str], *, label: str = "Mupen64Plus-Next"):
    installation = _Installation(answer)
    result = _stand_in(installation, traces).launchability("n64", "/roms/n64/Game.z64", label=label)
    return result, installation


class TestEachVerdictInTendersWords:
    def test_the_question_names_the_entry_tender_starts(self, traces):
        _result, installation = _ask(LaunchabilityAnswer(verdict=VERDICT_LAUNCHABLE, extension=".z64"), traces)

        assert installation.questions == [("n64", "/roms/n64/Game.z64", "Mupen64Plus-Next")]

    def test_an_entry_that_does_not_take_the_file_names_the_entries_that_do(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(verdict=VERDICT_ENTRY_NOT_ACCEPTED, extension=".z64", alternatives=("ParaLLEl N64",)),
            traces,
        )

        assert (result.state, result.other_entries) == (NOT_THIS_EMULATOR, ("ParaLLEl N64",))

    def test_an_archive_whose_contents_were_not_read_starts_and_says_so(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".zip",
                caveats=(_caveat(CAVEAT_ARCHIVE_CONTENTS_UNREAD, entry="Mupen64Plus-Next", extension=".zip"),),
            ),
            traces,
        )

        assert (result.state, result.contents_unread, result.format_unestablished) == (STARTS, True, False)

    def test_a_format_the_entry_was_not_established_to_read_is_said(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".z64",
                caveats=(_caveat(CAVEAT_ENTRY_FORMAT_UNESTABLISHED, entry="Mupen64Plus-Next", extension=".z64"),),
            ),
            traces,
        )

        assert (result.state, result.format_unestablished) == (STARTS, True)

    def test_an_installer_with_no_entry_of_the_system_is_left_out(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(
                verdict=VERDICT_NEEDS_INSTALLATION, extension=".cia", installers=(Installer(emulator="AZAHARPLUS"),)
            ),
            traces,
        )

        assert (result.state, result.install_in) == (NEEDS_INSTALLING, ())

    def test_nothing_known_to_install_it_is_an_empty_list_not_none(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(verdict=VERDICT_NEEDS_INSTALLATION, extension=".cia", installers=()), traces
        )

        assert (result.state, result.install_in) == (NEEDS_INSTALLING, ())

    def test_an_unreadable_catalogue_is_not_established_for_that_reason(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(
                verdict=VERDICT_UNKNOWN,
                extension=".z64",
                caveats=(_caveat(CAVEAT_EMULATOR_CATALOGUE_UNREADABLE), _caveat(CAVEAT_ARRANGEMENT_VERSION_DRIFTED)),
            ),
            traces,
        )

        assert (result.state, result.reason) == (NOT_ESTABLISHED, CATALOGUE_UNREAD)

    def test_a_system_the_catalogue_does_not_declare_is_worded_so(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(verdict=VERDICT_UNKNOWN, extension=".z64", caveats=(_caveat(CAVEAT_SYSTEM_UNKNOWN),)),
            traces,
        )

        assert (result.state, result.reason) == (NOT_ESTABLISHED, SYSTEM_UNKNOWN)

    def test_a_catalogue_stating_no_rom_directory_names_no_reason(self, traces):
        # rom-path-undeclared also stands for a system declared without a
        # <path>, so it never says the system is not declared.
        result, _ = _ask(
            LaunchabilityAnswer(
                verdict=VERDICT_UNKNOWN, extension=".z64", caveats=(_caveat(CAVEAT_ROM_PATH_UNDECLARED),)
            ),
            traces,
        )

        assert (result.state, result.reason) == (NOT_ESTABLISHED, None)

    def test_an_unknown_verdict_with_no_code_tender_words_names_no_reason(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(
                verdict=VERDICT_UNKNOWN, extension=".z64", caveats=(_caveat(CAVEAT_ARRANGEMENT_VERSION_DRIFTED),)
            ),
            traces,
        )

        assert (result.state, result.reason) == (NOT_ESTABLISHED, None)

    def test_a_verdict_tender_does_not_know_is_not_established(self, traces):
        result, _ = _ask(LaunchabilityAnswer(verdict="launchable-on-tuesdays", extension=".z64"), traces)

        assert result.state == NOT_ESTABLISHED

    def test_a_resolver_that_raises_is_not_established_and_traced(self, traces):
        result, _ = _ask(RuntimeError("an invariant broke"), traces)

        assert (result.state, result.reason, result.file) == (NOT_ESTABLISHED, None, "Game.z64")
        assert any("resolver failed on launchable('n64'" in line for line in traces)


# Every reason the resolver states on a refusal of its own, and every point its
# reading of a file the entry starts stopped short of, in Tender's word (#2195 D40).
_REFUSALS = {
    CAVEAT_ENTRY_TAKES_NO_DIRECTORY: TAKES_NO_FOLDER,
    CAVEAT_BOOT_NOTHING_FOUND: NOTHING_TO_START,
    CAVEAT_BOOT_FILE_UNBOOTABLE: NOT_A_GAME_FILE,
    CAVEAT_BOOT_DISC_ROOT_MISSING: DISC_NOT_FOUND,
    CAVEAT_BOOT_DISC_KEY_MISSING: DISC_KEY_MISSING,
}
_STOPPED_SHORT = {
    CAVEAT_BOOT_PATH_MISSING: CONTENT_MISSING,
    CAVEAT_BOOT_PATH_UNESTABLISHED: CONTENT_UNREADABLE,
    CAVEAT_BOOT_REGISTRY_UNREAD: GAME_LIST_UNREAD,
    CAVEAT_BOOT_CATEGORY_UNESTABLISHED: GAME_KIND_UNFOLLOWED,
    CAVEAT_BOOT_INSIDE_INTERNAL_DRIVE: INSIDE_EMULATOR_DRIVE,
    CAVEAT_BOOT_DRIVE_UNRESOLVED: EMULATOR_DRIVE_UNKNOWN,
    CAVEAT_BOOT_SAVESTATE_UNREAD: SAVESTATE_UNREAD,
    CAVEAT_BOOT_ISO_REGIONS_INVALID: IMAGE_LAYOUT_UNREAD,
    CAVEAT_BOOT_ISO_ENCRYPTION_UNREAD: ENCRYPTION_UNREAD,
    CAVEAT_BOOT_ISO_KEY_UNTESTED: KEY_UNTESTED,
    CAVEAT_BOOT_KEY_FOLDER_UNRESOLVED: KEY_FOLDER_UNKNOWN,
    CAVEAT_BOOT_DISC_DIR_CONFIGURED: DISC_FOLDER_SET,
    CAVEAT_BOOT_DISC_DIR_UNRESOLVED: DISC_FOLDER_UNKNOWN,
    CAVEAT_ENTRY_FORMAT_CONTENT_UNREAD: CONTENT_UNCHECKED,
    CAVEAT_ENTRY_FORMAT_BUILD_DEPENDENT: VERSION_DEPENDENT,
}


def _ask_ps3(answer: LaunchabilityAnswer, traces: list[str]):
    installation = _Installation(answer)
    return _stand_in(installation, traces).launchability(
        "ps3", "/roms/ps3/Game.ps3", label="RPCS3 Shortcut (Standalone)"
    )


class TestTheResolversOwnReasonsInTendersWords:
    def test_every_boot_code_the_resolver_states_is_worded(self):
        boot_codes = {getattr(atlas, name) for name in atlas.__all__ if name.startswith("CAVEAT_BOOT_")}

        assert boot_codes <= set(_REFUSALS) | set(_STOPPED_SHORT)

    @pytest.mark.parametrize(("code", "word"), sorted(_REFUSALS.items()))
    def test_an_entry_that_refuses_for_a_reason_of_the_resolvers_names_it(self, code, word, traces):
        result = _ask_ps3(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".ps3",
                alternatives=("RPCS3 Directory (Standalone)",),
                caveats=(_caveat(code, entry="RPCS3 Shortcut (Standalone)"),),
            ),
            traces,
        )

        assert (result.state, result.reason, result.other_entries) == (
            NOT_THIS_EMULATOR,
            word,
            ("RPCS3 Directory (Standalone)",),
        )

    @pytest.mark.parametrize(("code", "word"), sorted(_STOPPED_SHORT.items()))
    def test_a_file_that_starts_names_what_the_reading_stopped_short_of(self, code, word, traces):
        result = _ask_ps3(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".ps3",
                caveats=(
                    _caveat(code, entry="RPCS3 Shortcut (Standalone)"),
                    _caveat(CAVEAT_ARRANGEMENT_VERSION_DRIFTED),
                ),
            ),
            traces,
        )

        assert (result.state, result.reason, result.format_unestablished) == (STARTS, word, False)

    def test_a_refusal_the_resolver_states_no_reason_for_names_none(self, traces):
        result = _ask_ps3(LaunchabilityAnswer(verdict=VERDICT_ENTRY_NOT_ACCEPTED, extension=".zip"), traces)

        assert (result.state, result.reason) == (NOT_THIS_EMULATOR, None)

    def test_a_refusal_code_is_never_read_as_a_note_on_a_file_that_starts(self, traces):
        result = _ask_ps3(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".ps3",
                caveats=(_caveat(CAVEAT_ENTRY_TAKES_NO_DIRECTORY),),
            ),
            traces,
        )

        assert (result.state, result.reason) == (STARTS, None)

    def test_a_note_code_is_never_read_as_a_refusal_reason(self, traces):
        result = _ask_ps3(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".ps3",
                caveats=(_caveat(CAVEAT_BOOT_REGISTRY_UNREAD),),
            ),
            traces,
        )

        assert (result.state, result.reason) == (NOT_THIS_EMULATOR, None)

    def test_the_payload_carries_the_reason(self, traces):
        result = _ask_ps3(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".ps3",
                caveats=(_caveat(CAVEAT_ENTRY_TAKES_NO_DIRECTORY),),
            ),
            traces,
        )

        assert result.payload(platform_name="PlayStation 3")["reason"] == TAKES_NO_FOLDER


# What the resolver states about the files a disc file opens, in Tender's word
# (#2698 D7): a failure of the open by the effect it states for it (#2698 D5), and what
# the reading could not follow.
_STOPS = {
    CAVEAT_OPENS_FILE_MISSING: OPENED_FILE_MISSING,
    CAVEAT_OPENS_TRACK_REFUSED: TRACK_REFUSED,
    CAVEAT_OPENS_SHEET_REFUSED: SHEET_REFUSED,
    CAVEAT_OPENS_FORMAT_REFUSED: FORMAT_REFUSED,
    CAVEAT_OPENS_PATH_TOO_LONG: PATH_TOO_LONG,
    CAVEAT_OPENS_PLAYLIST_REFUSED: PLAYLIST_REFUSED,
}
_GOES_ON = {
    CAVEAT_OPENS_FILE_MISSING: GOES_ON_FILE_MISSING,
    CAVEAT_OPENS_TRACK_REFUSED: GOES_ON_TRACK_REFUSED,
    CAVEAT_OPENS_SHEET_REFUSED: GOES_ON_SHEET_REFUSED,
    CAVEAT_OPENS_FORMAT_REFUSED: GOES_ON_FORMAT_REFUSED,
    CAVEAT_OPENS_PATH_TOO_LONG: GOES_ON_PATH_TOO_LONG,
}
_UNESTABLISHED = {CAVEAT_OPENS_PATH_TOO_LONG: PATH_TOO_LONG_UNCHECKED}
_NOT_FOLLOWED = {
    CAVEAT_OPENS_FORMAT_UNREAD: OPENED_FORMAT_UNREAD,
    CAVEAT_OPENS_CONTENT_UNREAD: OPENED_CONTENT_UNREAD,
    CAVEAT_OPENS_FILE_UNREAD: OPENED_FILE_UNREAD,
    CAVEAT_OPENS_SETTING_UNREAD: PATCH_SETTING_UNREAD,
    CAVEAT_OPENS_BOUND_EXCEEDED: OPENS_PAST_BOUND,
}

_PSX_ENTRY = "Beetle PSX"
_TRACK = "/roms/psx/Game/Game (Track 2).bin"


def _ask_psx(answer: LaunchabilityAnswer, traces: list[str]):
    installation = _Installation(answer)
    return _stand_in(installation, traces).launchability("psx", "/roms/psx/Game/Game.cue", label=_PSX_ENTRY)


def _opens(code: str, effect: str | None = None, file: str = _TRACK) -> Caveat:
    if effect is None:
        return _caveat(code, entry=_PSX_ENTRY, file=file)
    return _caveat(code, entry=_PSX_ENTRY, file=file, effect=effect)


class TestWhatADiscFileOpensInTendersWords:
    def test_every_opens_code_the_resolver_states_is_worded(self):
        opens_codes = {getattr(atlas, name) for name in atlas.__all__ if name.startswith("CAVEAT_OPENS_")}

        assert opens_codes == set(_STOPS) | set(_NOT_FOLLOWED) | {CAVEAT_OPENS_ENTRY_IGNORED}

    def test_every_failure_is_read_by_each_effect_the_resolver_states_for_it(self):
        assert set(OPENS_EFFECTS) == {EFFECT_STOPS, EFFECT_GOES_ON, EFFECT_UNESTABLISHED}
        assert set(_STOPS) == set(OPENS_FAILURE_CODES)
        assert set(_GOES_ON) == set(OPENS_FAILURE_CODES) - {CAVEAT_OPENS_PLAYLIST_REFUSED}

    @pytest.mark.parametrize(("code", "word"), sorted(_STOPS.items()))
    def test_the_stop_is_the_refusal_s_reason_and_names_its_file(self, code, word, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".cue",
                alternatives=("SwanStation",),
                caveats=(_opens(code, EFFECT_STOPS),),
            ),
            traces,
        )

        assert (result.state, result.reason, result.reason_file, result.other_entries) == (
            NOT_THIS_EMULATOR,
            word,
            "Game (Track 2).bin",
            ("SwanStation",),
        )

    def test_a_file_the_load_goes_on_without_ahead_of_the_stop_is_not_the_reason(self, traces):
        # A playlist's missing disc the core goes on without, stated before the
        # recursion it stops on (Beetle PSX HW: a missing playlist, then one that names itself).
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".m3u",
                caveats=(
                    _opens(CAVEAT_OPENS_FILE_MISSING, EFFECT_GOES_ON, "/roms/psx/Game/Missing.m3u"),
                    _opens(CAVEAT_OPENS_SHEET_REFUSED, EFFECT_STOPS, "/roms/psx/Game/Loop.m3u"),
                ),
            ),
            traces,
        )

        assert (result.state, result.reason, result.reason_file) == (NOT_THIS_EMULATOR, SHEET_REFUSED, "Loop.m3u")

    @pytest.mark.parametrize(("code", "word"), sorted(_GOES_ON.items()))
    def test_a_file_the_start_goes_on_without_is_said_beside_it_and_named(self, code, word, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".cue",
                caveats=(_opens(code, EFFECT_GOES_ON), _caveat(CAVEAT_ARRANGEMENT_VERSION_DRIFTED)),
            ),
            traces,
        )

        assert (result.state, result.reason, result.reason_file) == (STARTS, word, "Game (Track 2).bin")

    @pytest.mark.parametrize(
        ("code", "effect", "word"),
        [
            *((code, None, word) for code, word in sorted(_NOT_FOLLOWED.items())),
            *((code, EFFECT_UNESTABLISHED, word) for code, word in sorted(_UNESTABLISHED.items())),
        ],
    )
    def test_what_could_not_be_established_is_said_beside_the_start_and_named(self, code, effect, word, traces):
        result = _ask_psx(
            LaunchabilityAnswer(verdict=VERDICT_LAUNCHABLE, extension=".cue", caveats=(_opens(code, effect),)),
            traces,
        )

        assert (result.state, result.reason, result.reason_file) == (STARTS, word, "Game (Track 2).bin")

    def test_a_playlist_s_discs_past_the_limit_are_said_beside_the_start(self, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".m3u",
                caveats=(_opens(CAVEAT_OPENS_ENTRY_IGNORED, file="/roms/megacd/Game/Game.m3u"),),
            ),
            traces,
        )

        assert (result.state, result.reason, result.reason_file) == (STARTS, PLAYLIST_DISCS_PAST_LIMIT, "Game.m3u")

    def test_a_failure_without_an_effect_names_no_reason(self, traces):
        refused = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED, extension=".cue", caveats=(_opens(CAVEAT_OPENS_FILE_MISSING),)
            ),
            traces,
        )
        starts = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE, extension=".cue", caveats=(_opens(CAVEAT_OPENS_FILE_MISSING),)
            ),
            traces,
        )

        assert (refused.reason, starts.reason) == (None, None)

    def test_what_the_reading_could_not_follow_is_never_a_refusal_reason(self, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".cue",
                caveats=(_opens(CAVEAT_OPENS_CONTENT_UNREAD), _opens(CAVEAT_OPENS_TRACK_REFUSED, EFFECT_STOPS)),
            ),
            traces,
        )

        assert (result.state, result.reason) == (NOT_THIS_EMULATOR, TRACK_REFUSED)

    def test_a_reason_of_the_resolver_s_own_names_no_file(self, traces):
        result = _ask_ps3(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".ps3",
                caveats=(_caveat(CAVEAT_ENTRY_TAKES_NO_DIRECTORY, file="/roms/ps3/Game.ps3"),),
            ),
            traces,
        )

        assert (result.reason, result.reason_file) == (TAKES_NO_FOLDER, None)

    def test_the_payload_carries_the_file_the_reason_names(self, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".cue",
                caveats=(_opens(CAVEAT_OPENS_FILE_MISSING, EFFECT_GOES_ON),),
            ),
            traces,
        )

        assert result.payload(platform_name="PlayStation")["reason_file"] == "Game (Track 2).bin"


def _disc_opens_vector(name: str) -> dict[str, Any]:
    return next(
        vector for stem, vector in vectors_asking("launchable") if stem == "disc-opens" and vector["name"] == name
    )


def _through_tender(inp: dict[str, Any], traces: list[str], label: str = "Genesis Plus GX"):
    """The recorded machine's question asked live and read through Tender's reading module."""
    adapter = AtlasCatalogueAdapter(
        sources=EmulatorSourcesAdapter(
            user_home=inp["home"], settings={}, log_debug=traces.append, machine=fixture_machine(inp)
        ),
        log_debug=traces.append,
    )
    query = inp["launchable_query"]
    return adapter.launchability(query["system"], query["content_path"], label=label)


class TestGenesisPlusGxNotesOnlyWhereTheyHold:
    """Both notes stand only where the resolver states their case, over its recorded machines (#2698 D7)."""

    @pytest.fixture(autouse=True)
    def _no_host_deploy(self):
        """A recorded machine answers for its own ``/var/lib/flatpak`` deploy; nothing on this host is read."""

    def test_a_playlist_past_four_discs_says_so(self, traces):
        inp = _disc_opens_vector("opens-genesis-plus-gx-a-playlist-past-four-discs-ignores-the-rest")["input"]

        result = _through_tender(inp, traces)

        assert (result.state, result.reason, result.reason_file) == (STARTS, PLAYLIST_DISCS_PAST_LIMIT, "Game.m3u")

    def test_a_playlist_of_four_discs_says_nothing(self, traces):
        inp = copy.deepcopy(
            _disc_opens_vector("opens-genesis-plus-gx-a-playlist-past-four-discs-ignores-the-rest")["input"]
        )
        playlist = inp["launchable_query"]["content_path"]
        inp["files"][playlist] = "".join(inp["files"][playlist].splitlines(keepends=True)[:4])

        result = _through_tender(inp, traces)

        assert (result.state, result.reason) == (STARTS, None)

    def test_a_sheet_path_of_255_bytes_is_not_established(self, traces):
        inp = _disc_opens_vector("opens-genesis-plus-gx-a-sheet-path-of-255-bytes-is-not-established")["input"]

        result = _through_tender(inp, traces)

        assert (result.state, result.reason, result.reason_file) == (STARTS, PATH_TOO_LONG_UNCHECKED, "Game.cue")

    def test_a_sheet_path_under_255_bytes_says_nothing(self, traces):
        inp = copy.deepcopy(
            _disc_opens_vector("opens-genesis-plus-gx-a-sheet-path-of-255-bytes-is-not-established")["input"]
        )
        long_folder = os.path.dirname(inp["launchable_query"]["content_path"])
        short_folder = os.path.join(os.path.dirname(long_folder), "Game")
        inp["files"] = {path.replace(long_folder, short_folder): text for path, text in inp["files"].items()}
        inp["launchable_query"]["content_path"] = inp["launchable_query"]["content_path"].replace(
            long_folder, short_folder
        )

        result = _through_tender(inp, traces)

        assert (result.state, result.reason) == (STARTS, None)


class TestEveryStatementIsSaid:
    """Every statement the resolver makes beside a verdict is carried, in its order (#2698 D8)."""

    @pytest.fixture(autouse=True)
    def _no_host_deploy(self):
        """A recorded machine answers for its own ``/var/lib/flatpak`` deploy; nothing on this host is read."""

    def test_beside_a_start_each_statement_is_carried_in_the_resolver_s_order(self, traces):
        missing = _opens(CAVEAT_OPENS_FILE_MISSING, EFFECT_GOES_ON, "/roms/psx/Game/Disc 2.cue")
        unread = _opens(CAVEAT_OPENS_FORMAT_UNREAD, file="/roms/psx/Game/Disc 3.chd")

        first = _ask_psx(
            LaunchabilityAnswer(verdict=VERDICT_LAUNCHABLE, extension=".m3u", caveats=(missing, unread)), traces
        )
        swapped = _ask_psx(
            LaunchabilityAnswer(verdict=VERDICT_LAUNCHABLE, extension=".m3u", caveats=(unread, missing)), traces
        )

        assert first.statements == (
            LaunchStatement(GOES_ON_FILE_MISSING, "Disc 2.cue"),
            LaunchStatement(OPENED_FORMAT_UNREAD, "Disc 3.chd"),
        )
        assert swapped.statements == tuple(reversed(first.statements))
        assert (first.reason, swapped.reason) == (GOES_ON_FILE_MISSING, OPENED_FORMAT_UNREAD)

    def test_beside_a_refusal_every_statement_but_the_stop_is_carried_in_the_resolver_s_order(self, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_ENTRY_NOT_ACCEPTED,
                extension=".m3u",
                caveats=(
                    _opens(CAVEAT_OPENS_FILE_MISSING, EFFECT_GOES_ON, "/roms/pce/Game/Disc 2.cue"),
                    _opens(CAVEAT_OPENS_CONTENT_UNREAD, file="/roms/pce/Game/Disc 1.cue"),
                    _opens(CAVEAT_OPENS_PLAYLIST_REFUSED, EFFECT_STOPS, "/roms/pce/Game/Game.m3u"),
                ),
            ),
            traces,
        )

        assert (result.reason, result.reason_file) == (PLAYLIST_REFUSED, "Game.m3u")
        assert result.statements == (
            LaunchStatement(GOES_ON_FILE_MISSING, "Disc 2.cue"),
            LaunchStatement(OPENED_CONTENT_UNREAD, "Disc 1.cue"),
        )

    def test_beside_a_refusal_a_disc_whose_opening_was_not_checked_is_said(self, traces):
        # The recorded Beetle PCE playlist whose second disc is missing, with
        # that disc a .chd: the core's open of it is not followed, and the load
        # fails for the playlist.
        inp = copy.deepcopy(
            _disc_opens_vector("opens-beetle-pce-a-playlist-missing-its-second-disc-states-which-caveat-stops")["input"]
        )
        folder = os.path.dirname(inp["launchable_query"]["content_path"])
        inp["files"][f"{folder}/Game.m3u"] = "Game (Disc 1).cue\nGame (Disc 2).chd\n"
        inp["files"][f"{folder}/Game (Disc 2).chd"] = {"size": 4096}

        result = _through_tender(inp, traces, label="Beetle PCE")

        assert (result.state, result.reason, result.reason_file) == (NOT_THIS_EMULATOR, PLAYLIST_REFUSED, "Game.m3u")
        assert result.statements == (LaunchStatement(OPENED_FORMAT_UNREAD, "Game (Disc 2).chd"),)

    def test_the_format_and_archive_notes_are_statements_of_their_own(self, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".zip",
                caveats=(
                    _caveat(CAVEAT_ENTRY_FORMAT_UNESTABLISHED),
                    _opens(CAVEAT_OPENS_SETTING_UNREAD),
                    _caveat(CAVEAT_ARCHIVE_CONTENTS_UNREAD),
                ),
            ),
            traces,
        )

        assert result.statements == (
            LaunchStatement(FORMAT_UNESTABLISHED),
            LaunchStatement(PATCH_SETTING_UNREAD, "Game (Track 2).bin"),
            LaunchStatement(CONTENTS_UNREAD),
        )
        assert (result.format_unestablished, result.contents_unread) == (True, True)

    def test_a_core_that_cannot_be_read_keeps_its_statement_beside_the_setting(self, traces):
        inp = _disc_opens_vector("opens-swanstation-a-core-that-cannot-be-read-keeps-its-statement")["input"]

        result = _through_tender(inp, traces, label="SwanStation")

        assert result.statements == (LaunchStatement(PATCH_SETTING_UNREAD), LaunchStatement(FORMAT_UNESTABLISHED))

    def test_a_path_that_names_a_folder_is_named_by_its_folder(self, traces):
        inp = _disc_opens_vector("opens-genesis-plus-gx-a-blank-playlist-line-is-a-disc-at-the-directory")["input"]

        result = _through_tender(inp, traces)

        assert result.statements == (LaunchStatement(OPENED_FILE_UNREAD, "Game"),)

    def test_the_payload_carries_every_statement(self, traces):
        result = _ask_psx(
            LaunchabilityAnswer(
                verdict=VERDICT_LAUNCHABLE,
                extension=".cue",
                caveats=(_opens(CAVEAT_OPENS_FILE_MISSING, EFFECT_GOES_ON), _caveat(CAVEAT_ENTRY_FORMAT_UNESTABLISHED)),
            ),
            traces,
        )

        assert result.payload(platform_name="PlayStation")["statements"] == [
            {"reason": GOES_ON_FILE_MISSING, "file": "Game (Track 2).bin"},
            {"reason": FORMAT_UNESTABLISHED, "file": None},
        ]
