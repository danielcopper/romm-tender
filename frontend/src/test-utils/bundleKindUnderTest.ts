/**
 * What `virtual:tender-bundle-kind` resolves to under Vitest.
 *
 * The real module is served by the build (`frontend/rollup.config.js`), and the
 * suite never runs a built bundle — so without this the modules that import it
 * could not be loaded here at all. The value here is the DEFAULT a test gets
 * when it does not say which bundle it is asking about;
 * both answers are reached by passing the kind explicitly, which is why
 * `readSearchingCopy` takes it as an argument rather than reading the constant
 * itself.
 */
export const BUNDLE_KIND = "standalone";
