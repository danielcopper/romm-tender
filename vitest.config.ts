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
      exclude: [
        "frontend/src/**/*.{test,spec}.{ts,tsx}",
        "frontend/src/test-setup.ts",
        "frontend/src/test-utils/**",
        // Aligned with sonar-project.properties `sonar.coverage.exclusions`.
        "frontend/src/types/**",
        "frontend/src/index.tsx",
        "frontend/src/bigpicture/patches/**",
        "frontend/src/utils/metadataPatches.ts",
        "frontend/src/utils/styleInjector.ts",
      ],
    },
  },
});
