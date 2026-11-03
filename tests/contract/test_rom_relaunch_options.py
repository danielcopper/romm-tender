"""Contract tests for what a PS3 game kept as a folder starts with, over the real catalogue and the real UoW."""

from __future__ import annotations

from domain.rom_install import RomInstall

from ._seed import seed_component_launcher, seed_es_find_rules, seed_es_systems, seed_rom

# A ps3 system as RetroDECK ships it: the shortcut form is its default and cannot
# be baked, and RPCS3 Directory (Standalone) is the entry whose command hands
# RPCS3 the game's path.
_PS3_ES_SYSTEMS_XML = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>ps3</name>
    <extension>.desktop .ps3 .ps3dir</extension>
    <command label="RPCS3 Shortcut (Standalone)">%ENABLESHORTCUTS% %EMULATOR_OS-SHELL% %ROM%</command>
    <command label="RPCS3 Directory (Standalone)">%EMULATOR_RPCS3% --no-gui %ROM%</command>
  </system>
</systemList>
"""

# RPCS3 find rule in the real shape's staticpath half: a host AppImage entry
# BEFORE the bundled /app RetroDECK component launcher (the sandbox path the
# direct bake uses). The real file's systempath rule is left out: ES-DE checks
# it first, against the sandbox's PATH, and with no Flatpak runtime under
# tmp_path the resolver cannot see that PATH and states nothing.
_PS3_ES_FIND_RULES_XML = """\
<?xml version="1.0"?>
<ruleList>
  <emulator name="RPCS3">
    <rule type="staticpath">
      <entry>~/Applications/rpcs3*.AppImage</entry>
      <entry>/app/retrodeck/components/rpcs3/component_launcher.sh</entry>
    </rule>
  </emulator>
</ruleList>
"""


# The shell the shortcut form runs, found where the deploy keeps it, so that the
# resolver states that form startable rather than its installation unestablished.
_PS3_AND_SHELL_FIND_RULES_XML = _PS3_ES_FIND_RULES_XML.replace(
    "</ruleList>",
    """  <emulator name="OS-SHELL">
    <rule type="staticpath">
      <entry>/app/retrodeck/components/osshell/component_launcher.sh</entry>
    </rule>
  </emulator>
</ruleList>""",
)


def _seed_ps3_folder_game(harness, rom_id: int, *, find_rules: str = _PS3_ES_FIND_RULES_XML, folder: str = "MyGame"):
    """A PS3 game installed as the folder *folder* and bound to a shortcut, over RetroDECK's ps3 catalogue."""
    seed_es_systems(harness, _PS3_ES_SYSTEMS_XML)
    seed_es_find_rules(harness, find_rules)
    launcher = seed_component_launcher(harness, "rpcs3")
    seed_rom(harness, rom_id, platform_slug="ps3", shortcut_app_id=rom_id)
    rom_dir = harness.tmp_path / "retrodeck" / "roms" / "ps3" / folder
    eboot = rom_dir / "PS3_GAME" / "USRDIR" / "EBOOT.BIN"
    eboot.parent.mkdir(parents=True, exist_ok=True)
    eboot.write_bytes(b"\x00" * 16)
    with harness.uow_factory() as uow:
        uow.rom_installs.save(
            RomInstall.mark_installed(
                rom_id=rom_id,
                file_path=str(eboot),
                rom_dir=str(rom_dir),
                platform_slug="ps3",
                system="ps3",
                installed_at="2026-01-01T00:00:00",
            )
        )
    return rom_dir, launcher


async def test_a_ps3_folder_game_with_its_shipped_default_cannot_be_started_by_tender_yet(harness):
    """No entry is taken by itself, and a default Tender cannot bake is refused (#2195 D7, D23).

    Whether the default starts the folder is the launch verdict's (D21). The
    resolver looks at the folder as RetroDECK's sandbox sees it, which does not
    see under ``tmp_path``; what it answers about a folder it does see is pinned
    in ``tests/adapters/test_atlas_launchability.py``.
    """
    _seed_ps3_folder_game(harness, 56, find_rules=_PS3_AND_SHELL_FIND_RULES_XML, folder="MyGame.ps3")
    seed_component_launcher(harness, "osshell")

    info = await harness.endpoints.get_platform_core_info(56)

    assert info["active_core_label"] == "RPCS3 Shortcut (Standalone)"
    assert info["start"] == {"state": "cannot_start", "reason": "not_bakeable", "entry": "RPCS3 Shortcut (Standalone)"}
    assert info["launch"]["entry"] == "RPCS3 Shortcut (Standalone)"


async def test_a_pick_of_an_entry_tender_cannot_bake_is_stored_and_its_start_refused(harness):
    """Choosable like any other entry, and Play refused rather than started through ES-DE's choice (#2195 D7, D23)."""
    _seed_ps3_folder_game(harness, 57, find_rules=_PS3_AND_SHELL_FIND_RULES_XML, folder="MyGame.ps3")
    seed_component_launcher(harness, "osshell")
    with harness.uow_factory() as uow:
        uow.roms.set_emulator_override(57, "RPCS3 Directory (Standalone)", "retrodeck")

    pinned = await harness.endpoints.set_game_core(57, "RPCS3 Shortcut (Standalone)")
    info = await harness.endpoints.get_platform_core_info(57)

    assert pinned["success"] is True
    with harness.uow_factory() as uow:
        assert uow.roms.get(57).emulator_override == "RPCS3 Shortcut (Standalone)"
    assert info["start"] == {"state": "cannot_start", "reason": "not_bakeable", "entry": "RPCS3 Shortcut (Standalone)"}
