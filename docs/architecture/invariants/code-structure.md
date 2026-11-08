# Invariants: Code structure

How the code is put together: background tasks, aggregates and their verbs, Units of Work and the seams they must not
wrap, injected clocks, module size, service independence, import direction on both sides, and suppressions. How an entry
reads and binds, and the other areas: [the register's index](index.md).

- **`create_task`, `ensure_future` and `asyncio.Task(...)` are called under `backend/` only in
  `lib/background_tasks.py`, whose `BackgroundTasks` holds each task until it ends — `spawn` for a task nobody awaits
  (logged as a warning when it fails), `start_awaited` for one its starter awaits itself** — check

  An owner with a place in `Application.shutdown` cancels and awaits its tasks there; a task `gather` or `shield` makes
  from a coroutine its caller awaits on the spot is outside the rule, and so are `host/`, `_vendor/` and `native/`

  `scripts/check_background_tasks.py`. The event loop keeps only a weak reference to a task, so a task nothing else
  holds can be collected before it ends and its work is lost without a word; and the exception of a task nobody awaits
  reaches asyncio's own log only when the task is collected, or is dropped by a `gather(..., return_exceptions=True)`.
  `BackgroundTasks` holds every task it starts until the task ends. `spawn` is for a task nobody awaits: it logs one
  that ends with an exception as a warning under the name it was started with (a cancellation is not logged).
  `start_awaited` is for a task its starter awaits itself and whose failure the starter handles there, so it logs
  nothing. An owner with a place in `Application.shutdown` cancels and awaits what it still holds there; which owners
  have one, and what happens to the tasks of those that do not, is [Backend Architecture](../backend-architecture.md)'s
  shutdown paragraph. A service that needs a handle on its task keeps the one the helper answers. A task
  `asyncio.gather` or `asyncio.shield` makes from a coroutine its caller awaits on the spot is the other kind, and
  outside the rule. `backend/host/` is outside it too: it uses the standard library only, and its call tasks end with
  their connection; `_vendor/` and `native/` are copies. The check is an AST call-name scan with no pragma: it fails on
  a call named `create_task` or `ensure_future` in any shape, on `asyncio.Task(...)`, and on a bare `Task(...)` in a
  module that imports `Task` from `asyncio`; what it does not see is listed in its docstring
- **Aggregate state mutated only via verb-named methods: outside them no code assigns, deletes or `setattr`s an
  aggregate's field, or mutates one of its list, dict or set fields in place** — check + prompt-only —
  `scripts/check_aggregate_field_assignment.py`, by field name: the fields the class body of every `@cosmic_aggregate`
  class in `domain/` annotates, written on any receiver but bare `self` anywhere in `backend/` but `_vendor/` and
  `native/`; a value that only shares a field name takes `# pragma: no aggregate-check`. An aggregate's verb-named
  methods are where it keeps its invariants, so a field written from outside them leaves a state no method would have
  produced, and the repository persists it as it finds it. The check cannot know what a receiver holds, so it matches by
  field name rather than by variable or type: a variable list misses every aggregate held under a new name, and reading
  each receiver's type from annotations would re-implement the type checker with less recall. Matching by name means no
  aggregate escapes for being held under another name, at the price of a pragma on a value that only shares a field name
  (e.g. the ctypes struct in `adapters/gavel_native.py`). Bare `self` is exempt in every class, because an aggregate's
  own methods write through it and other classes' own fields share the names freely. `tests/` is not scanned: tests and
  fakes build state by writing fields on purpose. Unseen by it: a container field, or a value inside one, bound to
  another name, handed to a helper, or reached through a call, `getattr`, `or` or `and` (`(state.files or {})[k] = v`);
  a container mutated through one of its dunder methods (`state.files.__setitem__`); a `setattr` whose name is not a
  literal; a field the class body does not annotate, an aggregate outside `domain/`, and the in-place mutation of a
  container field typed by an alias or an abstract type; a store through bare `self` in any class; a subclass of an
  aggregate, or an aggregate bound to `self`, writing through bare `self`; `tests/` and `scripts/`; a file it cannot
  read or parse, which it skips without a word. This list is the one home of what the check misses; the script's
  docstring and Database Design point here. Prompt-only: those shapes, and the repository's column writers, which are
  outside the check because they write a column in SQL and assign no field, and so are no field assignment to the check.
  Most of them are their column's only write path because the sync's upsert never touches that column (their docstrings
  in `services/protocols/repositories.py`): the aggregate's verb sets the value and the writer persists that one column
  — `RomRepository`'s `set_selected_disc`, `set_chosen_sgdb_id`, `set_artwork_checked_at` and `set_shortcut_applied`
  each persist one column the aggregate's own verb has just set, and `set_emulator_override` the two
  (`emulator_override` and `emulator_override_source`) `Rom.pin_emulator_override` has just set, so the sync upsert
  never touches them. Two more change aggregate state with no verb: `RomRepository.clear_all_shortcut_applied` (Force
  Full Sync, the reset the `shortcut_applied` entry ([library sync](library-sync.md)) sanctions) and
  `PlatformSyncStateRepository.revoke_skip` (by design, `domain/platform_sync_state.py`'s docstring). The writes the
  entry names as having no verb change the stored aggregate without its invariants being asked at all, which is why each
  is named, with what sanctions it where something does. Detail: [Database Design](../database-design.md) → AST
  field-assignment check
- **No UoW-opening seam (ActiveCoreResolver, PlatformSystems, uow_factory) is called while a UoW is open on the same
  path** — check — `scripts/check_uow_seam_nesting.py`, first seam family (the first of the **two** rules that script
  carries, over one shared matcher; the file-I/O rule below is a different hazard with its own seam list and its own
  failure message, and neither entry is evidence about the other). A seam reached as a bound method, through a helper, a
  local alias or a nested `def`/`lambda`, inside a UoW opened through a factory attribute not ending in `uow_factory`, a
  `__call__`-only seam, and a seam whose implementation later grows a UoW open slip past it
- **No file-I/O seam is called while a UoW is open — a Unit of Work wraps database reads and writes, never file or
  server I/O** — check — (GLOSSARY.md → Unit of Work, ADR-0006). `scripts/check_uow_seam_nesting.py`, second seam family
  (`IO_SEAM_METHODS`). The list is **the seams this checker can see and has been told about, never an inventory of the
  I/O seams that exist**: `DiscResolver.enumerate_discs` / `.resolve_for_install` (a recursive walk of the ROM's install
  directory), the two `CoreInfoProvider` reads — `get_active_core` and `get_emulator_options` — which are answered by
  the vendored resolver's live read of ES-DE's catalogue (every call from the panel opens it again; only a run's one
  reading of the sources keeps a system's answer for the run, and the answer carries the resolver's lookup of each
  entry's emulator on the machine), `StartCommandReader.start_command` (the same catalogue read, plus the files the
  command reads in and the distribution's own start; `StartHandover.hand_over`, which writes the start's file, is
  unlisted, because its name is also `ArtworkSlotHandover.hand_over`, a domain verb called inside a UoW),
  `SystemSupportedExtensionsFn` / `SystemKnownFn` (two more questions to the same catalogue, asked afresh from the panel
  and once per reading within a run), `SteamConfigStore.read_shortcut_exes` and `.read_shortcut_launch_options` (each
  parses Steam's whole `shortcuts.vdf`, for the one-time shortcut relocation and for a launch-options confirm that
  Steam's reports did not settle. **Listing them changes nothing at their call sites**: each service reaches its reading
  through `run_in_executor` as a bound method, which is this checker's documented blind spot, so the entries are a
  statement of the rule rather than an enforcement of it. They are also not the store's only real I/O — `grid_dir()` is
  called from `services/artwork/` (seven sites), `services/steamgrid/` (`service.py` and `_slots.py`),
  `services/shortcut_removal.py` and `services/library/reporter.py`, `set_steam_input_config()` from
  `services/settings.py`, `services/shortcut_removal.py` and `services/library/reporter.py`, `write_shortcut_icon()`
  from `services/steamgrid/service.py`, and `ensure_grid_file()` from `services/shortcut_artwork.py` — those are
  unlisted, and their being unlisted is a gap, not a judgement), `.read_shortcut_icons` (the same whole-file parse, for
  the shortcut artwork job's worklist, called plainly, so its entry is enforced), `.grid_file_names` (lists Steam's grid
  directory to answer which artwork slots a shortcut has), `.read_grid_file` (reads one grid file whole, for the ARTWORK
  tab to hash a slot's image; reached as a bound method, so listed rather than enforced), `.grid_file_stamps` (stats the
  files filling the ARTWORK tab's slots, for each thumbnail's signature), the shortcut artwork job's reading of Steam's
  renderer memory before each batch (`_renderer_above_ceiling`, with `LibraryService.renderer_above_ceiling` beside it:
  a `/proc` read, and at or above `GC_SKIP_BELOW_KB` a garbage collection over Steam's debugger, bounded at about 5 s,
  and a second read), `FirmwarePlatformResolver` (reads what one system's emulators want WITH content verification: it
  opens each candidate in a declared folder and reads it the way the emulator does — 59-173 ms per system on the
  reference machine at emu-atlas 0.22) and its whole-machine sibling `FirmwareResolver`, the save answer —
  `resolve_save_answer` and the saves package's own `save_answer` wrapper, a full read of the machine per ROM (the first
  ask about a core also runs that core's probe) — the savestate question put to the same catalogue entry
  (`resolve_savestate_location`) and the seam's detection question (`installation_detected`, which detects the emulator
  sources afresh and reads the answering one's health), the two path resolvers — `MigrationFileStore.realpath` (one walk
  per stored RetroDECK-home marker, a directory that may sit on the SD card the marker is pending a migration away from)
  and `ResolvedPathFn` (the same walk, but on **both** sides of a comparison, so a call site costs what the rows it
  checks cost, not what it checks them against) — and every `RetroDeckFolders` question: `download_folder`, `rom_root`,
  `rom_folders`, `bios_folder`, `saves_root` and `move_roots`, each a fresh reading of the emulator sources and a
  question to the resolver, which reads RetroDECK's settings file and ES-DE's, and a `realpath` of the answer — together
  with the services' own wrappers around them (`_rom_folder`, `_rom_folders`, `_resolve_target`, `_platform_dir`,
  `_saves_root`), which are what the services call; the save backup's two moves, `SaveBackup.back_up_save_file` (a
  creation and a listing of `.romm-backup`, a rename, and the removal of copies past its retention) and
  `.back_up_claimed_save_file` (a creation and a listing of that folder, and a rename), with `_back_up_save`, the
  adoption renamer's binding of the first; the save backup's check of that folder before a first move,
  `SaveBackup.prepare_save_backup_folder` (a creation and a listing of that folder); and every save-transfer step
  (`fetch_server_copy`, `hash_server_copy`, `download_save_file`, `upload_save_file`, `acknowledge_server_head`,
  `adopt_server_head`, `record_server_head`: a RomM request, a temporary file, or a measure of the local file). The
  firmware timing is the only entry a cost was measured for; every other one is listed from reading its implementation.
  One other real I/O seam was weighed and kept out — the reason is in the script's docstring, and it is not an
  exemption; nor is it an inventory of what else touches the disk. **"It's only a read" is the reasoning this rule
  exists to refuse**: `SqliteUnitOfWork.__enter__` issues `BEGIN IMMEDIATE`, so even a read-only UoW takes the write
  lock. The database is in WAL, so readers are unaffected — but every other **writer** waits on the lock for up to
  `busy_timeout=5000` and fails with `SQLITE_BUSY` if it is still held then, and `FakeUnitOfWork` shares no connection,
  so no unit test notices. Six call sites had drifted across the rule before anything looked (#1779), for the reason the
  check exists: nothing at a call site reveals that an injected seam touches the disk. **The rule and the gate come from
  reading code — no measurement of how long any of those transactions actually held the lock exists, and nothing here
  should be read as one.** What the check sees is the deadlock rule's matcher unchanged — an **attribute** call naming a
  listed seam, lexically inside a `with <...>uow_factory()` block in the same function scope — so it inherits every
  blind spot of that half: a seam behind a helper one level down, an alias to a local, a factory attribute whose name
  does not end in `uow_factory`, a nested `def`/`lambda` (which resets the scope by design), a seam **passed as a bound
  method** (`run_in_executor(None, self._disc_resolver.enumerate_discs, install)` — an attribute, not a call, and
  `run_in_executor` is exactly how `disc.py` and `cores.py` reach their `_io` bodies; the same shape
  `check_read_only_module.py` records for its own gate), and the hand-maintained list itself, which cannot notice a seam
  whose implementation _grows_ a file read later. Matching only attribute calls is deliberate: the pure
  `domain.disc_selection.enumerate_discs` shares a name with the seam and does no I/O — it is safe because its call site
  imports it bare, not because of the name. The call-shaped blind spot is shared with the deadlock rule and only this
  family closes it: for each `__call__`-only seam the list carries the attribute it is bound to — by convention rather
  than by construction, and only while such a name means one thing, which is exactly what keeps `_list_files` out. Which
  entries are call-shaped, and which of those also carry a twin under their implementation's own method name, is in the
  script's module docstring. The deadlock rule's own call-shaped seams stay open. One `# pragma: no uow-check` covers
  both families — it suppresses the line, and no seam is in both lists, so where a line does name two seams it silences
  both
- **Services never call clocks / sleep / uuid / random directly (inject the Protocol)** — check —
  `scripts/check_cosmic_call_bans.sh`
- **No module in `services/`, `bootstrap/`, `adapters/`, `domain/`, `lib/` or `models/` crosses the ~1000-LOC
  decomposition threshold** — check — `scripts/check_module_size.py` (no module is grandfathered any more: every one
  that predated the gate has dropped back under the threshold and left its `ALLOWLIST`, to which nothing is ever added.
  `main.py`, `_vendor/`, `tests/`, `scripts/` and `frontend/src/` are out of scope, each for a reason recorded at
  `SCOPE_DIRS`)
- **Service-independence contract list stays complete** — check — `scripts/check_service_independence_contract.py`
- **Layer import direction (services ↛ adapters, adapters ↛ services, …)** — check — `.importlinter` (`lint-imports`)
- **Frontend direction: `frontend/src/utils/` and `frontend/src/api/` never import either surface
  (`frontend/src/bigpicture/`, `frontend/src/desktop/`) or `frontend/src/shared/`; `shared/` never imports either
  surface; the two surfaces never import each other** — check

  Only `frontend/src/index.tsx` and its test import `frontend/src/qam/` or `frontend/src/boot/`, and neither of those
  two imports the other; `qam/` never imports either surface; `frontend/src/types/` imports nothing else from
  `frontend/src/`; and no `frontend/src/` module takes part in an import cycle

  `frontend/eslint.config.js` (`import-x/no-restricted-paths`, `import-x/no-cycle`). The surface pair is a peer rule,
  not a layer rule: the two share data and logic and almost nothing visual, so anything that turns out to belong to both
  moves DOWN, never sideways — UI into `shared/`, which both surfaces may import and which imports neither, and the rest
  into `api/`, `utils/` or `types/`. `qam/`, Tender's Quick Access entry, sits below the surfaces: it mounts the panel
  through the factory `index.tsx` hands it, and may import `shared/` like either surface — the panel's error boundary is
  there. `boot/` sits beside `index.tsx` instead, and may import a surface, because its start-up check has to see every
  Steam lookup the panel relies on, a surface's included — `boot/steamModules.ts` reads `AppDetailsRoute` from
  `bigpicture/patches/installGamePagePatch.ts`. The `qam/` and `boot/` zones name every directory under `frontend/src/`
  but their own, and every file directly in it but `index.tsx` and its test, by glob, so a directory or a module added
  there is covered without a change to them. These rules go inert rather than loud when misconfigured: until the config
  names `.ts`/`.tsx` for the plugin to read, `no-cycle` finds no cycle among the frontend's modules (the comment at
  `import-x/extensions` in `frontend/eslint.config.js` says how). `frontend/src/eslintBoundaries.test.ts` lints
  known-bad fixtures through the real config and fails if any of the rules stops reporting — a green `pnpm lint` alone
  proves nothing. For the cycle rule a type-only import is not an edge (erased at runtime), which is why the
  `api/backend.ts` ⇄ `utils/cachedGameDetailStore.ts` back-reference is not a cycle; a zone reports an `import type`
  like any other import
- **No bare `# type: ignore` / blanket suppressions** — check — `scripts/check_no_bare_ignores.sh`
