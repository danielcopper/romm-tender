"""Contract tests for the core-selection endpoints over the real nesting.

``CoreService.clear_game_core`` (Reset / Follow default) and
``set_system_core`` (per-platform core change fan-out) both re-bake a ROM's
Steam ``launch_options`` from its active core, resolved through the real
:class:`ActiveCoreResolver` — which opens its **own** Unit of Work.

The unit tests inject a ``FakeActiveCoreResolver`` (no real UoW), so they never
exercise the nesting. This tier drives the **real** endpoints over the
**real** file-based SQLite UoW the harness wires: every UoW opens with
``BEGIN IMMEDIATE`` (the per-connection write lock), and the lock is not
re-entrant. Resolving the active core inside the still-open write UoW would
block the resolver's own UoW until ``busy_timeout`` then raise
``database is locked`` (#1047 / #1134) — which is why the write UoW is closed
before the resolve. ``clear_game_core`` additionally must resolve *after* the
pin-clear commits so the resolver reads the landed NULL and bakes the
POST-clear core, never the just-cleared pin (#1047).

The resolution reads the live ``es_systems.xml`` the harness seeds under
``tmp_path`` (#1210) — there is no ``core_defaults`` snapshot. The last block
pins the SHAPE of the three emulator-picker payloads — the game-detail
``get_platform_core_info``, the platform-keyed ``get_system_core_info`` the
Library page's Platforms detail asks, and ``get_platform_firmware_status``, the
BIOS page's per-platform answer — with the emulator list present (happy) and
absent (emulator data unavailable). The ``get_firmware_status`` overview beside
them carries none of it, and is pinned for that absence.
"""

from __future__ import annotations

from typing import Any

from ._seed import (
    seed_component_launcher,
    seed_es_find_rules,
    seed_es_systems,
    seed_install,
    seed_retrodeck_marker,
    seed_rom,
)

_GBA_FIRMWARE = [
    {
        "id": 1,
        "file_name": "gba_bios.bin",
        "file_size_bytes": 100,
        "md5_hash": "",
    }
]

_MGBA_ENTRY = {
    "label": "mGBA",
    "kind": "core",
    "core_so": "mgba_libretro",
    "emulator": "mgba_libretro.so",
    "declared_index": 0,
    "is_default": True,
    "chosen_in_frontend": False,
    "bakeable": True,
    "reason": None,
}
_VBA_NEXT_ENTRY = {
    "label": "VBA Next",
    "kind": "core",
    "core_so": "vba_next_libretro",
    "emulator": "vba_next_libretro.so",
    "declared_index": 1,
    "is_default": False,
    "chosen_in_frontend": False,
    "bakeable": True,
    "reason": None,
}


async def test_clear_game_core_bakes_post_clear_core_not_old_pin(harness):
    """Clearing a pin bakes the resolved default, not the just-cleared override.

    A ``gba`` ROM pinned to VBA Next is cleared. The resolver runs after the
    pin-clear commits, so it reads the landed NULL and resolves to the live
    es_systems default (mGBA); the re-baked ``launch_options`` must carry the
    mGBA core, never the cleared VBA Next pin.
    """
    seed_es_systems(harness)
    seed_install(harness, 42, system="gba", platform_slug="gba", file_name="pokemon.gba")
    with harness.uow_factory() as uow:
        uow.roms.set_emulator_override(42, "VBA Next", "retrodeck")

    result = await harness.endpoints.clear_game_core(42)

    assert result["success"] is True
    assert result["app_id"] == 42
    # The POST-clear core (system default mGBA) is baked, NOT the cleared pin.
    assert "mgba_libretro.so" in result["launch_options"]
    assert "vba_next_libretro.so" not in result["launch_options"]
    # The pin is gone (SQL NULL) — read back through a fresh UoW.
    with harness.uow_factory() as uow:
        assert uow.roms.get(42).emulator_override is None


async def test_clear_game_core_unknown_rom_returns_canonical_failure(harness):
    """An unknown ROM returns the canonical ``{success, reason, message}`` failure."""
    result = await harness.endpoints.clear_game_core(999)

    assert result["success"] is False
    assert result["reason"] == "not_found"
    assert isinstance(result["message"], str)
    assert result["message"]


