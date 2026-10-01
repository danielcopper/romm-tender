import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import commonjs from "@rollup/plugin-commonjs";
import { nodeResolve } from "@rollup/plugin-node-resolve";
import externalGlobals from "rollup-plugin-external-globals";
import ts from "typescript";

import { packageBytes } from "./scripts/package-budgets.mjs";

// The three globals Steam's own React lives under. They are not Steam's doing —
// Decky's loader installs them, and so does `src/boot/steamGlobals.ts`, which is
// why one of the builds below exists only to produce that bootstrap.
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

// TREE-SHAKING.
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
// **The answer is Rollup's default, and it is measured rather than reasoned.**
// Three builds of `src/index.tsx` with `@decky/ui` BUNDLED, against rollup
// 4.62.2 and `@decky/ui` 4.12.0:
//
//   default treeshake                         initModuleCache present, 759,413 B
//   preset "smallest"                         initModuleCache present, 759,092 B
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
 * LGPL-2.1: the licence text travels with the work.
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

const BUNDLE_KIND_MODULE = "virtual:tender-bundle-kind";

/**
 * Tell a panel bundle which of the two it is.
 *
 * `src/boot/searchingCopy.ts` needs the answer to say whose copy of `@decky/ui`
 * ran a search that came back empty — ours in the standalone bundle, Decky
 * Loader's in the coexistence one — and the two repairs are different programs.
 * It is a BUILD fact, so the build states it: a runtime probe would read
 * `typeof DFL !== "undefined"`, which is also true of a standalone bundle loaded
 * on a machine where Decky happens to be running, and the page would then credit
 * Decky's copy with work our own copy did.
 *
 * A virtual module rather than `@rollup/plugin-replace`, which was the other
 * candidate: it is one dependency fewer, and a build that lost this plugin SAYS
 * SO. Measured against rollup 4.63.1, such a build exits 0 and emits the
 * artefact — but it prints `(!) Unresolved dependencies` and leaves the bare
 * `import { BUNDLE_KIND } from "virtual:tender-bundle-kind"` standing at the top
 * of it, where a token left unreplaced is a free identifier nothing says a word
 * about. Both break on a device, and the difference is that one of them is
 * visible before it gets there. The React bootstrap build below deliberately
 * does NOT carry the plugin — it is neither of the two panel bundles, so it has
 * no answer to give, and nothing in its import graph asks for one.
 *
 * `frontend/scripts/check-bundle-shape.mjs` asserts the stamp on each artefact,
 * because a warning rollup prints and an exit code of 0 is not what stops a
 * wrong bundle from shipping.
 */
const stampBundleKind = (kind) => ({
  name: "stamp-bundle-kind",
  resolveId: (id) => (id === BUNDLE_KIND_MODULE ? `\0${BUNDLE_KIND_MODULE}` : null),
  load: (id) => (id === `\0${BUNDLE_KIND_MODULE}` ? `export const BUNDLE_KIND = ${JSON.stringify(kind)};` : null),
});

// Beside the package rather than in `dist/`: `scripts/package.sh` ships the
// whole of `dist/`, and this is a fact about the build, not part of what runs.
const PACKAGE_RECORD = "bundle-packages.json";

// `rollup -c` evaluates this file once and runs the three builds in turn, so the
// record holds this run's builds only, and each build rewrites it with all of
// them so far.
const packageRecord = {};

/**
 * Record how many bytes each third-party package puts into this build's bundle,
 * with the digest of the bundle as written so that `check-package-budgets.mjs`
 * can refuse a record older than `dist/`.
 */
const recordPackageBytes = () => ({
  name: "record-package-bytes",
  writeBundle({ file }, bundle) {
    const name = path.basename(file);
    const modules = Object.entries(bundle[name].modules).map(([id, { code }]) => [id, Buffer.byteLength(code ?? "")]);
    packageRecord[name] = {
      sha256: createHash("sha256").update(readFileSync(file)).digest("hex"),
      packages: packageBytes(modules),
    };
    writeFileSync(PACKAGE_RECORD, `${JSON.stringify(packageRecord, null, 2)}\n`);
  },
});

const DIAGNOSTIC_FORMAT = {
  getCanonicalFileName: (fileName) => fileName,
  getCurrentDirectory: ts.sys.getCurrentDirectory,
  getNewLine: () => ts.sys.newLine,
};

const failOn = (diagnostics) => {
  if (diagnostics.length > 0) throw new Error(ts.formatDiagnostics(diagnostics, DIAGNOSTIC_FORMAT));
};

