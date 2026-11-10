"""Tests for ActiveCoreResolver — the single per-ROM active-core read seam.

Covers the three-layer precedence: a resolvable per-game override wins; a
per-platform ``settings.json`` core beats the live es_systems default; the
per-game override beats the per-platform core; a NULL override with no
per-platform core delegates to the es_systems default; a stale per-game or
per-platform label degrades to the next layer without raising; a
per-game/per-platform label may name a **standalone** emulator; and the retired
ES-DE gamelist is never consulted.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from fakes.fake_core_info_provider import (
    FakeCoreInfoProvider,
    libretro_option,
    standalone_option,
)
from fakes.fake_platform_systems import RETRODECK_SOURCE, FakePlatformSystems
from fakes.fake_unit_of_work import FakeUnitOfWork, FakeUnitOfWorkFactory

from domain.emulator_choice import DEFAULT, GAME_PICK
from domain.emulator_pick import StoredPick
from domain.platform_system import NO_SYSTEM, PLATFORM_IDS_KEY, KeptPlatform, PlatformSystem, encode_platform_ids
from domain.rom import Rom
from domain.rom_install import RomInstall
from services.active_core_resolver import ActiveCoreResolver, ActiveCoreResolverConfig

if TYPE_CHECKING:
    import pytest


class FakePlatformCoreReader:
    """In-memory ``PlatformCoreReader`` mapping a platform's ``fs_slug`` to its core label.

    Returns the configured label as RetroDECK's pick, or ``None`` when absent. Records
    each queried ``fs_slug`` so a test can assert the per-platform layer was consulted
    (or skipped when a per-game override already resolved).
    """

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping if mapping is not None else {}
        self.calls: list[str | None] = []

    def get_platform_core(self, platform_folder: str | None) -> StoredPick | None:
        self.calls.append(platform_folder)
        label = self.mapping.get(platform_folder) if platform_folder is not None else None
        return StoredPick(label=label, source="retrodeck") if label is not None else None


def _seed_rom(
    uow: FakeUnitOfWork,
    *,
    rom_id: int,
    platform_slug: str,
    emulator_override: str | None = None,
) -> None:
    """Seed one ``Rom`` (optionally pinned to ``emulator_override``, RetroDECK's pick) into the UoW."""
    uow.roms.save(
        Rom(
            rom_id=rom_id,
            platform_slug=platform_slug,
            platform_fs_slug=platform_slug,
            name=f"rom-{rom_id}",
            fs_name=f"rom-{rom_id}.gba",
            shortcut_app_id=rom_id,
            last_synced_at="2026-01-01T00:00:00+00:00",
            emulator_override=emulator_override,
            emulator_override_source="retrodeck" if emulator_override is not None else None,
        )
    )


def _seed_install(
    uow: FakeUnitOfWork,
    *,
    rom_id: int,
    file_path: str,
    rom_dir: str | None,
    platform_slug: str = "ps3",
    system: str = "ps3",
) -> None:
    """Seed one ``RomInstall`` into the UoW (folder-backed unless *rom_dir* is None)."""
    uow.rom_installs.save(
        RomInstall.mark_installed(
            rom_id=rom_id,
            file_path=file_path,
            rom_dir=rom_dir,
            platform_slug=platform_slug,
            system=system,
            installed_at="2026-01-01T00:00:00",
        )
    )


def _make_resolver(
    *,
    uow: FakeUnitOfWork,
    core_info: FakeCoreInfoProvider,
    platform_systems: FakePlatformSystems | None = None,
    platform_core_reader: FakePlatformCoreReader | None = None,
) -> tuple[ActiveCoreResolver, FakePlatformSystems]:
    resolver_fn = platform_systems if platform_systems is not None else FakePlatformSystems()
    platform_reader = platform_core_reader if platform_core_reader is not None else FakePlatformCoreReader()
    resolver = ActiveCoreResolver(
        config=ActiveCoreResolverConfig(
            uow_factory=FakeUnitOfWorkFactory(uow=uow),
            core_info=core_info,
            platform_core_reader=platform_reader,
            platform_systems=resolver_fn,
            logger=logging.getLogger("test"),
        ),
    )
    return resolver, resolver_fn


# --- override set + resolvable → returns the override's (core_so, label) -------


def test_resolvable_override_returns_pinned_core() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=42, platform_slug="gba", emulator_override="mGBA")
    core_info = FakeCoreInfoProvider(
        available_cores=[
            {"core_so": "vba_next_libretro", "label": "VBA Next", "is_default": True},
            {"core_so": "mgba_libretro", "label": "mGBA", "is_default": False},
        ],
        # System default differs from the pin so the test proves the override won.
        active_core=("vba_next_libretro", "VBA Next"),
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    assert resolver.active_core_for_rom(42) == ("mgba_libretro", "mGBA")
    # System-layer get_active_core must NOT be consulted when the override resolves.
    assert core_info.active_core_calls == []


def test_resolvable_override_normalizes_platform_slug_to_system() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=7, platform_slug="gba", emulator_override="mGBA")
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "mgba_libretro", "label": "mGBA", "is_default": True}],
    )
    resolver, platform_systems = _make_resolver(
        uow=uow,
        core_info=core_info,
        platform_systems=FakePlatformSystems(mapping={"gba": "gba"}),
    )

    resolver.active_core_for_rom(7)
    # The available-cores read seam must receive the resolved system, not the raw slug.
    assert platform_systems.calls == [("gba", None)]
    assert core_info.emulator_options_calls == ["gba"]


