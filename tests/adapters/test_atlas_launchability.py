"""Whether one game's file starts, read in the catalogue's reading module (#2195 D2, D3, D11, D13).

The first class drives the REAL resolver over a fabricated RetroDECK deploy
under ``tmp_path``: what is under test is that each verdict the resolver states
arrives as its own state, with what that state's sentence names. The second
builds answers from real atlas value objects for the shapes a seeded tree does
not reach cheaply.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from _vendor.atlas import (
    CAVEAT_ARCHIVE_CONTENTS_UNREAD,
    CAVEAT_ARRANGEMENT_VERSION_DRIFTED,
    CAVEAT_EMULATOR_CATALOGUE_UNREADABLE,
    CAVEAT_ENTRY_FORMAT_UNESTABLISHED,
    VERDICT_ENTRY_NOT_ACCEPTED,
    VERDICT_LAUNCHABLE,
    VERDICT_NEEDS_INSTALLATION,
    VERDICT_UNKNOWN,
)
from _vendor.atlas.installations import Installer, LaunchabilityAnswer
from _vendor.atlas.placement import Caveat

from adapters.atlas_catalogue import AtlasCatalogueAdapter
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.launchability import (
    CATALOGUE_UNREAD,
    ENTRY_UNKNOWN,
    NEEDS_INSTALLING,
    NO_SOURCE,
    NOT_A_LAUNCH_FILE,
    NOT_ESTABLISHED,
    NOT_THIS_EMULATOR,
    STARTS,
    SYSTEM_UNKNOWN,
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

    def test_a_ps3_folder_named_as_es_de_scans_it_starts_its_format_not_established(self, tmp_path, traces):
        adapter = _real(_seed_retrodeck(tmp_path), traces)

        answer = adapter.launchability("ps3", f"{_roms(tmp_path)}/ps3/Game.ps3", label="RPCS3 Directory (Standalone)")

        assert answer.state == STARTS
        assert answer.format_unestablished is True

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

    def test_an_installer_with_no_entry_of_the_system_is_named_as_the_resolver_names_it(self, traces):
        result, _ = _ask(
            LaunchabilityAnswer(
                verdict=VERDICT_NEEDS_INSTALLATION, extension=".cia", installers=(Installer(emulator="AZAHARPLUS"),)
            ),
            traces,
        )

        assert (result.state, result.install_in) == (NEEDS_INSTALLING, ("AZAHARPLUS",))

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
