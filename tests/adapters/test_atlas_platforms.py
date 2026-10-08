"""Tests for the atlas platforms adapter — a RomM platform's system, asked of one source at a time.

Two halves. The first drives the REAL resolver over a fabricated RetroDECK
deploy under ``tmp_path``, whose catalogue declares the systems and their
platform tags, so what is checked is the resolver's own answer read through
the adapter: the main system among regional ones, a system switched off only
in the catalogue's comments, and an id nothing maps. The second uses stand-in
installations, which is the only way to have two sources answer differently
for one platform.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from _vendor.atlas.installations import PlatformSystemMatch, PlatformSystemsAnswer

from adapters.atlas_platforms import AtlasPlatformSystemsAdapter
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.emulator_sources import CATALOGUE_UNAVAILABLE, NO_SOURCE_DETECTED, SWITCHED_OFF_SETTING
from domain.platform_system import (
    FOUND,
    NO_SYSTEM,
    STATUS_DECLARED,
    STATUS_DISABLED,
    SWITCHED_OFF,
    UNASKED,
    PlatformIds,
    PlatformSystem,
)

_SYSTEMS_SUFFIX = os.path.join(
    "retrodeck", "components", "es-de", "share", "es-de", "resources", "systems", "linux", "es_systems.xml"
)


def _system(name: str, platform: str) -> str:
    return (
        "  <system>\n"
        f"    <name>{name}</name>\n"
        f"    <path>%ROMPATH%/{name}</path>\n"
        "    <extension>.bin</extension>\n"
        '    <command label="Core">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/core_libretro.so %ROM%</command>\n'
        f"    <platform>{platform}</platform>\n"
        "  </system>\n"
    )


# Three systems for one platform, as RetroDECK ships SNES; a system declared
# only inside an XML comment, beside a different one it must not become; and
# the CD-i under the name RetroDECK declares for it.
_CATALOGUE = (
    "<systemList>\n"
    + _system("sfc", "snes")
    + _system("snes", "snes")
    + _system("snesna", "snes")
    + "<!--\n"
    + _system("xbox360", "xbox360")
    + "-->\n"
    + _system("xbox", "xbox")
    + _system("cdimono1", "cdimono1")
    + "</systemList>\n"
)


@pytest.fixture(autouse=True)
def _no_host_deploy(tmp_path, monkeypatch):
    """Never resolve ``/app`` out of the dev box's own RetroDECK deploy (as in ``test_atlas_catalogue.py``)."""
    monkeypatch.setattr(
        "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
    )


@pytest.fixture
def traces() -> list[str]:
    return []


def _seed_retrodeck(tmp_path) -> str:
    home = tmp_path / "home"
    marker = home / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck" / "retrodeck.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"paths": {"rd_home_path": str(home / "retrodeck")}}), encoding="utf-8")
    deploy = home / ".local" / "share" / "flatpak" / "app" / "net.retrodeck.retrodeck" / "current" / "active" / "files"
    catalogue = deploy / _SYSTEMS_SUFFIX
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text(_CATALOGUE, encoding="utf-8")
    return str(home)


def _real(tmp_path, traces: list[str]) -> AtlasPlatformSystemsAdapter:
    sources = EmulatorSourcesAdapter(user_home=_seed_retrodeck(tmp_path), settings={}, log_debug=traces.append)
    return AtlasPlatformSystemsAdapter(sources=sources, log_debug=traces.append)


def _kind(platform: PlatformSystem) -> str | None:
    return platform.source.kind if platform.source is not None else None


def _ask(adapter: AtlasPlatformSystemsAdapter, ids: PlatformIds, slug: str, **kwargs: Any):
    return adapter.platform_system(ids, platform_slug=slug, platform_name=slug.upper(), **kwargs)


