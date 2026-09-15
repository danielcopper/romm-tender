import config from "./rollup.config.js";

// Dev-only config: inherits the default build and turns source maps back on
// for CEF debugging. Used by `pnpm build:dev` / `mise run build` / `mise run dev`.
// A shipping build never sees this file — it always runs the default
// `rollup -c`, which is map-free.
config.output.sourcemap = true;

export default config;