const readCompilerOptions = () => {
  const tsconfig = path.resolve("tsconfig.json");
  const { config, error } = ts.readConfigFile(tsconfig, ts.sys.readFile);
  failOn(error ? [error] : []);
  const { options, errors } = ts.parseJsonConfigFileContent(config, ts.sys, path.dirname(tsconfig));
  failOn(errors);
  return options;
};

const COMPILER_OPTIONS = readCompilerOptions();

const SOURCE_DIR = path.resolve("src") + path.sep;

const isOurTypeScript = (id) => id.startsWith(SOURCE_DIR) && /\.tsx?$/.test(id) && !id.endsWith(".d.ts");

/**
 * TypeScript to JavaScript one file at a time. What fails the build and what
 * checks types instead: `docs/contributing/development.md`, "Building".
 *
 * `ts.transpileModule` sees only the file it is handed, and that is the point.
 * The obvious alternative, `@rollup/plugin-typescript`, compiles the whole
 * program (parse, check, emit) through a watch program even for a one-shot
 * build, and that compile is nearly all of every bundle's time — for a type
 * check that plugin only prints as a warning.
 *
 * What only the whole program knows is out of its reach, and the one case that
 * matters is not silent: a re-export of a name that is only a type stays in the
 * output, and Rollup refuses it as a missing export. `export type { … }` says
 * what a single file cannot know.
 *
 * Whether to emit a source map is decided here rather than read off
 * `output.sourcemap`, because `transform` runs before any output exists — one
 * build can be written to several outputs.
 */
const transpileTypeScript = ({ sourcemap }) => {
  const compilerOptions = { ...COMPILER_OPTIONS, sourceMap: sourcemap };
  return {
    name: "transpile-typescript",
    resolveId(importee, importer) {
      if (!importer || !isOurTypeScript(importer)) return null;
      const { resolvedModule } = ts.resolveModuleName(importee, importer, compilerOptions, ts.sys);
      const resolved = resolvedModule && path.normalize(resolvedModule.resolvedFileName);
      return resolved && isOurTypeScript(resolved) ? resolved : null;
    },
    transform(code, id) {
      if (!isOurTypeScript(id)) return null;
      const { outputText, sourceMapText, diagnostics } = ts.transpileModule(code, {
        compilerOptions,
        fileName: id,
        reportDiagnostics: true,
      });
      const errors = diagnostics.filter((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error);
      if (errors.length > 0) {
        const message = errors
          .map((error) => `TS${error.code}: ${ts.flattenDiagnosticMessageText(error.messageText, "\n")}`)
          .join("\n");
        this.error(message, errors[0].start);
      }
      return { code: outputText, map: sourceMapText ? JSON.parse(sourceMapText) : null };
    },
  };
};

const plugins = ({ sourcemap }) => [
  transpileTypeScript({ sourcemap }),
  commonjs(),
  nodeResolve({ browser: true }),
  externalGlobals(STEAM_REACT_GLOBALS),
];

// `context: "window"` is Steam's: a bundle evaluated through `import()` in the
// SharedJSContext has no module `this`, and a dependency reading top-level
// `this` expecting the global object would see `undefined`.
const build = ({ input, file, external, bundleKind, extraPlugins = [], sourcemap }) => ({
  input,
  external,
  context: "window",
  plugins: [
    ...(bundleKind ? [stampBundleKind(bundleKind)] : []),
    ...plugins({ sourcemap }),
    ...extraPlugins,
    recordPackageBytes(),
  ],
  output: { file: `${OUT_DIR}/${file}`, format: "esm", sourcemap },
});

export const configure = ({ sourcemap }) => [
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
    sourcemap,
  }),

  // STANDALONE — `@decky/ui` bundled. What ships when Decky Loader is not
  // running: our sweep of Steam's module registry is then the first of the
  // session and carries.
  build({
    input: "./src/index.tsx",
    file: "index.js",
    external: Object.keys(STEAM_REACT_GLOBALS),
    bundleKind: "standalone",
    extraPlugins: [shipDeckyUiLicence()],
    sourcemap,
  }),

  // COEXISTENCE — `@decky/ui` taken from Decky's already-loaded copy through the
  // `DFL` global.
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
  // WHICH of the two is loaded is the injector's decision and is not made here;
  // what is made here is the pair, and two file names to tell them apart.
  build({
    input: "./src/index.tsx",
    file: "index-coexistence.js",
    external: [...Object.keys(STEAM_REACT_GLOBALS), "@decky/ui"],
    bundleKind: "coexistence",
    extraPlugins: [externalGlobals({ "@decky/ui": "DFL" })],
    sourcemap,
  }),
];

export default configure({ sourcemap: false });
