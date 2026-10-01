/**
 * Holds what the build recorded per third-party package against
 * `package-budgets.json`, and fails on every difference `budgetProblems` names.
 * Why the budgets exist and how to add one: `docs/architecture/frontend-bundles.md`,
 * "Third-party package budgets".
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { budgetProblems } from "./package-budgets.mjs";

const FRONTEND = new URL("../", import.meta.url);
const DIST = new URL("../../dist/", import.meta.url);

function readJson(name) {
  try {
    return JSON.parse(readFileSync(new URL(name, FRONTEND), "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function digestOf(bundle) {
  try {
    return createHash("sha256")
      .update(readFileSync(new URL(bundle, DIST)))
      .digest("hex");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

const budgets = readJson("package-budgets.json");
if (budgets === null) {
  console.error("ERROR: frontend/package-budgets.json is missing.");
  process.exit(1);
}
const record = readJson("bundle-packages.json");
const bundles = new Set([...Object.keys(budgets), ...Object.keys(record ?? {})]);
const digests = Object.fromEntries([...bundles].map((bundle) => [bundle, digestOf(bundle)]));

for (const [bundle, entry] of Object.entries(record ?? {})) {
  const packages = Object.entries(entry.packages).map(([name, bytes]) => {
    const budget = budgets[bundle]?.[name];
    return `${name} ${bytes} B (${budget === undefined ? "no budget" : `budget ${budget} B`})`;
  });
  console.log(`${bundle}: ${packages.length > 0 ? packages.join(", ") : "no third-party package"}`);
}

const problems = budgetProblems({ budgets, record, digests });
if (problems.length > 0) {
  console.error("\nERROR: the third-party packages in the bundles are not what package-budgets.json allows.\n");
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}

console.log("OK: every third-party package in every bundle is within a budget of its own.");