class TestTheRealResolverOverARealTree:
    def test_the_main_system_is_taken_not_a_regional_one(self, tmp_path, traces):
        platform = _ask(_real(tmp_path, traces), PlatformIds(igdb_id=19), "snes")

        assert (platform.state, platform.system, _kind(platform)) == (FOUND, "snes", "retrodeck")

    def test_a_system_only_in_the_catalogues_comments_is_switched_off_and_not_corrected(self, tmp_path, traces):
        # IGDB 12 is the Xbox 360; RetroDECK ships it only commented out, and
        # the Xbox beside it is a different system and emulator.
        platform = _ask(_real(tmp_path, traces), PlatformIds(igdb_id=12), "xbox360")

        assert (platform.state, platform.system) == (SWITCHED_OFF, "xbox360")

    def test_an_id_nothing_maps_gives_no_system_rather_than_the_slug(self, tmp_path, traces):
        platform = _ask(_real(tmp_path, traces), PlatformIds(igdb_id=999_999), "commodore-cdtv")

        assert (platform.state, platform.system) == (NO_SYSTEM, None)

    def test_the_system_the_catalogue_declares_is_taken_whatever_rom_m_calls_it(self, tmp_path, traces):
        # IGDB 117 is the CD-i, which RetroDECK declares as cdimono1.
        platform = _ask(_real(tmp_path, traces), PlatformIds(igdb_id=117), "philips-cd-i")

        assert platform.system == "cdimono1"

    def test_a_later_id_is_asked_where_the_earlier_ones_give_nothing(self, tmp_path, traces):
        ids = PlatformIds(igdb_id=999_999, libretro_slug="Not A Platform", ss_id=4)

        assert _ask(_real(tmp_path, traces), ids, "snes").system == "snes"

    def test_a_platform_with_no_ids_has_no_system(self, tmp_path, traces):
        assert _ask(_real(tmp_path, traces), PlatformIds(), "snes").state == NO_SYSTEM


class _Installation:
    """A source's handle that answers each ``(vocabulary, value)`` from a table, and counts the questions."""

    def __init__(self, kind: str, answers: dict[tuple[str, str], PlatformSystemsAnswer | Exception]) -> None:
        self.kind = kind
        self._answers = answers
        self.asked: list[tuple[str, str]] = []

    def systems_for_platform(self, vocabulary: str, value: str) -> PlatformSystemsAnswer:
        self.asked.append((vocabulary, value))
        answer = self._answers.get((vocabulary, value), PlatformSystemsAnswer(vocabulary, value))
        if isinstance(answer, Exception):
            raise answer
        return answer


def _matched(vocabulary: str, value: str, system: str, status: str = STATUS_DECLARED) -> PlatformSystemsAnswer:
    return PlatformSystemsAnswer(
        vocabulary,
        value,
        (system,),
        matches=(PlatformSystemMatch(system, status, (system,), "catalogue"),),
    )


def _adapter(installations: list[Any], traces: list[str], settings: dict[str, Any] | None = None, **kwargs):
    sources = EmulatorSourcesAdapter(
        user_home="/home/deck",
        settings=settings if settings is not None else {},
        log_debug=traces.append,
        detect_installations=lambda home, machine: list(installations),
        machine=object(),
        **kwargs,
    )
    return AtlasPlatformSystemsAdapter(sources=sources, log_debug=traces.append), sources


_IDS = PlatformIds(igdb_id=29)


