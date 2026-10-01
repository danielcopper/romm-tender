/**
 * Every component source under `bigpicture/` and `shared/`, as TEXT — what a
 * drift lock searches.
 *
 * A drift lock holds a wording in one module by failing when a component spells
 * it out for itself. Both of the BIOS locks used to name the components they
 * searched, and that is what failed: the list held two while three surfaces
 * rendered the states, the third went on spelling an older wording of them, and
 * nothing said so. **A surface missing from a hand-kept list is indistinguishable
 * from one that never drifted**, so correcting such a list's count leaves the
 * failure exactly where it was and only moves it to the next surface added.
 *
 * So the set is swept rather than listed: every `.tsx` under `bigpicture/` or
 * `shared/` that is not itself a test. A component added tomorrow is searched
 * because it exists, not because someone remembered.
 *
 * **Deriving the set from who IMPORTS the wording module would be wrong**, and
 * wrong in the one direction that matters: a surface wording a state for itself
 * is precisely a surface that does NOT import the module, so an import-derived
 * sweep would skip every file a lock exists to catch and pass green over all of
 * them.
 *
 * `.tsx` only, which is a real limit rather than a definition: a wording helper
 * extracted into a `.ts` beside its component is not swept. The components a
 * surface renders live under `bigpicture/` and `shared/`, and `src/utils` is
 * where a common wording legitimately lives, so a blanket `src/**` would fail on
 * the module that owns the phrases.
 */

import { globSync, readFileSync } from "node:fs";

/** One component source file: its repo-relative path, and its text. */
export interface ComponentSource {
  /** Relative to `src/`, POSIX-separated — the name a test case is reported
   *  under. */
  path: string;
  /** The file's whole text, comments included: a comment quoting a sentence the
   *  module owns is drift too, since the reasoning belongs where the words do. */
  source: string;
}

// The Vitest root, which is this package's directory — not `import.meta.url`,
// which is not a `file:` URL for a non-test module under the happy-dom
// environment and throws on `fileURLToPath`. A wrong root is caught below rather
// than assumed away: it finds no file, and the sweep refuses to answer with an
// empty set.
const SRC_DIR = `${process.cwd()}/src/`;

const SWEPT_PATTERNS = ["bigpicture/**/*.tsx", "shared/**/*.tsx"];

/**
 * Every component source a lock should search, sorted so the report is stable.
 *
 * Throws when any one pattern finds no file, rather than answering with what
 * the others found: an empty set passes every `not.toContain` there is, which is
 * the same vacuous green the locks' own "searches for something" cases exist to
 * refuse, and a pattern that matches nothing retires the locks over its
 * directory just as silently. A moved directory or a broken glob then fails
 * loudly, naming the pattern, instead of retiring part of both locks.
 */
export function componentSources(): ComponentSource[] {
  const files = SWEPT_PATTERNS.flatMap((pattern) => {
    const found = globSync(pattern, { cwd: SRC_DIR }).filter((relative) => !relative.endsWith(".test.tsx"));
    if (found.length === 0) {
      throw new Error(
        `No component sources found for ${pattern} under ${SRC_DIR} — a drift lock over nothing passes always.`,
      );
    }
    return found;
  })
    .map((relative) => relative.split(/[\\/]/).join("/"))
    .sort();
  return files.map((path) => ({ path, source: readFileSync(`${SRC_DIR}${path}`, "utf8") }));
}
