# Core and Emulator Selection

## Overview

A RomM game launches through RetroDECK on some **emulator** — most often a **RetroArch libretro core**, but for a few
platforms (PS2, PS3, …) a **standalone emulator** (PCSX2, RPCS3) that ES-DE lists as the working default. Most games use
their platform's default emulator, but the user can pin a different core for a single game (a **per-game** emulator
override) or for a whole platform (a **per-platform** emulator override). This page documents how Tender decides which
emulator a game uses, where that decision is stored, and how it is applied at launch.

The central rule: **the read-path core equals the launched core.** Whatever core Tender reports for a game — in the
BIOS-requirement filter, the save-directory name, the save-sync core tag, the core-change warning, the game-detail badge
— is the exact core that game will launch on. A single resolver guarantees that, and every start asks for the command of
the same resolved entry. Tender **owns emulator selection end to end**: it reads RetroDECK/ES-DE configuration for the
default emulator, but its own launches never depend on ES-DE's `gamelist.xml` — it neither reads nor writes that file.
The **live `es_systems.xml` is the sole source** for the system-layer default and the picker's emulator list; there is
no bundled snapshot (the curated `core_defaults.json` and its generator were deleted in #1210). What **reads** that file
is the vendored emu-atlas resolver, through `adapters/atlas_catalogue.py` — Tender's own parser is gone (#1840). See
[ADR-0011](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0011-per-game-core-override-in-db-applied-via-e-flag.md)
(the per-game DB override + `-e`),
[ADR-0012](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0012-plugin-owns-core-selection-always-e-no-gamelist.md)
(per-platform core in `settings.json`, always `-e`, gamelist dropped),
[ADR-0020](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0020-live-es-systems-emulator-resolution.md)
(live `es_systems.xml` as the only source; the default is the first entry in declared order, as amended), and
[ADR-0030](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0030-the-emulator-catalogue-is-read-by-the-vendored-resolver.md)
(the resolver is what reads it; its overlays are honoured, a choice made in ES-DE never becomes Tender's default, and,
as amended, the rows keep the resolver's order with that choice shown) for the decision records.

### What an entry launches: a core, a program, or a core this machine cannot load

What a catalogue entry launches, the core it loads and where that core lies are the resolver's statements (`kind`,
`core_so`, `core_path` on the catalogue entry), read in `adapters/atlas_catalogue.py` and carried in Tender's own words
on `domain.emulator_commands.EmulatorOption.kind` and on the picker payload's `kind`:

- **`core`** — RetroArch loads a core of this machine; `core_so` is its bare `.so` name. This is the
  overwhelming-majority path, and it includes a command that passes RetroArch more than its core, such as FinalBurn
  Neo's `--subsystem neocd` on Neo Geo CD.
- **`program`** — an emulator program of its own (PS2 → PCSX2, PS3 → RPCS3, GameCube/Wii → Dolphin, PSP → PPSSPP, …);
  `core_so` is `None`.
- **`foreign_core`** — a RetroArch launch naming a core file this machine cannot load (EmuDeck's `n3ds` overlay names
  Windows `.dll` cores). It is marked and nothing more: whether it starts is the resolver's command's answer, as for any
  entry.

Tender reads none of this off the command text. The resolved emulator is a pure value object —
`domain.emulator_commands.EmulatorOption` — carrying the entry's label, its kind, its emulator identity and the
`core_so` of a core entry for the `.so`-space consumers. How the entry starts is not on it: every start asks the
resolver for its command ([Starting a game](#starting-a-game-the-resolvers-command)).

Every kind is first-class throughout: the **default** may be any of them (the entry its source ships first), and the
per-game / per-platform picker lists them all — so a pin may name an emulator program OR a core (#1210). A pin is stored
as the emulator **LABEL** with the emulator source it was chosen from, and resolved through the same classified option
list at use time.

An emulator program has **no** libretro `.so`, so the read-path projection reports `core_so = None` and every
`.so`-space consumer degrades exactly as it does for an unconfigured platform (see
[the single read seam](#the-single-read-seam-activecoreresolver)).

## The two override scopes

Tender owns two **deviations** from the RetroDECK default core. Each stores only the deviation as a core LABEL; absence
means "follow the default."

| Scope            | Stored where                                                                                   | Applies to              | Written by                                                                                                                     |
| ---------------- | ---------------------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **Per-game**     | Tender's database — `roms.emulator_override` (nullable LABEL), `roms.emulator_override_source` | one ROM (by `rom_id`)   | Tender (`pin`/`clear`)                                                                                                         |
| **Per-platform** | `settings.json` — `platform_cores` map (`{slug: {"label", "source"}}`)                         | every ROM on a platform | Tender (`set_system_core`; the firmware listing's refiling moves an override filed under a server folder name to its platform) |

Both overrides are Tender's own state and live in Tender's own stores. Neither is written into ES-DE's `gamelist.xml` —
Tender **never writes that file**. It still **reads** the RetroDECK/ES-DE configuration it does not own (the live
`es_systems.xml` — the system default emulator and the full classified command list the picker offers), but the per-game
and per-platform deviations are layered on top of that read by Tender itself.

## Storage: the per-game override is a LABEL on the `Rom` aggregate

`roms.emulator_override` is a nullable `TEXT` column added by migration `002_add_emulator_override.sql`. It holds the
core **LABEL** the user picked (e.g. `"Beetle PSX HW"`), exactly as ES-DE displays it — never a resolved `.so` filename.

- **`NULL` = no override** → the game follows the RetroDECK/ES-DE default.
- It anchors on `roms`, not `rom_installs`, so the choice **survives uninstall/reinstall** (per
  [ADR-0007](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0007-rom-retention-identity-anchor.md)).
- `roms.emulator_override_source` (migration `039`) holds the kind of the emulator source whose list the label was
  chosen from (`retrodeck`, `emudeck`), so a label that disappears from that source can be named with it. The migration
  recorded `retrodeck` beside every pick stored before it, the only source Tender started through then, so the column is
  `NULL` exactly where there is no pick ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D20).
- Mutations go through the verb-named aggregate methods `Rom.pin_emulator_override(label, source=…)` (rejects a blank
  label; the source is required) and `Rom.clear_emulator_override()`, which drops both. Only `pin`/`clear` ever write
  the two columns, through `set_emulator_override(rom_id, label, source)`; they are **excluded from the sync UPSERT
  `SET` clause**, so a re-sync never wipes a user's pin.

Tender stores the **deviation** (the LABEL, or `NULL`), not a resolved core. The default and system layers are owned by
RetroDECK/ES-DE and change externally — a RetroDECK update can ship a new default emulator — so a stored resolved value
would go stale. Storing only the deviation keeps Tender authoritative over exactly the slice it owns and re-resolves the
rest live. The LABEL is looked up in the live-`es_systems.xml` option list at use time:
`domain.emulator_commands.option_for_label` finds the entry it names — so a per-game pin may name an emulator program,
not only a core (#1210).

## Storage: the per-platform core is a LABEL in `settings.json`

The per-platform core lives in a `platform_cores` map in `settings.json` — `{platform_slug: {"label", "source"}}`, an
empty map by default (`DEFAULT_SETTINGS` in `adapters/persistence.py`). It holds the same kind of value as the per-game
pin: the core **LABEL**, never a resolved `.so`, with the kind of the source it was chosen from
(`domain/emulator_pick.py`). Settings version 14 turned each bare label of an older file into that shape with
`"source": "retrodeck"`, as migration `039` did for the database; a value of any other shape is no pick. An **absent
key** means "no per-platform deviation — follow the default for this platform."

It is an
[ADR-0003](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0003-json-sqlite-persistence-boundary.md)
**bucket-1** value: a flat, user-set, relationship-free intent toggle. So it lives in `settings.json`, **not** SQLite,
and there is **no `Platform` aggregate** — consistent with the `platform_slug`-is-denormalized stance. The map starts
empty: there is no seed and no import from any previously-set ES-DE gamelist core (see
[No migration](#no-migration-re-apply-once)). Tender reads it through the `PlatformCoreReader` Protocol
(`PlatformCoreReaderAdapter` in `adapters/persistence.py`), which holds the **live** settings dict so a fan-out after a
write resolves the freshly-written value rather than a stale snapshot.

## The single read seam: `ActiveCoreResolver`

`ActiveCoreResolver` (`backend/services/active_core_resolver.py`) is the one place that answers "which emulator will
this ROM actually launch with?" It exposes three methods over the **same** three-layer resolution:

- **`resolve_rom(rom_id) -> ResolvedEmulator | None`** — the choice (`EmulatorChoice`: the entry in effect, the layer it
  came from, and a pick that is gone) beside the system's options, or `None` where no system is found. The start check
  and the game page read it.
- **`active_emulator_for_rom(rom_id) -> EmulatorOption | None`** — the entry in effect (libretro core OR standalone), or
  `None` where none is.
- **`active_core_for_rom(rom_id) -> (core_so, label)`** — the **read-path projection** of the above, kept for the
  `.so`-space consumers. A libretro emulator yields `(core_so, label)`; a **standalone** emulator yields
  `(None, label)`; an unresolvable platform yields `(None, None)`. Consumers already degrade on a `None` core, so a
  standalone launch never breaks them.

The precedence is the invariant:

> **per-game DB `emulator_override` (top) → per-platform `settings.json` `platform_cores` → the default, the entry the
> source ships first → no entry, and the game does not start (#2195 D43).**

```text
resolve_rom(rom_id):
  rom, install = read roms + rom_installs rows             ── one UoW read
  system = rom_system(rom.platform_slug, install)          ── the install's system, else the platform's (below)
  if system is None: return None                           ── no switched-on system: no entry
  options = get_emulator_options(system)["options"]        ── every es_systems entry, in the resolver's order
  choice = choose_emulator(options, game=pin, platform=platform_cores[slug])
                                                           ── a stored label stands while it names an entry, whatever
                                                           ── its state; a label that is gone gives way, named
  return choice, options

active_emulator_for_rom(rom_id) = resolve_rom(rom_id).choice.option
active_core_for_rom(rom_id):                               ── the .so-space projection
  e = active_emulator_for_rom(rom_id)
  return (None, None) if e is None else (e.core_so, e.label)
```

`domain/emulator_choice.py::choose_emulator` takes the per-game pin, else the per-platform pick, else the default
(`domain.emulator_commands.default_option`). A stored pick takes part only while its source is the one games start
through (`domain/emulator_pick.py::pick_for`): otherwise the default applies, the pick is kept as stored and nothing is
said, and a label is only ever checked against its own source's list
([#2222](https://github.com/danielcopper/romm-tender/issues/2222) D5). **A stored pick stands while its label still
names an entry, whatever that entry's state** ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D23):
one that is not installed, or that the resolver gives no complete command for, cannot start, and the game page says so
and disables Play (below). Only a label that no longer exists in the list gives way to the next layer — never another
entry Tender picks by itself — with a WARNING, and the answer names that pick so a surface says "Your choice '_label_'
no longer exists in _source_;" and what is used instead — "your platform choice '_label_' is used" where a per-platform
pick stands, "the default '_default_' is used" for the default, "there is no default to use instead" where there is none
(D5). The default layer is `domain.emulator_commands.default_option` over the same list: the entry its source ships
first (see [The default: the first in declared order](#the-default-the-first-in-declared-order)), which may itself be
standalone (PCSX2, RPCS3, Dolphin, …) or libretro. **Only** the live file decides it — no bundled snapshot — and **no
gamelist selection ever moves it**: the resolver states which entry a per-game `<altemulator>` or a system-level
`<alternativeEmulator>` promotes, and the default is read off the declared position, which promotion never touches.
Where no entry is in effect — the system lists none, no system is found, the catalogue cannot be read, no default is
established, or a pick is gone with no default — the start is refused, and nothing starts in that entry's place, ES-DE's
own choice included (#2195 D43).

**What starting comes to.** `domain/emulator_choice.py::start_payload` turns the choice into the `start` the game page
and the platform page read: `starts`; `cannot_start` with the reason — `not_installed` for an entry the resolver states
not installed, or, on the game page and at the start, why the resolver gives the entry no complete command for the game
([#2221](https://github.com/danielcopper/romm-tender/issues/2221) D2, D20; the reasons are under
[Starting a game](#starting-a-game-the-resolvers-command)); `choose` where the shipped default's installation is not
established, so there is no default until the user picks one (D9); `no_entry` where there is nothing to start with. The
platform page asks no command, since a platform has no game to ask it with. Beside a `starts`, `remarks` names each
placeholder ES-DE leaves unfilled in the command. A `cannot_start` or `choose` answer disables the game page's Play, and
the launch watcher refuses Steam's own Play on the shortcut with the same sentence
(`frontend/src/utils/emulatorStart.ts`). Whether the entry starts the game's own file — a game installed as a folder
included, which RPCS3 Shortcut does not take — is the resolver's launch verdict, not the start's (D21; see
[Whether an entry starts the installed game](#whether-an-entry-starts-the-installed-game)). Tender never switches such a
game to another entry by itself.

### Which emulator source answers

The resolver detects every **emulator source** it knows — RetroDECK, EmuDeck, a RetroArch without a frontend, Flatpak or
native (GLOSSARY.md, "Emulator source") — and `adapters/emulator_sources.py` is the one place they are detected and
held; the catalogue, firmware and save adapters take their installation from it and choose none themselves. The user
orders the sources and switches them off under Settings › Emulator sources, stored by kind in `settings.json`
(`emulator_source_order`, and the deny-list `emulator_sources_off`, so a source seen for the first time is on and joins
the end of the order); `domain/emulator_sources.py` holds the rules.

**A game's answers come from the source it starts through.** Every game starts through RetroDECK, so while RetroDECK is
detected and switched on it answers, wherever the user put it in the order; the stored picks are RetroDECK labels and
are read against RetroDECK only, and a pick records that source (D20). That holds for a RetroDECK that has not been set
up yet too: it is detected and answers, and every answer says it is not set up. Without it, the first switched-on source
in the order answers, with `starts_games` false, and the panel says Tender cannot start games through it yet. Until
Tender chooses among every source's emulators, the order decides only while RetroDECK is absent or switched off.

**A switched-off starting source starts nothing.** A shortcut's launch command starts its game through its source
whatever that source's switch says, so the shared launch gate refuses the start instead: while the source that would
start the game is detected and switched off, `check_start_source` answers its kind
(`domain/emulator_sources.py::switched_off_starting_source`, over a detection and nothing else of the sources), and both
funnels — the game page's Play and the launch watcher — refuse before any save-sync work and say, as a notification, the
sentence `startingSourceOffSentence` (`frontend/src/utils/emulatorSourceWording.ts`) has for that kind. Today that
source is always RetroDECK, the one with `starts_games`, and RetroDECK's is the only sentence there; a kind with no
sentence of its own says only that it is switched off. A switched-on source that starts games takes the start over, and
one that does not starts nothing, so it lifts no refusal. Where the starting source is not detected at all, the start
goes ahead: a source that is not listed has no switch to turn back on. While the backend is not running there is no
watcher, and a start from Steam's library goes through as before; the launcher reads no setting of Tender's.

Where the check cannot tell — no answer within `LOCAL_CALL_LIMIT_MS`, a call that fails, or a detection of the sources
that raised, which `check_start_source` refuses as `detection_failed` rather than answer as if nothing were detected —
nothing starts or is refused silently: both funnels ask the question `UNCHECKED_START_SOURCE_QUESTION` holds, under the
title `UNCHECKED_START_SOURCE_TITLE` (both in `frontend/src/utils/emulatorSourceWording.ts`), in
`frontend/src/shared/UncheckedSourceModal.tsx` — in the desktop dev build's desktop client, in
`frontend/src/desktop/gameview/dialogs/DesktopUncheckedSourceDialog.tsx` — with Start and Cancel. Start runs the rest of
the launch check and starts the game; Cancel starts nothing.

**How long an answer is kept.** Sources are detected per reading, so a source installed later appears on the next call,
and nothing outlives the call or run that asked it. A call from the panel takes a fresh reading per question. The
library sync's launch resolution takes one reading per run (`ShortcutLaunchResolver.do_read_sources`, in the preview and
once before the units of an apply) and hands it to every ROM's emulator (`do_build_core_overrides`) and to every
folder-backed install's disc enumeration (`do_scan_installed_paths`, `do_read_installed_paths`), so a system's catalogue
and accept-list are asked once per run, and a switch or a move during the run takes effect from the next one. The
resolver's `RealMachine` is one for the process and handed to every detection: it remembers only a libretro core's
probe, keyed on the core file's path, modification time and size, so the probe of an unchanged core runs once while
every answer stays live.

### A platform's system

RomM names a platform by a slug of its own, which only sometimes equals a system a source declares (`psx` does,
`new-nintendo-3ds` does not), so the system a platform's games belong to is the **source's own answer**:
`systems_for_platform`, asked through the resolver with the ids RomM holds for the platform — `igdb_id`,
`libretro_slug`, `ss_id` and `tgdb_id`, the four vocabularies the resolver has a crosswalk for (a numeric id as its
decimal string), in that order. RomM's other ids (MobyGames, LaunchBox, RetroAchievements, Hasheous, Flashpoint) have
none and are not asked. The resolver reads the catalogue's own `<platform>` tags, so a system the user added to a
catalogue by hand is answered as readily as a shipped one, and nothing in Tender holds a table of platforms.
`adapters/atlas_platforms.py` asks each of RomM's ids in the resolver's vocabulary for it and reads each matching
system's status into Tender's own — switched on (declared), switched off (disabled) or not here (absent);
`domain/platform_system.py::pick_system` decides over those. An answer carrying a status the adapter does not read is
never taken as absent: the platform's system is then not established (`unavailable`), as where the question raised
([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D19).

**Which answer is taken.** Only a **switched-on** system matches — one the catalogue declares; a system present only in
the catalogue's comments is switched off, and one the source does not have at all is no match. The first id whose answer
gives a switched-on system decides, and a later id is never asked. Where several systems match one platform (SNES gives
`sfc`, `snes` and `snesna`), **Tender's own rule** picks one, because the resolver names every match and prefers none:
its order — switched on, then switched off, then not here, alphabetical within each — states no preference. The one
named like the platform the id was resolved to is taken — `snes` — else the first the resolver lists; the regional
systems differ in name and look only, and the user does not pick among them. Which region a regional system stands for
is not stated by the resolver yet; once it is ([emu-atlas#583](https://github.com/danielcopper/emu-atlas/issues/583)),
that statement replaces this rule's choice among them. Where one id's answer names several platforms (libretro's
NES/Famicom entry gives `famicom` and `nes`), only the one equal to RomM's slug with its hyphens dropped decides
(`nes`), and the slug chooses only among them — it never becomes a system or a folder name itself; where it equals none
of them, the next id is asked. Another platform is never taken in its place: a Satellaview or Amiga CD32 game is not an
SNES or Amiga game, and a wrong system would stay in its folder, its link and its saves. So where the slug's platform
has only switched-off systems, one of them is named, and where no id gives the slug's platform, or it has no system
here, the platform has none.

**Where there is none.** Where no id gives a switched-on system but some id gives a switched-off one, the platform's
pages say **"System _system_ is switched off in _source_."**; where no id gives any, **"_source_ has no system for
_platform_, so Tender cannot download its games."**. _platform_ there, and wherever else the platform's system names the
platform — the pages that show the system, and a download's queue entry and every frame and event it sends — is the name
the `platform_names` cache holds for its slug, else the `name` kept with its ids (below), else the slug itself
([#2573](https://github.com/danielcopper/romm-tender/issues/2573) D1). The emulator list's reason is then
`platform_system_off` or `no_platform_system`, nothing is downloaded, and the platform's BIOS download and an adoption's
replace are refused the same way. Every other use keeps a state it already has: an uninstalled game's save answer is not
established, the search for a copy already on disk finds nothing, and the launch falls back to the plain one.

**Each source is asked for itself.** A download, and every question about where one would land — an uninstalled game's
save answer, whether the target is taken, the search for a copy already there, an adoption's target — asks
**RetroDECK**, the one source Tender downloads into, whatever its switch: the switch is the folder's question
(`adapters/retrodeck_folders.py`). Every other question — the emulator choice, the BIOS answers, the platform page —
asks the answering source (above), so a platform can be one system in RetroDECK and another in EmuDeck. **An installed
game keeps the system its install record holds** for its saves, its emulator choice, its BIOS answer, the BIOS download
started from its page and its launch (`PlatformSystems.rom_system`); only a new download, and the platform page's BIOS
download, follow the source's current answer. A source that renames, moves or drops a system is not followed yet.

**The ids are kept.** `kv_config`'s `platform_ids` holds every listed platform's four ids and the `name` RomM's listing
gives it, replaced at the start of every sync from RomM's listing (a failed write is logged and leaves the run alone).
Where none are kept for a platform — the first start after an update — `PlatformSystemService` reads the listing once
and keeps it. Where that read fails, the question answers the reason `classify_error` gives it — `server_unreachable`
while RomM is offline — as every other read that needs RomM does, and keeps nothing; the next read with RomM reachable
keeps the ids. A game not downloaded then shows the offline state on its page (its save status, its BIOS tab reading
unknown), and its emulator menu, its BIOS tab and the platform page say **"RomM cannot be reached, so Tender does not
know this platform's system yet."**; where RomM answered but refused the read for any other reason (`auth_failed`,
`not_found`, `config_error`, …), they say **"RomM did not give this platform's ids, so Tender does not know its system
yet."**, and the cause itself shows where Tender's connection state shows it; a downloaded game keeps its recorded
system and is not affected. The game page's two looks at the disk — whether a download's target is already taken, and
whether the game is there under another name — must reach no network, so they never read the listing: for them a
platform with no kept ids has no system, and they go quiet.

The platform page names the system taken with its source in its header line ([qam-panel.md](qam-panel.md#library)).

### The default: the first in declared order

Selection is **data-derived from the live `es_systems.xml` alone** — there is no curated snapshot. The pure kernel
`domain/emulator_commands.py` turns each of a system's catalogue entries into an `EmulatorOption` (`label`, `kind`,
`core_so`, `emulator`, `not_installed`, `not_established`, `declared_index`, `chosen_in_frontend`), and
`shipped_default` returns **the entry with the lowest `declared_index`** — the one its source ships first, whatever
Tender can do with it ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D7). `default_option` takes it
unless its installation is not established (D9). Nothing later in the list is taken instead: an entry that cannot start
is said so (above). This amends [ADR-0020](../adr/0020-live-es-systems-emulator-resolution.md)'s rule, which skipped to
the first entry Tender could write into a shortcut.

**The rows keep the resolver's order; the default is read off the declared position.** The resolver states its entries
in _effective_ order, where a choice the user made in ES-DE — a per-game `<altemulator>`, a system-level
`<alternativeEmulator>` — puts that entry first and states it in the entry's `selection`, and carries each entry's
shipped position as `declared_index`. The adapter lists the rows in that order and marks the promoted entry
`chosen_in_frontend` where it is not the shipped default, so the picker, the game page and the platform page show what
the user chose in ES-DE beside Tender's own choice; what is set in ES-DE never becomes Tender's default (D7). The game
page asks with the installed game's path, so a game's own choice counts; the launch path asks per system. The indices
are ascending but need not start at 0 or be dense — ES-DE's own walk lets an empty-text `<command>` hold a position
without yielding an entry, and drops a duplicate label entirely — so the lowest one counts, never `entries[0]` and never
"index 0 exists". An entry with **no** declared position is never the default; that is the resolver's derived
enumeration for a catalogue-less arrangement, and such an entry carries an empty command.

Whether an entry starts a game is the resolver's command for that game, asked at the start and on the game's page
([Starting a game](#starting-a-game-the-resolvers-command)); Tender keeps no rule of its own about a command's text
([#2221](https://github.com/danielcopper/romm-tender/issues/2221) D20). A system whose first command is the shortcut
form (`%ENABLESHORTCUTS% %EMULATOR_OS-SHELL% %ROM%`, RetroDECK's PS3) keeps it as its default, and its game page says
what the resolver answers for it.

**Whether an entry is installed is the resolver's answer.** RetroDECK lists more emulators in `es_systems.xml` than it
bundles, so a system's default could name one that is not on disk (Ryubing on `switch`). Each catalogue entry carries
the resolver's `availability`, from ES-DE's own lookup over its find rules, for libretro cores and standalone emulators
alike, and `adapters/atlas_catalogue.py` reads it:

- **startable** — whether it starts a game is the command's answer (above).
- **not installed** — reason `not_installed`, whichever of the resolver's causes it states (no find rule matched, the
  program or its launcher script is missing, or the core is). It stands in place of any command: no command is asked for
  it, the picker shows it disabled as "emulator not installed" (`entryNotes`, `frontend/src/utils/emulatorMenu.tsx`),
  and as the default or a stored pick it cannot start.
- **not established** — the resolver cannot tell. The entry carries a note (`EmulatorOption.not_established`, sent as
  the entry's `reason`) naming the cause where the reading knows it: `no_find_rule` (ES-DE has no rule for the emulator
  or the core), `find_rules_unread`, `lookup_unsupported` (the resolver does not follow this lookup yet — the
  arrangement's, or this one command's shape) or `path_unseen` (ES-DE would look where the resolver cannot see: the
  sandbox's own `/etc`, `/tmp`, `/dev`, `/proc` or `/sys`, or a path it cannot stat); `not_established` where it does
  not. Such an entry stays a pick a user may make, and a pin to it stays, but `default_option` never takes it, so it is
  never the default by itself (#2195 D9). The picker offers it with "installation not established" and the cause, and a
  page whose emulator it is says so under it (`frontend/src/utils/emulatorInstallation.ts`); it is never called missing.
  Such an entry is a `program`: the resolver states no core path for a `core` entry whose installation it cannot
  establish (#2195 D15).

The resolver's `run-game-differs` note — RetroDECK's `run_game.sh` would find a different program for the command's
`%EMULATOR_X%` token than ES-DE does — changes nothing here: no start goes through that script (#2221 D7).

`get_emulator_options(system)` returns `{"available": bool, "options": [EmulatorOption, ...], "reason", "source"}`.
**`available` is `False` whenever nothing could be established**: no emulator source answers (`reason` `no_source`, or
`switched_off` where every detected source is switched off); the resolver raised (`unavailable`); the catalogue answer
carries one of the four `emulator-catalogue-*` refusals — the arrangement ships no catalogue, the resolver has not
located one, or the one it has could not be read (all three `unavailable`), or only part of it is readable (`sealed`,
EmuDeck's on a runtime with no zstd codec, see
[backend-architecture.md](backend-architecture.md#composition-root-bootstrap)); or it carries `catalogue-invalid`
(`catalogue_invalid`), a file ES-DE refuses its whole load on (usually a typo in the user's own `custom_systems`
overlay); or it carries `not-set-up` (`not_set_up`), a RetroDECK the resolver found by its Flatpak deploy with no
`retrodeck.json` — not started for this home yet, or its first-run setup left unfinished — which answers every question
with that finding and an empty enumeration. `source` is the answering source's `{kind, starts_games}`. The platform page
and the picker say why, from `reason`, rather than show an empty list they cannot distinguish from a system the frontend
knows no emulator for ([qam-panel.md](qam-panel.md#notices-and-homes)); the launch degrades to plain. The overlay
entries the resolver gives beside `emulator-catalogue-sealed` are not read as a list — not for the emulator list, not
for saves and not for the firmware rows and verdicts, whose answer beside a sealed catalogue reads as nothing
established: an incomplete list would look complete. The one use is a BIOS link: a place such an answer states is a
place a link may go, and the list never decides that a link is unwanted
([#2191](https://github.com/danielcopper/romm-tender/issues/2191) D37, D40). An empty list carrying none of those codes
is that real "knows none", and `emulator-catalogue-exclusive` is not a refusal at all: a custom `es_systems.xml`
declaring itself the whole catalogue gives a complete answer, merely a small one. The test is the codes and never an
empty caveat list — a broken installation states health findings on every answer it gives. `options_to_payload` projects
the list to the frontend picker shape
(`{label, kind, core_so, emulator, declared_index, is_default, chosen_in_frontend, reason}`): every entry is clickable
but one the resolver states not installed, which reads "emulator not installed" (D9, D23, D31); the default and the
user's ES-DE choice are marked; an entry's `reason` is its not-established note, else `not_installed`. Whether the
resolver gives an entry a complete command for the game is not on the list: the game page's emulator menu reads it per
game ([below](#whether-an-entry-starts-the-installed-game)). Asked with `source`, `get_emulator_options` answers for
that detected source instead of the answering one, which the platform page's per-source defaults read (below).

**One row per entry.** Every entry is a row of its own, in the resolver's order, two labels that run one command
included: RetroDECK lists `pcsx2_libretro.so` as `LRPS2` and as `PCSX2`, and ES-DE stores a choice by its label, so
joining them would lose the choice ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D8). Within one
answer a row is joined on `(emulator, declared_index)`; a stored pick is found by its label, which is unique within a
system's list. See [ADR-0020](../adr/0020-live-es-systems-emulator-resolution.md).

**The BIOS part names the emulator; the launch places name the entry.** Two names stand on a page, and they answer two
questions. Where Tender offers or names what a platform or game launches with — the emulator menus on the platform page
and a game's page, the platform header, the toast after a game's switch, the line under the button while a platform
switches — it prints the entry's `label`. The BIOS part — its selector, its header sentence, the tie clause, the group
headings and the row notes — names the emulator by its identity, every label the resolver gives that identity joined in
the list's order (`_labels_by_emulator`, D28), because the firmware is the same for every way an emulator launches. The
two agree wherever an emulator has one entry and differ where it has several: the header reads "LRPS2" while the BIOS
sentences name the emulator "LRPS2 / PCSX2", every label the resolver gives it.

Adding a new standalone system needs **no change to Tender** — the moment a RetroDECK update lists its emulator first in
`es_systems.xml`, Tender's default follows.

**Every per-game core read consumer draws from this one seam**, so the launch core cannot diverge from any derived
value:

| Consumer                                      | What it uses the core for                                   |
| --------------------------------------------- | ----------------------------------------------------------- |
| `FirmwareService` / game-detail BIOS check    | which BIOS files the active core requires (optional vs req) |
| `RomInfoService` (saves) → save answer        | which emulator is asked where the save lives                |
| `SyncEngine` (saves) core tag                 | the per-core save-sync identity                             |
| `StatusService.check_core_change`             | detect a core change since the last save sync               |
| `GameDetailService` → CPU badge / Active Core | the core shown on the game detail page                      |

A pinned per-game or per-platform label that no longer exists (the entry was removed by a RetroDECK update) is **never
fatal**: the resolver logs a WARNING and takes the next layer (the per-platform core, then the default), and the page
names the pick that is gone. No consumer ever sees a bogus `.so`.

### Whether an entry starts the installed game

The emulator menu on an installed game's page shows, for each entry, whether it can start the game and, where not, why
([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D22, D29, D31). Each answer is the resolver's
launchability verdict asked with that entry's label (`CoreService.get_emulator_launches`, read when the menu opens); the
menu opens at once with every entry choosable, and the rows wait together on Play's throbber (`romm-throbber`) for the
one request that answers them all; an answer that lands after the page moved to another version, or after the menu
closed, is dropped (`utils/emulatorLaunches.ts`). Each line says what the resolver states: an entry that refuses the
file names the resolver's reason where it gives one — RPCS3 Shortcut "cannot start a game installed as a folder" for a
game kept as a `<game>.ps3` folder, Beetle PCE "it starts no game from a playlist" for a `.m3u`
([#2698](https://github.com/danielcopper/romm-tender/issues/2698) D7) — and an entry that starts it says what the
resolver's reading of the file could not establish, where it stopped short (D40), a file its start goes on without, with
"can start it, without _file_: …", or what else it is established to do short of the whole start, with "can start it,
but …". Where the answer says more than one thing, the line is the first with "+_N_ more" (#2698 D8). The same request
asks each installed entry's command for the game (`start_verdict`), and an entry the resolver gives no complete command
for gets no "can start it" under it: its row says "Tender cannot start it yet: _reason_", after the file's statements
and counted among them ([#2221](https://github.com/danielcopper/romm-tender/issues/2221) D20). The menu's answer never
moves the pick; a pick of an entry that cannot start the game is the user's, and Play is then off with the verdict's
sentence — the resolver's refusal of the file where it states one, then "Tender cannot start _entry_ yet: _reason_."
(`frontend/src/utils/emulatorStart.ts::startRefusal`). What a game's own entry answers, and what each answer does to a
start, is [Launch-target validation](backend-architecture.md#launch-target-validation).

## Starting a game: the resolver's command

A game starts with the command the resolver gives for it
([#2221](https://github.com/danielcopper/romm-tender/issues/2221) D1):
`launch_command(system, path, label=<entry in effect>)`, asked of the answering source with the game's own path, the
path the launch verdict is asked with (D3). `adapters/atlas_launch.py` is the one module that reads that answer, into
`domain/start_command.py`'s view: a complete command as Tender runs it, or why the resolver gives none (D2).

A command is complete where nothing in it is missing: the resolver answers one, states what the program reads from its
distribution's own start (`distribution_environment`; not stated is `launch-environment-unpinned`), gives every variable
there a value (D13 — `""` is a value, passed on as set and empty), and places the folder the command starts in on the
host where it names one. Whether a variable is `provided` gates nothing; it is passed on as stated.

For a Flatpak program Tender runs
`flatpak run --nosocket=wayland --filesystem=<game folder> --filesystem=<firmware store>:ro --env=… --command=<program> <app id> <arguments…>`:
the socket is left out as before (D18, the reason under
[RetroDECK runs through XWayland in Desktop Mode](../user-guide/troubleshooting.md#retrodeck-runs-through-xwayland-in-desktop-mode)),
the game's folder is granted writable and Tender's firmware store read-only for this start only (D4), the distribution's
variables are set first and the command's own `env` after them, so the command wins on a shared name, and a `:` or `\`
in a granted folder is escaped as Flatpak reads it. An AppImage or a native program runs as the file the resolver names,
through `env` where it is started with variables. Nothing else of the command is Tender's: the program, its arguments
and their order are the resolver's, every placeholder already resolved, so an environment prefix such as Dolphin's
`QT_QPA_PLATFORM=xcb` is set rather than passed as an argument.

Why there is no complete command reads, in Tender's words: how ES-DE starts it could not be read, it starts through a
`.desktop` file, a file its command reads in could not be read, its command names no program, where its games are kept
could not be established, its command is longer than can be read, which ES-DE build the source ships could not be read,
what it needs from RetroDECK's start could not be read or is not known yet, or the folder it starts in could not be
placed; an entry the resolver does not state startable reads as its installation does. Beside a command that starts, a
placeholder ES-DE leaves in the command as written is named; a value ES-DE's own substitution would break is passed as
the resolver answers it, and nothing is said. `tests/adapters/test_launch_command_vectors.py` runs every recorded
launch-command machine of the vendored resolver through this reading.

### The start: one command, handed over for one start

Nothing of the command is stored (#2221 D7). Just before a start, the panel asks `prepare_game_start`
(`services/game_start.py`): the entry in effect, the game's path (`domain/rom_files.py::asked_path`, the path the launch
verdict is asked with), the resolver's command for them, and the game's folder — its own folder for a game kept in one,
else the folder its file lies in — and Tender's firmware store as the grants. The command goes into a file of its own,
`<runtime dir>/start/<appId>` (D15): the folder the command starts in, then the program and its arguments, each ended by
a NUL, written whole through a staging file into an owner-only folder and readable by the user only
(`adapters/start_handover.py`). The runtime directory is `$XDG_RUNTIME_DIR/romm-tender`, in memory and emptied at
logout; a session with no `XDG_RUNTIME_DIR` hands nothing over, and its starts are refused. The folder is the one the
command names (created where ES-DE would create it), else `start-folder/` in Tender's data folder, created at the start
(D6).

The shortcut's launch options are its appId, and the answer names them; the panel sets them back before the start (D16).
Steam starts `tender-rom-launcher <appId>`, which ignores every word after the appId, claims the file by renaming it,
removes it and runs the command in its folder; with no file, or one older than 30 seconds, it starts nothing and says so
on its error output (D11 — what it says to the player is #2230's). A refusal of `prepare_game_start` stops the start:
`not_found` for a game with no shortcut, `not_installed`, `cannot_start` with the game's `start` where its entry cannot
start it or the resolver gives no complete command, and `start_unavailable` where the command cannot be handed over.

## Application: a start asks, a shortcut carries nothing

No shortcut carries anything about what starts its game: its launch options are its appId, the same for every bound
shortcut, installed or not ([#2221](https://github.com/danielcopper/romm-tender/issues/2221) D7, D10). So nothing is
written to a shortcut when the entry in effect, the disc pick, the install or the RetroDECK home changes; the next start
asks with what is in effect then ([The start](#the-start-one-command-handed-over-for-one-start) above). A per-game pin,
a per-platform core, a disc pick and a reinstall therefore apply at the next start, with no sync and no Steam write.

The one write left is the appId itself. The sync sets it on every shortcut it creates or rewrites
(`frontend/src/utils/steamShortcuts.ts::shortcutLaunchOptions`), the start check sets it back before each start, and the
reconcile at panel load and after a sync (`get_bound_launch_options`, `services/startup_healing.py`) lists every bound
shortcut whose launch options in Steam's shortcut file are anything else — a shortcut an older version wrote a command
into, or one edited in Steam — and the panel confirm-sets those.

## Multi-disc selection

A multi-disc game (a PS1 RPG across four CDs, say) installs as a folder of disc images. The same start that asks for the
core's command also asks with **which disc launches** — a second per-game deviation that follows the core override's
structure point for point. The decision record is
[ADR-0014](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0014-per-game-disc-selection-in-db-applied-as-bake-time-launch-path-override.md);
the user-facing guide is
[Picking a Disc for Multi-Disc Games](https://github.com/danielcopper/romm-tender/blob/main/docs/user-guide/managing-games.md#picking-a-disc-for-multi-disc-games).

### Storage: the disc pick is a basename on the `Rom` aggregate

`roms.selected_disc` is a nullable `TEXT` column added by migration `004_add_selected_disc.sql`. It holds the
**basename** of the disc the user pinned (e.g. `"Example Quest - Second Journey (USA) (Disc 2).cue"`), never a resolved
absolute path and never a disc index.

- **`NULL` = no selection** → the ROM follows the **default**: the install's `.m3u` when `file_path` is one (the
  in-emulator disc-swap default), else the first enumerated disc.
- It anchors on `roms`, not `rom_installs`, so the pick **survives uninstall/reinstall and RetroDECK-home migration**
  (per [ADR-0007](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0007-rom-retention-identity-anchor.md))
  — the disc folder is gone while uninstalled, but the basename re-resolves the moment it returns.
- Mutations go through the verb-named aggregate methods `Rom.pin_selected_disc(filename)` (rejects a blank filename) and
  `Rom.clear_selected_disc()`. Only `pin`/`clear` ever write the column (`SqliteRomRepository.set_selected_disc`); it is
  **excluded from the sync UPSERT `SET` clause** — the same `_SYNC_COLUMNS` tuple that omits `emulator_override` omits
  `selected_disc` — so a re-sync never wipes the pick.

Tender stores the **basename** because the absolute path changes across uninstall/reinstall and home migration (a stored
path would go stale) and a positional index would silently re-point if a disc file were added, removed, or renamed. The
basename re-resolves to the same disc whenever it is present and cleanly registers as **stale** (→ default

- WARNING) when it is not.

### Disc identity vs the live accept-list

Enumerating a ROM's discs needs two different facts kept separate:

- **Disc identity is format-semantic and hardcoded** — `domain/disc_formats.py` defines `DISC_IMAGE_EXTENSIONS`, the
  irreducible set of launchable disc-image containers: the single-file images and the descriptors of a multi-track set,
  listed in GLOSSARY.md → Disc. A track file (`.bin`, `.raw`, `.img`, `.sub`, `.mdf`) is a **sidecar** (owned by its
  descriptor, never launched directly) and an `.m3u` is a **playlist**; both are excluded simply by not being in the
  set. The disc unit is the image or the descriptor itself, never a track.
- **The per-system accept-list is a capability and read live** —
  `AtlasCatalogueAdapter.get_supported_extensions(system)` (`adapters/atlas_catalogue.py`) returns the system's
  es_systems `<extension>` set verbatim, both cases where the file lists both (ES-DE compares case-exactly; the
  launch-file rule, the disc enumeration and the adoption search still fold case on the file's side, which ES-DE does
  not), threaded into the resolver through the `SystemSupportedExtensionsFn` Protocol, exactly as `system_supports_m3u`
  is read for the `.m3u` gate
  ([ADR-0013](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0013-platform-gated-m3u-via-es-systems.md)).

Enumeration keeps the files whose extension is in **the intersection** of the two, so a disc the emulator cannot launch
on this system is never offered. es_systems alone cannot supply disc identity — it is a flat accept-list with no
per-token role metadata, so it lists `.cue`, `.bin`, and `.m3u` identically and cannot say which is the disc. When
es_systems is unavailable, enumeration falls back to the full disc set rather than intersecting to nothing.

### The read seam: `DiscLaunchResolver`

`DiscLaunchResolver` (`backend/services/disc_launch_resolver.py`) is the single place that answers "which file will this
multi-disc ROM actually launch with?", mirroring `ActiveCoreResolver`. It scans the install directory recursively (the
file-lister Protocol), reads the live accept-list, enumerates the discs (`domain/disc_selection.enumerate_discs`), and
resolves the persisted `selected_disc` over them (`domain/disc_selection.resolve_launch_path`):

```text
resolve_for_install(install, selected_disc):
  discs = enumerate_discs(scan(install.rom_dir), supported_extensions(install.system))
  path =
    if len(discs) < 2:                    ── not multi-disc → file_path unchanged
        install.file_path
    elif selected_disc names a disc:      ── valid pin
        that disc's path
    else:                                 ── NULL, or a stale pin (warn + degrade)
        install.file_path if it ends .m3u else discs[0].path   ── the default
  return folder_boot_root(path, install.rom_dir) or path   ── folder-boot override (ADR-0019)
```

A **non-multi-disc ROM resolves to its own `file_path`** — zero behavior change for the overwhelming majority of games.
A **stale pin** (the selected disc no longer present) degrades to the default with a WARNING, never fatal, exactly like
`ActiveCoreResolver`'s stale-label handling. Crucially, the resolver **never rewrites `file_path`**: it returns the path
a start is asked with (`domain/rom_files.py::asked_path`), and `file_path`-derived values (save path, core, displayed
filename) stay stable.

### Folder-boot launch target

Some systems boot a game **directory**, not the nested launch file. A PS3 game installs as a folder whose payload is
`…/PS3_GAME/USRDIR/EBOOT.BIN`, and `detect_launch_file` picks that EBOOT as `file_path` — correct as the launch _file_
identity, but RPCS3's directory-boot rejects it and wants the folder that contains `PS3_GAME`
([#1212](https://github.com/danielcopper/romm-tender/issues/1212)). The path a start is asked with is therefore the
folder.

A path override, layered **after** disc resolution in the same `resolve_disc_path` seam:
`folder_boot_root(path, install.rom_dir)` (`domain/rom_files.py`) strips a hardcoded `FOLDER_BOOT_MARKERS` run
(`PS3_GAME/USRDIR/EBOOT.BIN`, matched case-sensitively) from the resolved path and returns the game root, guarded so it
fires only when `rom_dir` is set and the derived root stays inside `rom_dir` (a bare EBOOT in the shared system dir
never names that shared dir). Because it triggers only when the resolved path still carries the marker, it composes with
disc resolution — a resolved disc path (`…(Disc 2).cue`) has no marker, so a multi-disc ROM is never folder-stripped,
and the folder rule applies only when disc resolution returned `file_path`.

`RomInstall.file_path` stays the EBOOT (the [ADR-0008](../adr/0008-rom-install-launch-file-and-rom-dir.md) anchor), so
save-path, core, and displayed-filename derivations are untouched. The resolver's command for the folder is run as it
answers it, like any other ([Starting a game](#starting-a-game-the-resolvers-command)); RetroDECK's `run_game.sh`, which
turned a folder `%ROM%` into a path inside it, is no longer in the way
([#2221](https://github.com/danielcopper/romm-tender/issues/2221) D7). See
[ADR-0019](../adr/0019-folder-as-launch-target.md).

**The folder is named as ES-DE scans it.** A folder-boot download's folder is renamed `<game>.ps3` (`ps3_folder_rename`,
`domain/rom_files.py`), the name ps3's accept-list carries for a game kept as a folder, and the resolver is asked about
that folder as about any other game's file: on the game's page with the entry the game starts with, in the emulator menu
with each entry, never with an entry of Tender's own choosing. The resolver also names the folder RPCS3 boots, but the
marker run above decides which directory inside it a start is asked with, until a later part of
[#2221](https://github.com/danielcopper/romm-tender/issues/2221) reads the resolver's instead
([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D11, D38). A folder whose name already ends in a
suffix the accept-list carries, compared verbatim as ES-DE compares it, keeps its name, and so does one whose new name
is taken: nothing is moved over another folder. A download whose game folder lies exactly one level inside the folder it
was unpacked into has that game folder placed as the folder itself (`ps3_folder_lift`), since ES-DE, the resolver and
RPCS3 look only at the folder and find nothing to boot one level above the game (D39) — only where the game folder is
its only entry, so nothing beside it is deleted or left outside the install. An older install is renamed and lifted once
at start-up (D27, D39; see [Launch-target validation](backend-architecture.md#launch-target-validation)).

### The picker use cases

Two service methods on `DiscService` (`backend/services/disc.py`) drive the inline `DiscSelector` dropdown on the game
detail page:

- **`get_disc_selection(rom_id)`** reports `{multi_disc: false}` for an unknown, not-installed, single-file, or
  fewer-than-two-disc ROM (the frontend renders no picker), else `{multi_disc: true, discs: [...], selected, default}`.
  Read-only over the local filesystem; the no-picker answers are normal responses, not failures.
- **`select_disc(rom_id, filename)`** pins a disc (or clears to the default with `filename = null`). An unknown filename
  is refused with `not_found` and **nothing is written**; a folder with fewer than two discs is refused with
  `unsupported`; a not-installed or single-file ROM with `not_installed`. Each refusal is raised, and the endpoint
  answers it in the canonical `{success: false, reason, message}` shape. On success it persists the pick via the
  pin-only `set_selected_disc` write path and returns the now-effective `selected`. Every start asks with the pin, so
  the picker's selection and the disc a start opens cannot diverge.

## Set and clear

### Per-game (`CoreService`)

The frontend CPU-button menu on the game detail page drives two backend endpoints:

- **`set_game_core(rom_id, label)`** checks the LABEL **first** against the ROM platform's classified command list. A
  label the list does not carry, or one whose emulator the resolver states not installed, is refused with
  `core_unavailable` and **nothing is written**; an entry the resolver gives no complete command for is pinned like any
  other, and its start is then refused at Play. On success it `pin`s the override.
- **`clear_game_core(rom_id)`** (triggered by picking the **default-marked core** in the menu) `clear`s the override to
  `NULL`, so the ROM follows the per-platform core or the default from its next start. There is no separate "Reset" item
  — selecting the default-marked entry is the clear path; any other entry pins that core.

Both refuse a ROM Tender does not track with `not_found`; a pin also refuses with `not_found` when the ROM is retired
while its label is resolved, and with `core_unavailable` when its platform changed meanwhile. Both answer
`{success: true}` and write nothing to the shortcut, so the panel toasts success once the backend has answered.

### Per-platform (`CoreService.set_system_core`)

The platform detail's **Change core** button — a `ButtonItem` opening the same context menu as the game-detail picker,
so every entry renders and is chosen as on a game's page (#1210) — calls
**`set_system_core(platform_slug, core_label)`**:

1. It writes the choice into `settings["platform_cores"]` — storing the LABEL with the kind of the answering source
   under the slug (a platform no source answers for refuses with `core_unavailable` and writes nothing), or popping the
   slug when the label is empty (revert to the default) — and persists `settings.json` through the injected
   `SettingsPersister`.
2. It returns `{success: true}`. A `settings.json` that cannot be written gives the previous choice back to the live
   settings, so memory and file agree, and refuses with `save_failed` ("Save failed: …"). The write checks no BIOS, so a
   firmware read cannot add a second way to report a stored switch as failed. The platform detail re-reads the platform
   once the switch is done, and that read answers for the new core's firmware.

`platform_cores` has one more writer: the firmware listing's refiling (`services/firmware/refiling.py`) moves an
override stored under a server folder name, before a firmware file's platform came from RomM's `platform_id`, to its
platform's slug, in the same live dict, and gives the move back when `settings.json` cannot be written.

A per-platform core change applies from the next start of every game on the platform that has no per-game pin — no sync
and no Steam write required. Because the `PlatformCoreReaderAdapter` holds the live settings dict, the next start
resolves the value just written rather than a stale snapshot.

The Library page's platform detail reads its core through **`get_system_core_info(platform_slug)`**, a read of its own
rather than a slice of another payload: `get_platform_core_info` is keyed by ROM and layers that ROM's own pin on top,
and `get_platform_firmware_status` answers whenever the page's walk reaches that platform — which is not when the reader
focuses it. It costs one ES-DE options read and a `settings.json` lookup, opens no Unit of Work, and is issued once per
selection.

Its **`active_core_label`** names the per-platform pick while its label still names an entry, whatever its state, else
**Tender's default** ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D4): the default of the first
enabled source in the user's order that offers one — EmuDeck's where EmuDeck is first. Where Tender's default comes from
a source games do not start through, the page shows that default and none of the starting source's own lines beneath it:
no refusal, no vanished pick, no red (D24). **`source_defaults`** names every enabled source in the user's order with
the entry it ships first for the platform, the user's choice in it inside ES-DE where that differs, or why there is none
(its list's refusal, `no_entry`, or the shipped entry's not-established note); the page shows only the ES-DE choices
from it (D25), and Tender's default is derived from it: **`tender_default`** names the source and entry Tender's default
is, and **`default_elsewhere`** whether, with nothing picked, that default comes from a source games do not start
through — D24's verdict, which the panel reads rather than works out, and under which it reads nothing off the starting
source's rows. `start` and `vanished_pick` are the platform's own, as on the game page. The identically-named field
`get_platform_firmware_status` carries per platform is the label of `resolve_firmware_option`: the pick while its label
exists, else the shipped default whatever its state, since an emulator that cannot start still declares the firmware it
would need.

### One identity space on the wire

Every payload that names the active emulator names it the same way, and the name is the **emulator identity** — the
resolver's own `emulator` field, `mgba_libretro.so` for a libretro entry and `DUCKSTATION` for a standalone one. Three
places carry it and they are the same string:

- the **`active_core`** of `get_platform_core_info` (per ROM, through `resolve_rom`'s choice) and of
  `get_platform_firmware_status` (per platform, through `resolve_firmware_option`, which takes the shipped default where
  nothing is picked, whatever its state);
- the **`emulator`** field of every picker row in `options_to_payload`, beside its `core_so`;
- the **`emulator`** of each entry of a BIOS answer's emulator selector (`emulator_tabs`) and a firmware row's
  `declared_by`.

That is what lets a BIOS page match each emulator's entry and rows to the picker's labels, mark the one the answer was
scoped to and tell the backend which emulator a download is for
([#2190](https://github.com/danielcopper/romm-tender/issues/2190) D15). `core_so` cannot serve any of it: it is `None`
for every standalone emulator and carries no extension for a libretro one, so matching on it named no standalone
emulator and printed `dolphin_libretro.so` where the label said Dolphin. `label` cannot either — ES-DE lists one
`pcsx2_libretro.so` as both `LRPS2` and `PCSX2`, so a label identifies a launch ROW and two rows can be one emulator.
`core_so` stays on the picker payload because a surface may still need to name the libretro core itself; it is not a
second answer to "which emulator is this".

`active_core_for_rom`'s `(core_so, label)` is therefore **not** what a payload sends: it answers `None` for a standalone
pick, which is the degradation this swap removed.

## Why a start never goes through the gamelist

ES-DE stores core choices in `gamelist.xml` — a per-game `<altemulator>` element and a system-level
`<alternativeEmulator>`. Tender does **not** use that file for its own launches at all (it neither reads nor writes it).
Every start runs the command the resolver states for the entry Tender chose, so a core a user set inside ES-DE's UI
never reaches a start Tender makes, and the BIOS badge, the per-core save path and the core-change warning all follow
the same choice the start does
([ADR-0012](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0012-plugin-owns-core-selection-always-e-no-gamelist.md),
as amended). Before, the command went through RetroDECK's `run_game.sh` with `-e`, which skipped the script's gamelist
lookup; that lookup matched the ROM path with an awk regex that any metacharacter in a file name broke
([#210](https://github.com/danielcopper/romm-tender/issues/210) /
[RetroDECK#1358](https://github.com/RetroDECK/RetroDECK/issues/1358)). A start no longer runs that script at all.

Tender reads what the gamelist states only through the resolver, to show it: the entry the user chose in ES-DE is marked
beside Tender's own choice ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D7) and never decides a
launch.

Writing the gamelist is dropped for the same ownership reason: `gamelist.xml` is ES-DE's strict-parser-hostile,
multi-root-tolerant file, and the per-platform deviation that once lived there now lives in Tender's own
`settings.json`. There is **no gamelist write** anywhere in Tender.

### No migration, re-apply once

There is **no migration** from any old gamelist model. A per-game core previously written to `gamelist.xml` is not
imported (per
[ADR-0011](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0011-per-game-core-override-in-db-applied-via-e-flag.md)),
and a per-platform core previously set as a system-level `<alternativeEmulator>` is **not** imported into
`platform_cores` either — `platform_cores` starts empty. This is by design: a gamelist-import path would revive the
multi-root-XML parse failures and folder-collapse ambiguity that the model in which Tender owns core selection was
chosen to avoid. Re-apply any per-platform core once through the platform detail's Change core button and it sticks from
then on.

## What starts is the resolver's command

What a start runs — the program, its arguments, its environment and the folder it starts in — is the resolver's answer
for the entry, read in `adapters/atlas_launch.py`; Tender adds only the sandbox grants and the RetroDECK distribution
environment the resolver states. The per-ROM **selection** (which entry does this ROM start with?) is a service-layer
read. A launcher other than RetroDECK's ([#918](https://github.com/danielcopper/romm-tender/issues/918)) is one the
resolver answers for, not a second seam in Tender.

The **core picker now lists standalone emulators alongside libretro cores** (#1210 / ADR-0020) — you _can_ choose
standalone PCSX2 vs the LRPS2 libretro core in the UI, at both per-game and per-platform scope. **BIOS** is no longer
part of the follow-up: since #1821 a firmware answer is keyed on the emulator identity, so a standalone pick is asked
and answered like any libretro core rather than reading `active_core = None` and degrading. **Save-sync** for a
standalone system is still its own effort.

---

**Related pages:**

- [Backend Architecture](backend-architecture.md) — service/adapter layering, dependency diagram
- [Config Source Parsers](config-source-parsers.md) — one-parser-per-source principle; how the live `es_systems.xml` is
  read as the sole core/emulator source (the gamelist and the bundled `core_defaults.json` are no longer read)
- [Steam Non-Steam Shortcuts](steam-non-steam-shortcuts.md) — AddShortcut API, launch options (the appId), app-id
  derivation
- [Database Design](database-design.md) — the `Rom` aggregate and the `roms` table (incl. `selected_disc`)
- [BIOS and Emulator Cores](../user-guide/bios-management.md) — the user-facing core-selection guide
- [Managing Games](../user-guide/managing-games.md#picking-a-disc-for-multi-disc-games) — the user-facing disc picker
