import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import importX, { createNodeResolver } from "eslint-plugin-import-x";
import { createTypeScriptImportResolver } from "eslint-import-resolver-typescript";
import eslintConfigPrettier from "eslint-config-prettier";
import globals from "globals";
import { readFileSync } from "node:fs";
import path from "node:path";
import noResolverVocabulary from "./eslint-rules/no-resolver-vocabulary.js";
import qamFocusableRow from "./eslint-rules/qam-focusable-row.js";

// One plugin object for every block that turns on one of its rules: ESLint
// refuses a second definition of the same plugin name for a file.
const tender = {
  rules: { "no-resolver-vocabulary": noResolverVocabulary, "qam-focusable-row": qamFocusableRow },
};

// Names that carry "plugin" because they name someone else's plugin, and must
// keep the word. Each lets through its whole name, wherever it appears, and
// never a longer name containing it.
const NAMES_NOT_ABOUT_TENDER = [
  // Decky Loader's own window global, read to name the loader's version.
  "DeckyPluginLoader",
  // The config key rollup and ESLint each read their plugins from, and
  // rollup.config.js's list of the rollup plugins every bundle is built with.
  "plugins",
  // rollup.config.js's rollup plugins one bundle is built with beside those.
  "extraPlugins",
];

const NAMES_THAT_MISNAME_TENDER = ["Identifier", "JSXIdentifier", "PrivateIdentifier"].map((type) => ({
  selector: `${type}[name=/plugin/i]${NAMES_NOT_ABOUT_TENDER.map((name) => `:not([name="${name}"])`).join("")}`,
  message:
    "Tender is not a plugin: name this after what it is (the panel, the backend, Tender). A third-party name that must keep the word goes on NAMES_NOT_ABOUT_TENDER in eslint.config.js, with its reason.",
}));

// A token shaped like a minified class hash: twelve or more letters, digits,
// `_` and `-`, holding a digit, a capital and a small letter. What that shape
// misses is listed at the rule's entry in CLAUDE.md's invariant register.
const CLASS_HASH = String.raw`(^|[^\w-])(?=[\w-]*\d)(?=[\w-]*[A-Z])(?=[\w-]*[a-z])[\w-]{12,}([^\w-]|$)`;

// A regex literal's `value` is a RegExp, which the `value` selector never
// matches, so its pattern is read separately.
const NO_CLASS_HASHES = [
  `Literal[value=/${CLASS_HASH}/]`,
  `Literal[regex.pattern=/${CLASS_HASH}/]`,
  `TemplateElement[value.raw=/${CLASS_HASH}/]`,
].map((selector) => ({
  selector,
  message:
    'This looks like one of Steam\'s minified class hashes, which a Steam update changes. Find the element through the selector ladder instead (docs/architecture/desktop-dom-architecture.md, "The selector ladder").',
}));

// The resolver's codes and the watched values of its other closed vocabularies,
// taken from the vendored resolver, and the answer field names the backend's
// check watches (`WATCHED_NAMES`), all written by
// `scripts/check_resolver_reading.py --write`, which also fails when the file
// is not what it would write today.
const { codes, values, names } = JSON.parse(
  readFileSync(path.join(import.meta.dirname, "resolver-vocabulary.json"), "utf8"),
);
const RESOLVER_VOCABULARY = { codes: [...codes, ...values], names };

