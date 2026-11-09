# Invariants: Save-file sync

A running game's saves, the device id every save operation sends, the per-ROM lock, the emulator's answer for which
files a save is, save places with holes, slots, and the decisions the gavel core makes. How an entry reads and binds,
and the other areas: [the register's index](index.md).

- **No save path but the sync before a start, the one after a game and a conflict resolved before a start changes the
  saves of a game Steam lists as running** — test + prompt-only

  The RetroDECK home migration excepted, which [#2226](https://github.com/danielcopper/romm-tender/issues/2226) decided
  to remove: Sync All Saves Now and a platform's delete pass it over and name it, every press on its page that writes
  its saves is refused `game_running`, the removed-game cleanup skips its group, the follow of a moved save directory
  does nothing, and a start's stop reaches a sweep's or a Sync Saves' sync of that game

  An emulator that rewrites its save by path writes over a save swapped under it, and the next sync uploads that over
  the other device's newer save without a conflict; one that keeps the file open writes the session into the copy moved
  aside ([A game that is running](../save-file-sync-architecture.md#a-game-that-is-running)). The exempt paths run
  before the start or after it: a conflict the launch check resolves is passed `before_start` from the launch prompts'
  `resolveConflicts` — `gamepadLaunchPrompts`', and in the desktop dev build `desktopLaunchPrompts`' — never inferred.
  `tests/services/saves/test_running_game.py` pins the sweep's skip and its sentence, each refused press and the same
  press with the game not running, the platform delete, the follow on the status read, the local-save check and the
  platform count, the two exempt syncs, the conflict resolved before a start, and the stop of a sweep's and a Sync
  Saves' sync of the started game with another game's untouched;
  `tests/services/prune/test_planning.py::TestARunningGameIsLeftOut` the cleanup's skip before any probe and
  `tests/services/prune/test_service.py::test_a_running_removed_game_is_left_as_it_is_and_offered_again` its re-check
  before the Steam action; `tests/contract/test_running_game.py` the sweep, Sync Saves, the platform delete and the two
  conflict resolutions, and
  `tests/contract/test_prune.py::test_a_removed_game_steam_lists_as_running_is_left_as_it_is_over_the_real_wire` the
  cleanup, over the real wiring with Steam's reading included. **Prompt-only**: a new path that writes, moves or deletes
  a game's saves asks `SyncEngine.refuse_while_running` or `is_game_running` under the ROM's `rom_lock` before it looks
  at a file — nothing finds a path that does not — and only `pre_launch_sync`, `post_exit_sync`, a
  `resolve_sync_conflict` the launch check calls with `before_start` and the sweep, right after its own check, follow
  with `while_running`
- **Every save operation sends RomM the device id `SyncEngine.live_device_id` answers — registered or healed a moment
  before — and no other** — check + test + prompt-only

  `RommSaveApi.list_saves`, the sync logic (`do_sync_rom_saves`, `MatrixExecutor.sync_rom_saves`) and the save
  transfer's steps that name the device (`SaveTransfer.download_save_file`, `upload_save_file`,
  `acknowledge_server_head`, `adopt_server_head`) take a `LiveDeviceId`, only that entry builds one, and no save service
  but `SyncEngine` holds the `DeviceRegistry`

  RomM answers a listing without a device id with no sync row for this device on any save, so the decision reads every
  save as one this device never touched (the never-touched rows of the decision table, among them a conflict on a save
  this device is current on); and a stored id the server has since lost (a RomM database wiped or restored) is answered
  with a 404. An operation that sent the stored id instead would send no id while the device is unregistered, and a dead
  one after such a wipe. The entry registers or heals the device on every call (one `PUT /api/devices/{id}`), under a
  lock no caller takes anything beneath, and refuses `server_unreachable` when the registration could not reach RomM, or
  `device_not_registered` when it failed any other way — RomM, or a proxy in front of it, answered without registering
  the device (the id-less answer included), or the stored token could not be sent
  (`docs/architecture/save-file-sync-architecture.md`, "`DeviceRegistry` owns device identity"). Three things keep the
  rule. basedpyright, in the gate, refuses a stored id or `None` where a `LiveDeviceId` is taken; in basic mode it does
  not refuse a value typed `Any` — a `dict[str, Any]` read such as `ensure_device_registered()["device_id"]` — nor one
  an unannotated parameter carries. `scripts/check_live_device_id.py` (AST) fails on a `LiveDeviceId(...)` call or a
  `typing.cast` naming it anywhere under `backend/` but `SyncEngine.live_device_id`, and on `LiveDeviceId` imported
  under another name; it does not see the name bound to a variable and called through it, a call reached through
  `getattr`, a function that builds one and is defined inside the entry itself, a `# type: ignore` or
  `# pyright: ignore` that hands a plain `str` to a parameter typed `LiveDeviceId`, or `tests/`, where building one is
  how a test hands an id over. `tests/services/saves/test_device_registry_holder.py` reads every module under
  `services/saves/` and fails on an annotation naming `DeviceRegistry` anywhere but `SyncEngineConfig` and on a
  `DeviceRegistry(...)` assigned to an attribute of `self`; an unannotated parameter or attribute holding one, and a
  registry reached through another object, pass it. What the script and the test miss is listed in their docstrings.
  Prompt-only: each save operation calls the entry once, before its first request to RomM, and hands the id down; and a
  new request that names the device takes the id from there — `upload_save`, `download_save_content`, `negotiate_sync`
  and `confirm_download` take a plain `str`, and are reached today only through code that holds a `LiveDeviceId`
- **Every read-mutate-write of a `RomSaveSyncState` runs under `SyncEngine.rom_lock(rom_id)`** — test + prompt-only —
  the sync paths, `get_save_status`, the slot list, the three slot mutations, copy to slot, rolling back to a save
  version, the removed-game cleanup, deleting a ROM's or a platform's local saves and counting a platform's, the passes
  that record each installed ROM's save directory, the local-save check (`check_local_drift`) and the SAVES tab's read
  with save sync off (`get_local_save_files`) hold the lock (the count, the record passes, the check and that read for
  the directory follow, not for a write of the save state), and following a moved save directory runs inside the lock of
  whichever holder calls it. Tests pin the status
  (`tests/services/saves/test_status.py::TestGetSaveStatusRomLockSerialization`), the three slot mutations
  (`tests/services/saves/test_slots.py::TestSlotMutationLocking`), the slot list
  (`test_slots.py::TestSaveSlotsUnderRomLock`), a sync
  (`tests/services/saves/test_service.py::TestPerRomLockSerialization::test_per_rom_lock_serializes_concurrent_sync`),
  the removed-game cleanup (`tests/services/saves/test_prune_support.py::TestLockPruneRoms`), the local-save check
  (`test_status.py::TestCheckLocalDriftWithSaveSyncOn::test_the_check_waits_for_the_roms_sync_lock`) and the read with
  save sync off (`tests/services/saves/test_local_saves.py::TestTheRomsLock`); prompt-only: every other holder and any
  new read-mutate-write — mechanize via a call-site audit of `rom_save_sync_states.save` and `write_save_state`
- **Which files a game's save consists of is the EMULATOR's answer, read live, and six of its seven states refuse the
  sync — no probe, no state written; a place not read from the emulator's own settings is one of them** — test +
  prompt-only — `tests/adapters/test_atlas_saves.py` pins the seven states and every way the question cannot be put,
  `tests/domain/test_save_answer.py` pins the precedence that makes "exactly one" well defined, and
  `tests/services/saves/test_save_shape_gate.py` pins the absences **each beside a control that asserts the same probe
  DOES happen for a syncable answer** — without those controls a service that had stopped probing entirely would pass.
  The answered save directory is not sync state: it lives in its own table (`answered_save_directories`) and may be
  recorded for a refusing answer, whose files are then followed when that directory moves — a move, not a sync
  ([Following a Moved Save Directory](../save-file-sync-architecture.md#following-a-moved-save-directory)). The rule
  spans seven modules and no diff-scoped review sees it whole: `AtlasSaveLocationAdapter` reads the machine,
  `domain/save_answer.py` decides what the reading means, `RomInfoService.save_answer` turns it into names,
  `SyncEngine`'s three per-ROM entry points refuse on it through `sync_engine/_shape_refusal.py`, which holds the
  reading and the refusals it raises, `MatrixExecutor.sync_rom_saves` is the backstop every sync path crosses, and
  `services/saves/status/service.py` puts it on the wire. **Four halves have no mechanical check at all.** (1) The
  refusal is enforced at five call sites — the three per-ROM entry points, which report the refusal via
  `sync_engine/_shape_refusal.py`'s `live_save_answer` / `refuse_unsyncable`, the whole-library sweep, which asks the
  same refusal of every installed ROM it does not pass over as running through `SweepTally.passes_over` and counts what
  it passed over by the refusal's reason, and `MatrixExecutor.sync_rom_saves` (reached through
  `SyncEngine.do_sync_rom_saves`), the backstop behind them all. A sixth entry point added without `refuse_unsyncable`
  or the backstop goes green, and its failure is silent because a per-game probe for a shared card finds nothing and
  reports "no saves". The backstop is pinned by the ABSENCE of a server round-trip
  (`tests/services/saves/test_save_shape_gate.py::TestTheRefusalIsASkipAndNotAFailure::test_the_matrix_backstop_asks_romm_nothing_for_a_refusing_answer`,
  which calls it directly beside a control), because everything downstream of it is redundantly safe — a refusing answer
  carries no names, so nothing is probed or grouped even without it. The five write paths refuse for themselves. Four
  refuse on the same answer's `sync_directory` — empty for every answer a sync would not carry, a shared card with a
  known directory included — and with `save_shape_message` beside the reason: `switch_slot` (`slots/switching.py`),
  `copy_save_to_slot` (`copies.py`), `rollback_to_version` (`versions.py`) and `resolve_sync_conflict`
  (`sync_engine/rollback.py`). `confirm_slot_choice` (`slots/setup.py`) refuses through `refuse_unsyncable` itself,
  which reads the same, with or without a migration, as a sync of the game does, and `get_save_setup_info` beside it
  answers `sync_unavailable` off that same refusal. `test_save_shape_gate.py` pins those five, and
  `tests/services/saves/test_slots.py::TestAGameWhoseSavesCannotSync` the slot choice's plain path and the setup info;
  nothing pins the list: a sixth write path that keys its refusal on `saves_dir` instead writes into a directory the
  answer never offered a sync. The same holds for following a moved directory first: `SyncEngine.follow_save_directory`
  is called before any local file is looked at by the sync, write, delete, count and status paths that
  `docs/architecture/save-file-sync-architecture.md` lists: the four sync paths, the five write paths, the two deletes,
  the two counting reads (`count_platform_saves`, `get_save_status`), the local-save check (`check_local_drift`) and the
  SAVES tab's read with save sync off (`get_local_save_files`), and a new reader of local save files that skips it looks
  in the directory the files have just left — nothing mechanical finds such a reader. (2) A configuration-role file is
  excluded by `SaveAnswer.synced_files` and included by `owned_files`, which is what a directory move must carry — a
  caller reading `components` directly gets neither rule, and syncing Saturn's `.smpc` overwrites the console settings
  the user chose on the other device. Saturn is the only example that actually reaches the rule on a stock RetroDECK:
  MAME states a per-game `.cfg` too, but its answer classifies as not-established, so the sync refuses before any role
  is consulted. The rule is a DENIAL — `CONFIGURATION_ROLES` names what to hold back — and turning it into an allow-list
  of the roles known today is the one change here that fails in silence and in the expensive direction: the resolver's
  own `unknown` role (a file on the machine no declaration describes) and a component with no role at all are both
  carried today, and an allow-list drops them, along with every role upstream names next.
  `tests/domain/test_save_answer.py` and `tests/adapters/test_atlas_saves.py` pin both directions; nothing else would
  notice, because a dropped file is simply a file the page does not mention. (3) The two axes a rendering must read
  alongside the state are single fields nothing forces a consumer to touch. `SaveAnswer.unestablished` holds three
  shapes, and a truthiness test on `state == "unestablished"` collapses "nobody has audited this core" into "the folder
  is known and the names are not" and into "the question was never put". `content_installed` is worse, because ignoring
  it is invisible: an uninstalled ROM answers with a state, a directory and a full file list, every name a prediction
  about the path the game WOULD occupy, so a surface that renders them tells a user their uninstalled game already has
  three save files. The wire flag beside each name is `carried`, not `synced`, for the same reason — it names the RULE
  applied to a file, never that file's sync state. (4) **The question must carry the ROM's REAL content path**, because
  the answer turns on the content file's own EXTENSION — PUAE answers `save-inside-content` for an Amiga `.adf` and
  establishes nothing for an `.hdf`; Genesis Plus GX answers a shared `scd_*.brm` for a Sega CD `.chd` and a per-game
  `.srm` for a `.bin`. Within `RomInfoService` the system and the path are decided in exactly two places —
  `_installed_answer` for a ROM on disk and `_uninstalled_answer` for one the library only knows about — so ADR-0010's
  slug leak has two sites to guard there rather than one per caller. A **third** site exists outside it:
  `services/rom_adoption/renamer.py` asks the resolver directly for the save and the savestate directory of both launch
  paths of a rename, taking the system off the adoption target the service resolved — so it cannot leak the slug, and it
  is a site the same rule has to hold at. A synthetic stem passed anywhere else answers a different question in a shape
  that looks like an answer to this one, and nothing would say so. It is also why every per-system pin in
  `tests/adapters/test_atlas_saves.py` is keyed by `(system, extension)`: a pin that does not name the extension it
  asked with is pinning nothing, which is how two independent measurements of the same systems produced contradictory
  fact lists. **Every path asks live and nothing caches an answer** — every call detects the emulator sources afresh,
  and only the resolver's machine, which remembers a core's probe by the core file's path, modification time and size,
  outlives it — because the user changes a core's options in the emulator's own quick menu between a launch and the next
  sync; a display cache added without invalidating it on every sync entry is the one change that makes this rule fail
  silently and expensively. **A place not read from the emulator's own settings** — a settings file missing or
  unreadable, a source not set up, a bare RetroArch's marker, which is its `retroarch.cfg` — refuses as its own state,
  and what decides it is the answer's own findings (`domain/save_answer.py::place_not_read_finding`), never the source's
  health: ES-DE's settings and EmuDeck's RetroArch beside a standalone emulator are health findings about files no save
  of that emulator is placed by, and reading them would refuse a save whose place was read. RetroDECK's and EmuDeck's
  marker findings alone refuse nothing, because their marker names the source's folders and never where an emulator
  writes; a missing saves root refuses only where the source's roots answer states that root `read`, since a root it
  only assumed says nothing about a drive. `TestAPlaceNotReadFromTheSettings`,
  `TestTheRealResolverStatesWhereAPlaceWasNotRead` and `TestAMissingSavesRoot` in `tests/adapters/test_atlas_saves.py`
  pin both directions, the real resolver's included. Unchecked: such a refusal keeps the directory the resolver named,
  because the rename on adoption carries a game's saves there, so anything that moves files by `SaveAnswer.directory`
  has to check the state itself, as `services/saves/save_directory.py::_followable_directory` does — a new mover that
  does not carries files into a place the emulator's settings never named, and nothing would say so. Detail:
  `docs/architecture/save-sync-coverage.md`, GLOSSARY.md → Save state / Save scope. Prompt-only: every per-ROM entry
  point refuses through `sync_engine/_shape_refusal.py` and every sync path crosses the `MatrixExecutor.sync_rom_saves`
  backstop; the five write paths (`slots/switching.py`, `copies.py`, `versions.py`, `slots/setup.py`,
  `sync_engine/rollback.py`) each refuse on the answer's `sync_directory` (the slot choice through `refuse_unsyncable`,
  which reads the same), never on `saves_dir` — `test_save_shape_gate.py` pins those five, not the list, and
  `tests/services/saves/test_slots.py::TestAGameWhoseSavesCannotSync` the slot choice's plain path; the sync, write,
  delete, count and status paths that `docs/architecture/save-file-sync-architecture.md` lists call
  `SyncEngine.follow_save_directory` before they look at local files; configuration-role files are held back through
  `SaveAnswer.synced_files` by the denial `CONFIGURATION_ROLES`, never an allow-list, a directory move carries
  `owned_files`, and nothing reads `components` directly; a rendering reads `unestablished`'s shape and
  `content_installed` beside the state; the question carries the ROM's real content path, and system and path are
  decided only at `RomInfoService._installed_answer`, `._uninstalled_answer` and `services/rom_adoption/renamer.py`;
  nothing caches an answer; a refusal for a place not read keeps the resolver's directory for the rename on adoption, so
  anything else that moves files by `SaveAnswer.directory` checks the state first, as
  `services/saves/save_directory.py::_followable_directory` does.
- **A save place whose directory still holds a resolver template (`<cwd>`, `<region>`, `<save_id>`) is never followed,
  recorded, renamed into or used as a save-state place** — test + prompt-only — the resolver answers a place it cannot
  complete as a template and lists the holes in `needs`: a RetroArch save folder set as a relative path answers
  `<cwd>/…`, below the folder a game is started from, which Tender names it no value for (#2385 D20); Dolphin's GameCube
  cards answer `…/<region>/Card A`; a Wii or Cemu title answers `…/<save_id>/…`. Such a string names no folder. Carried
  into, it is a literal `<region>` folder or a path relative to the backend's own working directory, and the upgrade to
  emu-atlas 0.25.0 made that reachable for real files: a relative save folder that 0.24.0 answered as the platform
  default — recorded as such — answers `<cwd>/saves` after it, and a follow that took the new answer would move the
  game's saves into a folder below wherever the backend happens to run. The rule spans three files:
  `SaveAnswer.concrete_directory` (`domain/save_answer.py`) is `None` wherever a hole in `needs` appears in the
  directory as `<hole>`, `_followable_directory` (`services/saves/save_directory.py`) reads it for the follow and both
  records, the rename on adoption's `_save_dir` (`services/rom_adoption/renamer.py`) reads it, and
  `resolve_savestate_location` (`adapters/atlas_saves.py`) answers `None` — nothing established — while the savestate
  placement's `needs` is not empty. A hole in a file name alone leaves the directory a folder (Flycast's cards).
  Enforced by `tests/services/saves/test_save_directory.py::TestNeverIntoAPlaceWithAHole` (the upgrade case: a concrete
  record, a `<cwd>/saves` answer, the file left where it is, no folder created, the record unchanged; a region-keyed
  folder; a first sight that records nothing),
  `tests/services/test_rom_adoption.py::TestAdoptCandidate::test_a_save_directory_with_a_hole_still_in_it_carries_no_save`,
  and in `tests/adapters/test_atlas_saves.py` `TestWhatTheAnswerCarries`' hole cases,
  `TestTheSavestateQuestion::test_a_place_with_a_hole_still_in_it_establishes_nothing` and
  `TestTheVocabularyIsTheResolversOwn::test_the_start_folder_hole_and_how_the_resolver_spells_a_hole_in_a_path`, which
  holds the `<hole>` spelling to the resolver's own template constants. Unseen by them: a new mover or recorder that
  reads `SaveAnswer.directory` instead of `concrete_directory` — the field stays the resolver's answer, because the
  status read shows it — a savestate place taken from anywhere but that adapter, and a template the resolver spells
  other than `<hole>`, or one whose hole `needs` does not list. Prompt-only: a new mover or recorder of save files reads
  `concrete_directory`, and a new reader of a savestate place goes through `resolve_savestate_location`, so the guard
  holds at it without a test of its own. Detail: `docs/architecture/save-sync-coverage.md`, "Nor is it always the
  game's"
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
  `ResolveUploadConflictFn` seams; `domain/sync_action.py` holds only the `SyncAction` vocabulary the core answers in**
  — test

  So a change to either decision is a contract change carried by re-copied gavel vectors

  Both vendored vector families run against the core (ladder in `tests/adapters/test_gavel_native.py`, decision table in
  `tests/adapters/test_gavel_native_table_vectors.py`); the `.so`, the vectors and the `ctypes` structs in
  `adapters/gavel_native.py` are pinned to the same upstream release tag and are bumped together: a struct that lags the
  binary's header is read and written at the wrong offsets, and only a vector that exercises the moved field notices
- **An adopt of a head this device must confirm — one the core marks `acknowledge` in the sync, and Keep Local's adopt
  without an upload always — records its baseline only after `POST /api/saves/{id}/downloaded` succeeded; a failed
  acknowledgement records nothing** — test + prompt-only

  And the status read records no such adopt; Keep Local's adopt records the sync boundary as the sync's does, recording
  no core, so the last upload's stays

  The core sets `acknowledge` on an adopt of a head this device is not current on (romm-gavel `SPEC.md`, "Bookkeeping").
  Without the acknowledgement RomM keeps the device not current on that head, so a baseline recorded anyway would make
  the next local edit read as one under a head that moved past it — a conflict no other device caused — while recording
  nothing leaves the next sync to decide the same adopt again. One step holds the order:
  `SaveTransfer.adopt_server_head` (`services/saves/save_transfer.py`) confirms through the retry strategy and records
  only once that succeeded — once RomM's answer no longer shows this device not current, after at most one more
  acknowledgement (`SaveTransfer._acknowledge`; `docs/architecture/save-file-sync-architecture.md`, "A download is
  acknowledged once placed, never marked at the GET"). Two callers take it: `MatrixExecutor._dispatch_skip`
  (`services/saves/sync_engine/matrix.py`), which lets a failure reach `_dispatch_sync_action` to be reported as that
  file's error, and `RollbackOrchestrator._resolve_conflict_keep_local` (`sync_engine/rollback.py`), which lets a
  failure end the resolution, but for one while the file holds the head's bytes, which answers success
  ([#2664](https://github.com/danielcopper/romm-tender/issues/2664) D9). `SaveTransfer.record_server_head` records
  without confirming, for an adopt the core does not mark; `StatusService._partition_outcomes`
  (`services/saves/status/service.py`) records only such an adopt, because showing a page never writes to RomM. Enforced
  by `tests/services/saves/sync_engine/test_matrix.py::TestAnAdoptOfTheHead`,
  `tests/services/saves/sync_engine/test_rollback.py::TestKeepLocalAdoptAcknowledges`,
  `tests/services/saves/sync_engine/test_keep_local.py::TestKeepLocalAdoptsAsTheSyncDoes` and
  `tests/services/saves/test_status.py::TestTheStatusReadAndAnAdopt`. Prompt-only: a new consumer of an adopting `Skip`
  adopts through `SaveTransfer.adopt_server_head` when it reads `acknowledge`, and records through
  `SaveTransfer.record_server_head` only when it does not. Detail: `docs/architecture/save-file-sync-architecture.md`,
  "Outcomes"
