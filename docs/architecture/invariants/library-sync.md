# Invariants: Library sync: the run

A sync run's lifecycle and its seams, its progress frames, the per-unit rows, the shortcut record, the chunk
acknowledgements and the preview question. Skips and stamps are in [library-sync-skips](library-sync-skips.md), what a
run removes in [library-sync-removals](library-sync-removals.md). How an entry reads and binds, and the other areas:
[the register's index](index.md).

- **Sync run-lifecycle (`sync_state` / `current_sync_id`) written only via `LibrarySyncStateBox` verbs** — check —
  `scripts/check_sync_lifecycle_owner.py`
- **A library-sync seam is held only by the module owning the job it belongs to — in `services/library/`, `renderer_rss`
  / `renderer_gc` by `session_budget.py` and `artwork` by `cover_preparer.py` **and** `reporter.py`, the one confinement
  with two owners, never a third** — check

  `active_core` / `disc_resolver` / `emulator_sources` by no module, because a shortcut carries nothing about what
  starts its game. No module of the package but a seam's owner names it — `bootstrap/library.py` hands each to its
  owner's config, so a seam named anywhere else in the package, the façade included, is a finding

  `cover_preparer.py` holds `artwork` for the apply path's covers and `services/library/reporter.py` for commit-time
  cover-path finalisation — two owners because those are two different questions a unit asks at two different points;
  two owners is a named pair, not a licence for a third, and no other module of the package holds an `ArtworkManager`. A
  shortcut carries its appId and nothing about what starts its game, so the sync has no question about a ROM's emulator,
  its disc or the emulator sources, and what starts a game is asked at the start (`services/game_start.py`).
  `bootstrap/library.py` builds every part and hands each seam to its owner's config, so inside the package a seam is
  named only where its owner receives it, and anywhere else — the `service.py` façade included — it is a finding.
  `scripts/check_seam_owner.py` (AST over attributes named after a seam and over annotations naming its Protocol,
  resolved per seam rather than per module — owning one grants nothing about another. It sees the injection, not every
  later use: a seam aliased to a differently-named attribute or local, one reached through `getattr`, one passed
  positionally into a helper that holds it, a quoted string annotation, and a Protocol imported under an alias all slip
  past it — the last two only on the annotation half, because the constructor read that unpacks the config is flagged
  whatever the field is called). On the budget side the confinement holds `session_budget`'s stated promise that no
  renderer-RSS reading is taken anywhere else in the package
- **A module declared read-only calls no repository write — `services/library/local_library_reader.py` and
  `services/library/views.py`** — check — `scripts/check_read_only_module.py` (AST over the declared file's own calls,
  matching the two-attribute `<...>.<repo>.<method>` shape against the sixteen repositories the UoW exposes). Read or
  write is decided **by the name's shape** — `get` / `get_*` / `iter_*` / `count` plus the explicitly listed reads — and
  `tests/scripts/test_check_read_only_module.py` re-derives every method name from `services/protocols/repositories.py`
  and pins its classification, so a new repository method fails there until it is classified. **The two directions are
  not symmetric.** A read named outside the shapes is called a write: loud, safe. A **write named like a read is called
  a read and passes in silence** — `uow.roms.get_or_create(...)` is green today, which is the very accident class this
  entry is otherwise about, so "repository writes are named as writes" stays a prose rule the gate does not carry. It
  also sees only calls the file itself makes, and only as calls: a write behind a helper it calls, a write **passed as a
  bound method** (`run_in_executor(None, uow.roms.save, rom)` — an attribute, not a call, and the exact idiom
  `local_library_reader.py`'s reads are invoked through), an aliased handle (`repo = uow.roms`), a `getattr`-reached
  repository, and a repository name not in its list all pass green. What it does catch is the plain `uow.roms.save(...)`
  dropped into a read module because a UoW was already open there. The declaration earns its keep by making the boundary
  checkable rather than descriptive: the reader's reads open their own short UoW and are offloaded to an executor at
  points chosen for cheapness, so a write among them would land at a moment nobody picked. The platform stamp's DELETE
  sits in `unit_apply.py` on those grounds — it is a write, so a read-only module cannot hold it, and it is cohesive
  with the half of the apply pipeline that performs it: the DELETE sits with `UnitApplier.apply_unit`, which builds a
  unit's delta, while the re-stamp went to `ChunkDispatcher._build_final_platform_stamp` with the final chunk whose
  commit UoW it rides, so the stamp's two ends now sit in two modules and each names the other. **Why the DELETE sits
  exactly where it sits in that pipeline is a property of its call site, not of its module** — after the fetch, after
  the artwork, after the cancel guard, before the first chunk (ADR-0023 / #1025) — and that argument lives in full at
  the call site's own comment, which is the only place a move could not have carried it away from
- **An emitted `sync_progress` frame stops a run (`running: False`) only with a terminal stage, and a terminal stage is
  only ever emitted with the run stopped** — test — `tests/services/library/test_terminal_frame_contract.py`
  (structural, AST call sites and dict literals across all of `backend/services` — it covers the error paths a
  behavioural test would have to provoke one at a time, but a frame assembled by a helper it cannot follow, or emitted
  through an aliased callable, slips past it; its own scope tests pin the producers and the root it reaches, so a
  narrowing fails rather than shrinking the rule in silence. Frame producers are not confined to `services/library/`:
  `services/artwork/service.py` emits through an injected `emit_progress`). The QAM panel derives "a run is in flight"
  from `running` and keys the run's end — the status line, the live-ETA teardown, Main's stats re-read, the Sync page's
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
  `LibrarySyncStateBox` holds it with the slot, three separate backend frame builders carry it (`emit_progress`, the
  CANCELLED terminal `emit_cancelled` and the ERROR terminal `write_error_frame`, all three in `sync_progress.py`),
  three frontend start paths stamp it themselves on the optimistic frame they show before the first real one arrives
  (`useSyncPage`'s `computePreview` as `preview`, its `applyPreview` and `startRunDirectly` as `apply`), the frontend's
  `SyncProgress.runKind` (`frontend/src/types/sync.ts`) and `useSyncRunView` pass it through, `MainPage` both seeds it
  from the `get_sync_status` snapshot onto the store at mount and maps it to the slot's label, and two readers announce
  an apply run's failure off it — the `sync_progress` listener's toast in `index.tsx` and `useSyncPage`'s status line.
  **Nothing mechanical stands behind the seam between them**: a fourth frame builder that omits the key, a fourth start
  path that stamps the kind it is not, or a reader that spends the absent case on one of the two answers — a
  `runKind ?? "preview"`, a `=== "preview"` where the neutral branch was — goes green, because each test above pins one
  half and none of them pins the join. The failure is silent and worst exactly where the frontend cannot help itself:
  after a JS-context rebuild mid-run the store starts empty, the snapshot is the only thing that can say what the run is
  doing, and Main then tells the reader a real apply run is merely checking for changes. Why the kind cannot be derived
  at all is stated at `domain/sync_run_kind.py` and in `docs/architecture/qam-panel.md`'s Main section; do not restate
  it here. Prompt-only: every frame builder carries the key, every start path stamps the kind it is, and no reader
  treats a missing `runKind` as `preview` or `apply`
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
  a resume and the one start whose rows are true loses them. Prompt-only: every start path in `useSyncPage` clears, and
  a resume is told apart only by `syncResumeState(stats).canResume` at the press
- **`roms.shortcut_applied` is set only by the sync's commit, for a binding target (`SyncReporter`, through
  `Rom.record_shortcut_applied` and `set_shortcut_applied`); excluded from the sync UPSERT; the only sanctioned reset is
  Force Full Sync's clear (`clear_all_shortcut_applied`)** — test + prompt-only — a wrong record is the only path to a
  wrong delta-skip. `tests/services/library/test_reporter.py`
  (`TestCommitUnitResults::test_commit_records_the_shortcut_applied_for_binding_target`,
  `TestClearSyncCache::test_clears_every_applied_shortcut_so_the_next_apply_skips_nothing`) and
  `tests/adapters/repositories/test_rom.py::TestShortcutApplied`; a new writer is prompt-only — mechanize via a
  `set_shortcut_applied` call-site audit. Every bound shortcut carries its appId
  ([#2221](https://github.com/danielcopper/romm-tender/issues/2221) D7, D10), so a download, an adoption, an uninstall,
  "Forget this download", the RetroDECK home migration and a version switch write nothing into a shortcut and record
  nothing; `tests/services/test_rom_removal.py::TestForgetDownload` and `tests/contract/test_missing_download.py` pin
  that the record stays as the sync left it (D17)
- **An abandoned-chunk stash's whole-unit apply staging (`pending_sync` / `pending_all_roms` / `pending_cover_sources` /
  `pending_followed_keys`) is never mutated while the stash is pending (box IDLE) — every run-entry path passes
  `try_begin_run`, which clears the stash before any staging write** — prompt-only — the invariant holds today rather
  than being aspirational; mechanize via a staging-writer call-site audit
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
  already applied — slow, plausible-looking, and silent (#1052 / #1367). Prompt-only: the round trip through
  `services/library/_state.py` and `SyncReporter.report_unit_results`
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
