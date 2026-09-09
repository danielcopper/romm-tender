# Save Sync Coverage

Why some saves sync and others don't. The mechanics live in
[Save File Sync Architecture](save-file-sync-architecture.md); this page explains the **coverage envelope** — which save
files the per-game model can and cannot reach — and the strategy for the gaps. The user-facing result is the
[Save sync support matrix](../user-guide/save-sync-support-matrix.md).

## The per-game discovery model

Save discovery is **exact-name probing**, not a directory scan. For an installed ROM whose file stem is `rom_name`, the
sync looks for exactly the files the save answer names for this ROM's emulator, and uploads the ones that exist. There
is no glob, no `listdir`, no pattern match.

This is a deliberate bijection: **one ROM → one set of `<rom_name>.<ext>` files in the save folder.** It maps perfectly
onto libretro's own SRAM convention, where the save file mirrors the ROM name. Everything that doesn't fit that shape is
invisible to the sync.

Two hard properties follow, and they define the entire coverage envelope:

1. **The filename must be the ROM stem.** A file named anything else — a fixed card name (`pcsx-card2.mcd`,
   `vmu_save_A1.bin`), or a name with a slot/unit infix (`game.1.mcr`) — is never probed.
2. **The file must live in the save folder.** A save in RetroArch's _system_ directory (Flycast VMUs), in a per-emulator
   subdirectory (`mame/nvram/`), or next to the ROM (`savefiles_in_content_dir`) is never seen.

The explicit removed-game cleanup starts with this exact-path projection and adds path-safe filenames already persisted
for that ROM in `RomSaveSyncState.files`. This is an identity-backed exception to the filename rule, not directory
discovery: cleanup never scans for new names. Class (c), unknown fixed/shared names, unresolved uninstalled layouts, and
save states are left physically untouched and recorded as warnings where applicable. If two installed local rows project
the same canonical current-save path, that path is shared ownership; purging one owner may copy it into recovery but
cannot remove it while another owner remains.

The names come from the **save answer**, read live off the machine per ROM and per the emulator that would launch it
(`services/protocols/paths.py` → `SaveLocationReader`, implemented by `adapters/atlas_saves.py` over the vendored
resolver). They used to come from a static per-system extension table this repo maintained by hand; that table is
retired, and nothing replaces it — for every system it covered the machine's answer is at least as good, and for four it
is better in one of two ways. For Amiga, Sega CD and Saturn the machine names something the table got wrong: a save
inside the disk image, a shared BRAM card, a `.smpc` that is configuration rather than progress. For Amiga CD32 the
machine agrees with the table on a `.chd` and REFUSES on a `.bin`, where the table answered `.nvr` for both — a refusal
replacing a guess, which is the smaller of the two improvements and the easier one to mistake for a regression. The
reasoning is in [ADR-0031](../adr/0031-a-save-is-answered-by-the-emulator-that-writes-it.md).

## The answer is per ROM, never per platform

**The answer turns on the content file's own extension**, so it is a property of the ROM and not of its system. Measured
on a stock RetroDECK at emu-atlas 0.14.0:

| System     | Content | Answer                                              |
| ---------- | ------- | --------------------------------------------------- |
| Amiga      | `.adf`  | the save is inside the disk image                   |
| Amiga      | `.lha`  | a directory is known, its file names are not        |
| Amiga      | `.hdf`  | nothing established — PUAE's mode could not be read |
| Amiga CD32 | `.chd`  | `<stem>.nvr`                                        |
| Amiga CD32 | `.bin`  | nothing established                                 |
| Sega CD    | `.chd`  | a shared BRAM card (`scd_E.brm`, …)                 |
| Sega CD    | `.bin`  | per-game `<stem>.srm`                               |

That is why every question carries the ROM's **real** content path: `RomInstall.file_path` for an installed ROM, and the
path built from `roms.fs_name` for one the library holds but has not installed. A synthetic stem would answer a
different question and look like an answer to this one. Where no path can be formed at all, the answer is "not
established", never a guess.

### Named right, looked for in the wrong place

A per-game answer is not yet a save the plugin **finds**. The names now come from the resolver; the DIRECTORY still
comes from this repo's own `resolve_save_dir`, and for two systems the two disagree:

