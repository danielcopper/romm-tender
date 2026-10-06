# Invariant register

The invariant register in the repository's `CLAUDE.md` lists the cross-cutting safety rules: the ones that span files,
so no diff-scoped review sees the whole rule. There, each rule is one binding statement with its enforcement tier and
what enforces it, if anything. This page holds the long form of every entry, in the same order: why the rule exists,
what breaks without it, and where it lives in the code.

The statement in `CLAUDE.md` is the rule. Where this page and that statement disagree, the statement wins and this page
is the one to correct.

Format: **invariant** — tier — enforced by.

- **Endpoint failures use `{success, reason, message}` (never `error` / `error_code`); a module on `CONVERTED_MODULES`
  builds no failure shape — a service there raises its refusal, which `Endpoints` answers; anything an endpoint raises
  but a refusal or a `RommApiError` stays a transport error** — check + test + prompt-only —
  `scripts/check_failure_shape.py --check` (returned dicts in `services/`, and every literal, `error_response` call or
  refusal-helper spread in a listed module), `tests/test_endpoints_translation.py` (on every route, a raised `Refused` /
  `DomainRefused` answers that shape and any other exception stays `backend_exception`; on one `def` and one `async def`
  route, a `RommApiError` answers `classify_error`'s reason and a returned `PartialFailure` is serialized into it; every
  route is the translating wrapper). Prompt-only: no entry is ever taken off `CONVERTED_MODULES`, and a further type
  joins `main._TRANSLATED` only by decision
- **A definitive 404 is `not_found`, never `server_unreachable` — a catch-all `except Exception` in `services/` may not
  bind a verdict key (`reason` / `status` / `recommended_action`) to a hardcoded `SERVER_UNREACHABLE`; route the
  exception through `classify_error`, or peel the 404 off with a sibling `except RommNotFoundError` where the verdict is
  a partial-success flag** — check — `scripts/check_404_not_unreachable.py --check`
- **A 404 becomes `RommNotFoundError` only when RomM's entity layer is proven to have answered it; only the three
  byte-stream fetches opt out** — test + prompt-only — `TestNotFoundDiscrimination` plus the per-call-site
  `test_generic_route_404_still_raises_not_found` trio in `tests/adapters/romm/test_http.py`; a fourth byte-stream
  fetch's opt-out is prompt-only — `.claude/rules/romm-http.md`
- **Every RomM request goes out through `RommHttpAdapter._urlopen`, the single point that clears the known-unreachable
  state — a request method calling `urllib.request.urlopen` itself leaves every retry ladder degraded to one attempt
  until an unrelated path happens to succeed, and nothing else fails** — check — `scripts/check_urlopen_choke_point.py`
  (structural, AST call sites — an alias or a `getattr` would slip past it. Which requests may skip the ladder, and
  which pass `romm_origin=False` because they do not talk to RomM at all, stays prompt-only in
  `.claude/rules/romm-http.md`)
