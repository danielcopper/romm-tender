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
folder of its own: the 3DO and Neo Geo cores write below `saves/3do/opera/per_game` and `saves/neogeo/fbneo`, and the
plugin, looking in `saves/3do` and `saves/neogeo`, found nothing. It also kept the migration's own state: two
`kv_config` markers holding the last-seen flags and the flags before an unmigrated change, a notice asking the user to
migrate, and a rule that every sync honoured the OLD layout while a migration was pending.

The resolver reproduces RetroArch's own path rule, sort flags included, so its directory already carries whatever
sorting is in force. Measured on the reference machine before the change: every syncable answer for an installed game
named the directory the plugin's math produced, byte for byte, so moving to the resolver's directory moves no path the
plugin synced.

What a directory answer alone cannot tell is where a game's save WAS. That is the question the migration answered by
computing the old path from the old flags.

## Decision

**1. The directory every sync, probe, rename and move uses is the resolver's.** `get_rom_save_info`'s `saves_dir` is
`SaveAnswer.directory`, and nothing is joined onto it. An answer with no directory is never given one: each reader takes
its existing refusal or skip. Saves written beside the content are read off the answer's `root_kind` rather than off
`retroarch.cfg`, and stay gated off exactly as before. The adoption rename asks the resolver for the save and the
savestate directory of both launch paths; the new one need not exist.

**2. A moved directory is followed per game, from a recorded answer.** `rom_save_sync_states.answered_save_dir` holds
the directory the resolver last answered for that ROM. It is compared, never used as a location. At every sync entry
point, before any refusal, today's answer is compared with it: nothing recorded — record it; the same — nothing;
different — move the game's files from the recorded directory to the answered one, then record the new one. A name
present in both directories is never overwritten: the older copy goes through the save-backup funnel. A one-time
background pass on the first start records every installed ROM that has no record yet.

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
follow a moved directory before the first launch, at one live reading per game: a median of 137 ms and a p90 of 393 ms
per ROM on the reference machine, on one core. For a library of hundreds that is minutes of background I/O on every
start to catch a change that is rare and that the next sync of each game catches anyway. The one-time pass pays that
once, to fill the record in.

## Consequences

- **A save-sort migration pending at the update cannot be followed.** Where a user flipped a sort flag and had not
  migrated yet, the backfill records today's answer and the old directory is known to nothing. Games whose saves are on
  the server recover them at their next sync; a save only on the device stays in the old folder. Stated in the user
  guide.
- **The content-directory gate is per ROM.** The whole-library sweep no longer has a machine-wide verdict; it passes
  such a ROM over inside its run and still reports the content-directory skip, or names the ROMs it held back.
- **A refusal may write one field.** A refusing answer still names a directory, and first sight records it, so a refused
  ROM can carry an `answered_save_dir` and no other sync state.
- **3DO and Neo Geo saves are found**, because the plugin now looks where their cores write.
- **The record lives on the save-sync state, not the install**, so it survives an uninstall and a re-download.
