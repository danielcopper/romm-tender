import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "happy-dom",
    globals: true,
    setupFiles: ["./frontend/src/test-setup.ts"],
    include: ["frontend/src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["frontend/src/**/*.{ts,tsx}"],
      // An exclusion names a property of the code, never a place: a folder entry
      // stands only where membership in the folder IS the property, and every
      // file entry carries its reason as a `// coverage-exempt:` marker in the
      // file itself, so the reason moves with the file. This list and
      // sonar-project.properties' `sonar.coverage.exclusions` must agree —
      // `scripts/check_coverage_exclusions.py` holds both halves and every
      // asymmetry: the one Vitest-only entry below, and the five Sonar-only
      // ones (the Python suite, its fixtures, vendored Python and the two root
      // config globs) that stand outside this file's `include` anyway.
      exclude: [
        // Vitest-only: Sonar takes the tests out of the coverage ratio through
        // `sonar.test.inclusions` rather than through its exclusion list.
        "frontend/src/**/*.{test,spec}.{ts,tsx}",
        "frontend/src/types/**",
        "frontend/src/test-utils/**",
        "frontend/src/test-setup.ts",
        "frontend/src/bigpicture/patches/gameDetailPatch.tsx",
        "frontend/src/utils/styleInjector.ts",
      ],
    },
  },
});