| System  | The emulator's directory   | Where the plugin looks |
| ------- | -------------------------- | ---------------------- |
| 3DO     | `saves/3do/opera/per_game` | `saves/3do`            |
| Neo Geo | `saves/neogeo/fbneo`       | `saves/neogeo`         |

So **3DO and Neo Geo saves are still not found**, exactly as before this change — what improved is that the plugin now
knows their names. Neither is a regression, and neither is fixed until the path math is retired. That is deliberately a
separate change: discovery and the save-sort migration must agree on the directory, and moving one without the other
reopens the race the migration's markers exist to prevent. The
[Save sync support matrix](../user-guide/save-sync-support-matrix.md) reports both as not syncing.

**Cost.** A live reading is roughly 170 ms warm and 490 ms cold per ROM on the reference device. A single-ROM sync and a
status read each take one, whether or not the ROM's slot is confirmed: the sync's entry gate reads the answer to decide
whether to refuse at all, and hands that same reading both to the matrix and to the negotiate session's inventory rather
than letting either take a second. "Ask live" is a rule about operations, not about layers.

The whole-library sweep is the exception, at **two per ROM**. It posts one device-wide inventory before its per-ROM loop
begins, and that inventory walks each confirmed ROM's save files — so it reads every answer once before any ROM's run
exists to hand one to, and each run then takes its own. Reusing the inventory's readings would mean carrying a per-run
map of them across the loop, which is a cache in everything but name on the one path where nothing is launched
afterwards; the sweep is a background operation and pays the second reading instead.

The two per-platform loops — `count_platform_saves` and `delete_platform_saves` — take one per **installed** ROM on that
platform, so four installed games is well under a second and fifty is several. Nothing is cached: the correctness rule
is that every sync path asks live, and the count exists so the number the button offers equals the number the delete
removes.

## The five save states

The answer classifies every ROM into **exactly one** of five states. Only the first is a save this plugin can carry; the
other four are refusals, and each says something different about why. A refusal costs nothing: no path is probed, no
sync state is written, and the sync returns the benign-skip shape (`reason: "save_shape_unsupported"`) rather than a
failure — the same shape the `savefiles_in_content_dir` skip returns.

| State                  | What it means                                                                 | Example on a stock RetroDECK        |
| ---------------------- | ----------------------------------------------------------------------------- | ----------------------------------- |
| **per-game files**     | The answer names concrete files with no hole. Sync as usual, any number.      | Game Boy Advance, Saturn            |
| **shared**             | One card or file that many games write, so per-game sync would overwrite.     | PS2, and a Sega CD disc image       |
| **inside the content** | The save is written into the game file itself; there is nothing separate.     | an Amiga `.adf`                     |
| **hole**               | The shape is known, but part of the path or name is the game's own identity.  | Dreamcast, GameCube, 3DS, Wii U     |
| **not established**    | Nobody established what this emulator writes, or the names in a known folder. | MAME, PSP, ScummVM, unaudited cores |

**The hole is not always in a file name.** Flycast's Dreamcast cards need the game's `save_id` in the filename;
Dolphin's GameCube memory cards need the game's `region` in the DIRECTORY. Either way the plugin cannot complete the
path, which is what the state is about.

**The last state has three shapes and they are kept apart**, because they are three different sentences to a reader.
`nothing_established` — nobody has established what this emulator writes. `directory_known` — the directory is known and
the file names in it are not, and telling a user "nothing is known" about a folder we can point at would be wrong.
`not_asked` — no question ever reached the resolver, because RetroArch writes saves to the content directory or no
emulator resolved for this ROM at all; the emulator is not implicated, and saying it is would be wrong too.

**Scope is the emulator, never the platform.** PS2 is not unsupported — standalone PCSX2 is, and a libretro core for the
same platform can answer differently. Every state the payload carries names the emulator it is about.

## Progress and configuration

The answer states each file's **role**, and a file whose role is the emulator's configuration rather than the player's
progress is **never synced**: it is machine-local by nature, so carrying it to another device would overwrite settings
the user chose there. On a stock RetroDECK the only one that reaches this rule is Saturn's `.smpc` console-settings
file: MAME states a per-game `.cfg` as well, but its answer is not-established, so the sync refuses that ROM before any
role is consulted.

