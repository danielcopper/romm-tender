import builds from "./rollup.config.js";

// Dev-only config: inherits the default builds and turns source maps back on for
// CEF debugging. Used by `pnpm build:dev` / `mise run build` / `mise run dev`.
// A shipping build never sees this file — it always runs the default
// `rollup -c`, which is map-free.
//
// It maps over the array because the default export is one now: since #1899 the
// build produces the React bootstrap and the two panel bundles, and a file that
// reached for `.output` on the array would have set the flag on `undefined` and
// shipped no maps while reporting nothing.
export default builds.map((build) => ({ ...build, output: { ...build.output, sourcemap: true } }));