async def test_set_system_core_rebakes_only_unpinned_rom(harness):
    """The per-platform fan-out re-bakes each unpinned ROM through the real resolver.

    Two installed+bound ``gba`` ROMs: one plain, one with a per-game pin. Setting
    the platform core to VBA Next must re-bake only the unpinned ROM (the pin
    wins over the platform default for the other), with the VBA Next core baked.
    """
    seed_es_systems(harness)
    seed_install(harness, 1, system="gba", platform_slug="gba", file_name="a.gba")
    seed_install(harness, 2, system="gba", platform_slug="gba", file_name="b.gba")
    with harness.uow_factory() as uow:
        uow.roms.set_emulator_override(2, "mGBA", "retrodeck")

    result = await harness.endpoints.set_system_core("gba", "VBA Next")

    assert result["success"] is True
    items = result["rebake_items"]
    # Exactly the unpinned ROM (app_id 1) is re-baked; the pinned ROM is absent.
    assert len(items) == 1
    assert items[0]["app_id"] == 1
    assert "vba_next_libretro.so" in items[0]["launch_options"]
    assert all(item["app_id"] != 2 for item in items)


async def test_set_system_core_answers_exactly_its_rebake_list_and_lease(harness):
    """A stored switch answers its re-bake list and the lease for it, and no BIOS verdict."""
    seed_es_systems(harness)
    seed_install(harness, 1, system="gba", platform_slug="gba", file_name="a.gba")

    result = await harness.endpoints.set_system_core("gba", "VBA Next")

    assert set(result) == {"success", "rebake_items", "prune_lease_token"}
    assert result["success"] is True


async def test_set_game_core_unbakeable_label_returns_canonical_failure(harness):
    """A label that does not resolve to a bakeable emulator hard-fails, no write."""
    seed_es_systems(harness)
    seed_install(harness, 5, system="gba", platform_slug="gba", file_name="c.gba")

    result = await harness.endpoints.set_game_core(5, "Not A Real Emulator")

    assert result["success"] is False
    assert result["reason"] == "core_unavailable"
    assert isinstance(result["message"], str)
    assert result["message"]
    with harness.uow_factory() as uow:
        assert uow.roms.get(5).emulator_override is None


async def test_get_platform_core_info_payload_shape(harness):
    """The game-detail picker payload pins its keys, emulator list, and active core."""
    seed_es_systems(harness)
    seed_rom(harness, 42, platform_slug="gba")

    result = await harness.endpoints.get_platform_core_info(42)

    assert set(result) == {
        "emulators",
        "emulator_data_available",
        "emulator_data_reason",
        "emulator_source",
        "platform_system",
        "active_core",
        "active_core_label",
        "platform_core_label",
        "has_game_override",
        "start",
        "launch",
        "vanished_pick",
    }
    assert result["emulator_data_available"] is True
    assert result["emulators"] == [_MGBA_ENTRY, _VBA_NEXT_ENTRY]
    # One identity space across the whole payload: the active pick is stated in
    # the same spelling the picker entry beside it carries, which is what the
    # BIOS pane joins its per-emulator rows on.
    assert result["active_core"] == "mgba_libretro.so"
    assert result["active_core"] == _MGBA_ENTRY["emulator"]
    assert result["active_core_label"] == "mGBA"
    assert result["platform_core_label"] is None
    assert result["has_game_override"] is False


async def test_get_platform_core_info_unavailable_when_no_es_systems(harness):
    """No es_systems (RetroDECK not detected) → emulator data flagged unavailable."""
    seed_rom(harness, 43, platform_slug="gba")

    result = await harness.endpoints.get_platform_core_info(43)

    assert result["emulator_data_available"] is False
    assert result["emulators"] == []


async def test_get_system_core_info_payload_shape(harness):
    """The platform-keyed picker payload pins its keys and the resolved label."""
    seed_es_systems(harness)

    result = await harness.endpoints.get_system_core_info("gba")

    # No `success`: the endpoint has no in-band failure branch, so a key that
    # could only ever read True would be an offer of an answer it never gives.
    assert set(result) == {
        "emulators",
        "emulator_data_available",
        "emulator_data_reason",
        "emulator_source",
        "platform_system",
        "active_core_label",
        "start",
        "vanished_pick",
        "source_defaults",
        "tender_default",
        "default_elsewhere",
    }
    assert result["emulator_data_available"] is True
    assert result["emulators"] == [_MGBA_ENTRY, _VBA_NEXT_ENTRY]
    assert result["active_core_label"] == "mGBA"


async def test_get_system_core_info_answers_without_a_rom(harness):
    """No ROM of the platform is synced — the platform-keyed read still answers.

    This is what separates it from ``get_platform_core_info``: the Platforms
    detail selects a platform the user has never synced and must still be able
    to show and change its core.
    """
    seed_es_systems(harness)

    result = await harness.endpoints.get_system_core_info("gba")

    with harness.uow_factory() as uow:
        assert list(uow.roms.iter_all()) == []
    assert result["emulators"] == [_MGBA_ENTRY, _VBA_NEXT_ENTRY]


