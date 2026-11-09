# Invariants: Library sync: representatives and removals

Which member of a sibling group a shortcut stands for, the end-of-run stale removal and the convergence beside it, and
what the user hid in RomM. The run itself is in [library-sync](library-sync.md). How an entry reads and binds, and the
other areas: [the register's index](index.md).

- **Where the library sync resolves a representative, neither RomM's main-sibling leg nor the ranking picks a member
  RomM holds no file for (`has_file_on_disk` false) while one with a file remains; the sync's installed and
  existing-binding legs keep such a member** — test + prompt-only

  A bound member without a file that is not installed is rebound only in a complete view, only onto a sibling with a
  file, no shortcut of its own and an equal non-empty `fs_name_no_ext`; and a bound member gone from RomM is rebound
  only by that same rule, installed or not, its stem read off its stored `fs_name`, and otherwise keeps its binding —
  each of these unless its group holds another bound shortcut, where `domain/sync_diff.py::converge_bound_groups`
  decides which binding stays

  `tests/domain/test_sibling_resolution.py::TestMembersWithoutAFile` for the chain's legs,
  `tests/domain/test_sync_diff.py::TestCollapseMembersWithoutAFile` for the sync's collapse (the name, shortcut and
  partial-view cases among it); `tests/domain/test_sync_diff.py::TestCollapseSiblingGroups` holds the bound member gone
  from RomM (kept beside siblings under other names, moved onto the same dump whether or not it is installed), and
  `tests/contract/test_vanished_version_same_dump.py` the move end to end. Why such a member stays in some legs and is
  left out of others, and why a rebind needs an equal `fs_name_no_ext`, are in
  [Steam Non-Steam Shortcuts](../steam-non-steam-shortcuts.md#a-bound-rom-romm-holds-no-file-for) and
  [Backend Architecture](../backend-architecture.md#libraryservice-decomposition-serviceslibrary), "Group-aware sync";
  do not restate them here. The rule lives in two places: `domain/sibling_resolution.py::resolve_group_representative`
  applies it to the legs through `fileless_rom_ids`, and `domain/sync_diff.py::collapse_sibling_groups` passes it and
  rebinds through `_same_dump_with_file`. The version picker resolves no representative: its Default badge is RomM's
  default-version flag alone, on a version without a file too (`services/version_switch.py`). The removed-game cleanup
  picks no representative at all: it never moves a shortcut onto another version. **What breaks without the rule:** a
  chain that ranks a member without a file like any other binds a new shortcut to a row whose download is a 404 — after
  a rescan turns a `.zip` into a folder the two rows tie and the older one wins on `rom_id` — and a new caller that
  forgets `fileless_rom_ids` brings that back silently. Nothing mechanical sees it. A RomM payload's flag is read only
  by `domain/rom_file.py::has_file_on_romm`, which reads only an explicit `false` as no file; the stored
  `Rom.has_file_on_disk` is written only from its answer, and is read directly by the version picker, the game-detail
  payload and the rows a skipped unit rebuilds from the database. A bound member gone from RomM moves only onto the same
  dump because any other sibling is a version the user did not choose — another region, language or revision — and a
  sync that moved it there would say nothing; the game page says the version is gone instead, and the version picker is
  the way to switch. **What breaks without it:** a sync moves a shortcut onto another version, and a downloaded copy of
  the old one is left unbound beside it, still playable and no longer started by anything. Prompt-only: a new caller of
  `resolve_group_representative` passes `fileless_rom_ids`; the flag is read off a RomM payload only through
  `domain/rom_file.py::has_file_on_romm`, and the stored `Rom.has_file_on_disk` is written only from its answer
- **The end-of-run stale removal removes the shortcut only of a game outside what the sync syncs, or of one the user hid
  in RomM: a stale row in the run's hidden set whose game the run did not sync is removed unasked on any route and its
  mark cleared (#2502 D1)** — test + prompt-only

  A bound row missing from a platform the run read whole is kept; on a platform the run did not read, a row whose game
  the run synced — another member of its sibling group among the ROMs the run counts as synced, a skipped unit's stamp
  members included — stays bound whatever RomM answers, and is asked about and marked only where any other row would be,
  while a row without a group key is judged alone (#2533 D6); any other row a collection last delivered
  (`roms.delivered_through`) is asked for by its exact id on every run until it is found or unbound, and kept unless
  RomM's answer is the ROM itself, while any other row is kept when the run's own platform listing no longer names its
  platform and removed unasked when it does; a row with no recorded route is asked only when the collections' last
  completed members list it; a kept row stays in its platform's Steam collection, and so does a bound row a collection's
  skip rebuilt on a platform the run read whole without returning it (#2549 D1), and, with platform groups on, every
  other row the run leaves bound, a row RomM did not confirm included (#2490 D2, D3); the preview counts exactly the
  removals and the platform collections the apply makes; and of the two, only the apply records what the stale removal
  established on the row (`roms.gone_from_romm`): a 404 or a keep on a platform RomM no longer lists marks it, the ROM
  itself clears the mark, and any other answer writes nothing. Beside it, a group holding more than one bound shortcut
  converges (#2645): on a platform unit the run fetched in full, every bound row of such a group but the one
  `domain/sync_diff.py::converge_bound_groups` keeps is removed on a run that was not stopped — never sorted as stale,
  never over an appId the run bound, and never while Steam lists one of the group's shortcuts as running, read at the
  platform unit and again before the removal — with the preview counting the same removals unless a game starts or quits
  between the preview and the removal (#2645 D5, D6), and no platform whose rows still hold such a group is skipped; on
  a platform the sync leaves off, the run's collection deliveries converge a group they bring in the same way (#2661),
  once for the run, over the bound rows they deliver and the bound rows of the same stored key a delivered member lists
  in `sibling_roms` (`domain/sync_diff.py::brought_in_by_delivery`), a row no collection delivered ranked by its stored
  version fields and the sibling list's default, while the convergence leaves a bound row no delivered member lists to
  the stale sort, and no collection holding a member of such a group is skipped; a bound version RomM joined to the game
  after Tender last read it, its stored key still its old game's, is not brought in and goes the stale way (#2661 D5);
  in a group no listed version holds, a bound row the user hid takes no part in either convergence (#2502 D1), and in a
  group a listed version holds it ranks as one RomM no longer lists in both (D8). A row whose shortcut either removes
  keeps its binding until the panel reports the shortcut gone from Steam, the finalize settles the row's stamps whatever
  its binding by then, and `report_sync_removals` unbinds it only while it still holds the reported appId; a removal
  never reported is emitted and counted by the preview again while the shortcut is in Steam, and unbound by the next
  sync's start reconcile where it is gone (#2653 D1). The removed-game cleanup's own proof marks and clears rows too, by
  its rule in docs/architecture/removed-game-cleanup.md, "Discovery"

  `tests/domain/test_sync_diff.py::TestSortStaleRows` for the rule (`sort_stale_rows`, `settle_asked_rows`),
  `tests/domain/test_sync_diff.py::TestAHiddenRowInTheStaleSort` for a row the user hid,
  `tests/domain/test_fetch_generation.py::TestHiddenWithoutAListedVersion` for the platform skip's rebuild that leaves
  such a row stale, `TestConvergeBoundGroups`' hidden cases and
  `tests/services/library/test_sync_orchestrator.py::TestAGameACollectionBringsInConverges::test_a_version_the_user_hid_ranks_as_one_romm_no_longer_lists`
  for the convergence, and `tests/services/library/test_sync_orchestrator.py::TestAGameTheUserHidInRomm` for every route
  end to end, preview included,
  `tests/services/library/test_sync_orchestrator.py::TestAVersionReachedOnlyThroughACollection` for the apply and the
  preview (a 404 keeps, a found ROM removes, an answer that establishes nothing keeps; a 404 is asked again on the third
  run, a stopped run leaves the row asked at the next, a row its platform last delivered is removed unasked, and a row
  with no recorded route is asked only when a collection record lists it),
  `TestDoSyncPerUnit::test_a_game_whose_every_version_is_gone_keeps_its_shortcut` and `TestPlatformTurnedOffAndBackOn`
  for the two ends — a game gone from RomM keeps its shortcut, a platform turned off loses its games, and
  `test_a_platform_still_on_that_romm_no_longer_lists_keeps_its_shortcuts` holds a platform gone from RomM's list apart
  from one turned off, `test_a_platform_romm_no_longer_lists_keeps_its_steam_collection` keeps its games in its Steam
  collection under its last name with the preview agreeing, and `test_a_platform_read_mid_run_still_counts_as_unlisted`
  judges it by the run's own listing after a game page's read wrote it back into the kept ids —
  `TestAVersionSwitchedOffTheCollectionsMember` for a game only a collection delivers, bound to a version the collection
  does not list (kept whether the collection was skipped or fetched, or delivers a version for the first time; asked,
  marked on a 404 and cleared when RomM returns it where a collection last delivered it; the preview agreeing each time;
  a bound version of another game, which no unit delivered, still removed; and the gap #2533 D10 accepts, pinned as it
  is), and `tests/services/library/test_reporter.py::TestCommitUnitResults` for the route a commit records. The stale
  removal judges the game rather than the row, because a sibling group is one game with one shortcut: a version switch
  binds that shortcut to a member no unit delivers — and records no route on it — while a collection still delivers the
  game through another version (#2533 D1). Such a row is still asked about where any other row would be, because its
  mark is the removed-game cleanup's only evidence for it (D6). The group keys come from
  `domain/sync_diff.py::group_keys_of` over the ROMs the run counts as synced, which the apply reads off the rows its
  commits upserted and the preview lays the keys its fetch stamped over the same read. A skipped platform rebuilds every
  bound row but a hidden one whose game it holds no version of, which the hidden rule removes, so only a fetched one can
  leave any other bound row out, and RomM is then the only thing that dropped it. A platform the run did not read says
  nothing about RomM; the route the row's last commit recorded tells a game reached through a collection from one
  reached through its platform. A collection's completion stamp cannot carry that, because every collection apply
  rewrites its members from RomM's current membership: a game the collection dropped is off the stamp after the run that
  asked about it, and a route recorded only at a unit's commit is untouched by the question, so a 404 is asked again on
  every later run. A row with no recorded route falls back to the collection records, read before the run's first unit
  rewrites any of them (`LocalLibraryReader.do_read_collection_listed_rom_ids`). A platform RomM no longer lists is read
  off the run's own platform listing (`LibrarySyncStateBox.run_listed_platform_slugs`), never off the kept ids under
  `platform_ids`, which a game page's read can widen during the run; which platforms are on in Tender is keyed by a RomM
  id such a platform no longer has, so the route stands in for it. Only a 404 is evidence of absence, so every other
  answer keeps the shortcut. The apply's finalize writes what it established onto the row
  (`StaleSort.gone_from_romm_marks`, written by `RunFinalizer.finalize_per_unit_run`), which is the only evidence the
  removed-game cleanup has for a version reached through a collection or kept on a platform RomM no longer lists, since
  no platform stamp names either; `tests/domain/test_sync_diff.py::TestGoneFromRommMarks` pins which answer writes what,
  and `tests/contract/test_gone_from_romm_mark.py` that the apply marks, a later delivery clears, and a preview writes
  nothing. A bound row a collection's skip rebuilt is counted as synced, so the sort never sees it; where its platform
  was read whole without returning it, `domain/sync_diff.py::rebuilt_rows_filed_as_kept` files it as a kept row, which
  is where the sort would put it had the collection been fetched instead;
  `tests/domain/test_sync_diff.py::TestRebuiltRowsFiledAsKept` pins the rule and
  `tests/services/library/test_sync_orchestrator.py::TestAHiddenVersionKeepsItsPlatformCollectionWhetherItsCollectionIsSkippedOrFetched`
  the preview and the apply agreeing, skipped or fetched, a platform the run did not read, and a stopped run, and
  `tests/services/library/test_sync_orchestrator.py::TestThePreviewNamesAPlatformCollectionAsTheApplyDoes` the two
  naming a platform collection alike where a platform's slug and name differ, a rebuilt row's and a platform only
  collections reach alike. A row RomM was asked about and did not return stays bound, and so does one whose game the run
  synced on a platform it did not read, so the apply's end-of-run scan, which files every bound row through
  `domain/sync_diff.py::platform_collection_name`, files either where platform groups are on. No emitted entry carries
  such a row, so the preview passes `compute_platform_collection_diff` every stale row the stale removal neither removes
  nor keeps (`left_bound_rows`), and the diff files each through the same function (#2490 D3).
  `tests/services/library/test_sync_orchestrator.py::TestThePreviewFilesAGameRommDidNotConfirmAsTheApplyDoes` pins the
  two agreeing for a 404 and for an answer that establishes nothing, with platform groups on and off, on a platform RomM
  still lists and on one it no longer lists, and
  `tests/domain/test_sync_diff.py::TestComputePlatformCollectionDiff::test_a_row_left_bound_is_filed_by_the_setting_alone`
  the diff's rule;
  `tests/services/library/test_sync_orchestrator.py::TestThePreviewFilesAGameTheRunSyncedAsTheApplyDoes` pins the two
  agreeing for a row whose game the run synced on a platform it did not read, the only bound row on that platform once a
  version switch moved the shortcut onto it, whether RomM is asked about it or not, the collection skipped or fetched,
  with platform groups on and off. The convergence keeps a game whose bindings the stale sort would keep one by one from
  keeping two shortcuts for good (a game on a platform RomM no longer lists keeps them): on a platform a unit reads it
  judges a group over the unit's whole listing and its platform's bound rows, the rows the listing leaves out included,
  which is why only a fetched platform unit converges there, why the gate refuses the skip on the stored keys of every
  bound row whatever its generation (`domain/fetch_generation.py::convergence_pending`, over the same
  `groups_to_converge`), and why the preview converges over the listings it fetched in full rather than over its union,
  where a skipped collection's rebuilt row would read as listed. On a platform the sync leaves off, the run's collection
  deliveries converge a group they bring in (#2661 D1): over the bound rows they deliver and the bound rows of the same
  stored key a delivered member lists in `sibling_roms` (`domain/sync_diff.py::brought_in_by_delivery`), decided once
  for the run after every collection unit is fetched, the rows no collection delivered ranked by their stored version
  fields (one the user hid ranks as unlisted, #2502 D8) and the sibling list's default (`stored_version`, D4); a bound
  row of the group no delivered member lists is not converged, so a stored key alone never converges one away — it keeps
  its shortcut while a ROM the run synced carries its stored key, and goes the stale way where the delivered version is
  the one regrouped. An unchanged collection holding a member of such a group is fetched rather than skipped
  (`domain/collection_skip.py::holds_a_game_to_converge`) — on every sync while the version no collection delivers is
  the one RomM no longer lists as a sibling (D3; where the delivered version is the one regrouped, the skip returns
  after one fetch: Backend Architecture, "Convergence") — and a bound version RomM joined to the game after Tender last
  read it, its stored key still its old game's, is not brought in and goes the stale way on a platform the sync leaves
  off (D5); the preview converges over the ROMs its collection units fetched, never a skipped collection's rebuilt rows;
  `tests/domain/test_sync_diff.py::TestBroughtInByDelivery`, `TestStoredVersion` and
  `tests/domain/test_collection_skip.py::TestHoldsAGameToConverge` pin those rules,
  `tests/services/library/test_collection_fetcher.py::TestAGameToConvergeRefusesTheSkip` the gate, and
  `tests/services/library/test_sync_orchestrator.py::TestAGameACollectionBringsInConverges` the apply and the preview
  agreeing whichever version a collection holds, for a game split over two collections, an installed version no
  collection delivered, a version RomM no longer lists as a sibling, a skipped collection's row ranked by its stored
  version, a version RomM joined that no collection brings in, a version left behind where RomM regrouped the delivered
  one, with the skip back a sync later, a platform RomM no longer lists, a running game and a stopped run;
  `tests/domain/test_sync_diff.py::TestConvergeBoundGroups` and `TestCollapseHandedTheConvergedRows` pin the rule and
  the collapse's side of it, `TestAGroupWithARunningGameIsLeftOut` and `TestWithoutRunningGroups` a group left out while
  it runs, at the unit and at the finalize,
  `tests/services/library/test_sync_orchestrator.py::TestAGameWithSeveralShortcutsConverges` the apply and the preview
  agreeing for every install state, an unlisted version and a game RomM lists no version of, the stopped run, the #1036
  guard, the row the kept shortcut moves onto and a running game left for the next sync,
  `TestAGameASkippedCollectionStillListsConvergesAsThePreviewCounts` the preview's listing, and
  `tests/services/library/test_fetcher.py::TestAGroupWithSeveralShortcutsIsFetchedInFull` the gate and the plan's
  prediction; that a new collapse caller on a complete view hands the collapse the losers (`converged=`) is prompt-only.
  Neither removal unbinds its row in the finalize (#2653 D1): the panel removes the shortcut later, and a removal it
  does not finish — cut short by the next run's start, sent to no panel, or not made by Steam — would otherwise leave a
  live shortcut no row is bound to, which no later sync finds. So the row stays bound until the panel reports the
  shortcut gone (`ShortcutRemovalService.report_sync_removals`, compare-and-unbind, so a late report never unbinds a row
  a later run bound again), the next run's stale removal or convergence emits it again while the shortcut is in Steam,
  and the next sync's start reconcile unbinds it where the shortcut is gone and only the report was lost; the preview,
  which runs after that reconcile, counts what the apply then does. The finalize's collection maps, name cache and
  terminal count treat the row as unbound already, and the finalize settles the row's stamps whatever its binding, since
  the report can land before it. `TestARemovalThePanelDidNotReport` in
  `tests/services/library/test_sync_orchestrator.py` pins both ways for a stale and a converged removal, preview
  included, `TestAReportThatLandsBeforeTheFinalize` there a platform and a collection turned off and back on with the
  report landing first, `tests/services/test_shortcut_removal.py::TestReportSyncRemovals` the report, and
  `frontend/src/utils/syncRemovalReport.test.ts`, `frontend/src/utils/steamShortcuts.test.ts` ("shortcutsGoneFromSteam")
  and `frontend/src/index.test.tsx`'s `sync_stale` listener the panel's side; that the panel reports from the tail's
  `finally`, and only what Steam no longer lists, is prompt-only. The rule spans the domain sort, the reader, the
  reporter's commit, the chunk dispatcher, the fetcher's exact-id question (`LibraryFetcher.ask_romm_for_roms`), the
  orchestrator and the preview, and the preview and the apply each read it — through
  `services/library/_stale_rows.py::settle_stale_rows` with the group keys `domain/sync_diff.py::group_keys_of` reads
  for the run's synced ROMs, which is prompt-only, as is that the chunk dispatcher names the unit's kind on every
  commit; that the collection records are read before the run's first unit (`do_read_collection_listed_rom_ids`); that
  the platform listing is the run's own (`LibrarySyncStateBox.run_listed_platform_slugs`) and never the kept ids; that
  the preview names every platform collection its diff counts by the name the names
  `services/library/_stale_rows.py::with_kept_platform_names` answers hold for its slug, else the slug — an emitted
  entry's and a row left bound's through `domain/sync_diff.py::platform_collection_name`, a kept row's by the same
  lookup in `compute_platform_collection_diff` — which must agree with `RunFinalizer._names_with_bound_platforms_kept`;
  that the preview hands that diff every stale row `settle_stale_rows` neither removes nor keeps; that both pick the
  rebuilt rows they file as kept through `domain/sync_diff.py::rebuilt_rows_filed_as_kept`; and that both converge a
  game a collection brings in through `services/library/_convergence.py::converge_collection_deliveries`, over the ROMs
  the collection units fetched and never a skipped collection's rebuilt rows. **What breaks without it:** a kept row the
  preview counts as a removal offers Apply on every preview; a game only a collection delivers loses its shortcut on
  every sync after a switch to a version the collection does not list; a kept version never asked about keeps a mark
  RomM disproved, or never gets the one the cleanup needs; a removal on a platform read whole takes a game gone from
  RomM out of Steam with its playtime and collections; a rule read off the collection stamps alone removes a game gone
  from RomM at the second run after its collection dropped it; a platform RomM stopped listing loses every shortcut as
  if it had been turned off; a kept row left out of the platform's collection drops out of it, or keeps its game under a
  collection renamed to the slug; a game hidden in RomM leaves its platform's collection whenever the collection that
  lists it is skipped; a preview that names a platform collection by an entry's own `platform_name` counts one added —
  the slug beside the name — that the apply never makes, and offers Apply on every preview; with platform groups on, a
  preview that files only the kept rows counts as removed the platform collection of a game RomM did not confirm, which
  the apply keeps, and offers Apply on every preview; and a mark a preview wrote, or an answer that established nothing
  set or cleared, would offer the cleanup a game RomM still serves or hide one it no longer does; a game a collection
  brings in keeps every shortcut it holds, for good; and a row unbound before its shortcut was gone leaves that shortcut
  in Steam with no row, for good; a game the user hid keeps its shortcut on a platform read whole while the cleanup
  offers it on every scan and refuses it, and through a standard collection goes only once that collection is fetched
  for another reason. Prompt-only: the panel reports from the `sync_stale` tail's `finally`, and only what Steam no
  longer lists; a new collapse caller on a complete view hands the collapse the convergence's losers (`converged=`); the
  collection records are read before the run's first unit (`do_read_collection_listed_rom_ids`); the platform listing is
  the run's own (`LibrarySyncStateBox.run_listed_platform_slugs`) and never the kept ids; the chunk dispatcher names the
  unit's kind on every commit; the preview and the apply both sort through
  `services/library/_stale_rows.py::settle_stale_rows` with the group keys `domain/sync_diff.py::group_keys_of` reads
  for the run's synced ROMs; the preview names every platform collection its diff counts by the name the names
  `services/library/_stale_rows.py::with_kept_platform_names` answers hold for its slug, else the slug — an emitted
  entry's and a row left bound's through `domain/sync_diff.py::platform_collection_name`, a kept row's by the same
  lookup in `compute_platform_collection_diff` — which must agree with `RunFinalizer._names_with_bound_platforms_kept`;
  the preview hands that diff every stale row `settle_stale_rows` neither removes nor keeps; the preview and the apply
  both pick the rebuilt rows they file as kept through `domain/sync_diff.py::rebuilt_rows_filed_as_kept`; and both
  converge a game a collection brings in through `services/library/_convergence.py::converge_collection_deliveries`,
  over the ROMs the collection units fetched and never a skipped collection's rebuilt rows
- **What the user hid in RomM is read off one request, `GET /api/roms?statuses=hidden`
  (`RommApiAdapter.list_hidden_roms`), read only through `RommApiAdapter.read_hidden_rom_ids`, which parses it with
  `domain/hidden_roms.py::hidden_rom_ids` and logs an answer it cannot read** — test + prompt-only

  And asked once per sync's work queue, once per version-picker open and once per cleanup scan, whose start reuses the
  scan's set; an answer that cannot be read — one whose first item is not marked hidden included, as from a RomM that
  ignored `statuses` — takes nothing for hidden: no removal, no mark, nothing left out of the cleanup; and a ROM an
  admin hid is told apart from a deleted one nowhere

  The parse by `tests/domain/test_hidden_roms.py`, the request by `tests/adapters/romm/test_romm_api.py`
  (`TestListHiddenRoms`, `TestReadHiddenRomIds`), the work queue asking once and an unreadable answer by
  `tests/services/library/test_fetcher.py::TestTheHiddenSet` and, end to end,
  `tests/services/library/test_sync_orchestrator.py::TestAGameTheUserHidInRomm::test_a_hidden_list_that_cannot_be_read_removes_nothing`
  with `::test_a_removal_the_sync_makes_for_another_reason_stands_beside_an_unread_hidden_list`, the picker by
  `tests/services/test_version_switch.py::TestAVersionTheUserHidInRomm`, the cleanup and its start by
  `tests/services/prune/test_service.py::TestAGameTheUserHidInRomm`, and the fake telling the two kinds apart by
  `tests/fakes/test_fake_romm_api.py::TestTheTwoKindsOfHide`. RomM's two kinds of hidden, and why only the user's own
  can be read, are under
  [Backend Architecture](../backend-architecture.md#libraryservice-decomposition-serviceslibrary), "A game the user hid
  in RomM". One request answers every id of the user's own kind
  ([#2502](https://github.com/danielcopper/romm-tender/issues/2502) D2), and the sync, the picker and the cleanup each
  read that one answer (D5), so the three agree on what is hidden. **What breaks without it:** an answer that could not
  be read, taken for a set anyway, removes shortcuts on a guess; `rom_user.hidden` read off the exact-id answers the
  sync already gets is blind on the platform route and on a skipped collection, so the same hide would remove the game
  on one route and keep it on another; a picker or a cleanup reading another source disagrees with the sync about the
  same version. **What nothing checks**: that no reader takes `rom_user.hidden` off an exact-id answer or a list item
  for what is hidden (`hidden_rom_ids` reads the first item's only to tell which list came back); that within a run the
  set is read only off `LibrarySyncStateBox.run_hidden_rom_ids`; that no module but `adapters/romm/romm_api.py` calls
  `list_hidden_roms`, and that a new reader of what is hidden goes through `read_hidden_rom_ids`; and the guard's blind
  spot: a RomM that ignored `statuses` passes it when the first ROM in its order is one the user hid. Prompt-only: no
  reader takes `rom_user.hidden` off an exact-id answer or a list item for what is hidden (`hidden_rom_ids` reads the
  first item's only to tell which list came back); within a run the set is read only off
  `LibrarySyncStateBox.run_hidden_rom_ids`; no module but `adapters/romm/romm_api.py` calls `list_hidden_roms`, and a
  new reader of what is hidden goes through `read_hidden_rom_ids`
