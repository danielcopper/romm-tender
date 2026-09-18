/**
 * Every `function` declaration not nested in another, with its body.
 *
 * What a drift lock over a DEPENDENCY's shipped code needs and a regex cannot
 * give: a brace or a paren inside a string literal, a comment or a regex
 * literal is not structure. `@decky/ui`'s own `dist/utils/react/react.js` opens
 * `createPropListRegex` with `let regexString = fromStart ? "const\{" : ""`, so
 * a counter that reads that `{` runs to the end of the file and hands the caller
 * a body holding every function below it — so every read those functions make is
 * attributed to it, and nothing reports the attribution.
 *
 * So the scan skips strings (all three quotes, escapes, and a template's
 * `${…}`), line and block comments, and regex literals, and matches the
 * parameter list by balancing parens rather than by reading to the first `)`.
 * `async`, generators and `export default` are part of the header it reads.
 *
 * **A declaration nested inside another `function` declaration is not
 * reported** — the scan resumes past a body it has taken. One inside a block or
 * an arrow body still is (`dist/modules/Router.js:25` sits inside a `try`),
 * which costs nothing here: an export is never one of those. An anonymous
 * `export default function (` has no name and so is not a declaration this
 * reports either, which makes a helper nested inside one its own answer; the
 * installed package has none.
 */

/** One `function` declaration: its name, whether it is exported, and its body including both braces. */
export interface DeclaredFunction {
  readonly name: string;
  readonly exported: boolean;
  readonly body: string;
}

/**
 * After one of these, a `/` opens a regex literal rather than dividing.
 *
 * The standard heuristic. The limit that matters here is that `)` and `]` are
 * absent from the set, so a `/` after a parenthesised expression reads as
 * division; a regex written directly after one would then be scanned as code —
 * its quotes opening a string skip, its braces counted as structure — which is
 * the failure this whole module exists to avoid, one shape over. Nothing can
 * detect that from inside; against the installed package it is checkable, and
 * the start-up check's sweep does so, under "reads no slash in the installed
 * @decky/ui as division with a second slash after it on the line, which is the
 * one shape the regex heuristic cannot tell apart".
 */
const REGEX_PRECEDERS = new Set("(,=:[!&|?{};+-*%<>~^".split(""));

/** And after one of these words, for the same reason. */
const REGEX_KEYWORDS = new Set([
  "return",
  "typeof",
  "instanceof",
  "in",
  "of",
  "new",
  "delete",
  "void",
  "throw",
  "case",
  "do",
  "else",
]);

/** Whether the `/` at `i` opens a regex literal, judged by what significantly precedes it. */
function startsRegex(source: string, previous: number): boolean {
  if (previous < 0) return true;
  const char = source[previous]!;
  if (REGEX_PRECEDERS.has(char)) return true;
  if (!/[\w$]/.test(char)) return false;
  let start = previous;
  while (start > 0 && /[\w$]/.test(source[start - 1]!)) start -= 1;
  return REGEX_KEYWORDS.has(source.slice(start, previous + 1));
}

/**
 * The index just past the regex literal opening at `open`, or `-1` where the
 * literal does not close on its line — which means it was division after all.
 */
function skipRegex(source: string, open: number): number {
  let inClass = false;
  for (let i = open + 1; i < source.length; i += 1) {
    const char = source[i];
    if (char === "\n") return -1;
    if (char === "\\") {
      i += 1;
      continue;
    }
    if (char === "[") inClass = true;
    else if (char === "]") inClass = false;
    else if (char === "/" && !inClass) {
      let end = i + 1;
      while (end < source.length && /[a-z]/.test(source[end]!)) end += 1;
      return end;
    }
  }
  return -1;
}

/**
 * The index just past the string opening at `open`, whose quote is `source[open]`.
 *
 * A template's `${…}` is balanced rather than scanned for the closing backtick,
 * so a backtick inside an interpolation does not end the string early.
 */
function skipString(source: string, open: number): number {
  const quote = source[open];
  let i = open + 1;
  while (i < source.length) {
    const char = source[i];
    if (char === "\\") {
      i += 2;
      continue;
    }
    if (char === quote) return i + 1;
    i = quote === "`" && char === "$" && source[i + 1] === "{" ? skipBalanced(source, i + 1, "{", "}") : i + 1;
  }
  return source.length;
}

/** The index just past the comment starting at `i`, or `-1` where none does. */
function skipComment(source: string, i: number): number {
  if (source[i] !== "/") return -1;
  if (source[i + 1] === "/") {
    const newline = source.indexOf("\n", i);
    return newline === -1 ? source.length : newline;
  }
  if (source[i + 1] !== "*") return -1;
  const end = source.indexOf("*/", i + 2);
  return end === -1 ? source.length : end + 2;
}

/**
 * The index just past the string or regex literal starting at `i`, or `-1`
 * where neither does. `previous` is the last significant character's index,
 * which is what tells a regex literal from a division.
 */
function skipLiteral(source: string, i: number, previous: number): number {
  const char = source[i];
  if (char === '"' || char === "'" || char === "`") return skipString(source, i);
  if (char !== "/" || !startsRegex(source, previous)) return -1;
  return skipRegex(source, i);
}

