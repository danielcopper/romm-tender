# How the panel is built and loaded

What `pnpm -C frontend build` produces, why there are two copies of the same panel, and what has to have happened before
either can run.

## Four files, three of them code

| File                         | What it is                                                                |
| ---------------------------- | ------------------------------------------------------------------------- |
| `dist/globals.js`            | Steam's React, ReactDOM and JSX runtime, installed under `SP_*`           |
| `dist/index.js`              | The panel, with `@decky/ui` bundled inside it — **standalone**            |
| `dist/index-coexistence.js`  | The panel, taking `@decky/ui` from Decky's `DFL` global — **coexistence** |
| `dist/LICENSE-@decky-ui.txt` | `@decky/ui`'s LGPL-2.1 text, emitted beside the bundle that carries it    |

They are three separate Rollup builds sharing no chunk, which is why the build takes about 40 s rather than 15 s.

`dist/` sits at the repository root rather than under `frontend/`, because it is the seam between the two halves: the
backend serves it as `<code_dir>/dist` and must not reach into the frontend's directory to find it.

## Why two copies of the panel

`@decky/ui` is not a component library. Almost every member is a search predicate over Steam's own minified bundle, and
resolving those predicates means running `initModuleCache()` — a sweep that force-executes **every** module in Steam's
live webpack registry, swallowing each failure. The package runs it at module scope, on import.

**What makes a second import fatal is a consumer already RENDERING from the modules being re-executed** — not the number
of sweeps, and not Big Picture. Four passes on the device:

| Pass                                                                           | Result                               |
| ------------------------------------------------------------------------------ | ------------------------------------ |
| Second sweep, desktop client, Decky stopped                                    | survives (14 CEF targets, unchanged) |
| Third sweep, Big Picture open and the Quick Access view mounted, Decky stopped | survives (5 targets, unchanged)      |
| Decky started afterwards into that same session                                | survives, its interface normal       |
| Big Picture + Quick Access + **Decky already rendering**                       | **crash**, reproduced                |

Steam's own interface is not a consumer in the sense that matters; Decky's is. A module re-executed underneath something
holding its exports leaves an empty object where a component was, which is exactly what React reports:
`Minified React error #31`, "Objects are not valid as a React child (found: object with keys {})". It takes the whole
Big Picture window with it. The same bundle taking the package from Decky's already-loaded copy survives the identical
sequence.

Alone, with no Decky running, nothing else is rendering from those modules and the sweep is harmless.

**A runtime `if` cannot express this.** The damage happens at import, and ESM evaluates the whole import graph before
any set-up code in the importing module runs — there is no point at which the branch could stand. Hence two artifacts.

