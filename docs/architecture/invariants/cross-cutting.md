# Invariants: Cross-cutting

Rules no single area owns: who writes `settings.json`, the program's directories and identifiers, naming, coverage
exclusions, locks, links and vendored trees. It also holds the long form of the backup-or-confirm rule, whose statement
stands in `CLAUDE.md`. How the code is layered is in [code-structure](code-structure.md). How an entry reads and binds,
and the other areas: [the register's index](index.md).

## Entries

- **`settings.json` is written only by its owner (`adapters/persistence.py`)** — check —
  `scripts/check_settings_owner.py`
- **Where this program's directories are is resolved once from the environment, and every consumer reads them off
  `AppDirectories`** — prompt-only — `domain/app_directories.py` is the ladder (`TENDER_*`, then XDG, then the built-in
  defaults) and it is pure: the environment is handed in, so every rung is checkable against a table. `main.run()`
  resolves it once and hands it to `bootstrap()`, which derives nothing, and `RuntimeBundle` carries no directory at all
  — it used to carry two, and that is how a question about a plugin loader's own layout came to sit beside a question
  about the user's data as two plain `str` fields on structs the composition root passes around. **Counting rule** (an
  AST walk for an attribute in `{config_dir, data_dir, cache_dir, state_dir, runtime_dir, code_dir, bin_dir}` whose base
  ends in `directories`): **19 reads over three modules**, `main.py`, `bootstrap/adapters.py` and
  `bootstrap/services.py` — `code_dir` 5, `cache_dir` 4, `data_dir` 4, `state_dir` 3, and one each for `config_dir`,
  `runtime_dir` and `bin_dir`. The eighth field, `handover_dir` (`None` where `XDG_RUNTIME_DIR` is unset), is read once,
  in `bootstrap/adapters.py`, for the start hand-over. Re-derive it rather than trusting the number. **One field is read
  in `main.py` alone** and nowhere else: `runtime_dir`, which the port file lives in. `state_dir` is read there twice,
  by the logging setup that opens it and by the injection whose crash record lives under it, and once in
  `bootstrap/adapters.py`, for the record of a rolled-back update. `config_dir` has exactly one reader,
  `PersistenceAdapter`, and `bin_dir` exactly one, the launcher install in `bootstrap/adapters.py`. The pairing that
  matters is `cache_dir` against `data_dir`: re-derivable data goes under the first and data that is not re-derivable
  under the second — covers, artwork and the SGDB artwork cache on the first because they are re-derivable from the
  server and the database on the second because it is not; a system that clears caches must be able to clear one and not
  the other. The launcher's home is the read whose mix-up a user would see rather than the next start only, since
  `launcher_in_bin_dir(directories.bin_dir)` is carried on as `ShortcutLauncher.path` and written into every shortcut's
  `exe`. `bin_dir` is one of the two fields not named after this program (the other is `code_dir`, wherever the program
  was installed; GLOSSARY.md's "The program's directories" is the home of that split) — it is the directory every
  program a user installs for themselves puts a binary in, which is why nothing under it may be treated as ours to
  remove. Nothing mechanical tells the eight apart: they are seven `str` fields and one `str | None` on one frozen
  struct, so a read of the wrong one is a rename away and fails silently in whichever direction it happened to point.
  **One reader sits outside the backend**: `bin/tender-rom-launcher` starts without it, so it spells the hand-over
  folder from `XDG_RUNTIME_DIR` itself, the same path `AppDirectories.handover_dir` holds. **One raw read of
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
  and at `DB_FILENAME` themselves, because a diff that folds either opens neither the docstring nor this file.
  Prompt-only: the reverse fold through an aliased import and the third home, the frontend's `SESSION_BREADCRUMB_KEY`
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
  `backend/` (less the vendored `_vendor/` and `native/`), `tests/` and `scripts/` and fails on any name a module binds,
  reads, declares or imports that carries the word: a name, an attribute, a parameter, a keyword argument, a function or
  class, an import, its alias or the module a `from` import names, a type parameter, an exception or pattern capture, a
  class pattern's keyword, a `global` or `nonlocal`. Each half has its own list of exceptions — `NAMES_NOT_ABOUT_TENDER`
  beside the rule and `_NAMES_NOT_ABOUT_TENDER` beside the test — for names that carry the word because they name
  someone else's plugin (`DeckyPluginLoader`, `plugins` in the rollup and ESLint configs, `extraPlugins` in
  `rollup.config.js`, the two install tests about a Decky plugin), each with its reason; an exception is matched as a
  whole name, and either list fails on an entry nothing carries any more. **Neither half reads prose**: a string literal
  is not a name, so the `"plugin_version"` key a recovery bundle's manifest carries stays, and a vitest title, a comment
  or a docstring that calls Tender a plugin passes both, as does a shell script, a file name, a name built at run time
  (`getattr`, a computed key), and an excepted name put to a new use for Tender; a Python string annotation
  (`x: "Settings"`) passes the backend half
