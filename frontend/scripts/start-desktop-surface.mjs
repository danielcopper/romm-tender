// The transform behind `pnpm build:desktop` (rollup.desktop.config.js): it
// rewrites src/index.tsx as it is bundled to start the desktop surface. Nothing
// that ships reads this file (docs/architecture/frontend-bundles.md, "The
// desktop dev build").
export const START_ANCHOR = "disownStrandedPruneLeases();";

const ENTRY = "/src/index.tsx";

/**
 * A rollup plugin for a build of the panel. It fails that build when the build
 * never reached src/index.tsx, or when that module does not hold the anchor
 * exactly once, rather than letting it finish without the surface.
 */
export const insertDesktopSurfaceStart = () => {
  let entrySeen = false;
  return {
    name: "insert-desktop-surface-start",
    buildStart() {
      entrySeen = false;
    },
    transform(code, id) {
      if (!id.replace(/\\/g, "/").endsWith(ENTRY)) return null;
      entrySeen = true;
      const anchors = code.split(START_ANCHOR).length - 1;
      if (anchors !== 1) {
        this.error(`holds ${START_ANCHOR} ${anchors} times rather than once, so the desktop surface would not start`);
      }
      // `map: null` tells rollup the module's lines did not move, so every line
      // stays where it was: the call joins the anchor's line, and the import, which
      // ESM hoists wherever it stands, goes after the last line.
      const transformed = `${code.replace(START_ANCHOR, `${START_ANCHOR} startDesktopSurface();`)}\nimport { startDesktopSurface } from "./desktop";\n`;
      return { code: transformed, map: null };
    },
    buildEnd(error) {
      if (!error && !entrySeen) {
        this.error(`never reached ${ENTRY.slice(1)}, so the desktop surface would not start`);
      }
    },
  };
};
