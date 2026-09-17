/**
 * The stamp each panel bundle carries about which of the two it is.
 *
 * The module does not exist on disk: `frontend/rollup.config.js` serves it from
 * a `resolveId` + `load` pair, once per panel build, with that build's own
 * answer inside. So the value cannot be edited into disagreement with the
 * artefact it describes, and a build that lost the plugin fails to resolve the
 * import instead of shipping a bundle that names the wrong one.
 *
 * The suite resolves the same specifier through `vitest.config.ts`'s alias,
 * onto `src/test-utils/bundleKindUnderTest.ts` — nothing here runs a built
 * bundle, so without that there would be no value at all. Every reader below
 * `readSearchingCopy` takes the kind as an argument for the same reason: a
 * constant a test cannot vary is a branch a test cannot reach.
 */
declare module "virtual:tender-bundle-kind" {
  /**
   * Which panel bundle this code was built into — `dist/index.js` with
   * `@decky/ui` inside it, or `dist/index-coexistence.js` taking the package
   * from Decky Loader's copy through the `DFL` global.
   */
  export const BUNDLE_KIND: "standalone" | "coexistence";
}
