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
 * its components carry, and **none of them is written in any of this project's
 * own `src/**` TypeScript** — which is the property that makes them evidence
 * rather than decoration, and which the sweep below asserts in exactly those
 * terms. A string this project also writes would be found in both bundles and
 * prove nothing about either.
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

/**
 * Which bundle a bundle says it is, read off the artefact.
 *
 * `rollup.config.js` serves `virtual:tender-bundle-kind` once per panel build
 * with that build's own answer inside, and `src/boot/searchingCopy.ts` turns the
 * answer into the sentence the start-up failure page prints — our copy of
 * `@decky/ui` searched and missed, or Decky Loader's did. The stamp is asserted
 * here because rollup answers an unresolved import with a WARNING and an
 * external import: a build that lost the plugin would emit a bundle that throws
 * on a device, having said nothing worse than a line nobody reads. A stamp that
 * is simply the wrong one says nothing at all until a user is sent after the
 * wrong program.
 */
const stampedKind = (text) => /const BUNDLE_KIND\w* = "(standalone|coexistence)";/.exec(text)?.[1] ?? null;

/** How a wrong stamp reads in a finding — a stamp naming the other bundle, or none at all. */
const stampSays = (kind) => (kind === null ? "carries no bundle stamp at all" : `says it is the ${kind} bundle`);

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
//
// What is swept is `src/**/*.{ts,tsx}`, which is what the bundles are built from
// and is therefore the whole of what could contaminate one. It is stated rather
// than rounded up to "under src/": there is one non-TS file there today,
// `boot/decky-globals-block.txt`, and it is upstream Decky source that no bundle
// can reach — so the gap is harmless, and saying so is cheaper than a reader
// discovering the sweep is narrower than the sentence.
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
  // Reported AFTER the loop and from what it found: pushed before it, the line
  // said "none written" two lines above a finding naming one.
  report.push(
    `probe strings: ${DECKY_UI_IMPLEMENTATION.length}, ${contaminated.size} also written in ` +
      `src/**/*.{ts,tsx} (${sources.length} files swept)`,
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
  report.push(
    `globals.js: ${Buffer.byteLength(source)} B, sweep ${source.includes(sweep) ? "present" : "ABSENT"}, ${dfl} DFL.`,
  );
  if (!source.includes(sweep)) {
    findings.push("globals.js does not contain @decky/ui's module sweep — every search in it would answer undefined.");
  }
  // It is neither of the two panel bundles, so it has no answer to give and is
  // built without the plugin that serves one.
  if (stampedKind(source) !== null) {
    findings.push(
      `globals.js ${stampSays(stampedKind(source))}. It is the React bootstrap and carries no panel, so a stamp ` +
        "there is an answer to a question nothing in it asks.",
    );
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
  const kind = stampedKind(source);
  report.push(
    `index.js (standalone): ${Buffer.byteLength(source)} B, ${hits.length}/${DECKY_UI_IMPLEMENTATION.length} @decky/ui implementation strings, ${dfl} DFL., stamped ${kind ?? "NOTHING"}`,
  );
  if (kind !== "standalone") {
    findings.push(
      `index.js ${stampSays(kind)}. It carries its own @decky/ui, so its start-up failure page must send the user ` +
        "after Tender; stamped the other way it names Decky Loader, whose copy ran none of these searches, and " +
        "unstamped it carries a bare import of a module no browser can resolve and never loads at all.",
    );
  }
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
  const kind = stampedKind(source);
  report.push(
    `index-coexistence.js: ${Buffer.byteLength(source)} B, ${hits.length}/${DECKY_UI_IMPLEMENTATION.length} @decky/ui implementation strings, ${dfl} DFL., stamped ${kind ?? "NOTHING"}`,
  );
  if (kind !== "coexistence") {
    findings.push(
      `index-coexistence.js ${stampSays(kind)}. Its searches are run by Decky Loader's copy of @decky/ui, so ` +
        "stamped the other way its start-up failure page sends the user after Tender for a fault in Decky — which " +
        "is breaking Decky's own interface and its other plugins at the same moment.",
    );
  }
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
  report.push(`LICENSE-@decky-ui.txt: ${Buffer.byteLength(licence)} B`);
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
