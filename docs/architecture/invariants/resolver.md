# Invariants: The resolver

Who reads the resolver's answer and its vocabulary, where its installations are detected, and the RetroDECK folders
taken from it; below the entries, [how an answer reads](#reading-an-answer). How an entry reads and binds, and the other
areas: [the register's index](index.md).

- **The resolver's answer is read only in the adapter that asks for it — `adapters/atlas_catalogue.py`,
  `atlas_firmware.py`, `atlas_launch.py`, `atlas_platforms.py`, `atlas_saves.py`, `emulator_sources.py` and
  `retrodeck_folders.py`: only they import the resolver, and only names in `atlas.__all__`** — check + test +
  prompt-only

  Outside them no backend module and no panel code names one of the resolver's codes, a distinctive value of any other
  closed vocabulary it exports or one of its distinctive answer field names; and every caveat, health-issue, unresolved
  and reason code the vendored resolver states is classified by one of them or stands on the list of codes nothing
  classifies today. Only infrastructure and homonyms are excepted: by name, each with its reason,
  `adapters/atlas_zstd.py` imports the resolver to hand it the zstd codec and `adapters/emulator_sources.py` holds
  `RealMachine` (`IMPORT_GRANTS`, `INTERNALS_GRANTED`); `tests/` is excepted as a whole, because tests build the
  resolver's machines and answers rather than read an answer for display; and a name that means something else in a
  module stands on `NOT_THE_RESOLVERS`, in the backend's check and in the panel's rule. The modules that read the answer
  today (`READERS_TODAY`, `RESOLVER_READERS_TODAY`), `atlas_firmware.py`'s three private imports
  (`INTERNALS_TO_REMOVE`), the homonyms, the generic words left unwatched (`GENERIC_VALUES`, and in the panel alone
  `PANEL_GENERIC_VALUES`) and the codes nothing classifies (`_UNCLASSIFIED`) stand on lists that only shrink

  `scripts/check_resolver_reading.py` (the backend's imports and vocabulary, and that
  `frontend/resolver-vocabulary.json` is what it writes today; what it does not see is listed in its docstring), the
  ESLint rule `tender/no-resolver-vocabulary` in `frontend/eslint.config.js`, kept live by
  `frontend/src/eslintNoResolverVocabulary.test.ts`, and `tests/adapters/test_resolver_codes.py` (a code is classified
  once a reading module imports its constant). Every list but `READING_MODULES`, which is held to what was decided
  rather than to what reads, fails on an entry nothing needs any more — a generic word once Tender's own code no longer
  speaks it or the resolver no longer states it; the readers', internals', homonyms' and generic words' lists fail their
  tests on an entry they did not hold when they were written, and the reading modules and the two grants are held to
  exactly what was decided; the codes nothing classifies are the classification test's own list, which fails on a code a
  reading module classifies or the resolver no longer states, so a code added to it is an edit of that test. Unseen by
  the ESLint rule: a second site in a listed or excepted file reading the name it stands there for; a name as a plain
  string (`"satisfied" in row`, `Pick<T, "satisfied">`, `T["satisfied"]`); a code, value or name spelled as the name of
  a class field, a class method, a method in a type, an enum member, a JSX attribute or a binding (a variable, a
  parameter, a function, a class, a type, an import or an export), or as a qualified name in a type (`typeof a.pal`);
  test files, `src/test-setup.ts` and `src/test-utils/`; a code or name built at run time; a field reached through a
  spread or `Object.entries`; a generic word of those vocabularies, which the check leaves out (`GENERIC_VALUES`); a
  word the panel alone speaks with a meaning of its own, which the rule leaves out in the panel
  (`PANEL_GENERIC_VALUES`); a word of Tender's own spoken inside one of those vocabularies (`"rejected"`); and a watched
  value handed on through a parameter or a field of Tender's own name. Unseen by the classification test: whether a
  reading module that names a code reads it rightly. Prompt-only: an entry on `NOT_THE_RESOLVERS` is a name that means
  something else in its module, never a read of the resolver's answer; a code the resolver adds is classified by a
  reading module rather than added to the list; Tender keeps no table of its own for anything the resolver answers (find
  rules, launch command patterns, launch-file types, RomM regions); a join is on the resolver's keys (`declared`,
  `(emulator, declared_index)`), never on `file_name` alone or a label; where the resolver gives no answer, Tender says
  it could not be established and an emu-atlas issue asks for it; no decision is proposed that lets Tender work out a
  fact the resolver states — because the resolver is slow, cannot answer yet, or another page already does it; and the
  derivations and imports on today's lists stand until the issue that builds their kind's reading module removes them
  ([#2579](https://github.com/danielcopper/romm-tender/issues/2579) D5): fixing one is not a finding, extending one or
  copying it to a new page is. Without the rule, every layer after the adapter decides a fact of its own from the raw
  answer, and the layers disagree: whether a file is required, whether a one-of group is met, whether a file is there,
  which file a requirement means (one image under two spellings became two rows), and what a caveat code means, copied
  into the backend and into the panel. The adapter turns the answer into Tender's own view, joined on the resolver's
  keys; the panel receives Tender's own closed set of marks per row, group and emulator and words them; and a Tender
  field that carries one of the watched names is renamed when the reading module of its kind is built
  ([#2579](https://github.com/danielcopper/romm-tender/issues/2579) D1–D3). Which names and values are watched, and why
  the others are not, is `WATCHED_NAMES` and `GENERIC_VALUES` in the script, and for the panel alone
  `PANEL_GENERIC_VALUES` in `frontend/eslint.config.js`. The checks hold the reading half of ADR-0042's first decision;
  the rest of it stays prose, the prompt-only part above
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
- **Every RetroDECK folder Tender downloads into, removes from, bounds a removal by or reads for the move code is the
  resolver's answer, asked through `adapters/retrodeck_folders.py`** — test + prompt-only

  No module reads RetroDECK's settings or builds such a folder from a root — except the move code, which joins `bios`
  and `saves` onto each old home a pending move left — and a root the resolver only assumed (`assumed` in its roots
  answer, `domain/source_roots.py`) or gives no path for is used for nothing, each root judged by its own provenance,
  also under a `marker-*` finding, and a system's folder going with its ROM root; where detecting the sources or the
  roots question raised, none of those folders is used — except that a download while RetroDECK is switched off says so,
  and a raise on a system's own ROM folder refuses only the presses that need that folder; a removal of a game's files
  is bounded by its system's own ROM folder, never by the ROM root

  `tests/domain/test_source_roots.py` (the rule) and `tests/adapters/test_retrodeck_folders.py` (the real resolver over
  tmp homes: every folder its answer, `TestEachRootIsJudgedByItsProvenance` over a settings file without `paths`, one
  key unset, a `version` alone unreadable, ES-DE's own default ROM folder and an assumed home that is missing, each
  `marker-*` finding refusing the BIOS, saves and move questions and `not-set-up` every question,
  `TestWhereAQuestionToTheResolverRaises` and `TestWhereDetectingTheSourcesRaises` refusing every question for a raise
  on the detection or the roots and only its own for a system's folder, and `TestNoModuleReadsRetroDecksSettings` over
  the code's string constants) and `tests/contract/test_retrodeck_folders.py` (under each finding, with a key unset, and
  where the detection or the roots could not be asked, no press downloads, deletes, cleans up or migrates in a folder
  that was not read, the cleanup is refused at its start for an assumed saves root, and a `version` alone unreadable
  refuses no press); the system-folder bound by
  `tests/services/test_downloads.py::TestASystemFolderLinkedToAnotherDrive` and the linked-folder and one-system cases
  of `tests/contract/test_prune.py`, with Uninstall all's one-system case in `tests/contract/test_retrodeck_folders.py`
  (`test_where_one_system_s_folder_question_raises_uninstall_all_goes_on_for_every_other_system`). A folder of Tender's
  own beside the resolver's is the drift this replaced: `retrodeck.json` read twice, a system's ROM folder built as
  `<roms root>/<system>` in five places while ES-DE may declare another, and a fallback to `~/retrodeck` that pointed
  downloads, deletes and the move code at internal storage while RetroDECK lay on an SD card. A root the resolver only
  assumed is the same kind of guess — RetroDECK's usual layout, below the home where the settings name one and at
  `~/retrodeck` where they do not — whatever finding stands beside it (a `retrodeck.json` without `paths` or with one
  key unset carries none), so using it would be that guess again, and the missing-root findings the resolver states for
  it name a folder nobody configured ([#2294](https://github.com/danielcopper/romm-tender/issues/2294)). The rules each
  question answers by are on [Config source parsers](../config-source-parsers.md#retrodecks-folders-retrodeckjson). A
  save's folder and a firmware file's place take the other route, the save and firmware answers
  (`adapters/atlas_saves.py`, `adapters/atlas_firmware.py`), and this entry does not cover them: a save's place comes
  from the emulator's own settings rather than from `retrodeck.json`, so under the three `marker-*` findings a save
  still syncs where the emulator keeps it, and a place those settings did not name is the save-answer entry's to refuse.
  Unseen by the tests: a folder joined onto a root under another name, and a caller that puts the question to
  RetroDECK's handle itself. Prompt-only: a new caller that downloads into, removes from or bounds a removal by a
  RetroDECK folder asks `RetroDeckFolders` and raises the refusal it answers with

## Reading an answer

An empty collection in a resolver answer is a statement about provenance, never about need — and Python spells it the
same as an absence. The verdict on a catalogue entry is `requirements_met`, three-valued: `True`, `False`, `None` for
"could not be established". It narrows only and is never `True` out of ignorance: with `verify=False` an entry whose
required files are **present** still answers `None`, because presence is not the question that field asks.

What an empty `requirements` list means is decided by `declaration` and by nothing else: `read` and empty is the only
pairing that means "this emulator needs no firmware"; `packaged` and empty means a card exists and this query
established nothing (a card may identify its image by content, so it names no file until the bytes are read);
`unsupported` means the emulator is installed and the resolver has no source for what it wants. Reading a length where
the verdict was asked produced a green "needs nothing" over a console that does not boot without an image — twice in one
hour, the second time with `requirements_met: None` already on screen.

The same shape recurs across the answer: `system_firmware: None` is nothing recorded rather than nothing needed, and
`core_so: None` is a standalone emulator's entry rather than no entry. Which emulator that is comes from `emulator`, the
identity field that stands on both kinds and on both the catalogue answer and the firmware one; `label` is presentation
(one `pcsx2_libretro.so` under two of them), a caveat's `token` is a third vocabulary again, and a caveat's `core` a
fourth — the libretro short name (`pcsx2`), which equals no identity.

**An answer about a system says how it resolved it.** Since emu-atlas 0.35.0 every answer to a question that names a
system — the catalogue, the ROM location, the launch, the launch command, the firmware answer — carries
`resolution.status`: `resolved` where the asked system or pairs named one system the source holds, else `unknown`,
`ambiguous` (several systems answer, the resolver names none) or `conflict`. Anything but `resolved` is the question's
own empty or unknown form, so a reading module reads the status before the answer and reads it as not established, never
as "nothing" — an empty emulator list under `ambiguous` is no list
([#2751](https://github.com/danielcopper/romm-tender/issues/2751)). An answer to a question that names no system states
no resolution and reads as resolved (`adapters/atlas_catalogue.py::resolution_status`). The platform question itself
carries none: whether the resolver can match a platform is its translation's `intersection_status`, and a match's
`status` says how firmly the system is here (`adapters/atlas_platforms.py::read_platform_answer`). An answer names a
system as a `(source, key)` pair; Tender forwards that pair to the next question and compares no system name of its own.
A firmware answer listing no emulator beside `firmware-declaration-unknown` is a look that found nothing to read, not
"nothing needed".

**The answer is entry-shaped**, and `answer.requirements` is a flattening that has already discarded `declaration`,
`requirements_met`, `caveats`, `unread` and `refused` — so an entry-level question answered from it is answered from
evidence thrown away before the question was put. Separately, `description` is outside the resolver's contract (the
packager's prose from a core's `.info`), so it is not a field to render as a row's headline.

Nothing mechanical carries any of this. The vocabulary overlaps Tender's almost exactly — `satisfied`, `required`,
`present`, `cores` and `description` exist on both sides and name different types — which is what makes a wrong reading
look like a correct one.
