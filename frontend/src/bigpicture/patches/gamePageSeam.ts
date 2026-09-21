/**
 * Where Tender's section joins Steam's game page — the half that is decidable
 * without Steam.
 *
 * Three questions stand between the patch and the page, and each one is
 * answerable off plain objects: which of Steam's module factories draws the
 * game page, which of that module's exports a patch can be installed on, and
 * what one render of the route component is owed. Reaching Steam at all — the
 * webpack registry, the patcher, the live fiber tree — is
 * `installGamePagePatch.ts`, which no test can drive.
 *
 * The seam itself, and why the page component's own `type` is not it, is on
 * `docs/architecture/frontend-bundles.md`.
 */

/**
 * The three property names Steam's app-details factory carries.
 *
 * Property names survive Steam's minification and local identifiers do not, so
 * these three are the only stable handle on the module: its id and its export
 * names are both minifier output and change with every Steam build. Matching a
 * number instead would also match SVG path coordinates.
 */
const APP_DETAILS_MARKS = ["renderFunc", "AppDetailsOverviewPanel", "InnerContainer"] as const;

/** Does this module factory's source text draw Steam's game page? */
export function matchesAppDetailsFactory(source: string): boolean {
  return APP_DETAILS_MARKS.every((mark) => source.includes(mark));
}

/**
 * The id of the ONE factory that matches, or nothing at all.
 *
 * Nothing is also the answer for more than one match, and deliberately so: a
 * second match means the predicate no longer identifies a single module, and
 * both patching them and picking between them install Tender's section
 * somewhere nobody has looked. The start-up check then reports the miss, which
 * is a fault a reader can act on where a section drawn in the wrong place is
 * not.
 */
export function soleMatchingFactory(
  sources: Iterable<readonly [string, string]>,
  matches: (source: string) => boolean,
): string | undefined {
  let match: string | undefined;
  for (const [id, source] of sources) {
    if (!matches(source)) continue;
    if (match !== undefined) return undefined;
    match = id;
  }
  return match;
}

const REACT_MEMO = Symbol.for("react.memo");

const isPatchableMemo = (value: unknown): value is object =>
  typeof value === "object" &&
  value !== null &&
  (value as { $$typeof?: unknown }).$$typeof === REACT_MEMO &&
  typeof (value as { type?: unknown }).type === "function";

/** One export read on its own, so a getter that throws costs only that export. */
function readExport(exports: object, name: string): unknown {
  try {
    return (exports as Record<string, unknown>)[name];
  } catch {
    return undefined;
  }
}

/**
 * Could this module be the one the game page's route comes from, judged by the
 * SHAPE of its exports alone?
 *
 * Every export a patchable memo, and more than one of them. That is what the
 * module looks like in Steam today — it exports the route component and the
 * page component and nothing else — and it is a question about values already
 * in hand, where asking what a factory's source says means reading the source
 * text of a module. What it is FOR is at `soleMatchingFactory`'s caller: this
 * narrows the set whose sources are read, and it decides nothing on its own.
 *
 * Being wrong about it usually costs only work: a module that should have
 * passed and did not is still reached by the full scan behind it, and one that
 * passes without carrying the three property names costs a source read and
 * fails the predicate that matters. The exception is where those names name
 * more than one module — there this predicate decides rather than narrows,
 * because the refusal an ambiguity earns is taken per pass. What that is worth
 * is on `docs/architecture/frontend-bundles.md`.
 */
export function hasRouteModuleShape(exports: unknown): boolean {
  if (typeof exports !== "object" || exports === null) return false;
  const names = Object.keys(exports);
  if (names.length < 2) return false;
  return names.every((name) => isPatchableMemo(readExport(exports, name)));
}

/**
 * The exports of that module a patch can be installed on: every `React.memo`
 * whose `type` is a function.
 *
 * All of them are taken rather than the route component picked out, because
 * nothing on an export says which one it is — the names are minified and they
 * differ only in what their bodies read. Patching them all is safe because the
 * handler acts on what React passes: only the route component is ever rendered
 * with a `renderFunc`, so on any other export the handler finds nothing to do.
 *
 * A webpack namespace defines its exports as getters, and one belonging to a
 * module whose own evaluation failed throws on read — so each is read on its
 * own and a throwing one is passed over rather than ending the search.
 */
export function patchableMemos(exports: unknown): object[] {
  if (typeof exports !== "object" || exports === null) return [];
  const found: object[] = [];
  for (const name of Object.keys(exports)) {
    const value = readExport(exports, name);
    if (isPatchableMemo(value)) found.push(value);
  }
  return found;
}

/**
 * What one render of the route component is owed: its `renderFunc` wrapped, if
 * it has one and nobody has wrapped this props object yet.
 *
 * Answers whether it wrapped, which is what a test can read; in Steam the
 * answer is discarded and the wrapping is the whole point.
 *
 * **The props object is the identity, not the component.** Decky Loader's
 * router hook clones a route's child as `(props) => createElement(oType,
 * props)`, so beside a Decky the route component is handed a FRESH props object
 * on every render and a guard kept per component would wrap the first one and
 * no other. Beside no Decky the same object comes back until the router
 * re-renders, which the set absorbs.
 *
 * **Nothing is retained per props object.** The wrap installs a patch and that
 * patch is not kept: `@decky/ui`'s `createReactTreePatcher` caches its work per
 * original component type, so the cost of a second props object is a cache hit,
 * and a props object dies with the element it was made for. Keeping the patches to
 * undo them would keep every props object alive for the life of the process,
 * and there is nothing to undo them WITH — the patch on the memo is what the
 * install takes back, and a props object already wrapped keeps its wrapper
 * until its element is built again, which is how Decky's own unpatch behaves
 * too.
 */
export function wrapRouteRenderFunc(
  args: readonly unknown[],
  wrapped: WeakSet<object>,
  wrap: (props: object) => void,
): boolean {
  const props = args[0];
  if (typeof props !== "object" || props === null) return false;
  if (typeof (props as { renderFunc?: unknown }).renderFunc !== "function") return false;
  if (wrapped.has(props)) return false;
  wrapped.add(props);
  wrap(props);
  return true;
}
