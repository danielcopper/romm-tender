# Invariants: Firmware: answers and verdicts

What a firmware answer says and how it is shown: unknown against not needed, a row's verdict, the BIOS colour, one-of
groups, the emulator a set of answers is about, and when it is asked. Files, links and deletes are in
[firmware-files](firmware-files.md). How an entry reads and binds, and the other areas:
[the register's index](index.md).

- **A firmware answer nothing could establish is `unknown`, never `not_needed` — and the distinction survives every
  layer it crosses** — test + prompt-only — `tests/adapters/test_atlas_firmware.py` pins the adapter's degradation (a
  raising resolver, a missing installation, an answer with no root all come back with `resolved` clear, never as an
  empty catalogue reading "nothing needed"), `tests/domain/test_firmware_wants.py` pins the classification, and
  `tests/services/test_firmware.py::TestCheckPlatformBiosUnknown` pins the same listing answering `not_needed` under a
  whole reading and `unknown` under a partial one, and that a launching emulator missing from the answer counts as not
  read, #2190 D4, for its rows as for the level. The rule spans four modules and no diff-scoped review sees it whole:
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
  switching a platform's emulator can move it from a finished answer to a withheld one. A launching emulator the answer
  holds no entry for at all counts as not read too ([#2190](https://github.com/danielcopper/romm-tender/issues/2190)
  D4): no entry is no verdict, so the level that follows is `unknown`, never a green "Nothing required" over the rows of
  other emulators, and `reading_complete_for` is False for it, so a library file nobody declared is `unknown` rather
  than `not_needed`
  (`tests/domain/test_bios_status.py::TestTheLevelIsTheResolversVerdict::test_an_emulator_missing_from_the_answer_counts_as_not_read`,
  `tests/domain/test_firmware_wants.py::TestReadingCompleteFor::test_a_launching_emulator_the_answer_holds_no_entry_for_is_never_complete`,
  `tests/services/test_firmware.py::TestCheckPlatformBiosUnknown::test_a_launching_emulator_missing_from_the_answer_leaves_library_files_unknown`).
  `declaration="packaged"` with an EMPTY requirement list counts as unread and is the shape most likely to be folded
  back the wrong way: a card may identify its image by content, so it names no file until the bytes are read, and
  reading the empty list as "wants nothing" puts a green all-clear on a PlayStation launching DuckStation. A
  `retroarch-foreign-core` entry — a RetroArch launch of a core file this host cannot load, stated with no identity —
  declares nothing and so adds nothing: no placement, no unread name, no verdict and no group
  (`tests/adapters/test_atlas_firmware.py::TestUnreadEmulators::test_a_retroarch_launch_of_a_foreign_core_adds_nothing`).
  It is not the only entry with no identity: any other the resolver could not identify still contributes the files it
  declares, as wants with no owner
- **A firmware row the RomM library does not hold (`on_server: False`) counts in the launching emulator's required
  counts, and never towards a download affordance or a progress ratio** — test + prompt-only —
  `tests/services/test_firmware.py` pins the row's shape (`id` absent, `on_server` clear), that it raises
  `required_count`, and that it stays out of `server_count`; `frontend/src/bigpicture/library/PlatformsTab.test.tsx`
  pins that the buttons key off the fetchable set. **The three axes live in three places and nothing joins them.**
  `domain/bios_status.py::count_required` gives the required counts a sentence words and counts every required row — the
  colour is the resolver's verdict, which weighs that file too, and no count's (the colour entry below);
  `services/firmware/status.py::_bios_aggregates` scopes `server_count` / `local_count` to `on_server` rows, and the
  stated pair `ratio_server_count` / `ratio_local_count` further to those with a verdict; the download buttons'
  condition is `isFetchable` (`frontend/src/utils/biosFetchable.ts`), which reads the row's `library` — the RomM file
  that fills it, by content or, where the resolver's identity is an archive or names no checksum, by name, among the
  platform's own library files (`domain/firmware_rows.py::platform_library`) — its `store_file`, its `link` and its
  `place_refusal`, called from `frontend/src/bigpicture/bios/BiosFiles.tsx`, the BIOS table both the platform detail and
  the game page's BIOS tab draw — and since #1815 the per-row Download button reads the same filtered set, so a fourth
  reader of the axis now exists in that one file. A fifth reads it in the same file for the On-disk cell's second mark
  (`⊘`), and that one is display alone: it neither counts nor gates, which is what keeps it out of all three folds
  below. Both pages list every row of the shown emulator
  ([#2429](https://github.com/danielcopper/romm-tender/issues/2429) D3) and call one predicate, `isFetchable`, through
  one module rather than spelling its clauses twice: a second copy would let one page point at a download button the
  other does not offer. Each fold has its own quiet failure: drop the row from the required counts and a sentence reads
  "all in place" under a red verdict; add it to the ratio and a SNES page reports `0 / 26 files, 26 missing` for
  twenty-six optional files no core wants; add it to the buttons and the page offers a download that cannot succeed. The
  counts and the ratio read `on_server`, the buttons read `library` (`on_server` only on a payload that states no
  `library`); the row's `id: None` is an honest absence with no consumer at all, so nothing breaks if it is filled in
  and nothing is guarded by leaving it empty
- **No BIOS answer outlives the page that asked for it** — test + prompt-only —
  `tests/services/test_game_detail.py::TestGetCachedGameDetailCarriesNoBiosAnswer` and the two contract cases in
  `tests/contract/test_game_detail_read.py`. `get_cached_game_detail` carries none and says so (`bios_status_unknown`,
  plus an unconditional `bios` stale field); the live `get_bios_status` fills it in. What holds the rule is an absence —
  `BiosChecker` has one method, so there is no cheap cached twin to reach for — and an absence is exactly what a future
  change restores without noticing. Re-adding a stored answer would look like a performance win and would put a previous
  page open's requirement on this page
- **A file requirement's verdict and presence are the resolver's wherever the row has a place; Tender's own look at a
  place answers whose link lies there, whether its name is free, whether its folder exists — and, for a file of a folder
  declaration, whether a file lies there** — prompt-only — `domain/bios_status.py::_row_verdict` (the verdict) and
  `services/firmware/rows.py` (presence) are the crossing points. The verdict is the resolver's per-file verdict over
  the launching emulator's own requirement ([#2190](https://github.com/danielcopper/romm-tender/issues/2190) D3): a
  wrong image is there and not met, a file it could not compare is there and withheld, and a place it could not look at
  is withheld, never missing. Presence is the resolver's `present` at the place, read the way the emulator reaches it —
  through a link of Tender's to the store file behind it, which the resolver judges by its bytes (emu-atlas#578) — so
  Tender re-implements no symlink walk. A library row has no place, no verdict, and its `downloaded` is whether this
  platform's store holds it. Tender's own look (`FirmwareFileStore.look`, no link followed) decides the link's state,
  and the row's answer in one case only: a row for one file of a folder declaration (`_folder_rows`), which the resolver
  answers per file only where it makes a statement about that file. There presence is the look's `lexists`, and the
  verdict is the statement — or, where the resolver made none, not in place when nothing lies there and not established
  when something does. `present is None` leaves `downloaded` false and the row's verdict withheld, and the
  `RomM library files` ratio counts only the rows whose verdict was established (`ratio_server_count` /
  `ratio_local_count`): there a withheld row would read as a file missing
  ([#2285](https://github.com/danielcopper/romm-tender/issues/2285) D3). **Nothing enforces either crossing point.** A
  row builder deciding presence by `look(place).lexists` would go green and read a link to a missing store file as
  present; one reading `downloaded` as the verdict would read a wrong image as met. A withheld verdict is not an absence
  — its cause is read off the row's caveat codes and, for a declared FILE, off its `checked` (GLOSSARY.md → Byte
  reading), never off the verdict itself. Three of that vocabulary's nine values sit behind one withheld verdict and are
  three different statements: a file the emulator READ and does not recognise was checked, so wording it "could not be
  checked" is untrue; `refused` is not withheld at all, arriving with the verdict already `false`. Nothing checks that a
  consumer keeps them apart — `checked` is a plain string on the row beside a `satisfied` that reads like its summary
- **A firmware row's verdict is `BiosFileEntry.satisfied` — the resolver's per-file verdict at the place the launching
  emulator opens it, never presence** — test + prompt-only

  And a folder declaration is one row per file of it the resolver makes a statement about or recognises in the library
  (#2191 D26), each with the verdict over that file, the folder's own verdict counting once as one requirement; where
  that leaves no row, one row stands for the folder with the folder's verdict and the codes the resolver states over it
  (D28)

  `tests/services/test_firmware_store.py::TestAFolderTheEmulatorLists` pins the per-file rows end-to-end,
  `tests/services/test_firmware.py::TestAFolderRequirementIsAnsweredByItsContents` the folder's own row (D28),
  `tests/domain/test_firmware_wants.py` the fold the service asks through, and
  `tests/adapters/test_atlas_firmware.py::TestWhatAFolderDeclarationHolds` each file's verdict as the resolver states it
  (an image the emulator's own test accepts met; a file its reads disagree on, or whose read did not come back,
  withheld; a file it rejected named only where the folder holds no image, and not met)
  ([#2191](https://github.com/danielcopper/romm-tender/issues/2191) D26). The rule spans three modules and no
  diff-scoped review sees it whole: the adapter carries the folder's verdict and each file's, settled in the same
  verified per-platform reading the rest of the row comes from; `services/firmware/rows.py` builds a row per file the
  folder takes — each library file the packaged table recognises for the folder, and each other file a statement names;
  a file the resolver passes over because a valid image lies beside it gets no row — marks none of them required, stands
  one row for the folder where that leaves none (`_empty_folder_row`), and hands the folder's own verdict to the counts
  as one requirement (`count_required`'s `folders`); and both frontend surfaces colour and word each row off its own
  verdict. **Nothing mechanical joins those three**, which is what a consumer reading `downloaded` for a folder breaks:
  RetroDECK links LRPS2's `pcsx2/bios` onto the BIOS root, so the folder is always there, and reading its presence as
  readiness reports "All required ready" over a PS2 install with no BIOS file at all. The same holds for the third
  value: a required row answered `None` keeps the resolver's verdict open, and folding it into `False` claims an absence
  nothing established. Prompt-only: no consumer reads `downloaded` as a verdict or folds a `None` verdict into `False`,
  and none of a folder's files is required on its own row
- **Every BIOS colour is the resolver's verdict for the launching emulator (`requirements_met`), never a count of rows**
  — test + prompt-only

  The exceptions are a one-of group whose verdict is open because of the disc's region alone (the platform page's amber)
  and, on a game's page, the image serving the game's own region (`game_region_level`) — each read off the resolver's
  own statements

  `tests/services/test_firmware.py::TestTheGamePageReadsItsOwnRegion` pins the game page's half (everything outside the
  group first, then the image serving the game's region, grey where none serves it);
  `tests/domain/test_bios_status.py::TestTheLevelIsTheResolversVerdict` pins the level as the verdict (identical counts
  reading green, red or grey by the verdict alone; an emulator missing from the answer grey; a refused optional
  declaration changing nothing) and `::TestTheRegionDecides` the exception; `tests/services/test_firmware.py`
  (`TestTheRegionRuleOnThePlatform`, `TestTheConsolesOwnFirmwareDemand`) pins both end to end, and
  `tests/contract/test_bios_search_found_nothing_read.py` a not-met verdict with no row to show — DuckStation over an
  empty BIOS folder, read by the real resolver — reaching both reads as red; `frontend/src/utils/playSection.test.ts`
  pins the badge to the red verdict and `frontend/src/bigpicture/BiosTab.test.tsx` /
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx` the wording per surface.
  `tests/services/test_firmware_vectors.py` runs the platform page's read over the resolver's recorded machines with a
  firmware question, takes each emulator the resolver's answer names as the launching one, and holds the level against
  that emulator's verdict; what it compares and what it leaves out is its docstring's. Where the two differ today — the
  amber against a verdict not established, and a verdict Tender withholds beside EmuDeck's sealed catalogue — the pair
  stands on a list that only shrinks ([#2585](https://github.com/danielcopper/romm-tender/issues/2585) D4, D5). Every
  amber the exception above produces is among them; [#2580](https://github.com/danielcopper/romm-tender/issues/2580)
  removes those ambers. Recorded in [#2190](https://github.com/danielcopper/romm-tender/issues/2190) D1, D10 and D13,
  under ADR-0042 §1. **The rule spans the adapter, the domain, the service and every surface, and nothing joins them**:
  `adapters/atlas_firmware.py` carries `requirements_met`, the console's `system_firmware` and its world-knowledge mark,
  whether that console's image is shown absent (`system_firmware_met`, which the game page reads as red before narrowing
  to the game's region), the plain required files it could not judge, its refused declarations and its region note per
  emulator; `domain/bios_status.py::compute_bios_level` is the one place a platform's level is made and
  `game_region_level` a game's, `region_decides` the one place the exception is read, counting nothing of its own —
  every part of it the resolver's: its verdict open, its note that the disc's region decides it, a group with an image
  met and one not met and none not established, every other group met, nothing else open — and
  `services/firmware/status.py` carries the level and the resolver's words beside it. **What no count may do is decide a
  colour.** A count is what a sentence says about how much is there — "2 of 3 files … are in place" under a red verdict
  — and the colour is the resolver's answer over the whole declaration, which also weighs what no row can carry: the
  console's own demand. A `.info` marks a file required or optional and nothing else, so SwanStation marks all five of
  its PlayStation images optional while the console does not boot without one; the resolver weighs its packaged table of
  world knowledge in (`system_firmware`, marked `system-firmware-world-knowledge`) and answers not met. That need
  reaches a surface only as words, under a red verdict (`console_needs_image`), whether its image is shown absent only
  from the resolver's `system_firmware_met`, and which image is in place only from the row verdicts, never from a
  reading of Tender's own beside the verdict. A system the table records nothing about is an unasked question and never
  "this console needs no firmware". **The play row's badge** (`frontend/src/utils/playSection.ts::extractBiosInfo`)
  rises on `bios_level` `missing` alone: a verdict the resolver left open raises none, and so does the region exception
  — the badge does not say a game will not start on a guess about its disc. Every surface words the states through
  `frontend/src/utils/biosSummary.ts`, sole holder of their order. **The module's own drift lock is a test that reads
  components as SOURCE** (`frontend/src/utils/biosSummary.test.ts`, over the phrase list the module builds its answers
  from, with the ratio's twin in `biosHeldRatio.test.ts`), sweeping the components rather than naming them
  (`frontend/src/test-utils/componentSources.ts`, which says which set and why). **What neither lock can catch is a
  component inventing a NEW wording for one of these states**: only a copied phrase is searchable. Two limits of the
  sweep, both deliberate: it is `.tsx` only, so a wording helper extracted into a `.ts` beside its component is
  unsearched (`frontend/src/bigpicture/panelState.ts` is such a file and quotes BIOS prose today), and
  `frontend/src/utils` is out of scope because that is where the phrases legitimately live. Prompt-only: no surface
  computes a colour — `domain/bios_status.py::compute_bios_level` is the one place a level is made, and `region_decides`
  counts nothing of its own; the console's need for an image reaches a surface only as words under a red verdict
  (`console_needs_image`, from the resolver's `system_firmware` and its world-knowledge mark), whether its image is
  shown absent only from the resolver's `system_firmware_met`, and which image is in place only from the row verdicts;
  every surface words the states through `frontend/src/utils/biosSummary.ts`, sole holder of their order; the play-row
  badge (`frontend/src/utils/playSection.ts::extractBiosInfo`) rises on `bios_level` `missing` alone; a new wording, or
  one in a `.ts` helper (`frontend/src/bigpicture/panelState.ts`), escapes the wording locks
  (`frontend/src/utils/biosSummary.test.ts` over `frontend/src/test-utils/componentSources.ts`)
- **A one-of group is ONE requirement, judged by the regions its options serve — and an option never makes its row
  required** — test + prompt-only — `tests/domain/test_firmware_groups.py` pins the verdict (every state, a region
  nobody checked keeping a covered group off `met`, a stated uncovered region staying missing beside an unchecked one,
  the game's own regions, RomM's region names, the download rule) and, in `TestAnyConsoleIsJudgedTheSameWay`, an
  invented console with invented regions judged by the same rules;
  `tests/adapters/test_atlas_firmware.py::TestOneOfGroups` pins that an option borrows no requirement, that a file that
  is a plain row and an option is one row, and which caveats name a group's other regions;
  `tests/domain/test_bios_status.py::TestAOneOfGroupIsOneRequirement` pins that a group counts once and only `met`
  counts as held, and `::TestThePlayRowTokenCountsPlainFilesOnly` what the token says beside one;
  `tests/services/test_firmware.py` (`TestAOneOfGroupIsOneRequirement`, the group cases of `TestOnePlatformOneEmulator`
  and `TestOneRomOneEmulator`, and `TestDownloadRequiredFirmware`'s group cases) pins it end to end, that the game page
  judges the ROM's own console regions (`console_regions_of`), and that the button's count and the download are one set;
  the frontend halves are `frontend/src/utils/biosSummary.test.ts`, `frontend/src/utils/biosGroup.test.ts`,
  `frontend/src/bigpicture/BiosTab.test.tsx` and `frontend/src/bigpicture/library/PlatformsTab.test.tsx`. The rule spans
  the adapter (`adapters/atlas_firmware.py::_wants` and `_groups`), two domain modules (`domain/firmware_groups.py`
  judges, `domain/bios_status.py` counts), the service (`services/firmware/status.py`, `downloads.py`, `game_detail.py`
  passing the ROM's regions), six frontend modules (`frontend/src/utils/biosSummary.ts`,
  `frontend/src/utils/biosGroup.ts`, `frontend/src/utils/biosFetchable.ts`,
  `frontend/src/bigpicture/bios/BiosFiles.tsx`, `frontend/src/bigpicture/BiosTab.tsx`,
  `frontend/src/bigpicture/library/PlatformDetail.tsx`) and the two type modules they share
  (`frontend/src/types/firmware.ts`, `frontend/src/api/backend.ts`), and nothing joins them. **Prompt-only**, each of
  those at a NEW site: no consumer reads an option's `need` as its row's `required` — `required` comes off an emulator's
  plain declarations alone; a group's verdict is never folded into a colour on the wire — the state is one of four words
  and the frontend picks the colour; `unknown` is never read as `met` or as `unmet`; the level a group reaches is the
  resolver's verdict, a partly covered group reaching the platform page's amber only under the region rule (the colour
  entry above); `Download required`'s count and the download it starts both come from `fetch_for_required`
  (`domain/firmware_groups.py::fetched_as_required`) and never from a second rule; and nothing in the judging knows a
  console, an emulator or a region by name — a group the resolver states for a console nobody has worded must be judged,
  counted and fetched with no code change, its regions printed in the resolver's own spelling where no name exists. The
  region sets the verdict reads beside the options come from the entry's own caveats, and which ones — and which one
  must never be read as unchecked — is `adapters/atlas_firmware.py::_UNCHECKED_REGION_CODES`'s. A region whose every
  name the resolver refused arrives with neither an option nor such a caveat: the resolver names it, with every other
  region the group carries no option for, only under `unstated` on the group's `core-mode-unestablished` caveat, and
  that field is the one it is read off (`adapters/atlas_firmware.py::_unstated_regions`; `TestOneOfGroups` pins it read
  from that statement alone and spelled as the resolver spells it,
  `tests/domain/test_firmware_groups.py::TestARegionTheGroupStatesNothingFor` that it is unchecked and named,
  `TestTheRegionDecides` that it keeps the platform off amber)
- **Which emulator a set of answers is about is ONE pick per scope — a platform's, and a ROM's — and every answer in
  that scope is a projection of it** — test + prompt-only —
  `tests/services/test_firmware.py::TestOnePlatformOneEmulator` asserts the two surfaces AGREE across every way a
  platform arrives at an emulator (no pick, each of the three ES-DE offers, a pin naming an emulator the catalogue no
  longer lists, a pin that stands whatever its state) rather than pinning today's value, because a value test would pass
  for a third resolution that diverges on some other configuration;
  `::TestDownloadRequiredFirmware::test_it_fetches_what_the_platforms_own_pick_calls_required` holds the download button
  to the same pick. The pick is `domain/emulator_commands.py::resolve_firmware_option`, through which every platform
  BIOS site resolves — the per-platform override (`settings.json` `platform_cores`), passed through
  `domain/emulator_pick.py::pick_for`, while its label still names an entry, whatever that entry's state, else the entry
  the source ships first, whatever its state, since an emulator that cannot start still declares the firmware it would
  need ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D7, D23) — and it is the read-path precedence
  `ActiveCoreResolver` applies minus the per-game layer. Two call sites read it today:
  `FirmwareStatusReader._platform_emulator` (which serves BOTH the per-platform answer's `active_core` /
  `active_core_label` and `check_platform_bios`'s `launching_emulator=None` fallback) and
  `FirmwareDownloader._platform_emulator_identity` (the emulator `Download required` fetches for where the page names
  none). The platform's core read (`CoreService.get_system_core_info`) is not one of them: its `active_core_label` names
  the pick, else Tender's default — the first enabled source's (#2195 D4) — and agrees with the BIOS pick wherever the
  answering source is that source and its default's installation is established. **Nothing joins them**, and a fourth
  resolution is exactly what this entry is about: the pane displayed a just-picked PCSX ReARMed and judged the platform
  by the libretro system default beside it, so one PlayStation read `ok` on the game page and `missing` on the pane.
  `.label` and `.emulator` must come off ONE call — two calls agree by coincidence, which is what the old pair did until
  an override was set. **One seam now carries the pick rather than a projection of it**: `BiosChecker` takes a
  `LaunchingEmulator` (`domain/emulator_commands.py` — `emulator` and `label`, both read-only), so the per-game caller
  hands over the whole resolution and `check_platform_bios` reads both projections off that one value. A mismatched pair
  is not representable there, which is why the answer may state its own `active_core_label`: it is the label half of the
  pick those very counts were filtered by (`TestTheAnswerNamesTheEmulatorItJudgedBy`, which hands the check two picks
  differing only in label and holds the label to moving while the judgment does not). That covers this seam and no other
  — the remaining sites still pair by discipline. The game page's BIOS headline names the emulator by the answer's
  launching entry in `emulator_tabs` — every label its identity goes by (#2190 D28, D38) — and by `active_core_label`
  only where no entry is the launching emulator's (`launchingName` in `frontend/src/utils/biosSummary.ts`). **The key is
  the emulator IDENTITY, not `.core_so`** (#1821): the identity names a standalone pick as readily as a libretro one,
  where `core_so` is `None` for every standalone emulator, and a pick with no identity is answered as not established
  (#2190 D26). Reaching for `.core_so` here again would turn every standalone pick into that answer silently, because
  the field is still there and still right for the picker payload beside it. `CoreInfoProvider.get_active_core` — the
  "first libretro entry, whatever its state" reading these sites used — has no production caller left. **The ROM scope
  is the same rule one layer in, over a different pair of modules**: the game page is assembled by two services that
  each ask `ActiveCoreReader.resolve_rom` for themselves — `services/cores.py::get_platform_core_info` names the pick in
  the picker through `resolve_rom`, `services/game_detail.py::get_bios_status` scopes the BIOS question to the entry
  that same call chose, whether or not it can start — and `::TestOneRomOneEmulator` asserts they agree across every way
  a ROM arrives at an emulator (nothing pinned, the platform's pick, a per-game override, the override over a platform
  pick naming something else, a standalone pick, a stale pin that degrades, a standing pick that is not installed). They
  read one seam today and nothing says they must; the picker reaching for `active_core_for_rom` — the `.so`-space
  projection right beside it — would answer `None` for every standalone pick and send the BIOS rows back to the
  platform's own, which is the platform-scoped defect above, per ROM. The one-of groups an answer judges are that same
  pick's too (`FirmwareCatalogue.groups_for(identity)`), and both classes hold the group verdicts in their agreement
  (`_emulator_dependent`), so a second resolution feeding the groups would show as a disagreement there rather than as a
  group judged for another emulator. So are the rows ([#2190](https://github.com/danielcopper/romm-tender/issues/2190)
  D2, D9): `FirmwareCatalogue.placements_for(identity)` answers each file name with the launching emulator's own
  requirement — its place, its presence, its verdict — wherever it declares that name, so with YabaSanshiro picked for
  Saturn the row is `saturn_bios.bin` directly under the BIOS root whatever lies under Kronos's `kronos/`
  (`tests/services/test_firmware.py::TestEachEmulatorsOwnPlace`). Every OTHER emulator the resolver answered for is an
  entry of its own (`emulator_tabs`, `::TestOneEntryPerEmulator`), its rows that emulator's own requirements, which the
  BIOS tables' emulator selector shows; choosing one never changes the level, the header's counts or the badge. What it
  does change on the platform page is what `Download required` fetches: it carries the shown emulator's identity
  (`emulator`, `None` for the pick) and fetches what THAT emulator requires
  (`TestEachEmulatorsOwnPlace::test_download_required_fetches_for_the_shown_emulator`), and an identity the answer holds
  no verdict for is refused with `emulator_unknown`, because `placements_for` would answer it with the first declarer's
  requirement (`::test_an_emulator_the_answer_does_not_hold_is_refused`). Where a download is linked follows no pick:
  every download, a game page's and a row's own included, lands in the platform's store folder and is linked wherever an
  emulator of the platform opens the file ([#2191](https://github.com/danielcopper/romm-tender/issues/2191) D3,
  `::test_a_game_page_download_links_the_file_for_every_emulator_of_the_platform`). A reader keying a launch's rows on
  `by_file_name()` instead would read the first declarer's place and verdict wherever two emulators declare one name at
  different places — Saturn green under YabaSanshiro over a file only under `kronos/`. **What the ROM sibling cannot pin
  is the fixture's own default**: the bare `FakeCoreInfoProvider` lists only the entries it is handed (`options`, or
  `available_cores`) and its `active_core` tuple decides no default, so a test that hands it none resolves an unpinned
  ROM to `None` where the live adapter resolves it to an emulator — the fixtures in that file hand it the declared
  entries, and every other fixture on a bare fake still exercises the weaker resolution
- **A platform's BIOS answer is asked for one platform at a time, and a row that has not got one yet is never rendered
  as a row nothing could be established for** — test + prompt-only —
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx` pins the five parts that can be seen from a test: the two
  renderings apart (an outline dot and "Checking…" against the solid grey dot and "Nothing could be established"), the
  walk reading every listed platform in the list's own order, the focused row asked ahead of the rows above it, the walk
  stopping at unmount, and a read issued before a core change not overwriting the one issued after it. Each was
  mutation-checked. **The JOIN between the calls is pinned once**, in `tests/contract/test_firmware_status_read.py`: it
  composes the list, the overview and one per-platform call per listed platform over the real wiring and holds the
  result against the key set the single whole-page call answered with, which is the one thing the service tier cannot do
  — its ~25 pane tests ask through a local helper that would reproduce a composition bug rather than catch it. **The
  rule spans three frontend modules and one backend split, and nothing joins them.** `services/firmware/status.py`
  answers `get_firmware_status` (whether RomM's firmware listing could be read, and nothing about any platform) and
  `get_platform_firmware_status` (one platform's whole entry, for every platform it is asked about, which pays a live
  per-system reading — measured in [qam-panel.md, "Library"](../qam-panel.md#library)); `usePlatformsPage` owns the
  walk, the per-slug ordering counter and the three-valued `firmwareState`; `PlatformsTab` draws the dot;
  `PlatformDetail` words the pane. Every failure here is silent and looks like an answer. A platform-bearing field
  creeping back onto the overview payload gets rendered over a platform nobody has asked about yet. A fifth rendering
  path reading `firmware === null` instead of the state says "nothing could be established" about most of the list for
  the first seconds of every visit — which is the confusion this cut exists to remove, restored by a truthiness test. An
  answer already held is not taken back by a later failure (`firmwareStale` beside the state, never instead of it).
  **Two halves no test reaches**: the `alive` guard in the hook's `accept` is unobservable under React Testing Library,
  which drops a write to an unmounted tree itself — what a test can see is the walk stopping, so the guard states the
  rule rather than being held to it; and whether an 8px outline reads as "not yet" against a filled dot is device-only,
  like everything else about this list's legibility
- **The whole-machine firmware inventory is never asked with content verification, and the per-platform reading is never
  asked without it** — prompt-only — `firmware_inventory()` (`FirmwareResolver`, `AtlasFirmwareAdapter`) is asked
  unverified: `verify=True` there sweeps every unclaimed file under the BIOS root plus each declared file the packaged
  identity table covers at a matching size, and its one caller — the home migration's untracked-BIOS sweep — needs only
  where a file LIES. `firmware_for_system(<system>)` (`FirmwarePlatformResolver`, `AtlasPlatformFirmwareAdapter`) is
  asked WITH it, and that half is the one a reader is likely to "optimise": drop the flag and two answers go silent
  rather than loud — a packaged card that identifies its image by content names no file at all (DuckStation comes back
  `declaration="packaged"` with an empty list and no system recording), and every folder declaration's verdict falls to
  `None`, which takes each platform holding one to `unknown`. Measured on the reference machine at emu-atlas 0.22, the
  median of five runs each: 59-173 ms per system verified, against 172 ms for one unverified whole-machine sweep — the
  per-system read performs no unclaimed sweep at all, which is what bounds it. Nothing detects either direction:
  `verify` is one keyword argument on each call and every test stays green
