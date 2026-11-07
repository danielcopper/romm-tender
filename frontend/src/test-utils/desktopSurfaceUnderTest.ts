/**
 * What `virtual:tender-desktop-surface` resolves to under Vitest.
 *
 * `true`, so the suite holds every entry of the start-up check's lists, the
 * dev build's included, to the code; what a release bundle leaves out is
 * asserted on the artefact by `frontend/scripts/check-bundle-shape.mjs`. The
 * Steam-internals check takes the answer as an argument, so a test reaches the
 * other answer by passing it.
 */
export const DESKTOP_SURFACE = true;