# --- override NULL → returns the system-default (delegation works) -------------


def test_null_override_delegates_to_system_default() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=1, platform_slug="snes", emulator_override=None)
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "snes9x_libretro", "label": "Snes9x", "is_default": True}],
        active_core=("snes9x_libretro", "Snes9x"),
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    assert resolver.active_core_for_rom(1) == ("snes9x_libretro", "Snes9x")
    # Delegation path: the system-layer get_active_core was consulted with the system only.


def test_null_override_passes_through_system_none() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=5, platform_slug="unknown", emulator_override=None)
    core_info = FakeCoreInfoProvider(active_core=(None, None))
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    # An unconfigured system yields (None, None); that passes through unchanged.
    assert resolver.active_core_for_rom(5) == (None, None)


# --- per-platform layer beats es_systems default; per-game beats per-platform ---


def test_per_platform_core_beats_es_systems_default() -> None:
    """An un-pinned ROM whose platform carries a per-platform core gets that core,
    not the es_systems default — the layer-2 selection wins over the system layer."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=20, platform_slug="snes", emulator_override=None)
    core_info = FakeCoreInfoProvider(
        available_cores=[
            {"core_so": "snes9x_libretro", "label": "Snes9x", "is_default": True},
            {"core_so": "bsnes_libretro", "label": "bsnes", "is_default": False},
        ],
        # es_systems default is Snes9x — the per-platform core must override it.
        active_core=("snes9x_libretro", "Snes9x"),
    )
    platform_reader = FakePlatformCoreReader(mapping={"snes": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    assert resolver.active_core_for_rom(20) == ("bsnes_libretro", "bsnes")
    # The es_systems default layer was never consulted — the per-platform core resolved.
    assert core_info.active_core_calls == []
    assert platform_reader.calls == ["snes"]


def test_per_game_override_beats_per_platform_core() -> None:
    """A pinned ROM keeps its per-game core even when its platform has a per-platform
    selection — layer-1 (per-game) wins over layer-2 (per-platform)."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=21, platform_slug="snes", emulator_override="Snes9x")
    core_info = FakeCoreInfoProvider(
        available_cores=[
            {"core_so": "snes9x_libretro", "label": "Snes9x", "is_default": True},
            {"core_so": "bsnes_libretro", "label": "bsnes", "is_default": False},
        ],
        active_core=("bsnes_libretro", "bsnes"),
    )
    platform_reader = FakePlatformCoreReader(mapping={"snes": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    assert resolver.active_core_for_rom(21) == ("snes9x_libretro", "Snes9x")
    # Per-game override resolved first — the per-platform layer is never consulted.
    assert platform_reader.calls == []
    assert core_info.active_core_calls == []


# --- override set but STALE → degrades to system default (no raise, no bogus so) ---


def test_stale_override_degrades_to_system_default() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=99, platform_slug="gba", emulator_override="Removed Core")
    # The options no longer carry "Removed Core", so the pick gives way.
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "mgba_libretro", "label": "mGBA", "is_default": True}],
        active_core=("mgba_libretro", "mGBA"),
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    result = resolver.active_core_for_rom(99)

    # Degrades to the system default — never a bogus "None.so", never raises.
    assert result == ("mgba_libretro", "mGBA")


