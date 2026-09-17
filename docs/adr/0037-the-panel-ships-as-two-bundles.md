# The panel ships as two bundles

## Status

Accepted.

## Context

The panel is built out of `@decky/ui`, and until now the build did not ship it. `@decky/rollup` mapped the package onto
`DFL` — the global Decky Loader installs — and listed it as external, so the artifact contained not one byte of it.
Measured on the shipped `dist/index.js` at `79323dbd`: **546 reads of `DFL.`**. The coexistence bundle this cut keeps is
that same externalisation, and it still measures the same way — `DFL` never declared or assigned anywhere in it, and
**none** of the nine strings that exist only inside the package's own implementation (five of its module-cache machinery
and its logger, four of its component predicates) present at all — the same nine
`frontend/scripts/check-bundle-shape.mjs` probes for.

So the dependency was a **runtime contract with Decky Loader**, not a shipped library. The same held for Steam's React:
`SP_REACT`, `SP_REACTDOM` and `SP_JSX` are not Steam's doing — the loader sets them, and the build maps `react`,
`react-dom` and `react/jsx-runtime` onto them. With the loader stopped, all three are `undefined` and the bundle cannot
load at all.

Bundling the package is therefore what makes the panel loadable without Decky. But it cannot simply be bundled always,
because of what importing it does.

**`@decky/ui` runs `initModuleCache()` at module scope.** The sweep force-executes every module in Steam's live webpack
registry and swallows each failure; a module that throws part-way leaves a cached exports object with no keys behind.
Importing the package a second time in one session therefore re-executes modules that something may already be holding
the exports of.

**What makes that fatal is not the second sweep — it is a consumer already rendering from the modules being
re-executed.** Measured on the device, four passes:

| Pass                                                                           | Result                               |
| ------------------------------------------------------------------------------ | ------------------------------------ |
| Second sweep, desktop client, Decky stopped                                    | survives (14 CEF targets, unchanged) |
| Third sweep, Big Picture open and the Quick Access view mounted, Decky stopped | survives (5 targets, unchanged)      |
| Decky started afterwards into that same session                                | survives, its interface normal       |
| Big Picture + Quick Access + **Decky already rendering**                       | **crash**, reproduced                |

So neither the count nor Big Picture is the condition, and Steam's own interface is not a consumer in the sense that
matters — Decky's is. That also accounts for the error text, which the count never did: a module re-executed underneath
something holding its exports leaves an empty object where a component was, and React says so —
`Minified React error #31`, "Objects are not valid as a React child (found: object with keys {})". It takes the Big
Picture window with it. The same bundle taking the package from Decky's already-loaded copy through `DFL` survives the
identical sequence.

A runtime switch inside one bundle cannot express this. The damage happens at **import**, and ESM evaluates the whole
import graph before any set-up code in the importing module runs — there is no point at which an `if` could stand.

## Decision

**1. The build produces two panel bundles, differing in one thing.**

- `dist/index.js` — **standalone**. `@decky/ui` bundled. For a machine where Decky Loader is not running: our sweep is
  then the first of the session and carries.
- `dist/index-coexistence.js` — **coexistence**. `@decky/ui` external, on `DFL`. For a machine where Decky is running.
  It contains no `initModuleCache` call at all.

