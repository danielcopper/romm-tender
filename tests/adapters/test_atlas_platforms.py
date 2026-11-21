"""Tests for the atlas platforms adapter — a RomM platform's system, asked of one source at a time.

Three parts. The first drives the REAL resolver over a fabricated
RetroDECK deploy under ``tmp_path``, whose catalogue declares the
systems and their platform tags, so what is checked is the resolver's own
answer read through the adapter: the system RomM's slug and folder narrow to,
twins none of which is picked, a system switched off only in the catalogue's
comments, one this machine has not set up, one no frontend has, and a platform
the resolver cannot match. The second uses stand-in installations, which is
the only way to have two sources answer differently for one platform. The
third holds the adapter's status table to what the resolver exports.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from _vendor import atlas
from _vendor.atlas import (
    CAVEAT_EMULATOR_CATALOGUE_SEALED,
    CAVEAT_PLATFORM_SOURCE_UNKNOWN,
    INTERSECTION_AGREED,
    PLATFORM_STATUS_ABSENT,
    PLATFORM_STATUS_DECLARED,
    PLATFORM_STATUS_DISABLED,
    Caveat,
    translate,
)
from _vendor.atlas.installations import PlatformSystemMatch, PlatformSystemsAnswer

from adapters.atlas_platforms import _STATUS_OF, AtlasPlatformSystemsAdapter, platform_pairs
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.emulator_sources import CATALOGUE_UNAVAILABLE, NO_SOURCE_DETECTED, SWITCHED_OFF_SETTING
from domain.platform_system import (
    CONTRADICTED,
    FOUND,
    NO_SYSTEM,
    NOT_HERE,
    SEVERAL,
    SWITCHED_OFF,
    UNASKED,
    UNMATCHED,
    PlatformKeys,
    PlatformSystem,
    SystemPair,
)

if TYPE_CHECKING:
    from _vendor.atlas.platforms import PlatformSystemStatus

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


# Three systems for the SNES, as RetroDECK ships them; the Master System's two
# twins, which carry one platform tag; a system declared only inside an XML
# comment, beside a different one it must not become; and the CD-i under the
# name RetroDECK declares for it.
_CATALOGUE = (
    "<systemList>\n"
    + _system("sfc", "snes")
    + _system("snes", "snes")
    + _system("snesna", "snes")
    + _system("mark3", "mastersystem")
    + _system("mastersystem", "mastersystem")
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


def _ask(adapter: AtlasPlatformSystemsAdapter, slug: str, folder: str | None, **kwargs: Any) -> PlatformSystem:
    return adapter.platform_system(PlatformKeys(slug, folder), platform_name=slug.upper(), **kwargs)


def _pair(source: str, key: str) -> SystemPair:
    return SystemPair(source, key)


class TestThePairsAsked:
    """#2217 D16: RomM's slug and the platform's folder in RomM, and nothing else."""

    def test_the_slug_and_the_folder_are_the_two_pairs(self):
        assert platform_pairs(PlatformKeys("genesis", "megadrivejp")) == (
            _pair("romm", "genesis"),
            _pair("folder", "megadrivejp"),
        )

    def test_a_platform_without_a_folder_is_asked_by_its_slug_alone(self):
        assert platform_pairs(PlatformKeys("snes")) == (_pair("romm", "snes"),)

    def test_the_vendored_table_knows_rom_ms_column(self):
        """A table without RomM's column would read every platform as unmatched, so it fails here."""
        caveats = translate(platform_pairs(PlatformKeys("snes"))).caveats

        assert CAVEAT_PLATFORM_SOURCE_UNKNOWN not in {caveat.code for caveat in caveats}