Such a file is still named on the wire, flagged `carried: false`, so a page can say "this file exists and we
deliberately leave it alone" rather than simply not showing it. A **directory move** — the save-sort migration — does
carry it, because splitting one save across two directories breaks the game as surely as leaving the battery file
behind.

**The rule names the roles to hold back, never the roles to carry**, and that is a decision rather than the shape it
happens to have. The resolver has a value for a file on the machine that no declaration describes (`unknown`), and a
component carries no role at all where no group claimed it — two ways of saying nobody said what this file is, and both
are carried. The two failures are not symmetric: a settings file carried onto another device costs a setting the user
can make again, and a battery file left behind costs a save nothing can restore. An allow-list of the roles known today
inverts exactly that, and it fails silently — on the day upstream names a new role, every file carrying it is dropped
and nothing says so.

## How RomM stores saves

RomM treats a save as a file blob keyed by `(rom_id, slot)`, with an `emulator` tag (which becomes a storage
subdirectory) and an MD5 `content_hash`. It stores the bytes the client sends and leaves their meaning to the client —
there is no `save_type` or memory-card concept, and each save belongs to a single ROM. This format-agnostic design keeps
the server simple and works for any client; RomM's own in-browser player (EmulatorJS) uses the same path, uploading one
save blob per `(game, core)`.

Because both the server and our discovery are organised **per game**, two things follow. Supporting a new _per-game_
save format is a client-side change — the server already accepts the file as-is. And a _shared_ card has no per-game
identity to map onto a `(rom_id, slot)` record, so any shared-card handling is a client-side modelling decision (see
below), not something the server provides or prevents.

## The three coverage classes

Every core's save behavior falls into one of these (plus "no save"):

| Class   | Shape                                                                                  | What it needs                                                                                                                 |
| ------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **(a)** | Per-game, single-token `<rom>.<ext>`, save folder                                      | Just add `<ext>` to the override map. No architecture change.                                                                 |
| **(b)** | Per-game, but a **slot/unit infix** (`<rom>.1.mcr`, `<rom>.A1.bin`)                    | Infix-aware discovery **and** download-target derivation (the current code drops the infix), plus multi-file-per-ROM support. |
| **(c)** | **Shared** card (one file, many games), **fixed** name, or **outside** the save folder | Breaks the per-game model. Not solvable by an extension — see strategy below.                                                 |

Class (a) is pure upside. Class (b) is bounded engineering that stays inside the per-game model. Class (c) is the
genuinely hard one.

## Strategy for class (c)

The emulation ecosystem has already converged on the answer, and it aligns with this project's "no assumptions, the user
decides on ambiguity" stance:

- **No tool merges binary card images.** Block-level merge of a shared `.mcr`/`.ps2`/`.raw` is a confirmed dead end. The
  only options are _isolate_ (per-game) or _pick-one_ (whole-file).
- **Prefer per-game mode.** Modern emulators default to it (DuckStation per-game cards; Beetle PSX / PCSX ReARMed slot
  0; PCSX2 Folder Memory Cards; Dolphin GCI folders). Where the launch core supports per-game cards, that path collapses
  class (c) into class (a)/(b) and maps cleanly onto per-game sync.
- **For an unavoidable shared card, treat it as one device-global blob:** whole-file, last-writer-wins, with conflict
  **detection that warns instead of clobbering** (the Ludusavi model), and rely on the existing version history as the
  recovery net. Never present it as per-game; never merge.
- **Sync SRAM, not save states.** Save states are core- and version-coupled and not portable across devices; in-game
  saves are.
- **The per-game card format is emulator-specific.** A per-game card written by the RetroArch PCSX2 core is not
  byte-compatible with standalone PCSX2. The sync key/format must match the core the user actually launches with — which
  is exactly what RomM's `emulator` subdirectory captures.

## Roadmap mapping