// The files that read the resolver's vocabulary today, and what each reads.
// The list only shrinks: src/eslintNoResolverVocabulary.test.ts fails on an
// entry the file no longer needs and on one this list did not hold when it was
// written.
const RESOLVER_READERS_TODAY = {
  "src/bigpicture/EmulatorSelector.tsx": ["requirements_met"],
  "src/bigpicture/GameInfoTab.tsx": ["launchable"],
  "src/bigpicture/bios/BiosFiles.tsx": ["checked", "declared_kind", "mismatch", "satisfied"],
  "src/types/api.ts": ["launchable"],
  "src/types/firmware.ts": [
    "by-name",
    "by-name-then-content",
    "caveats",
    "checked",
    "declaration",
    "declared",
    "declared_kind",
    "known_as",
    "locating",
    "mismatch",
    "not-comparable",
    "packaged",
    "refused",
    "requirements_met",
    "satisfied",
    "supplied_by",
    "unestablished",
    "unrecognised",
    "verified",
  ],
  "src/utils/biosFetchable.ts": ["caveats", "declared_kind", "firmware-path-inaccessible"],
  "src/utils/biosFileNote.ts": [
    "caveats",
    "checked",
    "declaration",
    "declared_kind",
    "firmware-directory-holds-no-candidate",
    "firmware-directory-holds-no-image",
    "firmware-image-contradicted",
    "firmware-image-identified",
    "firmware-image-unlisted",
    "firmware-path-not-a-directory",
    "firmware-path-obstructed",
    "firmware-scan-incomplete",
    "firmware-unreadable",
    "mismatch",
    "not-comparable",
    "satisfied",
    "supplied_by",
    "unrecognised",
    "verified",
  ],
  "src/utils/biosGroup.ts": ["mismatch", "ntsc-j", "ntsc-u", "pal", "satisfied"],
  "src/utils/biosPlaceWords.ts": ["declared_kind", "satisfied"],
  "src/utils/biosSummary.ts": [
    "by-name",
    "caveats",
    "checked",
    "declared",
    "firmware-path-escapes-root",
    "firmware-path-names-no-file",
    "firmware-path-unresolvable",
    "locating",
    "refused",
    "satisfied",
    "unestablished",
  ],
  "src/utils/emulatorSourceWording.ts": [
    "bios-root-missing",
    "catalogue-invalid",
    "companion-config-missing",
    "config-unreadable",
    "content-tree-unwired",
    "emulator-config-missing",
    "emulator-config-unreadable",
    "installation",
    "marker-invalid",
    "marker-missing",
    "marker-unreadable",
    "not-set-up",
    "roms-root-missing",
    "roms-root-not-absolute",
    "root-missing",
    "saves-root-missing",
  ],
  "src/utils/firmwareIdentifications.ts": ["known_as"],
  "src/utils/launchTarget.ts": ["launchable"],
};

// Values of the resolver's closed vocabularies that the panel's own code also
// speaks with a meaning of its own, too widely to except file by file, and so
// are not watched in the panel: where each is spoken. The backend's check still
// watches them. The list only shrinks, as the one above does, and the test also
// fails on a word the panel's own code no longer speaks or the resolver no
// longer states.
const PANEL_GENERIC_VALUES = {
  background: "a CSS property in a style object",
  disabled: "whether a control takes a press, and whether save sync is switched off",
  tag: "a log line's prefix, and the label under a folder tile",
};