class TestTheRealResolverOverARealTree:
    def test_the_folder_narrows_the_slug_to_one_system_and_it_is_found_as_the_answer_names_it(self, tmp_path, traces):
        platform = _ask(_real(tmp_path, traces), "snes", "snes")

        assert (platform.state, platform.taken, _kind(platform)) == (FOUND, _pair("esde", "snes"), "retrodeck")

    def test_twins_the_pairs_leave_are_several_and_none_is_picked(self, tmp_path, traces):
        """#2225 D8: Tender picks none of the systems the pairs leave; later questions ask with the pairs."""
        platform = _ask(_real(tmp_path, traces), "sms", "sms")

        assert platform.state == SEVERAL
        assert platform.named == (_pair("esde", "mark3"), _pair("esde", "mastersystem"))
        assert platform.taken == (_pair("romm", "sms"), _pair("folder", "sms"))
        assert platform.one_system is None

    def test_a_system_only_in_the_catalogues_comments_is_switched_off_and_not_corrected(self, tmp_path, traces):
        # RetroDECK ships the Xbox 360 only commented out, and the Xbox beside
        # it is a different system and emulator.
        platform = _ask(_real(tmp_path, traces), "xbox360", "xbox360")

        assert (platform.state, platform.named) == (SWITCHED_OFF, (_pair("esde", "xbox360"),))

    def test_a_system_this_machine_has_not_set_up_is_not_here(self, tmp_path, traces):
        platform = _ask(_real(tmp_path, traces), "n64", "n64")

        assert (platform.state, platform.named) == (NOT_HERE, (_pair("esde", "n64"),))

    def test_a_platform_no_frontend_has_a_system_for_has_no_system(self, tmp_path, traces):
        platform = _ask(_real(tmp_path, traces), "altair-8800", "altair-8800")

        assert (platform.state, platform.taken, platform.named) == (NO_SYSTEM, None, ())

    def test_a_platform_the_resolver_knows_by_neither_pair_is_unmatched_never_its_slug(self, tmp_path, traces):
        """#2751 D2: case one, never a system named after RomM's slug."""
        platform = _ask(_real(tmp_path, traces), "not-a-platform", "not-a-platform")

        assert (platform.state, platform.taken) == (UNMATCHED, None)

    def test_a_slug_and_a_folder_that_name_different_systems_contradict_each_other(self, tmp_path, traces):
        """#2751 D2 (changed): its own case, naming the slug and the folder, never case one."""
        platform = _ask(_real(tmp_path, traces), "snes", "xbox360")

        assert (platform.state, platform.taken) == (CONTRADICTED, None)
        assert platform.keys == PlatformKeys("snes", "xbox360")

    def test_two_folders_of_one_slug_answer_their_own_systems(self, tmp_path, traces):
        """#2217 D16 (changed 2026-11-18): RomM folds megadrivejp into genesis; each folder names its own system."""
        adapter = _real(tmp_path, traces)

        genesis = _ask(adapter, "genesis", "genesis")
        japan = _ask(adapter, "genesis", "megadrivejp")

        assert genesis.named == (_pair("esde", "genesis"),)
        assert japan.named == (_pair("esde", "megadrivejp"),)

    def test_the_system_the_catalogue_declares_is_taken_whatever_rom_m_calls_it(self, tmp_path, traces):
        platform = _ask(_real(tmp_path, traces), "philips-cd-i", "cdimono1")

        assert platform.taken == _pair("esde", "cdimono1")


class TestACatalogueTheResolverCouldNotRead:
    """A refused catalogue says nothing about what this machine has: never "not set up here" for every platform."""

    def _catalogue(self, tmp_path):
        deploy = tmp_path / "home" / ".local" / "share" / "flatpak" / "app" / "net.retrodeck.retrodeck"
        return deploy / "current" / "active" / "files" / _SYSTEMS_SUFFIX

    def test_a_systems_file_that_does_not_parse_leaves_every_platform_unasked(self, tmp_path, traces):
        adapter = _real(tmp_path, traces)
        self._catalogue(tmp_path).write_text("<systemList><system>broken", encoding="utf-8")

        for slug in ("snes", "n64", "sms"):
            platform = _ask(adapter, slug, slug)

            assert (platform.state, platform.unasked, platform.named) == (UNASKED, "catalogue_invalid", ())

    def test_a_systems_file_that_cannot_be_read_leaves_the_platform_unasked(self, tmp_path, traces):
        adapter = _real(tmp_path, traces)
        self._catalogue(tmp_path).unlink()

        platform = _ask(adapter, "snes", "snes")

        assert (platform.state, platform.named) == (UNASKED, ())
        assert platform.unasked == "unavailable"

    def test_a_retro_deck_never_set_up_leaves_the_platform_unasked_for_that_reason(self, tmp_path, traces):
        adapter = _real(tmp_path, traces)
        (
            tmp_path / "home" / ".var" / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck" / "retrodeck.json"
        ).unlink()

        platform = _ask(adapter, "snes", "snes")

        assert (platform.state, platform.unasked, platform.named) == (UNASKED, "not_set_up", ())

    def test_a_sealed_catalogue_leaves_the_platform_unasked_for_that_reason(self, traces):
        sealed = Caveat(CAVEAT_EMULATOR_CATALOGUE_SEALED, "sealed", {})
        answer = PlatformSystemsAnswer(translate((("romm", "snes"),)), caveats=(sealed,))
        adapter, _ = _adapter([_Installation("retrodeck", {"snes": answer})], traces)

        platform = _ask(adapter, "snes", "snes")

        assert (platform.state, platform.unasked) == (UNASKED, "sealed")


