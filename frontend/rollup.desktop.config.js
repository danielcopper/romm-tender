import { configure } from "./rollup.config.js";
import { insertDesktopSurfaceStart } from "./scripts/start-desktop-surface.mjs";

const GLOBALS_INPUT = "./src/boot/steamGlobals.ts";

// The desktop surface's dev build (docs/architecture/frontend-bundles.md, "The
// desktop dev build"). Every build but the globals one is a build of the panel, and
// each is stamped as one the desktop surface is in.
export default configure({ sourcemap: true, desktopSurface: true }).map((build) =>
  build.input === GLOBALS_INPUT ? build : { ...build, plugins: [...build.plugins, insertDesktopSurfaceStart()] },
);
