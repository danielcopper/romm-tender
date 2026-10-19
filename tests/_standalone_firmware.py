"""Standalone emulators' firmware settings as RetroDECK sets them up, laid down under a test home for the real resolver.

Shared by the adapter tier and the contract tier, which build the rest of the
RetroDECK tree each their own way. Each emulator's settings file sits where the
resolver reads it (``_vendor/atlas/data/emulator_settings.json``) and holds the
keys its firmware answer reads, the BIOS ones left empty:

- DuckStation: RetroDECK writes only the search directory and leaves the three
  per-region keys empty (``components/duckstation/component_prepare.sh``), so
  the emulator finds its BIOS by searching that folder;
- PCSX2: ``[Filenames] BIOS`` empty — no BIOS chosen in its settings;
- xemu: the three ``[sys.files]`` paths empty.
"""

from __future__ import annotations

import os

# ES-DE's systems with each standalone emulator as their one emulator.
SYSTEMS = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>psx</name>
    <fullname>Sony PlayStation</fullname>
    <path>%ROMPATH%/psx</path>
    <extension>.cue .CUE</extension>
    <command label="DuckStation (Standalone)">%EMULATOR_DUCKSTATION% -batch %ROM%</command>
    <platform>psx</platform>
  </system>
  <system>
    <name>ps2</name>
    <fullname>Sony PlayStation 2</fullname>
    <path>%ROMPATH%/ps2</path>
    <extension>.iso .ISO</extension>
    <command label="PCSX2 (Standalone)">%EMULATOR_PCSX2% -batch %ROM%</command>
    <platform>ps2</platform>
  </system>
  <system>
    <name>xbox</name>
    <fullname>Microsoft Xbox</fullname>
    <path>%ROMPATH%/xbox</path>
    <extension>.iso .ISO</extension>
    <command label="xemu (Standalone)">%EMULATOR_XEMU% -dvd_path %ROM%</command>
    <platform>xbox</platform>
  </system>
</systemList>
"""

# The one size DuckStation's search keeps a PlayStation image at (``bios.h``).
_IMAGE_SIZE = 512 * 1024


def lay_down_standalone_emulators(home: str, bios: str, *images: str) -> None:
    """The three emulators' settings under *home*, each pointing at *bios*, which holds *images*.

    Each image is a file of the size DuckStation's search keeps and bytes its
    table does not know — one DuckStation still boots.
    """
    os.makedirs(bios, exist_ok=True)
    for name in images:
        with open(os.path.join(bios, name), "wb") as f:
            f.write(b"\0" * _IMAGE_SIZE)
    var = os.path.join(home, ".var", "app", "net.retrodeck.retrodeck")
    _write(
        os.path.join(var, "config", "duckstation", "settings.ini"),
        f"[BIOS]\nSearchDirectory = {bios}\nPathNTSCU = \nPathNTSCJ = \nPathPAL = \n",
    )
    _write(
        os.path.join(var, "config", "PCSX2", "inis", "PCSX2.ini"), f"[Folders]\nBios = {bios}\n[Filenames]\nBIOS = \n"
    )
    _write(
        os.path.join(var, "data", "xemu", "xemu", "xemu.toml"),
        '[sys.files]\nbootrom_path = ""\nflashrom_path = ""\nhdd_path = ""\n',
    )


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