**Which one is loaded is the injector's decision and is not made here** — that is
[#1900](https://github.com/danielcopper/romm-tender/issues/1900). What this decision makes is the pair, and two file
names to tell them apart. The name is the whole of the SELECTION mechanism: nothing inside either bundle is read to
choose it, and no runtime probe decides anything.

Each panel bundle is nevertheless stamped by its own build with which of the two it is, and that is a consequence of the
pair rather than a qualification of the sentence above. The stamp is read in one place,
`frontend/src/boot/searchingCopy.ts`, and what it answers reaches the start-up failure page and the log line beside it:
the searches that can go stale are `@decky/ui`'s, the coexistence bundle runs Decky Loader's copy of the package, and so
the same empty search means "update Tender" in one bundle and "update Decky" in the other. The stamp carries no loading
decision, and a runtime probe could not carry this one either — `typeof DFL !== "undefined"` is equally true of a
standalone bundle loaded where Decky happens to be running. Current truth for the answers and their reading of Decky's
namespace lives in [frontend-bundles.md](../architecture/frontend-bundles.md).

**2. A third output installs Steam's React, and it is its own bundle for the same reason.**

`dist/globals.js` (`frontend/src/boot/steamGlobals.ts`) finds Steam's React, ReactDOM and JSX runtime in Steam's own
module registry and sets the three globals. It imports **only** `@decky/ui/dist/webpack` — the module-cache machinery
without the components — because the component half reads React internals while its modules evaluate, and so cannot sit
in the import graph of the module that creates the globals. Decky splits it in the same place, for the same reason.

**3. Our globals are Decky's globals, exactly, and that is enforced rather than intended.**

Decky's loader skips its **entire** globals block when `window.SP_REACT` is already set. So on a machine carrying both,
whichever runs first decides the shape both render through — and a predicate of ours that differs breaks **Decky's**
interface, not ours. `frontend/src/boot/decky-globals-block.txt` pins upstream's block verbatim with its provenance, and
`steamGlobals.test.ts` reads both files as text and holds the four search predicates, their assignments, and the JSX
stand-in against each other.

**4. Tree-shaking is answered by measurement, and the guard is on the artifact.**

`@decky/ui` declares `"sideEffects": false` and then sweeps Steam's whole module registry at import — a false statement
to any bundler. `@decky/rollup` answered this for us and has been removed, so the answer was made again. Three builds of
the standalone bundle, taken at `2c7847de` against rollup 4.62.2 and `@decky/ui` 4.12.0 — the bundle has grown since, so
what these three numbers compare is each other:

| Configuration                                  | `initModuleCache` | Size      |
| ---------------------------------------------- | ----------------- | --------- |
| Rollup's default tree-shaking                  | present           | 759,413 B |
| `preset: "smallest"` (what the preset set)     | present           | 759,092 B |
| every `@decky/ui` module forced side-effectful | present           | 770,208 B |

The sweep survives all three, because it writes the module-level map `findModule` reads and Rollup cannot treat such a
call as removable. Forcing the flag bought no behavioural difference and 10,795 bytes of the package's unused `EResult`
/ `EUIMode` enum tables. So the setting is Rollup's default, and what guards the sweep is
`frontend/scripts/check-bundle-shape.mjs`: a step of its own (`pnpm check:bundle`, run after the build by the gate and
by CI) that fails when the standalone bundle does not carry the package or the coexistence bundle does. That holds
across a Rollup upgrade and a `@decky/ui` bump; a treeshake setting chosen on one measurement would not.

**5. Bundling makes this project a distributor, and the notice travels with the bundle.**

`@decky/ui` is LGPL-2.1, and **two of the three builds ship its code**: `dist/index.js` carries the components and
`dist/globals.js` carries its module-cache half. Only the coexistence bundle carries none of it, taking the package from
Decky's own copy instead.

The standalone build emits the licence text verbatim as `dist/LICENSE-@decky-ui.txt`, and one copy covers the directory
because all three builds write into the same `dist/`. That is an arrangement rather than a licensing judgement, and it
is the premise a later change would break by serving `globals.js` without the standalone bundle beside it.
`THIRD-PARTY-NOTICES.md` names the package, its version and the replacement right.

## Consequences

- **Two artifacts to keep honest.** The bundle-shape check is what keeps them from converging; without it a standalone
  bundle that lost the package throws on its first `DFL.` read on a machine that has no `DFL`, and a coexistence bundle
  that gained it takes the Big Picture window down. Neither is a build error.
- **The standalone bundle is bigger**, by the size of `@decky/ui`. Both have their own size budget in
  `frontend/.size-limit.json`.
- **A `@decky/ui` bump is a device-test trigger**, and now for two reasons rather than one: the predicates it carries,
  and the globals block this project pins beside it.
- **The build takes three passes**, one per output, because the three must share no chunk. On the reference machine that
  is about 40 s against 15 s.
- **The React bootstrap has to be loaded before the panel**, by whatever loads them. Nothing in either file enforces the
  order; the standalone bundle simply throws on its first React read if the bootstrap has not run.

## Alternatives considered

**One bundle with a runtime switch.** The damage is done at import and ESM hoists the import above any set-up code in
the same module, so there is no point at which the switch could stand. This is the alternative the whole decision exists
to rule out.

**Write our own module-search predicates and drop `@decky/ui`.** Rejected on the evidence argued at
[#1899](https://github.com/danielcopper/romm-tender/issues/1899): Steam's output has broken such searches about five
times a year for four years, and upstream absorbs that at no cost here. The shape of the dependency is in
`frontend/src/boot/steamModules.ts`, which classifies every value the panel imports from the package — 33 of them, swept
from the source rather than listed — and finds **25 that are searches into Steam's bundle**, three the check cannot
answer for, and five that are the package's own code. Taking those searches over would mean maintaining 25 predicates
against a minified bundle that moves. The exit stays open per member — `frontend/src/utils/deckyUiInternals.ts` already
reaches Steam's controller-glyph component with a predicate of its own, because the package does not export it.

**Always bundle, and let the coexistence case break.** Rejected: users who keep Decky for other plugins are exactly the
users this epic promises to leave alone, and the failure takes their whole Big Picture window rather than just Tender's
tab.

**Keep `@decky/rollup` and configure around it.** It reads `frontend/plugin.json` unconditionally as its first
statement, hardcodes a single input, a single output directory and a source-map setting, and merges its own defaults
last — so every one of those already had to be overwritten after the fact. Producing three outputs is not something it
can be asked for.

## Related

- [ADR-0036](0036-the-backend-hosts-itself.md) — the backend became its own process and serves this bundle; this is the
  frontend half of the same move.
- [ADR-0035](0035-the-release-builds-no-decky-artifact.md) — the release stopped building a Decky artifact.
