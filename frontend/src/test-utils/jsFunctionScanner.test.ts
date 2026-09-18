/**
 * The scanner's own lock: a brace or a paren that is not structure.
 *
 * The cases below are ones a counting regex gets wrong by handing back a body
 * that is not the function's own, which is silent: a body is a body, and the
 * reads it holds are attributed to whichever function was credited with it.
 */

import { describe, expect, it } from "vitest";

import { declaredFunctions, divisionSlashes } from "./jsFunctionScanner";

// The shapes at once. Each declaration carries one the scan has to get right:
// a `)` in a parameter default, a `{` inside a string literal and an escaped
// quote with a `}` behind it, with a line comment holding another brace inside
// the body; a block comment whose unbalanced brace spans two lines, and a
// backtick string inside a template's `${…}`; a regex literal carrying `"`,
// `{`, `(`, an escaped `\/` and a `[/"]` class; a regex after a keyword; and
// the three header shapes — `async`, `export default`, and a generator.
//
// The last two are division, which the walk must not read as a literal. In
// `divides` both slashes sit on ONE line with an opening brace between them, so
// a walk that has lost track of what precedes a `/` hides that brace and ends
// the body at the `}` after it. In `after` the `/` follows a `}` — which does
// open a literal elsewhere — and the closing `/` it would need is on a LATER
// line, inside a string; only the line-close fallback stops the scan reaching
// across to it and swallowing that string's opening quote.
const SOURCE = `
export function first(close = ")", other) {
  // a line comment with { an unbalanced brace
  const literal = "const\\{";
  const escaped = "quote \\"} and brace";
  return literal + close + other + escaped;
}
export async function second() {
  /* a block comment whose {
     unbalanced brace spans two lines */
  return \`template \${\`inner } backtick\`} end\`;
}
export default function third(a, b) {
  const pattern = /"\\(\\/{[/"]+/g;
  return pattern.test(a + b);
}
function* fourth() {
  return /"{/;
}
function divides(a, b) {
  const ratio = a / 2; if (ratio) { b = b / 3; }
  return b;
}
function after(a) {
  const ratio = {} / 2;
  const mark = "/{";
  return mark + ratio + a;
}
`;

