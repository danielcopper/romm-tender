# Invariants: Install and update

The installer, the pre-install check, an update's two records, what the panel shows of the journal, the release tarball,
and one-time steps across releases. How an entry reads and binds, and the other areas: [the register's index](index.md).

- **The backend never writes or removes `update-failure.json`; only the installer does — `install.sh` writes it on an
  automatic rollback and, with `"kind": "check"`, when its pre-install check refuses an update's new version, and
  removes it after an update whose new version answered** — test + prompt-only — the record is the installer's statement
  that an update did not go through — that it rolled the update back, or that its pre-install check refused the new
  version before anything was stopped or replaced — and the notice on Main, the failure block under Settings › Updates,
  the start-up WARNING and the live `new_version_does_not_start` failure all rest on that. A backend that removed it
  would take the notice down before the user saw it, with nothing on disk saying the update failed; one that wrote it
  would claim a rollback or a refusal the installer never made. The pre-install check is the new version's own
  `backend/check.py`, and it never builds under the state root this record lives in (its own entry, below). So a record
  the backend finds no longer standing — its `restored_version` is not the running version
  (`domain/update_outcome.py::standing_update_failure`) — is ignored rather than cleaned up.
  `tests/adapters/test_update_failure.py::TestOnlyTheInstallerWritesTheRecord` reads the syntax tree of every backend
  module outside `_vendor/`: only `adapters/update_failure.py` and `domain/update_outcome.py` (the constant's home) may
  name `UPDATE_FAILURE_FILENAME`, by name, attribute or import, or carry the literal outside a docstring; and the
  adapter may call nothing named like a write, move or removal (`write`, `unlink`, `remove`, `rename`, `replace`, …) and
  may `open` nothing with a mode string that holds `w`, `a`, `x` or `+` — read wherever it stands, since `open` takes
  the mode second and `Path.open` first, or as `mode=`. What it cannot read counts as a write: anything but a constant
  from the second argument on, a constant second argument that is not a string (`os.open`'s flags), a `mode=` that is
  not a constant, any `flags=`, and an argument unpacked with `*` or `**`. It sees names, calls and constants, so it
  misses a call in `domain/update_outcome.py`, which may name the record and whose calls it does not read, a write under
  a name not on its list, a runtime mode passed as `Path.open`'s first argument, a record path assembled from pieces or
  handed in from elsewhere, a write through a helper in another module, a call reached through `getattr`, and a
  subprocess. The installer's half — the write on a rollback, before the restored version starts, the write on a version
  the pre-install check could not build or that crashed it, and the removal once a later update's new version answered —
  is pinned by `tests/scripts/test_install_sh.py` (`TestAnUpdateThatDoesNotStart`, `TestTheNewVersionIsCheckedFirst`,
  `TestAnUpdateThatStarts::test_a_later_update_that_starts_removes_the_record_of_one_that_did_not`)
- **`update-attempt.json` is written and removed by the backend alone, through `adapters/update_attempt.py`; the
  installer never touches it** — test + prompt-only — the record is how a start tells an installer that stopped without
  updating from any other start on the same version: the backend writes it right before the installer starts, and the
  start after judges it. An installer that wrote or removed it would decide what that start reports, and the two records
  of an update would no longer have one writer each — this one the backend's, `update-failure.json` the installer's.
  `tests/adapters/test_update_attempt.py::TestTheBackendIsItsOnlyWriter` reads the source: no backend module but
  `adapters/update_attempt.py` and `domain/update_install.py` names `UPDATE_ATTEMPT_FILENAME` or the literal, and
  neither `install.sh` nor a file under `scripts/` (`*.sh`) or `bin/` spells the literal. It reads names, so a record
  path assembled from pieces or handed in from elsewhere, a write through a helper, and a subprocess slip past it.
  **Prompt-only**: only `UpdateInstallService` calls the adapter's `write` and `remove`
  ([UpdateInstallService notes](../backend-architecture.md#updateinstallservice-notes))
- **No journal line reaches the panel with an admission token in it: every line read back from the journal is shown
  through `domain/update_output.py`'s `output_section` or `installer_section`, which replace the value of every
  admission-token spelling they know with `[hidden]`** — test + prompt-only — every start logs the address the panel is
  loaded from, admission token included, on stderr, which under the service is the `romm-tender` unit's journal
  (`host/runtime.py`; only the log file's formatter redacts it, `host/logging_setup.py`). The window "Show what the
  installer said" shows the failed version's lines from that journal after a rollback, so without the rule the panel
  would print a token — a dead one, since each process draws its own, but a token all the same, on a screen that can be
  photographed or shared. The token is not known by value to the reader (it belonged to another process), so the rule
  hides it by pattern, in three spellings: a query's `token=…`, the same percent-encoded inside another URL's parameter
  (`%3Ftoken%3D…`, `%26token%3D…`), and a JSON or Python mapping's `"token": "…"`; a value ends at the first character
  outside `secrets.token_urlsafe`'s alphabet, which is what `host/access.py::new_token` draws. The parameter's name is
  held equal to `host/access.py::TOKEN_PARAM` by a test, since `domain/` may not import the host.
  `tests/domain/test_update_output.py::TestHideToken` pins the address line's shape, each spelling, a token among other
  parameters and a word that merely ends in `token`, and `TestInstallerSection::test_the_token_is_still_hidden` that the
  installer's part hides it too; one service case
  (`tests/services/test_update_output.py::TestAfterARollback::test_answers_the_installer_s_run_and_what_the_failed_version_printed`)
  and one contract case
  (`tests/contract/test_update_output.py::test_after_a_rollback_both_runs_are_answered_with_the_token_hidden`, the only
  one end to end) assert `[hidden]` in the failed version's part. **Unseen by it**: a token printed in any other shape —
  under another name, encoded twice, split across lines, or holding a character outside that alphabet. **Prompt-only**:
  a new reader of journal text answers through `output_section`, `installer_section` or `hide_token`, never with the raw
  `JournalEntry.message` ([UpdateOutputService notes](../backend-architecture.md#updateoutputservice-notes))
- **The pre-install check (`backend/check.py`) never builds under a live root: its code root is the tree being checked,
  every other root and the runtime directory are absent or empty when it starts, neither copy lands where it is copied
  from** — test + prompt-only

  And the live database is read without a file created or removed beside it, and with no write to one but a reader's
  marks in the WAL index

  The check runs while the installed version still runs and before anything is stopped, and its whole promise is that a
  refusal changes nothing. A build under a live root would break that where no refusal shows it: the bin root's launcher
  replaced by the new version's before the new version is installed, the settings migrated in place, the running
  backend's port note overwritten under the runtime directory. `install.sh::check_the_new_version` names every root —
  the code root as the staged tree, the others and `XDG_RUNTIME_DIR` under the run's temporary directory — because a
  root left out falls back to the one the running version uses, and a panel install hands the installer the live ones in
  its environment. `backend/check.py` refuses, before copying anything, a root the environment does not name, a code
  root other than its own tree, a data or config root that is where the live data is copied from, and any other root
  that already holds something. `adapters/live_data_copy.py` reads the database through SQLite in a way that creates
  nothing beside it: a read-only open inside a transaction where a WAL with its index, or a journal, shows a connection,
  an `immutable=1` read where there is neither a WAL nor a journal, and a byte copy opened under the check's own root
  where a WAL has lost its index. `tests/test_check.py` runs the real entry over a fake install whose home and runtime
  directory it compares by names, modes and bytes around a check that builds and around ones that do not, names each
  root in turn as a live one (`TestItRefusesToBuildOnALiveRoot`), and copies the checked tree to show the build leaves
  nothing in it; `tests/adapters/test_live_data_copy.py` holds the database's directory to its names and bytes for each
  of the three reads — the index's bytes aside where a connection holds the database open — and for an index left
  without its WAL; and
  `tests/scripts/test_install_sh.py::TestTheNewVersionIsCheckedFirst::test_the_check_runs_the_staged_tree_with_every_root_its_own`
  holds the installer to naming every root outside the live ones. **Unseen by them**: a constructor that writes only
  where RetroDECK's or Steam's own paths exist, which the test home has neither of; a write the build makes by
  `user_home` or an absolute path rather than under a root it was handed; and a write to the live database the copy's
  change detection cannot see (`adapters/live_data_copy.py` names it)
- **From the press that starts an install attempt until it fails, everything a pending RetroDECK migration refuses is
  refused with `blocked_by_update`, and so is the migration itself, asked ahead of every other rule** — test +
  prompt-only

  At every `hold` / `hold_start` call that names the migration rule, at `migrate_retrodeck_files`, and at every direct
  migration check outside the rules

  The installer stops this process without waiting for anything, so work that starts after the press is cut short where
  it stands: a download leaves a partial file the next start removes, a save sync leaves a run half done, a file move
  leaves a record naming where the files no longer are. The press checks that no work of this process is in flight —
  each kind the panel names, and every claim held on the prune conflicts but those of endpoints that only read, or write
  nothing a restart can cut short (`READ_ONLY_CLAIMS` in `domain/update_install.py`, whose comment is the one list of
  the writes they may make), which covers every other endpoint that names the prune rule and every lease a frontend
  still holds; this rule keeps anything new from starting behind that check, and a use case that names both rules asks
  the update rule again once its operation is registered, so a press that came while it waited to register cannot slip
  in between. The migration rule already names every endpoint that touches local game data, so the update rule rides on
  it rather than keeping a list of its own, and it is asked first because an update in progress is the answer the user
  can do nothing about but wait. The migration itself names no migration rule — it is what that rule waits for — yet
  starting it is new work, so it names the update rule alone; `UPDATE_ONLY` in
  `tests/contract/test_conflict_refusals.py` pins it as the one such site
  (`test_the_update_rule_stands_without_the_migration_rule_only_where_pinned`). Source readers hold the two together:
  `tests/_conflict_rules.py::call_sites_with_rule` compares the `hold` / `hold_start` calls one by one
  (`test_every_call_site_naming_the_migration_rule_names_the_update_rule`), and
  `functions_checking_migration_without_update` finds any function under `backend/services/` that reads the migration
  check directly without also reading the update one, and finds none
  (`test_every_direct_migration_check_is_answered_by_the_update_rule_too`). The functions that read it directly today —
  the save engine's pre-launch and post-exit backstops and its save-directory follow, and the post-exit sync in
  `SessionLifecycleService` — read both, and `test_the_read_behind_the_direct_check_sees_the_checks_it_is_about` holds
  that the read sees them, so the empty answer is not a blind one. They read names in the source, so a check reached
  under another name, through a local alias or through a helper slips past them.
  `test_an_update_in_progress_refuses_the_endpoint` and
  `test_an_update_in_progress_answers_before_every_other_condition` drive every endpoint over the real wiring, and
  `tests/lib/test_conflict_rules.py` pins the order. **Prompt-only**: the rule is taken in the same loop turn as the
  check the press passed — an `await` between them would let work start in the gap — and it is given back only by an
  attempt that failed while this process runs, never on a guess while the installer may run; a successful install ends
  it with the process ([UpdateInstallService notes](../backend-architecture.md#updateinstallservice-notes))
- **A shell function whose value is taken with `$(...)` never reaches `exit` — it answers, and its caller aborts** —
  check — `scripts/check_shell_answer_functions.py` over `install.sh`, `scripts/package.sh`, `bin/tender-rom-launcher`
  and every `*.sh` under `scripts/` and `bin/` (the launcher is named because it carries no extension for the glob to
  find). `exit` inside a command substitution ends that subshell and nothing else, so such a function prints its message
  and the CALLER runs on with an empty answer — a second complaint about the emptiness, or a request built out of it,
  and non-zero either way, which is why the shape survives a test that reads only the status. Which helper ends the run
  is DERIVED (a function reaches `exit` if it runs one or calls a same-file function that does), so a second abort
  helper is covered the day it is written; the check follows the chain and names it. Its reading is a hand-written lexer
  — quotes, comments, heredocs, arithmetic, expansions, `$( )` and backtick nesting — rather than a bash parser, **so a
  construct it misreads drops real code in silence**: the failure is a function never collected or a body that ends
  early, and the `exit` below it is then simply not there. Eleven such shapes are read for by name — a closing `}`
  judged by what FOLLOWS it (an unquoted `${x}` or a `find … -exec rm {} \;` ended the enclosing function), a `}`
  written as an ARGUMENT (`echo }`) and a brace group opened after `!` (`if ! { exec 3< /dev/tty; }`, where the `{` was
  not a block's while its `}` was), `$(( 1 << 3 ))` read as a heredoc (which blanked the rest of the file) and a
  `$(( … ))` span ending one parenthesis short, a parameter expansion naming a function read as a call to it, a `case`
  arm's `)` ending the substitution it sits in, the POSIX arm written `(a)` whose leading parenthesis groups nothing,
  the fallthrough terminators `;&` and `;;&`, a `( … )` subshell inside a substitution whose closing parenthesis would
  otherwise end it, and a backtick substitution inside double quotes read as string text. The enumeration is the
  script's docstring; this is its summary, and the two are re-derived together. **The blind spots left are the list in
  the script's own docstring**, which is their one home; it names, among others, a function reached through a variable
  (`install.sh`'s own `step` is the live example), `( f )` and `f | cmd`, and a function defined twice at the top level.
  A call written inside `$( )` is deliberately NOT an edge in the graph — an `exit` there ends the subshell — so a
  nested pair is reported once, at the inner site
- **The release tarball is what the installer expects — one top-level `romm-tender/`, the files an install starts from
  plus the version file, the installer an installed tree rolls back with and the licence texts a distributed copy
  carries, nothing the packager prunes, a sidecar `sha256sum -c` accepts** — check — `scripts/check_release_tarball.py`,
  in CI's build job over a tarball packed from that build and in the release job over the one uploaded. It reads names,
  modes and digests and starts nothing; the script's docstring states what that misses
- **A one-time step — an installer move such as the covers' move into the cache root, a backend backfill behind a
  `kv_config` marker, a rung of the database's `user_version` ladder or of the settings' version ladder — stays safe to
  run again and stays in every later release** — test + prompt-only

  So an update that skips releases still gets it; one leaves only deliberately, with that release's notes naming the
  oldest version it can be updated from directly

  `tests/scripts/test_install_sh.py::TestAnUpdateThatSkipsARelease` updates from one release straight to a later one
  over covers still under the data root and asserts they reach the cache root with the database and settings unchanged:
  it proves the covers move runs on every update, over data left in the older layout. It cannot see a step being removed
  from a later release, so that half is prompt-only, and so is every backend step: nothing mechanical sees the backend's
  steps. **Why:** an update is a jump from whatever release a machine is on to the newest, not a walk through every
  release between them — the installer downloads one tarball, and a device that was off for a month skips every release
  of that month. A step that ran in 1.3 and was deleted in 1.4 is therefore never run on a machine that goes from 1.2 to
  1.5, and what that step moved or filled is simply missing there, with nothing failing. **Safe to run again** is the
  other half: every update runs every step still present, so a step that assumes it has not run yet damages the machines
  where it has. The installer's steps are idempotent by construction (`move_covers` in `install.sh` never moves over a
  file the cache already holds); a backend backfill is guarded by its `kv_config` marker, a database rung by
  `PRAGMA user_version` (`adapters/sqlite_migrations.py`), a settings rung by the stored `version`
  (`domain/state_migrations.py`). A step retired deliberately takes its floor with it: that release's notes name the
  oldest version it can be updated from directly
