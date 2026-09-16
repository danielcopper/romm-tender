/**
 * The build produces two panel bundles that differ in one thing, and this asserts
 * that they still do.
 *
 * `dist/index.js` carries `@decky/ui` inside it, so it loads where Decky Loader
 * is not running. `dist/index-coexistence.js` takes the package from Decky's
 * already-loaded copy through the `DFL` global, because bundling a second copy
 * beside a running Decky runs its module sweep a second time in one session and
 * kills the Quick Access menu with `Minified React error #31` — measured four
 * times on the device, taking the Big Picture window with it each time.
 *
 * **Neither failure is a build error, which is why this exists.** A standalone
 * bundle that lost the package has `DFL.` reads and nothing to answer them: it
 * throws on the first one, on a machine without Decky, and the build that
 * produced it said nothing. A coexistence bundle that gained the package is
 * worse — it builds, it loads, and it takes down the window.
 *
 * It asserts the ARTEFACT rather than a bundler setting, deliberately. The
 * tree-shaking question this cut had to answer (`@decky/ui` declares
 * `"sideEffects": false` and then sweeps Steam's whole module registry at import)
 * was settled by measuring three configurations, and none of them dropped the
 * sweep — see the note in `rollup.config.js`. A setting chosen on that
 * measurement is right for one version of Rollup and one version of the package;
 * this check is right for whatever they do next.
 */

import { readFileSync, statSync } from "node:fs";

const DIST = new URL("../../dist/", import.meta.url);

/**
 * Strings that exist only inside `@decky/ui`'s own implementation.
 *
 * Six are its module-cache machinery and its logger, four are search predicates
 * its components carry, and **none of them appears anywhere under `src/`** —
 * which is the property that makes them evidence rather than decoration. A
 * string this project also writes would be found in both bundles and prove
 * nothing about either; one candidate was dropped for exactly that reason
 * (`pane.tsx:262` quotes `@decky/ui`'s DialogButton predicate in a comment,
 * which survives into no bundle only because `removeComments` is on — evidence
 * resting on a compiler option is not evidence).
 */
const DECKY_UI_IMPLEMENTATION = [
  "Webpack Module Init",
  "Initializing all modules. Errors here likely do not matter",
  "Ignoring require error for module",
  "background: #16a085; color: black;",
  "%c @decky/ui %c",
  "initModuleCache",
  "shift-children-below",
  "Either closeModal or onCancel should be passed to GenericDialog",
  "bUpdateDisabled",
  "HideIfSubmenu",
];

/** How many times *needle* occurs in *text*. */
const count = (text, needle) => text.split(needle).length - 1;

/** How many of the implementation strings are in *text*. */
const implementationHits = (text) => DECKY_UI_IMPLEMENTATION.filter((needle) => text.includes(needle));

function read(name) {
  const path = new URL(name, DIST);
  try {
    statSync(path);
  } catch {
    throw new Error(`${name} is missing from dist/ — run the build first; this checks what the build produced.`);
  }
  return readFileSync(path, "utf8");
}

const findings = [];
const report = [];

// The React bootstrap. It imports `@decky/ui/dist/webpack` and nothing else from
// the package, so it must carry the sweep and none of the components — and it
// must never reach for `DFL`, since the whole point of it is the case where
// Decky has not run.
{
  const source = read("globals.js");
  const dfl = count(source, "DFL.");
  report.push(`globals.js: ${source.length} B, ${count(source, "initModuleCache")} initModuleCache, ${dfl} DFL.`);
  if (!source.includes("initModuleCache")) {
    findings.push("globals.js does not contain @decky/ui's module sweep — every search in it would answer undefined.");
  }
  if (dfl > 0) {
    findings.push(`globals.js reads DFL ${dfl} times — it runs where Decky has not, so nothing would answer.`);
  }
}

// STANDALONE.
{
  const source = read("index.js");
  const hits = implementationHits(source);
  const dfl = count(source, "DFL.");
  report.push(
    `index.js (standalone): ${source.length} B, ${hits.length}/${DECKY_UI_IMPLEMENTATION.length} @decky/ui implementation strings, ${dfl} DFL.`,
  );
  const absent = DECKY_UI_IMPLEMENTATION.filter((needle) => !source.includes(needle));
  if (absent.length > 0) {
    findings.push(
      `index.js is missing ${absent.length} of @decky/ui's implementation strings, so the package is not fully ` +
        `bundled: ${absent.map((needle) => JSON.stringify(needle)).join(", ")}`,
    );
  }
  if (dfl > 0) {
    findings.push(
      `index.js reads DFL ${dfl} times. It is the bundle for a machine with no Decky Loader, so the global that ` +
        `would answer does not exist and the first read throws.`,
    );
  }
}

// COEXISTENCE.
{
  const source = read("index-coexistence.js");
  const hits = implementationHits(source);
  const dfl = count(source, "DFL.");
  report.push(
    `index-coexistence.js: ${source.length} B, ${hits.length}/${DECKY_UI_IMPLEMENTATION.length} @decky/ui implementation strings, ${dfl} DFL.`,
  );
  if (hits.length > 0) {
    findings.push(
      `index-coexistence.js carries ${hits.length} of @decky/ui's implementation strings, so a second copy of the ` +
        `package is bundled into it: ${hits.map((needle) => JSON.stringify(needle)).join(", ")}. Loaded beside a ` +
        `running Decky this sweeps Steam's module registry a second time and kills the Quick Access menu.`,
    );
  }
  if (dfl === 0) {
    findings.push(
      "index-coexistence.js never reads DFL, so it is not taking @decky/ui from Decky's copy — which is the only " +
        "thing that distinguishes it from the standalone bundle.",
    );
  }
}

for (const line of report) console.log(line);

if (findings.length > 0) {
  console.error("\nERROR: the two panel bundles are not the two bundles they are supposed to be.\n");
  for (const finding of findings) console.error(`  - ${finding}`);
  process.exit(1);
}

console.log("OK: the standalone bundle carries @decky/ui, the coexistence bundle takes it from DFL.");
