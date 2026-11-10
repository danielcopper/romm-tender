"""Tests for CoreService — per-platform core write + fan-out + per-game pin/clear + core menu."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
from typing import Any, ClassVar, cast

import pytest
from _factories import (
    _make_conflict_rules,
    _make_prune_conflicts,
    _refused_by_conflict_rule,
)
from fakes.fake_active_core_resolver import FakeActiveCoreResolver
from fakes.fake_core_info_provider import FakeCoreInfoProvider, libretro_option, standalone_option
from fakes.fake_disc_resolver import FakeDiscResolver
from fakes.fake_emulator_sources import FakeSourcesReading
from fakes.fake_platform_systems import RETRODECK_SOURCE, FakePlatformSystems
from fakes.fake_settings_persister import FakeSettingsPersister
from fakes.fake_start_commands import FakeStartCommands
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory
from fakes.uow_open_probe import record_uow_open

from adapters.persistence import PlatformCoreReaderAdapter
from domain.disc_selection import Disc
from domain.emulator_choice import DEFAULT, EmulatorChoice, ResolvedEmulator, VanishedPick
from domain.emulator_commands import options_to_payload
from domain.emulator_pick import StoredPick
from domain.emulator_sources import ArrangedSource
from domain.launchability import NEEDS_INSTALLING, NOT_THIS_EMULATOR, TAKES_NO_FOLDER, Launchability
from domain.platform_system import (
    NO_SYSTEM,
    SWITCHED_OFF,
    PlatformSystem,
)
from domain.rom import Rom
from domain.rom_install import RomInstall
from domain.start_command import (
    DESKTOP_FILE,
    PLACEHOLDER_UNFILLED,
    SOURCE_STARTS_NO_GAMES,
    StartCommand,
    StartNote,
    StartRefused,
)
from lib.errors import Refused
from services.cores import CoreService, CoreServiceConfig
from services.platform_picks import PlatformPicks, PlatformPicksConfig


def _seed_rom(
    uow: FakeUnitOfWork,
    *,
    rom_id: int,
    platform_slug: str = "snes",
    shortcut_app_id: int | None = None,
    emulator_override: str | None = None,
    selected_disc: str | None = None,
) -> None:
    uow.roms.save(
        Rom(
            rom_id=rom_id,
            platform_slug=platform_slug,
            platform_fs_slug=platform_slug,
            name=f"rom-{rom_id}",
            fs_name=f"rom-{rom_id}.sfc",
            shortcut_app_id=shortcut_app_id,
            last_synced_at="2026-01-01T00:00:00+00:00",
            emulator_override=emulator_override,
            emulator_override_source="retrodeck" if emulator_override is not None else None,
            selected_disc=selected_disc,
        )
    )


def _seed_install(
    uow: FakeUnitOfWork,
    *,
    rom_id: int,
    file_path: str,
    platform_slug: str = "snes",
    rom_dir: str | None = None,
    system: str | None = None,
) -> None:
    uow.rom_installs.save(
        RomInstall(
            rom_id=rom_id,
            file_path=file_path,
            rom_dir=rom_dir,
            platform_slug=platform_slug,
            system=system or platform_slug,
            installed_at="2026-01-01T00:00:00+00:00",
        )
    )


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_cores")


@pytest.fixture
def core_info() -> FakeCoreInfoProvider:
    return FakeCoreInfoProvider(
        active_core=("snes9x_libretro", "Snes9x"),
        available_cores=[
            {"core_so": "snes9x_libretro", "label": "Snes9x", "is_default": True},
            {"core_so": "bsnes_libretro", "label": "bsnes", "is_default": False},
        ],
    )


@pytest.fixture
def platform_systems() -> FakePlatformSystems:
    return FakePlatformSystems(
        mapping={
            "dc": "dreamcast",
            "sms": "mastersystem",
            "neo-geo-pocket": "ngp",
        }
    )


@pytest.fixture
def settings() -> dict[str, Any]:
    return {"platform_cores": {}}


@pytest.fixture
def settings_persister() -> FakeSettingsPersister:
    return FakeSettingsPersister()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def uow_factory(uow) -> FakeUnitOfWorkFactory:
    return FakeUnitOfWorkFactory(uow=uow)


@pytest.fixture
def active_core() -> FakeActiveCoreResolver:
    return FakeActiveCoreResolver(default=("snes9x_libretro", "Snes9x"))


@pytest.fixture
def disc_resolver() -> FakeDiscResolver:
    return FakeDiscResolver()


@pytest.fixture
def prune_conflicts():
    return _make_prune_conflicts()


@pytest.fixture
def start_commands() -> FakeStartCommands:
    return FakeStartCommands()


@pytest.fixture
def service(
    event_loop,
    prune_conflicts,
    logger,
    core_info,
    platform_systems,
    settings,
    settings_persister,
    uow_factory,
    active_core,
    disc_resolver,
    start_commands,
) -> CoreService:
    return CoreService(
        config=CoreServiceConfig(
            loop=event_loop,
            logger=logger,
            core_info=core_info,
            platform_systems=platform_systems,
            platform_picks=PlatformPicks(
                config=PlatformPicksConfig(
                    settings=settings, settings_persister=settings_persister, lock=threading.Lock()
                )
            ),
            platform_core_reader=PlatformCoreReaderAdapter(settings),
            uow_factory=uow_factory,
            active_core=active_core,
            disc_resolver=disc_resolver,
            start_commands=start_commands,
            conflict_rules=_make_conflict_rules(prune_conflicts=prune_conflicts),
        ),
    )


# ── get_platform_core_info (rom_id-keyed emulator menu) ────────────────


class TestGetPlatformCoreInfo:
    def test_happy_path(self, event_loop, service, core_info, uow):
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result == {
            "emulators": options_to_payload(core_info.get_emulator_options("snes")["options"]),
            "emulator_data_available": True,
            "emulator_data_reason": None,
            "emulator_source": {"kind": "retrodeck", "starts_games": True},
            "platform_system": {"state": "found", "source": "retrodeck", "system": "snes", "platform": "snes"},
            "active_core": "snes9x_libretro.so",
            "active_core_label": "Snes9x",
            "platform_core_label": None,
            "has_game_override": False,
            "start": {"state": "starts", "reason": None, "entry": "Snes9x"},
            "launch": None,
            "vanished_pick": None,
        }

    def test_unknown_rom_returns_empty(self, event_loop, service, core_info):
        # No ROM seeded for rom_id=7 → empty emulators + no active core, and the
        # platform-wide enumeration is never reached.
        result = event_loop.run_until_complete(service.get_platform_core_info(7))
        assert result == {
            "emulators": [],
            "emulator_data_available": True,
            "emulator_data_reason": None,
            "emulator_source": None,
            "platform_system": None,
            "active_core": None,
            "active_core_label": None,
            "platform_core_label": None,
            "has_game_override": False,
            "start": {"state": "no_entry", "reason": None, "entry": None},
            "launch": None,
            "vanished_pick": None,
        }
        assert core_info.emulator_options_calls == []

    def test_emulator_data_unavailable_surfaces_flag(self, event_loop, service, core_info, uow):
        # es_systems.xml unreadable → emulator_data_available False, empty list.
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        core_info.available = False
        core_info.options = []
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["emulator_data_available"] is False
        assert result["emulators"] == []

    def test_platform_core_label_surfaces_per_platform_override(self, event_loop, service, uow, settings):
        # A per-platform override set on the platform detail (settings.json
        # platform_cores) surfaces as platform_core_label so the menu can mark
        # the system-level selection distinctly from the active core (#954).
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        settings["platform_cores"]["snes"] = {"label": "bsnes", "source": "retrodeck"}
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["platform_core_label"] == "bsnes"

    def test_platform_core_label_none_when_platform_absent(self, event_loop, service, uow, settings):
        # A platform with no per-platform override → platform_core_label is None
        # even when OTHER platforms carry one.
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        settings["platform_cores"]["gba"] = "mGBA"
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["platform_core_label"] is None

    def test_active_marker_reflects_pin(self, event_loop, service, uow, active_core):
        # A pinned ROM surfaces the OVERRIDE core as active (via the resolver),
        # not the system default — the menu highlights the pin.
        _seed_rom(uow, rom_id=42, platform_slug="snes", emulator_override="bsnes")
        active_core.per_rom[42] = ("bsnes_libretro", "bsnes")
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["active_core"] == "bsnes_libretro.so"
        assert result["active_core_label"] == "bsnes"
        assert active_core.emulator_calls == [42]

    def test_active_marker_names_a_standalone_pick(self, event_loop, service, uow, active_core):
        # The identity names a standalone emulator, which is the whole reason
        # the payload carries it rather than the `.so`: `active_core_for_rom`
        # answers `None` here, and the BIOS pane would then highlight no line at
        # all on every platform that launches one.
        # The label and the identity are deliberately different strings: ES-DE
        # names a ROW and the resolver names an EMULATOR, and a payload that
        # projected one of them twice would pass an assertion over equal ones.
        _seed_rom(uow, rom_id=42, platform_slug="ps2", emulator_override="PCSX2 (Standalone)")
        active_core.per_rom_emulator[42] = standalone_option("PCSX2 (Standalone)", emulator="PCSX2")
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["active_core"] == "PCSX2"
        assert result["active_core_label"] == "PCSX2 (Standalone)"

    def test_active_marker_is_none_for_an_unidentified_emulator(self, event_loop, service, uow, active_core):
        # An emulator the resolver could not identify carries no identity, and
        # nothing may be keyed on it — the pane highlights nothing rather than
        # matching a row by accident.
        _seed_rom(uow, rom_id=42, platform_slug="n3ds")
        active_core.per_rom_emulator[42] = standalone_option("Citra")
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["active_core"] is None
        assert result["active_core_label"] == "Citra"

    def test_has_game_override_true_when_rom_is_pinned(self, event_loop, service, uow):
        # A ROM with a per-game emulator_override surfaces has_game_override=True
        # so the menu's "Use System Override" reset item drops its ✓ (#211). The
        # frontend can't infer this from the active core — pinning the same core
        # as the per-platform override would be indistinguishable.
        _seed_rom(uow, rom_id=42, platform_slug="snes", emulator_override="bsnes")
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["has_game_override"] is True

    def test_has_game_override_false_when_rom_unpinned(self, event_loop, service, uow):
        # An unpinned ROM (NULL override) follows the system → has_game_override
        # is False so the reset item carries the ✓ (#211).
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["has_game_override"] is False

    def test_active_marker_falls_back_to_system_default(self, event_loop, service, uow, active_core):
        # An unpinned ROM (NULL override) surfaces the system default via the
        # resolver — the same seam, no divergence from the launched core.
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        active_core.default = ("snes9x_libretro", "Snes9x")
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["active_core"] == "snes9x_libretro.so"
        assert result["active_core_label"] == "Snes9x"

    def test_empty_emulators_list(self, event_loop, service, core_info, uow):
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        core_info.options = []
        result = event_loop.run_until_complete(service.get_platform_core_info(42))
        assert result["emulators"] == []

    @pytest.mark.parametrize(
        ("slug", "system"),
        [
            ("dc", "dreamcast"),
            ("sms", "mastersystem"),
            ("neo-geo-pocket", "ngp"),
            ("snes", "snes"),  # identity: slug already equals system
        ],
    )
    def test_resolves_system_for_emulator_options(
        self, event_loop, service, core_info, platform_systems, uow, slug, system
    ):
        _seed_rom(uow, rom_id=42, platform_slug=slug)
        event_loop.run_until_complete(service.get_platform_core_info(42))
        # The platform-wide enumeration receives the NORMALIZED system.
        assert core_info.emulator_options_calls == [system]
        assert platform_systems.calls == [(slug, None)]


# ── get_system_core_info (platform-slug-keyed emulator menu) ───────────


class TestGetSystemCoreInfo:
    def test_happy_path(self, event_loop, service, core_info):
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert result == {
            "emulators": options_to_payload(core_info.get_emulator_options("snes")["options"]),
            "emulator_data_available": True,
            "emulator_data_reason": None,
            "emulator_source": {"kind": "retrodeck", "starts_games": True},
            "platform_system": {"state": "found", "source": "retrodeck", "system": "snes", "platform": "snes"},
            "active_core_label": "Snes9x",
            "start": {"state": "starts", "reason": None, "entry": "Snes9x"},
            "vanished_pick": None,
            "source_defaults": [
                {"kind": "retrodeck", "default": "Snes9x", "chosen_in_frontend": None, "reason": None},
            ],
            "tender_default": {"kind": "retrodeck", "label": "Snes9x"},
            "default_elsewhere": False,
        }

    def test_answers_for_a_platform_with_no_synced_rom(self, event_loop, service, uow, core_info):
        # The whole point of the platform-keyed read: no ROM row is consulted, so
        # a platform the user has never synced still gets its picker.
        assert list(uow.roms.iter_all()) == []
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert result["emulators"] == options_to_payload(core_info.get_emulator_options("snes")["options"])

    def test_per_platform_override_wins_over_the_default(self, event_loop, service, settings):
        settings["platform_cores"]["snes"] = {"label": "bsnes", "source": "retrodeck"}
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert result["active_core_label"] == "bsnes"

    def test_unresolvable_override_degrades_to_the_default(self, event_loop, service, settings):
        # A core the user picked and then uninstalled: the label degrades rather
        # than naming an emulator that would not launch.
        settings["platform_cores"]["snes"] = "Gone-o-Tron"
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert result["active_core_label"] == "Snes9x"

    def test_another_platforms_override_is_not_read(self, event_loop, service, settings):
        settings["platform_cores"]["gba"] = "mGBA"
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert result["active_core_label"] == "Snes9x"

    def test_no_emulator_answers_no_label(self, event_loop, service, core_info):
        core_info.options = []
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert result["active_core_label"] is None
        assert result["emulators"] == []

    def test_unreadable_es_systems_surfaces_the_flag(self, event_loop, service, core_info):
        core_info.available = False
        core_info.options = []
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert result["emulator_data_available"] is False

    def test_reads_the_options_for_the_normalized_system(self, event_loop, service, core_info, platform_systems):
        event_loop.run_until_complete(service.get_system_core_info("dc", "dc"))
        assert core_info.emulator_options_calls == ["dreamcast"]
        assert platform_systems.calls == [("dc", None)]

    def test_reads_the_emulator_options_outside_any_unit_of_work(self, event_loop, service, uow, core_info):
        # get_emulator_options re-probes the ES-DE files on every call; inside a
        # UoW that file I/O would run under BEGIN IMMEDIATE (ADR-0006).
        observed = record_uow_open(uow, core_info, "get_emulator_options")
        event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))
        assert observed == [False]


# ── set_game_core (per-game pin; B4 hard-fail-before-write) ─────────────


class TestAPlatformWithNoSystem:
    """Where the answering source has no switched-on system, the list says why and no emulator is read."""

    @pytest.mark.parametrize(
        ("state", "system", "reason"),
        [(NO_SYSTEM, None, "no_platform_system"), (SWITCHED_OFF, "xbox360", "platform_system_off")],
    )
    def test_the_platform_page_says_why(self, event_loop, service, core_info, platform_systems, state, system, reason):
        platform_systems.answers["xbox-360"] = PlatformSystem(
            state, "xbox-360", "Xbox 360", system=system, source=RETRODECK_SOURCE
        )

        result = event_loop.run_until_complete(service.get_system_core_info("xbox-360", "xbox-360"))

        assert result["emulator_data_available"] is False
        assert result["emulator_data_reason"] == reason
        assert result["emulators"] == []
        assert result["platform_system"] == {
            "state": state,
            "source": "retrodeck",
            "system": system,
            "platform": "Xbox 360",
        }
        assert core_info.emulator_options_calls == []

    def test_a_game_page_says_why(self, event_loop, service, core_info, platform_systems, uow):
        _seed_rom(uow, rom_id=42, platform_slug="vic-20")
        platform_systems.answers["vic-20"] = PlatformSystem(NO_SYSTEM, "vic-20", "VIC-20", source=RETRODECK_SOURCE)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["emulator_data_reason"] == "no_platform_system"
        assert result["platform_system"]["platform"] == "VIC-20"
        assert core_info.emulator_options_calls == []

    def test_the_page_shows_the_system_taken(self, event_loop, service, platform_systems):
        result = event_loop.run_until_complete(service.get_system_core_info("dc", "dc"))

        assert result["platform_system"] == {
            "state": "found",
            "source": "retrodeck",
            "system": "dreamcast",
            "platform": "dc",
        }


class TestAnInstalledGameKeepsItsRecordedSystem:
    def test_its_emulator_list_is_its_install_records_system(self, event_loop, service, core_info, uow):
        _seed_rom(uow, rom_id=42, platform_slug="dc")
        _seed_install(uow, rom_id=42, file_path="/roms/dc/sonic.gdi", platform_slug="dc", system="naomi")

        event_loop.run_until_complete(service.get_platform_core_info(42))

        assert core_info.emulator_options_calls == ["naomi"]

    def test_a_game_not_installed_follows_the_platforms_system(self, event_loop, service, core_info, uow):
        _seed_rom(uow, rom_id=42, platform_slug="dc")

        event_loop.run_until_complete(service.get_platform_core_info(42))

        assert core_info.emulator_options_calls == ["dreamcast"]


class TestSetGameCore:
    def test_installed_and_bound_pins_and_writes_nothing_to_the_shortcut(self, event_loop, service, uow):
        # The shortcut carries its appId; the next start asks with the pin.
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        result = event_loop.run_until_complete(service.set_game_core(42, "bsnes"))
        assert result == {"success": True}
        # The pin landed on the Rom aggregate.
        assert uow.roms.get(42).emulator_override == "bsnes"

    def test_pins_standalone_emulator(self, event_loop, service, uow, core_info):
        # A per-game pin may name a STANDALONE emulator (#1210).
        core_info.options = [standalone_option("PCSX2 (Standalone)")]
        _seed_rom(uow, rom_id=42, platform_slug="ps2", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/ps2/gt4.iso", platform_slug="ps2")
        result = event_loop.run_until_complete(service.set_game_core(42, "PCSX2 (Standalone)"))
        assert result == {"success": True}
        assert uow.roms.get(42).emulator_override == "PCSX2 (Standalone)"

    def test_uninstalled_pins_without_live_launch(self, event_loop, service, uow):
        # Bound but NOT installed → pin stored.
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99)
        result = event_loop.run_until_complete(service.set_game_core(42, "bsnes"))
        assert result == {"success": True}
        assert uow.roms.get(42).emulator_override == "bsnes"

    def test_unbound_pins_without_live_launch(self, event_loop, service, uow):
        # Installed but UNBOUND (no shortcut_app_id) → pin stored.
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=None)
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        result = event_loop.run_until_complete(service.set_game_core(42, "bsnes"))
        assert result == {"success": True}
        assert uow.roms.get(42).emulator_override == "bsnes"

    def test_an_entry_tender_cannot_start_is_pinned_like_any_other(self, event_loop, service, uow, core_info):
        # Pinned like any other (#2195 D23); the game page and the start then say
        # Tender cannot start it yet.
        core_info.options = [standalone_option("RPCS3 Shortcut (Standalone)")]
        _seed_rom(uow, rom_id=42, platform_slug="ps3", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/ps3/Game.iso", platform_slug="ps3")

        result = event_loop.run_until_complete(service.set_game_core(42, "RPCS3 Shortcut (Standalone)"))

        assert result == {"success": True}
        assert uow.roms.get(42).emulator_override == "RPCS3 Shortcut (Standalone)"

    def test_an_entry_not_installed_is_refused_and_nothing_written(self, event_loop, service, uow, core_info):
        core_info.options = [standalone_option("Missing (Standalone)", not_installed=True)]
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        setting = service.set_game_core(42, "Missing (Standalone)")

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(setting)

        assert refused.value.reason == "core_unavailable"
        assert uow.roms.get(42).emulator_override is None

    def test_unresolvable_label_fails_and_writes_nothing(self, event_loop, service, uow):
        # A label the system does not list hard-fails BEFORE any write.
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        setting = service.set_game_core(42, "Genesis Plus GX")

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(setting)

        assert (refused.value.reason, refused.value.message) == (
            "core_unavailable",
            "Emulator 'Genesis Plus GX' is not available for snes",
        )
        # No pin written.
        assert uow.roms.get(42).emulator_override is None

    def test_unknown_rom_fails(self, event_loop, service):
        setting = service.set_game_core(7, "bsnes")

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(setting)

        assert refused.value.reason == "not_found"
        assert "7" in refused.value.message

    def test_resolves_system_before_label_lookup(self, event_loop, service, uow, core_info, platform_systems):
        # The installed game's system — its install record's — is read before the
        # emulator options, so label resolution keys off it and not the raw slug.
        _seed_rom(uow, rom_id=42, platform_slug="dc", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/dc/sonic.gdi", platform_slug="dc", system="dreamcast")
        core_info.options = [libretro_option("flycast_libretro", "Flycast")]
        result = event_loop.run_until_complete(service.set_game_core(42, "Flycast"))
        assert result["success"] is True
        assert ("dc", None) in platform_systems.calls
        assert core_info.emulator_options_calls == ["dreamcast"]


# ── clear_game_core (Reset / Follow default) ───────────────────────────


class TestClearGameCore:
    def test_clear_drops_the_pin_and_resolves_no_emulator(self, event_loop, service, uow, active_core):
        # After clearing the pin the ROM follows the per-platform/system default
        # from its next start; nothing is resolved or written now.
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99, emulator_override="Snes9x")
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        result = event_loop.run_until_complete(service.clear_game_core(42))
        assert result == {"success": True}
        assert active_core.emulator_calls == []
        # The pin is gone (SQL NULL).
        assert uow.roms.get(42).emulator_override is None

    def test_clear_uninstalled_drops_pin_without_live_launch(self, event_loop, service, uow):
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99, emulator_override="bsnes")
        result = event_loop.run_until_complete(service.clear_game_core(42))
        assert result == {"success": True}
        assert uow.roms.get(42).emulator_override is None

    def test_clear_unknown_rom_fails(self, event_loop, service):
        clearing = service.clear_game_core(7)

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(clearing)

        assert refused.value.reason == "not_found"


# ── set_system_core (per-platform settings write) ──────────────────────


class TestSetSystemCore:
    def test_writes_label_to_settings_and_persists(self, event_loop, service, settings, settings_persister):
        result = event_loop.run_until_complete(service.set_system_core("snes", "snes", "Snes9x"))
        assert result["success"] is True
        assert settings["platform_cores"] == {"snes": {"label": "Snes9x", "source": "retrodeck"}}
        assert settings_persister.save_count == 1

    def test_empty_core_label_clears_settings_entry(self, event_loop, service, settings, settings_persister):
        settings["platform_cores"]["snes"] = "Snes9x"
        result = event_loop.run_until_complete(service.set_system_core("snes", "snes", ""))
        assert result["success"] is True
        # Clearing removes the platform from the map (revert to es_systems default).
        assert "snes" not in settings["platform_cores"]
        assert settings_persister.save_count == 1

    def test_clearing_absent_platform_is_noop(self, event_loop, service, settings):
        # Clearing a platform with no prior selection just leaves the map empty.
        result = event_loop.run_until_complete(service.set_system_core("psx", "psx", ""))
        assert result["success"] is True
        assert settings["platform_cores"] == {}

    def test_a_stored_switch_answers_success_and_resolves_no_game(self, event_loop, service, uow, active_core):
        # Every game following the platform asks with the new pick at its next
        # start; the switch itself touches no shortcut and takes no lease.
        _seed_rom(uow, rom_id=1, platform_slug="snes", shortcut_app_id=101)
        _seed_install(uow, rom_id=1, file_path="/roms/snes/a.sfc")

        result = event_loop.run_until_complete(service.set_system_core("snes", "snes", "bsnes"))

        assert result == {"success": True}
        assert active_core.emulator_calls == []

    def test_a_failed_settings_write_refuses_and_keeps_the_previous_core(
        self, event_loop, service, settings, settings_persister
    ):
        def refuse() -> None:
            raise OSError("read-only file system")

        settings_persister.save_settings = refuse
        setting = service.set_system_core("snes", "snes", "Snes9x")

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(setting)

        assert (refused.value.reason, refused.value.message) == ("save_failed", "Save failed: read-only file system")
        assert settings["platform_cores"] == {}

    @pytest.mark.parametrize("core_label", ["Snes9x", ""])
    def test_a_failed_settings_write_gives_the_previous_label_back(
        self, event_loop, service, settings, settings_persister, core_label
    ):
        settings["platform_cores"]["snes"] = "bsnes"

        def refuse() -> None:
            raise OSError("read-only file system")

        settings_persister.save_settings = refuse
        setting = service.set_system_core("snes", "snes", core_label)

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(setting)

        assert refused.value.reason == "save_failed"
        assert settings["platform_cores"] == {"snes": "bsnes"}


# ── transaction boundary ───────────────────────────────────────────────


def _retire_rom_between_transactions(uow: FakeUnitOfWork, core_info: FakeCoreInfoProvider, rom_id: int) -> None:
    """Delete the ROM row while the emulator options are being read.

    That read is the window between ``set_game_core``'s read transaction and
    its write transaction — the moment a background sync, a finishing download
    or the removed-game cleanup can retire the ROM from its own connection.
    """
    get_emulator_options = core_info.get_emulator_options

    def retiring(system_name, *, reading=None, content_path=None, source=None):
        with uow:
            uow.roms.delete(rom_id)
        return get_emulator_options(system_name, reading=reading)

    core_info.get_emulator_options = retiring


def _move_platform_between_transactions(
    uow: FakeUnitOfWork, core_info: FakeCoreInfoProvider, rom_id: int, *, platform_slug: str
) -> None:
    """Re-sync the ROM onto a different platform while the options are read.

    ``platform_slug`` is a synced-identity column, so a sync UPSERT landing in
    the window between the two transactions rewrites it — and the label was
    resolved against the platform the first transaction read.
    """
    get_emulator_options = core_info.get_emulator_options

    def resyncing(system_name, *, reading=None, content_path=None, source=None):
        with uow:
            _seed_rom(uow, rom_id=rom_id, platform_slug=platform_slug, shortcut_app_id=99)
        return get_emulator_options(system_name, reading=reading)

    core_info.get_emulator_options = resyncing


class TestSetGameCoreTransactionBoundary:
    """The label resolution runs between transactions, never inside one.

    The emulator-options read re-probes ES-DE's config and each option's
    install on every call, and the game's system is the install record's or a
    question to the resolver that opens a UoW of its own. A UoW takes SQLite's
    ``BEGIN IMMEDIATE`` write lock, so either read held inside one stalls every
    other writer for its duration (GLOSSARY.md → Unit of Work, #1779).
    ``FakeUnitOfWork`` shares no connection, so what a test can see is the
    ordering.
    """

    def test_label_is_resolved_outside_the_uow(self, event_loop, service, uow, core_info, platform_systems):
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        open_at_system = record_uow_open(uow, platform_systems, "rom_system")
        open_at_options = record_uow_open(uow, core_info, "get_emulator_options")

        result = event_loop.run_until_complete(service.set_game_core(42, "bsnes"))

        assert result["success"] is True
        assert open_at_system == [False]
        assert open_at_options == [False]

    def test_rom_retired_between_transactions_fails_not_found(self, event_loop, service, uow, core_info):
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        _retire_rom_between_transactions(uow, core_info, 42)
        setting = service.set_game_core(42, "bsnes")

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(setting)

        assert refused.value.reason == "not_found"
        assert "42" in refused.value.message

    def test_platform_moved_between_transactions_refuses_the_pin(self, event_loop, service, uow, core_info):
        # The label resolved against "snes"; the row is "psx" by the time it
        # would be written, so it was never checked against the platform it will
        # resolve under. The single-transaction version could not see this.
        _seed_rom(uow, rom_id=42, platform_slug="snes", shortcut_app_id=99)
        _seed_install(uow, rom_id=42, file_path="/roms/snes/mario.sfc")
        _move_platform_between_transactions(uow, core_info, 42, platform_slug="psx")
        setting = service.set_game_core(42, "bsnes")

        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(setting)

        assert refused.value.reason == "core_unavailable"
        # Names the platform the row moved to, but claims only what was checked:
        # whether the label works there was never resolved, and finding out would
        # cost the ES-DE read this split keeps out of the transaction.
        assert "psx" in refused.value.message
        assert "not verified" in refused.value.message
        assert "not available" not in refused.value.message
        with uow as u:
            assert u.roms.get(42).emulator_override is None


# ── A per-game pin's conflict rules ─────────────────────────────────────────────


def _seed_bound_install(uow: FakeUnitOfWork, rom_id: int = 42) -> None:
    _seed_rom(uow, rom_id=rom_id, platform_slug="snes", shortcut_app_id=99)
    _seed_install(uow, rom_id=rom_id, file_path="/roms/snes/mario.sfc")


class TestTheCoreWriteLeases:
    """A per-game pin its conflict rules refuse writes nothing and leaves the prune conflicts as they were."""

    @pytest.mark.parametrize(
        ("migration_pending", "cleanup_running", "reason"),
        [(True, False, "blocked_by_migration"), (False, True, "prune_active")],
    )
    def test_a_refused_pin_writes_nothing(
        self, event_loop, service, uow, prune_conflicts, migration_pending, cleanup_running, reason
    ):
        _seed_bound_install(uow)
        if cleanup_running:
            prune_conflicts.register_run("held-run")
        service._rules = _make_conflict_rules(prune_conflicts=prune_conflicts, migration_pending=migration_pending)
        setting = service.set_game_core(42, "bsnes")

        with _refused_by_conflict_rule(reason):
            event_loop.run_until_complete(setting)

        assert uow.roms.get(42).emulator_override is None
        assert prune_conflicts.conflicting_operations == 0


# ── a pick's source, a vanished pick, and every source's default (#2195 D4, D5, D20) ─


class TestAPickIsStoredWithItsSource:
    def test_a_game_pick_records_the_source_its_list_came_from(self, event_loop, service, uow):
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        assert event_loop.run_until_complete(service.set_game_core(42, "bsnes"))["success"] is True
        rom = uow.roms.get(42)
        assert (rom.emulator_override, rom.emulator_override_source) == ("bsnes", "retrodeck")

    def test_clearing_a_game_pick_drops_its_source(self, event_loop, service, uow):
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        event_loop.run_until_complete(service.set_game_core(42, "bsnes"))
        event_loop.run_until_complete(service.clear_game_core(42))
        rom = uow.roms.get(42)
        assert (rom.emulator_override, rom.emulator_override_source) == (None, None)

    def test_a_platform_pick_with_no_source_answering_is_refused_and_not_written(
        self, event_loop, service, core_info, settings, settings_persister
    ):
        core_info.source = None
        call = service.set_system_core("snes", "snes", "bsnes")
        with pytest.raises(Refused) as refused:
            event_loop.run_until_complete(call)
        assert refused.value.reason == "core_unavailable"
        assert settings["platform_cores"] == {}
        assert settings_persister.save_count == 0


class TestAVanishedPick:
    def test_a_game_pick_whose_label_is_gone_is_named_with_its_source_and_the_default_used(
        self, event_loop, service, uow, active_core
    ):
        _seed_rom(uow, rom_id=42, platform_slug="snes", emulator_override="Gone")
        snes9x = libretro_option("snes9x_libretro", "Snes9x")
        active_core.resolved[42] = ResolvedEmulator(
            choice=EmulatorChoice(
                option=snes9x, layer=DEFAULT, vanished=VanishedPick(pick=StoredPick("Gone", "retrodeck"), layer="game")
            ),
            options=[snes9x],
        )

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["vanished_pick"] == {
            "label": "Gone",
            "source": "retrodeck",
            "layer": "game",
            "used": "Snes9x",
            "used_layer": "default",
        }

    def test_a_platform_pick_whose_label_is_gone_is_named_on_the_platform_page(self, event_loop, service, settings):
        settings["platform_cores"]["snes"] = {"label": "Gone-o-Tron", "source": "retrodeck"}

        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))

        assert result["vanished_pick"] == {
            "label": "Gone-o-Tron",
            "source": "retrodeck",
            "layer": "platform",
            "used": "Snes9x",
            "used_layer": "default",
        }
        assert result["active_core_label"] == "Snes9x"


class _TwoSourceCoreInfo(FakeCoreInfoProvider):
    """RetroDECK and EmuDeck, in the order and with the switches a test states, each with its own list."""

    def __init__(self, *, order: tuple[str, ...], off: tuple[str, ...] = (), lists: dict[str, Any]) -> None:
        super().__init__(options=lists["retrodeck"])
        self._order = order
        self._off = off
        self._lists = lists

    def sources_reading(self) -> FakeSourcesReading:
        return FakeSourcesReading(
            tuple(
                ArrangedSource(kind=kind, enabled=kind not in self._off, starts_games=kind == "retrodeck")
                for kind in self._order
            ),
            {},
        )

    def get_emulator_options(self, system_name: str, *, source: str | None = None, **kwargs: Any) -> dict[str, Any]:
        answer = super().get_emulator_options(system_name, **kwargs)
        if source is None:
            return answer
        listed = self._lists[source]
        if listed is None:
            return {"available": False, "options": [], "reason": "sealed", "source": {"kind": source}}
        return {"available": True, "options": listed, "reason": None, "source": {"kind": source}}


def _service_over(
    core_info: FakeCoreInfoProvider, *, settings: dict[str, Any] | None = None, **parts: Any
) -> CoreService:
    return CoreService(
        config=CoreServiceConfig(
            loop=parts["event_loop"],
            logger=logging.getLogger("test_cores"),
            core_info=core_info,
            platform_systems=FakePlatformSystems(),
            platform_picks=PlatformPicks(
                config=PlatformPicksConfig(
                    settings=(live := settings if settings is not None else {"platform_cores": {}}),
                    settings_persister=FakeSettingsPersister(),
                    lock=threading.Lock(),
                )
            ),
            platform_core_reader=PlatformCoreReaderAdapter(live),
            uow_factory=FakeUnitOfWorkFactory(uow=FakeUnitOfWork()),
            active_core=FakeActiveCoreResolver(),
            disc_resolver=FakeDiscResolver(),
            start_commands=FakeStartCommands(),
            conflict_rules=_make_conflict_rules(prune_conflicts=_make_prune_conflicts()),
        ),
    )


def _declared(*options: Any) -> list[Any]:
    return [dataclasses.replace(option, declared_index=index) for index, option in enumerate(options)]


class TestEverySourcesDefault:
    """The platform page names Tender's default and each source's own (#2195 D4)."""

    _RETRODECK: ClassVar[list[Any]] = _declared(libretro_option("genesis_plus_gx_libretro", "Genesis Plus GX"))
    _EMUDECK: ClassVar[list[Any]] = [
        *_declared(libretro_option("picodrive_libretro", "PicoDrive")),
    ]

    def test_with_emudeck_first_tenders_default_is_emudecks(self, event_loop):
        gearsystem = dataclasses.replace(
            libretro_option("gearsystem_libretro", "Gearsystem"), declared_index=1, chosen_in_frontend=True
        )
        core_info = _TwoSourceCoreInfo(
            order=("emudeck", "retrodeck"),
            lists={"retrodeck": self._RETRODECK, "emudeck": [gearsystem, *self._EMUDECK]},
        )

        result = event_loop.run_until_complete(
            _service_over(core_info, event_loop=event_loop).get_system_core_info("mastersystem", "mastersystem")
        )

        assert result["tender_default"] == {"kind": "emudeck", "label": "PicoDrive"}
        assert result["active_core_label"] == "PicoDrive"
        assert result["source_defaults"] == [
            {"kind": "emudeck", "default": "PicoDrive", "chosen_in_frontend": "Gearsystem", "reason": None},
            {"kind": "retrodeck", "default": "Genesis Plus GX", "chosen_in_frontend": None, "reason": None},
        ]

    def test_a_source_switched_off_is_left_out_and_one_that_cannot_answer_says_why(self, event_loop):
        core_info = _TwoSourceCoreInfo(
            order=("emudeck", "retrodeck"),
            lists={"retrodeck": self._RETRODECK, "emudeck": None},
        )

        result = event_loop.run_until_complete(
            _service_over(core_info, event_loop=event_loop).get_system_core_info("mastersystem", "mastersystem")
        )

        assert result["source_defaults"] == [
            {"kind": "emudeck", "default": None, "chosen_in_frontend": None, "reason": "sealed"},
            {"kind": "retrodeck", "default": "Genesis Plus GX", "chosen_in_frontend": None, "reason": None},
        ]
        assert result["tender_default"] == {"kind": "retrodeck", "label": "Genesis Plus GX"}

        core_info._off = ("emudeck",)
        result = event_loop.run_until_complete(
            _service_over(core_info, event_loop=event_loop).get_system_core_info("mastersystem", "mastersystem")
        )
        assert [entry["kind"] for entry in result["source_defaults"]] == ["retrodeck"]

    def test_with_emudeck_first_no_line_of_the_starting_source_stands_under_its_default(self, event_loop):
        """No refusal and no vanished pick of RetroDECK's beneath EmuDeck's default (#2195 D24)."""
        missing = dataclasses.replace(
            standalone_option("Missing", not_installed=True),
            declared_index=0,
        )
        core_info = _TwoSourceCoreInfo(
            order=("emudeck", "retrodeck"),
            lists={"retrodeck": [missing], "emudeck": self._EMUDECK},
        )
        settings = {"platform_cores": {"mastersystem": {"label": "Gone", "source": "retrodeck"}}}

        result = event_loop.run_until_complete(
            _service_over(core_info, settings=settings, event_loop=event_loop).get_system_core_info(
                "mastersystem", "mastersystem"
            )
        )

        assert result["active_core_label"] == "PicoDrive"
        assert result["start"] == {"state": "starts", "reason": None, "entry": "PicoDrive"}
        assert result["vanished_pick"] is None
        assert result["default_elsewhere"] is True

    def test_with_emudeck_first_a_standing_pick_that_cannot_start_says_so(self, event_loop):
        """A pick is no default from elsewhere: its own refusal stands (#2195 D23, D24)."""
        missing = dataclasses.replace(
            standalone_option("Missing", not_installed=True),
            declared_index=0,
        )
        core_info = _TwoSourceCoreInfo(
            order=("emudeck", "retrodeck"),
            lists={"retrodeck": [missing], "emudeck": self._EMUDECK},
        )
        settings = {"platform_cores": {"mastersystem": {"label": "Missing", "source": "retrodeck"}}}

        result = event_loop.run_until_complete(
            _service_over(core_info, settings=settings, event_loop=event_loop).get_system_core_info(
                "mastersystem", "mastersystem"
            )
        )

        assert result["active_core_label"] == "Missing"
        assert result["start"]["state"] == "cannot_start"
        assert result["default_elsewhere"] is False

    def test_with_retrodeck_first_its_own_lines_stand(self, event_loop):
        """The control: the same RetroDECK list first in the order says its default cannot start."""
        missing = dataclasses.replace(
            standalone_option("Missing", not_installed=True),
            declared_index=0,
        )
        core_info = _TwoSourceCoreInfo(
            order=("retrodeck", "emudeck"),
            lists={"retrodeck": [missing], "emudeck": self._EMUDECK},
        )

        result = event_loop.run_until_complete(
            _service_over(core_info, event_loop=event_loop).get_system_core_info("mastersystem", "mastersystem")
        )

        assert result["start"]["state"] == "cannot_start"
        assert result["default_elsewhere"] is False


class TestAPickWhileItsSourceIsSwitchedOff:
    """A RetroDECK pick waits while RetroDECK is off and applies again once it is on (#2222 D5)."""

    def test_the_platform_page_shows_the_default_and_keeps_the_pick(self, event_loop):
        options = _declared(
            libretro_option("picodrive_libretro", "PicoDrive"), libretro_option("gearsystem_libretro", "Gearsystem")
        )
        core_info = FakeCoreInfoProvider(options=options, source={"kind": "emudeck", "starts_games": False})
        settings = {"platform_cores": {"mastersystem": {"label": "Gearsystem", "source": "retrodeck"}}}
        service = _service_over(core_info, settings=settings, event_loop=event_loop)

        off = event_loop.run_until_complete(service.get_system_core_info("mastersystem", "mastersystem"))

        assert off["active_core_label"] == "PicoDrive"
        assert off["vanished_pick"] is None
        assert settings["platform_cores"]["mastersystem"] == {"label": "Gearsystem", "source": "retrodeck"}

        core_info.source = {"kind": "retrodeck", "starts_games": True}
        on = event_loop.run_until_complete(service.get_system_core_info("mastersystem", "mastersystem"))

        assert on["active_core_label"] == "Gearsystem"

    def test_the_game_page_names_no_platform_pick_meanwhile(self, event_loop, service, uow, settings, core_info):
        _seed_rom(uow, rom_id=42, platform_slug="snes")
        settings["platform_cores"]["snes"] = {"label": "bsnes", "source": "retrodeck"}
        core_info.source = {"kind": "emudeck", "starts_games": False}

        off = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert off["platform_core_label"] is None

        core_info.source = {"kind": "retrodeck", "starts_games": True}
        on = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert on["platform_core_label"] == "bsnes"


# ── get_emulator_launches (whether each entry starts the installed game) ──


def _emudeck() -> dict[str, Any]:
    """The answering source where RetroDECK is absent: one Tender starts no game through yet (#2221 D21)."""
    return {"kind": "emudeck", "starts_games": False}


def _resolved(*options: Any, chosen: Any | None = None) -> ResolvedEmulator:
    return ResolvedEmulator(
        choice=EmulatorChoice(option=chosen, layer=DEFAULT if chosen is not None else None, vanished=None),
        options=list(options),
    )


class TestGetEmulatorLaunches:
    """Each entry's verdict, asked with its own label, for the emulator menu (#2195 D22, D29)."""

    _RPCS3_SHORTCUT = standalone_option("RPCS3 Shortcut (Standalone)")
    _RPCS3_DIRECTORY = standalone_option("RPCS3 Directory (Standalone)")

    def test_every_entry_is_asked_with_its_own_label_about_the_installed_file(
        self, event_loop, service, core_info, active_core, uow
    ):
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(uow, rom_id=42, file_path="/roms/ps3/Game.iso", platform_slug="ps3")
        active_core.resolved[42] = _resolved(self._RPCS3_SHORTCUT, self._RPCS3_DIRECTORY)

        result = event_loop.run_until_complete(service.get_emulator_launches(42))

        assert [entry["entry"] for entry in result] == ["RPCS3 Shortcut (Standalone)", "RPCS3 Directory (Standalone)"]
        assert core_info.launchability_calls == [
            ("ps3", "/roms/ps3/Game.iso", "RPCS3 Shortcut (Standalone)"),
            ("ps3", "/roms/ps3/Game.iso", "RPCS3 Directory (Standalone)"),
        ]

    def test_a_game_kept_as_a_folder_is_asked_about_its_folder(self, event_loop, service, core_info, active_core, uow):
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(
            uow,
            rom_id=42,
            file_path="/roms/ps3/Game.ps3/PS3_GAME/USRDIR/EBOOT.BIN",
            rom_dir="/roms/ps3/Game.ps3",
            platform_slug="ps3",
        )
        active_core.resolved[42] = _resolved(self._RPCS3_DIRECTORY)

        event_loop.run_until_complete(service.get_emulator_launches(42))

        assert core_info.launchability_calls == [("ps3", "/roms/ps3/Game.ps3", "RPCS3 Directory (Standalone)")]

    def test_a_multi_disc_game_is_asked_about_the_disc_it_starts(
        self, event_loop, service, core_info, active_core, disc_resolver, uow
    ):
        _seed_rom(uow, rom_id=42, platform_slug="psx", selected_disc="Game (Disc 2).cue")
        _seed_install(
            uow,
            rom_id=42,
            file_path="/roms/psx/Game/Game (Disc 1).cue",
            rom_dir="/roms/psx/Game",
            platform_slug="psx",
        )
        disc_resolver.set_discs(
            "/roms/psx/Game",
            [
                Disc(filename="Game (Disc 1).cue", path="/roms/psx/Game/Game (Disc 1).cue", label="Disc 1", index=1),
                Disc(filename="Game (Disc 2).cue", path="/roms/psx/Game/Game (Disc 2).cue", label="Disc 2", index=2),
            ],
        )
        active_core.resolved[42] = _resolved(libretro_option("swanstation_libretro", "SwanStation"))

        event_loop.run_until_complete(service.get_emulator_launches(42))

        assert core_info.launchability_calls == [("psx", "/roms/psx/Game/Game (Disc 2).cue", "SwanStation")]

    def test_every_entry_s_answer_names_the_platform_as_the_game_page_does(self, event_loop, service, active_core, uow):
        uow.kv_config.set("platform_names", json.dumps({"ps3": "PlayStation 3"}))
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(uow, rom_id=42, file_path="/roms/ps3/Game.iso", platform_slug="ps3")
        active_core.resolved[42] = _resolved(self._RPCS3_SHORTCUT, self._RPCS3_DIRECTORY)

        result = event_loop.run_until_complete(service.get_emulator_launches(42))

        assert [entry["platform_name"] for entry in result] == ["PlayStation 3", "PlayStation 3"]

    def test_a_game_that_is_not_installed_asks_nothing(self, event_loop, service, core_info, active_core, uow):
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        active_core.resolved[42] = _resolved(self._RPCS3_DIRECTORY)

        assert event_loop.run_until_complete(service.get_emulator_launches(42)) == []
        assert core_info.launchability_calls == []

    def test_a_game_with_no_system_to_ask_asks_nothing(self, event_loop, service, core_info, active_core, uow):
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(uow, rom_id=42, file_path="/roms/ps3/Game.iso", platform_slug="ps3")
        active_core.resolved[42] = None

        assert event_loop.run_until_complete(service.get_emulator_launches(42)) == []
        assert core_info.launchability_calls == []


class TestTheChosenEntrysLaunch:
    """The page's core read carries whether the game starts with the entry it starts with (#2195 D2, D26)."""

    _SHORTCUT = standalone_option("RPCS3 Shortcut (Standalone)")
    _DIRECTORY = standalone_option("RPCS3 Directory (Standalone)")

    def test_an_installed_game_is_asked_about_with_its_chosen_entry_alone(
        self, event_loop, service, core_info, active_core, uow
    ):
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(
            uow,
            rom_id=42,
            file_path="/roms/ps3/Puppeteer/Puppeteer.pkg",
            rom_dir="/roms/ps3/Puppeteer",
            platform_slug="ps3",
        )
        active_core.resolved[42] = _resolved(self._SHORTCUT, self._DIRECTORY, chosen=self._DIRECTORY)
        core_info.launch_verdicts["RPCS3 Directory (Standalone)"] = Launchability(
            state=NEEDS_INSTALLING,
            entry="RPCS3 Directory (Standalone)",
            system="ps3",
            file="Puppeteer.pkg",
            extension=".pkg",
            install_in=("RPCS3 Game Serial (Standalone)",),
        )

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert core_info.launchability_calls == [
            ("ps3", "/roms/ps3/Puppeteer/Puppeteer.pkg", "RPCS3 Directory (Standalone)")
        ]
        assert result["launch"]["state"] == NEEDS_INSTALLING
        assert result["launch"]["install_in"] == ["RPCS3 Game Serial (Standalone)"]

    def test_a_folder_game_whose_entry_takes_no_folder_is_refused_by_the_verdict_not_by_its_start(
        self, event_loop, service, core_info, active_core, uow
    ):
        # Whether the entry takes a folder is the resolver's (#2195 D21 changed).
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(
            uow,
            rom_id=42,
            file_path="/roms/ps3/Game.ps3/PS3_GAME/USRDIR/EBOOT.BIN",
            rom_dir="/roms/ps3/Game.ps3",
            platform_slug="ps3",
        )
        active_core.resolved[42] = _resolved(self._SHORTCUT, self._DIRECTORY, chosen=self._SHORTCUT)
        core_info.launch_verdicts["RPCS3 Shortcut (Standalone)"] = Launchability(
            state=NOT_THIS_EMULATOR,
            entry="RPCS3 Shortcut (Standalone)",
            system="ps3",
            file="Game.ps3",
            extension=".ps3",
            other_entries=("RPCS3 Directory (Standalone)",),
            reason=TAKES_NO_FOLDER,
        )

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["start"] == {"state": "starts", "reason": None, "entry": "RPCS3 Shortcut (Standalone)"}
        assert (result["launch"]["state"], result["launch"]["reason"], result["launch"]["other_entries"]) == (
            NOT_THIS_EMULATOR,
            TAKES_NO_FOLDER,
            ["RPCS3 Directory (Standalone)"],
        )

    def test_the_answer_names_the_platform_as_the_game_page_does(self, event_loop, service, active_core, uow):
        # The sentences name the platform, never the system short name (#2195 D36).
        uow.kv_config.set("platform_names", json.dumps({"dc": "Sega Dreamcast"}))
        _seed_rom(uow, rom_id=42, platform_slug="dc")
        _seed_install(uow, rom_id=42, file_path="/roms/dreamcast/Game/track03.bin", platform_slug="dc")
        active_core.resolved[42] = _resolved(self._DIRECTORY, chosen=self._DIRECTORY)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["launch"]["platform_name"] == "Sega Dreamcast"

    def test_a_platform_the_cache_does_not_name_is_named_by_its_slug(self, event_loop, service, active_core, uow):
        _seed_rom(uow, rom_id=42, platform_slug="dc")
        _seed_install(uow, rom_id=42, file_path="/roms/dreamcast/Game/track03.bin", platform_slug="dc")
        active_core.resolved[42] = _resolved(self._DIRECTORY, chosen=self._DIRECTORY)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["launch"]["platform_name"] == "dc"

    def test_a_game_with_no_chosen_entry_asks_nothing(self, event_loop, service, core_info, active_core, uow):
        # No default and no pick: the start is refused for that already (#2195 D7).
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(uow, rom_id=42, file_path="/roms/ps3/Game.iso", platform_slug="ps3")
        active_core.resolved[42] = _resolved(self._SHORTCUT, self._DIRECTORY)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["launch"] is None
        assert core_info.launchability_calls == []

    def test_a_game_that_is_not_installed_asks_nothing(self, event_loop, service, core_info, active_core, uow):
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        active_core.resolved[42] = _resolved(self._DIRECTORY, chosen=self._DIRECTORY)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["launch"] is None
        assert core_info.launchability_calls == []


class TestWhatTheResolversCommandSaysOfTheStart:
    """The page's ``start`` and the menu's ``start_verdict`` carry the resolver's launch command (#2221 D2, D20)."""

    _SHORTCUT = standalone_option("RPCS3 Shortcut (Standalone)")
    _DIRECTORY = standalone_option("RPCS3 Directory (Standalone)")
    _MISSING = standalone_option("RPCS3 Missing (Standalone)", not_installed=True)

    @staticmethod
    def _installed(uow: FakeUnitOfWork) -> None:
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        _seed_install(uow, rom_id=42, file_path="/roms/ps3/Game.iso", platform_slug="ps3")

    def test_an_entry_the_resolver_gives_no_complete_command_for_cannot_start_with_its_reason(
        self, event_loop, service, active_core, start_commands, uow
    ):
        self._installed(uow)
        active_core.resolved[42] = _resolved(self._SHORTCUT, chosen=self._SHORTCUT)
        start_commands.answers["RPCS3 Shortcut (Standalone)"] = StartRefused(DESKTOP_FILE)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["start"] == {
            "state": "cannot_start",
            "reason": DESKTOP_FILE,
            "entry": "RPCS3 Shortcut (Standalone)",
        }
        assert start_commands.asked == [("ps3", "/roms/ps3/Game.iso", "RPCS3 Shortcut (Standalone)")]

    def test_a_complete_command_says_what_it_says_beside_the_start(
        self, event_loop, service, active_core, start_commands, uow
    ):
        self._installed(uow)
        active_core.resolved[42] = _resolved(self._DIRECTORY, chosen=self._DIRECTORY)
        start_commands.answers["RPCS3 Directory (Standalone)"] = StartCommand(
            argv=("rpcs3",), folder=None, notes=(StartNote(PLACEHOLDER_UNFILLED, "%RPCS3_GAMEID%"),)
        )

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["start"]["state"] == "starts"
        assert result["start"]["remarks"] == [{"reason": PLACEHOLDER_UNFILLED, "placeholder": "%RPCS3_GAMEID%"}]

    def test_a_game_that_is_not_installed_asks_no_command(self, event_loop, service, active_core, start_commands, uow):
        _seed_rom(uow, rom_id=42, platform_slug="ps3")
        active_core.resolved[42] = _resolved(self._SHORTCUT, chosen=self._SHORTCUT)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["start"]["state"] == "starts"
        assert start_commands.asked == []

    def test_an_entry_not_installed_says_so_and_asks_no_command(
        self, event_loop, service, active_core, start_commands, uow
    ):
        self._installed(uow)
        active_core.resolved[42] = _resolved(self._MISSING, chosen=self._MISSING)

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert (result["start"]["state"], result["start"]["reason"]) == ("cannot_start", "not_installed")
        assert start_commands.asked == []

    def test_the_menu_carries_each_entrys_command_verdict_asked_with_its_own_label(
        self, event_loop, service, active_core, start_commands, uow
    ):
        self._installed(uow)
        active_core.resolved[42] = _resolved(self._SHORTCUT, self._DIRECTORY, self._MISSING)
        start_commands.answers["RPCS3 Shortcut (Standalone)"] = StartRefused(DESKTOP_FILE)

        result = event_loop.run_until_complete(service.get_emulator_launches(42))

        assert [entry["start_verdict"] for entry in result] == [
            {"starts": False, "reason": DESKTOP_FILE, "remarks": []},
            {"starts": True, "reason": None, "remarks": []},
            None,
        ]
        assert start_commands.asked == [
            ("ps3", "/roms/ps3/Game.iso", "RPCS3 Shortcut (Standalone)"),
            ("ps3", "/roms/ps3/Game.iso", "RPCS3 Directory (Standalone)"),
        ]

    def test_a_game_the_answering_source_starts_no_game_through_cannot_start_and_asks_nothing(
        self, event_loop, service, active_core, start_commands, uow
    ):
        """EmuDeck answers where RetroDECK is absent, and Tender starts no game through it yet (#2221 D21)."""
        self._installed(uow)
        resolved = _resolved(self._SHORTCUT, chosen=self._SHORTCUT)
        active_core.resolved[42] = ResolvedEmulator(choice=resolved.choice, options=resolved.options, source=_emudeck())

        result = event_loop.run_until_complete(service.get_platform_core_info(42))

        assert result["start"] == {
            "state": "cannot_start",
            "reason": SOURCE_STARTS_NO_GAMES,
            "entry": "RPCS3 Shortcut (Standalone)",
            "source": "emudeck",
        }
        assert start_commands.asked == []

    def test_the_menu_carries_the_sources_refusal_for_every_entry_and_asks_nothing(
        self, event_loop, service, active_core, start_commands, uow
    ):
        self._installed(uow)
        active_core.resolved[42] = ResolvedEmulator(
            choice=_resolved().choice, options=[self._SHORTCUT, self._DIRECTORY, self._MISSING], source=_emudeck()
        )

        result = event_loop.run_until_complete(service.get_emulator_launches(42))

        refusal = {"starts": False, "reason": SOURCE_STARTS_NO_GAMES, "remarks": [], "source": "emudeck"}
        assert [entry["start_verdict"] for entry in result] == [refusal, refusal, None]
        assert start_commands.asked == []

    def test_a_platforms_own_read_asks_no_command(self, event_loop, service, start_commands):
        """The platform page says nothing of the command (#2221 D20)."""
        result = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))

        assert result["start"]["state"] == "starts"
        assert start_commands.asked == []