async def test_get_system_core_info_reflects_a_landed_platform_core(harness):
    """After ``set_system_core`` the read answers with the new label, not the default."""
    seed_es_systems(harness)

    assert (await harness.endpoints.set_system_core("gba", "VBA Next"))["success"] is True
    result = await harness.endpoints.get_system_core_info("gba")

    assert result["active_core_label"] == "VBA Next"


async def test_get_system_core_info_unavailable_when_no_es_systems(harness):
    """No es_systems (RetroDECK not detected) → emulator data flagged unavailable."""
    result = await harness.endpoints.get_system_core_info("gba")

    assert result["emulator_data_available"] is False
    assert result["emulators"] == []
    assert result["active_core_label"] is None


async def test_the_firmware_overview_reads_no_platform(harness):
    """The overview says whether RomM's firmware listing could be read, and nothing else.

    A platform's state costs a live per-system reading, so this call pays none —
    which makes the absence of every platform-bearing key the thing to pin: a
    ``bios_level``, a ``files`` list or a platform list here would be rendered
    over platforms nobody has asked about yet.
    """
    seed_es_systems(harness)
    seed_rom(harness, 6, platform_slug="gba")
    harness.romm.seed_firmware("gba", *_GBA_FIRMWARE)

    result = await harness.endpoints.get_firmware_status()

    assert result == {"success": True, "server_offline": False}


async def _platform_answer(harness, slug: str) -> dict[str, Any]:
    """The BIOS answer for one listed platform, asked for the way the page asks."""
    answer = await harness.endpoints.get_platform_firmware_status(slug)
    assert answer["success"] is True
    assert answer["platform"]["platform_slug"] == slug
    return answer["platform"]


async def test_a_platforms_answer_carries_its_classified_emulators(harness):
    """Every platform the page lists has the classified emulator list with its answer."""
    seed_es_systems(harness)
    seed_rom(harness, 7, platform_slug="gba")  # bound → has_games
    harness.romm.seed_firmware("gba", *_GBA_FIRMWARE)

    gba = await _platform_answer(harness, "gba")

    assert gba["emulator_data_available"] is True
    assert gba["emulators"] == [_MGBA_ENTRY, _VBA_NEXT_ENTRY]
    # The pane's pick and the identity its BIOS rows are keyed on are one field,
    # and it is the identity — the one spelling that names a standalone emulator
    # too. It is the same string the picker entry beside it carries.
    assert gba["active_core"] == "mgba_libretro.so"
    assert gba["active_core"] == _MGBA_ENTRY["emulator"]


async def test_a_platforms_answer_flags_unavailable_emulator_data(harness):
    """No es_systems → the platform's answer flags emulator data unavailable.

    Unavailable is not empty: the page says why the list is missing off this
    flag and its reason, and an empty list alone would read as a platform with
    no emulator.
    """
    seed_rom(harness, 8, platform_slug="gba")
    harness.romm.seed_firmware("gba", *_GBA_FIRMWARE)

    gba = await _platform_answer(harness, "gba")

    assert gba["emulator_data_available"] is False
    assert gba["emulators"] == []


# A switch system whose only bakeable standalone command names Ryubing — a
# RetroDECK component that the seeded deploy does not carry, so the resolver's
# launch lookup states it not installed.
_SWITCH_ES_SYSTEMS_XML = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>switch</name>
    <extension>.nsp .xci</extension>
    <command label="Ryubing (Standalone)">%EMULATOR_RYUBING% %ROM%</command>
    <command label="Yuzu">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/yuzu_libretro.so %ROM%</command>
  </system>
</systemList>
"""

# The bundled file carries RetroArch's rule as a shipped one does: RetroDECK's
# run_game.sh reads only this layer, and without it the resolver states the
# script would run nothing for a RetroArch entry.
_SWITCH_ES_FIND_RULES_XML = """\
<?xml version="1.0"?>
<ruleList>
  <emulator name="RETROARCH">
    <rule type="staticpath"><entry>/app/retrodeck/components/retroarch/retroarch</entry></rule>
  </emulator>
  <emulator name="RYUBING">
    <rule type="staticpath">
      <entry>/app/retrodeck/components/ryubing/component_launcher.sh</entry>
    </rule>
  </emulator>
