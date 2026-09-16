import deckyPlugin from "@decky/rollup";

// `plugin.json` beside this file is NOT a manifest. Nothing installs it, nothing
// ships it and no release reads it — it is an argument to the one build plugin
// below, which opens it unconditionally as its first statement
// (`@decky/rollup@1.0.2`, `src/index.js:74`: `JSON.parse(readFileSync(join(
// sourceRoot, "plugin.json")))`). There is no option that skips the read, so the
// file exists to keep `rollup -c` from throwing ENOENT and for no other reason.
//
// Its contents are inert. Read at that version, the manifest is reached in three
// places and every one of them is dead: `manifest.name` builds a
// `http://127.0.0.1:1337/plugins/<name>/` asset URL that no plugin loader serves
// for us any more; `manifest.name` again prefixes source-map paths with
// `decky://decky/plugin/<name>/`, which only the dev build emits; and the whole
// object is serialised into an `@decky/manifest` external global that no module
// under `src/` imports. So `name` is the only field kept, and nothing requires it
// to agree with the display name in `backend/domain/identity.py` — it is not one
// of that name's homes.
//
// This file and that one both go when `@decky/rollup` does, in #1899.
const config = deckyPlugin({});

// Default config: NO source map. This is the config a plain `rollup -c` picks
// up — CI's `pnpm build`, and any packaging that runs it — so a shipped bundle
// never carries dist/index.js.map. `@decky/rollup` hardcodes sourcemap: true and
// can't be overridden via its options arg, so we mutate the output here.
// Dev keeps source maps via rollup.dev.config.js.
config.output.sourcemap = false;
// Same reason the entry point is set here: `@decky/rollup` hardcodes
// `./src/index.tsx` and merges its own defaults LAST, so an `input` passed
// into the options arg is overwritten.
config.input = "./src/index.tsx";
// And the same for the output directory, which `@decky/rollup` hardcodes to
// `dist` beside the config. The build output belongs to the REPOSITORY, not to
// this package: the backend serves it from `<code_dir>/dist` and must not know
// how the frontend arranges itself internally — see `static_root` in
// `backend/main.py`, whose comment refuses that direction in its own words.
//
// Emptying that directory is the `build` script's job rather than this file's.
// `@decky/rollup` puts a `rollup-plugin-delete` in the plugin array aimed at
// `./dist/*`, which resolves against the working directory and therefore cleans
// this package's own (empty, unused) `dist` instead of the real one. Repointing
// it would mean rewriting a plugin instance inside an array upstream generates —
// a second thing to keep in step with a dependency we are about to drop.
config.output.dir = "../dist";

export default config;
