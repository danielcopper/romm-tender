/**
 * The stamp each panel bundle carries about whether the desktop surface is in it.
 *
 * Served by `frontend/rollup.config.js` the way `virtual:tender-bundle-kind` is
 * (`virtual-bundle-kind.d.ts` says why a build stamp rather than a runtime
 * probe): `true` only in `pnpm build:desktop`, and held `false` in the release
 * bundles by `frontend/scripts/check-bundle-shape.mjs`. The suite resolves it
 * through `vitest.config.ts`'s alias onto
 * `src/test-utils/desktopSurfaceUnderTest.ts`.
 */
declare module "virtual:tender-desktop-surface" {
  /** Whether this build carries the desktop surface (`frontend/src/desktop/`). */
  export const DESKTOP_SURFACE: boolean;
}