class _Installation:
    """A source's handle that answers each tuple of pairs from a table, and records the questions."""

    def __init__(self, kind: str, answers: dict[str, PlatformSystemsAnswer | Exception]) -> None:
        self.kind = kind
        self._answers = answers
        self.asked: list[tuple[tuple[str, ...], ...]] = []

    def systems_for_platforms(self, pairs: tuple[tuple[str, str], ...]) -> PlatformSystemsAnswer:
        self.asked.append(tuple(tuple(pair) for pair in pairs))
        answer = self._answers.get(pairs[0][1], PlatformSystemsAnswer(translate(pairs)))
        if isinstance(answer, Exception):
            raise answer
        return answer


def _matched(
    slug: str, *systems: tuple[str, str], status: PlatformSystemStatus = PLATFORM_STATUS_DECLARED
) -> PlatformSystemsAnswer:
    translation = translate((("romm", slug),))
    assert translation.intersection_status == INTERSECTION_AGREED
    return PlatformSystemsAnswer(
        translation,
        matches=tuple(PlatformSystemMatch(system, status, (system[1],), "catalogue") for system in systems),
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


class TestEachSourceIsAskedForItself:
    """#2217 D17: every source is asked with the same pairs and answers in its own systems."""

    def _two(self):
        retrodeck = _Installation("retrodeck", {"genesis": _matched("genesis", ("esde", "genesis"))})
        emudeck = _Installation("emudeck", {"genesis": _matched("genesis", ("esde", "megadrive"))})
        return retrodeck, emudeck

    def test_the_answering_source_answers_where_none_is_named(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces)

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.taken, _kind(platform)) == (_pair("esde", "genesis"), "retrodeck")
        assert emudeck.asked == []

    def test_emu_deck_answers_with_its_own_system_asked_with_the_same_pairs(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck"]})

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.taken, _kind(platform)) == (_pair("esde", "megadrive"), "emudeck")
        assert emudeck.asked == [(("romm", "genesis"), ("folder", "genesis"))]
        assert retrodeck.asked == []

    def test_with_every_source_switched_off_none_would_be_asked_and_it_says_why(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck", "emudeck"]})

        assert adapter.asked_or_why() == (None, "switched_off")
        assert adapter.asked_or_why(source="retrodeck")[1] is None

    def test_a_platform_no_source_is_asked_for_carries_why_in_both_fields(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck", "emudeck"]})

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.state, platform.unasked, platform.unanswered) == (UNASKED, "switched_off", "switched_off")

    def test_with_no_source_detected_it_says_so(self, traces):
        adapter, _ = _adapter([], traces)

        assert adapter.asked_or_why() == (None, "no_source")

    def test_with_a_source_that_answers_nothing_is_unanswered(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces)

        assert adapter.asked_or_why()[1] is None

    def test_a_named_source_is_asked_whatever_answers_and_whatever_its_switch(self, traces):
        retrodeck, emudeck = self._two()
        adapter, _ = _adapter([retrodeck, emudeck], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck"]})

        platform = _ask(adapter, "genesis", "genesis", source="retrodeck")

        assert (platform.taken, _kind(platform)) == (_pair("esde", "genesis"), "retrodeck")
        assert emudeck.asked == []

    def test_a_source_that_names_no_system_is_not_stood_in_for_by_another(self, traces):
        retrodeck = _Installation("retrodeck", {"genesis": _matched("genesis")})
        emudeck = _Installation("emudeck", {"genesis": _matched("genesis", ("esde", "megadrive"))})
        adapter, _ = _adapter([retrodeck, emudeck], traces)

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.state, _kind(platform)) == (NO_SYSTEM, "retrodeck")
        assert emudeck.asked == []


class TestTheSourceAQuestionGoesTo:
    def test_the_answering_source_or_the_one_named(self, traces):
        retrodeck, emudeck = _Installation("retrodeck", {}), _Installation("emudeck", {})
        adapter, _ = _adapter([retrodeck, emudeck], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck"]})

        answering = adapter.asked_source()
        named = adapter.asked_source(source="retrodeck")

        assert (answering.kind if answering else None, named.kind if named else None) == ("emudeck", "retrodeck")
        assert adapter.asked_source(source="bare_retroarch_native") is None
        assert retrodeck.asked == emudeck.asked == []


class TestWhereNoSourceCanBeAsked:
    def test_no_source_detected(self, traces):
        adapter, _ = _adapter([], traces)

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.state, platform.unasked, platform.source) == (UNASKED, NO_SOURCE_DETECTED, None)

    def test_every_source_switched_off(self, traces):
        adapter, _ = _adapter([_Installation("retrodeck", {})], traces, settings={SWITCHED_OFF_SETTING: ["retrodeck"]})

        assert _ask(adapter, "genesis", "genesis").unasked == "switched_off"

    def test_a_named_source_that_is_not_detected(self, traces):
        adapter, _ = _adapter([_Installation("emudeck", {})], traces)

        platform = _ask(adapter, "genesis", "genesis", source="retrodeck")

        assert (platform.state, platform.unasked) == (UNASKED, NO_SOURCE_DETECTED)

    def test_a_detection_that_raised_establishes_nothing_about_a_named_source(self, traces):
        def boom(home, machine):
            raise RuntimeError("detection failed")

        sources = EmulatorSourcesAdapter(
            user_home="/home/deck", settings={}, log_debug=traces.append, detect_installations=boom, machine=object()
        )
        adapter = AtlasPlatformSystemsAdapter(sources=sources, log_debug=traces.append)

        platform = _ask(adapter, "genesis", "genesis", source="retrodeck")

        assert (platform.state, platform.unasked) == (UNASKED, CATALOGUE_UNAVAILABLE)

    def test_a_question_that_raised_establishes_no_system(self, traces):
        adapter, _ = _adapter([_Installation("retrodeck", {"genesis": RuntimeError("boom")})], traces)

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.state, platform.unasked, _kind(platform)) == (UNASKED, CATALOGUE_UNAVAILABLE, "retrodeck")
        assert any("resolver failed" in line for line in traces)