**Which one is loaded is the injector's decision**, taken from the machine rather than from the window —
[how the panel gets into Steam](loading-the-panel.md#which-bundles-and-the-rule-that-cannot-bend). The file name is the
whole of the SELECTION mechanism: nothing in either bundle is consulted to choose it, and no runtime probe decides
anything.

Each panel bundle does, however, know which of the two it is. `frontend/rollup.config.js` serves
`virtual:tender-bundle-kind` once per panel build with that build's own answer inside, and one module imports it:
`frontend/src/boot/searchingCopy.ts`. What it answers reaches two places — the fallback page and the log line beside it,
both from the single resolution `index.tsx` makes. Whose copy of `@decky/ui` ran a search that came back empty decides
which program the user has to update. It is stamped by the build because a runtime probe cannot answer it:
`typeof DFL !== "undefined"` is also true of a standalone bundle loaded on a machine where Decky happens to be running,
and the page would then credit Decky's copy with work our own copy did. `check-bundle-shape.mjs` asserts each artefact's
stamp, because rollup answers an unresolved import with a warning rather than a failure.

`frontend/scripts/check-bundle-shape.mjs` fails when either bundle stops being what it is — when the standalone one has
lost the package, or the coexistence one has gained it. It is a step of its own, `pnpm -C frontend check:bundle`, run
after the build by `mise run gate` and by CI; the build itself has no opinion. Neither of those is a build error on its
own, and each fails on a device rather than in CI: the first throws on its first `DFL.` read where no `DFL` exists, the
second takes the window down.

## Steam's React, and why it is a separate file

Steam does not define `SP_REACT`, `SP_REACTDOM` or `SP_JSX`. Decky's loader does — and the panel build maps `react`,
`react-dom` and `react/jsx-runtime` onto exactly those names, so with Decky stopped the panel cannot load at all.

`frontend/src/boot/steamGlobals.ts` installs them, from Steam's own module registry. It is its own bundle because
`@decky/ui`'s component half reads React internals while its modules evaluate, so it cannot sit in the import graph of
the module that creates the globals. It imports only `@decky/ui/dist/webpack` — the module-cache machinery without the
components. Decky splits it in the same place, for the same reason.

**Whatever loads these has to load `globals.js` first.** Nothing in either file enforces the order; the panel simply
throws on its first React read if the bootstrap has not run.

**And `globals.js` must not be loaded beside a running Decky.** It imports `@decky/ui/dist/webpack`, whose
`initModuleCache()` is unguarded at module scope, so the bootstrap carries the same sweep the standalone panel bundle
does — `initModuleCache` appears twice in the built file. The short-circuit inside `installGlobals` protects nothing
here: it is a guard in the FUNCTION, and the sweep is in the IMPORT, which ESM runs first. That is a property of the
artefact rather than a decision the build makes; who loads which bundle is
[the injector's](loading-the-panel.md#which-bundles-and-the-rule-that-cannot-bend), and refusing that pairing is the
rule it is built around.

**`GlobalsReport.source` is a fact about one module instance, not about the session.** A second instance of this module
— a re-import, a re-injection — finds the globals already set, finds no `DFL`, and answers `"unknown"` even where the
first instance installed them itself. Nothing carries the attribution across instances, and `"tender"` is returned only
on the path that actually does the installing.

### The one thing that must not drift

Decky's loader skips its **entire** globals block when `window.SP_REACT` is already set. So when Tender installs the
three first, **Decky's whole frontend renders through Tender's shape** — and a search predicate of ours that differs
from Decky's breaks _Decky's_ interface, on a machine whose owner installed Decky for other plugins.

`frontend/src/boot/decky-globals-block.txt` holds upstream's block verbatim with its provenance (repo, the commit that
last touched the file, the commit it was read at, the date). `frontend/src/boot/steamGlobals.test.ts` reads both files
as text and compares the four search predicates, which global each answer is assigned to, and the JSX stand-in's keys
and aliasing. A failure there is a question — which of the two moved? — and never a test to adjust.

## The start-up check

A search predicate Steam has moved past returns `undefined` with nothing thrown. The panel then dies mid-tree, or
renders a hole and says nothing — and **an empty panel looks exactly like a backend that is not running**, which is a
completely different fault with a completely different fix.

So `frontend/src/boot/steamModules.ts` asks every search whether it found something, before anything mounts. Where what
missed is a name the panel renders with, the factory returns a fallback page and registers nothing at all: no route
patch, no launch interceptor, no event listeners, no shortcut relocation. A half-working panel acts on what it cannot
see, and nothing below the check is written to run without the components it was written against.

**Nothing it asks is answered by Steam's runtime state.** Every entry reads Steam's module registry or a global the
React bootstrap installed — one registry, the same one in Big Picture and in the desktop client (the desktop measurement
below answered every registry name) — and not what Steam has mounted or focused. `findSP` is such a name: `@decky/ui`
resolves it from `document.title`, or failing that from the focus controller's active (else last active) context, which
carries no Big Picture tree until Big Picture has been opened. A reading of it when the injector evaluates the bundle
answers for a moment nobody chose, and refusing to mount the panel on that answer reports a fault that does not exist —
which is what happened in the desktop client, measured on the device (#1945). So it is classified as asked-live instead,
and its two consumers in `utils/styleInjector.ts` ask it when the play button mounts and unmounts on a game page, and do
nothing when it answers nothing.

That is also why no mode question arises in the check at all. Every entry is answered by something that does not differ
between Steam's two modes; `findSP` was the only one that did, and it has left the list. (Measured in the desktop client
over the list as it then stood: twenty-eight of twenty-nine answered and `findSP` alone missed. The desktop client's own
surface is [#831](https://github.com/danielcopper/decky-romm-sync/issues/831), and nothing here branches on a mode.)

**Whether every search answered and whether the panel may mount are two questions**, and each entry states which one it
bears on through what its absence costs: the `panel`, a whole `feature` outside it, only its `appearance`, or only a
`diagnostic`. Blocking is the status quo, which costs no evidence to stay at; moving a name off it is a decision taken
per name, against each of its consumers. `ControllerGlyph` costs appearance — `bigpicture/layout/WidePage.tsx` is its
one consumer and already draws `‹ Back` where the glyph would be — and so does `toastClasses`, whose every read is
optional, leaving a toast that says everything it says in an unstyled box. `playSectionClasses` costs a diagnostic: its
one read in the program is inside `gameDetailPatch.tsx`'s one-shot `dumpTree`, which already prints `UNDEFINED` where
the class name would go, so nothing a user can see changes at all. `ToastRenderer`, `NotificationStore` and
`ErrorBoundary` cost a feature: without any one of them no toast appears at all, and every page, every sync and every
download is untouched — the result a toast would have announced is on the page it belongs to. `AppDetailsRoute` and
`appDetailsClasses` cost a feature for the same shape of reason on the other surface: the panel renders whole and
[Steam's game page](#tenders-section-on-steams-game-page) carries no Tender section.

**Only one of the four costs is read by anything.** `checkSteamModules`'s `!== "panel"` decides whether the panel
mounts; `feature`, `appearance` and `diagnostic` are told apart by nothing in the program, so all three record why a
name is off blocking rather than deciding anything. They stay apart because they answer different questions: a whole
function the reader loses is not a decoration whose absence they can see, and neither is a name whose absence nothing
renders at all. Both the notice on Main and the extra log sentence below read the lookup NAMES instead
(`notificationsMissing`, over `NOTIFICATION_LOOKUPS`), so a future `feature` entry for something else cannot make either
claim the notifications are what went missing.

When nothing that missed was needed to render the panel it mounts normally, and **the log line is then the only thing
that reports it at all**: `describeSurvivedMiss` says how many searches missed, that none of them is needed to render
the panel, and then answers the same question the fallback page answers — whose copy of `@decky/ui` ran them. It used to
name "a newer Tender" unconditionally, which was sound only while nothing that could reach it was a name the package
exports; `playSectionClasses` is one, and in the coexistence bundle the search behind it is Decky's.

A miss of something a toast is raised through adds one more sentence ahead of that verdict, because what is gone there
is a whole function rather than a decoration. It states the loss and names **no repair of its own** — the verdict
sentence right after it does, and that one is right under every answer. It has to be: `ErrorBoundary` is a `@decky/ui`
export, so a miss of it alone in the coexistence bundle is Decky's copy's search and the repair named beside it is a
newer Decky Loader.

Both surfaces answer every verdict below; where they come apart is the **repair**. On `none` the log line names one and
the page names no update at all: nothing that missed is a `@decky/ui` export, so every one of them is a lookup Tender
makes itself, and a newer Tender is the repair. The page asks for a report there instead because the three `SP_*`
globals reach its `none` (below); they cannot reach the log line, because their absence costs the panel. **That is the
property `steamModules.test.ts` locks** — a non-blocking name `@decky/ui` does not export must be one Tender resolves
for itself, in one of the three shapes such a lookup is written in: a `find(?:Module|ClassModule)\w*` call, a direct
read of a global off `window`, or a `searchSteamFactories` scan over Steam's module factories — rather than the short
set of names it produces today, which would go stale the moment a second name moved. `mixed` is the other place they
differ: the log line names a repair covering both programs, where the page names Decky's and asks for a report about the
rest, for the same reason.

The page names every search that came back empty, and distinguishes **some** of them missing from **all** of them. All
means something more basic than a stale predicate: the React bootstrap never ran, or Steam's module registry was read
before it was complete — neither is `@decky/ui`'s doing, so that one answer is the same in both bundles. The page words
it as Tender having started before Steam was ready, with restarting Steam as the first thing to try, which is the plain
reading of the second cause and does not rule out the first. The page uses no `@decky/ui`, because a page built out of
searches is the wrong thing to render when a search has missed.

**Some of them missing has five answers, not one, because the predicates are not always ours.** Most belong to
`@decky/ui`, and the coexistence bundle runs Decky Loader's copy of it. `frontend/src/boot/searchingCopy.ts` decides
whose copy ran them, from the build's own stamp and one reading of Decky's namespace, and `steamModules.ts`'s
`searchOwner` turns that into ONE verdict, which `describeFailure` words for the page and `describeSurvivedMiss` for the
log line:

| Verdict        | Bundle      | What was read                                                                         | What the page says                                                                                                                  |
| -------------- | ----------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `none`         | either      | no missed name is a package export                                                    | Tender could not find the parts it needs from Steam. **No update is named**; it asks for a report — see below.                      |
| `tender`       | standalone  | —                                                                                     | Steam has changed and this version of Tender does not know its way around it. **Update Tender.**                                    |
| `disagreement` | coexistence | a missed name is **not in** `DFL`                                                     | Tender and Decky Loader are out of step: Tender asks Decky's shared part for things it does not have. **Update both.**              |
| `mixed`        | coexistence | some missed names are not package exports, and Decky's copy carries every one that is | Steam has changed and Decky Loader does not know its way around it. **Update Decky Loader**; if the page persists, report the rest. |
| `decky`        | coexistence | every missed name is **in** `DFL`                                                     | The same, and Decky's own menu and its other plugins are affected too. **Update Decky Loader.**                                     |

**The verdict is read once and worded twice**, rather than branched at each surface. The page and the log line answer
the same question about the same machine, and when they each decided it for themselves the copies drifted: the log line
had the `mixed` case and the page did not, so a missed `SP_REACTDOM` beside any `@decky/ui` name fell through to `decky`
and told the user Decky's copy had run searches Decky ran none of.

**The order is a precedence, not a tally**, and `disagreement` is asked before `mixed` on purpose: a name Decky's copy
does not export is a fact about the two installs, where a name it exports with an empty value is a search result whose
cause — Steam having moved what the predicate looks for — is inferred; and bringing the pair to current repairs whatever
else went stale beside it. That is why its sentence does not claim the disagreement is the whole of what happened.
`frontend/src/boot/steamModules.test.ts` pins the precedence and the join — every verdict must reach a distinct sentence
on each surface, so a case one of them loses is reported rather than absorbed by its neighbour.

`mixed` settles both halves rather than neither, and its sentences say so. Decky's copy carries every name asked of it
and its searches for them still came back empty, so that copy is demonstrated stale in every `mixed` reading on either
surface. What the REST demonstrates is where the two surfaces part, and the locked property above is what decides it: a
non-blocking name the package does not export must be one Tender probes for itself, so on the log line the rest is
always Tender's own probe and that line names both programs. On the page it may be an `SP_*` global instead, which
belongs to no copy and has no update to name, so the page names Decky's and asks for a report about the rest, naming no
party for it: that rest can be `ControllerGlyph`, whose predicate is ours, or a global, which in the coexistence bundle
— the only one `mixed` arises in — Decky Loader has installed.

The `none` row is asked first and is about neither copy. Several of the names checked are not `@decky/ui` lookups at
all, and they come in three kinds: the three `SP_*` globals, which a React bootstrap installs; a global Steam installs
itself (`NotificationStore`); and the searches Tender runs for itself — `utils/deckyUiInternals.ts`'s `findModule`,
`findModuleExport` and `findClassModule` probes, and the scan over Steam's module factories behind the game page's
route. Which names those are is not a list kept here: they are `STEAM_LOOKUPS`'s `truthyUnexported` entries, and the
flag on each is what the row reads. A miss confined to them belongs to no copy of the package, and the row names none.
For the globals that is also what stops a sentence sending the user after a program that did nothing: in the coexistence
bundle the copy it would name is Decky's, and Decky's copy ran none of these searches.

**The row names no update either, and the two kinds of name behind that are not in the same position.** For the globals
there is no repair to offer. Which program installed them on a machine running both now HAS an answer — the injector
loads `globals.js` only where Decky Loader is not serving, so beside a serving Decky those globals are Decky's
([how the panel gets into Steam](loading-the-panel.md#which-bundles-and-the-rule-that-cannot-bend)) — and **this page
does not read it**: the branch keys on whose copy ran the search, not on which program installed a global, so it still
names no update. In the standalone bundle having the answer would not settle it anyway: a missing `SP_REACTDOM` there is
either `dist/globals.js` never having run (a load-order fault) or the ReactDOM predicate in `boot/steamGlobals.ts`
having gone stale (a version fault, whose repair is a newer Tender), one symptom over two repairs. `ControllerGlyph` is
the opposite case. Its predicate is **ours in both bundles**, so "update Tender" is its correct repair, and this row
cannot say so — the price of keying the branch on whose COPY ran the search, which buying back takes a third axis, whose
PREDICATE, rather than a reworded row. What that costs is bounded, because the glyph reaches this row only **alongside**
a global, whose silence is right anyway: on its own it costs appearance alone and brings no page up, and beside a
package name it is the `mixed` row's unnamed rest rather than this one's. Its own sentence is the log line above.

`SP_REACTDOM` is the only name that can reach this row alone — `SP_REACT` and `SP_JSX` are read while the panel bundle
is being evaluated, so with either unset the bundle throws at import and the check never runs.

`window.DFL` is an ESM module namespace object, so `in` is what discriminates — measured on the device against Decky
v3.2.8, where `"DialogButton" in DFL` is true and a generated non-existent name is false. It is put only about the names
that get past the `none` row — the package's own exports — because a Decky in perfect step with us carries none of the
other four, and asking would report a package disagreement on every miss. Those names are asked one at a time and the
first absent one settles it: one absent name is enough for `disagreement`, so the rest are never put. A name Decky's
copy does not export is a demonstrated fact about the two installs, where a name it exports with an empty value is a
search result whose cause is inferred, and the repair `disagreement` names covers both.

Every part of that reading is guarded and every guard falls the same way: no `DFL` to question, a `DFL` whose read
throws, a name the question itself throws on — none of them claims anything. An absence has to be demonstrated, and a
throw demonstrates nothing. The guards are not decoration: `definePlugin`'s factory reads this before it returns
anything, so a throw would take the failure page and the log line with it and leave exactly the blank panel the check
exists to tell apart from a backend that is not running.

Decky's version enriches the sentence and is never required for it. It is read from
`DeckyPluginLoader.deckyState._versionInfo.current`, an internal field behind an underscore, and a newer Decky renaming
it is exactly the skew being diagnosed — so the whole path is guarded and a failure simply drops the version from the
sentence. `remote` beside it is release data about the published version and is never consulted: a release existing does
not mean this machine installed it.

**Two of the names the panel imports cannot be answered for**, and they are listed with the reason rather than left out:
`DropdownItem` and `showModal` are wrappers the package always defines, while the lookup each one reaches inside is
module-private, so the export is truthy whether or not that search found anything. `steamModules.test.ts` sweeps every
value the panel imports from `@decky/ui` and fails on a name that is in none of its four lists — a search this check
asks, a name it cannot answer for, a name answered by Steam's runtime state, or the package's own code — so a new import
has to be classified before it can ship.

**The runtime-state list is the one the test derives rather than trusts.** Which axis a name is answered on is a
property of `@decky/ui`'s implementation, not of our opinion about it, so the test reads it there: it walks the
package's shipped `dist/` for exported functions that reach `getGamepadNavigationTrees`, `getFocusNavController` or
`document.title`, following module-private helpers within a file — which is what carries `useQuickAccessVisible` through
its own `getQuickAccessWindow` — and then requires that set, intersected with what the panel imports, to be exactly the
list. Both directions, so a live name cannot be left off it and a registry search cannot be parked on it. What the sweep
cannot see is a name that reaches those readers through an arrow export, a re-export, or another module's helper:
`showModal` is one, calling `findSP() || window` from `dist/components/Modal.js`. What the sweep cannot see it says
nothing about: such a name can sit in the checked list unflagged, which is the shape this cut removed by hand. What the
narrowness cannot do is put a registry search onto the live list in silence — a name the sweep did not derive fails the
equality there.

Reading the package's own source needs a scanner rather than a regex, and `frontend/src/test-utils/jsFunctionScanner.ts`
is it: a brace inside a string literal is not structure, and `@decky/ui`'s `createPropListRegex` opens with `"const\{"`,
which sends a counting regex to the end of its file and credits it with every read below it, without anything failing.
The scanner skips strings, comments and regex literals, balances the parameter list, and throws on an opener it cannot
close instead of answering to the end of the file — an unterminated string is not judged on its own: inside a body it
still ends in that throw, at top level the scan simply ends. Regex literals are told from division by what significantly
precedes the `/`, and the limit that matters is that `)` and `]` are absent from that set, so a regex written directly
after a parenthesised expression would be scanned as code, with the same consequences its quotes and braces have
anywhere else. Nothing can see that from inside; against the installed package it is pinned by the sweep named "reads no
slash in the installed `@decky/ui` as division with a second slash after it on the line, which is the one shape the
regex heuristic cannot tell apart".

Those lists are about VALUES, and the `in` reading above is a different axis — but the two do not meet, because the `in`
question is only ever put about a name whose search missed, and the check asks none of the four names in those two
lists, so they are never among the missing. Asking `in` of them would be a check this tree does not have: whether
Decky's copy carries a name whose value this check never reads.

## Talking to the backend

`frontend/src/api/host.ts` is what the panel imports for everything `@decky/api` used to give it, under five of the same
names — `callable`, `addEventListener`, `removeEventListener`, `toaster`, `definePlugin` — so a call site reads the same
as before. `@decky/api` itself is gone from the package, and so is the sixth name it forwarded: `routerHook` was Decky
Loader's route installer, and Tender's section reaches Steam's game page through a seam of its own instead
([below](#tenders-section-on-steams-game-page)).

**Three of the five are the wire.** `callable`, `addEventListener` and `removeEventListener` go through
`frontend/src/api/hostSocket.ts`, one WebSocket per bundle instance, on the protocol defined once on the other side in
`backend/host/protocol.py`. The port and the token are read off the URL this bundle was loaded from: the host mints
exactly that address, so they arrive with the code that needs them and cannot be stale.

Three properties are worth knowing before changing anything there:

- **A transport failure is thrown, never returned.** `error.reason` names something that went wrong _carrying_ a call; a
  callable's own failure is a perfectly successful transport and arrives inside `result` as
  `{success, reason,
  message}`. The frontend keeps them apart by throwing `HostTransportError` for the first, so it
  cannot reach a reader of the second.
- **There is no timeout.** A call made while the socket is down waits for it to come back. That is the contract
  `@decky/api`'s `callable` had and 150 call sites are written against it — `index.tsx` races its own deadline around
  the calls that must not wait.
- **A dropped connection fails the calls that were already sent, and only those.** A frame still queued never left, so
  re-sending it is safe; one already on the wire may have run, and retrying it would repeat whatever it did.

**A fourth opens no socket and is the one the panel reaches the screen through.** `definePlugin` answers with the
factory unchanged; `index.tsx` hands that factory to `frontend/src/qam/installEntry.tsx`, which calls it exactly once
and mounts what it answers with behind Tender's own Quick Access entry ([qam-panel.md](qam-panel.md) → The entry). The
seam is arranged that way so this module stays the wire and reaches no view — the name is upstream's contract and the
declaration is all of it that belongs here. Under Decky Loader the call was Decky's; nothing else in the tree makes it,
so without that line the panel is built for nobody.

**The fifth reaches Steam instead.** `toaster` was Decky Loader's own, and `@decky/api` only forwarded it, so it needs a
replacement of Tender's rather than a backend route: `utils/steamToaster.tsx` pushes a notification into Steam's own
`NotificationStore`, which then owns the popup window and its animation, the queue behind it, the sound, and the entry
left in the Quick Access notifications tab.

The toaster is two halves and the push is useless without the other one. Steam's renderer has no `case` for the type
these notifications carry; its `default` arm resolves to Steam's server-notification component, which reads fields our
payload does not have. So the drawing is put in front of that component before anything is pushed, and a toast the
drawing cannot be installed for is **logged instead of pushed**. Three lookups have to answer for that to work — the
renderer itself, the `NotificationStore` to push into, and `@decky/ui`'s `ErrorBoundary`, which keeps a throw inside our
own drawing from reaching the tree that draws everyone's notifications — and a miss of any one of them stops the push.

That component is patched by replacing `prototype.render`, and `@decky/ui`'s `injectFCTrampoline` — which is how Decky
Loader patches the same component — overwrites that property outright with no guard against a second application:
whoever applies it second orphans the first, in either order and in both bundles. So Tender never installs over a chain
it has not just read. It wraps whatever `render` it finds, and looks again before every push; a check at push time is
enough because a toast is drawn only after it is pushed, so no accessor trick and no observer is needed. A link it
replaces is retired as it goes, because whatever overwrote it may still delegate through it. At dismount it puts back
what it found where its own link is still on top, and turns that link into a pass-through where somebody wrapped over it
— cutting it out there would take the later patcher's drawing with it.

There are three layouts, chosen from the `location` Steam passes the renderer, and all of their class names come from
one map (`findClassModule((m) => m.ShortTemplate)`): several class maps carry these template names and exactly one
carries `ShortTemplate`. Steam's own values, from `library.js`'s function `Bn` (reached as `ey3`), which turns the prop
into a telemetry submethod name over `0 invalid, 1 gamepad, 2 desktop, 3 tray, 4 all, 5 push`: **1** is the Big Picture
popup, **2** the desktop-client popup and **3** the Quick Access notifications tab. Location 3 has a second consumer,
the desktop client's own notifications menu — but that menu skips client-sourced entries, which is what a Tender toast
is, so a kept toast is listed in the Quick Access tab and nowhere else. The Big Picture popup is a native window of a
fixed size that clips (innerWidth/innerHeight 321x81, `body { overflow: hidden }`, read over the debugger), so it gets
Steam's two-line short template and carries no subtext; the desktop popup and the tab entry carry one. An unrecognised
location falls back to the tab layout, which is the only one that imposes no size of its own. A toast is **kept** in the
tab only where it carries subtext — the popup had no room to show that, so there is something new to read there; without
one the entry would be a row the reader has to clear for nothing. In that entry the subtext wraps in full: Steam's own
rule for the line ends it after one line, or two with `Multiline`, and a reason cut to two lines was the thing the tab
was chosen to avoid. The entry grows with it — Steam's template is a fixed 50 px, which a wrapped subtext would run out
of over the next row.

The toaster does not reach Decky's loader API when one happens to be present, and what decides that is not purity: it
was the loader's own, and one that borrowed wherever it found one would behave differently on a machine running Decky
from one without — the difference this program exists not to depend on. The game-page seam below is the same rule
applied to the other name the loader used to answer for. Tender's own marker on a notification is deliberately not
Decky's `decky`, for the same reason read the other way: two programs marking their entries with one name would each
draw the other's. What Decky Loader does here is read from its own source, `frontend/src/toaster.tsx` on upstream
`main`.

## Tender's section on Steam's game page

Steam's game page is not ours to compose: `bigpicture/patches/gameDetailPatch.tsx` swaps Steam's own app-details
overview panel for Tender's play section and game-info panel, on RomM shortcuts only. Where that patch is INSTALLED is
`bigpicture/patches/installGamePagePatch.ts`, and the seam it uses is the one thing about it worth reading twice.

**The seam is the route component's `renderFunc`, never the page component's own `type`.** Steam's gamepad router
renders the library route inline —
`<Route path="/library/app/:appid">{<RouteComponent renderFunc={renderPage} />}</Route>` — so the page is reached
through a prop rather than through a module export. The install puts a `beforePatch` on the route component's memo
`type`, reads the props React is about to render it with, and wraps `renderFunc` on each props object it has not seen.

Patching the PAGE component instead looks simpler and does not work beside Decky Loader. `@decky/ui`'s tree patcher
caches the wrapped component per ORIGINAL type (`dist/utils/react/treepatcher.js`, `handleStep`), and every later render
goes through that cached copy — whose chain bottoms out at whatever the page component's `type` was when the first
plugin wrapped it. So a patch installed on that `type` afterwards is never entered again, which is exactly the ordinary
case: Tender's backend starts after Steam has been running, so Decky's plugins have already wrapped the page. On the
`renderFunc` seam the two compose the way Decky's plugins compose among themselves — each side wraps whatever it finds
and caches its own copy — and the order the wrappers end up in follows from which program installed first.

**The props object is the identity.** Decky Loader's router hook clones a route's child as
`(props) => createElement(originalType, props)`, so beside a Decky the route component is handed a fresh props object on
every render; a guard kept per component, or per `renderFunc`, would wrap the first render and no other. Nothing is
retained per props object either: the tree patcher's own cache bounds the work to one wrap per original component type,
and a props object dies with the element it was made for. What the teardown takes back is the patch on the memo.

**The module is found by three property names in a factory's source.** Steam's webpack `require` is obtained the way
`@decky/ui` obtains it — pushing a chunk keyed by a fresh Symbol, whose factory is handed it — and a module factory's
source text is read from `require.m`. The factory carrying all three of `renderFunc`, `AppDetailsOverviewPanel` and
`InnerContainer` is the one; its exports come from `@decky/ui`'s `modules` map, which is Decky's copy of the registry in
the coexistence bundle and ours in the standalone one. Property names survive Steam's minification and local identifiers
and module ids do not, which is why the predicate reads names and never a number — matching a number also matches SVG
path coordinates. Every memo export of that module whose `type` is a function is patched, rather than the route picked
out of them: nothing on an export says which one it is, and on any other export the handler finds no `renderFunc` and
does nothing. Two matching factories are treated as no match at all, because a second match means the predicate no
longer names one module.

**Whose sources are read is decided first, by shape.** Reading the source text of every module Steam ships is not free,
and this answer is wanted before the panel mounts — the start-up check asks for it. So a cheap pass goes first: over the
values already in `modules`, it collects the modules whose exports are ALL patchable memos and number more than one,
which is what the route's module looks like, and only those factories' sources are read. The full scan over `require.m`
stands behind it and runs only when the cheap pass names nothing — for the day Steam gives that module an export of
another kind, or splits it.

**The two passes can disagree, and where they do the shape pass is the one to believe.** The refusal a second match
earns is taken per pass, and the shape pass reads a subset, so where two modules carry all three property names and only
one of them has the shape, the shape pass answers and the full scan alone would refuse. That refusal exists because the
names no longer name one module; a shape only one of the two has settles exactly that, and the module it answers with
has satisfied both predicates where the others satisfied one. So the answer rests on more evidence than the refusal it
replaces, not less. What it costs is the case where the shape is the misleading half — a second module carrying those
names while the real one has lost the shape, which one split of that module could produce on its own. Then a module that
draws no route is patched: its memos are never rendered with a `renderFunc`, so nothing is wrapped and the game page
carries no Tender section, exactly as a refusal would leave it — and the start-up check reports the route as found
rather than missing, which is the one thing a refusal would have said out loud.

**The desktop client needs no gate.** Its library router renders the same route component with no `renderFunc` at all,
so the handler wraps nothing there — the same parity the old route patch had, since that one only ever ran on the
gamepad route. The in-game overlay routes render a different component again.

**A page that is already open is adopted.** React resolves a `memo` when it mounts and carries the resolved function on
the fiber, so replacing the export afterwards reaches nothing on screen. The install therefore walks the live fiber tree
from the host root's `current` fiber — not the fiber hanging off the container element, which can be the twin React is
not rendering — and, for every fiber whose `elementType` is one of the patched memos, points `type` back at the memo's
own (on the alternate too). The adopted fiber picks the patch up at its next render, which in practice is the next
navigation.

**A miss costs a feature, not the panel.** The start-up check asks this search under the name `AppDetailsRoute` and it
is one of Tender's own in both bundles, since `@decky/ui` has no reader of factory sources. With it missing every page
of the panel renders and works and Steam's game page carries no Tender section; `appDetailsClasses` costs the same
thing, because every read of it is in that same patch. The registration in `gameDetailPatch.tsx` logs a line of its own
naming the game page, for whoever reads the log with the game page in mind.

## What the tests here can and cannot see

The frontend suite replaces `@decky/ui` with a stub — 33 files plus a global mock in `frontend/src/test-setup.ts` — so
**no line of the real package executes in any test**. Everything about bundling, `initModuleCache` and module lookup is
invisible to a green suite. That is why the bundling half is checked against the built artifact instead, and why
`index.test.tsx` supplies the start-up check's answer rather than letting the stub produce one: under the stub almost
every search answers `undefined`, exactly as a Steam that had moved them would.

The same is true of the transport. `test-setup.ts` stubs `api/host` wholesale, so **no socket is opened anywhere in the
suite** and no line of `hostSocket.ts` runs through it; that module is covered by `hostSocket.test.ts` alone, against a
socket that file supplies. What neither reaches is the real wire: nothing here has ever carried a frame between this
code and `backend/host/`, and the first thing that will is the device.

## Related

- [How the panel gets into Steam](loading-the-panel.md) — which of these files is loaded, and by what.
- [ADR-0037](../adr/0037-the-panel-ships-as-two-bundles.md) — the decision, its measurements and the alternatives.
- [ADR-0036](../adr/0036-the-backend-hosts-itself.md) — the backend became its own process and serves these files.
- [QAM panel](qam-panel.md) — what the panel renders once it has mounted.