_PICK = {"label": "bsnes", "source": "retrodeck"}


class TestAPlatformsPickIsKeptByItsFolder:
    """A per-platform pick is stored under the platform's ``fs_slug`` (#2452 D7, D11, D15)."""

    def test_a_pick_on_one_folder_leaves_the_other_of_its_slug(self, event_loop, service, settings):
        """Done when: a pick on one of two platforms sharing a slug leaves the other at its default."""
        event_loop.run_until_complete(service.set_system_core("snes", "snes-hacks", "bsnes"))

        hacks = event_loop.run_until_complete(service.get_system_core_info("snes", "snes-hacks"))
        snes = event_loop.run_until_complete(service.get_system_core_info("snes", "snes"))

        assert settings["platform_cores"] == {"snes-hacks": {"label": "bsnes", "source": "retrodeck"}}
        assert hacks["active_core_label"] == "bsnes"
        assert snes["active_core_label"] == "Snes9x"

    def test_a_game_reads_the_pick_of_its_own_folder(self, event_loop, service, settings, uow):
        settings["platform_cores"]["snes-hacks"] = {"label": "bsnes", "source": "retrodeck"}
        settings["platform_cores"]["snes"] = {"label": "Snes9x", "source": "retrodeck"}
        uow.roms.save(
            Rom(
                rom_id=7,
                platform_slug="snes",
                platform_fs_slug="snes-hacks",
                name="rom-7",
                fs_name="rom-7.sfc",
                shortcut_app_id=None,
                last_synced_at="2026-01-01T00:00:00+00:00",
            )
        )

        result = event_loop.run_until_complete(service.get_platform_core_info(7))

        assert result["platform_core_label"] == "bsnes"

    @pytest.mark.parametrize(
        ("folder", "picks", "kept", "label"),
        [
            # D15: a game with no fs_slug recorded reads the pick still under its slug ...
            (None, {"snes": _PICK}, (), "bsnes"),
            # ... once the refiling moved that key, the pick of the one platform kept for the slug ...
            (None, {"snes-hacks": _PICK}, ("snes-hacks",), "bsnes"),
            # ... and the default where several share the slug, or none is kept.
            (None, {"snes-hacks": _PICK}, ("snes", "snes-hacks"), None),
            (None, {"snes-hacks": _PICK}, (), None),
            # A game with its fs_slug reads that platform's pick and nothing else.
            ("snes-hacks", {"snes": _PICK}, ("snes",), None),
        ],
    )
    def test_a_game_reads_its_folder_s_pick_else_without_one_its_slug_s(
        self, event_loop, service, settings, uow, platform_systems, folder, picks, kept, label
    ):
        settings["platform_cores"].update(picks)
        platform_systems.kept = {"snes": frozenset(kept)}
        uow.roms.save(
            Rom(
                rom_id=8,
                platform_slug="snes",
                platform_fs_slug=folder,
                name="rom-8",
                fs_name="rom-8.sfc",
                shortcut_app_id=None,
                last_synced_at="2026-01-01T00:00:00+00:00",
            )
        )

        result = event_loop.run_until_complete(service.get_platform_core_info(8))

        assert result["platform_core_label"] == label

    def test_a_pick_waits_for_a_writer_that_holds_the_picks_lock(self, service, settings):
        """The refiling and this write change the same picks from threads of their own."""
        with cast("PlatformPicks", service._platform_picks)._lock:
            worker = threading.Thread(target=service._set_system_core_io, args=("snes", "snes-hacks", "bsnes"))
            worker.start()
            worker.join(timeout=0.2)
            assert worker.is_alive()
            assert "snes-hacks" not in settings["platform_cores"]
        worker.join(timeout=5)

        assert settings["platform_cores"]["snes-hacks"]["label"] == "bsnes"

    def test_any_failure_to_save_gives_the_picks_back(self, service, settings, settings_persister, monkeypatch):
        settings["platform_cores"]["snes"] = {"label": "Snes9x", "source": "retrodeck"}

        def _fails() -> None:
            raise RuntimeError("dictionary changed size during iteration")

        monkeypatch.setattr(settings_persister, "save_settings", _fails)
        with pytest.raises(RuntimeError):
            service._set_system_core_io("snes", "snes", "bsnes")

        assert settings["platform_cores"] == {"snes": {"label": "Snes9x", "source": "retrodeck"}}

    def test_a_failed_save_is_logged_with_the_platform_it_was_for(self, service, settings_persister, caplog):
        def refuse() -> None:
            raise OSError("read-only file system")

        settings_persister.save_settings = refuse
        with caplog.at_level(logging.ERROR), pytest.raises(Refused):
            service._set_system_core_io("snes", "snes-hacks", "bsnes")

        assert "Failed to set the system core of snes-hacks: read-only file system" in caplog.text

    def test_a_save_of_the_whole_settings_iterating_them_meanwhile_does_not_fail(self, service, settings):
        """A save on another thread may be walking the settings while a pick is stored."""
        settings["platform_cores"]["snes"] = {"label": "Snes9x", "source": "retrodeck"}
        settings["other"] = True
        walking_settings = iter(settings)
        walking_picks = iter(settings["platform_cores"])
        next(walking_settings)
        next(walking_picks)

        service._set_system_core_io("snes", "snes-hacks", "bsnes")

        assert list(walking_settings) == ["other"]
        assert list(walking_picks) == []
        assert settings["platform_cores"]["snes-hacks"]["label"] == "bsnes"

    @pytest.mark.parametrize(("slug", "folder"), [("", "snes"), ("snes", ""), (None, "snes"), ("snes", None)])
    def test_a_platform_asked_without_a_slug_or_a_folder_is_refused(self, event_loop, service, settings, slug, folder):
        for call in (
            lambda: service.get_system_core_info(slug, folder),
            lambda: service.set_system_core(slug, folder, "bsnes"),
        ):
            with pytest.raises(Refused) as refused:
                event_loop.run_until_complete(call())
            assert refused.value.reason == "invalid_value"
        assert settings["platform_cores"] == {}
