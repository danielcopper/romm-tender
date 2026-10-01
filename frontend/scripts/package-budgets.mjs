/**
 * What each third-party package puts into each bundle, and whether that is what
 * `package-budgets.json` allows. Why the budgets exist and how to add one:
 * `docs/architecture/frontend-bundles.md`, "Third-party package budgets".
 */

const NODE_MODULES = "/node_modules/";

/**
 * The package a module belongs to, or `null` for the panel's own code.
 *
 * The LAST `node_modules/` segment decides, because pnpm puts every package at
 * `node_modules/.pnpm/<name>@<version>/node_modules/<name>/` — the first segment
 * names only the store. An id starting with `\0` is a module a plugin made up —
 * the bundle stamp, or `@rollup/plugin-commonjs`'s helpers and proxies — which
 * no package shipped.
 */
export function packageOf(id) {
  if (id.startsWith("\0")) return null;
  const path = id.replace(/\\/g, "/");
  const at = path.lastIndexOf(NODE_MODULES);
  if (at === -1) return null;
  const [first, second] = path.slice(at + NODE_MODULES.length).split("/");
  if (!first) return null;
  if (!first.startsWith("@")) return first;
  return second ? `${first}/${second}` : null;
}

/**
 * Bytes per package over a chunk's modules, given as `[id, bytes]` pairs.
 *
 * A package whose every module rendered to nothing is not in the bundle, so it
 * is left out rather than recorded at zero.
 */
export function packageBytes(modules) {
  const bytes = new Map();
  for (const [id, moduleBytes] of modules) {
    const name = packageOf(id);
    if (name === null || moduleBytes === 0) continue;
    bytes.set(name, (bytes.get(name) ?? 0) + moduleBytes);
  }
  return Object.fromEntries([...bytes].sort(([a], [b]) => a.localeCompare(b)));
}

/**
 * Every way the record falls short of the budgets, one sentence each; empty when
 * it does not.
 *
 * `record` is the build's record, or `null` when there is none. `digests` maps a
 * bundle's file name to the SHA-256 of what `dist/` holds under it now, or to
 * `null` when it holds nothing.
 */
export function budgetProblems({ budgets, record, digests }) {
  if (record === null) {
    return ["there is no package record — run `pnpm -C frontend build`; this checks what the build recorded."];
  }
  const problems = [];
  for (const bundle of Object.keys(record)) {
    if (!(bundle in budgets)) {
      problems.push(`${bundle} is recorded but has no entry in package-budgets.json — give it one.`);
    }
  }
  for (const [bundle, allowed] of Object.entries(budgets)) {
    const entry = record[bundle];
    if (entry === undefined) {
      problems.push(`${bundle} has budgets but no record — run \`pnpm -C frontend build\`.`);
      continue;
    }
    if (digests[bundle] == null) {
      problems.push(`${bundle} is not in dist/ — run \`pnpm -C frontend build\`.`);
      continue;
    }
    if (digests[bundle] !== entry.sha256) {
      problems.push(
        `the record of ${bundle} describes an older build than dist/ holds — run \`pnpm -C frontend build\`.`,
      );
      continue;
    }
    for (const [name, bytes] of Object.entries(entry.packages)) {
      if (!(name in allowed)) {
        problems.push(
          `${bundle}: ${name} puts ${bytes} B into it and has no budget — give it one deliberately in ` +
            "package-budgets.json.",
        );
      } else if (bytes > allowed[name]) {
        problems.push(`${bundle}: ${name} puts ${bytes} B into it, over its budget of ${allowed[name]} B.`);
      }
    }
    for (const name of Object.keys(allowed)) {
      if (!(name in entry.packages)) {
        problems.push(
          `${bundle}: ${name} has a budget but puts nothing into it — remove the budget, so the list stays the ` +
            "packages the bundle carries.",
        );
      }
    }
  }
  return problems;
}
