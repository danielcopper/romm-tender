import { copyFileSync, mkdirSync } from "node:fs";

import commonjs from "@rollup/plugin-commonjs";
import { nodeResolve } from "@rollup/plugin-node-resolve";
import typescript from "@rollup/plugin-typescript";
import externalGlobals from "rollup-plugin-external-globals";

// The three globals Steam's own React lives under. They are not Steam's doing —
// Decky's loader installs them, and since #1899 so does `src/boot/steamGlobals.ts`,
// which is why one of the builds below exists only to produce that bootstrap.
const STEAM_REACT_GLOBALS = {
  react: "SP_REACT",
  "react/jsx-runtime": "SP_JSX",
  "react-dom": "SP_REACTDOM",
};

// The build output belongs to the REPOSITORY, not to this package: the backend
// serves it as `<code_dir>/dist` and must not know how the frontend arranges
// itself internally — see `static_root` in `backend/main.py`, whose comment
// refuses that direction in its own words. Emptying it is the `build` script's
// job (`rm -rf ../dist && rollup -c`), not this file's.
const OUT_DIR = "../dist";

// TREE-SHAKING, and the answer this file owes rather than inherits.
//
// `@decky/ui` declares `"sideEffects": false` in its manifest and then runs
// `initModuleCache()` at module scope — a sweep that executes every module in
// Steam's live webpack registry and is the whole reason any of its exports
// resolve to anything. The manifest is therefore a false statement to every
// bundler that reads it, and a bundler that acted on it would drop the sweep.
// The consequence would not be a build error: every search answers `undefined`,
// the panel renders holes, and the start-up check reports a Steam client update
// that never happened.
//
// **`@decky/rollup` answered this for us and is being removed here**, so it has
// to be answered again. Its answer was `treeshake: { preset: "smallest",
// pureExternalImports: { pure: ["@decky/ui", "@decky/api"] } }`, which was moot
// there: the package was EXTERNAL, mapped onto the `DFL` global, so no byte of
// it was in the output for any setting to remove.
//
// **The answer here is Rollup's default, and it is measured rather than
// reasoned.** Three builds of `src/index.tsx` with `@decky/ui` BUNDLED, against
// rollup 4.62.2 and `@decky/ui` 4.12.0:
//
//   default treeshake                         initModuleCache present, 759,413 B
//   preset "smallest" (what the preset set)   initModuleCache present, 759,092 B
//   every @decky/ui module forced side-       initModuleCache present, 770,208 B
//     effectful via a resolveId plugin
//
// The sweep survives all three, because it writes the module-level Map that
// `findModule` reads and Rollup cannot treat such a call as removable. Forcing
// the flag bought no behavioural difference and 10,795 bytes of `@decky/ui`'s
// unused `EResult` / `EUIMode` enum tables, so it was measured, found inert, and
// not kept — a setting that protects nothing still has to be maintained and read.
//
// What guards the sweep instead is an assertion on the ARTEFACT:
// `frontend/scripts/check-bundle-shape.mjs` fails when the standalone bundle
// does not contain it, or when the coexistence bundle does. It is a step of its
// own (`pnpm check:bundle`, run after `pnpm build` by the gate and by CI) rather
// than part of this build — `pnpm build` is `rm -rf ../dist && rollup -c` and
// this file has no say in what runs next. The assertion holds across a Rollup
// upgrade and a `@decky/ui` bump, neither of which a treeshake setting here
// would survive being wrong about.

/**
 * Ship `@decky/ui`'s licence beside the bundle that carries its code.
 *
 * Bundling the package makes this project a DISTRIBUTOR of it, and it is
 * LGPL-2.1: the licence text travels with the work. Until #1899 this project
 * shipped not one byte of it — the package was mapped onto Decky's global — so
 * nothing was owed and nothing was carried.
 *
 * **Two of the three builds ship the package's code**, not one: `dist/index.js`
 * carries the components, and `dist/globals.js` carries its module-cache half.
 * The coexistence bundle carries none of it — it takes the package from Decky's
 * own copy — so it is the one build a notice would be a false claim on.
 *
 * The emission hangs off the standalone build for a reason of arrangement
 * rather than of licensing: all three builds write into one `dist/`, so one
 * copy covers the directory whichever of them produced it. Hanging it off a
 * build that ships none of the code would be the mistake; hanging it off one of
 * the two that do is enough while they share an output directory, and that
 * premise is what a later change serving `globals.js` on its own would break.
 *
 * `THIRD-PARTY-NOTICES.md` at the repository root names the version and says
 * what the reader may do with it; this is the text itself, verbatim and
 * untouched.
 */