/**
 * The index just past the delimiter closing the one that opens at `open`.
 *
 * Throws rather than answering the end of the file: an opener it cannot close
 * means the scan has lost the structure, and a caller handed a body running to
 * EOF would report a merged function as a real one. An unterminated string is
 * not judged here: `skipString` answers the end of the file, so inside a body
 * the walk arrives with its opener still open and throws, and at top level the
 * scan simply ends.
 */
function skipBalanced(source: string, open: number, opener: string, closer: string): number {
  let depth = 0;
  for (const i of codeOffsets(source, open)) {
    const char = source[i]!;
    if (char === opener) depth += 1;
    else if (char === closer) {
      depth -= 1;
      if (depth === 0) return i + 1;
    }
  }
  throw new Error(`Unbalanced ${opener}${closer} from offset ${open} — the source could not be scanned.`);
}

/** The index of the next character that is neither whitespace nor a comment. */
function nextCode(source: string, i: number): number {
  let at = i;
  while (at < source.length) {
    if (/\s/.test(source[at]!)) {
      at += 1;
      continue;
    }
    const comment = skipComment(source, at);
    if (comment === -1) return at;
    at = comment;
  }
  return source.length;
}

/**
 * Every offset from `from` that is neither comment nor literal, in order.
 *
 * The one place the skipping and the `previous` marker live: all three walks
 * here step through this, so none of them can come to its own reading of what
 * counts as code. A second copy would fail silently in the direction that
 * matters — a `/` taken as division by one walk and as a regex opener by
 * another, which is a body attributed to the wrong function and nothing said.
 *
 * A consumer that has swallowed a stretch of its own answers `next(resume)`
 * with the offset to carry on at; the marker then sits on the character before
 * it, which is what a consumer taking a body wants. Iterating with `for…of`
 * sends nothing, so every offset is visited and the marker advances one
 * character at a time, skipping whitespace.
 */
function* codeOffsets(source: string, from = 0): Generator<number, void, number | undefined> {
  let i = from;
  let previous = -1;
  while (i < source.length) {
    const comment = skipComment(source, i);
    if (comment !== -1) {
      i = comment;
      continue;
    }
    const literal = skipLiteral(source, i, previous);
    if (literal !== -1) {
      previous = literal - 1;
      i = literal;
      continue;
    }
    const resume = yield i;
    if (resume !== undefined) {
      previous = resume - 1;
      i = resume;
      continue;
    }
    if (!/\s/.test(source[i]!)) previous = i;
    i += 1;
  }
}

// The two branches are disjoint — one takes the generator's `*`, the other
// takes whitespace and can hold no `*` — so no stretch matches two ways and the
// scan cannot backtrack over one. The obvious `function\s*\*?\s*` can, and
// because each of its three parts may match nothing it also matches INSIDE a
// name: a method spelled `functionname(a) { … }` comes back from it as a
// declaration called `name`, carrying that method's body.
const HEADER = /(export\s+(?:default\s+)?)?(?:async\s+)?function(?:\s*\*\s*|\s+)(\w+)\s*\(/y;

/**
 * Every offset the walk reads as DIVISION rather than as a regex opener.
 *
 * The heuristic's exposure, made countable. A `/` read as division that is
 * really a regex opener is the invisible failure: the literal's own quotes and
 * braces are then scanned as code. Such a `/` always has its closing `/` later
 * on the same line, so a division-read `/` with no further `/` after it on its
 * line cannot be that mistake — which is what a sweep over a real package can
 * assert. Offsets where the line-close fallback rejected a literal are included,
 * because that is the same decision reached a different way.
 *
 * It answers for the offsets the walk reaches, and a template's `${…}` is
 * consumed whole by `skipString`, so a `/` read as division INSIDE an
 * interpolation is not reported and a sweep over a package cannot see a misread
 * one there.
 */
export function divisionSlashes(source: string): number[] {
  const offsets: number[] = [];
  // Every `/` the walk hands back is one `skipLiteral` refused — because
  // nothing could open a regex there, or because the opener found no closer on
  // its line — and those two refusals are exactly what this reports.
  for (const i of codeOffsets(source)) if (source[i] === "/") offsets.push(i);
  return offsets;
}

/** Whether a declaration may begin at `i` — not mid-identifier, not a member access. */
const startsAToken = (source: string, i: number): boolean => i === 0 || !/[\w$.]/.test(source[i - 1]!);

/**
 * Where the walk resumes past whatever begins at `i` — a declaration, which is
 * appended to `into`, or a header shape carrying no body — or `undefined` where
 * nothing this recognises begins there.
 */
function takeDeclaration(source: string, i: number, into: DeclaredFunction[]): number | undefined {
  if (!"fea".includes(source[i]!) || !startsAToken(source, i)) return undefined;
  HEADER.lastIndex = i;
  const match = HEADER.exec(source);
  if (!match) return undefined;
  const brace = nextCode(source, skipBalanced(source, i + match[0].length - 1, "(", ")"));
  if (source[brace] !== "{") return i + match[0].length;
  const end = skipBalanced(source, brace, "{", "}");
  into.push({ name: match[2]!, exported: Boolean(match[1]), body: source.slice(brace, end) });
  return end;
}

/** Every `function` declaration in `source` that is not nested in another. */
export function declaredFunctions(source: string): DeclaredFunction[] {
  const declared: DeclaredFunction[] = [];
  const walk = codeOffsets(source);
  let step = walk.next();
  while (!step.done) step = walk.next(takeDeclaration(source, step.value, declared));
  return declared;
}
