# User data lives under the user's home, not in the plugin's directories

## Status

Accepted. **Revises the "package name stays" decision recorded in
[#1536](https://github.com/danielcopper/romm-tender/issues/1536)** — specifically its first half, that the package name
is fixed at `decky-romm-sync` permanently because Decky keys `~/homebrew/settings/<pkg>` and `~/homebrew/data/<pkg>` off
it and a rename would cost "a settings-and-database migration". After this decision the data location no longer depends
on the package name at all, so that cost is gone; the second half of #1536's reasoning — the launcher path baked into
every Steam shortcut's `exe` field — is untouched here and is what the shortcut cut answers.

## Context

Decky derives every per-plugin directory from the plugin's **package name** (`decky_loader/plugin/sandboxed_plugin.py`),
and Decky's CLI derives the package name from the directory CI checked the repository out into
(`FilenameSource::Directory`). Neither is chosen by anything in this repository, and neither can be read back from
inside a running plugin as a decision anyone made.

That is not a theoretical hazard. #1536 decided the package name would stay `decky-romm-sync` **permanently**, with the
data-location cost as the leading reason — and the repository rename in the same wave changed the checkout directory,
which changed the package name, which moved `settings.json` and `romm_sync.db` for every user at 0.31.0. Nothing warned,
nothing failed: the plugin simply started against an empty database in a new directory while the user's library sat in
the old one. The warning card that now exists (#1865) was written after the fact, and it can only ask the user not to
delete the older install.

So the data location is decided by a string this project does not control, that a routine repository operation can
change, and whose change is silent and irreversible from inside. Every further packaging decision — the display name,
the store listing, the shortcut path — has been argued against that constraint rather than on its own merits.

Two smaller facts shape the answer.

**The plugin's environment is root's.** A Decky plugin descends from a root systemd system service. The unit sets only
`UNPRIVILEGED_PATH`, `PRIVILEGED_PATH` and `LOG_LEVEL`, and `sandboxed_plugin.py` adds `HOME`, `USER` and the `DECKY_*`
values on top. So the XDG environment variables are either unset or — if something did set them — describing root's
directories rather than the user's.

**There may be two libraries.** A user who has run both spellings of the folder can have a real library under each. A
migration that picked one silently would be choosing which of the user's two libraries survives as the live one, on
evidence no better than folder order.

## Decision

### 1. Two roots, both built from the user's home

`~/.config/romm-tender/` holds `settings.json` and its backups; `~/.local/share/romm-tender/` holds `romm_sync.db`, the
cover cache, the SteamGridDB artwork cache and the legacy `save_sync_state.json`. Both are built by
`domain/user_data_location.py` from `decky.DECKY_USER_HOME`.

The split is the XDG basedir split, and it is kept because the two halves genuinely differ: one is small, hand-editable
user intent that a user may reasonably want to back up or copy between devices, the other is a cache-and-state tree that
is large, rebuildable in part, and of no interest outside the machine. A single folder holding both invites backing up
268 MB of library data to keep a server URL.

**`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are deliberately not read** — see the environment fact above. Reading them would
be correct on a normal desktop process and wrong here in exactly the way that is hardest to notice: the plugin would aim
at root's directories, which either lands the library somewhere no user will ever look or fails outright, and every test
in this repository passes either way because none of them reads the environment. The home directory is used instead
because Decky reads it out of the user's own account.

Logs stay with Decky. `DECKY_PLUGIN_LOG_DIR` is read nowhere in this codebase and continues not to be: log rotation and
collection are the loader's job, and a plugin that hid its logs somewhere else would be harder to support, not easier.

### 2. The migration is a copy, run before the database is opened

`bootstrap()` performs it through `adapters/user_data_migration.py`, with the ladder as a pure decision in
`domain/user_data_location.py`. The full ladder is documented at
[Backend Architecture → Where user data lives](../architecture/backend-architecture.md#where-user-data-lives); the parts
that are decisions rather than mechanics:

- **Copy, never move, and never delete.** The source stays complete and untouched, so a migration that went wrong costs
  a support conversation instead of a library. A `README.txt` is left in each source saying what it is and that it can
  be deleted.
- **The whole directory travels.** A curated list of what comes along rots the moment a file is added — the copy takes
  the directory as it finds it, backups and caches included. Measured at 4.11 s for 268 MB on the maintainer's Steam
  Deck, which is not a cost worth optimising against a rotting list.
- **Staging plus `os.rename`.** The copy lands beside the target on the same filesystem and is renamed on, so an
  interrupted copy (a Steam restart, a standby) leaves a directory the next start discards and never a target root that
  looks finished.
- **A target root holding anything is done, and is never asked about again.** The sources are never deleted, so any rule
  that kept consulting them would raise the same question on every start for the rest of the install's life. It is also
  what makes the older databases stop being opened: the ladder takes its source probe as a thunk and never calls it once
  this rung settles, so a settled install pays nothing. The known cost is that a user who downgrades, works in the old
  version and upgrades again keeps the already-migrated state silently; that is accepted.
- **A recorded answer is obeyed only where the location it names is still there.** The folder names are known in
  advance, so a location is always NAMED whether or not anything stands behind it — obeying an answer for a folder the
  user has since deleted would copy an empty directory into place, settle the rung above for good, and strand the
  surviving library with no question ever raised again.
- **The two target roots are probed independently.** A root that will not answer takes only its own half down with it.
  Probing both under one guard means an unreadable data root un-settles a settings half that had already migrated, and
  the plugin silently reverts to the pre-migration `settings.json` — with every edit since lost the moment a good start
  reads the new root again.
- **"Has a library" is a row count, not a file.** `bootstrap()` creates an empty database on the first start of every
  install, so file existence is true from the first second and proves nothing. The source's database is opened read-only
  and asked `SELECT COUNT(*) FROM roms`; any error reads as "no library" rather than propagating.
- **A failure keeps the plugin on the Decky directories.** Starting against an empty new root while the data sits in a
  source is indistinguishable from data loss, so the failing half keeps reading and writing where the data is, the panel
  says what did not work, and the next start tries again.

### 3. Two libraries is the user's question, and it is answered across a restart

Where both older locations hold a library the plugin copies **nothing** and raises a notice whose button opens a modal
naming both candidates with path, size and last-changed date. The choice applies to both halves together — a database
paired with the other install's settings is a state that never existed.

The answer is **recorded, not executed**. The plugin is running from one of the two candidates with its database open,
and copying a live SQLite file risks a torn copy, so the answer goes to a small file in the Decky-assigned runtime
directory and the plugin's next start acts on it. The modal offers a DEVICE restart to reach that start, not a Steam
restart: restarting the Steam client reloads the frontend and does not start the plugin's backend again. The recorded
answer is deleted once the migration it named has completed.

### 4. `runtime_dir` keeps its old meaning, and gains no new one

`RuntimeBundle.runtime_dir` stays the Decky-assigned runtime directory. `LegacyInstallService` asks it a question about
Decky's own layout — is the pre-rename plugin folder still standing beside ours — by taking its parent, and pointing it
at the new data root would make it ask about a directory Decky never created, silently taking down the warning that
keeps a user from deleting the install their shortcuts launch through. Everything that follows the **data** reads
`WiringConfig.locations` instead. Two questions, two fields, and neither answers for the other.

## Consequences

- **The data location no longer depends on the package name.** Whatever the folder is called next, the library stays
  where it is. This is what #1536's first reason was protecting, and it is now protected by construction rather than by
  a promise not to rename.
- **The old folders remain, with a note in them.** Disk cost until the user deletes them; the alternative was deleting a
  user's only copy on the strength of a copy we had just made.
- **A support answer changes.** "Where is my database" is now one path under the user's home for every install, rather
  than a path that depends on which release wrote it.
- **A downgrade reads the old location.** An older build knows nothing about the new roots and will open the Decky
  directories, which still hold everything as of the migration. Work done in the downgraded build stays there, and the
  upgraded build does not pick it up — rung 1 has already settled. Stated rather than defended against: the alternative
  is a rule that re-examines the sources forever.
- **Nothing here touches the launcher.** Shortcuts still point at `exe` paths under Decky's plugin folder, so the
  pre-rename install must still not be removed. That is the shortcut cut's problem, and #1865's card still states it.

## Alternatives considered

- **Stay in Decky's data directory and pin the package name harder.** Rejected. That is the status quo #1536 chose, and
  the rename at 0.31.0 is the evidence against it: the name is derived from a CI checkout directory, so "we will not
  rename it" is a promise about something the project does not hold. It also leaves every future packaging decision
  (store listing, display name, repository) arguing against a data-migration cost that has nothing to do with it.
- **One collector folder in the home** (`~/.romm-tender/`, or one folder holding both halves). Rejected. It solves the
  same problem, and a single dotted folder in `$HOME` is the older Unix convention with real momentum on a Steam Deck.
  What it loses is the split: settings and 268 MB of library data end up in one tree, so any backup of the small,
  irreplaceable half drags the large, rebuildable one with it, and neither the user nor a future cut can separate them
  without a second migration. The convention it would follow is also the one the desktop it runs on has moved away from.
- **XDG with the environment variables honoured.** Rejected on measurement, not on principle: the process environment is
  root's, so honouring them is a silent write into `/root` on any system that sets them. If Decky's sandbox ever hands
  the plugin the user's own environment, honouring them becomes a one-line change and the roots do not move for anyone
  who has the defaults.
- **Move instead of copy.** Rejected. It halves the disk cost and removes the "which copy" question outright, and it
  makes a failure mid-way a partial library in neither place. A copy that fails leaves the user exactly where they were.
- **Ask the user before migrating at all.** Rejected. The overwhelming case has exactly one library and one right
  answer, and a dialog asking permission to do the obvious thing on first start teaches users to dismiss dialogs. The
  question is asked only where there genuinely is one.

## Related

See also: [Backend Architecture → Where user data lives](../architecture/backend-architecture.md#where-user-data-lives)
(the roots, the ladder and the wiring), [Database Design](../architecture/database-design.md) (the database's path),
[QAM Panel → Notices and homes](../architecture/qam-panel.md#notices-and-homes) (the notice and its modal),
[Where Your Data Lives](../user-guide/where-your-data-lives.md) (what the user is told).