describe("scanning a JavaScript source for its function declarations", () => {
  // Scanned inside each test rather than out here: a scanner that throws on
  // this fixture has to fail a test by name, not take the file down at
  // collection where the run reports every other test as passed beside it.
  const scan = () => declaredFunctions(SOURCE);

  it("finds each declaration once, in order, with its export flag", () => {
    const scanned = scan();
    expect(scanned.map((fn) => fn.name)).toEqual(["first", "second", "third", "fourth", "divides", "after"]);
    expect(scanned.map((fn) => fn.exported)).toEqual([true, true, true, false, false, false]);
  });

  it("reads the keyword as a keyword, so a name that merely contains it is not a declaration", () => {
    // `function` has to be a token of its own: the header takes the generator's
    // `*` or whitespace after it and nothing else. Without that, a method whose
    // name merely starts with the keyword is read as a declaration — the two
    // below answer `name` and `Helper`, each with the method's body attributed
    // to a function that does not exist, which is this module's own failure
    // shape. A bare CALL does not discriminate: it has no body, so neither
    // reading reports it.
    expect(declaredFunctions("const o = { functionname(a) { return a; } };")).toEqual([]);
    expect(declaredFunctions("class C { functionHelper(a) { return a; } }")).toEqual([]);
    expect(declaredFunctions("asyncHelper(1);")).toEqual([]);
    expect(declaredFunctions("myfunction(1);")).toEqual([]);
    // And each shape the keyword does open, spacing and all.
    const spellings = declaredFunctions(
      "function a() {}\nfunction* b() {}\nfunction *c() {}\nfunction*d() {}\n" +
        "export default function e() {}\nasync function f() {}\nexport async function g() {}",
    );
    expect(spellings.map((fn) => fn.name)).toEqual(["a", "b", "c", "d", "e", "f", "g"]);
    expect(spellings.map((fn) => fn.exported)).toEqual([false, false, false, false, true, false, true]);
  });

  it("ends each body at its own closing brace, so none absorbs its neighbour", () => {
    const [first, second, third, fourth, divides, last] = scan();
    expect(first!.body).toContain("const\\{");
    // Read the escaped quote as the string's closing one and the `}` behind it
    // ends this body inside that string, short of the return — a body that
    // still looks like one, and still excludes `second`.
    expect(first!.body.endsWith("other + escaped;\n}")).toBe(true);
    expect(first!.body).not.toContain("second");
    // The tail matters more than the head here: read the interpolation's inner
    // backtick as the string's own closing one and the body stops at the `}`
    // inside it, which still looks like a body and still excludes `third`.
    expect(second!.body).toContain("template");
    expect(second!.body.endsWith("end`;\n}")).toBe(true);
    expect(second!.body).not.toContain("third");
    expect(third!.body).toContain("pattern");
    expect(third!.body).not.toContain("fourth");
    expect(fourth!.body).toBe('{\n  return /"{/;\n}');
    expect(divides!.body.endsWith("return b;\n}")).toBe(true);
    expect(divides!.body).not.toContain("after");
    expect(last!.body).toBe('{\n  const ratio = {} / 2;\n  const mark = "/{";\n  return mark + ratio + a;\n}');
  });

  it("reads division as division, so no literal opens and swallows what follows", () => {
    // The other direction of the heuristic, and it has to bite on one line:
    // treat every `/` as opening a literal and the scan runs to the next one,
    // taking whatever sits between them with it.
    const divided = declaredFunctions('const r = a / 2; function hidden() { return "{"; } const q = b / 2;');
    expect(divided.map((fn) => fn.name)).toEqual(["hidden"]);
    // And once where nothing before the `/` was a character the header attempt
    // even looks at, so only the top-level marker itself can say what precedes
    // it.
    expect(declaredFunctions("b / 2; function probe() {} const q = c / 3;").map((fn) => fn.name)).toEqual(["probe"]);
  });

  it("reports the offsets it read as division, so a sweep for them is not asking an empty question", () => {
    // What the dist sweep in `boot/steamModules.test.ts` leans on. That sweep
    // finds nothing in the installed package because the package divides
    // nowhere, so the proof that an empty answer means "no division" rather
    // than "this stopped working" has to live here.
    expect(divisionSlashes("const r = a / 2;")).toEqual([12]);
    expect(divisionSlashes("const r = /a/.test(b);")).toEqual([]);
  });

  it("reports a source it cannot balance rather than answering to the end of it", () => {
    // The failure this replaces was silent: a body running to EOF still looks
    // like a body. A scanner that cannot finish has to say so.
    expect(() => declaredFunctions("function broken() { if (true) {")).toThrow(/Unbalanced/);
  });

  it("resumes past a body with the marker inside it, so the next line's regex still opens", () => {
    // The marker a body hands back is its own closing `}`, not the character
    // after it: a `/` on the next line is then read against a `}`, which opens
    // a literal, rather than against the newline, which does not. Read as
    // division, the `"` inside this regex opens a string that runs to the end
    // of the file and takes `g` with it.
    expect(declaredFunctions('function f() {}\n/"/;\nfunction g() { return 1; }').map((fn) => fn.name)).toEqual([
      "f",
      "g",
    ]);
  });

  it("resumes a header without a body with the marker on its last character, so a regex right after it still opens", () => {
    // The other stretch the walk swallows. Resuming here re-reads the parameter
    // list, so the `/` inside it is judged against the `(` the header ended on,
    // which opens a literal. Judged against the `/` itself — the marker one
    // character further on — it is division, and the `"` after it opens a
    // string that runs to the end of the file and takes `g` with it.
    expect(declaredFunctions('function f(/"/) ; function g() { return 1; }').map((fn) => fn.name)).toEqual(["g"]);
  });

  it("skips a helper nested in a function, so it is not a second answer", () => {
    const nested = declaredFunctions("export function outer() { async function inner() { return 1; } return inner; }");
    expect(nested.map((fn) => fn.name)).toEqual(["outer"]);
    expect(nested[0]!.body).toContain("inner");
  });

  it("still reports a declaration inside a block, which an export never is", () => {
    // `dist/modules/Router.js:25` is exactly this — a helper inside a `try`.
    const inBlock = declaredFunctions("try {\n  function helper() { return 1; }\n} catch {}");
    expect(inBlock.map((fn) => fn.name)).toEqual(["helper"]);
  });
});
