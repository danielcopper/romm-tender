# The launcher leaves the plugin folder, and shortcuts are rewritten in place

## Status

Accepted. **Answers the half of [#1536](https://github.com/danielcopper/romm-tender/issues/1536) that
[ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md) left open** — the launcher path baked into every Steam
shortcut's `exe`, which ADR-0031 named as "the shortcut cut's problem" and did not touch.

## Context

Decky deletes a plugin's folder whole before it unpacks the new zip. Every shortcut this plugin has ever written names a
file inside that folder — `<plugin>/bin/rom-launcher`, the pure exec wrapper of
[ADR-0009](0009-launcher-pure-exec-wrapper-baked-launch-options.md) — so for the length of an update no game can start,
and an update whose unpack fails leaves them broken with nothing able to repair them: the plugin that would put the file
back is the one that is not there. The same holds the moment a user removes the pre-rename install, which is what the
#1865 card exists to ask them not to do.

#1536 decided the package name would stay `decky-romm-sync` permanently, and gave two reasons. ADR-0031 disposed of the
first (Decky's per-plugin data directories) by taking the data out of them. The second was this one, and it rested on a
belief about Steam: that changing a shortcut's `exe` re-derives its `appId`, because the `appId` was thought to be
`CRC32(exe + appName)`. That derivation was disproven on device in 2026-07 — 68 live shortcuts matched no CRC32
candidate, and the ids are spread uniformly across `[0x80000000, 0xFFFFFFFF]` — the half that is negative read as a
signed int32, which is also what all 828 of this device's shortcuts are — consistent with assignment at creation. What
survived in the docs was the conclusion without its premise: "an `exe` change is applied by delete + recreate", marked
as no longer verified and believed anyway.

The cost of that conclusion is not small. A delete + recreate gives Steam a **new** `appId`, and an `appId` is what
Steam keys a game by: its recorded playtime, its artwork, its collection membership, and its per-game Steam Input
profile all belong to the id, not to the name or the exe. Applying it to a whole library also means one `AddShortcut`
per game — the renderer-heap cost the session-budget gate exists to keep away from
([ADR-0024](0024-session-budget-rss-gate.md)).

## Decision

### 1. The launcher's home is `<data root>/bin/rom-launcher`

It sits beside the database, under the user's own home, and it is installed by `bootstrap()` on **every** plugin start —
written to a staging file beside the destination and renamed on, and only when what is already there is not this
release's copy. Two properties follow from that shape and neither is incidental: a launcher a game is executing right
now keeps its inode (bash reads a script as it runs it, so a replacement written in place is read out from under the
interpreter mid-line), and a launcher is never visible at its own path half-written.

Installing on every start rather than once is what keeps the launcher and the release together. A once-only install
would freeze the launcher at whatever version happened to be running the day it moved, and the file is small enough that
comparing it costs a read.

The last two components are load-bearing: a shortcut is recognised as this plugin's by its `exe` **ending** in
`/bin/rom-launcher` (`src/utils/steamShortcuts.ts`, `py_modules/services/prune/requests.py`). The new home ends the same
way, so every shortcut written before the move is still ours, with nothing to change on either side.

**It waits for the data migration, and that ordering is load-bearing.**
[ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md)'s first rung reads a target root holding **anything**
as already migrated, so a launcher written into an empty data root would settle that rung for the life of the install
and the user's library would never come across — with no failure, no notice and nothing in the log. The install
therefore runs only on a start whose data half already stands at that root, decided off what the migration just returned
rather than by probing the directory again. A start that has not got there installs nothing, creates nothing, and points
new shortcuts at the copy the release ships inside the plugin folder — where they pointed before this cut, and which
still runs.

### 2. Existing shortcuts are rewritten in place, never deleted and recreated

At frontend start the plugin rewrites `exe` and `startDir` on each of its shortcuts that does not already carry the new
path, through `SetShortcutExe` / `SetShortcutStartDir`. The `appId` does not move, so playtime, artwork, collections,
Steam Input profiles and the `roms.shortcut_app_id` binding all survive untouched.

**Measured, not assumed.** On the maintainer's device: 828 non-Steam shortcuts, 826 of them ours, every one rewritten in
a single pass. The `appId` set afterwards was identical to a `shortcuts.vdf` backup taken before it — 0 new, 0 lost,
names unchanged — and the 826 calls cost 12 ms of renderer time. No delay is paced between them: the 50 ms the apply
loop uses belongs to a **newly added** shortcut waiting for its overview to register, and an in-place `Set*` on a
shortcut that already exists waits for nothing.

The rewrite runs at frontend start rather than when the QAM panel is opened, because a user can launch a game without
ever opening the panel.

### 3. The backend says WHICH shortcuts, off `shortcuts.vdf`, once

Which shortcuts still name a plugin folder is a question about every non-Steam shortcut's `exe`, and an app **overview**
does not carry one. The frontend's only route to it is `RegisterForAppDetails` per shortcut, which makes Steam load and
cache a fat details object each time — 828 of them at every start, against the renderer heap budget
[ADR-0024](0024-session-budget-rss-gate.md) exists to protect. The backend reads the same fact out of `shortcuts.vdf` in
one 315 KB parse and hands the frontend a ready list of app IDs plus the `exe` and `startDir` to write. The frontend
stops asking and only writes.

Two shapes in that file are not obvious and both fail quietly: Steam has written the keys in more than one case, so they
are matched case-insensitively, and the id is stored **signed** while every `SteamClient` API takes the unsigned form.

**It is a one-time task with a recorded completion**, in the shape of a schema migration: once a run has rewritten
everything a reading found, that is stamped in `kv_config` and no later start reads the file again — a plugin start
already carries enough checks for this not to become a permanent one. The stamp is written only for a run that issued
every write it was given; a reading that could not be done, or a pass that stopped part-way, leaves the question open
for the next start.

**Nothing clears the stamp, deliberately.** A shortcut that turns up later carrying the old path — restored from a
backup, written by a downgraded build — stays on it, and no start will look again. It keeps launching, because the
package still ships `bin/rom-launcher` and the old path is a real file; what it does not keep is the panel's agreement,
since the card reads this stamp as "nothing points into the pre-rename install any more" and offers its removal on the
strength of it. Clearing the stamp on Force Full Sync was considered and rejected — it would only ever reach a user who
had already diagnosed the shortcut, and that button carries enough meanings already.

### 4. The shipped copy stays, and the old path keeps working

The package still ships `bin/rom-launcher`; it is the source the installer copies from. A shortcut nobody has rewritten
— written by an older release, on a machine where the rewrite has not run yet — still points at a real file and still
launches. Nothing about the old path becomes an error.

## Consequences

- **The plugin folder becomes code only, and disposable.** An update that deletes it, or fails to unpack into it, no
  longer stops games from starting.
- **The #1865 card changes its sentence rather than disappearing.** Once no shortcut points into the pre-rename install,
  the card that asked the user not to remove it tells them they now can, and where — and that statement, alone among the
  panel's notices, carries a Dismiss that hides a condition which is still true, because keeping the older install is a
  legitimate end state.
- **Two kinds of start install nothing, and they differ.** One cannot migrate the data, so nothing is attempted at all;
  the other is settled and its write failed. Neither rewrites a shortcut and both keep the card on its first statement,
  but only the first leaves the home untouched — the second leaves it empty, which is why the path a new shortcut is
  built against follows the install rather than the migration. Both name the copy the release ships, exactly as before
  this cut.
- **The display name is still unmeasured.** The `exe` measurement above says nothing about what `SetShortcutName` does
  to an `appId`, and the sync writes the name in place alongside the exe. Do not read one as covering the other.
- **A Steam client that never runs this frontend keeps its old shortcuts.** The rewrite is a frontend action; a library
  synced from a device that has not started the new version keeps pointing into the plugin folder there.

## Alternatives considered

- **Delete and recreate every shortcut** — what #1536 assumed was required. Rejected on the measurement above: it was
  believed necessary because of a derivation that does not hold, and its cost is the loss of everything Steam keys by
  `appId` (playtime, artwork, collection membership, per-game Steam Input profiles) plus one `AddShortcut` per game
  across the whole library.
- **Leave the launcher in the plugin folder and pin the folder name harder.** Rejected. The name is derived from the
  directory CI checks the repository out into (ADR-0031), so it is not the project's to pin — and it would not help
  anyway: Decky deletes the folder before every update whatever it is called.
- **Install the launcher once, on the first start that finds it missing.** Rejected. The launcher would then be whatever
  version the day of the move shipped, forever, while the plugin that hands it its arguments moves on.
- **Let the frontend find the shortcuts itself**, through the `RegisterForAppDetails` sweep it already runs once per
  sync. Rejected on cost and on where the fact lives: the sweep would move from once per sync to once per start, each
  call materialising a details object in the renderer's heap, to read something the backend can parse out of one file. A
  variant that swept only the app IDs the backend has bound was rejected too — it would miss an unbound orphan, and the
  card's "nothing points into the older install" would then claim more than was checked.
- **A third location of its own** (`~/.local/bin`, or a folder beside the two roots). Rejected. It would be a third
  thing to migrate, a third thing to explain in [Where Your Data Lives](../user-guide/where-your-data-lives.md), and it
  buys nothing the data root does not: the launcher is already outside everything Decky deletes.

## Related

See also: [ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md) (the data roots this one hangs the launcher
under), [ADR-0009](0009-launcher-pure-exec-wrapper-baked-launch-options.md) (what the launcher is and why it owns no
state), [Steam Non-Steam Shortcuts](../architecture/steam-non-steam-shortcuts.md) (the `Set*` calls and the appId
evidence), [Backend Architecture → Where user data lives](../architecture/backend-architecture.md#where-user-data-lives)
(the roots and the start-up install).
