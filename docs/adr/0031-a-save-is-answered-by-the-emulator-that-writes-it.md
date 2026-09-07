# A save is answered by the emulator that writes it, live, in one of five states

## Status

Accepted. **Retires the plugin's own per-system save knowledge** (`domain/save_extensions.py`) in favour of a live
reading through the vendored [emu-atlas](https://github.com/danielcopper/emu-atlas) resolver. **Builds on
[ADR-0030](0030-the-emulator-catalogue-is-read-by-the-vendored-resolver.md):** that cut made the resolver the reader of
ES-DE's catalogue, which is what lets this one ask a catalogue **entry** where its emulator writes saves instead of
mapping plugin labels onto resolver entries through a bridge. **Does not touch
[ADR-0017](0017-client-baseline-detection-authoritative-negotiate-is-transport.md)** — which files are synced changes;
how a file's sync is decided does not. Tracked under
[#1858](https://github.com/danielcopper/decky-romm-sync/issues/1858), cut 4 of
[#1660](https://github.com/danielcopper/decky-romm-sync/issues/1660).

## Context

Which files make up a game's save was a hand-maintained table in this repo, keyed by RetroDECK system: a default of
`.srm` / `.rtc` / `.sav` plus eight per-system overrides, written from a one-pass desk audit.

Measured against the resolver on a stock RetroDECK, for the systems the table had entries for. The table could state one
answer per system; the machine answers per **content file**, so where that matters the row says which one it was asked
with:

| System         | Content | The table                      | The machine                                           |
| -------------- | ------- | ------------------------------ | ----------------------------------------------------- |
| Neo Geo Pocket | `.ngp`  | `.flash`, from a desk audit    | `.flash`, read out of the core                        |
| Amiga CD32     | `.chd`  | `.nvr`                         | `.nvr`                                                |
| Amiga CD32     | `.bin`  | `.nvr`                         | nothing established                                   |
| Amiga          | `.adf`  | `.nvr`                         | no separate file — the save is inside the disk image  |
| Amiga          | `.hdf`  | `.nvr`                         | nothing established — PUAE's mode could not be read   |
| Sega CD        | `.chd`  | `.brm`                         | a shared BRAM card, not a per-game file at all        |
| Sega CD        | `.bin`  | `.brm`                         | per-game `<stem>.srm`                                 |
| 3DO            | `.chd`  | the three default extensions   | `<stem>.0.srm`, with a version digit the table lacked |
| Saturn         | `.chd`  | three extensions, all progress | two are progress, one is console configuration        |

**The failures were invisible.** For Amiga the plugin searched forever for a file that cannot be there; for 3DO it
searched for a name no core writes. Neither surfaced as an error — the exact-name probe simply found nothing, which is
indistinguishable from "this game has no save yet".

Two deeper problems were not fixable by adding rows.

**A save is not a property of a platform.** It is a property of the ROM and of the emulator that opens it, on two axes
at once.

The emulator axis: the same PS2 game keeps two shared memory cards under standalone PCSX2 and could keep a file per game
under a libretro core.

The ROM axis: the answer turns on the **content file's own extension**. Measured at emu-atlas 0.13.0, PUAE puts an Amiga
`.adf`'s save inside the disk image, states a directory it cannot name the contents of for a `.lha`, and establishes
nothing for an `.hdf`; Genesis Plus GX keeps a Sega CD `.chd` on a shared BRAM card and a `.bin` in a per-game `.srm`.
This axis is easy to miss and expensive to get wrong — two people measuring the same systems with differently shaped
content reach opposite conclusions and both are right.

A table keyed by system can express neither axis, so it answered for whichever emulator and whichever content shape the
author had in mind.

**Some shapes are not syncable at all, and the plugin had no way to say so.** A shared card, a save written inside the
game file, a path or name whose middle is the game's own identity — the per-game model is simply wrong for each, and the
table answered them all with a list of extensions to go looking for. The sync then probed, found nothing, and reported
"no saves", which is a different and much more comforting sentence than the truth.

## Decision

**1. The save question goes to the emulator, live, on every path.**

A new seam (`services/protocols/paths.py` → `SaveLocationReader`, implemented by `adapters/atlas_saves.py`) answers per
ROM: the directory the emulator opens, the link-resolved backing directory, the concrete file names, each file's role,
the holes, the granularity, and the resolver's caveat codes. Services see a `domain.save_answer.SaveAnswer` and never a
resolver type.

The question is put to the **catalogue entry** the plugin resolved for this ROM — the label `ActiveCoreResolver`
produced, which is the label the launch bakes — not to a bare core. That is what lets a standalone emulator answer for
itself, and it keeps the read path and the launch path on one emulator.

It is also put with the ROM's **real** content path, never a synthetic stem: `RomInstall.file_path` for an installed
ROM, and the path built from `roms.fs_name` for one the library holds but has not installed. Where no path can be formed
at all, the answer is "not established". Every per-system pinning test names the content extension it asked with, for
the same reason — a pin that does not say which extension it used is pinning nothing.

**Nothing is cached but the installation handle.** The user changes a core's options in the emulator's own quick menu
between one launch and the next sync; a remembered granularity would have the plugin carry a shared card as though it
belonged to one game. Holding the handle is what keeps a live reading affordable — 170 ms warm against 490 ms cold on
the reference machine — and no write this plugin performs can invalidate it.

**2. Five states, exactly one of which holds, and four of them refuse.**

`per-game files` is the one this plugin can carry. `shared`, `inside the content`, `hole` and `not established` refuse:
no path is probed, no sync state is written, and the result is the benign-skip shape (`save_shape_unsupported`) rather
than a failure — the same shape the `savefiles_in_content_dir` skip has returned since #239.

The last state keeps **three shapes** — `nothing_established`, `directory_known` and `not_asked` — because they are
different sentences to a reader: a folder we can point at is not the same as a folder nobody has heard of, and neither
is the same as a question that never reached the resolver, which is what an uninstalled game or a content-dir machine
produces.

**3. A configuration-role file is never synced.**

The answer states each file's role. A file that holds the emulator's settings rather than the player's progress is
machine-local by nature, so carrying it to another device overwrites settings the user chose there. It stays visible on
the wire, flagged, and a directory move still carries it — splitting one save across two directories breaks the game as
surely as leaving the battery file behind.

**4. The table is deleted, not kept as a fallback.**

A fallback would fire exactly where the resolver refuses, which is where guessing is most dangerous: it would put the
Amiga probe back, and it would answer a shared card with a per-game name.

## Consequences

**A refusal is now sayable.** The states reach the game-detail payload in this cut and are rendered in the next one, so
a user will be told that their PS2 saves live on a shared card rather than being shown "no saves".

**Five systems on the reference machine lose a sync they never really had.** Where the plugin resolves no bakeable
emulator (Apple II, Apple IIGS, Macintosh, PS Vita, Xbox) there is no entry to ask, so the answer refuses. Those
platforms found nothing under the old extension list either; what changes is that the plugin now says so.

**Four things a user with existing saves can notice.** Saturn syncs one file fewer — the `.smpc` console-settings file
is no longer carried, which is the configuration rule working as intended. The post-exit failure toast stops appearing
for every refusing system, because a refusal is a benign skip rather than an error. A server-side file this emulator's
answer does not name — a `.smpc` uploaded before the role rule existed, or a legacy `.sav` from the guessed-extension
era — is no longer pulled down over the local file, on the sync path and on the slot-switch path alike. And a save that
is left where it is stays on the server: nothing here deletes it.

**A live reading costs time.** About 170 ms per ROM warm. Single-ROM paths absorb it beside their network round-trip;
the per-platform loops behind the save-count and save-delete buttons pay it per ROM, so a fifty-game platform count
takes seconds where it took milliseconds. Caching display answers is permitted and deliberately not done here — the
correctness rule is that every sync path asks live, and a cache that a future entry point forgets to invalidate breaks
it silently.

**The plugin's own save-path math is untouched, and for two systems it now disagrees with the answer.** The names come
from the resolver; the DIRECTORY still comes from `resolve_save_dir`. Opera keeps 3DO saves in
`saves/3do/opera/per_game` and FinalBurn Neo keeps Neo Geo saves in `saves/neogeo/fbneo`, while the plugin probes the
flat system folder for both. **So 3DO and Neo Geo saves are still not found** — exactly as before this change, since the
old extension list had the name wrong as well as the directory. What improved is that the name is now right.

Fixing the directory is deliberately a separate change rather than part of this one: discovery and the save-sort
migration must agree on where a save lives, and moving one without the other reopens the race the migration's markers
exist to prevent. `compute_local_save_target` still builds the download name, now folded against the answer wherever the
answer names the file.

**The answer is only as good as the resolver.** A future emu-atlas bump can change what a system answers, so
`tests/adapters/test_atlas_saves.py` pins today's answer for every system the deleted table covered. That tier drives
the real machine and skips where none is installed, so it is a developer's pre-push gate rather than a CI one.

## Alternatives rejected

**Keep the table and consult the resolver only where the table is silent.** The table's errors were in the systems it
_did_ cover, so this preserves exactly the wrong half.

**Ask for a bare core rather than a catalogue entry.** Then a standalone emulator has no answer at all, which is most of
the interesting cases — PCSX2, Dolphin, RPCS3, PPSSPP.

**Report a refusal as a sync failure.** It is not a failure; nothing went wrong and the game still launches. Reporting
it as one would put an error toast on every launch of a PS2 or MAME game.

**Sync the shared card anyway, keyed to whichever game touched it.** This is the data-loss shape the state exists to
prevent: the card holds other games' progress, and a per-game download would overwrite it on every device.
