# `desktop/` — the desktop-client surface

This directory is the desktop surface: what Tender draws into Steam's desktop client. It is the peer of `bigpicture/`,
which holds the gamepad surface — the QAM panel and the patch into Steam's game-detail route. How it adapts Steam's
page, and the rules every change to that page follows, are in
[How the desktop surface adapts Steam's game page](../../../docs/architecture/desktop-dom-architecture.md).

- `index.ts` — `startDesktopSurface` / `stopDesktopSurface`: the one place that decides what the navigation watcher
  draws on a RomM game page.
- `desktopWindow.ts` — finding the desktop client window, and React's `createRoot`.
- `navigationWatcher.ts` — watches the desktop client's location and DOM, adapts Steam's game page and mounts the views
  it is given (`DesktopGamePage`); it imports no view.
- `watcher/` — what the navigation watcher adapts Steam's page with: `elementSelectors.ts` (finding Steam's elements),
  `fiberInspector.ts` (read-only Fiber reads), `restorationLedger.ts` (recording every change so it can be undone),
  `stickyPlayBarController.ts` (the play bar's pinned and glass states).
- `gameview/GameView.tsx` — what `index.ts` hands the watcher as the page's `gameView`: the page's cards in two columns.
  The About card (`gameview/AboutDetails.tsx`) is the one card so far.
- `gameview/styles.ts` — the play bar's glass and pinned colours, which `stickyPlayBarController.ts` applies, and the
  cards' style.

**Dev build only.** `rollup.config.js` builds the panel from `./src/index.tsx` — the module that mounts the bigpicture
surface — and nothing there imports this directory, so `pnpm build`, `build:dev`, CI and the release tarball ship none
of it. The one build that does is `build:desktop` (`rollup.desktop.config.js`), which rewrites `index.tsx` as it is
bundled to call `startDesktopSurface()`
([the desktop dev build](../../../docs/architecture/frontend-bundles.md#the-desktop-dev-build-dev-build-only)). How to
run it is in
[Frontend dev loop](../../../docs/contributing/frontend-dev-loop.md#desktop-surface-dev-loop-dev-build-only).

It is checked all the same, and each check has its own reason: `pnpm lint` runs `eslint .` over the frontend package,
and `tsconfig.json` includes the whole of `src`, so a file here is linted and type-checked wherever it sits. Vitest is
the one to read carefully — it collects only `*.{test,spec}.{ts,tsx}`, so a plain module here runs in no test, but it
does fall inside the coverage include glob (`src/**/*.{ts,tsx}` in `vitest.config.ts`), which is what puts an untested
file on the coverage report.

The two are **peers, not layers**. They share data and logic and almost nothing visual: the same reads, the same stores,
the same vocabulary, drawn for a controller on one side and for a keyboard and mouse on the other.

## What this surface may import

- `../shared/` — UI that belongs to both surfaces
- `../api/` — the wire to the backend (endpoints and events)
- `../utils/` — shared logic and the module stores
- `../types/` — the shared wire and domain types

## What it may not

- Nothing here may import from `../bigpicture/`, and nothing there may import from here.
- Nothing in `../shared/` may import from here.
- Nothing here may import from `../qam/` or `../boot/`, which only `../index.tsx` and its test import.

Anything that turns out to belong to both surfaces moves **down** — UI into `shared/`, the rest into `api/`, `utils/` or
`types/` — never sideways. Enforced by `import-x/no-restricted-paths` in `eslint.config.js`, whose zones are held to
reporting by `../eslintBoundaries.test.ts`.