- **A coverage exclusion names a property of the code, never a place: every frontend-scoped entry stands in BOTH
  `frontend/vitest.config.ts`'s `coverage.exclude` and `sonar-project.properties`' `sonar.coverage.exclusions`** —
  check + test

  Every file entry carries its reason as a `// coverage-exempt:` marker in the file's own first lines, and every marked
  file is listed

  `scripts/check_coverage_exclusions.py`, and `frontend/src/typesHoldNoValues.test.ts`. **The two lists spell a shared
  entry differently and that is not drift**: Sonar runs from the repository root and Vitest from `frontend/`, so
  `src/types/**` there is `frontend/src/types/**` here, and the gate normalises before comparing. An entry spelled
  repo-relative on the Vitest side excludes nothing at all — Vitest would resolve it to `frontend/frontend/...` — so it
  is reported by name rather than normalised into agreement with Sonar's identical-looking copy. (A folder entry is
  admitted only from the script's `FOLDER_ENTRIES`, where membership in the folder IS the property; the backend/config
  entries are Sonar-only and the frontend test glob Vitest-only, each declared there with its reason so the asymmetry is
  stated rather than tolerated). Two accidents it removes, both silent: `src/patches/**` excluded a FOLDER, so a file's
  coverage obligation changed when it was moved out of the folder and nothing said so; and `steamShortcuts.ts` sat on
  Sonar's list and not on Vitest's under a comment claiming the two were aligned. **What the check cannot see is
  MEMBERSHIP in a folder entry's directory** — the two admitted folders are checked to exist and their contents are
  never read by it. For `frontend/src/types/` a test answers instead: `frontend/src/typesHoldNoValues.test.ts` imports
  every module there, at any depth, and fails on any value one exports; a module that runs code at module scope without
  exporting a value passes it. For `frontend/src/test-utils/` nothing does, so a real module filed there is exempted by
  its PLACE, with no marker asked for and nothing failing: the very accident above, still live for that directory. It is
  declared rather than mechanized on purpose — "is this reached only from the test run" is not a cheap check, and a
  half-check would exempt on a property nobody stated while reading as enforcement. **Nor can it see the marker's
  SENTENCE** — a false reason passes green, which is what the three stated reasons this cut found were: "no logic to
  assert" over a file with four passing tests, "no isolated logic to assert" over 88.65% line coverage, and "thin
  plugin-entry shim" over 89.09%. Only the first was replaced by a truer marker; the other two files lost their
  exclusions outright, which is also how their list drift was settled
- **Every pinned version in a lock satisfies its `.txt` source constraint (`requirements-dev.*` at the root,
  `docs/requirements.*` beside the docs)** — check — `scripts/check_lock_sync.py`
- **Every local markdown link in tracked docs resolves (file target + heading/attr-list anchor)** — check —
  `scripts/check_markdown_links.py`