class TestOneReading:
    def test_the_same_platform_is_asked_once_within_a_reading(self, traces):
        retrodeck = _Installation("retrodeck", {"genesis": _matched("genesis", ("esde", "genesis"))})
        adapter, sources = _adapter([retrodeck], traces)
        reading = sources.read()

        _ask(adapter, "genesis", "genesis", reading=reading)
        _ask(adapter, "genesis", "genesis", reading=reading)

        assert retrodeck.asked == [(("romm", "genesis"), ("folder", "genesis"))]

    def test_one_question_with_both_pairs_and_no_id_of_rom_ms(self, traces):
        retrodeck = _Installation("retrodeck", {"genesis": _matched("genesis", ("esde", "genesis"))})
        adapter, _ = _adapter([retrodeck], traces)

        _ask(adapter, "genesis", "megadrivejp")

        assert retrodeck.asked == [(("romm", "genesis"), ("folder", "megadrivejp"))]


class TestTheResolversStatusesAreReadHere:
    def test_every_status_the_resolver_exports_is_read(self):
        stated = {getattr(atlas, name) for name in atlas.__all__ if name.startswith("PLATFORM_STATUS_")}

        assert set(_STATUS_OF) == stated

    @pytest.mark.parametrize(
        ("status", "state"),
        [
            (PLATFORM_STATUS_DECLARED, FOUND),
            (PLATFORM_STATUS_DISABLED, SWITCHED_OFF),
            (PLATFORM_STATUS_ABSENT, NOT_HERE),
        ],
    )
    def test_each_status_decides_as_it_states(self, traces, status, state):
        retrodeck = _Installation("retrodeck", {"genesis": _matched("genesis", ("esde", "genesis"), status=status)})
        adapter, _ = _adapter([retrodeck], traces)

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.state, platform.named) == (state, (_pair("esde", "genesis"),))

    def test_a_status_the_adapter_does_not_read_establishes_no_system(self, traces):
        unread: Any = (_Unread(("esde", "genesis"), "pending"),)
        answer = PlatformSystemsAnswer(translate((("romm", "genesis"),)), matches=unread)
        adapter, _ = _adapter([_Installation("retrodeck", {"genesis": answer})], traces)

        platform = _ask(adapter, "genesis", "genesis")

        assert (platform.state, platform.unasked, platform.taken) == (UNASKED, CATALOGUE_UNAVAILABLE, None)
        assert any("'pending'" in line for line in traces)


class _Unread:
    """A match whose status no release of the resolver states today; its own type, since the real one refuses it."""

    def __init__(self, system: tuple[str, str], status: str) -> None:
        self.system = system
        self.status = status
        self.platforms = (system[1],)
        self.tags_source = "catalogue"
