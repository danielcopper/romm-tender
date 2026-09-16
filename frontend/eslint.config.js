import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import importX, { createNodeResolver } from "eslint-plugin-import-x";
import { createTypeScriptImportResolver } from "eslint-import-resolver-typescript";
import eslintConfigPrettier from "eslint-config-prettier";
import globals from "globals";
import qamFocusableRow from "./eslint-rules/qam-focusable-row.js";

export default tseslint.config(
  // ESLint's root is this package, so the only gitignored trees it can reach are
  // this package's own: its `node_modules` and the coverage report Vitest writes
  // beside it. Everything the old list also named — `.venv`, `site`, `.claude`,
  // `.worktrees`, `defaults`, `bin`, and the build output, which the repository
  // owns rather than this package — sits ABOVE this directory and is now out of
  // reach by construction rather than by exclusion.
  {
    ignores: ["node_modules", "coverage"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  react.configs.flat.recommended,
  react.configs.flat["jsx-runtime"],
  reactHooks.configs.flat["recommended-latest"],
  jsxA11y.flatConfigs.recommended,
  // Global so eslint-plugin-react resolves the version for every linted file —
  // including this package's config files (eslint.config.js, vitest.config.ts, …)
  // that the react flat configs apply to but that the src-scoped block below never
  // matches. Without it the plugin prints a "React version not specified" warning.
  { settings: { react: { version: "detect" } } },
  // Direction rules for `src/`. The backend gets this from
  // `.importlinter`, which is Python-only; without an equivalent here nothing
  // in the frontend toolchain has an opinion about which module may reach
  // which. The seven rules below make the WRONG seam fail — they cannot certify
  // that a seam is right. A helper imported by exactly one parent, taking a
  // dozen parameters and doing nothing on its own, is neither a cycle nor a
  // direction violation and still passes.
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "import-x": importX },
    settings: {
      // Resolution decides what these rules see. The TS resolver handles path
      // aliases, index files, and `import type` — which is erased at runtime and
      // must not count as an edge. A resolver that misses imports would make the
      // rules quietly permissive.
      "import-x/resolver-next": [createTypeScriptImportResolver(), createNodeResolver()],
      // Both of these are load-bearing and neither is the default. `extensions`
      // ships as ['.js'], so without .ts/.tsx the plugin resolves an import but
      // never opens the target to read ITS imports — `no-cycle` then walks a graph
      // one edge deep and reports nothing, on any codebase, forever. `parsers`
      // supplies the TS parser it needs to do that reading. A probe cycle is the
      // only way to tell this apart from "no cycles exist"; see
      // src/eslintBoundaries.test.ts for the one that stays.
      "import-x/extensions": [".ts", ".tsx"],
      "import-x/parsers": { "@typescript-eslint/parser": [".ts", ".tsx"] },
    },
    rules: {
      // A cycle is the signature of the fake split: an extraction whose halves
      // still call each other back. `service-independence` does this job in the
      // backend; its literal form does not transfer, because composition means a
      // component must be allowed to import a component. `ignoreExternal` keeps
      // the walk inside our own graph, which is the only one we can fix.
      "import-x/no-cycle": ["error", { ignoreExternal: true }],
      "import-x/no-restricted-paths": [
        "error",
        {
          zones: [
            {
              target: "./src/utils",
              from: "./src/bigpicture",
              message:
                "utils/ is the bottom layer and must not reach up into a surface. Declare what you need to ask (see LaunchPrompts in utils/launchInterceptor.ts) and let index.tsx supply it.",
            },
            {
              target: "./src/utils",
              from: "./src/desktop",
              message:
                "utils/ is the bottom layer and must not reach up into a surface. Declare what you need to ask (see LaunchPrompts in utils/launchInterceptor.ts) and let the surface's entry point supply it.",
            },
            {
              target: "./src/api",
              from: "./src/bigpicture",
              message: "api/ is the wire layer and has no business reaching into the view.",
            },
            {
              target: "./src/api",
              from: "./src/desktop",
              message: "api/ is the wire layer and has no business reaching into the view.",
            },
            {
              target: "./src/bigpicture",
              from: "./src/desktop",
              message:
                "bigpicture/ and desktop/ are peers, not layers: the two surfaces share data and logic and almost nothing visual, so neither may reach into the other. Anything that turns out to belong to both moves DOWN into api/, utils/ or types/ — never sideways.",
            },
            {
              target: "./src/desktop",
              from: "./src/bigpicture",
              message:
                "desktop/ and bigpicture/ are peers, not layers: the two surfaces share data and logic and almost nothing visual, so neither may reach into the other. Anything that turns out to belong to both moves DOWN into api/, utils/ or types/ — never sideways.",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "src/index.tsx",
      "src/bigpicture/{MainPage,SyncPage,LibraryPage,SettingsPage,DangerZone,RemovedGamesCleanup,DownloadQueue}.tsx",
      "src/bigpicture/{SessionBudgetBanner,MigrationBlockedPage,SettingsResetBanner,PlaytimeScopeBanner,DownloadProgressRow,LoadingRow}.tsx",
      "src/bigpicture/{layout,sync,library,settings}/**/*.tsx",
    ],
    // A test renders no row a reader walks, and a modal is not a panel row at all —
    // it mounts in Steam's `ModalRoot`, outside the region that scrolls by focus.
    // Neither exemption is load-bearing today: with both removed the rule still
    // reports nothing across `src/**/*.tsx`.
    ignores: ["src/**/*.test.tsx", "src/bigpicture/**/*Modal.tsx"],
    plugins: {
      tender: {
        rules: { "qam-focusable-row": qamFocusableRow },
      },
    },
    rules: {
      "tender/qam-focusable-row": "error",
    },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
      globals: {
        ...globals.browser,
        SteamClient: "readonly",
        appStore: "readonly",
        appDetailsStore: "readonly",
        appDetailsCache: "readonly",
        collectionStore: "readonly",
      },
    },
    rules: {
      "react/prop-types": "off", // TS handles this
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
      // Promoted back to error in #617 cleanup. Untyped sites that genuinely
      // need `any` (Steam internal React tree walking in bigpicture/patches/) carry
      // an inline `// eslint-disable-next-line @typescript-eslint/no-explicit-any`
      // with a documented reason.
      "@typescript-eslint/no-explicit-any": "error",
      // Cherry-picked type-aware rule (#838): enabled via parserOptions.projectService
      // above, without adopting the full recommendedTypeChecked preset (which drags in
      // the no-unsafe-* noise family — the JS twin of pyright's rejected reportUnknown*).
      "@typescript-eslint/await-thenable": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-unnecessary-condition": "error",
    },
  },
  {
    // Ambient global type declarations require `var` and `any` by their nature.
    files: ["**/*.d.ts"],
    rules: {
      "no-var": "off",
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    // Vitest globals (describe/it/expect/vi/...) are injected at runtime via
    // vitest.config.ts `globals: true` + tsconfig "types": ["vitest/globals"].
    files: [
      "src/**/*.{test,spec}.{ts,tsx}",
      "src/test-setup.ts",
      "src/test-utils/**/*.ts",
    ],
    languageOptions: {
      globals: { ...globals.vitest },
    },
    rules: {
      // Anonymous mock components are fine — they don't appear in real render trees.
      "react/display-name": "off",
    },
  },
  // Must stay LAST: turns off ESLint rules that conflict with Prettier formatting
  // so the two tools don't fight. Prettier owns formatting; ESLint owns correctness.
  eslintConfigPrettier,
);