def test_stale_override_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=99, platform_slug="gba", emulator_override="Removed Core")
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "mgba_libretro", "label": "mGBA", "is_default": True}],
        active_core=("mgba_libretro", "mGBA"),
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    with caplog.at_level(logging.WARNING, logger="test"):
        resolver.active_core_for_rom(99)

    assert any("Removed Core" in r.message and "degrading" in r.message for r in caplog.records)


# --- stale per-platform core → degrades to es_systems default (no raise) -------


def test_stale_per_platform_core_degrades_to_system_default() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=30, platform_slug="gba", emulator_override=None)
    # available_cores no longer carries the per-platform label → degrades.
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "mgba_libretro", "label": "mGBA", "is_default": True}],
        active_core=("mgba_libretro", "mGBA"),
    )
    platform_reader = FakePlatformCoreReader(mapping={"gba": "Removed Core"})
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    result = resolver.active_core_for_rom(30)

    # Degrades to the es_systems default — never a bogus "None.so", never raises.
    assert result == ("mgba_libretro", "mGBA")
    assert platform_reader.calls == ["gba"]


def test_stale_per_platform_core_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=30, platform_slug="gba", emulator_override=None)
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "mgba_libretro", "label": "mGBA", "is_default": True}],
        active_core=("mgba_libretro", "mGBA"),
    )
    platform_reader = FakePlatformCoreReader(mapping={"gba": "Removed Core"})
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    with caplog.at_level(logging.WARNING, logger="test"):
        resolver.active_core_for_rom(30)

    assert any("Removed Core" in r.message and "per-platform" in r.message for r in caplog.records)


# --- no per-platform core → falls through to es_systems default ----------------


def test_no_per_platform_core_falls_through_to_system_default() -> None:
    """A NULL override + an empty per-platform map delegates straight to the
    es_systems default — the per-platform layer was consulted and found nothing."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=40, platform_slug="snes", emulator_override=None)
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "snes9x_libretro", "label": "Snes9x", "is_default": True}],
        active_core=("snes9x_libretro", "Snes9x"),
    )
    platform_reader = FakePlatformCoreReader()  # empty map → None for every slug
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    assert resolver.active_core_for_rom(40) == ("snes9x_libretro", "Snes9x")
    assert platform_reader.calls == ["snes"]


# --- bad path: unknown rom_id → (None, None), no raise -------------------------


def test_missing_rom_resolves_to_none_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    uow = FakeUnitOfWork()
    core_info = FakeCoreInfoProvider(active_core=("mgba_libretro", "mGBA"))
    platform_reader = FakePlatformCoreReader(mapping={"gba": "mGBA"})
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    with caplog.at_level(logging.WARNING, logger="test"):
        result = resolver.active_core_for_rom(404)

    assert result == (None, None)
    # No system read happens for a ROM that does not exist — and the per-platform
    # layer (the retired-gamelist replacement) is never consulted for a missing ROM.
    assert core_info.active_core_calls == []
    assert platform_reader.calls == []
    assert any("404" in r.message for r in caplog.records)


# --- active_emulator_for_rom: the entry in effect, standalone or core (#129) -----


def test_standalone_system_default_resolves_to_standalone_emulator() -> None:
    """An un-pinned ROM on a standalone-emulator platform (PS3) resolves to the
    standalone entry, not a libretro core."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=50, platform_slug="ps3", emulator_override=None)
    core_info = FakeCoreInfoProvider(options=[standalone_option("RPCS3 Directory (Standalone)")])
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    emulator = resolver.active_emulator_for_rom(50)
    assert emulator is not None
    assert (emulator.label, emulator.core_so) == ("RPCS3 Directory (Standalone)", None)
    # A standalone emulator has no libretro core — the .so-space projection is
    # (None, label) so read-path consumers degrade exactly as for (None, None).
    assert resolver.active_core_for_rom(50) == (None, "RPCS3 Directory (Standalone)")