</ruleList>
"""


async def test_a_detected_installation_without_a_catalogue_reads_nothing_off_the_host(harness):
    """A marker with no seeded catalogue answers unavailable — never the dev box's own.

    The regression this pins is hermeticity, not behaviour. Detection triggers on
    ``retrodeck.json``, and the resolver then resolves its ``/app`` paths through
    the flatpak deploy — which, unguarded, is the real one on any machine with
    RetroDECK installed, so this test answered with five ``gba`` entries and 172
    systems off the host. Every other test in this file either seeds the per-user
    deploy, which wins over the system one, or seeds no marker at all and is never
    detected — so nothing else could see it.
    """
    seed_retrodeck_marker(harness)
    seed_rom(harness, 60, platform_slug="gba")

    result = await harness.endpoints.get_platform_core_info(60)

    assert result["emulator_data_available"] is False
    assert result["emulators"] == []
    assert result["active_core"] is None
    assert result["active_core_label"] is None


async def test_a_standalone_whose_component_is_absent_stays_the_default_and_cannot_start(harness):
    """An emulator the resolver states not installed is withheld from picking, and stays the default (#2195 D7).

    Ryubing is declared first and is the only standalone; its component launcher
    is not in the seeded deploy, so the picker must show it ``needs_setup`` /
    ``not_installed``. Nothing behind it is taken instead: the platform names
    Ryubing and says it cannot start. Drives the real catalogue read and the
    resolver's own launch lookup over the seeded find rules.
    """
    seed_es_systems(harness, _SWITCH_ES_SYSTEMS_XML)
    seed_es_find_rules(harness, _SWITCH_ES_FIND_RULES_XML)

    result = await harness.endpoints.get_system_core_info("switch")

    assert result["emulator_data_available"] is True
    assert result["emulators"] == [
        {
            "label": "Ryubing (Standalone)",
            "kind": "program",
            "core_so": None,
            "emulator": "RYUBING",
            "declared_index": 0,
            "is_default": True,
            "chosen_in_frontend": False,
            "bakeable": False,
            "reason": "not_installed",
        },
        {
            "label": "Yuzu",
            "kind": "core",
            "core_so": "yuzu_libretro",
            "emulator": "yuzu_libretro.so",
            "declared_index": 1,
            "is_default": False,
            "chosen_in_frontend": False,
            "bakeable": True,
            "reason": None,
        },
    ]
    assert result["active_core_label"] == "Ryubing (Standalone)"
    assert result["start"] == {
        "state": "cannot_start",
        "reason": "not_installed",
        "entry": "Ryubing (Standalone)",
        "folder_entry": None,
    }


async def test_the_same_standalone_is_the_default_once_its_component_is_there(harness):
    """The other half of the resolver's lookup: seeding the component makes Ryubing startable, the declared first."""
    seed_es_systems(harness, _SWITCH_ES_SYSTEMS_XML)
    seed_es_find_rules(harness, _SWITCH_ES_FIND_RULES_XML)
    seed_component_launcher(harness, "ryubing")

    result = await harness.endpoints.get_system_core_info("switch")

    assert [(e["label"], e["bakeable"], e["is_default"]) for e in result["emulators"]] == [
        ("Ryubing (Standalone)", True, True),
        ("Yuzu", True, False),
    ]
    assert result["active_core_label"] == "Ryubing (Standalone)"


# Neo Geo CD as RetroDECK ships it, FinalBurn Neo first: a core whose command
# passes RetroArch more than its core.
_NEOGEOCD_ES_SYSTEMS_XML = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>neogeocd</name>
    <path>%ROMPATH%/neogeocd</path>
    <command label="FinalBurn Neo">%EMULATOR_RETROARCH% --subsystem neocd -L %CORE_RETROARCH%/fbneo_libretro.so %ROM%\
</command>
    <command label="NeoCD">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/neocd_libretro.so %ROM%</command>
  </system>
</systemList>
"""


async def test_finalburn_neo_on_neo_geo_cd_reads_as_its_core_and_starts_with_its_subsystem(harness):
    """The resolver's kind and core path, end to end: FinalBurn Neo is a core, and its bake keeps its subsystem."""
    seed_es_systems(harness, _NEOGEOCD_ES_SYSTEMS_XML)
    seed_install(harness, 7, system="neogeocd", platform_slug="neogeocd", file_name="game.chd")

    info = await harness.endpoints.get_system_core_info("neogeocd")
    result = await harness.endpoints.clear_game_core(7)

    fbneo = info["emulators"][0]
    assert (fbneo["label"], fbneo["kind"], fbneo["core_so"], fbneo["is_default"]) == (
        "FinalBurn Neo",
        "core",
        "fbneo_libretro",
        True,
    )
    assert result["success"] is True
    assert (
        '-e "%EMULATOR_RETROARCH% --subsystem neocd -L /var/config/retroarch/cores/fbneo_libretro.so %ROM%"'
        in result["launch_options"]
    )
