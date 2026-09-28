import { configure } from "./rollup.config.js";

// Dev-only config: the default builds with source maps on, for CEF debugging.
// Used by `pnpm build:dev` / `mise run build` / `mise run dev`. A shipping build
// never sees this file — it always runs the default `rollup -c`, which is
// map-free.
export default configure({ sourcemap: true });