// Names that mean something else in a file, each with what it means there.
// The list only shrinks, as the two above do.
const NOT_THE_RESOLVERS = {
  // An artwork slot's outcome: Steam did not take the image.
  "src/bigpicture/ArtworkTab.tsx": ["refused"],
  // How many of the start-up check's searches into Steam's interface ran.
  "src/boot/steamModules.ts": ["checked"],
  // The panel bundle's own kind.
  "src/boot/searchingCopy.ts": ["standalone"],
  "src/types/virtual-bundle-kind.d.ts": ["standalone"],
  // Whether the launch gate could ask which emulator source starts the game.
  "src/utils/launchGate.ts": ["checked"],
  "src/utils/startingSource.ts": ["checked"],
  // A React key.
  "src/bigpicture/GameInfoTab.tsx": ["rom-name"],
  // An outcome of the adoption's own content check against RomM's checksums.
  "src/bigpicture/AdoptExistingModal.tsx": ["mismatch"],
  "src/types/downloads.ts": ["mismatch"],
  // Something carrying the game's name that a download cannot adopt.
  "src/bigpicture/AdoptUnusableModal.tsx": ["unusable"],
  // The key of a source report's own catalogue state.
  "src/types/emulatorSources.ts": ["catalogue"],
  "src/utils/emulatorSourceWording.ts": ["catalogue"],
  // The empty emulator setting Tender's own `bios-unchosen` note names, not the region source.
  "src/types/firmware.ts": ["setting"],
  // The release the update card on Main names.
  "src/utils/updateDot.ts": ["card"],
  // The conditions in Steam a toast still waits on.
  "src/utils/steamReadyForToasts.ts": ["unmet"],
  "src/utils/failedUpdateToast.ts": ["unmet"],
};

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
  // which. The fifteen rules below make the WRONG seam fail — they cannot certify
  // that a seam is right. A helper imported by exactly one parent, taking a
  // dozen parameters and doing nothing on its own, is neither a cycle nor a
  // direction violation and still passes.
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "import-x": importX },
    settings: {
      // Resolution decides what these rules see. The TS resolver handles path
      // aliases and index files. A resolver that misses imports would make the
      // rules quietly permissive.
      "import-x/resolver-next": [createTypeScriptImportResolver(), createNodeResolver()],
      // The files the plugin opens to read THEIR imports are those whose
      // extension is in `extensions` or listed under `parsers`
      // (`getFileExtensions`, eslint-plugin-import-x `lib/utils/ignore.js`), and
      // the default is JavaScript only. With `.ts`/`.tsx` in neither, an import
      // still resolves but its target is never read, and `no-cycle` finds no
      // cycle among our modules. Either line alone is enough, so the probe cycle
      // in src/eslintBoundaries.test.ts — the only thing that tells this apart
      // from "no cycles exist" — goes red only once both are gone.
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
                "utils/ is the bottom layer and must not reach up into a surface. Declare what you need to ask (see LaunchPrompts in utils/launchVerdict.ts) and let index.tsx supply it.",
            },
            {
              target: "./src/utils",
              from: "./src/desktop",
              message:
                "utils/ is the bottom layer and must not reach up into a surface. Declare what you need to ask (see LaunchPrompts in utils/launchVerdict.ts) and let the surface's entry point supply it.",
            },
            {
              target: "./src/utils",
              from: "./src/shared",
              message:
                "utils/ is the bottom layer and must not reach up into shared/. Declare what you need to ask (see LaunchPrompts in utils/launchVerdict.ts) and let index.tsx supply it.",
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
              target: "./src/api",
              from: "./src/shared",
              message: "api/ is the wire layer and has no business reaching into the view.",
            },
            {
              target: "./src/shared",
              from: "./src/bigpicture",
              message:
                "shared/ holds UI that belongs to both surfaces and sits below them, so it must not reach up into either. Code it needs from a surface belongs in shared/ itself or below it, in api/, utils/ or types/.",
            },
            {
              target: "./src/shared",
              from: "./src/desktop",
              message:
                "shared/ holds UI that belongs to both surfaces and sits below them, so it must not reach up into either. Code it needs from a surface belongs in shared/ itself or below it, in api/, utils/ or types/.",
            },
            {
              target: "./src/bigpicture",
              from: "./src/desktop",
              message:
                "bigpicture/ and desktop/ are peers, not layers: the two surfaces share data and logic and almost nothing visual, so neither may reach into the other. Anything that turns out to belong to both moves DOWN, never sideways: UI into shared/, the rest into api/, utils/ or types/.",
            },
            {
              target: "./src/desktop",
              from: "./src/bigpicture",
              message:
                "desktop/ and bigpicture/ are peers, not layers: the two surfaces share data and logic and almost nothing visual, so neither may reach into the other. Anything that turns out to belong to both moves DOWN, never sideways: UI into shared/, the rest into api/, utils/ or types/.",
            },
            {
              target: ["./src/!(qam)/**", "./src/!(index|index.test).{ts,tsx}"],
              from: "./src/qam",
              message:
                "qam/ is Tender's Quick Access entry, and nothing but index.tsx and its test imports it. What you need from it belongs below it, in api/, utils/ or types/.",
            },
            {
              target: ["./src/!(boot)/**", "./src/!(index|index.test).{ts,tsx}"],
              from: "./src/boot",
              message:
                "boot/ is start-up work — the globals bundle's own entry, and the check and failure page index.tsx runs before the panel mounts — and nothing but index.tsx and its test imports it. What you need from it belongs below it, in api/, utils/ or types/.",
            },
            {
              target: "./src/qam",
              from: ["./src/bigpicture", "./src/desktop"],
              message:
                "qam/ sits below the surfaces: it mounts the panel through the factory index.tsx hands it, and never reaches into a surface itself. UI it shares with a surface belongs in shared/.",
            },
            {
              target: "./src/types",
              from: "./src",
              except: ["./types"],
              message: "types/ holds type declarations only and imports nothing from the rest of src/.",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "src/index.tsx",
      "src/bigpicture/{MainPage,SyncPage,LibraryPage,SettingsPage,DataManagementPage,RemovedGamesCleanup,DownloadQueue}.tsx",
      "src/bigpicture/{SessionBudgetBanner,MigrationBlockedPage,SettingsResetBanner,PlaytimeScopeBanner,DownloadProgressRow,LoadingRow}.tsx",
      "src/bigpicture/{layout,sync,library,settings,data}/**/*.tsx",
    ],
    // A test renders no row a reader walks, and a modal is not a panel row at all —
    // it mounts in Steam's `ModalRoot`, outside the region that scrolls by focus.
    // Neither exemption is load-bearing today: with both removed the rule still
    // reports nothing across `src/**/*.tsx`.
    ignores: ["src/**/*.test.tsx", "src/bigpicture/**/*Modal.tsx"],
    plugins: { tender },
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
    // Build-time scripts run under Node, not in Steam's window: `process`,
    // `console` and `URL` are theirs. Scoped to `scripts/` rather than added to
    // the `src/` block, because a module under `src/` reaching for `process`
    // ships a reference that resolves to nothing inside Steam.
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      globals: { ...globals.node },
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
    files: ["src/**/*.{test,spec}.{ts,tsx}", "src/test-setup.ts", "src/test-utils/**/*.ts"],
    languageOptions: {
      globals: { ...globals.vitest },
    },
    rules: {
      // Anonymous mock components are fine — they don't appear in real render trees.
      "react/display-name": "off",
    },
  },
  // Tender is not a plugin, and no name in this package says it is — every file
  // ESLint lints, tests and config files included. What it reads is the names
  // themselves: a string literal (a test title, a `"plugin_version"` key) and a
  // comment are prose, out of its sight. A later block that sets
  // `no-restricted-syntax` for some files replaces these options there rather
  // than adding to them, so such a block repeats this entry.
  {
    rules: {
      "no-restricted-syntax": ["error", ...NAMES_THAT_MISNAME_TENDER],
    },
  },
  // No code under src/desktop/ names one of Steam's minified class hashes: a
  // string, template part or regex literal holding a token shaped like one
  // fails. Test files are left out, because a test builds Steam's page out of
  // such classes.
  {
    files: ["src/desktop/**/*.{ts,tsx}"],
    ignores: ["src/**/*.{test,spec}.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": ["error", ...NAMES_THAT_MISNAME_TENDER, ...NO_CLASS_HASHES],
    },
  },
  // No code in the panel reads the resolver's codes or answer field names: the
  // backend's reading module turns the answer into Tender's own marks, and the
  // panel words those.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/**/*.{test,spec}.{ts,tsx}", "src/test-setup.ts", "src/test-utils/**"],
    plugins: { tender },
    rules: {
      "tender/no-resolver-vocabulary": [
        "error",
        {
          ...RESOLVER_VOCABULARY,
          readers: RESOLVER_READERS_TODAY,
          notTheResolvers: NOT_THE_RESOLVERS,
          generic: PANEL_GENERIC_VALUES,
        },
      ],
    },
  },
  // Must stay LAST: turns off ESLint rules that conflict with Prettier formatting
  // so the two tools don't fight. Prettier owns formatting; ESLint owns correctness.
  eslintConfigPrettier,
);
