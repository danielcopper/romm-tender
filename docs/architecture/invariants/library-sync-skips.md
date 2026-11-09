# Invariants: Library sync: skips and stamps

When a platform or collection is skipped, and what its completion stamp records. The run itself is in
[library-sync](library-sync.md). How an entry reads and binds, and the other areas: [the register's index](index.md).

- **Every reader that decides or predicts a platform skip — the skip gate, the plan estimate and the preview's
  `restamp_platform_count` — reads the stamp through `domain/platform_sync_state.py::stamp_for_skip`, which answers a
  revoked stamp with none** — test + prompt-only

  The resume offer's `PlatformSyncStateRepository.has_any` applies the same rule in its query; removed-game discovery,
  the prune canaries, `reachable_count` and the recovery snapshot read the stamp raw; the end-of-run stale removal
  revokes a skip only on a platform outside `processed_platform_slugs`

  The helper by `tests/domain/test_platform_sync_state.py::TestStampForSkip`; each skip-side reader by tests over a
  revoked stamp (`tests/services/library/test_fetcher.py`'s
  `TestIncrementalSkipFromPlatformStamp::test_a_revoked_stamp_never_skips` and
  `TestPlanEstimates::test_a_revoked_stamp_predicts_no_skip_and_no_collapsed_count` for the gate and the estimate,
  `test_sync_orchestrator.py::TestPreviewRestampPlatformCount::test_a_platform_whose_skip_was_revoked_is_counted` for
  the re-stamp count,
  `test_reporter.py::TestGetSyncStatsResumeInputs::test_a_platform_stamp_whose_skip_was_revoked_is_no_resume` and
  `tests/adapters/repositories/test_platform_sync_state.py::TestHasAny::test_a_revoked_stamp_is_not_counted` for the
  resume offer); each raw reader but the recovery snapshot by one test over a revoked stamp that still tells the rows
  the stamp's fetch returned from the rows it did not
  (`tests/services/prune/test_preview.py::test_a_platform_whose_skip_was_revoked_still_discovers_its_dropped_rows`,
  `tests/services/prune/test_registry.py::TestCanaryRomIds::test_a_revoked_skip_still_offers_what_the_fetch_returned`,
  `tests/contract/test_prune.py::test_preview_discovers_on_a_platform_whose_skip_was_revoked` over real SQLite, and
  `test_reporter.py::TestRegistryPlatformsReachableCount::test_a_revoked_stamp_still_rules_out_what_its_fetch_did_not_return`);
  the stale removal by `test_reporter.py::TestFinalizePerUnitRun` and end to end by
  `test_sync_orchestrator.py::TestPlatformTurnedOffAndBackOn`. Why the stamp is kept and why a processed platform keeps
  its skip are in [Backend Architecture](../backend-architecture.md#libraryservice-decomposition-serviceslibrary),
  "Incremental skip"; do not restate them here. **Each wrong turn is silent.** A skip gate that read the stamp raw would
  let a platform skip over games it unbound while it was turned off: the preview says "Everything is up to date." and
  those games never get their shortcuts back. A raw re-stamp count offers no Apply to an enabled platform with a revoked
  stamp and an empty delta, so it is never re-stamped and full-fetches on every sync. A raw plan estimate can price such
  a platform as a skip, so the run's progress estimate runs short while the platform full-fetches; a raw `has_any` can
  offer "Resume Sync" on the strength of stamps that save the next run nothing. A raw reader moved behind the helper
  blinds removed-game discovery, the canaries a 404 round asks first, or the reachable count on every platform a removal
  touched, and for a platform whose sync stays off it stays blind, since no new complete fetch comes. A stale removal
  that revoked on a processed platform would take the skip from the stamp that same run just wrote; there it removes
  only the shortcut of a game the user hid in RomM — a skipped platform rebuilds every other bound row, and a row
  missing from a platform the run read is kept — which the next skip leaves out of its rebuild as well and whose unhide
  changes the checksum, so the cost would land with the first rule that removes anything else; one that skipped an
  unprocessed platform brings the turned-off-and-back-on gap back. **What nothing checks**: a new skip-side reader
  calling `platform_sync_state.get` raw passes every test above, because each test pins one existing call site; the
  recovery snapshot's raw read has no test, so moving it behind the helper would drop a revoked stamp from a sealed
  bundle unnoticed; and `has_any`'s `WHERE skip_revoked = 0` and `stamp_for_skip` state the one rule twice — each is
  pinned alone and nothing holds them equal. Prompt-only: a new skip-side reader calling `platform_sync_state.get` raw,
  the recovery snapshot's raw read (no test pins it), and `has_any`'s query agreeing with `stamp_for_skip` — the query
  and the helper are each pinned alone and nothing joins them
- **A platform's completion stamp records what the skip compares RomM's answer with, and the gate and the preview's
  re-stamp count read it through one comparison** — test + prompt-only

  The checksum is over exactly the ids of the unit's full fetch (`ChunkDispatcher._build_final_platform_stamp`, over
  every chunk's ROMs), `fetch_started_at` is Tender's clock read before the skip request (`UnitApplier`), the ten-minute
  margin is applied when the stamp is read and never stored (`domain/platform_skip.py::skip_reference`), and the gate's
  `probe_verdict` and the count's `restamp_due` both decide through `domain/platform_skip.py::_compare`

  The checksum by
  `tests/services/library/test_sync_orchestrator.py::TestOnlyAFreshStampRestoresTheSkip::test_a_hidden_rom_with_a_shortcut_is_fetched_in_full_once_then_skipped`,
  whose second apply has no shortcut to change and still stamps the ids its fetch returned, leaving out the ROM an admin
  hid, which stays bound; the stored fetch start by that class's
  `test_a_rom_edited_while_the_run_applied_is_fetched_in_full_next_sync` (the fetch's start, unmargined, not the run's
  end, and an edit between the two is seen next sync) and
  `test_a_stamp_without_a_checksum_is_fetched_in_full_once_then_skipped`; a unit applied in more than one chunk by
  `tests/services/library/test_chunk_dispatcher.py::TestApplyChunking::test_the_final_chunk_s_stamp_checksums_every_chunk_s_ids`;
  the margin by `tests/domain/test_platform_skip.py::TestSkipReference` and, through the gate,
  `tests/services/library/test_fetcher.py::TestIncrementalSkipFromPlatformStamp::test_the_reference_is_the_fetch_start_less_the_margin`.
  The rule spans three modules, each holding one part: the dispatcher writes the checksum at the final chunk, the unit
  applier reads the clock and hands it down, and `domain/platform_skip.py` holds the margin and the comparison. Why the
  stamp records a fetch start and a checksum rather than a completion time and a count, and what the margin covers, are
  in [Backend Architecture](../backend-architecture.md#libraryservice-decomposition-serviceslibrary), "Incremental
  skip"; do not restate them here. **Each wrong turn is silent.** A checksum over anything but the full fetch's ids —
  the final chunk's ROMs, or the bound rows the skip rebuilds from — never equals RomM's id index again, so the platform
  is fetched in full and offered the re-stamp Apply on every sync. A clock read later than the walk's start records a
  start after ROMs the walk has already read; once that gap is longer than the margin, an edit RomM makes to such a ROM
  inside it is older than the next stamp's reference, and the next skip misses it. A margin stored in the stamp is fixed
  into every row: a later change to it leaves the stamps already written at the old one, and a reader that subtracts it
  as well applies it twice. Two deciders that stop sharing one comparison disagree about a stamp: the preview offers an
  Apply that leaves the next sync fetching in full, or offers none to a platform the gate will keep refusing. **What
  nothing checks**: the test clock does not move across the skip request, so a clock read after it passes every test
  above; and `TestProbeVerdict` and `TestRestampDue` in `tests/domain/test_platform_skip.py` pin each decider alone, so
  nothing holds them to one comparison. Prompt-only: the clock read before the skip request rather than after it (the
  test clock does not move across the request), and the two deciders sharing `_compare` — `TestProbeVerdict` and
  `TestRestampDue` in `tests/domain/test_platform_skip.py` pin each alone and nothing joins them
- **A collection's completion stamp records what the collection skip compares RomM's answers with, and the preview's
  re-stamp count is that skip's own refusal** — test + prompt-only

  The checksum is over exactly the `rom_ids` the collection listing carried less the run's hidden set
  (`WorkUnit.member_rom_ids`, in `ChunkDispatcher._build_final_collection_stamp`), never the stamp's `member_rom_ids`;
  `fetch_started_at` is Tender's clock read before the unit's own skip check, for a collection unit as for a platform
  one (`CollectionFetcher.fetch_collection_units`, which fetches every collection unit before the first applies); the
  margin is the platform skip's, applied when the stamp is read (`domain/platform_skip.py::skip_reference`); no
  `rom_count` is compared; and a collection is counted in `restamp_collection_count` exactly when the preview's gate
  refused it for a reason a fresh stamp settles (`domain/collection_skip.py::CollectionSkip.restamp_settles`, never for
  a probe that raised or could not be read) and the apply will stamp it — it has members, and `recorded_updated_at`
  answers, which `domain/collection_skip.py::apply_stamps` decides for the count and for the final chunk's stamp alike

  The fetch start by
  `tests/services/library/test_sync_orchestrator.py::TestOnlyAFreshCollectionStampRestoresTheSkip::test_a_member_edited_while_the_run_applied_is_fetched_in_full_next_sync`
  (the stamp stores the fetch's start, not the run's end: an edit between the two is seen next sync) and
  `tests/services/library/test_collection_fetcher.py::TestTryCollectionIncrementalSkip::test_the_probe_s_reference_is_the_fetch_start_less_the_margin`,
  and that each unit's start is read before its own probe by
  `tests/services/library/test_collection_fetcher.py::TestFetchingEveryCollectionUnit::test_each_unit_s_fetch_start_is_the_clock_read_before_its_own_skip_check`
  (a clock each request moves); the listing's checksum by
  `tests/services/library/test_chunk_dispatcher.py::TestFinalChunkCollectionStamp::test_the_final_chunk_s_stamp_records_the_fetch_start_and_the_listing_s_checksum`,
  over a unit applied in more than one chunk, and end to end by
  `TestOnlyAFreshCollectionStampRestoresTheSkip::test_an_own_smart_collection_whose_walk_returns_more_than_its_listing_is_skipped_once_stamped`;
  no count by `TestTryCollectionIncrementalSkip::test_the_listing_s_rom_count_is_not_compared`; the count by
  `TestOnlyAFreshCollectionStampRestoresTheSkip::test_a_stamp_without_a_fetch_start_is_fetched_in_full_once_then_skipped`
  (counted, re-stamped by the Apply, then skipped and no longer counted) and
  `::test_an_empty_collection_and_one_without_updated_at_are_not_counted`; a refusal no fresh stamp settles left out by
  `::test_a_collection_whose_probe_failed_is_fetched_in_full_and_not_counted` and, per refusal,
  `tests/services/library/test_collection_fetcher.py::TestWhetherAFreshStampSettlesTheRefusal` and
  `tests/domain/test_collection_skip.py::TestWhetherAFreshStampSettlesTheRefusal`; and that a stamp the apply writes
  matches the listing it was written from by
  `TestFinalChunkCollectionStamp::test_the_stamp_it_writes_matches_the_listing_it_was_written_from`, and that the writer
  refuses what `apply_stamps` refuses by `::test_a_collection_handed_no_members_is_not_stamped`. That the members are
  the listing's ids less the run's hidden set ([#2502](https://github.com/danielcopper/romm-tender/issues/2502) D4) is
  pinned by
  `tests/services/library/test_fetcher.py::TestTheHiddenSet::test_a_collection_unit_s_members_are_its_listing_less_the_hidden_ones`,
  and end to end by
  `tests/services/library/test_sync_orchestrator.py::TestAGameTheUserHidInRomm::test_a_game_hidden_before_a_stopped_run_goes_on_the_next_run_that_skips_its_unit`:
  a collection stamped over the full listing would replay a hidden member on every skip, and a hide or an unhide would
  never fetch it. The rule spans five modules: the collection fetcher reads the clock and judges, the preview counts,
  the dispatcher writes the stamp, and `domain/collection_skip.py` with `domain/platform_skip.py` hold the comparisons
  and the margin. Why the checksum covers the listing rather than the visible id set the platform's covers is in
  [Backend Architecture](../backend-architecture.md#libraryservice-decomposition-serviceslibrary), "Incremental skip";
  do not restate it here. **Each wrong turn is silent.** A checksum over the stamp's `member_rom_ids` holds the ids a
  walk answered beyond the listing, so an own smart collection whose walk answers more never matches and is fetched in
  full, and offered the re-stamp Apply, on every sync. A collection unit whose fetch start is not read records none, so
  its stamp never skips. A count that judges the stamp a second time instead of reading the gate's answer can disagree
  with it: the preview offers an Apply that leaves the next sync fetching in full, or none to a collection the gate
  keeps refusing. A count of every refusal offers an Apply for a probe that raised or could not be read, which a fresh
  stamp does not settle, so a probe that keeps failing offers it on every sync. A count and a stamp writer that stop
  sharing `apply_stamps` disagree in one of two directions: a writer looser than the count stamps a collection the count
  never asked for, and a writer stricter than the count leaves the Apply offered with nothing written, so the offer
  comes back on every sync. **What nothing checks**: the count reading the gate's `restamp_settles` answer from the
  preview's own fetch rather than judging the stamp a second time — nothing but review keeps the count on the gate's own
  answer. Prompt-only: the count reading the gate's `restamp_settles` answer from the preview's own fetch rather than
  judging the stamp a second time
