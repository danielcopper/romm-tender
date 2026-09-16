/**
 * The drift lock between Tender's React globals and Decky Loader's.
 *
 * Decky skips its whole globals block when `SP_REACT` is already set, so on a
 * machine carrying both, whichever runs first decides the shape BOTH render
 * through. That makes this a cross-project invariant with no diff that shows
 * both halves: `steamGlobals.ts` is ours, `decky-globals-block.txt` is
 * upstream's, and nothing but this file holds them against each other.
 *
 * What it compares is the SEARCH PREDICATES, because they are the shape: two
 * searches reading different properties answer with different modules, and a
 * different React is what breaks Decky's interface. Everything else in the two
 * files — the wait, the logging, the report — may differ freely and does.
 *
 * **Both sides are read as TEXT, including ours.** Reading our own predicates
 * back through `Function.prototype.toString` would compare whatever the test
 * transform emitted rather than what a reviewer sees, and it would need the
 * module imported — which drags in `@decky/ui`'s module-cache sweep for a
 * question that is purely about source. Reading the file keeps the two sides
 * symmetric: one extractor, applied twice.
 *
 * A failure here is not a test to fix. It means one of the two has moved, and
 * which one is the question to answer before anything is edited.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The Vitest root, which is this package's directory. `import.meta.url` is not a
// `file:` URL under the happy-dom environment, so `fileURLToPath` throws on it —
// the same reason `test-utils/componentSources.ts` resolves its root this way.
const BOOT_DIR = `${process.cwd()}/src/boot`;

const PINNED = readFileSync(`${BOOT_DIR}/decky-globals-block.txt`, "utf8");
const OURS = readFileSync(`${BOOT_DIR}/steamGlobals.ts`, "utf8");

const BEGIN = "--- BEGIN decky-loader frontend/src/index.ts (globals block) ---";
const END = "--- END decky-loader frontend/src/index.ts (globals block) ---";

/** Decky's block, exactly as pinned — the text between the two markers. */
function pinnedBlock(): string {
  const from = PINNED.indexOf(BEGIN);
  const to = PINNED.indexOf(END);
  if (from < 0 || to < 0) throw new Error("decky-globals-block.txt has lost its markers");
  return PINNED.slice(from + BEGIN.length, to);
}

/**
 * Every module-search argument in *source*, in order.
 *
 * Parentheses are balanced rather than matched by a regex: the JSX predicate
 * contains three nested pairs of its own, so a lazy regex stops inside the first
 * of them and a greedy one swallows the next call whole.
 *
 * The marker has no leading dot, which is what lets one extractor read both
 * files: Decky calls the finder off its namespace import and we call it bare.
 */
function searchPredicates(source: string): string[] {
  const found: string[] = [];
  const marker = "findModule(";
  for (let at = source.indexOf(marker); at >= 0; at = source.indexOf(marker, at + 1)) {
    let depth = 1;
    let cursor = at + marker.length;
    const start = cursor;
    while (cursor < source.length && depth > 0) {
      if (source[cursor] === "(") depth += 1;
      else if (source[cursor] === ")") depth -= 1;
      cursor += 1;
    }
    if (depth !== 0) throw new Error(`unbalanced module search at index ${at}`);
    found.push(source.slice(start, cursor - 1));
  }
  return found;
}

/**
 * Two predicates spelled the same way, whatever whitespace each carries.
 *
 * Collapsing whitespace is the ONLY licence taken, and it is needed because each
 * file is Prettier's wrapping of a different line length. Nothing else is
 * normalised: a renamed parameter, a reordered clause, or a `&&` turned into a
 * `?.` is drift, and is meant to fail.
 */
const canonical = (text: string): string => text.replace(/\s+/g, " ").trim();

describe("the React globals Tender installs", () => {
  it("searches for the same four things as Decky Loader, in the same order", () => {
    // Asserted as whole arrays rather than entry by entry, because the ORDER is
    // part of the claim: Decky's second ReactDOM search is a fallback behind the
    // first, and a build where they swapped would answer with the other module.
    expect(searchPredicates(OURS).map(canonical)).toEqual(searchPredicates(pinnedBlock()).map(canonical));
  });

  it("finds four searches in each file, so an extraction that stopped working cannot pass", () => {
    // Without this, a marker block or a call spelling that stopped matching
    // would yield two empty lists, the comparison above would pass, and the lock
    // would retire itself in silence.
    expect(searchPredicates(pinnedBlock())).toHaveLength(4);
    expect(searchPredicates(OURS)).toHaveLength(4);
  });

  it("assigns each search's answer to the same global Decky assigns it to", () => {
    // The predicates being identical is not enough on its own: a bundle that
    // searched the same way and put ReactDOM's answer on `SP_REACT` would pass
    // the comparison above and hand Decky the wrong module.
    // The trailing `[^=]` is what keeps a comparison out: `??=` and `=` are
    // assignments, `==` and `===` are not, and the two spellings differ only in
    // what follows.
    const assignments = (source: string) =>
      [...source.matchAll(/(SP_(?:REACTDOM|REACT|JSX))\s*\?{0,2}=[^=]/g)].map((match) => match[1]);
    expect(new Set(assignments(pinnedBlock()))).toEqual(new Set(["SP_REACT", "SP_REACTDOM", "SP_JSX"]));
    expect(new Set(assignments(OURS))).toEqual(new Set(assignments(pinnedBlock())));
  });

  it("builds the JSX stand-in out of the same keys, aliased the same way", () => {
    // When ours runs first, this object IS what Decky's own components are
    // compiled against, so its shape is as load-bearing as the predicates.
    // Optional chaining is stripped before comparing because it changes what
    // happens when the search MISSED, which the start-up check answers for, and
    // not what the object is when it hit.
    const standIn = (source: string): Record<string, string> => {
      const match = /\{\s*jsx:[\s\S]*?\}/.exec(source);
      if (!match) throw new Error("no JSX stand-in found in the source");
      const body = canonical(match[0])
        .slice(1, -1)
        .replace(/\?\./g, ".")
        .replace(/window\./g, "");
      return Object.fromEntries(
        body
          .split(",")
          .map((entry) => entry.trim())
          .filter(Boolean)
          .map((entry) => [entry.slice(0, entry.indexOf(":")).trim(), entry.slice(entry.indexOf(":") + 1).trim()]),
      );
    };
    const theirs = standIn(pinnedBlock());
    expect(Object.keys(theirs).sort()).toEqual(["Fragment", "jsx", "jsxs"]);
    // The aliasing itself, stated rather than assumed: `jsxs` takes `jsx`'s
    // value, which is the whole reason the stand-in exists.
    expect(theirs.jsxs).toBe(theirs.jsx);
    expect(standIn(OURS)).toEqual(theirs);
  });
});
