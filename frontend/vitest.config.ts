import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      // `virtual:tender-bundle-kind` is served by the build (`rollup.config.js`)
      // and exists on no disk, so without this the start-up check's modules
      // cannot be imported here at all. What it resolves to is the DEFAULT a
      // test gets; both answers are reached by passing the kind explicitly,
      // never by re-aliasing this.
      "virtual:tender-bundle-kind": fileURLToPath(new URL("./src/test-utils/bundleKindUnderTest.ts", import.meta.url)),
    },
  },
  test: {
    environment: "happy-dom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      // The lcov report is read by SonarCloud, which resolves every `SF:` path
      // against the REPOSITORY root while this config's root is the package.
      // `@vitest/coverage-v8` defaults istanbul's `projectRoot` to
      // `ctx.config.root`, so without this override the report says
      // `SF:src/index.tsx`, Sonar finds no `src/` beside `backend/`, and every
      // frontend file arrives uncovered — with nothing failing, because
      // `sonar.sources` and `sonar.coverage.exclusions` are repo-relative and
      // still match. The 80%-on-new-code gate would simply start judging
      // frontend changes as untested. Vitest spreads the reporter's options over
      // its own, so the value below wins.
      reporter: ["text", ["lcov", { projectRoot: fileURLToPath(new URL("..", import.meta.url)) }]],
      include: ["src/**/*.{ts,tsx}"],
      // An exclusion names a property of the code, never a place: a folder entry
      // stands only where membership in the folder IS the property, and every
      // file entry carries its reason as a `// coverage-exempt:` marker in the
      // file itself, so the reason moves with the file. This list and
      // sonar-project.properties' `sonar.coverage.exclusions` must agree —
      // `scripts/check_coverage_exclusions.py` holds both halves and every
      // asymmetry: the one Vitest-only entry below, and the five Sonar-only
      // ones (the Python suite, its fixtures, vendored Python and this
      // package's two config globs) that stand outside this file's `include`
      // anyway. Sonar runs from the repository root and this file does not, so
      // the same entry is spelt `frontend/src/...` there and `src/...` here;
      // the gate normalises before comparing.
      exclude: [
        // Vitest-only: Sonar takes the tests out of the coverage ratio through
        // `sonar.test.inclusions` rather than through its exclusion list.
        "src/**/*.{test,spec}.{ts,tsx}",
        "src/types/**",
        "src/test-utils/**",
        "src/test-setup.ts",
        "src/bigpicture/patches/gameDetailPatch.tsx",
        "src/bigpicture/patches/installGamePagePatch.ts",
        "src/qam/installEntry.tsx",
        "src/utils/styleInjector.ts",
      ],
    },
  },
});