- **Frontend↔backend endpoint parity (names + arity)** — check — `scripts/check_endpoint_parity.py`
- **Every backend `emit` event name has a frontend listener, and vice versa** — check — `scripts/check_event_parity.py`
- **`settings.json` is written only by its owner (`adapters/persistence.py`)** — check —
  `scripts/check_settings_owner.py`
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
  ([UpdateInstallService notes](backend-architecture.md#updateinstallservice-notes))
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
  `JournalEntry.message` ([UpdateOutputService notes](backend-architecture.md#updateoutputservice-notes))
- **The pre-install check (`backend/check.py`) never builds under a live root: its code root is the tree being checked,
  every other root and the runtime directory are absent or empty when it starts, neither copy lands where it is copied
  from, and the live database is read without a file created or removed beside it, and with no write to one but a
  reader's marks in the WAL index** — test + prompt-only — the check runs while the installed version still runs and
  before anything is stopped, and its whole promise is that a refusal changes nothing. A build under a live root would
  break that where no refusal shows it: the bin root's launcher replaced by the new version's before the new version is
  installed, the settings migrated in place, the running backend's port note overwritten under the runtime directory.
  `install.sh::check_the_new_version` names every root — the code root as the staged tree, the others and
  `XDG_RUNTIME_DIR` under the run's temporary directory — because a root left out falls back to the one the running
  version uses, and a panel install hands the installer the live ones in its environment. `backend/check.py` refuses,
  before copying anything, a root the environment does not name, a code root other than its own tree, a data or config
  root that is where the live data is copied from, and any other root that already holds something.
  `adapters/live_data_copy.py` reads the database through SQLite in a way that creates nothing beside it: a read-only
  open inside a transaction where a WAL with its index, or a journal, shows a connection, an `immutable=1` read where
  there is neither a WAL nor a journal, and a byte copy opened under the check's own root where a WAL has lost its
  index. `tests/test_check.py` runs the real entry over a fake install whose home and runtime directory it compares by
  names, modes and bytes around a check that builds and around ones that do not, names each root in turn as a live one,
  and copies the checked tree to show the build leaves nothing in it; `tests/adapters/test_live_data_copy.py` holds the
  database's directory to its names and bytes for each of the three reads — the index's bytes aside where a connection
  holds the database open — and for an index left without its WAL; and
  `tests/scripts/test_install_sh.py::TestTheNewVersionIsCheckedFirst::test_the_check_runs_the_staged_tree_with_every_root_its_own`
  holds the installer to naming every root outside the live ones. **Prompt-only**: a constructor that writes only where
  RetroDECK's or Steam's own paths exist, which the test home has neither of; a write the build makes by `user_home` or
  an absolute path rather than under a root it was handed; and a change to the live database that the copy's detection
  cannot see, which `adapters/live_data_copy.py` states
- **From the press that starts an install attempt until it fails, everything a pending RetroDECK migration refuses is
  refused with `blocked_by_update`, and so is the migration itself, asked ahead of every other rule — at every `hold` /
  `hold_start` call that names the migration rule, at `migrate_retrodeck_files`, and at every direct migration check
  outside the rules** — test + prompt-only — the installer stops this process without waiting for anything, so work that
  starts after the press is cut short where it stands: a download leaves a partial file the next start removes, a save
  sync leaves a run half done, a file move leaves a record naming where the files no longer are. The press checks that
  no work of this process is in flight — each kind the panel names, and every claim held on the prune conflicts but
  those of endpoints that only read, or write a cache they can build again (`READ_ONLY_CLAIMS`, which a restart cuts
  nothing short of), which covers every other endpoint that names the prune rule and every lease a frontend still holds;
  this rule keeps anything new from starting behind that check, and a use case that names both rules asks the update
  rule again once its operation is registered, so a press that came while it waited to register cannot slip in between.
  The migration rule already names every endpoint that touches local game data, so the update rule rides on it rather
  than keeping a list of its own, and it is asked first because an update in progress is the answer the user can do
  nothing about but wait. The migration itself names no migration rule — it is what that rule waits for — yet starting
  it is new work, so it names the update rule alone; `UPDATE_ONLY` in `tests/contract/test_conflict_refusals.py` pins it
  as the one such site (`test_the_update_rule_stands_without_the_migration_rule_only_where_pinned`). Source readers hold
  the two together: `tests/_conflict_rules.py::call_sites_with_rule` compares the `hold` / `hold_start` calls one by one
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
  it with the process ([UpdateInstallService notes](backend-architecture.md#updateinstallservice-notes))
- **Where this program's directories are is resolved once from the environment, and every consumer reads them off
  `AppDirectories`** — prompt-only — `domain/app_directories.py` is the ladder (`TENDER_*`, then XDG, then the built-in
  defaults) and it is pure: the environment is handed in, so every rung is checkable against a table. `main.run()`
  resolves it once and hands it to `bootstrap()`, which derives nothing, and `RuntimeBundle` carries no directory at all
  — it used to carry two, and that is how a question about a plugin loader's own layout came to sit beside a question
  about the user's data as two plain `str` fields on structs the composition root passes around. **Counting rule** (an
  AST walk for an attribute in `{config_dir, data_dir, cache_dir, state_dir, runtime_dir,
  code_dir, bin_dir}` whose
  base ends in `directories`): **19 reads over three modules**, `main.py`, `bootstrap/adapters.py` and
  `bootstrap/services.py` — `code_dir` 5, `cache_dir` 4, `data_dir` 4, `state_dir` 3, and one each for `config_dir`,
  `runtime_dir` and `bin_dir`. Re-derive it rather than trusting the number. **One field is read in `main.py` alone**
  and nowhere else: `runtime_dir`, which the port file lives in. `state_dir` is read there twice, by the logging setup
  that opens it and by the injection whose crash record lives under it, and once in `bootstrap/adapters.py`, for the
  record of a rolled-back update. `config_dir` has exactly one reader, `PersistenceAdapter`, and `bin_dir` exactly one,
  the launcher install in `bootstrap/adapters.py`. The pairing that matters is `cache_dir` against `data_dir` — covers,
  artwork and the SGDB artwork cache on the first because they are re-derivable from the server and the database on the
  second because it is not; a system that clears caches must be able to clear one and not the other. The launcher's home
  is the read whose mix-up a user would see rather than the next start only, since
  `launcher_in_bin_dir(directories.bin_dir)` is carried on as `ShortcutLauncher.path` and baked into every shortcut's
  `exe`. `bin_dir` is one of the two fields not named after this program (the other is `code_dir`, wherever the program
  was installed; GLOSSARY.md's "The program's directories" is the home of that split) — it is the directory every
  program a user installs for themselves puts a binary in, which is why nothing under it may be treated as ours to
  remove. Nothing mechanical tells the seven apart: they are seven `str` fields on one frozen struct, so a read of the
  wrong one is a rename away and fails silently in whichever direction it happened to point. **One raw read of
  `TENDER_CODE_DIR` is deliberate and is not a directory read**: `domain/update_release.py::resolve_update_source`,
  called once by `main.run()` beside `resolve_directories`, asks whether the variable was SET and whether it names the
  directory this process's code sits in — which decides whether this is the installed program an update may replace.
  `AppDirectories.code_dir` cannot answer that, because the ladder has already folded "set" and "fell back to where the
  code sits" into one value; the function derives no directory
- **The identifier's four homes are never derived from one another — in particular `APP_DIR_NAME`
  (`domain/user_data_location.py`) is never read from `PACKAGE_NAME` (`domain/identity.py`), and the database's file
  name `DB_FILENAME` (`bootstrap/adapters.py`) from neither** — test + prompt-only — the four homes and the question
  each answers are enumerated in `backend/domain/identity.py`'s module docstring. `APP_DIR_NAME` and `PACKAGE_NAME`
  spell the same string today, so `APP_DIR_NAME = PACKAGE_NAME` reproduces every current path exactly and every value
  comparison stays green — the two are still equal after the fold, which is what makes it invisible; the cost arrives at
  the next package rename, which then moves every user's library on the following start with nothing failing and nothing
  said. `tests/domain/test_identity.py::TestTheIdentifierStaysInSeparatePlaces` therefore asks the module what it
  ASSIGNS rather than what it resolves to: it parses `user_data_location.py` and fails unless `APP_DIR_NAME` is a string
  literal, which is the one answer that cannot be another constant's — a fold through a transform
  (`PACKAGE_NAME.lower()`) is a call node and fails too. The reverse fold, `PACKAGE_NAME = APP_DIR_NAME`, is caught by
  asserting `domain.identity` has no `APP_DIR_NAME` attribute, and **that half is the weaker one**: importing it under
  an alias evades it. The THIRD home is unchecked entirely — `SESSION_BREADCRUMB_KEY` is frontend TypeScript and no test
  on either side relates it to the other three. The FOURTH home, `DB_FILENAME`, is a name over persisted state as
  `APP_DIR_NAME` is: derived from either of the first two, a rename would start every user on an empty database and
  leave the library under a name nothing opens. `test_db_filename_is_its_own_literal_rather_than_a_derived_name`, in the
  same class, parses `bootstrap/adapters.py` and fails unless `DB_FILENAME` is a string literal, so
  `f"{PACKAGE_NAME}.db"`, which reproduces today's value exactly, fails too. The rule is also stated at `APP_DIR_NAME`
  and at `DB_FILENAME` themselves, because a diff that folds either opens neither the docstring nor this file
- **No name in the code calls Tender a plugin; a third-party name that must keep the word is excepted by name, whole,
  with its reason** — check — Tender stopped being a Decky Loader plugin when the backend began hosting itself, and a
  name that still says "plugin" teaches its reader the old model: that something loads Tender and owns its life cycle,
  and that Decky's shapes (`definePlugin`, a plugin folder) are the ones to reach for (GLOSSARY.md → What Tender is,
  which names the words to use instead). The frontend half is a `no-restricted-syntax` entry in
  `frontend/eslint.config.js` over every `Identifier`, `JSXIdentifier` and `PrivateIdentifier` whose name contains the
  word in any case — variables, functions, types, parameters, object keys, JSX attribute names — in every file ESLint
  lints, tests and config files included. `frontend/src/eslintNoPluginNames.test.ts` lints known-bad fixtures through
  the real config and fails if the rule stops reporting any of them. The backend half is
  `tests/domain/test_identity.py::TestNoNameMisnamesTender`, which walks the syntax tree of every Python module under
  `backend/` (less the vendored `_vendor/` and `native/`), `tests/` and `scripts/` and fails on any name a module binds
  or reads that carries the word: a name, an attribute, a parameter, a keyword argument, a function or class, an import,
  its alias or the module a `from` import names, a type parameter, an exception or pattern capture, a class pattern's
  keyword, a `global` or `nonlocal`. Each half has its own list of exceptions — `NAMES_NOT_ABOUT_TENDER` beside the rule
  and `_NAMES_NOT_ABOUT_TENDER` beside the test — for names that carry the word because they name someone else's plugin
  (`DeckyPluginLoader`, `plugins` in the rollup and ESLint configs, `extraPlugins` in `rollup.config.js`, the two
  install tests about a Decky plugin), each with its reason; an exception is matched as a whole name, and either list
  fails on an entry nothing carries any more. **Neither half reads prose**: a string literal is not a name, so the
  `"plugin_version"` key a recovery bundle's manifest carries stays, and a vitest title, a comment or a docstring that
  calls Tender a plugin passes both, as does a shell script, a file name, a name built at run time (`getattr`, a
  computed key), and an excepted name put to a new use for Tender; a Python string annotation (`x: "Settings"`) passes
  the backend half
- **Sync run-lifecycle (`sync_state` / `current_sync_id`) written only via `LibrarySyncStateBox` verbs** — check —
  `scripts/check_sync_lifecycle_owner.py`
- **A library-sync seam is held only by the module owning the job it belongs to: `active_core` / `disc_resolver` /
  `emulator_sources` (where a run takes its one reading of the sources) by
  `services/library/shortcut_launch_resolver.py`, `renderer_rss` / `renderer_gc` by
  `services/library/session_budget.py`, and `artwork` by `services/library/cover_preparer.py` (the apply path's covers)
  **and** `services/library/reporter.py` (commit-time cover-path finalisation) — the one confinement with two owners,
  because those are two different questions a unit asks at two different points; two owners is a named pair, not a
  licence for a third, and the orchestrator that used to be the third holds no `ArtworkManager` at all. The `service.py`
  façade is not a co-owner — it may **pass** a seam on and may not **use** one, which the check reads structurally: a
  seam attribute standing as a call's keyword-argument value, and a seam annotation on a field of
  `LibraryServiceConfig`, are wiring; anything else in the façade is a finding** — check — `scripts/check_seam_owner.py`
  (AST over attributes named after a seam and over annotations naming its Protocol, resolved per seam rather than per
  module — owning one grants nothing about another. It sees the injection, not every later use: a seam aliased to a
  differently-named attribute or local, one reached through `getattr`, one passed positionally into a helper that holds
  it, a quoted string annotation, and a Protocol imported under an alias all slip past it — the last two only on the
  annotation half, because the constructor read that unpacks the config is flagged whatever the field is called). What
  the confinement buys is that each module's transactions stay separable: `shortcut_launch_resolver` holds a read UoW
  across its install-path scan while the `active_core` seam opens its own `BEGIN IMMEDIATE` per ROM, so folding the two
  into one pass deadlocks — on a real device only, since `FakeUnitOfWork` shares no connection. **Nothing mechanical
  stands behind that half.** `check_uow_seam_nesting.py` catches the fold only in its inline form (the seam method named
  inside the `with` block); the peer-call form — `do_build_core_overrides` invoked from inside the install-path readers'
  own UoW, which is what putting the three methods on one class makes cheapest — is that gate's documented blind spot
  and passes green. On the budget side the confinement holds `session_budget`'s stated promise that no renderer-RSS
  reading is taken anywhere else in the package
- **The resolver's installations are detected in one place, `adapters/emulator_sources.py`, through the one
  `RealMachine` the process keeps; every other adapter asks it, and a game's questions go to the source
  `domain/emulator_sources.py::answering_source` names** — test + prompt-only —
  `tests/adapters/test_emulator_sources.py::TestOnlyTheHolderDetects` (no other backend module imports `detect`,
  `every_installation`, `RealMachine` or an installation class from the resolver, star-imports it, imports the
  resolver's package as a module, or reads one of those names off it) and, for the last clause,
  `tests/domain/test_emulator_sources.py::TestAnsweringSource::test_retrodeck_answers_even_when_another_source_is_first_in_the_order`
  and `test_retrodeck_answers_with_emudeck_first_in_the_order` in each of the three adapters' tests —
  `tests/adapters/test_atlas_catalogue.py::TestWhichSourceAnswers`,
  `tests/adapters/test_atlas_firmware.py::TestDegradation` and
  `tests/adapters/test_atlas_saves.py::TestWhichSourceAnswers`. An adapter that detected on its own would pick its own
  source — the old "first detected" — and would build a fresh machine, so every save question would run its core's probe
  again. Unseen by the scan: a name reached through `getattr` or `importlib`. Prompt-only: no adapter keeps a handle or
  an answer past the reading it came through (a panel call's per question, a run's for the run)
- **A module declared read-only calls no repository write — `services/library/local_library_reader.py` to start** —
  check — `scripts/check_read_only_module.py` (AST over the declared file's own calls, matching the two-attribute
  `<...>.<repo>.<method>` shape against the twelve repositories the UoW exposes). Read or write is decided **by the
  name's shape** — `get` / `get_*` / `iter_*` / `count` plus the explicitly listed reads — and
  `tests/scripts/test_check_read_only_module.py` re-derives every method name from `services/protocols/repositories.py`
  and pins its classification, so a new repository method fails there until it is classified. **The two directions are
  not symmetric.** A read named outside the shapes is called a write: loud, safe. A **write named like a read is called
  a read and passes in silence** — `uow.roms.get_or_create(...)` is green today, which is the very accident class this
  entry is otherwise about, so "repository writes are named as writes" stays a prose rule the gate does not carry. It
  also sees only calls the file itself makes, and only as calls: a write behind a helper it calls, a write **passed as a
  bound method** (`run_in_executor(None, uow.roms.save, rom)` — an attribute, not a call, and the exact idiom this
  module's own reads are invoked through), an aliased handle (`repo = uow.roms`), a `getattr`-reached repository, and a
  repository name not in its list all pass green. What it does catch is the plain `uow.roms.save(...)` dropped into a
  read module because a UoW was already open there. The declaration earns its keep by making the boundary checkable
  rather than descriptive: these reads open their own short UoW and are offloaded to an executor at points chosen for
  cheapness, so a write among them would land at a moment nobody picked. The platform stamp's DELETE stayed in
  `sync_orchestrator.py` on those grounds — it is a write, so a read-only module cannot hold it, and it is cohesive with
  the half of the apply pipeline that performs it: the DELETE stayed with `_sync_one_unit`, which builds a unit's delta,
  while the re-stamp went to `ChunkDispatcher._build_final_platform_stamp` with the final chunk whose commit UoW it
  rides, so the stamp's two ends now sit in two modules and each names the other. **Why the DELETE sits exactly where it
  sits in that pipeline is a property of its call site, not of its module** — after the fetch, after the artwork, after
  the cancel guard, before the first chunk (ADR-0023 / #1025) — and that argument lives in full at the call site's own
  comment, which is the only place a move could not have carried it away from
- **An emitted `sync_progress` frame stops a run (`running: False`) only with a terminal stage, and a terminal stage is
  only ever emitted with the run stopped** — test — `tests/services/library/test_terminal_frame_contract.py`
  (structural, AST call sites and dict literals across all of `backend/services` — it covers the error paths a
  behavioural test would have to provoke one at a time, but a frame assembled by a helper it cannot follow, or emitted
  through an aliased callable, slips past it; its own scope tests pin the producers and the root it reaches, so a
  narrowing fails rather than shrinking the rule in silence. Frame producers are not confined to `services/library/`:
  `services/artwork.py` emits through an injected `emit_progress`). The QAM panel derives "a run is in flight" from
  `running` and keys the run's end — the status line, the live-ETA teardown, Main's stats re-read, the Sync page's
  three, the failure toast, and the Sync page's failure line — on the stage, so a stopping frame with a non-terminal
  stage would collapse the in-progress rows while ending nothing. The panel cannot defend against it: a bare
  `running: false` is exactly what the Sync page's own retraction of an optimistic start looks like. Since #1814 the
  frontend's frame store reads the same discrimination for a rule of its own — a run whose stopping frame carried a
  terminal stage AND a run id can never be put back in flight, which is what stops the apply loop's next item from
  resurrecting a run that has already ended — so a stopping frame emitted without a terminal stage would record no
  ending there either, and the freeze that rule removes comes back
- **The KIND of run a `sync_progress` frame belongs to is stated on it (`runKind`), never inferred from it — and a frame
  that states none is rendered as neither of the two answers** — test + prompt-only — the backend half is pinned end to
  end by `tests/services/library/test_sync_orchestrator.py::TestRunKindOnTheWire` (every frame of a preview run and of
  an apply run, both terminal frames, and the `get_sync_status` snapshot) and
  `tests/services/library/test_state.py::TestRunKind` (claimed with the run slot, cleared with it); the frontend half by
  `frontend/src/utils/syncRunView.test.ts` and the slot's three labels in `frontend/src/bigpicture/MainPage.test.tsx`.
  **Nothing joins the thirteen sites it passes through**, counted one per site at the granularity this list names them:
  `LibrarySyncStateBox` holds it with the slot, three separate backend frame builders carry it (`emit_progress`,
  `_finish_sync`'s CANCELLED terminal, and the per-unit ERROR dict literal in `sync_orchestrator.py`), three frontend
  start paths stamp it themselves on the optimistic frame they show before the first real one arrives (`useSyncPage`'s
  `computePreview` as `preview`, its `applyPreview` and `startRunDirectly` as `apply`), `SyncProgress.runKind` and
  `useSyncRunView` pass it through, `MainPage` both seeds it from the `get_sync_status` snapshot onto the store at mount
  and maps it to the slot's label, and two readers announce an apply run's failure off it — the `sync_progress`
  listener's toast in `index.tsx` and `useSyncPage`'s status line. **Nothing mechanical stands behind the seam between
  them**: a fourth frame builder that omits the key, a fourth start path that stamps the kind it is not, or a reader
  that spends the absent case on one of the two answers — a `runKind ?? "preview"`, a `=== "preview"` where the neutral
  branch was — goes green, because each test above pins one half and none of them pins the join. The failure is silent
  and worst exactly where the frontend cannot help itself: after a JS-context rebuild mid-run the store starts empty,
  the snapshot is the only thing that can say what the run is doing, and Main then tells the reader a real apply run is
  merely checking for changes. Why the kind cannot be derived at all is stated at `domain/sync_run_kind.py` and in
  `docs/architecture/qam-panel.md`'s Main section; do not restate it here
- **A press that starts a run clears the previous run's per-unit rows — unless that press is a RESUME, the one start
  they are still true for** — test + prompt-only — `frontend/src/bigpicture/SyncPage.test.tsx`'s "a previous run's rows
  at the next press" pins all three start paths in both directions, and `frontend/src/utils/runUnitsStore.test.ts` pins
  the clear itself. **The rule spans three modules and nothing joins them.** `utils/runUnitsStore.ts` holds the rows and
  offers `clearRunUnits`; `useSyncPage` decides, at each of the three presses that write an optimistic frame
  (`computePreview`, `applyPreview`, `startRunDirectly` — the same three the entry above names); and `index.tsx`'s
  `sync_plan` listener is the only OTHER thing that ever replaces the rows, which is what makes the press the moment
  that matters. The plan arrives after `build_work_queue()` on the two apply paths and never at all on the preview path,
  so a fourth start path that forgets the clear leaves the previous run's `done` rows — with its apply results, and with
  the unit it died in dressed as running by this run's frames — standing over the new run for the length of a work-queue
  build, or for the whole of it. The store's own guards cannot help: they REFUSE a foreign frame, and refusing is not
  clearing. The discriminator can be nothing but the frontend's `syncResumeState(stats).canResume` at the press, because
  a resume is a new run with a new id and the backend has no resume concept at all — no frame, kind or id tells the two
  apart. Both directions fail in silence: forget the clear and another run's rows read as this run's progress, clear on
  a resume and the one start whose rows are true loses them
- **Every reader that decides or predicts a platform skip — the skip gate, the plan estimate and the preview's
  `restamp_platform_count` — reads the stamp through `domain/platform_sync_state.py::stamp_for_skip`, which answers a
  revoked stamp with none; the resume offer's `PlatformSyncStateRepository.has_any` applies the same rule in its query;
  removed-game discovery, the prune canaries, `reachable_count` and the recovery snapshot read the stamp raw; the
  end-of-run stale removal revokes a skip only on a platform outside `processed_platform_slugs`** — test + prompt-only —
  the helper by `tests/domain/test_platform_sync_state.py`; each skip-side reader by tests over a revoked stamp
  (`test_fetcher.py` for the gate and the estimate, `test_sync_orchestrator.py::TestPreviewRestampPlatformCount` for the
  re-stamp count, `test_reporter.py` and the repository's `TestHasAny` for the resume offer); each raw reader but the
  recovery snapshot by one test over a revoked stamp that still tells the rows the stamp's fetch returned from the rows
  it did not (`tests/services/prune/test_preview.py`, `test_registry.py::TestCanaryRomIds`,
  `tests/contract/test_prune.py` over real SQLite, and `test_reporter.py::TestRegistryPlatformsReachableCount`); the
  stale removal by `test_reporter.py::TestFinalizePerUnitRun` and end to end by
  `test_sync_orchestrator.py::TestPlatformTurnedOffAndBackOn`. Why the stamp is kept and why a processed platform keeps
  its skip are in [Backend Architecture](backend-architecture.md#libraryservice-decomposition-serviceslibrary),
  "Incremental skip"; do not restate them here. **Each wrong turn is silent.** A skip gate that read the stamp raw would
  let a platform skip over games it unbound while it was turned off: the preview says "Everything is up to date." and
  those games never get their shortcuts back. A raw re-stamp count offers no Apply to an enabled platform with a revoked
  stamp and an empty delta, so it is never re-stamped and full-fetches on every sync. A raw plan estimate can price such
  a platform as a skip, so the run's progress estimate runs short while the platform full-fetches; a raw `has_any` can
  offer "Resume Sync" on the strength of stamps that save the next run nothing. A raw reader moved behind the helper
  blinds removed-game discovery, the canaries a 404 round asks first, or the reachable count on every platform a removal
  touched, and for a platform whose sync stays off it stays blind, since no new complete fetch comes. A stale removal
  that revoked on a processed platform would take the skip from the stamp that same run just wrote whenever it unbinds a
  game RomM dropped, costing that platform one extra full fetch on the next run each time; one that skipped an
  unprocessed platform brings the turned-off-and-back-on gap back. **What nothing checks**: a new skip-side reader
  calling `platform_sync_state.get` raw passes every test above, because each test pins one existing call site; the
  recovery snapshot's raw read has no test, so moving it behind the helper would drop a revoked stamp from a sealed
  bundle unnoticed; and `has_any`'s `WHERE skip_revoked = 0` and `stamp_for_skip` state the one rule twice — each is
  pinned alone and nothing holds them equal
- **A firmware answer nothing could establish is `unknown`, never `not_needed` — and the distinction survives every
  layer it crosses** — test + prompt-only — `tests/adapters/test_atlas_firmware.py` pins the adapter's degradation (a
  raising resolver, a missing installation, an answer with no root all come back with `resolved` clear, never as an
  empty catalogue reading "nothing needed"), `tests/domain/test_firmware_wants.py` pins the classification, and
  `tests/services/test_firmware.py::TestCheckPlatformBiosUnknown` pins the same listing answering `not_needed` under a
  whole reading and `unknown` under a partial one. The rule spans four modules and no diff-scoped review sees it whole:
  the adapter decides whether the reading happened, `domain/firmware_wants.py` holds the two values apart,
  `services/firmware/status.py` scopes the doubt to the emulator the platform launches with, and both frontend surfaces
  render them as different sentences. **Nothing mechanical stands behind the scoping half.** A future caller that folds
  the two values back together — a truthiness test on a placement, a `wanted != "needed"` bucket, a default of
  `not_needed` where the catalogue is silent — goes green: the collapse is the upstream defect this swap removed, and it
  is one careless `or` away from returning. The scope is the second half, and since #1821 it is ONE emulator rather than
  a platform's whole list: `reading_complete_for` takes the launching emulator's identity and refuses `None` — an
  unresolved pick, or one the resolver could not identify. Read as complete, a `None` is a finished reading of nobody:
  every server file classifies `not_needed`, `required_count` is 0, and the platform reports a green "Nothing required"
  over firmware the emulator will not boot without. **An unread emulator the platform also offers no longer withholds
  the answer**, which is deliberate — it says nothing about a launch that does not use it — and the cost is that
  switching a platform's emulator can move it from a finished answer to a withheld one. `declaration="packaged"` with an
  EMPTY requirement list counts as unread and is the shape most likely to be folded back the wrong way: a card may
  identify its image by content, so it names no file until the bytes are read, and reading the empty list as "wants
  nothing" puts a green all-clear on a PlayStation launching DuckStation. A `retroarch-foreign-core` entry — a RetroArch
  launch of a core file this host cannot load, stated with no identity — declares nothing and so adds nothing: no
  placement, no unread name, no verdict and no group
  (`tests/adapters/test_atlas_firmware.py::TestUnreadEmulators::test_a_retroarch_launch_of_a_foreign_core_adds_nothing`).
  It is not the only entry with no identity: any other the resolver could not identify still contributes the files it
  declares, as wants with no owner
- **A firmware row the RomM library does not hold (`on_server: False`) counts towards readiness, and never towards a
  download affordance or a progress ratio** — test + prompt-only — `tests/services/test_firmware.py` pins the row's
  shape (`id` absent, `on_server` clear), that it raises `required_count`, and that it stays out of `server_count`;
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx` pins that the buttons key off the fetchable set. **The three
  axes live in three places and nothing joins them.** `domain/bios_status.py::count_required` is readiness and counts
  every required row; `services/firmware/status.py::_bios_aggregates` scopes `server_count` / `local_count` to
  `on_server` rows; the download buttons' condition is `isFetchable` (`frontend/src/utils/biosFetchable.ts`), called
  from `frontend/src/bigpicture/library/PlatformDetail.tsx` — and since #1815 the per-row Download button reads the same
  filtered set, so a fourth reader of the axis now exists in that one file. A fifth reads it in the same file for the
  On-disk cell's second mark (`⊘`), and that one is display alone: it neither counts nor gates, which is what keeps it
  out of all three folds below. **A sixth reader is the game page's BIOS tab** (`BiosTab.tsx`'s `rowBelongsOnThisPage`),
  and it is display alone in the same sense: it decides whether a row gets a LINE — a file no page can fetch, that this
  launch does not require and that is not there, is nothing that page can act on — and gates no count and offers no
  action, so it belongs to none of the three folds below either. What holds the two surfaces together is that they call
  one predicate rather than spelling the three clauses twice: a second copy would let the game page point at a download
  button the platform page does not offer, or leave off a row it does. What must NOT be shared is the game page's rule
  around it — required for this launch, the console's own image, present, fetchable, or unjudged — which is that page's
  alone; the platform detail has no such rule, and a shared "visibility" module would invent a notion only one surface
  has. Each fold has its own quiet failure: drop the row from readiness and a platform reads ready while a required file
  is absent; add it to the ratio and a SNES page reports `0 / 26 files, 26 missing` for twenty-six optional files no
  core wants; add it to the buttons and the page offers a download that cannot succeed. `on_server` is the one field all
  three read; the row's `id: None` is an honest absence with no consumer at all, so nothing breaks if it is filled in
  and nothing is guarded by leaving it empty
- **No BIOS answer outlives the page that asked for it** — test + prompt-only —
  `tests/services/test_game_detail.py::TestGetCachedGameDetailCarriesNoBiosAnswer` and the two contract cases in
  `tests/contract/test_game_detail_read.py`. `get_cached_game_detail` carries none and says so (`bios_status_unknown`,
  plus an unconditional `bios` stale field); the live `get_bios_status` fills it in. What holds the rule is an absence —
  `BiosChecker` has one method, so there is no cheap cached twin to reach for — and an absence is exactly what a future
  change restores without noticing. Re-adding a stored answer would look like a performance win and would put a previous
  page open's requirement on this page
- **Tender attaches a configured custom header to a RomM-origin request and to no other request it issues, and never
  over a header the adapter sets itself** — test + prompt-only — `tests/domain/test_custom_headers.py` pins the
  validation in both directions (every reserved name case-insensitively, a CRLF in a value, and what the persisted
  reading skips), and `tests/adapters/romm/test_http.py::TestCustomProxyHeaders` pins the three attachment points, the
  one exclusion, and that a hand-planted `Authorization` / `Host` still loses. **Nothing joins them.** The rule spans
  `domain/custom_headers.py`, the transport's `_apply_origin_headers` and its three callers — `_apply_default_headers`
  (every authenticated route), `unauthenticated_post_json` (the pairing-code exchange) and `basic_auth_request` (the
  token mint) — plus the one place that must NOT call it, `download_external`. Both directions fail in silence and each
  one is worse than it looks. A fourth request method that forgets the helper works perfectly for the user who has no
  proxy and 403s for the user who has one, on that path only. Adding it to `download_external` hands the user's proxy
  credential to a third-party metadata CDN, which no test would notice because the fetch still succeeds. **What Tender
  issues is the whole of the claim**: `_urlopen` uses the default opener, so urllib's redirect handler follows a 30x by
  copying every header but `content-length` / `content-type` onto the next request with no same-origin test — measured,
  not read: a cross-host 302 delivers both the configured header and the RomM bearer to the foreign host. That is the
  transport's behaviour and predates this rule (the bearer always travelled it), which is why the invariant is worded
  about attachment rather than about arrival; #1889 holds the gap. The reserved set is held by **two** mechanisms, not
  three, and they are not equally strong. One is validation — `_name_refusal` against `RESERVED_NAMES`, reached from
  both `resolve_custom_headers` (the wire) and `stored_custom_headers` (every request, so a hand-edited `settings.json`
  cannot route around it). Those are two call sites of ONE frozenset: drop a name from it and both gates open in a
  single edit. The other is attachment ORDER, and it covers only a name the adapter itself re-adds after
  `_apply_origin_headers` — `User-Agent`, `Authorization`, `Content-Type`, and conditionally `Accept-Encoding` / `Range`
  / `If-None-Match` / `If-Modified-Since`. It covers `Host` and `Content-Length` **not at all**, because the adapter
  sets neither: `http.client._send_request` suppresses its own derived `Host` when the caller supplied one, so a
  configured `Host` retargets every request's virtual host — and for that name, the one this list singles out as
  dangerous, the frozenset is the only defence there is. Nothing pins the ordering leg either: the two tests that look
  like they do (`..._never_displaces_the_bearer`, `..._never_retargets_the_request`) pass because
  `stored_custom_headers` drops the entry long before `add_header` is reached, so both stay green if the ordering is
  reversed. Detail: `docs/architecture/backend-architecture.md` → "the headers every RomM-origin request carries"
- **`dist/globals.js` is never evaluated into Steam where Decky Loader is serving, and what decides that is read from
  the MACHINE rather than from the window** — test + prompt-only — `tests/host/inject/test_bundles.py` pins both choices
  in both directions (the globals bundle absent beside a serving loader, and the standalone panel absent too, since it
  carries the same sweep), and `tests/host/inject/test_injector.py::TestBesideDeckyLoader` pins it end to end over a
  real socket with something answering on the loader's port. It is the only crash cause ever observed here: that bundle
  carries `@decky/ui`'s module sweep at import scope, and re-running it under an interface already rendering from those
  modules takes the Big Picture window down. **The rule spans three modules and nothing joins them.**
  `host/inject/machine.py` asks whether Decky Loader's server answers, `host/inject/bundles.py` turns that into a file
  list, and `host/inject/injector.py` evaluates it. A fourth caller building its own file list, or one reading the
  window instead, goes green: at the moment the question has to be answered every Decky marker on the window — `DFL`,
  `DeckyPluginLoader`, `DeckyBackend`, `deckyAuthToken`, `deckyHasLoaded` — is still `undefined`, so a window probe
  answers "no Decky" on a machine that has one, picks the globals, and crashes the interface. The gap between "the
  loader's server answers" and "Decky is rendering" is deliberately left on the safe side, and the ordering measurement
  (Tender loading first is safe) may not be leant on to close it the other way: a reconnect puts the same question at a
  moment when Steam has been up for an hour. Detail: `docs/architecture/loading-the-panel.md`
- **Where the globals bundle is loaded, its installer is CALLED between the import that defines it and the panel import,
  and the panel is not imported unless the report says every global it names is installed** — test + prompt-only —
  `tests/host/inject/test_bootstrap.py::TestItRunsUnderNode` runs the bootstrap under node against a stub page, with the
  bundles as `data:` modules that record having run and that leave the installer on the window where the real one does,
  and pins the order, the refusal and each refusal's sentence; `test_bundles.py` pins that the globals bundle comes
  first; `TestTheInstallersName` holds the property's spelling in Python to `frontend/src/boot/steamGlobals.ts`. The
  rule spans three modules in two languages: `host/inject/bundles.py` says WHICH file installs (`globals_at`),
  `host/inject/bootstrap.py` turns that into import, call, import and into the gate, and `steamGlobals.ts` leaves the
  installer under the shared name and decides the report's keys. **What nothing checks is the seam the node tier
  stubs**: the stub report is written from the TypeScript interface rather than derived from it, so a `GlobalsReport`
  that renamed the `installed` field passes every test named here and fails on a device the same silent way — the
  bootstrap reads no keys, refuses, and leaves no panel, a card, and a green suite. Renaming the keys INSIDE `installed`
  costs nothing, because the gate is generic over whatever the report names; renaming `steamReady` costs only the
  readiness sentence, and nothing on either side holds one spelling to the other
- **An injection that could not be observed is never counted as a crash, and this process answers for its own record
  before it reads one** — test + prompt-only — `tests/host/inject/test_watchdog.py` pins the state machine in every
  direction and which of the three answers settles an armed record;
  `tests/host/inject/test_injector.py::TestDidTheInterfaceSurviveIt` pins every way a record is answered for over the
  real loop — the survival, the collapse that leaves it open, and each way an attempt is closed without being counted.
  Count those by their call: a test in that class that ends with the record `open: false` and `failures: 0` is one of
  them. One more way lives in its own class, `::TestWhatThisBackendDoesToSteamIsNeverACrash`, because it is reached only
  through replacing a panel an earlier backend left behind. That test drives the reload; the fallback's SIGTERM to
  `steamwebhelper` bumps the same counter and has no test of its own, because every path to it passes the reload's bump
  first. The crash cannot be counted inside a session — it leaves `SharedJSContext` alive with our marker on it, so the
  injector sees "already injected" and never tries again — so what is counted is a record left open at the NEXT attempt,
  and two in a row stop the injection. **Three things have to hold together and nothing checks that they do.** `judge`
  writes its resolution back before it answers, or one open record counts once per attempt for ever. **The ORDER is the
  second**: an injection answers for the record this process already holds before `judge` reads one, because a
  JS-context rebuild inside the alive window starts the next injection while the last record is still open and
  unanswered — ordinary during start-up settle, and what a Steam restart produces — and read the other way round it is a
  crash that never happened, twice over on a machine where nothing was wrong. A real crash is unaffected: the check that
  saw it marks its reading taken (`stays_open`) and the record stays open for the next `judge` to find. The third is
  that the alive check closes the record without counting whenever nothing was established — the debugger stopped
  answering, the backend is shutting down, nothing but the renderer was open when the panel was loaded, a second
  injection began before the check that answers for the first could run, or this process took the interface down itself
  after the panel was loaded (the reload that replaces a stranded panel, or its fallback's SIGTERM to `steamwebhelper`,
  each counted in `PanelInjector` before it happens and compared by the check against the count at arming) — because the
  signature is specific (every other page target goes at once while the debugger keeps answering), and a run in which
  that could not be observed says nothing. A close that counted any of those would stop the panel loading over a user
  closing Steam, and the failure is silent in both directions: too lenient and a crash loop is never stopped, too strict
  and the panel disappears with only a log line to say why. The way back is not inside Steam (the interface is what is
  gone): the fingerprint — Tender's version, the bundle bytes, Steam's client build — drops the count on its own, and
  `TENDER_INJECT=force` is the switch the refusal line names
- **This machine takes Steam's interface down to replace a stranded panel at most twice in ten minutes, across backend
  starts; a reload Steam does not refuse and the fallback's SIGTERM both count** — test + prompt-only —
  `tests/host/inject/test_reload_limit.py` pins the record (under the limit, at it, the window passing, a clock set
  back, a record it cannot read or write, an entry no float can hold), and
  `tests/host/inject/test_injector.py::TestAcrossBackendStarts` pins it over the real loop from a record written before
  the backend starts: a reload under the limit, nothing at it and nothing recorded for the refusal, the fallback refused
  because the reload before it counted, a reload Steam refused left uncounted and one that got no answer counted, and
  takedowns the window has passed forgotten. Why the record outlives the process, why two, and why one it cannot read or
  write blocks nothing:
  [a panel an earlier backend left behind](loading-the-panel.md#a-panel-an-earlier-backend-left-behind). **The join is
  prompt-only**: `recovery.py` goes through `_may_take_the_interface_down` in front of both takedowns it performs and
  calls `ReloadLimit.record` once each is under way — the SIGTERM before it is sent, the reload once Steam has not
  refused it — and nothing checks that a third takedown does the same; it would pass every test above and reload in a
  loop again
- **Tender's Quick Access entry composes with Decky's rather than going through it, and everything it binds to the Quick
  Access window is bound from inside that window's React tree** — test + prompt-only —
  `frontend/src/qam/quickAccessEntry.test.ts` pins what a render pass does to a tab array (added once, added again to
  the replacement array a remount builds, moved to the end whenever a pass finds it higher up, and recognised by its
  marker alone in an array this module has never seen), each case mutation-checked. **Everything above that line is
  device-only**: whether the two renderers are found, patched and hand back a tree the resolver can walk was measured in
  #1897 and nothing here re-measures it — a suite that faked it would assert against a tree it wrote itself. The rule
  spans the frontend entry and the backend injector, and nothing joins them. **Three halves fail green.** (1) The entry
  carries `tender` as its marker and its key and never `decky`, and nothing writes `window.__TABS_HOOK_INSTANCE` or
  calls `__TABS_HOOK_INSTANCE.add()`: Decky's own render counts its `decky`-marked entries against its list length, so a
  foreign entry there desynchronises that guard into re-pushing every tab with no convergence, and its constructor calls
  `deinit()` on whatever it finds in that global. **Both are read off Decky's source rather than measured** — the
  `add()` route was deliberately never taken, so its runaway was never observed, and observing the `deinit()` would mean
  breaking Decky's boot on purpose. Neither is a presence check and neither may become one; which bundle is loaded was
  already decided from the machine (`backend/host/inject/machine.py`). (2) **No tab array is held anywhere**, which is
  available only because there is no unpatch: the injector refuses to load the panel into a context already carrying
  `window.__tender_panel__` (`backend/host/inject/bootstrap.py`), and what clears that marker is a JS-context rebuild,
  which takes the module, its patches and every array with it. Re-adding an unpatch is therefore also re-adding a reason
  to retain arrays, and the spike's shape — a `Set` of every array ever pushed into — leaks one dead array per Quick
  Access remount, with the strip's entries and their React elements, for the life of the JS context. (3) The placement
  is re-asserted on EVERY pass rather than set at creation, because `afterPatch` runs the previous handler first:
  whoever patches last lands lowest, measured both ways, and install order is a property of which program starts first.
  A handler that pushed once and trusted the order goes green here and comes out above Decky on exactly the machines
  where TENDER started first — it pushes first and Decky pushes under it. Where Decky started first the single push
  already lands lowest, which is the case re-assertion does not have to fix and the one a developer is most likely to
  test. The third rule's own half — **nothing binds to the Quick Access window at module scope** — is unmechanized and
  unpinned: that window is replaced by every remount, so a listener, observer or stylesheet held across one is bound to
  a document nothing renders. What holds today was measured rather than assumed — an unfiltered grep over `frontend/src`
  (tests aside) for `addEventListener(`, `ResizeObserver`, `MutationObserver`, `ownerDocument`, `defaultView` and
  `createElement(`, with the enclosing function of every hit read. **Neither observer term is prefixed with `new`**, and
  that is what makes it find anything: this repo's realm rule takes the constructor off the node's own view, so every
  observer here is spelled `new view.ResizeObserver` or `new panelView.MutationObserver`, and a pattern anchored on
  `new ResizeObserver` matches nothing in `frontend/src` at all. The sweep's own boundary is worth stating, because a
  reader re-deriving it meets the other kind first: a `globalThis` listener binds SharedJSContext's window, which the
  menu's remount does not touch, so those are out of scope however many of them there are. Every binding into the MENU's
  window sits inside an effect or an event handler of a component the menu mounts: `utils/qamExpansion.ts`'s stylesheet
  and `MutationObserver`, `utils/entryFocus.ts`'s focus listeners, `bigpicture/layout/WidePage.tsx`'s `ResizeObserver`,
  and `bigpicture/layout/ScrollRegion.tsx`, which reads the view per event and retains nothing. The glyph binds nothing
  there: its update dot reads stores that are module state of the window the panel's code runs in, and subscribes to
  them from the tree through `useSyncExternalStore`. (`utils/styleInjector.ts` writes into `findSP()`'s document, which
  is the game page's and not the menu's.) One added at module scope would work perfectly until the first
  Gaming-Mode-to-Desktop switch and then do nothing, silently. Detail: `docs/architecture/qam-panel.md` → The entry
- **Tender's section reaches Steam's game page through the ROUTE component's `renderFunc`, and never through the page
  component's own `type`** — test + prompt-only — `frontend/src/bigpicture/patches/gamePageSeam.test.ts` pins the half
  that is decidable without Steam: the factory predicate in both directions, that two matching factories answer as no
  match, the shape predicate that decides whose sources are read at all, the memo selection (including an export whose
  getter throws), and the per-render decision — wrapped once per PROPS object, never per component and never per
  `renderFunc`. Each case mutation-checked. **Everything the install does with those answers is device-only**: obtaining
  Steam's webpack `require`, reading the factory sources, patching the memo and adopting a mounted page are
  `installGamePagePatch.ts`, which is coverage-exempt because a suite that faked any of it would assert against a
  registry and a fiber tree it wrote itself. The rule spans that file, the seam module, the patch it installs
  (`gameDetailPatch.tsx`) and the start-up check, and nothing joins them. **Why the page component is the wrong seam is
  the half a reader will re-derive wrongly**: `@decky/ui`'s tree patcher caches the wrapped component per ORIGINAL type
  (`dist/utils/react/treepatcher.js`, `handleStep`), so once any plugin has wrapped the page component every later
  render goes through that cached copy and a patch installed on the original is never entered again. That is the
  ordinary case rather than a corner: this backend starts after Steam has been running, so Decky's plugins have already
  wrapped the page. The failure is silent and machine-dependent — green suite, green gate, and the section simply never
  appears on a machine that runs Decky Loader while appearing on one that does not, which is the difference this program
  exists not to depend on. Two further halves nothing checks: the install patches EVERY memo export of that module whose
  `type` is a function rather than picking the route out (nothing on an export says which one it is, and on any other
  export the handler finds no `renderFunc` to wrap), and the start-up check's `AppDetailsRoute` entry costs a `feature`
  beside `appDetailsClasses`, which costs one for the same reason — every read of it is in that same patch. Moving
  either to `panel` takes the whole interface off the air for a section outside it; moving a panel name to `feature`
  beside them renders a hole. Detail: `docs/architecture/frontend-bundles.md` → Tender's section on Steam's game page
- **Aggregate state mutated only via verb-named methods (no field assignment)** — check —
  `scripts/check_aggregate_field_assignment.py`
- **No UoW-opening seam (ActiveCoreResolver, RelaunchOptionsResolver, uow_factory) is called while a UoW is open on the
  same path** — check — `scripts/check_uow_seam_nesting.py` (the first of the **two** rules that script carries, over
  one shared matcher; the file-I/O rule below is a different hazard with its own seam list and its own failure message,
  and neither entry is evidence about the other)
- **No file-I/O seam is called while a UoW is open — a Unit of Work wraps database reads and writes, never file or
  server I/O (GLOSSARY.md → Unit of Work, ADR-0006)** — check — `scripts/check_uow_seam_nesting.py`, second seam family
  (`IO_SEAM_METHODS`). The list is **the seams this checker can see and has been told about, never an inventory of the
  I/O seams that exist**: `DiscResolver.enumerate_discs` / `.resolve_for_install` (a recursive walk of the ROM's install
  directory), the three `CoreInfoProvider` reads — `get_active_core`, `get_default_emulator`, `get_emulator_options` —
  which are answered by the vendored resolver's live read of ES-DE's catalogue (every call from the panel opens it
  again; only a run's one reading of the sources keeps a system's answer for the run; `get_emulator_options`
  additionally globs each **bakeable standalone** option's emulator install through the find rules on **every** call,
  uncached so a component installed mid-session is seen), `SandboxLauncherFn` (re-probes the flatpak roots for
  `es_find_rules.xml` and re-stats it before it may use the parse cache), `SystemResolver` (parses Tender's **own**
  bundled `config.json`, not RetroDECK's `retrodeck.json`, and does no network work despite living on the RomM HTTP
  adapter), `SystemSupportedExtensionsFn` / `SystemKnownFn` (two more questions to the same catalogue, asked afresh from
  the panel and once per reading within a run), `SteamConfigStore.read_shortcut_exes` (parses Steam's whole
  `shortcuts.vdf` — 315 KB and 828 entries on the reference machine — for the one-time shortcut relocation. **Listing it
  changes nothing at its only call site**: the service reaches it through `run_in_executor` as a bound method, which is
  this checker's documented blind spot, so the entry is a statement of the rule rather than an enforcement of it. It is
  also not the store's only real I/O — `grid_dir()` is called from `services/artwork.py` (six sites),
  `services/shortcut_removal.py` and `services/library/reporter.py`, and `check_retroarch_input_driver()` from
  `services/settings.py` — those are unlisted, and their being unlisted is a gap, not a judgement),
  `FirmwarePlatformResolver` (reads what one system's emulators want WITH content verification: it opens each candidate
  in a declared folder and reads it the way the emulator does — 64-318 ms per system on the reference machine) and its
  whole-machine sibling `FirmwareResolver`, the save answer — `resolve_save_answer` and the saves package's own
  `save_answer` wrapper, a full read of the machine per ROM (the first ask about a core also runs that core's probe) —
  the savestate question put to the same catalogue entry (`resolve_savestate_location`) and the seam's detection
  question (`installation_detected`, which detects the emulator sources afresh), the two path resolvers —
  `MigrationFileStore.realpath` (one walk per stored RetroDECK-home marker, a directory that may sit on the SD card the
  marker is pending a migration away from) and `ResolvedPathFn` (the same walk, but on **both** sides of a comparison,
  so a call site costs what the rows it checks cost, not what it checks them against) — and the `RetroDeckPaths` getters
  that answer with a root: `bios_path`, `roms_path`, `saves_path` and `retrodeck_home`, four of the Protocol's five path
  getters, each resolving on every call. The fifth, `config_path`, stays out because it resolves nothing — it is
  `os.path.join` over the user home, so calling it costs no I/O. That timing is the only entry a cost was measured for;
  every other one is listed from reading its implementation. One other real I/O seam was weighed and kept out — the
  reason is in the script's docstring, and it is not an exemption; nor is it an inventory of what else touches the disk.
  **"It's only a read" is the reasoning this rule exists to refuse**: `SqliteUnitOfWork.__enter__` issues
  `BEGIN IMMEDIATE`, so even a read-only UoW takes the write lock. The database is in WAL, so readers are unaffected —
  but every other **writer** waits on the lock for up to `busy_timeout=5000` and fails with `SQLITE_BUSY` if it is still
  held then, and `FakeUnitOfWork` shares no connection, so no unit test notices. Six call sites had drifted across the
  rule before anything looked (#1779), for the reason the check exists: nothing at a call site reveals that an injected
  seam touches the disk. **The rule and the gate come from reading code — no measurement of how long any of those
  transactions actually held the lock exists, and nothing here should be read as one.** What the check sees is the
  deadlock rule's matcher unchanged — an **attribute** call naming a listed seam, lexically inside a
  `with <...>uow_factory()` block in the same function scope — so it inherits every blind spot of that half: a seam
  behind a helper one level down, an alias to a local, a factory attribute whose name does not end in `uow_factory`, a
  nested `def`/`lambda` (which resets the scope by design), a seam **passed as a bound method**
  (`run_in_executor(None, self._disc_resolver.enumerate_discs, install)` — an attribute, not a call, and
  `run_in_executor` is exactly how `disc.py` and `cores.py` reach their `_io` bodies; the same shape
  `check_read_only_module.py` records for its own gate), and the hand-maintained list itself, which cannot notice a seam
  whose implementation _grows_ a file read later. Matching only attribute calls is deliberate: the pure
  `domain.disc_selection.enumerate_discs` shares a name with the seam and does no I/O — it is safe because its call site
  imports it bare, not because of the name. The call-shaped blind spot is shared with the deadlock rule and only this
  family closes it: for each `__call__`-only seam the list carries the attribute it is bound to — by convention rather
  than by construction, and only while such a name means one thing, which is exactly what keeps `_list_files` out. Which
  entries are call-shaped, and which of those also carry a twin under their implementation's own method name, is in the
  script's module docstring. The deadlock rule's own call-shaped seams stay open. `SystemResolver` is the odd one out
  for a second reason: the adapter memoises its map for the life of the process, so exactly one call ever opens the
  file, and the entry earns its place because that one call can land inside a UoW. One `# pragma: no uow-check` covers
  both families — it suppresses the line, and no seam is in both lists, so where a line does name two seams it silences
  both
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
- **Services never call clocks / sleep / uuid / random directly (inject the Protocol)** — check —
  `scripts/check_cosmic_call_bans.sh`
- **No module in `services/`, `bootstrap/`, `adapters/`, `domain/`, `lib/` or `models/` crosses the ~1000-LOC
  decomposition threshold, and the ones already over it may not grow** — check — `scripts/check_module_size.py` (the
  modules that predate the gate are grandfathered at their exact size. A ceiling goes up only for a change that adds no
  code — a rename, a reformat — and only with the reason recorded at its `ALLOWLIST` entry; a raise taken silently has
  retired the gate. Entries only ever come out, when the module drops back under the threshold. `main.py`, `_vendor/`,
  `tests/`, `scripts/` and `frontend/src/` are out of scope, each for a reason recorded at `SCOPE_DIRS`)
- **Service-independence contract list stays complete** — check — `scripts/check_service_independence_contract.py`
- **Layer import direction (services ↛ adapters, adapters ↛ services, …)** — check — `.importlinter` (`lint-imports`)
- **Frontend direction: `frontend/src/utils/` and `frontend/src/api/` never import either surface
  (`frontend/src/bigpicture/`, `frontend/src/desktop/`) or `frontend/src/shared/`; `shared/` never imports either
  surface; the two surfaces never import each other; and no `frontend/src/` module takes part in an import cycle** —
  check — `frontend/eslint.config.js` (`import-x/no-restricted-paths`, `import-x/no-cycle`). The surface pair is a peer
  rule, not a layer rule: the two share data and logic and almost nothing visual, so anything that turns out to belong
  to both moves DOWN, never sideways — UI into `shared/`, which both surfaces may import and which imports neither, and
  the rest into `api/`, `utils/` or `types/`. These rules go inert rather than loud when misconfigured: until the config
  names `.ts`/`.tsx` for the plugin to read, `no-cycle` finds no cycle among the frontend's modules (the comment at
  `import-x/extensions` in `frontend/eslint.config.js` says how). `frontend/src/eslintBoundaries.test.ts` lints
  known-bad fixtures through the real config and fails if any of the eleven stops reporting — a green `pnpm lint` alone
  proves nothing. Type-only imports are not edges (erased at runtime), which is why the `api/backend.ts` ⇄
  `utils/cachedGameDetailStore.ts` back-reference is not a cycle
- **No bare `# type: ignore` / blanket suppressions** — check — `scripts/check_no_bare_ignores.sh`
- **A transport failure and an endpoint's own failure never arrive in the same shape, on either end** — test +
  prompt-only — `backend/host/protocol.py` states the vocabulary and `.claude/rules/host.md` holds the backend half; the
  frontend half is `frontend/src/api/hostSocket.ts`, which THROWS `HostTransportError` for an `error` message and
  resolves only a `reply`, so a transport reason cannot reach a reader of `{success, reason, message}`.
  `hostSocket.test.ts` pins both directions. **Nothing joins the two ends**: `connection_lost` is the one reason no
  backend ever sends — the caller's own register answers with it — and it is spelled once in Python and once in
  TypeScript with no check that the two agree. A frontend that spelled it differently would go green, and the divergence
  would surface only to whoever eventually matched on it
- **The standalone panel bundle carries `@decky/ui` and the coexistence one carries none of it** — check —
  `frontend/scripts/check-bundle-shape.mjs`, over the built artifact rather than a bundler setting (nine strings that
  exist only in the package's implementation, plus the `DFL.` read count, in both directions; the licence file the
  standalone build owes and each bundle's own build stamp are asserted there too). Both failures are silent in CI and
  land on a device: a standalone bundle that lost the package throws on its first `DFL.` read where no `DFL` exists, and
  a coexistence bundle that gained it re-executes the modules a rendering Decky is rendering FROM, and takes the Big
  Picture window down. **The check sees the artefacts and not the decision**: which of the two the injector loads is
  `backend/host/inject/bundles.py`'s, and nothing here would notice the wrong one being served
- **Every third-party package a bundle carries has a budget of its own in `frontend/package-budgets.json`, and a package
  without one, a package over its budget, or a budget for a package the bundle no longer carries fails the package
  check** — check — `frontend/scripts/check-package-budgets.mjs` (`pnpm -C frontend check:packages`), over the record
  the build writes to `frontend/bundle-packages.json`, refused when it is older than `dist/`. Without it, a large
  library slipping into a bundle reads as one more raise of a total cap; per package it is a failure naming the package.
  A package with no budget fails rather than passing unmeasured, and a budget whose package has left fails too, so the
  file stays the list of what the bundles carry. **The panel's own code is not budgeted**, and only
  `frontend/.size-limit.json`'s total watches it. How the record is made, what counts as own code and how a package is
  given a budget: [frontend-bundles.md](frontend-bundles.md#third-party-package-budgets)
- **Tender's three React globals are spelled exactly the way Decky Loader spells them** — test —
  `frontend/src/boot/steamGlobals.test.ts`, which reads `steamGlobals.ts` and the pinned `decky-globals-block.txt` as
  TEXT and compares the four search predicates, which global each answer is assigned to, and the JSX stand-in's keys and
  aliasing. The cost of a difference lands on DECKY's users, not ours: its loader skips its entire globals block when
  `SP_REACT` is already set, so when ours runs first, Decky's whole frontend renders through our shape. **The pinned
  copy is the half nothing can check** — it is upstream's file, held still by hand, so a refresh that is wrong reads as
  agreement; the provenance header names the commit it was taken at so the question can be re-asked rather than trusted
- **Every value the panel imports from `@decky/ui` is classified by the start-up check** — test —
  `frontend/src/boot/steamModules.test.ts`, which sweeps every non-test module under `frontend/src/` and fails on a name
  that is in none of the four lists (a search it asks, a name it cannot answer for, a name answered by Steam's runtime
  state, the package's own code). The swept set is derived rather than listed, because a file missing from such a list
  carries no lock at all. **What it cannot see is whether a classification is TRUE**: two names sit in the unverifiable
  list because they are wrappers the package always defines, and moving a real search there to quieten the check would
  pass green and leave the panel rendering a hole where the check reported everything resolved. The one list it CAN
  judge is `ASKED_LIVE`, which the test derives rather than checks for membership — it walks `@decky/ui`'s shipped
  `dist/` for exported functions reaching `getGamepadNavigationTrees`, `getFocusNavController` or `document.title`
  (through a module-private helper within a file, which is what carries `useQuickAccessVisible` through
  `getQuickAccessWindow`) and holds that set, intersected with what the panel imports, EQUAL to the list's keys, with
  none of it in `STEAM_LOOKUPS`. That axis is the rule the whole check rests on: every entry reads the module registry
  or a bootstrap global — one registry, the same in both of Steam's modes — and not what Steam has mounted or focused,
  which the same question answers differently a second later. A start-up reading of `findSP` refused to mount the panel
  in the desktop client over a Big Picture tree that had not been built, and blamed Decky Loader for it. **What the
  derivation cannot see is a runtime-state reader reached through a name the sweep does not know** — an arrow export, a
  re-export, or another module's helper (`showModal` calls `findSP() || window` from `dist/components/Modal.js`) — and
  it sees `function` declarations only, not nested in another. What the sweep cannot see it says nothing about: such a
  name can sit in `STEAM_LOOKUPS` unflagged, which is the shape this cut removed by hand. What the narrowness cannot do
  is put a registry search onto the live list in silence — a name the sweep did not derive fails the equality there. The
  walk is `frontend/src/test-utils/jsFunctionScanner.ts`, a string-, comment- and regex-aware scan; why a regex could
  not do it is on the docs page
- **Whether every search answered and whether the panel may MOUNT are two questions, and a miss that costs less than the
  panel never takes the interface off the air** — check + test + prompt-only — the type carries the first half:
  `SteamLookup.absenceCost` is required, so a new entry does not compile until it states which of the four its absence
  costs — the `panel`; a whole `feature` outside it (`ToastRenderer`, `NotificationStore` and `ErrorBoundary`, without
  any one of which no toast appears at all and every page, sync and download is untouched, plus `AppDetailsRoute` and
  `appDetailsClasses`, without either of which Steam's game page carries no Tender section); only its `appearance`
  (`ControllerGlyph`, whose only consumer `layout/WidePage.tsx` already draws `‹ Back` in its place, and `toastClasses`,
  whose every read is optional so the toast says what it says in an unstyled box); or only a `diagnostic`
  (`playSectionClasses`, read nowhere but `gameDetailPatch.tsx`'s one-shot `dumpTree`, which already prints `UNDEFINED`
  in its place) — and there is no default to arrive in. `frontend/src/index.test.tsx` pins both factory branches — the
  panel mounts with everything registered, and the miss reaches the log. **Blocking is the status quo and staying there
  costs no evidence: nothing here is a claim that every other name was judged**, only that moving one OUT needs its
  every consumer read, one name at a time. **The join is prompt-only and spans three places**: `checkSteamModules`
  derives `panelMayMount` from the costs, `index.tsx` gates the fallback page on it and logs `describeSurvivedMiss` on
  the other side, and that sentence answers whose COPY of `@decky/ui` ran the missed searches rather than naming a
  repair of its own — it used to say "a newer Tender" unconditionally, which held only while nothing reaching it was a
  name the package exports, and `playSectionClasses` is one. What `frontend/src/boot/steamModules.test.ts` locks is the
  property the line's remaining own answer rests on — a non-blocking name `@decky/ui` does NOT export must be one Tender
  resolves for itself, swept from the source in the three shapes one is written in (a `find(?:Module|ClassModule)\w*`
  call, a direct cast of `window` whose exported name equals the property read, and a `searchSteamFactories` scan over
  Steam's module factories) — so the three `SP_*` globals, which the frontend cannot attribute to a program from inside
  the page, fail there the moment one is made non-blocking, instead of shipping a repair aimed at whichever program did
  not install them. **`!== "panel"` is the only reading of `absenceCost` there is**, so `feature`, `appearance` and
  `diagnostic` record why a name is off blocking and decide nothing. The two things that DO answer for the toasts are
  prompt-only and read NAMES: `notificationsMissing` over `NOTIFICATION_LOOKUPS` puts the notice on Main, and
  `describeSurvivedMiss` puts the same fact in the log as a sentence stating the loss and naming NO repair of its own —
  the verdict sentence beside it names one that is right under every answer, which it has to be, since `ErrorBoundary`
  is a `@decky/ui` export and a miss of it alone in the coexistence bundle is `decky`. So a `feature` entry added for
  something else cannot make either claim the notifications are what went missing, and a second spelling of any of the
  three cannot leave them answering for a lookup nobody asked about. Both directions fail quietly: call a real
  dependency cosmetic and the panel mounts and renders a hole, which is the fault the whole check exists to tell apart
  from a backend that is not running; call a decoration blocking and one missing glyph costs the user their entire
  interface, which is what this entry removed
- **The start-up failure page names the copy of `@decky/ui` that actually ran the search that missed, and the repair
  that follows from it** — check + test + prompt-only — the artefact's stamp is checked
  (`frontend/scripts/check-bundle-shape.mjs`, per bundle and on `globals.js`, which must carry none), and the sentence
  behind every verdict in `SEARCH_OWNERS` is pinned in `frontend/src/boot/steamModules.test.ts` and
  `StartupFailurePanel.test.tsx` with both bundle values exercised — the test iterates that list rather than a count, so
  a verdict added without a sentence on each surface fails instead of going unworded. **The join is prompt-only and
  spans four places**: `rollup.config.js` serves the stamp, `boot/searchingCopy.ts` reads it and Decky's namespace,
  `boot/steamModules.ts` words it, and `index.tsx` resolves it ONCE for the log line and the page — two resolutions
  could disagree with each other. The predicates belong to `@decky/ui` and the coexistence bundle runs DECKY's copy, so
  a page that blamed Tender in both would send a user after the wrong program while Decky's own interface and its other
  plugins broke beside it. **A miss confined to names `@decky/ui` does not export names NO copy and offers NO repair** —
  `SP_REACTDOM` is the only one that reaches that state alone, `ControllerGlyph` only ever beside a global (on its own
  it is cosmetic and brings no page up at all, per the entry above), and `describeFailure` answers it before it asks
  whose copy ran anything. Naming a copy would blame Decky for a predicate of ours; the silence about a repair is right
  for the three globals and a real loss for the glyph, and only the second half of that is easy to forget. For the
  globals no repair follows: who installed them on a machine running both now HAS an answer — the injector loads
  `globals.js` only where Decky Loader is not serving, so beside a serving Decky they are Decky's — and **this branch
  does not read it**, because it keys on whose COPY ran the search rather than on which program installed a global. In
  the standalone bundle the answer would not settle it anyway: a missing `SP_REACTDOM` there is `globals.js` not having
  run OR our own ReactDOM predicate in `boot/steamGlobals.ts` having gone stale — two repairs behind one symptom.
  `ControllerGlyph` is reached by a `findModule` predicate of ours in BOTH bundles, so a newer Tender IS its repair and
  this branch cannot say so; restoring it here would take a third axis (whose PREDICATE, not whose copy), never a
  reworded answer. What bounds that cost is only that the glyph's absence costs appearance, so it never brings the page
  up alone and `describeSurvivedMiss` prints its sentence into the log whenever it is the whole of the miss. **It is NOT
  bounded to the company of a global**: beside a blocking `@decky/ui` name the verdict is `mixed` and the glyph is that
  answer's unnamed rest, asking for a report rather than naming an update, with no global anywhere in the miss —
  `steamModules.test.ts`'s "leaves the glyph in the unnamed rest with no global anywhere in the miss" is that case. Four
  quiet ways back: a runtime probe instead of the stamp (`typeof DFL !== "undefined"` is true of a standalone bundle
  loaded beside a running Decky), asking `in DFL` about a name `@decky/ui` never exported (`SP_*`, `ControllerGlyph` — a
  package disagreement reported on every miss, which is what `SteamLookup.deckyUiExport` and its sweep-derived lock
  exist to prevent), reading an unreadable `DFL` as an absence rather than as nothing established, and letting the
  reading THROW at all — `definePanel`'s factory reads it before it returns anything, so an unguarded `window.DFL` or
  `name in DFL` costs the page AND the log line and leaves the blank panel the check exists to tell apart from a dead
  backend. The version beside the name is an enrichment only — `_versionInfo.current` is internal, guarded, and every
  sentence is complete without it; `remote` beside it is the PUBLISHED version and is never consulted
- **A coverage exclusion names a property of the code, never a place: every frontend-scoped entry stands in BOTH
  `frontend/vitest.config.ts`'s `coverage.exclude` and `sonar-project.properties`' `sonar.coverage.exclusions`, every
  file entry carries its reason as a `// coverage-exempt:` marker in the file's own first lines, and every marked file
  is listed** — check — `scripts/check_coverage_exclusions.py`. **The two lists spell a shared entry differently and
  that is not drift**: Sonar runs from the repository root and Vitest from `frontend/`, so `src/types/**` there is
  `frontend/src/types/**` here, and the gate normalises before comparing. An entry spelled repo-relative on the Vitest
  side excludes nothing at all — Vitest would resolve it to `frontend/frontend/...` — so it is reported by name rather
  than normalised into agreement with Sonar's identical-looking copy. (A folder entry is admitted only from the script's
  `FOLDER_ENTRIES`, where membership in the folder IS the property; the backend/config entries are Sonar-only and the
  frontend test glob Vitest-only, each declared there with its reason so the asymmetry is stated rather than tolerated).
  Two accidents it removes, both silent: `src/patches/**` excluded a FOLDER, so a file's coverage obligation changed
  when it was moved out of the folder and nothing said so; and `steamShortcuts.ts` sat on Sonar's list and not on
  Vitest's under a comment claiming the two were aligned. **What the check cannot see is MEMBERSHIP in a folder entry's
  directory** — the two admitted folders are checked to exist and their contents are never read, so a real module filed
  into `frontend/src/types/` or `frontend/src/test-utils/` is exempted by its PLACE, with no marker asked for and
  nothing failing: the very accident above, still live for those two directories. It is declared rather than mechanized
  on purpose — "is this really only a type declaration" is not a cheap check, and a half-check would exempt on a
  property nobody stated while reading as enforcement. **Nor can it see the marker's SENTENCE** — a false reason passes
  green, which is what the three stated reasons this cut found were: "no logic to assert" over a file with four passing
  tests, "no isolated logic to assert" over 88.65% line coverage, and "thin plugin-entry shim" over 89.09%. Only the
  first was replaced by a truer marker; the other two files lost their exclusions outright, which is also how their list
  drift was settled
- **Every pinned version in a lock satisfies its `.txt` source constraint (`requirements-dev.*` at the root,
  `docs/requirements.*` beside the docs)** — check — `scripts/check_lock_sync.py`
- **Every local markdown link in tracked docs resolves (file target + heading/attr-list anchor)** — check —
  `scripts/check_markdown_links.py`
- **Every RomM minimum stated for a reader matches the enforced `MIN_ROMM_VERSION`** — check —
  `scripts/check_romm_min_version.py`. The constant in `backend/domain/identity.py` is the floor `test_connection()`
  refuses a server below; every other place the number appears is a restatement for a reader, and a restatement drifts.
  The check holds exactly the statements its `CLAIMS` list names — each one a narrow regex that captures the version and
  nothing around it, so `--fix` can rewrite it in place — and fails both when a named statement says another number and
  when it no longer matches at all, because a regex that silently stopped matching would read as a claim that holds.
  **What it cannot see is a restatement nobody added to that list**: a new page stating the floor is unchecked until its
  sentence is listed there. The worked examples of the floor split three ways. The ones above the floor (`5.3.1-beta`,
  `5.4.0-alpha.1`) are unchecked, because no equality test fits them, and a floor raise makes them false, so they are
  rewritten by hand. The ones at the floor (`5.3.0-beta.1`, `5.3.0-alpha.1`) are listed and checked where a page calls
  them the floor's own tags, as both save-sync pages do. The conditional one in the ConnectionService notes of
  [backend-architecture.md](backend-architecture.md) is not listed, because it stays true whatever the floor is. ADRs
  are out of scope: they record the floor as it stood when the decision was taken, so the numbers in them are history
  and must not be rewritten
- **Every tree under `backend/_vendor/` is pinned by the `<pkg>.SHA256SUMS` beside it: every manifest entry under
  `<pkg>/` matches the vendored file's digest, the vendored file set EQUALS the manifest's set restricted to that
  prefix, and a package directory with NO manifest is a failure** — check — `scripts/check_vendored_trees.py` (the
  manifest is discovered, never named in the script, so the next vendored package is guarded by default rather than when
  someone remembers — the hole this closed was a second tree, `vdf`, sitting unpinned beside a pinned `atlas` while the
  gate reported OK. The set-equality half is the part `sha256sum -c` cannot do at all: the plain form fails on a correct
  copy, because a wheel's manifest lists release artifacts and dist-info files we never vendor, and `--ignore-missing` —
  the flag that makes it green again — exits 0 after a vendored file is deleted). **What the check cannot see is the
  manifest itself.** For `atlas` it is upstream's own release manifest, so the digests additionally prove identity with
  the tagged release; for a patched copy like `vdf` it is our own digest of the tree we ship, so a manifest regenerated
  to bless a hand-edit passes green and only review catches it — which is why regenerating one is the last step of a
  deliberate re-copy and never the answer to a failing gate. The licence assertion is data-driven, not package-driven:
  the sibling `<pkg>.LICENSE` is checked exactly where the manifest carries a dist-info licence entry, and a sibling the
  manifest carries no entry for is reported as pinned by nothing — otherwise regenerating a wheel's manifest from its
  own tree would delete the licence check in the same step, silently, while the file stayed. Deliberately outside it:
  `backend/native/` is pinned by its own `sha256sum -c` over one `.so`, and `__pycache__` is ignored wherever it
  appears, on both sides of the comparison. **Two things are outside it by accident of shape, and neither is loud.** The
  gate sees **directories only** (`package_dirs` filters on `entry.is_dir()`), so a single-module dependency dropped in
  as `_vendor/six.py` is never asked for a manifest — and there is no shape here that could pin one: dropping a
  `six.SHA256SUMS` beside it makes the gate fail with "it pins no vendored tree", so the guarded-by-default half is a
  property of package DIRECTORIES alone. And `rglob` does not descend into a symlinked directory, so every file below
  one is invisible to the set comparison whatever the per-file symlink guard does; git records the link itself as mode
  120000, which is what makes it a review question rather than a silent one
- **The release tarball is what the installer expects — one top-level `romm-tender/`, the files an install starts from
  plus the version file, the installer an installed tree rolls back with and the licence texts a distributed copy
  carries, nothing the packager prunes, a sidecar `sha256sum -c` accepts** — check — `scripts/check_release_tarball.py`,
  in CI's build job over a tarball packed from that build and in the release job over the one uploaded. It reads names,
  modes and digests and starts nothing; the script's docstring states what that misses
- **A one-time step — an installer move such as the covers' move into the cache root, a backend backfill behind a
  `kv_config` marker, a rung of the database's `user_version` ladder or of the settings' version ladder — stays safe to
  run again and stays in every later release, so an update that skips releases still gets it; one leaves only
  deliberately, with that release's notes naming the oldest version it can be updated from directly** — test +
  prompt-only — `tests/scripts/test_install_sh.py::TestAnUpdateThatSkipsARelease` updates from one release straight to a
  later one over covers still under the data root and asserts they reach the cache root with the database and settings
  unchanged: it proves the covers move runs on every update, over data left in the older layout. It cannot see a step
  being removed from a later release, so that half is prompt-only, and nothing mechanical sees the backend's steps.
  **Why:** an update is a jump from whatever release a machine is on to the newest, not a walk through every release
  between them — the installer downloads one tarball, and a device that was off for a month skips every release of that
  month. A step that ran in 1.3 and was deleted in 1.4 is therefore never run on a machine that goes from 1.2 to 1.5,
  and what that step moved or filled is simply missing there, with nothing failing. **Safe to run again** is the other
  half: every update runs every step still present, so a step that assumes it has not run yet damages the machines where
  it has. The installer's steps are idempotent by construction (`move_covers` in `install.sh` never moves over a file
  the cache already holds); a backend backfill is guarded by its `kv_config` marker, a database rung by
  `PRAGMA user_version` (`adapters/sqlite_migrations.py`), a settings rung by the stored `version`
  (`domain/state_migrations.py`). A step retired deliberately takes its floor with it: that release's notes name the
  oldest version it can be updated from directly
- **Server-supplied path components pass `safe_join` (`lib/path_safety.py`)** — test + prompt-only — traversal tests per
  path builder; new call sites are prompt-only
- **A firmware row's presence comes from the resolver wherever the resolver declared it; Tender's own filesystem probe
  covers only three leftovers** — prompt-only — `services/firmware/demand.py::FirmwareDemand.is_downloaded` is the
  single crossing point and states the boundary: the probe answers for a library file with no placement in the
  platform's catalogue (no emulator the resolver read declares it), for a placement whose location Tender cannot honour,
  and for the already-there check before a download (the batch and the per-row fetch). Everything else reads the
  resolver's `present`, which follows symlinks Tender would have to re-implement — the PS2 folder is one directory
  reached through two spellings. `present is None` reads as absent, the safe direction, because the row then shows work
  outstanding rather than a readiness nobody established. **Nothing enforces the crossing point.** A fourth status
  builder calling `_firmware_file_store.exists(dest)` directly would go green, and its rows would silently answer from
  the weaker source — `os.path.exists` on a path Tender assembled, which can render a satisfied requirement as missing.
  `services/firmware/status.py` holds that store itself, for `_stamp_deletable`'s records-still-on-disk probe, so the
  wrong probe is one line away from every row builder that should be asking `FirmwareDemand`. Related and separate:
  presence is not the row's verdict (GLOSSARY.md → Row verdict), and a withheld verdict is not an absence — its cause is
  read off the row's caveat codes and, for a declared FILE, off its `checked` (GLOSSARY.md → Byte reading), never off
  the verdict itself. Three of that vocabulary's eight values sit behind one withheld verdict and are three different
  statements: a file the emulator READ and does not recognise was checked, so wording it "could not be checked" is
  untrue; `refused` is not withheld at all, arriving with the verdict already `false`. Nothing checks that a consumer
  keeps them apart — `checked` is a plain string on the row beside a `satisfied` that reads like its summary
- **A firmware row's verdict is `BiosFileEntry.satisfied`, and for a folder declaration it is what the folder HOLDS —
  never that the folder is there** — test + prompt-only —
  `tests/services/test_firmware.py::TestAFolderRequirementIsAnsweredByItsContents` pins all three answers end-to-end,
  `tests/domain/test_firmware_wants.py` pins the fold the service asks through, and
  `tests/adapters/test_atlas_firmware.py` pins each folder answer and which codes those rows carry. The rule spans three
  modules and no diff-scoped review sees it whole: the adapter carries `declared_kind` and the folder verdict — settled
  in the same verified per-platform reading the rest of the row comes from, so there is no second question to keep in
  step — `domain/bios_status.py::_row_verdict` decides the row's answer, and both frontend surfaces colour and word the
  row off it. **Nothing mechanical joins those three**, which is what a consumer reading `downloaded` for a folder row
  breaks — an `if row.downloaded` beside the verdict, a count that spends presence as readiness. RetroDECK links LRPS2's
  `pcsx2/bios` onto the BIOS root, so such a consumer reports "All required ready" over a PS2 install with no BIOS file
  at all; that was the state before #1807 declined the verdict, and this cut replaced the declining with a real answer,
  so the same field access brings it straight back. The same holds for the third value: a required row answered `None`
  takes the level to `unknown`, and folding it into `False` claims an absence nothing established. `declared_kind`
  carries a second rule with **no check at all**: a folder declaration is never offered as a download — the emulator
  lists that name, so there is no file to fetch into it. Three places refuse it today (`PlatformDetail.tsx`'s fetchable
  filter, `FirmwareDownloader._download_firmware_batch`, and `FirmwareDownloader.download_platform_firmware_file`, which
  answers one named file and so refuses with a reason where the batch simply passes the row over);
  `FirmwareDownloader.download_firmware(firmware_id)` still does not. It is the DECLARATION's kind, so it survives an
  absent folder, which is exactly the case a presence check would let through
- **The console's own firmware demand is a value of its own (`system_image`) and is never folded into a count, and the
  resolver's `system_firmware: null` reaches it as a claim about nothing; it answers only for a launching emulator that
  states no one-of group** — test + prompt-only — `tests/domain/test_bios_status.py::TestClassifySystemImage` pins all
  four answers and the precedence over them, `::TestTheVerdictOverTheSystemImage` pins what the level and the token do
  with each, and `tests/services/test_firmware.py::TestTheConsolesOwnFirmwareDemand` pins the PlayStation case end to
  end including that the overview and the game page stamp one answer;
  `tests/domain/test_bios_status.py::TestAOneOfGroupIsOneRequirement::test_where_the_emulator_states_a_group_the_system_image_stays_silent`
  pins that a group takes its place. The frontend halves are pinned per surface
  (`frontend/src/bigpicture/BiosTab.test.tsx`, `frontend/src/bigpicture/library/PlatformsTab.test.tsx`). **The rule
  spans eight modules and nothing joins them** — counted one per file the answer passes through, four backend and four
  frontend: the adapter (`adapters/atlas_firmware.py`) carries `CoreFirmware.system_firmware` and `requirements_met` per
  core, `domain/firmware_wants.py::CoreFirmwareVerdict` holds the four spellings apart from the absence,
  `domain/bios_status.py::classify_system_image` decides, `services/firmware/status.py` stamps it beside the counts,
  every frontend surface that words it does so through ONE module (`frontend/src/utils/biosSummary.ts`; which components
  those are is answered by reading them, not by a tally kept here), and a fourth reads it without wording it (below). A
  libretro `.info` can mark a file required or optional and nothing else — no way to say "one of these", none to say the
  console will not start without one — so an author who knows it will not has two lossy moves, and the deployed
  catalogue takes both: SwanStation marks all five of its PlayStation images **optional**, Beetle PSX marks three of its
  own **required**. Which is why no count can be relied on to carry this: it is ONE requirement over the whole list, and
  putting it in `required_count` reports every image the core declares as required —
  `0 of N files … requires are in
  place`, `N` being every image it declares. The page's own `RomM library files` ratio
  is the library's inventory for the platform, a different set again, and reading the two as one is how the wrong ratio
  gets written. Each fold fails its own way and all of them silently. Fold it into the counts and the page states a
  ratio over the wrong set. Read `system_firmware: null` as "this console needs nothing" — a truthiness test, a
  `!= "runs-without-firmware"` bucket, a default — and Tender claims an all-clear over a console nobody has looked at,
  which is the collapse the `unknown`/`not_needed` entry above is about, one axis over. **The demand comes from the
  table and the presence from our rows, and `requirements_met` is not consulted at all** — weigh the two against each
  other and you have made the misreading that field exists to prevent, because ignorance there is always `None` and a
  `False` is therefore a demonstrated statement rather than a disagreement. Its two causes (a DIFFERENT required file
  absent, or one present with the wrong bytes) each leave one of our own required rows unmet, so the counts already
  report them by name; the second needs a content check to arise, and the inventory is asked **unverified** (the entry
  below), so it cannot occur here. What the presence half actually resolves to — a row's `satisfied` is presence, `null`
  in two shapes, and both read as not held — is written once, at `classify_system_image`, because an outside reader took
  that field for the resolver's usability answer and drew a false finding from it. And on the frontend,
  `system_image: "unsettled"` joins `required_withheld` on the side `PlatformDetail`'s `nothingEstablished` excludes:
  its rows were answered, so the pane has a file list to point at rather than only a place to put files by hand. Since
  #1821 that flag decides WORDING alone — the download affordances are built off the fetchable set and read the verdict
  nowhere, because what the resolver could establish is the emulator's demand and what is fetchable is what the library
  holds; the two further inputs they do read (`fetch_for_required`, and the library's own finished ratio) are demand and
  inventory, not readiness gates. **A fourth frontend reader is the play row's BIOS badge**
  (`frontend/src/utils/playSection.ts::extractBiosInfo`), where `"absent"` is a second established absence beside the
  required count. Whether the count sees the same thing is the core author's choice, which is why the badge may not be
  left to it: a core marking every image optional leaves `required_count` at 0 and the comparison beside it vacuously
  false, while `"absent"` is the same whatever the core marked. `"unsettled"` deliberately raises no badge, the same
  reading a withheld required row gets: the badge claims a file is NOT THERE, and nothing established that. **Where BOTH
  ignorances hold** — a console needing an image whose required folder row could not be judged, the LRPS2 shape and a
  reachable one — `biosSummary` names the withheld ROW rather than the console. They are not two gaps over two different
  file sets: a `required_by_active` row always carries the launching emulator, so it is always one of the rows the
  disjunction is read over. It is always one of the unjudged rows that verdict is read over rather than a finding beside
  it — the decline needs at least one such row, and this is one — and need not be the only one, since another image the
  core declares can be unjudged too; it is the only half of the pair that can name a file, and naming it points at the
  file list, where its caveat explains itself. **`"absent"` is tested BEFORE the level's decline**, and the pair never
  arrives at all today because the backend lands `absent` on `missing`. Since #1863 that order lives ONCE, in
  `biosSummary`, which is what every wording surface reads — `PlatformsTab.tsx`'s row tooltip last, since it kept a copy
  of the order and an older spelling of the states for a cut longer and described one platform in two vocabularies a
  keypress apart. **The module's own drift lock is a test that reads components as SOURCE** (`biosSummary.test.ts`, over
  the phrase list the module builds its answers from, with the ratio's twin in `biosHeldRatio.test.ts`) — and since
  #1866 it SWEEPS the set it searches rather than naming it (`frontend/src/test-utils/componentSources.ts`, every
  non-test `.tsx` under `frontend/src/bigpicture` or `frontend/src/shared`), because the naming is what failed: both
  locks listed two components while three rendered these states, and a surface missing from such a list carries no lock
  at all and cannot be told from one that never drifted. Deriving the set from who IMPORTS the module would be worse
  than the list — a surface wording a state for itself is exactly one that does not import it. **What neither lock can
  catch is a component inventing a NEW wording for one of these states**: only a copied phrase is searchable, so a green
  run there is evidence about copied sentences and about nothing else. Two limits of the sweep, both deliberate: it is
  `.tsx` only, so a wording helper extracted into a `.ts` beside its component is unsearched
  (`frontend/src/bigpicture/panelState.ts` is such a file and quotes BIOS prose today), and `frontend/src/utils` is out
  of scope because that is where the phrases legitimately live **Where the launching emulator states a one-of group,
  this axis is silent** (`classify_system_image(..., groups=...)` answers `not_demanded`): the group IS the console's
  demand, said region by region, and it is judged as the next entry sets out. A second, coarser reading of the same
  demand beside it would be one requirement stated twice
- **A one-of group is ONE requirement, judged by the regions its options serve — and an option never makes its row
  required** — test + prompt-only — `tests/domain/test_firmware_groups.py` pins the verdict (every state, a region
  nobody checked keeping a covered group off `met`, a stated uncovered region staying missing beside an unchecked one,
  the game's own regions, RomM's region names, the download rule) and, in `TestAnyConsoleIsJudgedTheSameWay`, an
  invented console with invented regions judged by the same rules;
  `tests/adapters/test_atlas_firmware.py::TestOneOfGroups` pins that an option borrows no requirement, that a file that
  is a plain row and an option is one row, and which caveats name a group's other regions;
  `tests/domain/test_bios_status.py::TestAOneOfGroupIsOneRequirement` pins that a group counts once and how each state
  reaches the level and the token; `tests/services/test_firmware.py` (`TestAOneOfGroupIsOneRequirement`, the group cases
  of `TestOnePlatformOneEmulator` and `TestOneRomOneEmulator`, and `TestDownloadRequiredFirmware`'s group cases) pins it
  end to end, the ROM's own region, and that the button's count and the download are one set; the frontend halves are
  `frontend/src/utils/biosSummary.test.ts`, `biosGroup.test.ts`, `playSection.test.ts`, `BiosTab.test.tsx` and
  `library/PlatformsTab.test.tsx`. The rule spans the adapter (`adapters/atlas_firmware.py::_wants` and `_groups`), two
  domain modules (`domain/firmware_groups.py` judges, `domain/bios_status.py` counts), the service
  (`services/firmware/status.py`, `downloads.py`, `game_detail.py` passing the ROM's regions), six frontend modules
  (`utils/biosSummary.ts`, `utils/biosGroup.ts`, `utils/playSection.ts`, `utils/biosFetchable.ts`,
  `bigpicture/BiosTab.tsx`, `bigpicture/library/PlatformDetail.tsx`) and the two type modules they share
  (`types/firmware.ts`, `api/backend.ts`), and nothing joins them. **Prompt-only**: no consumer reads an option's `need`
  as its row's `required` — `required` comes off an emulator's plain declarations alone; a group's verdict is never
  folded into a colour on the wire — the state is one of four words and the frontend picks the colour; `unknown` is
  never read as `met` or as `unmet`; the play badge leaves a `partial` group out (`required_partial`) as it leaves out
  what nothing could judge; `Download required`'s count and the download it starts both come from `fetch_for_required`
  (`domain/firmware_groups.py::fetched_as_required`) and never from a second rule; and nothing in the judging knows a
  console, an emulator or a region by name — a group the resolver states for a console nobody has worded must be judged,
  counted and fetched with no code change, its regions printed in the resolver's own spelling where no name exists. The
  region sets the verdict reads beside the options come from the entry's own caveats, and which ones — and which one
  must never be read as unchecked — is `adapters/atlas_firmware.py::_UNCHECKED_REGION_CODES`'s. **Unseen by every
  test**: a region whose every name the resolver refused arrives with neither an option nor such a caveat, so a group
  can read `met` over it; emu-atlas#556 asks for it
- **Which emulator a set of answers is about is ONE pick per scope — a platform's, and a ROM's — and every answer in
  that scope is a projection of it** — test + prompt-only —
  `tests/services/test_firmware.py::TestOnePlatformOneEmulator` asserts the two surfaces AGREE across every way a
  platform arrives at an emulator (no pick, each of the three ES-DE offers, a pin naming an emulator the catalogue no
  longer lists, a pin whose command cannot be baked) rather than pinning today's value, because a value test would pass
  for a third resolution that diverges on some other configuration;
  `::TestDownloadRequiredFirmware::test_it_fetches_what_the_platforms_own_pick_calls_required` holds the download button
  to the same pick. The pick is `domain/emulator_commands.py::resolve_platform_option` — the per-platform override
  (`settings.json` `platform_cores`) when its label still names a bakeable emulator, else the es_systems default — and
  it is the read-path precedence `ActiveCoreResolver` applies minus the per-game layer. Three call sites read it today:
  `FirmwareStatusReader._platform_emulator` (which serves BOTH the overview's `active_core` / `active_core_label` and
  `check_platform_bios`'s `launching_emulator=None` fallback) and `FirmwareDownloader._platform_emulator_identity`.
  **Nothing joins them**, and a fourth resolution is exactly what this entry is about: the pane displayed a just-picked
  PCSX ReARMed and judged the platform by the libretro system default beside it, so one PlayStation read `not_demanded`
  / `ok` on the game page and `absent` / `missing` on the pane. `.label` and `.emulator` must come off ONE call — two
  calls agree by coincidence, which is what the old pair did until an override was set. **One seam now carries the pick
  rather than a projection of it**: `BiosChecker` takes a `LaunchingEmulator` (`domain/emulator_commands.py` —
  `emulator` and `label`, both read-only), so the per-game caller hands over the whole resolution and
  `check_platform_bios` reads both projections off that one value. A mismatched pair is not representable there, which
  is why the answer may state its own `active_core_label`: it is the label half of the pick those very counts were
  filtered by, and the game page's BIOS headline names the emulator from it (`TestTheAnswerNamesTheEmulatorItJudgedBy`,
  which hands the check two picks differing only in label and holds the name to moving while the judgment does not).
  That covers this seam and no other — the remaining sites still pair by discipline. **The key is the emulator IDENTITY,
  not `.core_so`** (#1821): the identity names a standalone pick as readily as a libretro one, where `core_so` is `None`
  for every standalone emulator and sent the rows back to "every declaring emulator". Reaching for `.core_so` here again
  restores that degradation silently, because the field is still there and still right for the picker payload beside it.
  `CoreInfoProvider.get_active_core` — the "first libretro entry, bakeable or not" reading these sites used — has no
  production caller left. **The ROM scope is the same rule one layer in, over a different pair of modules**: the game
  page is assembled by two services that each ask `ActiveCoreReader.active_emulator_for_rom` for themselves —
  `services/cores.py::get_platform_core_info` names the pick in the picker, `services/game_detail.py::get_bios_status`
  scopes the BIOS question to it — and `::TestOneRomOneEmulator` asserts they agree across every way a ROM arrives at an
  emulator (nothing pinned, the platform's pick, a per-game override, the override over a platform pick naming something
  else, a standalone pick, a stale pin that degrades). They read one seam today and nothing says they must; the picker
  reaching for `active_core_for_rom` — the `.so`-space projection right beside it — would answer `None` for every
  standalone pick and send the BIOS rows back to the platform's own, which is the platform-scoped defect above, per ROM.
  The one-of groups an answer judges are that same pick's too (`FirmwareCatalogue.groups_for(identity)`), and both
  classes hold the group verdicts in their agreement (`_emulator_dependent`), so a second resolution feeding the groups
  would show as a disagreement there rather than as a group judged for another emulator. **What the ROM sibling cannot
  pin is the fixture's own default**: `FakeCoreInfoProvider.get_default_emulator` builds its invocation from the
  `active_core` tuple, which carries no identity, so a test on the bare fake resolves an unpinned ROM to a `None` where
  the live adapter resolves it to an emulator — `_DeclaredDefaultCoreInfo` in that file renders the declared default the
  way `AtlasCatalogueAdapter` does, and every other fixture on the bare fake still exercises the weaker resolution
- **A platform's BIOS answer is asked for one platform at a time, and a row that has not got one yet is never rendered
  as a row nothing could be established for** — test + prompt-only —
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx` pins the four halves that can be seen from a test: the two
  renderings apart (an outline dot and "Checking…" against the solid grey dot and "Nothing is known"), the focused row
  asked ahead of the rows above it, the walk stopping at unmount, and a read issued before a core change not overwriting
  the one issued after it. Each was mutation-checked. **The JOIN between the two calls is pinned once**, in
  `tests/contract/test_firmware_status_read.py`: it composes them over the real wiring and holds the result against the
  key set the single whole-page call answered with, which is the one thing the service tier cannot do — its ~25
  whole-page tests compose through a local helper that would reproduce a composition bug rather than catch it. **The
  rule spans three frontend modules and one backend split, and nothing joins them.** `services/firmware/status.py`
  answers `get_firmware_status` (which platforms the page can speak for) and `get_platform_firmware_status` (one
  platform's whole entry — 106-486 ms each against 4.6 ms for the overview, measured); `usePlatformsPage` owns the walk,
  the per-slug ordering counter and the four-valued `firmwareState`; `PlatformsTab` draws the dot; `PlatformDetail`
  words the pane. Every failure here is silent and looks like an answer. A state-bearing field creeping back onto the
  overview payload gets rendered over a platform nobody has asked about yet. A fifth rendering path reading
  `firmware === null` instead of the state says "nothing could be established" about most of the list for the first
  seconds of every visit — which is the confusion this cut exists to remove, restored by a truthiness test. An answer
  already held is not taken back by a later failure (`firmwareStale` beside the state, never instead of it), and "the
  overview did not name this platform" is one of the two ways to hold one. **Two halves no test reaches**: the `alive`
  guard in the hook's `accept` is unobservable under React Testing Library, which drops a write to an unmounted tree
  itself — what a test can see is the walk stopping, so the guard states the rule rather than being held to it; and
  whether an 8px outline reads as "not yet" against a filled dot is device-only, like everything else about this list's
  legibility
- **The whole-machine firmware inventory is never asked with content verification, and the per-platform reading is never
  asked without it** — prompt-only — `firmware_inventory()` (`FirmwareResolver`, `AtlasFirmwareAdapter`) is asked
  unverified: `verify=True` there sweeps every unclaimed file under the BIOS root plus each declared file the packaged
  identity table covers at a matching size, and its two callers — the home migration's untracked-BIOS sweep and
  `download_firmware(firmware_id)` — need only where a file GOES. `firmware_for_system(<system>)`
  (`FirmwarePlatformResolver`, `AtlasPlatformFirmwareAdapter`) is asked WITH it, and that half is the one a reader is
  likely to "optimise": drop the flag and two answers go silent rather than loud — a packaged card that identifies its
  image by content names no file at all (DuckStation comes back `declaration="packaged"` with an empty list and no
  system recording), and every folder declaration's verdict falls to `None`, which takes each platform holding one to
  `unknown`. Measured on the reference machine: 64-318 ms per system verified, against 248 ms for one unverified
  whole-machine sweep — the per-system read performs no unclaimed sweep at all, which is what bounds it. Nothing detects
  either direction: `verify` is one keyword argument on each call and every test stays green
- **No sentinel objects on the wire — explicit JSON-representable tagged values only** — prompt-only — no sentinel
  survives on the wire today (`NO_MIGRATION` retired with #1004, legacy `slot:null` confirmation with #1276), so the
  rule now guards reintroduction; nothing mechanical detects a new one
- **Every destructive op has backup-or-confirm; never delete data that exists nowhere else** — test + prompt-only —
  save-file removals route through the `.romm-backup` funnel (`MatrixExecutor.quarantine_local_file`; the removed-game
  cleanup's claimed variant is `PruneSaveSupport.quarantine_prune_saves`); every other delete path carries the rule
  unmechanized. Removed-game cleanup takes the **confirm** leg for one case deliberately: installed ROM content the user
  did not select for the recovery bundle is deleted with its row. The ROM is re-downloadable from RomM where a save is
  not, the per-candidate opt-in and its consequence are stated in the confirmation dialog and the user guide, and the
  row cannot be removed at all without a fresh 404 — so this is a disclosed choice, not an exception that drifted in.
  The adopt dialog's **replace** exit is the second such case, and it does **not** rest on that justification: the
  premise is that the content is the user's own — a different rip, a patch, a romhack — which is exactly what the server
  cannot hand back. What carries it instead is that the user is shown both sides, offered a content check, and chooses
  between two named outcomes behind a second confirmation ([ADR-0028](../adr/0028-adopted-install-is-an-install.md)).
  That reasoning covers the **ROM** only: an adoption's Overwrite also replaces save and savestate files, and those take
  the **backup** leg through the same `MatrixExecutor.quarantine_local_file` — every argument ADR-0028 gives for not
  quarantining a ROM (gigabytes, no sensible retention, re-fetchable from RomM) inverts for a save, and a savestate is
  synced nowhere at all. It is the first caller to hand that funnel a directory outside the saves root: it takes the
  directory it is given, so a savestate's backup lands in `<states>/.romm-backup/`. Following a moved save directory
  (`services/saves/save_directory.py`) takes the same **backup** leg on a collision — detail:
  [Following a Moved Save Directory](save-file-sync-architecture.md#following-a-moved-save-directory). The installer
  replaces the user's database and settings on two paths, and they are held to the rule differently. A **rollback by
  hand** (`install.sh --rollback`) takes the **backup** leg: it stops the unit and copies the database files and
  `settings.json` it is about to replace into `rollback-backup/` under the data root — put in place over the previous
  copy the same way as the update's backup, and never touched by an update — and a copy that cannot be made refuses the
  rollback with nothing changed. No prompt: the copy is what makes asking unnecessary. The **automatic rollback** of an
  update whose new version did not answer makes no such copy, because all it discards is what that version wrote while
  the installer waited for it, and that version was never seen to answer. Both are pinned in
  `tests/scripts/test_install_sh.py` (`TestRollingBackByHand`, and
  `TestAnUpdateThatDoesNotStart::test_it_keeps_no_copy_of_what_the_failed_version_wrote`); what an update and a rollback
  do, in order: [Running an installed one](../contributing/development.md#running-an-installed-one). A settings file
  Tender does not read — older than version 13, without a whole-number version, or not a JSON object — is written over
  with the defaults by the start's own save and takes neither leg: 1.0.0 is a breaking release, and everything such a
  file held can be entered again ([PersistenceAdapter notes](backend-architecture.md#persistenceadapter-notes)). An
  install record whose file is missing stays ([why](database-design.md#a-download-whose-file-is-missing)); an install
  record goes only by something the user does: **Uninstall** and **Uninstall all ROM files** (`RomRemovalService`),
  **Forget this download** (`RomRemovalService.forget_download`, which deletes no file and refuses with `file_present`
  while the recorded file or folder exists), a download or adoption of another version of the game (the sibling
  supersede through `remove_rom_unchecked`), and **Clean Up Removed RomM Games** (the `roms` row's delete cascades);
  never by the start-up step, which only reports it (`StartupHealingService.report_missing_installs`). Pinned by
  `tests/services/test_startup_healing.py::TestReportMissingInstalls` and `tests/contract/test_missing_download.py` for
  the start-up step and the forget; that the list of paths is complete is prompt-only — nothing fails when a new path
  that deletes `rom_installs` rows is not one the user starts
- **A BIOS file is deleted only where a `downloaded_bios` record names it under one of the platform's firmware slugs,
  and only at the path that record holds** — test + prompt-only —
  `tests/services/test_firmware.py::TestDeletePlatformBios` and `::TestDeleteOneBiosFile` pin every direction
  end-to-end: an emulator-shipped file survives, a hand-placed file under a server file's name survives, our own
  download is still removed once RomM no longer holds it, a download whose placement has since moved is unlinked where
  it was written rather than where the placement now points, and a per-row delete takes only the record it names. What
  makes the destructive-op rule (the `backup-or-confirm` entry) concrete for BIOS files is that authority to delete
  comes from having placed the file, and the record is the only evidence of that, because `BiosFile.mark_downloaded` is
  written in the download path and nowhere else. So the records are the delete's whole input: it iterates them, not a
  status listing, which is also what keeps a download RomM has since dropped deletable instead of gated behind a file
  list that no longer names it. The authorisations a reader reaches for instead are wrong in opposite directions.
  `downloaded` is `os.path.exists` and nothing more: authorise on it and Delete BIOS destroys firmware RetroDECK ships
  with its own components, which no RomM library holds and nothing here can fetch back — it did exactly that to
  `<bios>/dolphin-emu/Sys/codehandler.bin` on a real device. `on_server` describes what the library holds _now_, not who
  wrote the file: authorise on it and a file dropped from RomM after we downloaded it is stranded on disk with nothing
  in the UI able to remove it. The PATH has its own version of the same trap: a status row's `local_path` is recomputed
  from today's placement, so for a file fetched before an emu-atlas bump moved it the name still matches our record
  while the path names whatever now occupies the new destination — RetroDECK's own `codehandler.bin`, in the case that
  motivated this. The count the UI offers is bound to the same set: `deletable_count` on the
  `get_platform_firmware_status` payload is records-still-on-disk, counted as distinct paths, because `local_count` is
  the library's progress ratio and is wrong in both directions — it hid the button entirely for a platform whose
  downloads had all left the library. **Since #1815 the same field is stamped per ROW** (`_stamp_deletable`), and the
  frontend authorises a destructive action on it: a row's Delete is offered where `deletable_count` is non-zero and
  nowhere else, and a folder row's counts the distinct files our records name underneath it, because a folder is never a
  download but what we put inside one is still ours — and two records naming one path are one unlink, the platform
  count's own rule read one layer in. That is a wire field a page reads to decide whether to offer a delete, so deriving
  it from `downloaded` — the same substitution as below, one layer out — puts the button on `codehandler.bin`;
  `TestGetFirmwareStatusDeletableCount` pins the row's answer for a file Tender did not place. **Three buttons now reach
  one removal loop** (`PlatformBiosDeleter._delete_recorded_io`, under a record predicate per button): a second copy of
  that loop is the shape this rule is about, because the copies would drift silently. **Nothing mechanical stands behind
  any of this.** A delete path looping a status list on `downloaded` alone would go green — which is exactly the shape
  this one had when it destroyed that file
- **Every read-mutate-write of a `RomSaveSyncState` runs under `SyncEngine.rom_lock(rom_id)`** — prompt-only — sync
  paths, `get_save_status`, and the three slot mutations hold the lock; mechanize via a `rom_save_sync_states.save`
  call-site audit
- **Which files a game's save consists of is the EMULATOR's answer, read live, and four of its five states refuse the
  sync — no probe, no state written** — test + prompt-only — `tests/adapters/test_atlas_saves.py` pins the five states
  and every way the question cannot be put, `tests/domain/test_save_answer.py` pins the precedence that makes "exactly
  one" well defined, and `tests/services/saves/test_save_shape_gate.py` pins the absences **each beside a control that
  asserts the same probe DOES happen for a syncable answer** — without those controls a service that had stopped probing
  entirely would pass. The answered save directory is not sync state: it lives in its own table
  (`answered_save_directories`) and may be recorded for a refusing answer, whose files are then followed when that
  directory moves — a move, not a sync
  ([Following a Moved Save Directory](save-file-sync-architecture.md#following-a-moved-save-directory)). The rule spans
  seven modules and no diff-scoped review sees it whole: `AtlasSaveLocationAdapter` reads the machine,
  `domain/save_answer.py` decides what the reading means, `RomInfoService.save_answer` turns it into names,
  `SyncEngine`'s three per-ROM entry points refuse on it through `sync_engine/_shape_refusal.py`, which holds the
  reading and the skip shape, `MatrixExecutor.sync_rom_saves` is the backstop every sync path crosses, and
  `services/saves/status/service.py` puts it on the wire. **Four halves have no mechanical check at all.** (1) The
  refusal is enforced at four call sites — the three per-ROM entry points, which report the skip via
  `sync_engine/_shape_refusal.py`'s `live_save_answer` / `sync_refusal`, and `MatrixExecutor.sync_rom_saves` (reached
  through `SyncEngine.do_sync_rom_saves`), the backstop that covers the whole-library sweep, whose single result has no
  room to name the ROM it passed over. A fifth entry point added without either goes green, and its failure is silent
  because a per-game probe for a shared card finds nothing and reports "no saves". The backstop is pinned by the ABSENCE
  of a server round-trip, because everything downstream of it is redundantly safe — a refusing answer carries no names,
  so nothing is probed or grouped even without it. The five write paths refuse for themselves, each on the same answer's
  `sync_directory` — empty for every answer a sync would not carry, a shared card with a known directory included — and
  with `save_shape_message` beside the reason: `switch_slot` (`slots/switching.py`), `copy_save_to_slot` (`copies.py`),
  `rollback_to_version` (`versions.py`), `confirm_slot_choice` with migration (`slots/setup.py`) and
  `resolve_sync_conflict` (`sync_engine/rollback.py`). `test_save_shape_gate.py` pins those five and nothing pins the
  list: a sixth write path that keys its refusal on `saves_dir` instead writes into a directory the answer never offered
  a sync. The same holds for following a moved directory first: `SyncEngine.follow_save_directory` is called before any
  local file is looked at by the four sync paths, the five write paths, the two deletes and the two counting reads
  (`count_platform_saves`, `get_save_status`), and a new reader of local save files that skips it looks in the directory
  the files have just left — nothing mechanical finds such a reader. (2) A configuration-role file is excluded by
  `SaveAnswer.synced_files` and included by `owned_files`, which is what a directory move must carry — a caller reading
  `components` directly gets neither rule, and syncing Saturn's `.smpc` overwrites the console settings the user chose
  on the other device. Saturn is the only example that actually reaches the rule on a stock RetroDECK: MAME states a
  per-game `.cfg` too, but its answer classifies as not-established, so the sync refuses before any role is consulted.
  The rule is a DENIAL — `CONFIGURATION_ROLES` names what to hold back — and turning it into an allow-list of the roles
  known today is the one change here that fails in silence and in the expensive direction: the resolver's own `unknown`
  role (a file on the machine no declaration describes) and a component with no role at all are both carried today, and
  an allow-list drops them, along with every role upstream names next. `tests/domain/test_save_answer.py` and
  `tests/adapters/test_atlas_saves.py` pin both directions; nothing else would notice, because a dropped file is simply
  a file the page does not mention. (3) The two axes a rendering must read alongside the state are single fields nothing
  forces a consumer to touch. `SaveAnswer.unestablished` holds three shapes, and a truthiness test on
  `state == "unestablished"` collapses "nobody has audited this core" into "the folder is known and the names are not"
  and into "the question was never put". `content_installed` is worse, because ignoring it is invisible: an uninstalled
  ROM answers with a state, a directory and a full file list, every name a prediction about the path the game WOULD
  occupy, so a surface that renders them tells a user their uninstalled game already has three save files. The wire flag
  beside each name is `carried`, not `synced`, for the same reason — it names the RULE applied to a file, never that
  file's sync state. (4) **The question must carry the ROM's REAL content path**, because the answer turns on the
  content file's own EXTENSION — PUAE answers `save-inside-content` for an Amiga `.adf` and establishes nothing for an
  `.hdf`; Genesis Plus GX answers a shared `scd_*.brm` for a Sega CD `.chd` and a per-game `.srm` for a `.bin`. Within
  `RomInfoService` the system and the path are decided in exactly two places — `_installed_answer` for a ROM on disk and
  `_uninstalled_answer` for one the library only knows about — so ADR-0010's slug leak has two sites to guard there
  rather than one per caller. A **third** site exists outside it: `services/rom_adoption/renamer.py` asks the resolver
  directly for the save and the savestate directory of both launch paths of a rename, taking the system off the adoption
  target the service resolved — so it cannot leak the slug, and it is a site the same rule has to hold at. A synthetic
  stem passed anywhere else answers a different question in a shape that looks like an answer to this one, and nothing
  would say so. It is also why every per-system pin in `tests/adapters/test_atlas_saves.py` is keyed by
  `(system, extension)`: a pin that does not name the extension it asked with is pinning nothing, which is how two
  independent measurements of the same systems produced contradictory fact lists. **Every path asks live and nothing
  caches an answer** — every call detects the emulator sources afresh, and only the resolver's machine, which remembers
  a core's probe by the core file's path, modification time and size, outlives it — because the user changes a core's
  options in the emulator's own quick menu between a launch and the next sync; a display cache added without
  invalidating it on every sync entry is the one change that makes this rule fail silently and expensively. Detail:
  `docs/architecture/save-sync-coverage.md`, GLOSSARY.md → Save state / Save scope
- **Per-slot server reads/deletes go through `domain/save_slot.py` (legacy omits `&slot=`, client-filters)** —
  prompt-only — `get_slot_saves` / `get_slot_delete_info` / `delete_slot` / `list_file_versions` / `rollback_to_version`
  use `slot_query_param` + `save_in_slot`; RomM can't address `slot:null` via the param, so legacy MUST omit it + filter
  client-side, and a legacy delete is refused up-front
- **Every save-sync decision comes from `compute_sync_action` (via `list_saves`), never the `negotiate` op list; every
  automatic upload POSTs `overwrite=false` (409-backstopped); `overwrite=true` only from an explicit `keep_local`** —
  test + prompt-only — the hand-enumerated core cases (`tests/adapters/test_gavel_native_decision_table.py`) and the
  core property tier (`tests/adapters/test_gavel_native_property.py`) + contract 409 tests (`tests/contract/`); new
  upload/dispatch call sites are prompt-only
- **Both save-sync decisions run in the compiled gavel core, reached only through the `ComputeSyncActionFn` /
  `ResolveUploadConflictFn` seams; `domain/sync_action.py` holds only the `SyncAction` vocabulary the core answers in,
  so a change to either decision is a contract change carried by re-copied gavel vectors** — test — both vendored vector
  families run against the core (ladder in `tests/adapters/test_gavel_native.py`, decision table in
  `tests/adapters/test_gavel_native_table_vectors.py`); the `.so` and the vectors are pinned to the same upstream
  release tag and are bumped together
- **`applied_launch_options` is written only by the six recorded-state writer sites (sync ack-commit, download-complete,
  adopt-complete, uninstall, home-migration, version-switch), each recording the exact command the frontend wrote;
  excluded from the sync UPSERT; the only sanctioned reset is Force Full Sync's clear-to-NULL (a wrong recorded value is
  the only path to a wrong delta-skip)** — test + prompt-only — each writer site carries a value-exact test; new
  launch-options write paths are prompt-only — mechanize via a `set_applied_launch_options` /
  `record_applied_launch_options` call-site audit. Download-complete and adopt-complete are one site in the code
  (`RomInstallRecorder.do_record_applied_launch_options`) and two in the flow, because an adopted install is an install
  in every respect (ADR-0028). The uninstall site — `RomRemovalService._drop_install_record` for one ROM,
  `_uninstall_all_roms_io` for the bulk run, both recording `""` — serves "Forget this download" as well:
  `forget_download` is the uninstall without the file deletion, so it records the same `""` through
  `_drop_install_record` rather than adding a seventh writer site, and
  `tests/services/test_rom_removal.py::TestForgetDownload` and `tests/contract/test_missing_download.py` pin the value
- **An abandoned-chunk stash's whole-unit apply staging (`pending_sync` / `pending_all_roms` / `pending_cover_sources`)
  is never mutated while the stash is pending (box IDLE) — every run-entry path passes `try_begin_run`, which clears the
  stash before any staging write** — prompt-only — the invariant holds today rather than being aspirational; mechanize
  via a staging-writer call-site audit
- **An apply chunk's ack identity — `active_unit_id` / `active_chunk_index` and a fresh `unit_complete_event` — is
  stamped on the box BEFORE that chunk's `sync_apply_unit` is emitted, with nothing awaited in between** — test +
  prompt-only — `tests/services/library/test_chunk_dispatcher.py::TestAckIdentityPrecedesTheEmit`, which wraps
  `ChunkDispatcher._emit` and records the box at call time over a two-chunk unit. The rule spans three modules and no
  diff-scoped review sees it whole: `ChunkDispatcher` stamps, `services/library/_state.py` holds the fields and their
  verbs, and `SyncReporter.report_unit_results` validates an incoming ack against them (#1041). **What the test pins is
  the ordering inside the dispatcher, not the round-trip** — it observes a mock's call-time state, so a real frontend
  ack racing a real emit is still unexercised, and every other suite lets the emit mock swallow the call. The failure
  mode is why the entry exists rather than being left to the comment: stamp after the emit and a fast ack is rejected as
  stray, the wait then stalls the full 60-second heartbeat window, and the run ends by stashing a chunk the frontend had
  already applied — slow, plausible-looking, and silent (#1052 / #1367)
- **Every path on which the user answers the preview question leaves a live snapshot on neither side — the
  pending-preview store (`frontend/src/utils/pendingPreviewStore.ts`) and the backend's `pending_delta`** — prompt-only
  — three paths clear the store and tell the backend, and all three are the Sync page's: Apply (`applyPreview`), Cancel
  (`cancelPreview`) and Refresh (`computePreview(true)`, which discards before asking for the next one). The fourth is
  the cancel that lands just after a preview was staged, which never adopted it into the store and so discharges the
  rule by discarding server-side alone. A fifth path is not an answer at all and is held to the same rule: a successful
  **Force Full Sync** (`forceFullSync`) discards the state the preview was computed against, so it clears the store and
  tells the backend too — a preview left standing there offers an Apply that would skip exactly what the clear armed a
  re-fetch for. Main holds none of them, and holds none of them for a stronger reason than a division of labour: it
  starts no run and computes no preview, so it never holds one to answer for — its slot opens the page, and its Cancel
  ends a run rather than answering a preview. So the answer is given once, where the change table is. Nothing mechanical
  can tell: an answer path is a page handler, and neither the store nor the backend can know that a call it never
  received was an answer. Forget the store and a table stands over a decision already made; forget the backend and the
  terminal-stage re-ask fetches it back a round trip later
- **A prune run's claim reservation and its refusal of every conflicting endpoint happen in one atomic hold of the prune
  conflicts' lock (the preview rebuild does not), and frontend-owned Steam work holds a heartbeated lease through every
  continuation's final write, and a continuation with an owner is tombstoned by its owner's teardown** — test +
  prompt-only — prune service and prune conflicts race tests + contract endpoint-entry matrix
  (`tests/contract/test_conflict_refusals.py`), and `frontend/src/utils/pruneLease.test.ts` for the heartbeat and the
  owner's tombstone; new conflicting entry points are prompt-only
- **A removed-game cleanup's run claim is registered on the prune conflicts before the start's reservation is given
  back, so the two windows overlap and no conflicting endpoint runs in a gap between them** — test + prompt-only —
  `tests/services/prune/test_service.py::test_a_started_run_holds_its_run_claim_until_it_ends` and
  `tests/contract/test_prune.py::test_a_cleanup_refuses_conflicting_endpoints_from_its_start_to_its_end`. A gap would
  let a conflicting endpoint change local state after the start revalidated its preview and before the run acts on it.
  The reservation is taken by `hold_start` in `PruneService.start_prune`, which gives it back once the block around the
  whole start ends; the run claim is registered in the second hold of the prune service's own lock, where it sets
  `_run_id`. The order holds only because the second happens inside the first's block. Prompt-only: the start's body
  (`_start_prune`) is reached only through that `hold_start` — a caller that reached it another way would skip the
  refusal on held operations and leases, and would run the start's validation with no reservation in front of it, open
  to every conflicting endpoint that could change the local state the refreshed preview is checked against. The service
  test checks that the run is registered by the time `start_prune` returns, which is what keeps the order; the contract
  test holds a real start inside its preview rebuild and checks the refusal during validation and after the start
  returns, and its lifting once the run ends — it does not see a registration moved into the run task's first step,
  because that step runs before the start's caller resumes. Neither sees a second caller
- **A prune frontend action mutates Steam only after atomically claiming its exact run/token/discriminant/binding;
  repeats are idempotent and an outcome lost in transit is ambiguous, never success** — test + prompt-only — prune
  service claim tests + `frontend/src/utils/pruneActions.test.ts`; new action kinds are prompt-only
- **Every installed-content mutation is authorized by a descriptor-relative no-follow claim (root identity, descendant
  identities, and — where a bundle exists — regular-file hashes) revalidated immediately before it, never by a path
  re-lookup; refusal, partial mutation and ambiguity are reported, never rewritten into success. The hashes bind a
  deletion to bytes held somewhere else, so they follow the **bundle**, not the caller: a source a sealed bundle holds
  consumes the bundle's digest-bound claim, a source it did not capture seals a fresh content-bound one, and a removal
  with no bundle anywhere — recovery off, or a user-initiated uninstall — seals identity-only
  (`claim_source(..., digest=False)`). An identity-only claim is also the only one that may adopt interrupted
  `.{basename}.romm-prune-*` staging, and only where a surviving install row proves the path. Everything else holds for
  both: staging rename, mount checks, no-follow traversal, and exact-identity revalidation under writer exclusion held
  across each unlink. The one guarantee that differs is all-or-nothing: a content-bound removal leases the whole tree up
  front, an identity-only directory leases per unlink (a whole-tree hold hits `EMFILE` and makes large dumps
  un-uninstallable), so a writer arriving mid-loop yields a reported partial removal instead of a clean refusal** —
  test + prompt-only — descriptor-path, recovery-adapter, real RomRemovalService, and prune contract tests; new mutation
  adapters are prompt-only
- **Every prune frame carries its originating preview ID; only a matching pending preview may adopt a run, and an
  accepted contiguous terminal result seals it against every later frame** — test + prompt-only — prune service frame
  tests + `frontend/src/utils/pruneStore.test.ts`; new prune frame types are prompt-only
- **Every destructive RomM proof is bound to one canonical server-origin/token-origin/user namespace from preview
  through every exact-ID request; a namespace change is uncertainty, never a 404 deletion authority** — test +
  prompt-only — prune service namespace-race tests; new destructive RomM proof paths are prompt-only
- **Every write into per-rom detail state that crosses an `await` is bound to a rom identity — the store
  (`frontend/src/utils/gameDetailStore.ts`) via `writerForRom`, or the answer's own `rom_id` in `applySaveStatus`; the
  panel's state, event and tab-content modules (`frontend/src/bigpicture/panelState.ts`,
  `frontend/src/bigpicture/panelEvents.ts`, `frontend/src/bigpicture/panelTabContent.tsx` — the panel component itself
  holds none of these writes) via `RomBinding`, built by `bindRom` for a read a run of the `[appId]` effect issued and
  by `bindRomInState` for the active tab's panes, whose writer is built during render; the achievements tab
  (`frontend/src/bigpicture/AchievementsTab.tsx`) by construction, its React key being the rom id, so its state cannot
  outlive the identity it was read for. A version switch re-keys without closing, so neither the store's generation
  counter nor the panel's `[appId]` effect sees this class. Binding answers the wrong-rom question only; two answers for
  the SAME rom are ordered instead, by a sequence taken when the read is issued — the store's `loadSeq`, the panel's
  `takeReadTicket` (#1717). Four writes are unbound. Three are ordered: the two identity writes install what a binding
  would compare against, so ordering is all they can have, and the panel's lazy SAVES-tab slot load
  (`frontend/src/bigpicture/panelSlotsLoad.ts`) writes through the raw setter, ordered by its own `slots` ticket. The
  fourth has neither — the store's `cached.bios_status` fold runs in the same synchronous run as its guard. The event
  lane's `handleBiosChange` is bound and ordered: it re-reads `get_bios_status` for the rom it shows, whose emulator is
  resolved per ROM — a per-game pin over the platform's pick — and takes the `bios` ticket the panel's other BIOS
  re-reads take (#1718). The play button is NOT covered (#1714)** — test + prompt-only — the panel's fourteen bound
  sites each carry a version-switch test (`frontend/src/bigpicture/RomMGameInfoPanel.test.tsx`); the store side and
  every new write site on either are prompt-only, because a checker scoped to the store's own function bodies would be
  green on the case this rule was written for. The reasons behind the two writer mechanisms live at `writerForRom` and
  `RomBinding` — do not restate them here
- **Every row a reader must be able to reach on a QAM page is a row Steam can focus — a toggle, a button, or a
  `Focusable` declaring a stop of its own, including a table row with no action of its own, so the reader can walk the
  table** — check + prompt-only — `tender/qam-focusable-row` checks the narrow syntactic slice where an `@decky/ui`
  `Focusable` in the QAM module map has no nav-stop prop, static focusable descendant, opaque child, or unknown spread;
  focus order, runtime reachability, edge revelation, scrolling geometry, and controller behaviour remain prompt-only.
  The frontend suite cannot see those runtime properties: happy-dom has no nav tree, so a page whose rows are
  unreachable renders exactly like one whose rows are not, and a mouse-driven dev loop never meets the problem either. A
  region scrolls only by moving focus — Steam's plain `ScrollPanel` binds no gamepad direction — so an unreachable row
  is also an unscrollable one, and everything below the fold is simply out of reach with a controller. The trap is that
  a bare `Focusable` is a container rather than a focus stop: Steam's navigation asks the nav node the panel renders
  (`GetFocusable()`), which finds no stop on a row declaring none of the four options it reads — nor on one whose
  `onActivate`/`onOKButton` would have promoted it, since that promotion is skipped where an option was supplied — all
  stated in full at `docs/architecture/qam-panel.md`, which owns that mechanism. **What the check cannot see it says
  nothing about**, and three shapes matter. One opaque child anywhere among a row's children passes the whole row, which
  is how the removed-games cleanup's details region stood unreachable in the one release that has shipped with this gate
  green — a `Focusable` of plain text, fixed by declaring a nav option on it and not by the rule. A descendant the
  BROWSER can focus — a `tabIndex`, a `button` — is taken as an escape, so a row reachable with a mouse and not with a
  stick passes. And a row whose own `tabIndex` is its only affordance is reported rather than accepted, because that
  attribute reaches the rendered element and not the node. The rule spans every page the wide frame will host, and the
  frame cannot carry it: it holds a page's content as opaque nodes, never as rows it could check. **What the frame DOES
  carry is the content outside a region's focusable rows, at both ends** — a heading, a counts line or a column header
  above the first, a legend or a total below the last, each unreachable for the same reason and with no neighbour to
  ride along with — so `ScrollRegion` scrolls itself to the top when focus reaches the first stop in it and to its end
  when focus reaches the last (`revealEdge`, over `revealTop` and `revealBottom`). Both halves are pinned by
  `frontend/src/bigpicture/layout/ScrollRegion.test.tsx` over mocked geometry, so what is tested is the DECISION and not
  the scroll: whether the panel and the reader agree about which element is topmost or last stays device-only, like the
  rest of this entry. **Reachable is not near, and the same mechanism decides where a page puts its controls**: focus
  moves one row at a time, so a button under a list of N focusable rows is N presses from the top of the column —
  sixteen, measured on the device for the Cancel that stops a sixteen-unit run. That is why the Sync page's two button
  rows sit ABOVE their tables, which is the reading the layout wants anyway: what you can do, then why. Nothing checks
  that half either, and the suite is blind to it for the same reason — a page whose only control is a library's length
  below the point it opens at renders exactly like one whose control is a press away. Detail:
  `docs/architecture/qam-panel.md`, "Building blocks"
- **A list-and-detail page opens on the row it was opened WITH, not on its first row — because on that layout focus
  selects, so entry focus landing anywhere selects what it lands on** — test + prompt-only — `ListDetail.test.tsx` pins
  the mark and that it moves with the selection, `WidePage.test.tsx` pins that the frame asks for the declared stop, and
  both use a **non-first** row deliberately. **The rule spans three modules and nothing joins them**:
  `utils/entryFocus.ts` owns `ENTRY_STOP_ATTR` and `pageEntryStop`, `bigpicture/layout/WidePage.tsx` places entry focus
  through it rather than through `firstBodyStop`, and `bigpicture/layout/ListDetail.tsx` marks its selected row — plus
  whatever page passes a starting selection at all, `SettingsPage` today. A fourth wide page that opens on a non-first
  row and forgets the mark selects its first row instead, and every test still passes. **The end to end is unreachable
  here**: happy-dom performs no layout and does not reproduce Steam's focus resolution, so what the suite pins is the
  declaration and the finder, never the press. Only a controller confirms it. **What hid this for a whole review round
  is the shape of the failure, not its size**: Main's notices name sections, and the one naming the FIRST section keeps
  working, so a reader checking Open Connections sees the feature working while Open Controller lands on Connections. A
  check that exercises the first row proves nothing about the rule. The mark sits on a `display: contents` wrapper
  AROUND each row rather than on the row, and that is load-bearing rather than stylistic: `pageEntryStop` calls
  `firstBodyStop(declared)`, which searches DESCENDANTS — a mark on the row itself finds no candidate inside it, falls
  back to the first row, and ships the defect under a comment saying it does not
