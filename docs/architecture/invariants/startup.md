# Invariants: Bundles and the start-up check

What each bundle carries and its budgets, the third-party notices, Steam's React globals, the Steam internals list, and
what the start-up check asks, logs and shows. How an entry reads and binds, and the other areas:
[the register's index](index.md).

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
  given a budget: [frontend-bundles.md](../frontend-bundles.md#third-party-package-budgets)
- **Every package `frontend/package-budgets.json` lists, in any bundle, has a `## <package>` section of its own in
  `THIRD-PARTY-NOTICES.md`** — check — `scripts/check_third_party_notices.py`. A package a bundle carries is distributed
  with Tender, and its licence asks for a notice that travels with it; a bundle carries a package's code but not its
  LICENSE file, so the notice lives in that file, and nothing about adding a package to a bundle touches it. The budgets
  file is the list that does move with a bundle — the package check above fails until a carried package has a budget —
  so holding the notices against it means a package cannot join a bundle unnamed. A package named only in running text
  or under a heading of another level counts as missing. **What it cannot see is whether a section is true**: the
  licence, the source and the bundles it names are read only in review. A section for a package no bundle carries any
  more passes, because a stale notice claims too much rather than too little. A `## <package>` line inside a code fence
  counts as a section, since the check reads lines and not Markdown. And content under another licence inside a package
  — the Font Awesome icons inside react-icons, CC BY 4.0 inside an MIT package — is seen by no check at all; its
  subsection is held only in review
- **Tender's three React globals are spelled exactly the way Decky Loader spells them** — test —
  `frontend/src/boot/steamGlobals.test.ts`, which reads `steamGlobals.ts` and the pinned `decky-globals-block.txt` as
  TEXT and compares the four search predicates, which global each answer is assigned to, and the JSX stand-in's keys and
  aliasing. The cost of a difference lands on DECKY's users, not ours: its loader skips its entire globals block when
  `SP_REACT` is already set, so when ours runs first, Decky's whole frontend renders through our shape. **The pinned
  copy is the half nothing can check** — it is upstream's file, held still by hand, so a refresh that is wrong reads as
  agreement; the provenance header names the commit it was taken at so the question can be re-asked rather than trusted
- **Every value the panel imports from `@decky/ui` is classified by the start-up check** — test —
  `frontend/src/boot/steamModules.test.ts`, which sweeps every non-test module under `frontend/src/` and fails on an
  imported name that is in none of the four lists (`STEAM_LOOKUPS`, a search it asks; `UNVERIFIABLE`, a name it cannot
  answer for; `ASKED_LIVE`, a name answered by Steam's runtime state; `PACKAGE_OWN`, the package's own code). The swept
  set is derived rather than listed, because a file missing from such a list carries no lock at all. **What it cannot
  see is whether a classification is TRUE**: two names sit in the unverifiable list because they are wrappers the
  package always defines, and moving a real search there to quieten the check would pass green and leave the panel
  rendering a hole where the check reported everything resolved. The one list it CAN judge is `ASKED_LIVE`, which the
  test derives rather than checks for membership — it walks `@decky/ui`'s shipped `dist/` for exported functions
  reaching `getGamepadNavigationTrees`, `getFocusNavController` or `document.title` (through a module-private helper
  within a file, which is what carries `useQuickAccessVisible` through `getQuickAccessWindow`) and holds that set,
  intersected with what the panel imports, EQUAL to the list's keys, with none of it in `STEAM_LOOKUPS`. That axis is
  the rule the whole check rests on: every entry reads the module registry or a bootstrap global — one registry, the
  same in both of Steam's modes — and not what Steam has mounted or focused, which the same question answers differently
  a second later. A start-up reading of `findSP` refused to mount the panel in the desktop client over a Big Picture
  tree that had not been built, and blamed Decky Loader for it. **What the derivation cannot see is a runtime-state
  reader reached through a name the sweep does not know** — an arrow export, a re-export, or another module's helper
  (`showModal` calls `findSP() || window` from `dist/components/Modal.js`) — and it sees `function` declarations only,
  not nested in another. What the sweep cannot see it says nothing about: such a name can sit in `STEAM_LOOKUPS`
  unflagged, which is the shape this cut removed by hand. What the narrowness cannot do is put a registry search onto
  the live list in silence — a name the sweep did not derive fails the equality there. The walk is
  `frontend/src/test-utils/jsFunctionScanner.ts`, a string-, comment- and regex-aware scan; why a regex could not do it
  is on the docs page
- **Every Steam internal the code reaches is named in one list — the panel's in `frontend/src/boot/steamInternals.ts`,
  what the backend spells in Steam in `backend/domain/steam_internals.py` — and `frontend/src/types/steam.d.ts` declares
  nothing the code does not reach** — check + test + prompt-only

  Every panel entry states what its absence costs, never the panel, and how the start-up check asks it

  Valve promises none of `SteamClient`'s calls, the stores, their members or the routes, and a Steam update that drops
  one breaks the feature built on it without a word: a press that does nothing, an error in the log, a placeholder where
  an image should be. The list is what a check at start-up and a check against Steam's published client can be built on,
  and it is only worth that while it is complete, so both halves are held to the code rather than kept by hand. The
  panel's half, `frontend/src/boot/steamInternals.ts`, is held by `frontend/src/boot/steamInternals.test.ts` to what the
  type checker resolves in the shipped code (`frontend/src/test-utils/steamReach.ts`) — its typed entries EQUAL to what
  that reaches, less `NOT_STEAM_INTERNALS`, and its untyped entries out of it: a member counts when it is declared in
  `steam.d.ts`'s globals, in `@decky/ui`'s `dist/globals/` or `webpack.d.ts` — which declare Steam's other globals
  (`webpackChunksteamui`, `App`, …) with types of their own, so a reach typed by them compiles without `steam.d.ts`
  naming it — or in a shape a cast of the window names or extends in its own file, followed within that cast's file. Its
  path is derived rather than written: Steam's own spelling, rooted at the global, along the first route from Steam's
  roots to the member's type — the shortest, ties broken as the docs page states — so the two checks the list is for can
  look each one up as Steam spells it; a type no route reaches fails. Both directions are required, and `steam.d.ts` is
  held the same way — every member it declares must be reached — so a declaration nothing reaches fails as surely as a
  reach nothing lists. The order of the two declarations of Steam's globals is pinned with it: `steam.d.ts` is the one
  in effect, which no compiler error shows, because `skipLibCheck` hides the conflict; were Decky's to win, which takes
  `steam.d.ts` ceasing to be a script, the list's typed entries and the members `steam.d.ts` declares would go
  unreached, and a reach the list does not name would appear. A reach no type describes — a string key, a `Record`, an
  `unknown`, a route — is listed by hand with the files that make it, and held only to its words appearing in code there
  — every part of its path spelled in code in each file it names; a window cast the scan cannot read, and a route
  literal (a path handed to `Navigate`, or one spelling `steamloopback.host`, `steam://` or `/library/app/`), must sit
  in such a file, so one in a file no entry names is reported. `frontend/src/test-utils/steamReach.test.ts` holds the
  scan's shapes the shipped code has no site of. The backend's half, `backend/domain/steam_internals.py`, is held by
  `tests/domain/test_steam_internals.py` over the syntax tree: every string under `backend/` but `_vendor/` and
  `native/` naming Steam (outside docstrings and logging calls) is held in a module-level constant the list names, each
  such constant spells its entries, and each property it reads is an entry's, or JavaScript's own as the test names them
  (`_JAVASCRIPTS_OWN`). Unseen on the panel's side: a read beneath a value typed `unknown`, `any` or `Record` (the
  coercions in `utils/runningApps.ts` and `utils/steamReadyForToasts.ts`, the values of `deckDesktopApps.apps`,
  `webpackChunksteamui.push`'s factories), so a new member read beneath any of them; an element access whose key is no
  string literal; a member asked by `in`, `Reflect` or `Object.keys`; a Steam shape declared in another file than the
  window cast that reaches it; a Steam object reached neither through a declared global nor through a window cast, or
  through a cast of something not typed as a window; a Steam global declared anywhere but `steam.d.ts`, `@decky/ui`'s
  `dist/globals/` and `webpack.d.ts`; a member `steam.d.ts` declares in another form than a property or method
  signature; a second unreadable cast or a second route literal in a file an untyped entry already names; a route that
  spells none of those three whole and is not a string literal handed as the first argument to a method named `Navigate`
  — one built from pieces, held in a variable, handed to `Navigate` called bare or by element access, or handed to
  anything else; an untyped entry whose every part its file spells but not its path, since the parts are matched one by
  one; and the route each site reaches a member by, since a member is named after its type's first route. Unseen on the
  backend's: a Steam global the sweep's spellings do not name, a name built at run time, a string inside a call to a
  method named like a logging one whatever its receiver, a global a listed expression reads with no member after it, a
  read compared by its name rather than its path, a read the check's two patterns (`.name`, a double-quoted `["…"]`)
  miss — a single-quoted, computed or escaped key — and a constant that spells each part of an entry but not its path.
  None of it is a check of what an internal DOES — Steam keeping a name while changing what it answers stays a device
  test. **The panel's half is what the start-up check asks after, so each entry carries what that check needs** (#2509
  D3): its absence cost on the start-up check's scale, which the type keeps off `panel` (`absenceCost` excludes it, and
  `steamInternals.test.ts` holds that no entry costs `panel`) — a core internal that brought up the fallback page would
  take away every feature that still works — and the losses the notice on Main names (`STEAM_FEATURES`, whose every
  label one list or the other must use), none for a `diagnostic` entry and at least one for every other, which the type
  holds too; and how it is asked, `call`, `read` or `notAtStartUp` with the reason, which the test requires every
  `notAtStartUp` to give. A wrong `call` is a false notice on every machine (a value reported as "not a function") and a
  wrong `read` a miss nobody sees, so the leaves the type checker can judge are held to it: a leaf typed entry is `call`
  exactly where `steamReach.ts` finds the member declared as something to call (`callable`), and `asked` is `call` for a
  path naming what a call answers and `read` for one naming an element. An entry only the desktop surface reaches is
  marked `devBuildOnly`, held to exactly the entries reached from `desktop/` alone, because a release build has no such
  surface to tell a user about. Entries the code falls back between are one group of alternatives (`oneOf`); which
  entries those are is read off the code's fallbacks by hand, and the test holds only that no group has one member.
  Prompt-only: a reach no type describes is listed with its files, and its `asked` is held by nothing where its path
  ends at its static part; which entries are alternatives of one another (`oneOf`); a `NOT_STEAM_INTERNALS` prefix names
  nothing of Steam's; and whether an entry's cost and losses are what its absence costs, and whether a `notAtStartUp`
  entry truly cannot be asked.

  **The list is also what a daily job looks for in the client Valve publishes** (#2509 D4, D14):
  `.github/workflows/steam-internals.yml` runs `scripts/watch_steam_internals.py` over the Deck client's stable and
  public-beta UI packages on Valve's client CDN and reports an internal Steam no longer spells on one issue, so a drop
  is seen in the public beta before it reaches stable, and within a day of any release, rather than on a device. It is
  an early warning and fails no check, because a name search proves nothing about Tender's code and a refactor on
  Valve's side can raise it with nothing removed. A name Steam's own JavaScript never spells carries a stated exemption
  on its entry, so the warning is not raised every day for a name it could never find; the job also reports an exemption
  both channels now spell, which nothing else would notice. The watch is tested by
  `tests/scripts/test_watch_steam_internals.py` and `frontend/scripts/steam-internals.test.mjs`, and each half's list
  test holds that every exemption gives a reason. Unseen by it: which object a name is on, since a name found anywhere
  counts, so an entry with generic names such as `id`, `data`, `Save`, `appid`, `has` or `App` is watched only through
  its other names; a refactor of the one site Steam spells a name at, which raises an alarm with nothing removed; a
  binding gone from Steam's native side while its JavaScript still spells it; an exempted name, and a group with an
  exempted member, which are never missing — only an exemption both channels now spell is reported, and whether a reason
  is true is prompt-only; `SharedJSContext`, found as a service name rather than as the debugger target's title; a
  manifest that stops moving, checked at its old version with only that version in the issue; a group member missing
  beside a found alternative, which reaches the run's log alone; a run that cannot fetch or verify a channel, which
  fails red and puts nothing on the issue; what a name does; and the job itself once GitHub disables a scheduled
  workflow after 60 days without activity in the repository, which the job cannot report
- **Whether every search answered and whether the panel may MOUNT are two questions, and a miss that costs less than the
  panel never takes the interface off the air** — check + test + prompt-only — the type carries the first half:
  `SteamLookup.absenceCost` is required, so a new entry does not compile until it states which of the four its absence
  costs — the `panel`; a whole `feature` outside it (`ToastRenderer`, `NotificationStore` and `ErrorBoundary`, without
  any one of which no toast appears at all and every page, sync and download is untouched, plus `AppDetailsRoute` and
  `appDetailsClasses`, without either of which Steam's game page carries no Tender section); only its `appearance`
  (`ControllerGlyph`, whose two consumers draw something in its place — `layout/WidePage.tsx`'s Back chip `‹ Back` and
  `EmulatorSelector.tsx` a key box naming L2 or R2 — and `toastClasses`, whose every read is optional so the toast says
  what it says in an unstyled box); or only a `diagnostic` (`playSectionClasses`, read nowhere but
  `gameDetailPatch.tsx`'s one-shot `dumpTree`, which already prints `UNDEFINED` in its place) — and there is no default
  to arrive in. `frontend/src/index.test.tsx` pins both factory branches — the panel mounts with everything registered,
  and the miss reaches the log. **Blocking is the status quo and staying there costs no evidence: nothing here is a
  claim that every other name was judged**, only that moving one OUT needs its every consumer read, one name at a time —
  prompt-only. **The join is prompt-only and spans three places**: `checkSteamModules` derives `panelMayMount` from the
  costs, `index.tsx` gates the fallback page on it and logs `describeSurvivedMiss` on the other side, and that sentence
  answers whose COPY of `@decky/ui` ran the missed searches rather than naming a repair of its own — it used to say "a
  newer Tender" unconditionally, which held only while nothing reaching it was a name the package exports, and
  `playSectionClasses` is one. What `frontend/src/boot/steamModules.test.ts` locks is the property the line's remaining
  own answer rests on — a non-blocking name `@decky/ui` does NOT export must be one Tender resolves for itself, swept
  from the source in the three shapes one is written in (a `find(?:Module|ClassModule)\w*` call, a direct cast of
  `window` whose exported name equals the property read, and a `searchSteamFactories` scan over Steam's module
  factories) — so the three `SP_*` globals, which the frontend cannot attribute to a program from inside the page, fail
  there the moment one is made non-blocking, instead of shipping a repair aimed at whichever program did not install
  them. **`!== "panel"` is the only reading of `absenceCost` that decides anything** (prompt-only), so `feature`,
  `appearance` and `diagnostic` record why a name is off blocking; the log line prints each missing name's cost and
  losses and the notice on Main names the losses of a survived miss — both read the cost and the losses only to word a
  miss — which is why every lookup at `feature` or `appearance`, and no other, must name a loss (`steamModules.test.ts`)
  — a loss the reader sees cannot then go unnamed. The two things that answer for the toasts are prompt-only and read
  NAMES: `notificationsMissing` over `NOTIFICATION_LOOKUPS` records what the toaster can do
  (`utils/notificationsHealth.ts`) — from those names, never from a `feature` cost — and `describeSurvivedMiss` puts the
  fact in the log as a sentence stating the loss and naming NO repair of its own — the verdict sentence beside it names
  one that is right under every answer, which it has to be, since `ErrorBoundary` is a `@decky/ui` export and a miss of
  it alone in the coexistence bundle is `decky`. So a `feature` entry added for something else cannot make either claim
  the notifications are what went missing, and a second spelling of any of the three cannot leave them answering for a
  lookup nobody asked about. Both directions fail quietly: call a real dependency cosmetic and the panel mounts and
  renders a hole, which is the fault the whole check exists to tell apart from a backend that is not running; call a
  decoration blocking and one missing glyph costs the user their entire interface, which is what this entry removed.
  Prompt-only: `checkSteamModules` derives `panelMayMount`, and `index.tsx` gates the fallback page on it; `!== "panel"`
  is the only reading of `absenceCost` that decides anything — the log line and the notice read the cost and the losses
  only to word a miss; what the toaster can do (`utils/notificationsHealth.ts`) is recorded from `notificationsMissing`
  over `NOTIFICATION_LOOKUPS` names, never from a `feature` cost; a name leaves `panel` only once its every consumer is
  read
- **Every start-up check that misses something writes ONE warning to the backend log — which check, then every missing
  name with its cost and losses — whether the panel mounts or the fallback page stands in its place; every miss the
  panel survives reaches the one notice on Main** — check + test + prompt-only

  A missed Quick Access search also raises one toast, under the panel and under the fallback page alike, unless one of
  `NOTIFICATION_LOOKUPS` missed too; the Steam-internals check asks once Steam reports its services initialised, or once
  the 30-second deadline has passed, under the fallback page too, for the log alone; a group of alternatives none of
  whose members is there is a miss like any other, while a member missing beside an alternative that is there or cannot
  be asked is logged and raises no notice; and what only the desktop surface reaches is asked only where the build stamp
  `virtual:tender-desktop-surface` says the surface is in the build

  A miss that reached only Steam's JS console was a miss no bug report carried, and the lost glyph of #2454 was found
  only because the fallback page happened to list every name (#2455). So both checks, the searches into Steam's module
  registry and the Steam internals, word a miss in one shape (`boot/startupMiss.ts`) — which check, how many of how many
  missed, then each missing name with its cost and losses — and the panel factory writes it through `frontend_log` at
  `warn`, the fallback page's miss included (#2455 D1, D2). A miss the panel survives is also the one notice on Main
  (`utils/startupMissNotice.ts`, #2455 D3, D4), which replaced the notice about Steam answering for no notifications:
  the losses both checks named, each once, or, where only a `diagnostic` name missed, the notice without a list; the
  names stay in the log. Its one blind spot is the Quick Access entry's own search, without which neither the panel, nor
  the fallback page in its place, nor Main can be reached, so that miss also raises one toast at start-up on either
  branch — none where one of the three searches a toast is raised through (`NOTIFICATION_LOOKUPS`) missed as well, when
  the log line alone carries it (#2509 D10); a missing `NotificationStore.ProcessNotification` is not one of them and is
  asked only after the toast is decided, under which the toast is raised into a store that refuses it. The
  Steam-internals check asks once Steam reports its services initialised (#2509 D11), since the panel is loaded the
  moment `webpackChunksteamui` exists and whether Steam's stores are assigned by then is not established; asked earlier
  it could raise a false notice on every cold start, and past the 30-second deadline it asks anyway and says Steam was
  not ready. The notice can therefore appear after Main has mounted, which is why Main subscribes. Under the fallback
  page the check is asked as well and only logged, since the log is to be complete whatever the user saw. Where the code
  falls back from one internal to another, the two are one group of alternatives (`oneOf`, #2509 D13): a group none of
  whose members is there is a miss like any other, logged and on Main, and a member missing while an alternative is
  there, or while the alternative cannot be asked at start-up, is logged under its own name and raises no notice — the
  log stays whole, and a Steam that drops one half raises no false notice. Asked alone, each member would raise one;
  counting an alternative that cannot be asked as present would drop a miss the check sees from the log too. A log level
  set to `error` drops the warning like any other, and nothing sees that. What only the desktop surface reaches —
  `ReactClient`'s search and the desktop internals — is asked only where the build stamp
  `virtual:tender-desktop-surface` says the surface is in the build (#2509 D12); every other build answers `false`,
  declares the desktop modules free of side effects so the search is left out with the folded entry, and
  `frontend/scripts/check-bundle-shape.mjs` holds the release bundles to both (both release panel bundles stamped
  `DESKTOP_SURFACE = false` and carrying no `createRoot` search); unseen by the bundle check: a desktop search that
  spells no `createRoot`. Each half fails quietly: a miss logged nowhere is the fault this entry removes, and a release
  bundle asking after the desktop surface tells every user about a surface their build does not have. Pinned by
  `frontend/src/index.test.tsx` (one warning for a survived and for a fallback miss, the internals line once Steam is
  ready, past the deadline and under the fallback page, a failed check logged, a miss an alternative stands in for
  logged with no notice, the panel mounting over an internals miss, the toast on both branches and its two absences, the
  installation's own warning for a module the check found); `frontend/src/boot/steamInternalsCheck.test.ts` (with the
  groups of alternatives), `frontend/src/boot/startupMiss.test.ts`, `frontend/src/bigpicture/MainPage.test.tsx` (the
  card with and without a list, an answer landing after Main mounted, no card) and
  `frontend/src/utils/steamReadyForToasts.test.ts`. Prompt-only: the warning is worded only by `boot/startupMiss.ts` and
  written only by the panel factory; nothing else records into `utils/startupMissNotice.ts`; a new search or internal at
  `feature` or `appearance` names its loss from `STEAM_FEATURES`, never a sentence of its own
- **The fallback page names the copy of `@decky/ui` that actually ran the search that missed, and the repair that
  follows from it** — check + test + prompt-only — the artefact's stamp (`BUNDLE_KIND`, standalone or coexistence) is
  checked (`frontend/scripts/check-bundle-shape.mjs`, per bundle and on `globals.js`, which must carry none), and the
  sentence behind every verdict in `SEARCH_OWNERS` is pinned in `frontend/src/boot/steamModules.test.ts` and
  `StartupFailurePanel.test.tsx` with both bundle values exercised — the test iterates that list rather than a count, so
  a verdict added without a sentence on each surface fails instead of going unworded. **The join is prompt-only and
  spans four places**: `rollup.config.js` serves the stamp, `boot/searchingCopy.ts` reads it and Decky's namespace,
  `boot/steamModules.ts` words it, and `index.tsx` resolves it ONCE for the log line and the page — two resolutions
  could disagree with each other. The predicates belong to `@decky/ui` and the coexistence bundle runs DECKY's copy, so
  a page that blamed Tender in both would send a user after the wrong program while Decky's own interface and its other
  plugins broke beside it. **A miss confined to names `@decky/ui` does not export names NO copy and offers NO repair**
  (prompt-only) — `SP_REACTDOM` is the only one that reaches that state alone, `ControllerGlyph` only ever beside a
  global (on its own it is cosmetic and brings no page up at all, per the entry above), and `describeFailure` answers it
  before it asks whose copy ran anything. Naming a copy would blame Decky for a predicate of ours; the silence about a
  repair is right for the three globals and a real loss for the glyph, and only the second half of that is easy to
  forget. For the globals no repair follows: who installed them on a machine running both now HAS an answer — the
  injector loads `globals.js` only where Decky Loader is not serving, so beside a serving Decky they are Decky's — and
  **this branch does not read it**, because it keys on whose COPY ran the search rather than on which program installed
  a global. In the standalone bundle the answer would not settle it anyway: a missing `SP_REACTDOM` there is
  `globals.js` not having run OR our own ReactDOM predicate in `boot/steamGlobals.ts` having gone stale — two repairs
  behind one symptom. `ControllerGlyph` is reached by a `findModule` predicate of ours in BOTH bundles, so a newer
  Tender IS its repair and this branch cannot say so; restoring it here would take a third axis (whose PREDICATE, not
  whose copy), never a reworded answer. What bounds that cost is only that the glyph's absence costs appearance, so it
  never brings the page up alone and `describeSurvivedMiss` prints its sentence into the log whenever it is the whole of
  the miss. **It is NOT bounded to the company of a global**: beside a blocking `@decky/ui` name the verdict is `mixed`
  and the glyph is that answer's unnamed rest, asking for a report rather than naming an update, with no global anywhere
  in the miss — `steamModules.test.ts`'s "leaves the glyph in the unnamed rest with no global anywhere in the miss" is
  that case. Four quiet ways back, all prompt-only: a runtime probe instead of the stamp (`typeof DFL !== "undefined"`
  is true of a standalone bundle loaded beside a running Decky), asking `in DFL` about a name `@decky/ui` never exported
  (`SP_*`, `ControllerGlyph` — a package disagreement reported on every miss, which is what `SteamLookup.deckyUiExport`
  and its sweep-derived lock exist to prevent), reading an unreadable `DFL` as an absence rather than as nothing
  established, and letting the reading THROW at all — `definePanel`'s factory reads it before it returns anything, so an
  unguarded `window.DFL` or `name in DFL` costs the page AND the log line and leaves the blank panel the check exists to
  tell apart from a dead backend. The version beside the name is an enrichment only (prompt-only) —
  `_versionInfo.current` is internal, guarded, and every sentence is complete without it; `remote` beside it is the
  PUBLISHED version and is never consulted