def test_per_game_pin_beats_standalone_system_default() -> None:
    """A per-game libretro pin wins over a platform's standalone default — the
    seam answers the libretro entry."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=51, platform_slug="ps2", emulator_override="LRPS2")
    core_info = FakeCoreInfoProvider(
        options=[standalone_option("PCSX2 (Standalone)"), libretro_option("pcsx2_libretro", "LRPS2")],
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    emulator = resolver.active_emulator_for_rom(51)
    assert emulator is not None
    assert (emulator.label, emulator.core_so) == ("LRPS2", "pcsx2_libretro")


def test_per_platform_core_beats_standalone_system_default() -> None:
    """A per-platform libretro core wins over the standalone system default."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=52, platform_slug="ps2", emulator_override=None)
    core_info = FakeCoreInfoProvider(
        options=[standalone_option("PCSX2 (Standalone)"), libretro_option("pcsx2_libretro", "LRPS2")],
    )
    platform_reader = FakePlatformCoreReader(mapping={"ps2": "LRPS2"})
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    emulator = resolver.active_emulator_for_rom(52)
    assert emulator is not None
    assert (emulator.label, emulator.core_so) == ("LRPS2", "pcsx2_libretro")


def test_resolvable_pin_returns_libretro_entry() -> None:
    """A resolvable pin answers its libretro entry."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=53, platform_slug="gba", emulator_override="mGBA")
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "mgba_libretro", "label": "mGBA", "is_default": True}],
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    emulator = resolver.active_emulator_for_rom(53)
    assert emulator is not None
    assert (emulator.label, emulator.core_so) == ("mGBA", "mgba_libretro")


def test_unresolvable_platform_returns_none() -> None:
    """A platform with no libretro default and no standalone pref resolves to None
    — no entry is in effect, and a start is refused."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=54, platform_slug="unknown", emulator_override=None)
    core_info = FakeCoreInfoProvider(active_core=(None, None))
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    assert resolver.active_emulator_for_rom(54) is None


# --- per-game / per-platform pin to a STANDALONE emulator label (#1210) --------

_RPCS3_LABEL = "RPCS3 Directory (Standalone)"


def test_per_game_pin_to_standalone_label_resolves_standalone() -> None:
    """A per-game pin naming a STANDALONE emulator resolves to it."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=60, platform_slug="ps3", emulator_override=_RPCS3_LABEL)
    core_info = FakeCoreInfoProvider(options=[standalone_option(_RPCS3_LABEL)])
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    emulator = resolver.active_emulator_for_rom(60)
    assert emulator is not None
    assert emulator.label == _RPCS3_LABEL
    # The .so-space projection is (None, label) — read-path consumers degrade.
    assert resolver.active_core_for_rom(60) == (None, _RPCS3_LABEL)


def test_per_platform_standalone_label_resolves_standalone() -> None:
    """A per-platform pin naming a STANDALONE emulator resolves to it."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=61, platform_slug="ps3", emulator_override=None)
    core_info = FakeCoreInfoProvider(options=[standalone_option(_RPCS3_LABEL)])
    platform_reader = FakePlatformCoreReader(mapping={"ps3": "RPCS3 Directory (Standalone)"})
    resolver, _ = _make_resolver(uow=uow, core_info=core_info, platform_core_reader=platform_reader)

    emulator = resolver.active_emulator_for_rom(61)
    assert emulator is not None
    assert emulator.label == _RPCS3_LABEL