- **Every tree under `backend/_vendor/`, and the resolver's recorded machines under `tests/adapters/atlas_vectors/`, is
  pinned by the `<pkg>.SHA256SUMS` beside it: every manifest entry under `<pkg>/` matches the vendored file's digest** —
  check

  The vendored file set EQUALS the manifest's set restricted to that prefix, and a package directory with NO manifest is
  a failure

  `scripts/check_vendored_trees.py` (the manifest is discovered, never named in the script, so the next vendored package
  is guarded by default rather than when someone remembers — the hole this closed was a second tree, `vdf`, sitting
  unpinned beside a pinned `atlas` while the gate reported OK. The set-equality half is the part `sha256sum -c` cannot
  do at all: the plain form fails on a correct copy, because a wheel's manifest lists release artifacts and dist-info
  files we never vendor, and `--ignore-missing` — the flag that makes it green again — exits 0 after a vendored file is
  deleted). **What the check cannot see is the manifest itself.** For `atlas` it is upstream's own release manifest, so
  the digests additionally prove identity with the tagged release; for a patched copy like `vdf` it is our own digest of
  the tree we ship, so a manifest regenerated to bless a hand-edit passes green and only review catches it — which is
  why regenerating one is the last step of a deliberate re-copy and never the answer to a failing gate. The licence
  assertion is data-driven, not package-driven: the sibling `<pkg>.LICENSE` is checked exactly where the manifest
  carries a dist-info licence entry, and a sibling the manifest carries no entry for is reported as pinned by nothing —
  otherwise regenerating a wheel's manifest from its own tree would delete the licence check in the same step, silently,
  while the file stayed. Deliberately outside it: `backend/native/` is pinned by its own `sha256sum -c` over one `.so`,
  and `__pycache__` is ignored wherever it appears, on both sides of the comparison. The resolver's recorded machines
  under `tests/adapters/atlas_vectors/` are the second root
  ([#2585](https://github.com/danielcopper/romm-tender/issues/2585) D1; why there, that folder's README). Their manifest
  is generated here, because upstream digests the release asset only as a whole, so it proves no identity with the
  release. The release line in that folder's README names the asset and its digest, and
  `tests/services/test_firmware_vectors.py` holds that line against the vendored resolver's version and upstream's
  manifest — which ties the NAME of the copy's release to the resolver's, and nothing more. **Nothing compares the files
  with the asset's contents**: steps 1 and 2 of that README's update procedure, which check the asset and unpack it, are
  the only guard. **Two things are outside it by accident of shape, and neither is loud.** The gate sees **directories
  only** (`package_dirs` filters on `entry.is_dir()`), so a single-module dependency dropped in as `_vendor/six.py` is
  never asked for a manifest — and there is no shape here that could pin one: dropping a `six.SHA256SUMS` beside it
  makes the gate fail with "it pins no vendored tree", so the guarded-by-default half is a property of package
  DIRECTORIES alone. And `rglob` does not descend into a symlinked directory, so every file below one is invisible to
  the set comparison whatever the per-file symlink guard does; git records the link itself as mode 120000, which is what
  makes it a review question rather than a silent one

## Backup or confirm

The rule — every destructive operation has backup-or-confirm, and never deletes data that exists nowhere else — stands
in `CLAUDE.md`'s register, because its breach loses data for good and any area can break it. What holds it, the deletes
it covers and what stays prompt-only:

save-file removals route through the `.romm-backup` funnel, the save backup (`SaveBackup.back_up_save_file`; the
removed-game cleanup's claimed variant is `SaveBackup.back_up_claimed_save_file`, reached through
`PruneSaveSupport.back_up_prune_saves`); every other delete path carries the rule unmechanized. Removed-game cleanup
takes the **confirm** leg for one case deliberately: installed ROM content the user did not select for the recovery
bundle is deleted with its row. The ROM is re-downloadable from RomM where a save is not, the per-candidate opt-in and
its consequence are stated in the confirmation dialog and the user guide, and the row cannot be removed at all without a
fresh 404 — so this is a disclosed choice, not an exception that drifted in. The adopt dialog's **replace** exit is the
second such case, and it does **not** rest on that justification: the premise is that the content is the user's own — a
different rip, a patch, a romhack — which is exactly what the server cannot hand back. What carries it instead is that
the user is shown both sides, offered a content check, and chooses between two named outcomes behind a second
confirmation ([ADR-0028](../../adr/0028-adopted-install-is-an-install.md)). A choice in the game page's ARTWORK tab over
an image set in Steam is the third: Steam's copy of the image the user set is cleared before Tender's goes in, and that
copy may exist nowhere else, so the picker asks once more — Replace or Cancel — and Cancel changes nothing
([#2381](https://github.com/danielcopper/romm-tender/issues/2381) D13;
`frontend/src/bigpicture/ArtworkPickerModal.test.tsx`, "asks once more before it replaces an image set in Steam, and
answers to replace it" and "Cancel on the second question replaces nothing"). The icon is not asked about: Tender
repoints the shortcut and the file the user set stays where it was. That reasoning about the adopt dialog covers the
**ROM** only: an adoption's Overwrite also replaces save and savestate files, and those take the **backup** leg through
the same `SaveBackup.back_up_save_file` — every argument ADR-0028 gives for not backing up a ROM (gigabytes, no sensible
retention, re-fetchable from RomM) inverts for a save, and a savestate is synced nowhere at all. It is the first caller
to hand that funnel a directory outside the saves root: it takes the directory it is given, so a savestate's backup
lands in `<states>/.romm-backup/`. A BIOS file at an emulator's place is changed only by a "Replace…" the user confirmed
on its row. It takes the **backup** leg too, by a path of its own rather than that funnel: once Tender's copy has passed
RomM's checksum, the file moves to `<data>/backups/bios/<date and time>/<original absolute path>`, where it stays until
the user deletes it. Where the link then cannot be placed, it moves back only while its place is still free, never over
anything that lies there by then, and stays in the backup folder otherwise
([#2191](https://github.com/danielcopper/romm-tender/issues/2191) D5, D17, D33, D34). The backup leg is also taken by a
file lying in the firmware store without a record of Tender's whose checksum is not RomM's, before a download takes its
name (D25). Both go through `FirmwareStore.do_back_up` and are pinned by `tests/services/test_firmware_store.py`
(`TestReplacingAWrongFile`, `TestAStoreFileWithoutARecord`). Following a moved save directory
(`services/saves/save_directory.py`) takes the same **backup** leg on a collision — detail:
[Following a Moved Save Directory](../save-file-sync-architecture.md#following-a-moved-save-directory). The installer
replaces the user's database and settings on two paths, and they are held to the rule differently. A **rollback by
hand** (`install.sh --rollback`) takes the **backup** leg: it stops the unit and copies the database files and
`settings.json` it is about to replace into `rollback-backup/` under the data root — put in place over the previous copy
the same way as the update's backup, and never touched by an update — and a copy that cannot be made refuses the
rollback with nothing changed. No prompt: the copy is what makes asking unnecessary. The **automatic rollback** of an
update whose new version did not answer makes no such copy, because all it discards is what that version wrote while the
installer waited for it, and that version was never seen to answer. Both are pinned in
`tests/scripts/test_install_sh.py` (`TestRollingBackByHand`, and
`TestAnUpdateThatDoesNotStart::test_it_keeps_no_copy_of_what_the_failed_version_wrote`); what an update and a rollback
do, in order: [Running an installed one](../../contributing/development.md#running-an-installed-one). A settings file
Tender does not read — older than version 13, without a whole-number version, or not a JSON object — is written over
with the defaults by the start's own save and takes neither leg: 1.0.0 is a breaking release, and everything such a file
held can be entered again ([PersistenceAdapter notes](../backend-architecture.md#persistenceadapter-notes)). An install
record whose file is missing stays ([why](../database-design.md#a-download-whose-file-is-missing)); an install record
goes only by something the user does: **Uninstall** and **Uninstall all ROM files** (`RomRemovalService`), **Forget this
download** (`RomRemovalService.forget_download`, which deletes no file and refuses with `file_present` while the
recorded file or folder exists), a download or adoption of another version of the game (the sibling supersede through
`remove_rom_unchecked`, or — for a sibling recorded at an adoption's own target path — the hand-over in
`DownloadService._conflicting_sibling_install_ids`, which deletes no file), and **Clean Up Removed RomM Games** (the
`roms` row's delete cascades); never by the start-up step, which only reports it
(`StartupHealingService.report_missing_installs`). Pinned by
`tests/services/test_startup_healing.py::TestReportMissingInstalls` and `tests/contract/test_missing_download.py` for
the start-up step and the forget; that the list of paths is complete is prompt-only — nothing fails when a new path that
deletes `rom_installs` rows is not one the user starts
