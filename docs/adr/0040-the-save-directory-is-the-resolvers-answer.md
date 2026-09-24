# The save directory is the resolver's answer, and a moved one is followed per game

## Status

Accepted. Completes [ADR-0034](0034-a-save-is-answered-by-the-emulator-that-writes-it.md) for the directory: that
decision took the NAMES of a save from the emulator and left where they sit to this repo's own path math.

## Context

After ADR-0034 the resolver answered which files a game's save consists of, and also where they are — but the plugin
used only the first half. Every path it opened was built by `domain/save_path.resolve_save_dir` from RetroArch's two
sort flags, read out of `retroarch.cfg` by `adapters/retroarch_config.py`, plus a RetroArch core name read out of the
core's `.info` file when saves were sorted by core. The same math, with a different set of flags, told the save-sort
migration where a game's save had been before the user flipped a flag.

That kept two readings of one machine alive side by side, and they disagreed wherever an emulator keeps its saves in a
folder of its own: the resolver places 3DO's Opera saves below `saves/3do/opera/per_game` and Neo Geo's FinalBurn Neo
saves below `saves/neogeo/fbneo`, while the plugin's math looked in `saves/3do` and `saves/neogeo`. It also kept the
migration's own state: two `kv_config` markers holding the last-seen flags and the flags before an unmigrated change, a
notice asking the user to migrate, and a rule that every sync honoured the OLD layout while a migration was pending.

The resolver reproduces RetroArch's own path rule, sort flags included, so its directory already carries whatever
sorting is in force. Measured on the reference machine before the change, over the platforms its RomM library holds: all
245 syncable answers named the directory the plugin's math produced, byte for byte, so moving to the resolver's
directory moves no path the plugin synced there. 3DO and Neo Geo were not among those platforms.

What a directory answer alone cannot tell is where a game's save WAS. That is the question the migration answered by
computing the old path from the old flags.

## Decision

**1. The save directory is the resolver's.** `get_rom_save_info`'s `saves_dir` is `SaveAnswer.directory`, and nothing is
joined onto it; a sync, a probe, the adoption rename and the directory follow all use it. An answer with no directory is
never given one: each reader takes its existing refusal or skip. Saves written as a file beside the content are read off
the answer's `root_kind` rather than off `retroarch.cfg`, and stay gated off, now per ROM. A save written inside the
content file is anchored in the same directory and keeps its own save-shape refusal, as before. The adoption rename asks
the resolver for the save and the savestate directory of both launch paths; the new one need not exist.

**2. A moved directory is followed per game, from a recorded answer.** A table of its own, `answered_save_directories`,
holds the directory the resolver last answered for each ROM. It is compared with today's answer and read as the source
of the move, never where a sync or a probe looks. At the sync entry points, and on the write paths that touch local
saves, before either looks at a local file, today's answer is compared with it: nothing recorded — record it; the same —
nothing; different — move the game's files from the recorded directory to the answered one, then record the new one. An
answer anchored in the content's directory is neither followed nor recorded. A name present in both directories is never
overwritten: the older copy goes through the save-backup funnel. A one-time background pass on the first start records
the answer for each installed ROM that has no record yet, and the RetroDECK home migration replaces each installed ROM's
record with today's answer once it has moved the files, or drops the record where that answer cannot be followed. The
mechanics are in
[Following a moved save directory](../architecture/save-file-sync-architecture.md#following-a-moved-save-directory).

**3. What went.** `resolve_save_dir`, `domain/save_layout.py`, `adapters/retroarch_config.py`, the save-sort migrator
and its two `kv_config` markers (deleted by migration 024), the start-up detection step, the notice, the Settings
section and the game-page banner, the three save-sort callables and the `save_sort_changed` event, and the
machine-vector conformance tier whose subject was the removed kernel. `sanitize_save_filename` and
`compute_local_save_target` stay: they harden a server-supplied name and hold no emulator knowledge.

## Alternatives rejected

**Computing the old directory again.** The resolver is upstream's; the documented way to ask it "where was this save
under the previous flags" is to hand it a machine whose `retroarch.cfg` says the old flags. That is a disguised
`retroarch.cfg` reader kept alive only to recompute a past answer, and it would still need the old flags recorded
somewhere — the markers this decision removes. A recorded directory is the same fact without the machinery that derives
it.

**A library-wide sweep at every start.** Comparing every installed game's answer with its record at start-up would
follow a moved directory before the first launch, at one live reading per game: 186 answers took 36 s on the reference
machine, on one core (a median of 137 ms and a p90 of 393 ms per ROM). For a library of a few hundred games that is
about a minute of background I/O on every start to catch a change that the next sync of each game catches anyway. The
one-time pass pays that once, to fill the record in.

## Consequences

- **A save-sort migration pending at the update cannot be followed.** Where a user flipped a sort flag and had not
  migrated yet, the backfill records today's answer and the old directory is known to nothing. Games whose saves are on
  the server recover them at their next sync; a save only on the device stays in the old folder. Stated in the user
  guide.
- **The content-directory gate is per ROM.** The whole-library sweep no longer has a machine-wide verdict; it passes
  such a ROM over inside its run and still reports the content-directory skip, or counts the ROMs it held back. With no
  confirmed slot at all it asks about the first installed ROM that launches with a RetroArch core, so that skip is still
  reported.
- **Nothing is followed into the content's own folder.** For a multi-file game that folder is removed whole on
  uninstall, so an answer anchored there — beside the content file or inside it — leaves the record as it is. This holds
  until the content-directory gate is lifted.
- **A refusal may be recorded.** A refusing answer can still name a directory, and first sight records it — in
  `answered_save_directories`, which is not sync state; a refusal still writes none.
- **The record has its own table**, because a `rom_save_sync_states` row means "tracked for save sync" and the record is
  written for games that never were. It hangs off `roms` rather than the install, so it survives an uninstall and a
  re-download.
- **3DO and Neo Geo saves are now looked for where their cores write**; that has not yet been observed on a device.