def test_a_standalone_pin_stands_whatever_starting_with_it_comes_to(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A per-game pin to a label that still exists stands whatever its state (#2195 D23).

    Whether the resolver gives a complete command for it is asked at the start
    (#2221 D2), and nothing is passed over.
    """
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=62, platform_slug="ps3", emulator_override="RPCS3 Shortcut (Standalone)")
    core_info = FakeCoreInfoProvider(
        options=[standalone_option(_RPCS3_LABEL), standalone_option("RPCS3 Shortcut (Standalone)")],
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    with caplog.at_level(logging.WARNING, logger="test"):
        result = resolver.active_emulator_for_rom(62)

    assert result is not None
    assert result.label == "RPCS3 Shortcut (Standalone)"
    assert not caplog.records


# --- the platform's system -------------------------------------------------------


def test_a_platform_with_no_system_falls_back_to_the_plain_launch() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=8, platform_slug="vic-20", emulator_override="VICE")
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "vice_libretro", "label": "VICE", "is_default": True}]
    )
    platform = PlatformSystem(NO_SYSTEM, "vic-20", "VIC-20", source=RETRODECK_SOURCE)
    resolver, _ = _make_resolver(
        uow=uow, core_info=core_info, platform_systems=FakePlatformSystems(answers={"vic-20": platform})
    )

    assert resolver.active_emulator_for_rom(8) is None
    assert core_info.emulator_options_calls == []


def test_an_installed_game_launches_with_its_install_records_system() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=9, platform_slug="dc")
    _seed_install(uow, rom_id=9, file_path="/roms/naomi/a.zip", rom_dir=None, platform_slug="dc", system="naomi")
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "flycast_libretro", "label": "Flycast", "is_default": True}]
    )
    resolver, _ = _make_resolver(
        uow=uow, core_info=core_info, platform_systems=FakePlatformSystems(mapping={"dc": "dreamcast"})
    )

    resolver.active_emulator_for_rom(9)

    assert core_info.emulator_options_calls == ["naomi"]


# --- the choice and what stands in its way (#2195 D7, D21, D23) -----------------

_SHORTCUT = standalone_option("RPCS3 Shortcut (Standalone)")


def test_a_shipped_default_stands_whatever_starting_with_it_comes_to() -> None:
    """The default stands, and names its entry to every read of the emulator a game starts with (#2195 D7)."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=85, platform_slug="ps3")
    core_info = FakeCoreInfoProvider(options=[_SHORTCUT, standalone_option(_RPCS3_LABEL)])
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    resolved = resolver.resolve_rom(85)

    assert resolved is not None
    assert resolved.choice.option is not None
    assert resolved.choice.option.label == "RPCS3 Shortcut (Standalone)"
    assert resolver.active_core_for_rom(85) == (None, "RPCS3 Shortcut (Standalone)")


def test_a_platform_with_no_entry_keeps_the_plain_launch() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=86, platform_slug="gba")
    resolver, _ = _make_resolver(uow=uow, core_info=FakeCoreInfoProvider(options=[]))

    assert resolver.active_emulator_for_rom(86) is None


def test_a_system_with_entries_but_no_default_established_has_no_entry_in_effect() -> None:
    """D9's choose state: no entry is in effect."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=87, platform_slug="ps2")
    unseen = dataclasses.replace(
        standalone_option("PCSX2 (Standalone)"),
        declared_index=0,
        not_established="no_find_rule",
    )
    resolver, _ = _make_resolver(uow=uow, core_info=FakeCoreInfoProvider(options=[unseen]))

    resolved = resolver.resolve_rom(87)

    assert resolved is not None
    assert resolved.choice.option is None


def test_a_vanished_pick_with_no_default_has_no_entry_in_effect() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=88, platform_slug="ps2", emulator_override="Gone (Standalone)")
    unseen = dataclasses.replace(
        standalone_option("PCSX2 (Standalone)"),
        declared_index=0,
        not_established="no_find_rule",
    )
    resolver, _ = _make_resolver(uow=uow, core_info=FakeCoreInfoProvider(options=[unseen]))

    resolved = resolver.resolve_rom(88)

    assert resolved is not None
    assert resolved.choice.vanished is not None
    assert resolved.choice.option is None


def test_a_pin_to_an_entry_not_installed_stands() -> None:
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=83, platform_slug="gba", emulator_override="Missing")
    missing = standalone_option("Missing", not_installed=True)
    core_info = FakeCoreInfoProvider(options=[libretro_option("mgba_libretro", "mGBA"), missing])
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    resolved = resolver.resolve_rom(83)

    assert resolved is not None
    assert resolved.choice.option is not None
    assert (resolved.choice.option.label, resolved.choice.layer) == ("Missing", "game")


def test_a_vanished_pin_is_named_with_its_source_beside_the_default() -> None:
    uow = FakeUnitOfWork()
    uow.roms.save(
        Rom(
            rom_id=84,
            platform_slug="gba",
            name="rom-84",
            fs_name="rom-84.gba",
            shortcut_app_id=84,
            last_synced_at="2026-01-01T00:00:00+00:00",
            emulator_override="Gone",
            emulator_override_source="retrodeck",
        )
    )
    core_info = FakeCoreInfoProvider(options=[libretro_option("mgba_libretro", "mGBA")])
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    resolved = resolver.resolve_rom(84)

    assert resolved is not None
    assert resolved.choice.vanished is not None
    assert (resolved.choice.vanished.pick.label, resolved.choice.vanished.pick.source) == ("Gone", "retrodeck")
    assert resolved.choice.option is not None
    assert resolved.choice.option.label == "mGBA"


def test_a_retrodeck_pin_while_retrodeck_is_switched_off_waits_for_it(caplog: pytest.LogCaptureFixture) -> None:
    """The default applies, the pin is kept and nothing is said; once RetroDECK is on, the pin applies (#2222 D5).

    The label is in the list that answers meanwhile, so only the pin's source keeps it from applying.
    """
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=90, platform_slug="gba", emulator_override="mGBA")
    core_info = FakeCoreInfoProvider(
        options=[
            dataclasses.replace(libretro_option("vba_next_libretro", "VBA Next"), declared_index=0),
            dataclasses.replace(libretro_option("mgba_libretro", "mGBA"), declared_index=1),
        ],
        source={"kind": "emudeck", "starts_games": False},
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    with caplog.at_level(logging.WARNING):
        off = resolver.resolve_rom(90)

    assert off is not None
    assert off.choice.option is not None
    assert (off.choice.option.label, off.choice.layer, off.choice.vanished) == ("VBA Next", DEFAULT, None)
    assert "no longer resolves" not in caplog.text
    pinned = uow.roms.get(90)
    assert pinned is not None
    assert (pinned.emulator_override, pinned.emulator_override_source) == ("mGBA", "retrodeck")

    core_info.source = {"kind": "retrodeck", "starts_games": True}
    on = resolver.resolve_rom(90)

    assert on is not None
    assert on.choice.option is not None
    assert (on.choice.option.label, on.choice.layer) == ("mGBA", GAME_PICK)


def test_the_resolution_carries_the_source_that_answered() -> None:
    """The start check reads whether the answering source starts games off the resolution (#2221 D21)."""
    uow = FakeUnitOfWork()
    _seed_rom(uow, rom_id=60, platform_slug="gba")
    core_info = FakeCoreInfoProvider(
        available_cores=[{"core_so": "mgba_libretro", "label": "mGBA", "is_default": True}],
        source={"kind": "emudeck", "starts_games": False},
    )
    resolver, _ = _make_resolver(uow=uow, core_info=core_info)

    resolved = resolver.resolve_rom(60)

    assert resolved is not None
    assert resolved.source == {"kind": "emudeck", "starts_games": False}


# --- the per-platform pick is the one of the game's own folder (#2452 D7, D15) --


def _seed_rom_on_folder(uow: FakeUnitOfWork, *, rom_id: int, folder: str | None) -> None:
    """An un-pinned ``snes`` game on the platform RomM keeps in *folder*; ``None`` is one no sync recorded it for."""
    uow.roms.save(
        Rom(
            rom_id=rom_id,
            platform_slug="snes",
            platform_fs_slug=folder,
            name=f"rom-{rom_id}",
            fs_name=f"rom-{rom_id}.sfc",
            shortcut_app_id=rom_id,
            last_synced_at="2026-01-01T00:00:00+00:00",
        )
    )


_SNES_CORES = FakeCoreInfoProvider(
    available_cores=[
        {"core_so": "snes9x_libretro", "label": "Snes9x", "is_default": True},
        {"core_so": "bsnes_libretro", "label": "bsnes", "is_default": False},
    ],
    active_core=("snes9x_libretro", "Snes9x"),
)


def test_a_game_takes_the_pick_of_its_own_folder_not_its_slug() -> None:
    uow = FakeUnitOfWork()
    _seed_rom_on_folder(uow, rom_id=70, folder="snes-hacks")
    reader = FakePlatformCoreReader(mapping={"snes": "Snes9x", "snes-hacks": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=_SNES_CORES, platform_core_reader=reader)

    assert resolver.active_core_for_rom(70) == ("bsnes_libretro", "bsnes")


def test_a_game_without_a_folder_takes_the_pick_still_under_its_slug() -> None:
    """D15 (changed): a game with no fs_slug recorded reads the pick kept under its slug."""
    uow = FakeUnitOfWork()
    _seed_rom_on_folder(uow, rom_id=71, folder=None)
    reader = FakePlatformCoreReader(mapping={"snes": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=_SNES_CORES, platform_core_reader=reader)

    assert resolver.active_core_for_rom(71) == ("bsnes_libretro", "bsnes")


def test_a_game_without_a_folder_and_no_pick_under_its_slug_takes_the_default() -> None:
    uow = FakeUnitOfWork()
    _seed_rom_on_folder(uow, rom_id=72, folder=None)
    reader = FakePlatformCoreReader(mapping={"snes-hacks": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=_SNES_CORES, platform_core_reader=reader)

    assert resolver.active_core_for_rom(72) == ("snes9x_libretro", "Snes9x")


def _keep(uow: FakeUnitOfWork, *folders: str) -> None:
    """Keep each of *folders* as a listed platform of the slug ``snes``."""
    kept = {folder: KeptPlatform(slug="snes", fs_slug=folder) for folder in folders}
    uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids(kept))


def test_a_game_without_a_folder_whose_slug_pick_moved_takes_the_one_kept_platform_s_pick() -> None:
    """D15: the refiling moved the slug's pick to the one platform kept for the slug."""
    uow = FakeUnitOfWork()
    _seed_rom_on_folder(uow, rom_id=74, folder=None)
    _keep(uow, "snes-hacks")
    reader = FakePlatformCoreReader(mapping={"snes-hacks": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=_SNES_CORES, platform_core_reader=reader)

    assert resolver.active_core_for_rom(74) == ("bsnes_libretro", "bsnes")


def test_a_game_without_a_folder_whose_slug_several_platforms_share_takes_the_default() -> None:
    uow = FakeUnitOfWork()
    _seed_rom_on_folder(uow, rom_id=75, folder=None)
    _keep(uow, "snes", "snes-hacks")
    reader = FakePlatformCoreReader(mapping={"snes-hacks": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=_SNES_CORES, platform_core_reader=reader)

    assert resolver.active_core_for_rom(75) == ("snes9x_libretro", "Snes9x")


def test_a_game_with_a_folder_never_reads_the_pick_under_its_slug_or_the_kept_platforms() -> None:
    uow = FakeUnitOfWork()
    _seed_rom_on_folder(uow, rom_id=73, folder="snes-hacks")
    _keep(uow, "snes")
    reader = FakePlatformCoreReader(mapping={"snes": "bsnes"})
    resolver, _ = _make_resolver(uow=uow, core_info=_SNES_CORES, platform_core_reader=reader)
    read_keys: list[str] = []
    kv_get = uow.kv_config.get
    uow.kv_config.get = lambda key: read_keys.append(key) or kv_get(key)  # type: ignore[method-assign]

    assert resolver.active_core_for_rom(73) == ("snes9x_libretro", "Snes9x")
    assert reader.calls == ["snes-hacks"]
    assert PLATFORM_IDS_KEY not in read_keys