- **(a)** — nothing to add: the resolver names these files already. Verification tracked in
  [#237](https://github.com/danielcopper/decky-romm-sync/issues/237).
- **(b)** — infix-aware per-game discovery (PS1 multi-card, Flycast per-game VMU, 3DO NVRAM); research in
  [#237](https://github.com/danielcopper/decky-romm-sync/issues/237), implementation under the save-format epic
  [#255](https://github.com/danielcopper/decky-romm-sync/issues/255).
- **(c)** — shared/system-dir handling under [#255](https://github.com/danielcopper/decky-romm-sync/issues/255) (save
  formats), [#151](https://github.com/danielcopper/decky-romm-sync/issues/151) (Dreamcast VMU), and
  [#129](https://github.com/danielcopper/decky-romm-sync/issues/129) (standalone emulators) — all v2.0.

## Evidence level, and where the audit has since disagreed

The classification below was a desk audit: libretro documentation plus core source reading, one pass, no per-core
observation. [emu-atlas](https://github.com/danielcopper/emu-atlas) is auditing the same ground with an explicit
evidence grade per core (source / binary / live), and as of its 2026-07-24 snapshot has reached **17 of RetroDECK's 159
libretro cores and none of the 22 standalone emulators**. Treat an unrevised row here as a documented expectation, not a
verified fact.

Every core the atlas audit has reached and disagreed with, corrected here:

| Core      | Was           | Audit finding                                                                                                                                                                                                                                        |
| --------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `opera`   | ✅ `.srm`     | Wrong. `RETRO_MEMORY_SAVE_RAM` returns NULL — there is no RetroArch-side `.srm` at all. NVRAM goes to `<save_dir>/opera/per_game/<rom_stem>.<version>.srm`, the version coming from `opera_nvram_version`. Per-game, but subdir + infix → class (b). |
| `flycast` | 🔴 (c) shared | Shared is the shipped default (`reicast_per_content_vmus=disabled` → `<system_dir>/dc/vmu_save_A1.bin` …). The core is **per-game capable**: the `VMU A1` / `All VMUs` modes root at `savefile_directory`; their filename scheme is unestablished.   |
| `pcsx2`   | 🔴 (c) shared | Shared is the shipped default (`pcsx2_shared_memory_cards=enabled` → `<system_dir>/pcsx2/memcards/Mcd00{1,2}.ps2`). Disabled, slot 1 is `<save_dir>/<rom_stem>.ps2` — **class (a)**, which the resolver answers on its own.                          |
| `neocd`   | 🔴 (c) shared | Per-game capable: `path.cpp:137-168` proves a per-content mode alongside the frontend save RAM path. Load precedence between the two is unobserved.                                                                                                  |
| `ppsspp`  | 🔴 (c) shared | Not established. The shipped sort override is known; the save subtree and its granularity are not.                                                                                                                                                   |
| `dolphin` | 🔴 (c) `.gci` | Not established. RetroDECK's prepared `dolphin-emu` subtree suggests a different root, but no core-written save has been observed.                                                                                                                   |
| `azahar`  | absent        | RetroDECK now ships Azahar for `n3ds` where this table still listed Citra. Verdict suspect — prepared save subtree suggests a deviation, nothing observed.                                                                                           |

Two of these are strategically relevant. `pcsx2` and `flycast` are not class (c) by nature — they are class (c) _as
configured_, which is exactly the "prefer per-game mode" path below, and it makes PS2 the cheapest of the shared-card
systems to support. The `opera` correction runs the other way: 3DO was published as syncing and does not.

The row set below is also a 2026-06-04 snapshot of RetroDECK's core list and has drifted (Azahar is one instance).

## Full core reference

Per-core classification from the audit of every RetroArch core RetroDECK can launch. **This table predates the live
resolver and is background reading — your machine's own answer governs.** `✅` per-game, in the save folder · `🟡 (a)`
per-game, needing verification · `🟠 (b)` per-game with a slot/unit infix · `🔴 (c)` shared / out-of-folder · `⚪` no
battery save · `❓` unverified. Non-`.srm` rows are libretro-documented and await on-device confirmation. Snapshot:
2026-06-04, with the rows above revised 2026-08-02.

??? note "All 156 cores, classified"

    | Core | Status | Save file(s) | Naming / dir | Used by (slugs) |
    | --- | --- | --- | --- | --- |
    | `boom3_libretro` | 🔴 (c) | `.save` | system_dir / content | doom |
    | `boom3_xp_libretro` | 🔴 (c) | `.save` | system_dir / content | doom |
    | `cannonball_libretro` | 🔴 (c) | `.xml` | shared_fixed_name / system | ports |
    | `cap32_libretro` | 🔴 (c) | `.sna` | single_token_nonstandard / content | amstradcpc, gx4000 |
    | `dosbox_svn_libretro` | 🔴 (c) | — | system_dir / saves | dos, pc |
    | `easyrpg_libretro` | 🔴 (c) | `.lsd`, `.lyn`, `.dyn`, `.lgs` | single_token_nonstandard / content | easyrpg |
    | `flycast_libretro` | 🔴 (c) | `.bin` | shared_fixed_name / system | arcade, atomiswave, consolearcade, dreamcast, mame +3 |
    | `mame2000_libretro` | 🔴 (c) | `.nv` | shared_fixed_name / system | arcade, cps, cps1, cps2, cps3 +1 |
    | `mame2003_libretro` | 🔴 (c) | `.nv` | shared_fixed_name / saves | arcade, cps, cps1, cps2, cps3 +1 |
    | `mame2003_plus_libretro` | 🔴 (c) | — | single_token_nonstandard / saves | arcade, cps, cps1, cps2, cps3 +1 |
    | `mame2010_libretro` | 🔴 (c) | `.nv` | system_dir / saves | arcade, cps, cps1, cps2, cps3 +1 |
    | `mame_libretro` | 🔴 (c) | `.nv` | system_dir / saves | apple2, apple2gs, arcade, arcadia, astrocde +29 |
    | `mess2015_libretro` | 🔴 (c) | — | system_dir / system | mess |
    | `neocd_libretro` | 🔴 (c) | `.srm` | shared_fixed_name / saves | neogeocd, neogeocdjp |
    | `openlara_libretro` | 🔴 (c) | `.dat` | shared_fixed_name / saves | ports |
    | `pcsx2_libretro` | 🔴 (c) | `.ps2` | shared_fixed_name / system | ps2 |
    | `prboom_libretro` | 🔴 (c) | `.dsg` | shared_fixed_name / saves | doom |
    | `px68k_libretro` | 🔴 (c) | — | system_dir / system | x68000 |
    | `scummvm_libretro` | 🔴 (c) | — | system_dir / system | scummvm |
    | `vitaquake2-rogue_libretro` | 🔴 (c) | — | shared_fixed_name / content | quake |
    | `vitaquake2-xatrix_libretro` | 🔴 (c) | — | shared_fixed_name / content | quake |
    | `vitaquake2-zaero_libretro` | 🔴 (c) | — | shared_fixed_name / content | quake |
    | `vitaquake2_libretro` | 🔴 (c) | — | shared_fixed_name / content | quake |
    | `mednafen_psx_hw_libretro` | 🟠 (b) | `.srm`, `.mcr` | infix / saves | psx |
    | `mednafen_psx_libretro` | 🟠 (b) | `.srm`, `.mcr` | infix / saves | psx |
    | `opera_libretro` | 🟠 (b) | `.srm` | version infix / saves subdir `opera/per_game` | 3do |
    | `pcsx_rearmed_libretro` | 🟠 (b) | `.srm`, `.mcd` | infix / saves | psx |
    | `swanstation_libretro` | 🟠 (b) | `.mcd`, `.mcr` | infix / saves | psx |
    | `tyrquake_libretro` | 🟠 (b) | `.sav` | infix / saves | quake |
    | `virtualjaguar_libretro` | 🟠 (b) | `.srm`, `.cdrom.srm` | infix / saves | atarijaguar |
    | `dosbox_pure_libretro` | 🟡 (a) | `.pure.zip`, `.save.zip` | single_token_nonstandard / saves | dos, pc, windows3x, windows9x |
    | `kronos_libretro` | 🟡 (a) | `.ram` | single_token_nonstandard / saves | arcade, consolearcade, mame, saturn, saturnjp +1 |
    | `nxengine_libretro` | 🟡 (a) | `.dat` | single_token_nonstandard / saves | ports |
    | `retro8_libretro` | 🟡 (a) | `.p8d.txt` | single_token_nonstandard / saves | pico8 |
    | `ardens_libretro` | ❓ | — | unknown / unknown | arduboy |
    | `azahar_libretro` | ❓ | — | unknown / unknown | n3ds |
    | `citra2018_libretro` | ❓ | — | unknown / unknown | n3ds |
    | `citra_libretro` | ❓ | — | unknown / unknown | n3ds |
    | `dolphin_libretro` | ❓ | `.gci`, `.gcs` | unknown / unknown | gc, wii |
    | `panda3ds_libretro` | ❓ | — | unknown / unknown | n3ds |
    | `ppsspp_libretro` | ❓ | — | unknown / unknown | psp |
    | `same_cdi_libretro` | ❓ | — | unknown / unknown | cdimono1 |
    | `wasm4_libretro` | ❓ | — | unknown / unknown | wasm4 |
    | `DoubleCherryGB_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | gb, gbc |
    | `blastem_libretro` | ✅ | `.srm` | single_token_default / saves | genesis, megadrive, megadrivejp |
    | `bsnes-jg_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | satellaview, sfc, snes, snesna, sufami |
    | `bsnes_hd_beta_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | satellaview, sfc, snes, snesna, sufami |
    | `bsnes_libretro` | ✅ | `.srm` | single_token_default / saves | gb, gbc, satellaview, sfc, snes +2 |
    | `bsnes_mercury_accuracy_libretro` | ✅ | `.srm` | single_token_default / saves | satellaview, sfc, snes, snesna, sufami |
    | `desmume2015_libretro` | ✅ | `.dsv` | single_token_default / saves | nds |
    | `desmume_libretro` | ✅ | `.dsv` | single_token_default / saves | nds |
    | `fbneo_libretro` | ✅ | `.nv`, `.fs` | single_token_default / saves | arcade, cps, cps1, cps2, cps3 +5 |
    | `fceumm_libretro` | ✅ | `.srm` | single_token_default / saves | famicom, fds, nes |
    | `gambatte_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | gb, gbc |
    | `gearboy_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | gb, gbc |
    | `gearcoleco_libretro` | ✅ | `.srm` | single_token_default / saves | colecovision |
    | `geargrafx_libretro` | ✅ | `.srm` | single_token_default / saves | supergrafx |
    | `gearsystem_libretro` | ✅ | `.srm` | single_token_default / saves | gamegear, mark3, mastersystem, multivision, sg-1000 |
    | `genesis-plus-gx-expanded-rom-size-paprium_libretro` | ✅ | `.srm` | single_token_default / saves | megadrive, megadrivejp |
    | `genesis_plus_gx_libretro` | ✅ | `.srm`, `.brm` | single_token_default / saves | gamegear, genesis, mark3, mastersystem, megacd +5 |
    | `genesis_plus_gx_wide_libretro` | ✅ | `.srm`, `.brm` | single_token_default / saves | gamegear, genesis, mark3, mastersystem, megacd +5 |
    | `geolith_libretro` | ✅ | `.srm`, `.nv`, `.mcr` | single_token_default / saves | arcade, mame, neogeo |
    | `gpsp_libretro` | ✅ | `.srm` | single_token_default / saves | gba |
    | `mednafen_ngp_libretro` | ✅ | `.flash` | single_token_default / saves | ngp, ngpc |
    | `mednafen_pce_fast_libretro` | ✅ | `.srm` | single_token_default / saves | pcengine, pcenginecd, tg16, tg-cd |
    | `mednafen_pce_libretro` | ✅ | `.srm` | single_token_default / saves | pcengine, pcenginecd, supergrafx, tg16, tg-cd |
    | `mednafen_pcfx_libretro` | ✅ | `.srm` | single_token_default / saves | pcfx |
    | `mednafen_saturn_libretro` | ✅ | `.bkr`, `.bcr`, `.smpc` | single_token_default / saves | saturn, saturnjp |
    | `mednafen_supafaust_libretro` | ✅ | `.srm` | single_token_default / saves | sfc, snes, snesna |
    | `mednafen_supergrafx_libretro` | ✅ | `.srm` | single_token_default / saves | supergrafx, tg16 |
    | `mednafen_vb_libretro` | ✅ | `.srm` | single_token_default / saves | virtualboy |
    | `mednafen_wswan_libretro` | ✅ | `.srm` | single_token_default / saves | wonderswan, wonderswancolor |
    | `melonds_libretro` | ✅ | `.sav` | single_token_default / content | nds |
    | `melondsds_libretro` | ✅ | `.srm` | single_token_default / saves | nds |
    | `mesen-s_libretro` | ✅ | `.srm` | single_token_default / saves | gb, gbc, satellaview, sfc, sgb +2 |
    | `mesen_libretro` | ✅ | `.srm` | single_token_default / saves | famicom, fds, nes |
    | `mgba_libretro` | ✅ | `.srm` | single_token_default / saves | gb, gba, gbc, sgb |
    | `mupen64plus_next_libretro` | ✅ | `.srm` | single_token_default / saves | n64, n64dd |
    | `nestopia_libretro` | ✅ | `.srm` | single_token_default / saves | famicom, fds, nes |
    | `noods_libretro` | ✅ | `.srm` | single_token_default / saves | gba |
    | `parallel_n64_libretro` | ✅ | `.srm` | single_token_default / saves | n64, n64dd |
    | `picodrive_libretro` | ✅ | `.srm` | single_token_default / saves | gamegear, genesis, mark3, mastersystem, megacd +7 |
    | `pokemini_libretro` | ✅ | `.eep` | single_token_default / saves | pokemini |
    | `potator_libretro` | ✅ | `.srm` | single_token_default / saves | supervision |
    | `puae2021_libretro` | ✅ | `.nvr` | single_token_default / saves | amiga, amiga1200, amiga600, amigacd32, cdtv |
    | `puae_libretro` | ✅ | `.nvr` | single_token_default / saves | amiga, amiga1200, amiga600, amigacd32, cdtv |
    | `quasi88_libretro` | ✅ | `.srm` | single_token_default / saves | pc88 |
    | `quicknes_libretro` | ✅ | `.srm` | single_token_default / saves | famicom, nes |
    | `race_libretro` | ✅ | `.ngf` | single_token_default / saves | ngp, ngpc |
    | `sameboy_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | gb, gbc, sgb |
    | `sameduck_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | megaduck |
    | `smsplus_libretro` | ✅ | `.srm` | single_token_default / saves | gamegear, mark3, mastersystem |
    | `snes9x2005_plus_libretro` | ✅ | `.srm` | single_token_default / saves | satellaview, sfc, snes, snesna, sufami |
    | `snes9x2010_libretro` | ✅ | `.srm` | single_token_default / saves | satellaview, sfc, snes, snesna, sufami |
    | `snes9x_libretro` | ✅ | `.srm` | single_token_default / saves | satellaview, sfc, snes, snesna, sufami |
    | `stella2014_libretro` | ✅ | `.srm` | single_token_default / saves | atari2600 |
    | `stella2023_libretro` | ✅ | `.srm` | single_token_default / saves | atari2600 |
    | `stella_libretro` | ✅ | `.srm` | single_token_default / saves | atari2600 |
    | `tgbdual_libretro` | ✅ | `.srm`, `.rtc` | single_token_default / saves | gb, gbc |
    | `tic80_libretro` | ✅ | `.srm` | single_token_default / saves | tic80 |
    | `vba_next_libretro` | ✅ | `.srm` | single_token_default / saves | gba |
    | `vbam_libretro` | ✅ | `.srm` | single_token_default / saves | gb, gba, gbc |
    | `vice_x128_libretro` | ✅ | `.nvr`, `.d64`, `.d71`, `.d81` | single_token_default / saves | c64 |
    | `vice_x64_libretro` | ✅ | `.nvr`, `.d64`, `.d71`, `.d81` | single_token_default / saves | c64 |
    | `vice_x64sc_libretro` | ✅ | `.nvr`, `.d64`, `.d71`, `.d81` | single_token_default / saves | c64 |
    | `vice_xplus4_libretro` | ✅ | `.nvr`, `.d64`, `.d71`, `.d81` | single_token_default / saves | plus4 |
    | `vice_xscpu64_libretro` | ✅ | `.nvr`, `.d64`, `.d71`, `.d81` | single_token_default / saves | c64 |
    | `vice_xvic_libretro` | ✅ | `.nvr`, `.d64`, `.d71`, `.d81` | single_token_default / saves | vic20 |
    | `yabasanshiro_libretro` | ✅ | `.bkr`, `.bcr`, `.smpc` | single_token_default / saves | saturn, saturnjp |
    | `yabause_libretro` | ✅ | `.srm` | single_token_default / saves | saturn, saturnjp |
    | `81_libretro` | ⚪ | — | none / none | zx81 |
    | `a5200_libretro` | ⚪ | — | none / none | atari5200 |
    | `arduous_libretro` | ⚪ | — | none / none | arduboy |
    | `atari800_libretro` | ⚪ | — | none / none | atari5200, atari800, atarixe |
    | `b2_libretro` | ⚪ | — | none / none | bbcmicro |
    | `bluemsx_libretro` | ⚪ | — | none / none | colecovision, msx, msx1, msx2, msxturbor +2 |
    | `cdi2015_libretro` | ⚪ | — | none / none | cdimono1 |
    | `chailove_libretro` | ⚪ | — | none / none | chailove |
    | `crocods_libretro` | ⚪ | — | none / none | amstradcpc, gx4000 |
    | `dice_libretro` | ⚪ | — | none / none | arcade, mame |
    | `dirksimple_libretro` | ⚪ | — | none / none | daphne, laserdisc |
    | `dosbox_core_libretro` | ⚪ | — | none / none | dos, pc |
    | `ecwolf_libretro` | ⚪ | — | none / none | ports |
    | `fbalpha2012_cps1_libretro` | ⚪ | — | none / none | cps, cps1, fba |
    | `fbalpha2012_cps2_libretro` | ⚪ | — | none / none | cps, cps2, fba |
    | `fbalpha2012_cps3_libretro` | ⚪ | — | none / none | cps, cps3, fba |
    | `fbalpha2012_libretro` | ⚪ | — | none / none | arcade, cps, cps1, cps2, cps3 +2 |
    | `fbalpha2012_neogeo_libretro` | ⚪ | — | none / none | fba |
    | `fmsx_libretro` | ⚪ | — | none / none | msx, msx1, msx2 |
    | `freechaf_libretro` | ⚪ | — | none / none | channelf |
    | `freeintv_libretro` | ⚪ | — | none / none | intellivision |
    | `frodo_libretro` | ⚪ | — | none / none | c64 |
    | `fuse_libretro` | ⚪ | — | none / none | zxspectrum |
    | `gw_libretro` | ⚪ | — | none / none | gameandwatch, lcdgames |
    | `handy_libretro` | ⚪ | — | none / none | atarilynx |
    | `hatari_libretro` | ⚪ | — | none / none | atarist |
    | `holani_libretro` | ⚪ | — | none / none | atarilynx |
    | `lowresnx_libretro` | ⚪ | — | none / none | lowresnx |
    | `lutro_libretro` | ⚪ | — | none / none | lutro |
    | `mednafen_lynx_libretro` | ⚪ | — | none / none | atarilynx |
    | `mojozork_libretro` | ⚪ | — | none / none | zmachine |
    | `mrboom_libretro` | ⚪ | — | none / none | ports |
    | `mu_libretro` | ⚪ | — | none / none | palm |
    | `nekop2_libretro` | ⚪ | — | none / none | pc98 |
    | `np2kai_libretro` | ⚪ | — | none / none | pc98 |
    | `o2em_libretro` | ⚪ | — | none / none | odyssey2, videopac |
    | `prosystem_libretro` | ⚪ | — | none / none | atari7800 |
    | `squirreljme_libretro` | ⚪ | — | none / none | j2me |
    | `superbroswar_libretro` | ⚪ | — | none / none | ports |
    | `theodore_libretro` | ⚪ | — | none / none | moto, to8 |
    | `uzem_libretro` | ⚪ | — | none / none | uzebox |
    | `vecx_libretro` | ⚪ | — | none / none | vectrex |
    | `vircon32_libretro` | ⚪ | — | none / none | vircon32 |
    | `virtualxt_libretro` | ⚪ | — | none / none | dos, pc |
    | `vitaquake3_libretro` | ⚪ | — | none / none | quake |
    | `x1_libretro` | ⚪ | — | none / none | x1 |