class TestEachSourceIsAskedForItself:
    def _two(self):
        retrodeck = _Installation("retrodeck", {("igdb", "29"): _matched("igdb", "29", "genesis")})
        emudeck = _Installation("emudeck", {("igdb", "29"): _matched("igdb", "29", "megadrive")})
        return retrodeck, emudeck

    def test_the_answering_source_answers_where_none_is_named(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces)

        platform = _ask(adapter, _IDS, "genesis")

        assert (platform.system, _kind(platform)) == ("genesis", "retrodeck")
        assert emudeck.asked == []

    def test_emu_deck_answers_with_its_own_system(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck"]})

        platform = _ask(adapter, _IDS, "genesis")

        assert (platform.system, _kind(platform)) == ("megadrive", "emudeck")
        assert retrodeck.asked == []

    def test_a_named_source_is_asked_whatever_answers_and_whatever_its_switch(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck"]})

        platform = _ask(adapter, _IDS, "genesis", source="retrodeck")

        assert (platform.system, _kind(platform)) == ("genesis", "retrodeck")
        assert emudeck.asked == []


class TestWhereNoSourceCanBeAsked:
    def test_no_source_detected(self, traces):
        adapter, _ = _adapter([], traces)

        platform = _ask(adapter, _IDS, "genesis")

        assert (platform.state, platform.unasked, platform.source) == (UNASKED, NO_SOURCE_DETECTED, None)

    def test_every_source_switched_off(self, traces):
        adapter, _ = _adapter([_Installation("retrodeck", {})], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck"]})

        assert _ask(adapter, _IDS, "genesis").unasked == "switched_off"

    def test_a_named_source_that_is_not_detected(self, traces):
        adapter, _ = _adapter([_Installation("emudeck", {})], traces)

        platform = _ask(adapter, _IDS, "genesis", source="retrodeck")

        assert (platform.state, platform.unasked) == (UNASKED, NO_SOURCE_DETECTED)

    def test_a_detection_that_raised_establishes_nothing_about_a_named_source(self, traces):
        def boom(home, machine):
            raise RuntimeError("detection failed")

        sources = EmulatorSourcesAdapter(
            user_home="/home/deck", settings={}, log_debug=traces.append, detect_installations=boom, machine=object()
        )
        adapter = AtlasPlatformSystemsAdapter(sources=sources, log_debug=traces.append)

        platform = _ask(adapter, _IDS, "genesis", source="retrodeck")

        assert (platform.state, platform.unasked) == (UNASKED, CATALOGUE_UNAVAILABLE)

    def test_a_question_that_raised_establishes_no_system(self, traces):
        adapter, _ = _adapter([_Installation("retrodeck", {("igdb", "29"): RuntimeError("boom")})], traces)

        platform = _ask(adapter, _IDS, "genesis")

        assert (platform.state, platform.unasked, _kind(platform)) == (UNASKED, CATALOGUE_UNAVAILABLE, "retrodeck")
        assert any("resolver failed" in line for line in traces)


class TestOneReading:
    def test_the_same_id_is_asked_once_within_a_reading(self, traces):
        retrodeck = _Installation("retrodeck", {("igdb", "29"): _matched("igdb", "29", "genesis")})
        adapter, sources = _adapter([retrodeck], traces)
        reading = sources.read()

        _ask(adapter, _IDS, "genesis", reading=reading)
        _ask(adapter, _IDS, "genesis", reading=reading)

        assert retrodeck.asked == [("igdb", "29")]

    def test_an_id_after_the_deciding_one_is_not_asked(self, traces):
        retrodeck = _Installation("retrodeck", {("igdb", "29"): _matched("igdb", "29", "genesis")})
        adapter, _ = _adapter([retrodeck], traces)

        _ask(adapter, PlatformIds(igdb_id=29, ss_id=1, tgdb_id=18), "genesis")

        assert retrodeck.asked == [("igdb", "29")]

    def test_the_four_ids_are_asked_in_the_decided_order(self, traces):
        retrodeck = _Installation("retrodeck", {("thegamesdb", "18"): _matched("thegamesdb", "18", "genesis")})
        adapter, _ = _adapter([retrodeck], traces)

        platform = _ask(adapter, PlatformIds(igdb_id=29, libretro_slug="Sega - Mega Drive", ss_id=1, tgdb_id=18), "md")

        assert platform.system == "genesis"
        assert retrodeck.asked == [
            ("igdb", "29"),
            ("libretro", "Sega - Mega Drive"),
            ("screenscraper", "1"),
            ("thegamesdb", "18"),
        ]


def test_the_status_vocabulary_is_the_resolvers():
    from _vendor.atlas.installations import PLATFORM_STATUS_DECLARED, PLATFORM_STATUS_DISABLED

    assert (STATUS_DECLARED, STATUS_DISABLED) == (PLATFORM_STATUS_DECLARED, PLATFORM_STATUS_DISABLED)