const shipDeckyUiLicence = () => ({
  name: "ship-decky-ui-licence",
  writeBundle() {
    mkdirSync(OUT_DIR, { recursive: true });
    copyFileSync("node_modules/@decky/ui/LICENSE", `${OUT_DIR}/LICENSE-@decky-ui.txt`);
  },
});

const plugins = () => [
  typescript({ tsconfig: "./tsconfig.json" }),
  commonjs(),
  nodeResolve({ browser: true }),
  externalGlobals(STEAM_REACT_GLOBALS),
];

// `context: "window"` is Steam's: a bundle evaluated through `import()` in the
// SharedJSContext has no module `this`, and a dependency reading top-level
// `this` expecting the global object would see `undefined`.
//
// `output.exports` is deliberately absent. `@decky/rollup` set it to "default",
// which Rollup ignores for `format: "esm"` — it applies to cjs/amd/umd/iife
// only — so carrying it over would have looked like a decision and been a no-op.
const build = ({ input, file, external, extraPlugins = [] }) => ({
  input,
  external,
  context: "window",
  plugins: [...plugins(), ...extraPlugins],
  output: { file: `${OUT_DIR}/${file}`, format: "esm", sourcemap: false },
});

// Four of the five dependencies `@decky/rollup` brought are not here, and each
// absence was checked rather than assumed: no module under `src/` imports a
// `.json` file (`@rollup/plugin-json`) or an asset (`rollup-plugin-import-assets`,
// which also pointed its public path at a `127.0.0.1:1337` plugin server that
// serves nothing for us); no module under `src/`, in `@decky/ui` or in
// `react-icons` reads `process.env` (`@rollup/plugin-replace`); and
// `rollup-plugin-delete` was aimed at `./dist/*`, which resolves against the
// working directory and cleaned this package's own unused `dist` rather than the
// real one.
export default [
  // The React bootstrap. Its own bundle, and that is load-bearing rather than
  // tidy: `@decky/ui`'s component half reads React internals while its modules
  // evaluate, so it cannot be in the import graph of the module that creates the
  // globals — ESM evaluates the whole graph before any body runs. Decky splits it
  // in the same place. It imports only `@decky/ui/dist/webpack`, the module-cache
  // machinery without the components.
  build({
    input: "./src/boot/steamGlobals.ts",
    file: "globals.js",
    external: Object.keys(STEAM_REACT_GLOBALS),
  }),

  // STANDALONE — `@decky/ui` bundled. What ships when Decky Loader is not
  // running: our sweep of Steam's module registry is then the first of the
  // session and carries.
  build({
    input: "./src/index.tsx",
    file: "index.js",
    external: Object.keys(STEAM_REACT_GLOBALS),
    extraPlugins: [shipDeckyUiLicence()],
  }),

  // COEXISTENCE — `@decky/ui` taken from Decky's already-loaded copy through the
  // `DFL` global, which is what the whole build did before #1899.
  //
  // It is not a legacy form kept for politeness. Importing `@decky/ui` re-runs
  // `initModuleCache()`, which re-executes every module in Steam's live webpack
  // registry — and what makes that fatal is a consumer already RENDERING from
  // those modules, not the number of sweeps. Measured on the device: a second
  // sweep on the desktop client survives, a third with Big Picture open and the
  // Quick Access view mounted survives, and starting Decky into that same
  // session survives; Big Picture plus Quick Access plus Decky already
  // rendering crashes with `Minified React error #31`, because a module
  // re-executed underneath something holding its exports leaves an empty object
  // where a component was. Steam's own interface is not such a consumer;
  // Decky's is. This build carries no `initModuleCache` call at all.
  //
  // WHICH of the two is loaded is the injector's decision (#1900) and is not made
  // here; what is made here is the pair, and two file names to tell them apart.
  build({
    input: "./src/index.tsx",
    file: "index-coexistence.js",
    external: [...Object.keys(STEAM_REACT_GLOBALS), "@decky/ui"],
    extraPlugins: [externalGlobals({ "@decky/ui": "DFL" })],
  }),
];
