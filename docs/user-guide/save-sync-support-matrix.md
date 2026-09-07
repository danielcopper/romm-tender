# Save sync support by platform

Save sync works **per game**: when a game is installed, its save is uploaded to RomM and pulled back before launch, so
you can carry on across devices. Standard cartridge saves work automatically. A few consoles store saves differently —
this page shows what syncs for each system today, and what's planned.

## How reliable is this page?

**The plugin no longer decides what a save is from a table like this one.** It asks your machine, per game and per the
emulator that will launch it, every time it syncs — so the answer follows your actual core choice and your actual core
options rather than a row written in advance. This page is now background reading: it explains why a platform behaves
the way it does, and the plugin's own answer is what governs.

Two things still matter when you read a row:

- **A ❌ often means "not with the settings that ship", not "impossible".** Several cores can write per-game saves if
  you change a core option — the rows below say which. Turning that on is not yet something the plugin does for you, but
  it does now **notice**: change the option and the next sync reads the new answer.
- **Rows are stated for each platform's _default_ core.** You can override the core per system and per game, and a
  different core can behave differently — which is exactly why the plugin asks per game. RetroArch's
  `savefiles_in_content_dir` still moves saves out of where the plugin looks, and that is reported separately.

Most of this table is derived from libretro's documentation and from reading core source, not from watching each core
write a save. Where the [emu-atlas](https://github.com/danielcopper/emu-atlas) audit has corrected an earlier
assumption, this page reflects the audit.

## When save sync does nothing, and why

Where the plugin cannot carry a game's saves it now says so instead of quietly finding nothing. There are four reasons,
and they mean different things:

- **The emulator keeps one save card that all games share.** Standalone PCSX2 is the common case. Syncing it per game
  would copy other games' progress onto this one's record, so the plugin leaves it alone.
- **The save is written inside the game file itself.** There is no separate file to carry.
- **The emulator files saves under the game's own identity**, which the plugin cannot read yet — in the file name for
  Dreamcast, in the folder name for GameCube. Dreamcast, GameCube, Nintendo 3DS and Wii U are here.
- **What the emulator writes could not be established** — either nobody has audited that core yet, or the folder is
  known and the names inside it are not.

None of these is an error, and none of them stops a game launching. If you change the emulator for a game, the answer is
read again for the new one.

## Categories

|    | Meaning                                                                                                                                                                                                                                                        |
| -- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ✅ | **Synced today.** Your saves for this system carry across devices automatically. A few systems answer differently for a disc image than for a raw dump — where that is so, the note says which file gets you the per-game save.                                |
| 🔜 | **Planned.** This save type isn't synced yet, but it fits the model and is on the way in a future release.                                                                                                                                                     |
| ❌ | **Not synced yet.** This system's default core writes a _shared_ card (one file for many games), keeps saves outside the per-game save folder, or hasn't been pinned down yet — so it doesn't fit per-game sync today. Handled differently in a later release. |
| ⚪ | **No save data.** This system's emulator has no in-game save to sync (you can still use save states locally).                                                                                                                                                  |

## What syncs today ✅

Standard per-game cartridge saves sync automatically. That covers the large majority of systems — Nintendo (NES, SNES,
Game Boy / Color / Advance, N64, DS), Sega (Master System, Game Gear, Genesis / Mega Drive), PC Engine / TurboGrafx,
WonderSwan, Atari Lynx, Virtual Boy, and more.

**Sega CD depends on your game file.** A raw `.bin` dump saves per game and syncs; a `.chd`, `.cue` or `.iso` disc image
puts the save on a shared BRAM card that all your Sega CD games write to, which cannot be carried per game. Amiga is the
same story with different answers: a `.chd` CD32 image saves per game, while an `.adf` floppy writes into the disk image
itself and an `.hdf` hard-disk image cannot be established at all.

3DO and Neo Geo are named right and still not found: those emulators keep saves in a per-emulator subfolder that the
plugin does not yet look in.

The systems whose default core has been **watched writing a save on a stock RetroDECK install** are Game Boy / Color /
Advance, N64, Saturn, Neo Geo Pocket (Color) and Pokémon Mini. The rest of the ✅ rows follow the same standard `.srm`
convention and are expected to behave identically, but haven't been observed one by one.

## Coming soon 🔜

Per-game saves for these systems fit the sync model and are planned for a future release:

| System      | Notes                                                                                                  |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| PlayStation | Memory-card saves. The cores write them per game, but under a name the plugin doesn't probe yet.       |
| 3DO         | The Opera core writes per-game NVRAM into its own `opera/per_game/` subfolder, under a versioned name. |

!!! warning "3DO was listed as syncing here before — it wasn't"

    Until this revision, 3DO showed as ✅ on the assumption that the Opera core writes a plain `<game>.srm` into the
    save folder. The core audit disproved that: Opera writes its NVRAM to `opera/per_game/` with a version number in
    the filename, and exposes no save RAM to RetroArch at all — so there was never a `.srm` for the plugin to find.
    **If you have 3DO games, your saves have not been backed up to RomM.** Copy them off the device yourself until
    this is supported.

A few less-common systems (some DOS, PICO-8, ST-V) may also gain support pending confirmation.

## Not synced yet ❌

These systems' default cores write a **shared card** — a single file holding the saves for _all_ your games — keep saves
outside the per-game save folder, or haven't been pinned down yet. A shared card can't be split per game without risking
other games' saves, so it doesn't fit per-game sync today. We're looking at safe ways to handle these in a future
release.

| System         | Why it doesn't sync today                                                                                                                                                                                          |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Dreamcast      | Flycast ships with **Per-Game VMUs off**, so every game shares `vmu_save_A1.bin` and its siblings in RetroDECK's system folder. The core _can_ write per-game VMUs; the names it uses then aren't pinned down yet. |
| PlayStation 2  | The LRPS2 core ships with **shared memory cards on** (`Mcd001.ps2` / `Mcd002.ps2`). With that option off it writes one `<game>.ps2` card per game — which would fit per-game sync.                                 |
| GameCube / Wii | The Dolphin core appears to keep saves under its own subtree rather than the per-game save folder. Not confirmed on-device yet.                                                                                    |
| Neo Geo CD     | Ships writing one shared save. The core has a per-content mode; which one wins when loading isn't confirmed.                                                                                                       |
| Nintendo 3DS   | The Azahar core appears to use its own save subtree. Not confirmed on-device yet.                                                                                                                                  |
| PSP            | Where the PPSSPP core keeps its saves, and whether they are per game, hasn't been established.                                                                                                                     |
| Arcade (MAME)  | NVRAM is stored separately by the emulator, not as a file named after your ROM.                                                                                                                                    |

## No save data ⚪

Many computer, arcade, and homebrew systems have no in-game battery save at all — there's simply nothing to sync (save
states still work locally). See the full table for specifics.

