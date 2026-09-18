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

That is also why no mode question arises in the check at all. Twenty-eight of the twenty-nine entries were answered by
something that does not differ between Steam's two modes; `findSP` was the only one that did, and it has left the list.
(Measured in the desktop client, where twenty-eight of twenty-nine answered and `findSP` alone missed. The desktop
client's own surface is [#831](https://github.com/danielcopper/decky-romm-sync/issues/831), and nothing here branches on
a mode.)

**Whether every search answered and whether the panel may mount are two questions**, and each entry states which one it
bears on through what its absence costs: the `panel`, only its `appearance`, or only a `diagnostic`. Blocking is the
status quo, which costs no evidence to stay at; moving a name off it is a decision taken per name, against each of its
consumers. Two names have been moved so far. `ControllerGlyph` costs appearance — `bigpicture/layout/WidePage.tsx` is
its one consumer and already draws `‹ Back` where the glyph would be. `playSectionClasses` costs a diagnostic: its one
read in the program is inside `gameDetailPatch.tsx`'s one-shot `dumpTree`, which already prints `UNDEFINED` where the
class name would go, so nothing a user can see changes at all. **Nothing in the program branches on the difference
between those two costs** — `checkSteamModules`'s `!== "panel"` is the field's only reader — so the value records why a
name was moved off blocking rather than deciding anything. They stay apart because they answer different questions: a
decoration whose absence a reader can see is not a name whose absence nothing renders at all.

When nothing that missed was needed to render the panel it mounts normally, and **the log line is then the only thing
that reports it at all**: `describeSurvivedMiss` says how many searches missed, that none of them is needed to render
the panel, and then answers the same question the fallback page answers — whose copy of `@decky/ui` ran them. It used to
name "a newer Tender" unconditionally, which was sound only while nothing that could reach it was a name the package
exports; `playSectionClasses` is one, and in the coexistence bundle the search behind it is Decky's.

Both surfaces answer every verdict below; where they come apart is the **repair**. On `none` the log line names one and
the page names none at all: nothing that missed is a `@decky/ui` export, so every one of them is a search Tender runs
with a module probe of its own, and a newer Tender is the repair. The page has to stay silent there because the three
`SP_*` globals reach its `none` (below); they cannot reach the log line, because their absence costs the panel. **That
is the property `steamModules.test.ts` locks** — a non-blocking name `@decky/ui` does not export must be one Tender
probes for itself — rather than the short set of names it produces today, which would go stale the moment a second name
moved. `mixed` is the other place they differ: the log line names a repair covering both programs, where the page names
Decky's and stays silent about the rest, for the same reason.

The page names every search that came back empty, and distinguishes **some** of them missing from **all** of them. All
means something more basic than a stale predicate: the React bootstrap never ran, or Steam's module registry was read
before it was complete — neither is `@decky/ui`'s doing, so that one answer is the same in both bundles. The page uses
no `@decky/ui`, because a page built out of searches is the wrong thing to render when a search has missed.

**Some of them missing has five answers, not one, because the predicates are not always ours.** Most belong to
`@decky/ui`, and the coexistence bundle runs Decky Loader's copy of it. `frontend/src/boot/searchingCopy.ts` decides
whose copy ran them, from the build's own stamp and one reading of Decky's namespace, and `steamModules.ts`'s
`searchOwner` turns that into ONE verdict, which `describeFailure` words for the page and `describeSurvivedMiss` for the
log line:

| Verdict        | Bundle      | What was read                                                                         | What the page says                                                                                                                          |
| -------------- | ----------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `none`         | either      | no missed name is a package export                                                    | Neither copy of `@decky/ui` ran these searches. **No repair is named** — see below.                                                         |
| `tender`       | standalone  | —                                                                                     | Tender's own copy searched and missed. **Update Tender.**                                                                                   |
| `disagreement` | coexistence | a missed name is **not in** `DFL`                                                     | Decky's copy does not carry the export: two separately installed programs disagreeing about the package. **Bring both to current.**         |
| `mixed`        | coexistence | some missed names are not package exports, and Decky's copy carries every one that is | Decky's copy ran **some** of them and missed — update Decky Loader for those; the rest are not `@decky/ui` names, so neither copy ran them. |
| `decky`        | coexistence | every missed name is **in** `DFL`                                                     | Decky's copy searched and missed — its own interface and its other plugins are affected the same way. **Update Decky Loader.**              |

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
belongs to no copy and has no repair to name, so the page names Decky's and stops there.

The `none` row is asked first and is about neither copy. Four of the names checked are not `@decky/ui` lookups at all —
the three `SP_*` globals, which a React bootstrap installs, and `ControllerGlyph`, which `utils/deckyUiInternals.ts`
reaches with a `findModule` predicate of its own — so a miss confined to those belongs to no copy of the package, and
the row names none. For the globals that is also what stops a sentence sending the user after a program that did
nothing: in the coexistence bundle the copy it would name is Decky's, and Decky's copy ran none of these searches.

**The row names no repair either, and the two kinds of name behind that silence are not in the same position.** For the
globals there is no repair to offer. Which program installed them on a machine running both now HAS an answer — the
injector loads `globals.js` only where Decky Loader is not serving, so beside a serving Decky those globals are Decky's
([how the panel gets into Steam](loading-the-panel.md#which-bundles-and-the-rule-that-cannot-bend)) — and **this page
does not read it**: the branch keys on whose copy ran the search, not on which program installed a global, so it still
names no repair. In the standalone bundle having the answer would not settle it anyway: a missing `SP_REACTDOM` there is
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

`frontend/src/api/host.ts` is what the panel imports for everything `@decky/api` used to give it, under the same six
names — `callable`, `addEventListener`, `removeEventListener`, `toaster`, `routerHook`, `definePlugin` — so a call site
reads the same as before. `@decky/api` itself is gone from the package.

**Four of the six are the wire.** They go through `frontend/src/api/hostSocket.ts`, one WebSocket per bundle instance,
on the protocol defined once on the other side in `backend/host/protocol.py`. The port and the token are read off the
URL this bundle was loaded from: the host mints exactly that address, so they arrive with the code that needs them and
cannot be stale.

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

**Two of the six are not the wire at all.** `toaster` and `routerHook` were Decky Loader's own, and `@decky/api` only
forwarded them. Their replacements are [#1901](https://github.com/danielcopper/romm-tender/issues/1901) — a toaster
through Steam's own notification store, and the game-page patch installed by Tender's own installer — so until then both
are **declared placeholders that do nothing**: no toast appears, and Steam's game page carries no Tender section.

Neither reaches Decky's loader API when one happens to be present, and what decides that is not purity: both are the
loader's own, [#1901](https://github.com/danielcopper/romm-tender/issues/1901) replaces them with Tender's, and a
placeholder that borrowed one wherever it found one would behave differently on a machine running Decky from one without
— the difference this program exists not to depend on.

**The reference machine runs Decky Loader** — measured while building the injector: `plugin_loader.service` active and
enabled, and `127.0.0.1:1337` listening. So a borrowing placeholder would be visible there rather than hidden, which is
the opposite of what this page said while it recorded the machine as having Decky installed but disabled.

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
