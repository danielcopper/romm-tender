# `desktop/` — the desktop-client surface

This directory is where the desktop client's UI goes. It holds this README and nothing else today. It is the peer of
`bigpicture/`, which holds the gamepad surface — the QAM panel and the patch into Steam's game-detail route.

**Nothing here reaches the shipped bundle.** `rollup.config.js` sets one entry, `./frontend/src/index.tsx` — the module
that mounts the bigpicture surface — so a file added here ships nowhere until someone imports it from a bundled module
or gives this surface an entry of its own. It is checked all the same, and each check has its own reason: `pnpm lint`
runs `eslint .` over the repository, and `tsconfig.json` includes the whole of `frontend/src`, so a file here is linted
and type-checked wherever it sits. Vitest is the one to read carefully — it collects only `*.{test,spec}.{ts,tsx}`, so a
plain module here runs in no test, but it does fall inside the coverage include glob (`frontend/src/**/*.{ts,tsx}` in
`vitest.config.ts`), which is what puts an untested file on the coverage report.

The two are **peers, not layers**. They share data and logic and almost nothing visual: the same reads, the same stores,
the same vocabulary, drawn for a controller on one side and for a keyboard and mouse on the other.

## What this surface may import

- `../api/` — the callable wire to the backend
- `../utils/` — shared logic and the module stores
- `../types/` — the shared wire and domain types

## What it may not

- Nothing here may import from `../bigpicture/`, and nothing there may import from here.

Anything that turns out to belong to both surfaces moves **down** into `api/`, `utils/` or `types/` — never sideways.
Enforced by `import-x/no-restricted-paths` in `eslint.config.js`, whose zones are held to reporting by
`../eslintBoundaries.test.ts`.
