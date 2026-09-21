# The launcher lives in ~/.local/bin, and a shortcut is ours by one ending

## Status

Accepted. Supersedes [ADR-0032](0032-shortcuts-are-rewritten-in-place.md)'s first decision — the launcher's home — and
retires the transition that decision's second half described.

## Context

Every Steam shortcut this program writes names one file as its `exe`: a pure exec wrapper that runs the launch command
baked into the shortcut's launch options. Where that file lives is the one thing about a shortcut the program cannot
repair from inside, because Steam holds the path and a user with a broken `exe` has a library of games that do not
start.

ADR-0032 moved it out of the plugin folder, to `<data root>/bin/rom-launcher`, and repointed the existing shortcuts
through `ShortcutRelocationService`. That answered the question the plugin loader posed — a directory deleted whole on
every update — and it put an executable in the directory that holds the database.

The program now installs itself (#1902). Its code goes to `~/.local/lib/romm-tender/`, which an update replaces and an
uninstall removes; its data, cache and state go to the XDG roots. The XDG base directory specification names
`$HOME/.local/bin` for a user's own executables, and names it as a path rather than through a variable. That is where an
executable other programs invoke belongs, and it is a directory an uninstaller has no business emptying.

## Decision

**1. The launcher is installed at `~/.local/bin/tender-rom-launcher`.** The bin root is a seventh field on
`AppDirectories` (`TENDER_BIN_DIR`, then the built-in default), so the installer resolves it once and writes it into the
unit like the others. It is one of the two roots not named after this program — the other is the code root, which is
wherever the program was installed — and this one because it is shared, so the directory is created at whatever the
umask says rather than owner-only, and nothing under it is ever treated as ours to remove.

**2. The file is renamed with the move.** `bin/rom-launcher` becomes `bin/tender-rom-launcher`. A bare `rom-launcher` in
a directory shared with every other program the user installed is a name with no owner on it.

**3. A shortcut is ours by one ending, and the ending is the new one.** `/bin/tender-rom-launcher`, which
`frontend/src/utils/steamShortcuts.ts` and `backend/services/prune/requests.py` each match as their own literal, and
which `domain/user_data_location.py` derives for the backend's own use. A shortcut naming any launcher an earlier
version wrote is foreign: not recognised, not repointed, not counted, not pruned.

**4. `ShortcutRelocationService` stays, unchanged.** It answers which of OUR shortcuts are not at the launcher's home
and stamps its completion when none are. What it can still find is a shortcut written against a different
`TENDER_BIN_DIR`; what it will no longer find is anything written before this release.

## Consequences

- **A user coming from 0.33 loses every shortcut.** Their shortcuts name the old launcher, which this version does not
  recognise, so the sync neither adopts nor repoints them and the panel counts none of them. The way through is to
  remove all non-Steam shortcuts and sync again. That costs the artwork Steam holds for them and any per-shortcut
  setting the user made in Steam itself; the library, the installs, the saves and the settings are untouched, because
  none of them is keyed on a shortcut.
- **The data root holds nothing executable.** What is in it is CONTEXT.md's "The program's directories" entry; the point
  here is only that none of it is a program. A backup of that root is data, and restoring it puts nothing on the machine
  that can run.
- **The uninstaller leaves the launcher.** Every shortcut names it, and removing the sync tool is no reason to stop a
  user's games from starting. The cost is one file left behind, named after this program so it can be found.
- **A `TENDER_BIN_DIR` whose last component is not `bin` breaks ownership silently.** The suffix is two components, so a
  launcher installed anywhere not called `bin` is written, is executable, launches games — and is foreign to every
  ownership test in this program. Nothing can check it: the value arrives from the environment.
- **One transition is retired and none replaces it.** There is no second suffix, no stamp and no state in which a
  shortcut is half ours.

## Alternatives considered

**Repoint the old shortcuts instead — `ShortcutRelocationService` already does exactly this.** The mechanism exists, is
wired, and the measurement behind it holds: rewriting every one of 826 shortcuts' `exe` left every appId in place, none
lost and none new. It was rejected for what it costs afterwards rather than for risk. Recognising two endings means the
ownership question has two answers for as long as anyone runs a build that wrote the old one, and every place that asks
it — the frontend's `steamShortcuts.ts`, the prune request validator, the relocation selector — carries both. The move
to a self-hosted install is the one moment where a clean break costs a single release's users a documented step instead
of costing every later reader a transition state that nothing ever removes.

**Keep the launcher in the data root and only rename it.** Rejected: it leaves an executable in the directory that holds
the only copy of the user's library, and it spends the same shortcut break on a smaller gain.

**Install to `~/.local/lib/romm-tender/bin/` beside the code.** Rejected for the reason ADR-0032 gave in the first
place: an update replaces the code directory, and a shortcut's `exe` must not name a file inside a directory something
else replaces.

## Related

- [ADR-0032](0032-shortcuts-are-rewritten-in-place.md) — the previous home, and the relocation mechanism this keeps.
- [ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md) — the user's data leaves the program's directories.
- [ADR-0009](0009-launcher-pure-exec-wrapper-baked-launch-options.md) — what the launcher is, and why ownership is read
  off the `exe` path at all.
- [ADR-0036](0036-the-backend-hosts-itself.md) — the backend runs as its own process, which is what made the install
  layout this program's own question.
