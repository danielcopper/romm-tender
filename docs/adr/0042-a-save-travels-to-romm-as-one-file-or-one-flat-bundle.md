# A save travels to RomM as one file or as one flat bundle

## Status

Proposed. Records the save format decided under [#1235](https://github.com/danielcopper/romm-tender/issues/1235), which
this program adopts and proposes to the other RomM clients; it can change after that discussion. The work is split into
[#2016](https://github.com/danielcopper/romm-tender/issues/2016) (bundle upload),
[#2017](https://github.com/danielcopper/romm-tender/issues/2017) (safe unpack),
[#2018](https://github.com/danielcopper/romm-tender/issues/2018) (member-wise comparison) and
[#2019](https://github.com/danielcopper/romm-tender/issues/2019) (the `emulator` field). **Builds on
[ADR-0034](0034-a-save-is-answered-by-the-emulator-that-writes-it.md)**, which made the emulator's live answer the
source of which files a save consists of; this record decides how those files travel. **Does not touch
[ADR-0017](0017-client-baseline-detection-authoritative-negotiate-is-transport.md)**: this program still decides every
sync action itself.

## Context

Upstream sources below are read at RomM `d084723` (master, after 5.3.1) and its documentation `84e4891`, argosy-launcher
`8b3eed9` (2.17.0-beta.3), grout `0d71675`, Ludo `8ab19b3` and argosy-sigil `8a3b008`.

**RomM stores one uploaded file per save row.** `POST /api/saves` takes one `saveFile`
(`backend/endpoints/saves.py:205-215`), and an upload into a slot is stored under a timestamp-tagged name (`:249-250`).
A save of several files that reaches the server as one zip is stored as one file and hashed by its entries; only in that
shape does RomM keep such a save together. The rest of the server keys on the row, not on the files inside it:

- **Saves pair on `(rom_id, slot)`, newest row, not on the file name.** `negotiate` keeps the newest row per slot
  (`backend/endpoints/sync.py:221-230`); the name and the `emulator` play no part.
- **`autocleanup` counts every row in a slot.** It keeps the newest N rows of `(user, rom, slot)` whatever their names
  and extensions (`backend/handler/database/saves_handler.py:264-270`), so a `.srm` and its `.rtc` uploaded as two rows
  share one retention budget and compete for "newest".
- **The content hash is chosen by content.** A file `zipfile.is_zipfile` accepts is hashed as the sorted
  `"<entry>:<md5>"` lines joined by `\n`, then MD5'd; anything else is a raw MD5
  (`backend/handler/filesystem/assets_handler.py:47-57`, `:198-206`). Entry names count; compression, entry order and
  timestamps do not.
- **The `emulator` is a folder.** It becomes a segment of the row's storage path
  (`backend/handler/filesystem/assets_handler.py:147-156`).

Neither the server nor its documentation defines a format for a client's multi-file save. The one place the server
builds such a save itself is emulator streaming: at the end of a session it pulls the game's in-game save data off the
container as one zip (`backend/handler/streaming/saves.py:26-55`, `:70-112`; `docs/using/emulator-streaming.md:72` in
the documentation) and stores it as `<ROM> [<emulator> <time>].saves.zip` with no slot. Into the next session it
restores the newest `.zip` row whose `emulator`, lowercased, equals the container's, with no slot filter
(`backend/handler/streaming/saves.py:155-180`). Two answers from the RomM maintainers are recorded in #1235: related
files are bundled as one zip with the files at the root and no folder, and the uploaded file name carries no meaning
while a slot is set.

**The clients disagree beyond the single-file case**:

| Client                     | Several files of one save                                                               | Name of a bundle                                                                                                                                                         | A downloaded zip it does not expect                                                                                                                                                 |
| -------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| this program, today        | one row per file, each paired with the server rows that map to its own name             | —                                                                                                                                                                        | left on the server without a word (`services/saves/sync_engine/matrix.py:868-879`)                                                                                                  |
| argosy-sigil (the library) | a flat zip when two or more of the game's files are on disk (`src/save_unit.c:410-413`) | `<first member>.zip`, e.g. `Game.srm.zip` (`src/save_unit.c:349-365`)                                                                                                    | — (it neither uploads nor downloads)                                                                                                                                                |
| Argosy                     | one flat zip once sigil finds two or more of the game's files                           | `<rom>.zip` in the default channel, `<channel>.zip` in a named one, a channel being Argosy's word for a slot (`SaveUploader.kt:269-275`, `SaveSyncApiClient.kt:592-604`) | written byte for byte over the save file unless every entry matches a file of the game (`UnitSaveHandler.kt:66-67`, `SaveUnitResolver.kt:166-168`, `RetroArchSaveHandler.kt:38-59`) |
| grout                      | only the first file per `(rom_id, slot)` is kept for sync (`flow.go:311-323`)           | only PSP folders are zipped, uploaded under a temporary file name (`zip_save.go:22`, `flow.go:1304-1313`)                                                                | written byte for byte to the save path on every platform but `psp` (`directory_saves.go:10-12`, `flow.go:1465-1477`)                                                                |
| Ludo                       | the newest file per `(rom_id, slot)` syncs (`sync_core.py:13404-13421`)                 | —                                                                                                                                                                        | saved as its name's stem plus `.srm`, since `.zip` is not in its extension list (`sync_core.py:10574-10593`, reached from `_resolve_download_target`, `:14621-14661`)               |

Argosy's paths are under `app/src/main/kotlin/com/nendo/argosy/data/`, grout's under `sync/`, and Ludo's file is
`engine/romm_sync_engine/sync_core.py`. Three of the clients write a zip they do not expect as a raw save file, so a
bundle from one client can reach another client's emulator as an unreadable `.srm`.

**A save's file set depends on the emulator build, and nothing on the wire says which build wrote it.** A row of sigil's
layout table states the behaviour of the core revision its maintainer read, for every build of that core, and a
distribution can ship an older build than that revision —
[a survey of sigil's table](https://github.com/danielcopper/emu-atlas/blob/785f3cf5faca503cba99d9acf489bed6a5bdca06/docs/research/sigil-territory.md#3-what-stays-where)
finds three such cores. Neither RomM nor any of these clients carries an emulator version with a save — RomM records
only the client's own version on the device.

## Decision

**1. A bundle is uploaded as `<ROM name>.saves.zip`, and recognised by its content.** `<ROM name>` is the ROM's file
name without its extension, or the folder's name for a ROM that is a folder; the suffix is the one RomM's streaming path
gives its own save archives, and why this name is chosen over others is under Alternatives considered. A client
recognises a bundle by the zip signature, never by the name (rule 5.1 names the one exception) — Argosy's bundles are
read the same way.

**2. The files present decide raw or bundle, and a save is compared member by member.** One present file of the kinds
rule 4 takes in travels raw; two or more travel as one bundle. A raw file and a bundle that holds only that file are the
same save, and a difference in the clock file (`.rtc`) alone is not a change. sigil's `identity_hash` computes the same
thing: the composite without the clock members, which for one remaining member is that member's raw hash
(`src/save_unit.c:337-340`). The comparison also leaves out every entry rule 4 leaves out, so a bundle from another
client that carries one — Argosy's Saturn bundles carry the `.smpc` — does not read as a change because of it.

**3. Entry names are flat: file names only, at the archive root.** This is the maintainers' answer, and it is what sigil
and Argosy already write, so the digests agree for the same files. The receiving client puts each file where the
emulator keeps that file on its own device. If two files of one save share a file name, the save is not uploaded at all
and the client reports why. It does not fall back to one row per file, because that is the shape that pairs wrongly on
`(rom_id, slot)`. No known core writes two such files for one game: Kronos, the one core known to use one name in two
folders, keeps `<stem>.ram` under `kronos/saturn` for Saturn content and under `kronos/stv` for ST-V content (the Kronos
card in emu-atlas's `atlas/data/core_oddities.json`). A save that is itself a folder the emulator keeps per game — PSP's
`SAVEDATA/<id>` directory, for example — keeps `<folder>/<file>` entries, the convention Argosy and grout share; a
directory the emulator merely stores many games' files in is not such a folder.

**4. A bundle holds the files the emulator writes for this game on this device, of the kinds that are the player's
progress.** In:

- `battery` — the game's own save memory, a cartridge clock (`.rtc`) included;
- `memory-card` — a memory card only this game uses;
- `disk-diff` — the changes a game wrote to its own disk image, kept in a separate file;
- `high-score` — an arcade machine's score table.

Out:

- `settings` — the emulator's configuration for the game or the console;
- `notes` — the user's own notes on the game;
- `unknown` — a file whose kind nothing states.

A file several games share, such as a shared memory card, is never part of one game's bundle
([#255](https://github.com/danielcopper/romm-tender/issues/255),
[#901](https://github.com/danielcopper/romm-tender/issues/901)). This program gets both answers — which files, and of
which kind — from [emu-atlas](https://github.com/danielcopper/emu-atlas), which reads the emulator build that is
installed; it keeps no table of its own.

**5. A download is unpacked only when every file in it belongs here.**

1. A download is a bundle when its content is a zip, whatever its name — unless the emulator's own save file for this
   game is a zip (DOSBox Pure's `.pure.zip`), in which case the download is that file.
2. A bundle is unpacked only if every entry is a file the emulator writes for this game on this device. An entry that
   passes that test but is one rule 4 leaves out — a `settings`, `notes` or `unknown` file, or a file several games
   share; Beetle Saturn's `.smpc` is a settings file — is skipped, and the local copy stays. Any other entry blocks the
   whole bundle.
3. A blocked bundle is not unpacked at all, the save stays on the server, and the user is told why. That is also how a
   save from another emulator version or setting surfaces when its file set differs from the one this device writes; a
   file whose format changed under the same name is not caught.
4. A bundle's bytes are never written as a raw save file.
5. Each local file a bundle member replaces is backed up first (this program moves it into `.romm-backup/`).
6. No local file is deleted because the bundle lacks it.

**6. The `emulator` field carries the core's short name, without the frontend and, for now, without a version.** For a
libretro core that is the core file's name up to `_libretro` — `mednafen_saturn_libretro.so` gives `mednafen_saturn`,
and so does an Android build's `_libretro_android.so` — the name Argosy sends. The rule keeps each build's own name, so
`mupen64plus_next_gles2` and `mupen64plus_next_gles3` stay as they are; Argosy folds both into `mupen64plus_next`
(Argosy's `emulator/EmulatorRegistry.kt:1636-1642`), a known difference. A standalone emulator has a fixed name, such as
`dolphin` or `pcsx2`. This program's `retroarch-` prefix goes. No version travels for now; the reasons are under
Alternatives considered.

## Consequences

**What changes in this program.** Uploads bundle a save of several files (#2016). Downloads unpack a bundle only under
rule 5, and a save that fails it is reported instead of being skipped silently (#2017). The check whether a save changed
since the last sync moves from one hash per file to one comparison per save, member by member, ignoring the clock file
and every file rule 4 leaves out (#2018). New uploads carry the core's short name as `emulator` (#2019).

**Accepted costs.**

- **The server records a new version when a file is added.** A lone `.srm` that gains its `.rtc` becomes a bundle, and
  the server hashes the archive, so the slot gets one new row. Clients compare member by member (rule 2), so no other
  device takes that row as a change on account of the clock file alone.
- **Beetle Saturn bundles differ from Argosy's.** Argosy's carry the `.smpc`, a settings file; this program's do not, so
  the two digests differ for the same game. Argosy's Saturn bundles can still be restored here: rule 5.2 skips their
  `.smpc`, which is not written, and rule 2 leaves it out of the comparison, so the save then stays in sync on this
  program. Argosy compares with sigil's `identity_hash` (Argosy's `repository/SaveCacheManager.kt:155`), which leaves
  out only the clock members (sigil's `src/save_unit.c:331`), so it still counts the `.smpc`.
- **A score table travels like any other file, so for now the newer copy wins.** Merging two tables entry by entry would
  be the right answer for `high-score` and is not done yet.
- **New uploads land in a new server folder.** The `emulator` is a storage folder, so dropping `retroarch-` puts new
  rows beside the old ones rather than with them. Pairing ignores `emulator`, so sync is unaffected.
- **Per-file rows from earlier syncs stay in their slots on the server.**

**Open questions, carried by the sub-issues.**

- Whether the first bundle upload supersedes the per-file rows already in the slot or leaves them (#2016).
- Whether a `.saves.zip` uploaded with a core name as `emulator` becomes eligible for RomM's streaming restore, which
  picks archives by emulator name without a slot filter (#2016).

**An optional version field on a save is a question for RomM.**

## Alternatives considered

The upload name of a slotted save carries no meaning, so the names below are weighed by what a person reads in RomM's
save list, and by what a client that derives a local file name from the upload name does anyway.

- **sigil's `<first member>.zip`.** Rejected: it names the bundle after one of its files, so `Game.srm.zip` reads as a
  zipped `.srm` rather than a save of several files.
- **`<rom>.zip`, Argosy's default-channel name.** Rejected: it reads like a zipped ROM, and Ludo, which keeps a
  download's stem and adds `.srm`, would write it over `<rom>.srm`, where `<rom>.saves.zip` becomes a stray
  `<rom>.saves.srm` beside the save (`sync_core.py:10574`, `:10593`).
- **A bundle whenever the core could write more than one file.** Rejected: it zips a lone `.srm` that grout and Ludo
  expect raw, and it decides by what a core might write where sigil and Argosy decide by what is there.
- **Once a bundle, always a bundle.** Rejected: a second file that appeared once would force a zip on the slot for good,
  which the clients that expect a raw file cannot read; and the slot's history, the only record of it, is pruned
  whenever a client asks for `autocleanup` or the server caps the versions per slot
  (`backend/endpoints/saves.py:131-136`), so a device cannot rely on it.
- **Entries keeping the core's subfolder.** Rejected: it departs from the maintainers' answer, its digest for the same
  bytes differs from sigil's and Argosy's because entry names are hashed, and it bakes one machine's directory layout
  into the save.
- **Settings files inside the bundle.** Rejected: a settings file is machine-local; carrying it overwrites settings the
  user chose on the other device.
- **A version inside `emulator`.** Rejected: `emulator` is a storage folder on the server, so a version in it gives the
  server a folder per build; and the value would change with every core update, so two devices on different builds would
  never agree on it.

## Related

- [ADR-0034](0034-a-save-is-answered-by-the-emulator-that-writes-it.md) — the emulator's live answer names the files and
  their kinds.
- [ADR-0017](0017-client-baseline-detection-authoritative-negotiate-is-transport.md) — this program decides every sync
  action itself; negotiate is the transport.