## Full platform list

??? note "Every platform (149)"

    Status of each platform's default emulator core. Some platforms offer alternative cores that may behave differently.

    | Platform | Status | Notes |
    | --- | --- | --- |
    | `amiga` | ❌ | An `.adf` floppy keeps the save inside the disk image; an `.hdf` or `.lha` is not answered |
    | `amstradcpc` | ❌ | Not synced |
    | `apple2` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `apple2gs` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `arcade` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `arcadia` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `astrocde` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `atomiswave` | ❌ | Shared VMU card (Flycast default; the core has a per-game mode) |
    | `consolearcade` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `cps` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `cps1` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `cps2` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `cps3` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `crvision` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `daphne` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `doom` | ❌ | Not synced |
    | `dreamcast` | ❌ | Shared VMU card (Flycast default; the core has a per-game mode) |
    | `easyrpg` | ❌ | Not synced |
    | `fmtowns` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `gamate` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `gameandwatch` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `gamecom` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `gc` | ❌ | Dolphin core's own save subtree — not confirmed on-device |
    | `gmaster` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `gx4000` | ❌ | Not synced |
    | `laserdisc` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `lcdgames` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `mame` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `mess` | ❌ | Not synced |
    | `model2` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `n3ds` | ❌ | Azahar core's own save subtree — not confirmed on-device |
    | `naomi` | ❌ | Shared VMU card (Flycast default; the core has a per-game mode) |
    | `naomi2` | ❌ | Shared VMU card (Flycast default; the core has a per-game mode) |
    | `naomigd` | ❌ | Shared VMU card (Flycast default; the core has a per-game mode) |
    | `neogeocd` | ❌ | Shared save (the core has a per-content mode) |
    | `neogeocdjp` | ❌ | Shared save (the core has a per-content mode) |
    | `ps2` | ❌ | Shared memory card by default (LRPS2); per-game `<game>.ps2` available as a core option |
    | `psp` | ❌ | Where the PPSSPP core keeps its saves is not established |
    | `pv1000` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `scummvm` | ❌ | Not synced |
    | `scv` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `supracan` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `vsmile` | ❌ | Saves are stored separately by the emulator (MAME) |
    | `wii` | ❌ | Dolphin core's own save subtree — not confirmed on-device |
    | `x68000` | ❌ | Not synced |
    | `3do` | 🔜 | Per-game NVRAM, but in the Opera core's own `opera/per_game/` subfolder with a versioned name |
    | `amiga1200` | 🔜 | Planned |
    | `amiga600` | 🔜 | Planned |
    | `atarijaguar` | 🔜 | Planned |
    | `cdimono1` | 🔜 | Under review |
    | `cdtv` | 🔜 | Planned — pending platform mapping (#907) |
    | `dos` | 🔜 | Planned |
    | `neogeo` | 🔜 | Per-game saves, but in the FinalBurn Neo core's own `fbneo/` subfolder |
    | `pc` | 🔜 | Planned |
    | `pico8` | 🔜 | Planned |
    | `psx` | 🔜 | Planned |
    | `quake` | 🔜 | Planned |
    | `saturnjp` | 🔜 | Planned |
    | `stv` | 🔜 | Planned |
    | `wasm4` | 🔜 | Under review |
    | `windows3x` | 🔜 | Planned |
    | `windows9x` | 🔜 | Planned |
    | `amigacd32` | ✅ | A `.chd` disc image saves per game; a raw `.bin` is not answered |
    | `atari2600` | ✅ | Synced |
    | `c64` | ✅ | Synced |
    | `famicom` | ✅ | Synced |
    | `fbneo` | ✅ | Synced |
    | `fds` | ✅ | Synced |
    | `gamegear` | ✅ | Synced |
    | `gb` | ✅ | Synced |
    | `gba` | ✅ | Synced |
    | `gbc` | ✅ | Synced |
    | `genesis` | ✅ | Synced |
    | `mark3` | ✅ | Synced |
    | `mastersystem` | ✅ | Synced |
    | `megacd` | ✅ | A raw `.bin` dump saves per game; a disc image uses a shared BRAM card |
    | `megacdjp` | ✅ | Synced |
    | `megadrive` | ✅ | Synced |
    | `megadrivejp` | ✅ | Synced |
    | `megaduck` | ✅ | Synced |
    | `multivision` | ✅ | Synced |
    | `n64` | ✅ | Synced |
    | `n64dd` | ✅ | Synced |
    | `nds` | ✅ | Synced |
    | `nes` | ✅ | Synced |
    | `ngp` | ✅ | Synced |
    | `ngpc` | ✅ | Synced |
    | `pc88` | ✅ | Synced |
    | `pcengine` | ✅ | Synced |
    | `pcenginecd` | ✅ | Synced |
    | `pcfx` | ✅ | Synced |
    | `plus4` | ✅ | Synced |
    | `pokemini` | ✅ | Synced |
    | `satellaview` | ✅ | Synced |
    | `saturn` | ✅ | Synced |
    | `sega32x` | ✅ | Synced |
    | `sega32xjp` | ✅ | Synced |
    | `sega32xna` | ✅ | Synced |
    | `segacd` | ✅ | A raw `.bin` dump saves per game; a disc image uses a shared BRAM card |
    | `sfc` | ✅ | Synced |
    | `sg-1000` | ✅ | Synced |
    | `sgb` | ✅ | Synced |
    | `snes` | ✅ | Synced |
    | `snesna` | ✅ | Synced |
    | `sufami` | ✅ | Synced |
    | `supergrafx` | ✅ | Synced |
    | `supervision` | ✅ | Synced |
    | `tg-cd` | ✅ | Synced |
    | `tg16` | ✅ | Synced |
    | `tic80` | ✅ | Synced |
    | `vic20` | ✅ | Synced |
    | `virtualboy` | ✅ | Synced |
    | `wonderswan` | ✅ | Synced |
    | `wonderswancolor` | ✅ | Synced |
    | `arduboy` | ⚪ | No save data |
    | `atari5200` | ⚪ | No save data |
    | `atari7800` | ⚪ | No save data |
    | `atari800` | ⚪ | No save data |
    | `atarilynx` | ⚪ | No save data |
    | `atarist` | ⚪ | No save data |
    | `atarixe` | ⚪ | No save data |
    | `bbcmicro` | ⚪ | No save data |
    | `chailove` | ⚪ | No save data |
    | `channelf` | ⚪ | No save data |
    | `colecovision` | ⚪ | No save data |
    | `fba` | ⚪ | No save data |
    | `intellivision` | ⚪ | No save data |
    | `j2me` | ⚪ | No save data |
    | `lowresnx` | ⚪ | No save data |
    | `lutro` | ⚪ | No save data |
    | `moto` | ⚪ | No save data |
    | `msx` | ⚪ | No save data |
    | `msx1` | ⚪ | No save data |
    | `msx2` | ⚪ | No save data |
    | `msxturbor` | ⚪ | No save data |
    | `odyssey2` | ⚪ | No save data |
    | `palm` | ⚪ | No save data |
    | `pc98` | ⚪ | No save data |
    | `ports` | ⚪ | No save data |
    | `spectravideo` | ⚪ | No save data |
    | `to8` | ⚪ | No save data |
    | `uzebox` | ⚪ | No save data |
    | `vectrex` | ⚪ | No save data |
    | `videopac` | ⚪ | No save data |
    | `vircon32` | ⚪ | No save data |
    | `x1` | ⚪ | No save data |
    | `zmachine` | ⚪ | No save data |
    | `zx81` | ⚪ | No save data |
    | `zxspectrum` | ⚪ | No save data |

---

_Coverage is reviewed against the emulator cores RetroDECK ships. Most rows still await on-device confirmation; see
[How reliable is this page?](#how-reliable-is-this-page) above. Last reviewed 2026-08-02, against the
[emu-atlas](https://github.com/danielcopper/emu-atlas) core audit as of 2026-07-24._
