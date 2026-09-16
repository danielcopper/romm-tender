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

Beside a **running** Decky Loader, whose own sweep already ran earlier in the session over a smaller module set, a
second sweep leaves cached exports objects with no keys behind. Opening the Quick Access menu then dies with
`Minified React error #31` and takes the whole Big Picture window with it — measured four times on the device. The same
bundle taking the package from Decky's already-loaded copy survives the identical sequence.

Alone, with no Decky running, our sweep is the first of the session and carries.

**A runtime `if` cannot express this.** The damage happens at import, and ESM evaluates the whole import graph before
any set-up code in the importing module runs — there is no point at which the branch could stand. Hence two artifacts.

**Which one is loaded is the injector's decision** ([#1900](https://github.com/danielcopper/romm-tender/issues/1900))
and is made nowhere in this tree today. The file name is the whole of the mechanism: no flag in the bundle, no marker,
no runtime probe.

`frontend/scripts/check-bundle-shape.mjs` fails the build when either bundle stops being what it is — when the
standalone one has lost the package, or the coexistence one has gained it. Neither of those is a build error on its own,
and each fails on a device rather than in CI: the first throws on its first `DFL.` read where no `DFL` exists, the
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

So `frontend/src/boot/steamModules.ts` asks every search whether it found something, before anything mounts. On a miss
the factory returns a fallback page and registers nothing at all: no route patch, no launch interceptor, no event
listeners, no shortcut relocation. A half-working panel acts on what it cannot see, and nothing below the check is
written to run without the components it was written against.

The page names every search that came back empty, and distinguishes **some** of them missing from **all** of them — that
is the single fact that leads to a repair. Some means a Steam client update moved what those predicates match. All means
something more basic: the React bootstrap never ran, or Steam's module registry was read before it was complete. The
page uses no `@decky/ui`, because a page built out of searches is the wrong thing to render when a search has missed.

**Three of the names the panel imports cannot be answered for**, and they are listed with the reason rather than left
out: `DropdownItem`, `showModal` and `useQuickAccessVisible` are wrappers the package always defines, while what each
reaches is module-private or read during render. `steamModules.test.ts` sweeps every value the panel imports from
`@decky/ui` and fails on a name that is in none of its three lists, so a new import has to be classified before it can
ship.

## What the tests here can and cannot see

The frontend suite replaces `@decky/ui` with a stub — 33 files plus a global mock in `frontend/src/test-setup.ts` — so
**no line of the real package executes in any test**. Everything about bundling, `initModuleCache` and module lookup is
invisible to a green suite. That is why the bundling half is checked against the built artifact instead, and why
`index.test.tsx` supplies the start-up check's answer rather than letting the stub produce one: under the stub almost
every search answers `undefined`, exactly as a Steam that had moved them would.

## Related

- [ADR-0037](../adr/0037-the-panel-ships-as-two-bundles.md) — the decision, its measurements and the alternatives.
- [ADR-0036](../adr/0036-the-backend-hosts-itself.md) — the backend became its own process and serves these files.
- [QAM panel](qam-panel.md) — what the panel renders once it has mounted.
