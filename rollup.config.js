import deckyPlugin from "@decky/rollup";

// Default config: NO source map. This is the config `decky plugin build`
// (and CI's `pnpm build`) pick up automatically, so the release zip never
// ships dist/index.js.map. `@decky/rollup` hardcodes sourcemap: true and
// can't be overridden via its options arg, so we mutate the output here.
// Dev keeps source maps via rollup.dev.config.js.
const config = deckyPlugin({});
config.output.sourcemap = false;
// Same reason the entry point is set here: `@decky/rollup` hardcodes
// `./src/index.tsx` and merges its own defaults LAST, so an `input` passed
// into the options arg is overwritten.
config.input = "./frontend/src/index.tsx";

export default config;
