/**
 * The build produces two panel bundles that differ in one thing, and this asserts
 * that they still do.
 *
 * `dist/index.js` carries `@decky/ui` inside it, so it loads where Decky Loader
 * is not running. `dist/index-coexistence.js` takes the package from Decky's
 * already-loaded copy through the `DFL` global, because importing the package
 * re-executes every module in Steam's live webpack registry — and beside a
 * RENDERING Decky that is fatal: a module re-executed underneath something
 * holding its exports leaves an empty object where a component was, and the
 * Quick Access menu dies with `Minified React error #31`, taking the Big
 * Picture window with it. Measured on the device, where the three passes
 * without a rendering consumer all survived; the count of sweeps is not the
 * condition.
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

import { globSync, readFileSync, statSync } from "node:fs";

const DIST = new URL("../../dist/", import.meta.url);

/**
 * Strings that exist only inside `@decky/ui`'s own implementation.
 *
 * Five are its module-cache machinery and its logger, four are search predicates
 * its components carry, and **none of them appears anywhere under `src/`** —
 * which is the property that makes them evidence rather than decoration. A
 * string this project also writes would be found in both bundles and prove
 * nothing about either.
 *
 * That property is asserted below rather than claimed, because it has now failed
 * twice. `pane.tsx:262` quotes `@decky/ui`'s DialogButton predicate in a comment,
 * and `steamGlobals.ts` names `initModuleCache` in its own; both stay out of the
 * artefact only because `removeComments` is on, and evidence resting on a
 * compiler option is not evidence. Both were dropped from this list — the second
 * one after the sentence above had already been written and was already false.
 */
const DECKY_UI_IMPLEMENTATION = [
  "Webpack Module Init",
  "Initializing all modules. Errors here likely do not matter",
  "Ignoring require error for module",
  "background: #16a085; color: black;",
  "%c @decky/ui %c",
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

// The probe list's own premise, asserted rather than trusted.
//
// Each string is evidence only while this project writes none of it: one we also
// write would be in BOTH bundles, so the coexistence bundle's "0 of N" would be a
// finding about our own source rather than about `@decky/ui`. Comments count —
// they are what caught this twice — because a probe that survives only through
// `removeComments` rests on a compiler option instead of on a fact.
{
  const sources = globSync("src/**/*.{ts,tsx}", { cwd: new URL("..", import.meta.url).pathname });
  if (sources.length === 0) {
    findings.push("found no sources under src/ to check the probe strings against — the sweep proves nothing empty.");
  }
  const contaminated = new Map();
  for (const relative of sources) {
    const text = readFileSync(new URL(`../src/${relative.split("src/").pop() ?? relative}`, import.meta.url), "utf8");
    for (const needle of DECKY_UI_IMPLEMENTATION) {
      if (text.includes(needle)) contaminated.set(needle, relative);
    }
  }
  report.push(
    `probe strings: ${DECKY_UI_IMPLEMENTATION.length}, none written under src/ (${sources.length} files swept)`,
  );
  for (const [needle, where] of contaminated) {
    findings.push(
      `${JSON.stringify(needle)} is a probe string AND is written at ${where}. It would then be found in both ` +
        `bundles, so it is evidence about nothing. Drop it from the list and pick one @decky/ui alone writes.`,
    );
  }
}

// The React bootstrap. It imports `@decky/ui/dist/webpack` and nothing else from
// the package, so it must carry the sweep and none of the components — and it
// must never reach for `DFL`, since the whole point of it is the case where
// Decky has not run.
{
  const source = read("globals.js");
  const dfl = count(source, "DFL.");
  // Asked by a string from INSIDE the sweep's own loop rather than by its name:
  // `initModuleCache` is a word this repository also writes, and a probe our own
  // sources contain proves nothing about what reached the bundle.
  const sweep = "Ignoring require error for module";
  report.push(`globals.js: ${source.length} B, sweep ${source.includes(sweep) ? "present" : "ABSENT"}, ${dfl} DFL.`);
  if (!source.includes(sweep)) {
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
        `RENDERING Decky this re-executes the modules it is rendering from and kills the Quick Access menu.`,
    );
  }
  if (dfl === 0) {
    findings.push(
      "index-coexistence.js never reads DFL, so it is not taking @decky/ui from Decky's copy — which is the only " +
        "thing that distinguishes it from the standalone bundle.",
    );
  }
}

// The licence that has to travel with the bundled package. Bundling `@decky/ui`
// makes this project a distributor of LGPL-2.1 code, and the text is emitted by
// the standalone build alone — the coexistence bundle distributes none of it.
{
  let licence = "";
  try {
    licence = read("LICENSE-@decky-ui.txt");
  } catch {
    licence = "";
  }
  report.push(`LICENSE-@decky-ui.txt: ${licence.length} B`);
  if (!licence.includes("GNU LESSER GENERAL PUBLIC LICENSE")) {
    findings.push(
      "LICENSE-@decky-ui.txt is missing or is not the LGPL text. dist/index.js carries @decky/ui's code, which " +
        "makes this a distribution of it, and the licence travels with the work.",
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
