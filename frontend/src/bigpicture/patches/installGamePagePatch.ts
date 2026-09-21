// coverage-exempt: every line reaches Steam — its own webpack registry, its
// minified module exports and the live React fiber tree — none of which the
// test runner has; what is decidable without them is `gamePageSeam.ts`.
/**
 * Putting Tender's section on Steam's game page.
 *
 * **Nothing here is reachable from a test.** The module the page is drawn from
 * is found by reading the source text of the factories a pass over the loaded
 * exports names by their shape — and of the whole registry only where that pass
 * names none; its exports are patched through `@decky/ui`, and a page that is
 * already open is adopted by walking React's live fiber tree. happy-dom has
 * none of that, so a test here would assert against a registry and a tree
 * written for it and would pass whatever Steam does. What IS decidable — which
 * factory, which exports, and what one render is owed — is `gamePageSeam.ts`,
 * and that half is covered.
 * `qam/installEntry.tsx` is exempt for the same reason and is the Quick Access
 * strip's half of the same job.
 *
 * Which seam this installs on, and why it is not the page component's own
 * `type`, is on `docs/architecture/frontend-bundles.md`.
 */

import { afterPatch, beforePatch, getReactRoot, modules, type GenericPatchHandler, type Patch } from "@decky/ui";

import {
  hasRouteModuleShape,
  matchesAppDetailsFactory,
  patchableMemos,
  soleMatchingFactory,
  wrapRouteRenderFunc,
} from "./gamePageSeam";

/** Steam's own webpack `require`, reduced to the map of module factories. */
interface SteamWebpackRequire {
  readonly m: Record<string, unknown>;
}

/**
 * Steam's webpack `require`, obtained the one way it is reachable from outside
 * the bundle: pushing a chunk whose factory is handed it.
 *
 * The chunk is keyed by a fresh Symbol, so it registers no module and runs its
 * factory once. `@decky/ui` reads the registry the same way, and the two
 * pushes do not interfere.
 *
 * Answers nothing where Steam is not the page this runs in — the global is the
 * bundle's, so its absence is not a fault to report but the ordinary shape of
 * every other host.
 */
function steamWebpackRequire(): SteamWebpackRequire | undefined {
  let found: SteamWebpackRequire | undefined;
  try {
    window.webpackChunksteamui.push([
      [Symbol("tender")],
      {},
      (webpackRequire: SteamWebpackRequire) => {
        found = webpackRequire;
      },
    ]);
  } catch {
    return undefined;
  }
  return found;
}

/** One factory's source text, or nothing where it cannot be read. */
function factorySource(webpackRequire: SteamWebpackRequire, id: string): string | undefined {
  try {
    return String(webpackRequire.m[id]);
  } catch {
    return undefined;
  }
}

/** The named factories' sources, read one at a time. */
function* sourcesOf(webpackRequire: SteamWebpackRequire, ids: Iterable<string>): Generator<readonly [string, string]> {
  for (const id of ids) {
    const source = factorySource(webpackRequire, id);
    if (source !== undefined) yield [id, source];
  }
}

/** Every module Steam ships, in the order its registry holds them. */
const everyFactoryId = (webpackRequire: SteamWebpackRequire): Iterable<string> => Object.keys(webpackRequire.m);

/**
 * The modules whose exports have the shape the route module has.
 *
 * Read off values already loaded, so reaching one costs a property read where
 * reaching a factory's source costs the source text of a module.
 */
function* shapedFactoryIds(): Generator<string> {
  for (const [id, exports] of modules) {
    if (hasRouteModuleShape(exports)) yield id;
  }
}

/** What a search over Steam's factories answers with, once it has been asked. */
type FactorySearch = () => readonly object[];

/**
 * Ask Steam's registry for the one module a predicate matches, and answer with
 * the exports of it a patch can be installed on.
 *
 * Asked at most once. Both the start-up check and the install want the answer
 * and the search can reach every module Steam ships, so the first reader pays
 * for it and every later one is handed what it found.
 *
 * Nothing invalidates it: Steam's registry is built before any panel is loaded
 * and a rebuild of it is a rebuild of the JS context, which takes this module
 * with it.
 */
function searchSteamFactories(matches: (source: string) => boolean): FactorySearch {
  let answer: readonly object[] | undefined;
  return () => (answer ??= readPatchableExports(matches));
}

/**
 * Which module the route comes from, asked of the cheap set first.
 *
 * The predicate that decides is the factory's source text, and reading the
 * source of every module Steam ships is not free at start-up — the check asks
 * for this answer before the panel mounts. So the shape pass goes first and
 * only its handful of sources are read; the full scan stands behind it for the
 * day Steam gives that module an export of another kind, and reads a superset
 * of the same sources.
 *
 * **The refusal a second match earns is taken per pass, so the two can
 * disagree**: where two modules carry the three property names and one of them
 * has the shape, this answers with that one where the full scan alone would
 * refuse. Why that answer is the one to keep, and what it costs where the shape
 * is the misleading half, is on `docs/architecture/frontend-bundles.md`.
 */
function routeModuleId(webpackRequire: SteamWebpackRequire, matches: (source: string) => boolean): string | undefined {
  return (
    soleMatchingFactory(sourcesOf(webpackRequire, shapedFactoryIds()), matches) ??
    soleMatchingFactory(sourcesOf(webpackRequire, everyFactoryId(webpackRequire)), matches)
  );
}

function readPatchableExports(matches: (source: string) => boolean): readonly object[] {
  const webpackRequire = steamWebpackRequire();
  if (!webpackRequire) return [];
  const id = routeModuleId(webpackRequire, matches);
  if (id === undefined) return [];
  return patchableMemos(modules.get(id));
}

/**
 * The module Steam's gamepad library renders the game page's route from —
 * reduced to the exports a patch can be installed on, and empty when Steam
 * carries no such module any more.
 *
 * Read by the start-up check, which reports the empty answer as a miss, and by
 * the install below, which is the only thing that can act on it.
 */
export const AppDetailsRoute = searchSteamFactories(matchesAppDetailsFactory);

/** One node of React's live fiber tree, reduced to what the adoption reads. */
interface Fiber {
  elementType?: unknown;
  type?: unknown;
  child?: Fiber | null;
  sibling?: Fiber | null;
  alternate?: Fiber | null;
}

function* fibersFrom(root: Fiber): Generator<Fiber> {
  const pending: Fiber[] = [root];
  for (let node = pending.pop(); node; node = pending.pop()) {
    yield node;
    if (node.child) pending.push(node.child);
    if (node.sibling) pending.push(node.sibling);
  }
}

/**
 * Make a game page that is already open pick the patch up, instead of leaving
 * it rendering through the component it mounted with.
 *
 * React resolves a `memo` when it mounts and carries the resolved function on
 * the fiber, so replacing the export afterwards reaches nothing already on
 * screen. That is React's own behaviour rather than `@decky/ui`'s, which
 * supplies the patcher and not the resolution — the same reason
 * `qam/installEntry.tsx` adopts an already-mounted Quick Access menu.
 *
 * The tree walked is the host root's `current` fiber and not the one hanging
 * off the container element: React double-buffers, and the fiber the element
 * carries can be the twin that is not being rendered.
 */
function adoptMountedPage(memos: readonly object[]): void {
  const host = document.getElementById("root");
  if (!host) return;
  const root = getReactRoot(host) as { stateNode?: { current?: Fiber } } | null | undefined;
  const current = root?.stateNode?.current;
  if (!current) return;
  for (const fiber of fibersFrom(current)) {
    const mounted = fiber.elementType;
    if (!memos.includes(mounted as object)) continue;
    const resolved = (mounted as { type?: unknown }).type;
    if (typeof resolved !== "function") continue;
    fiber.type = resolved;
    if (fiber.alternate) fiber.alternate.type = resolved;
  }
}

/** What an install answers with. */
export interface GamePagePatchHandle {
  /** Did anything get patched? `false` means no Tender section will appear. */
  readonly installed: boolean;
  /** Take the patch back off. Silent where nothing was installed. */
  unpatch(): void;
}

/**
 * Install `renderPatch` on the game page's route, and adopt a page that is
 * already open.
 *
 * The patch goes on the ROUTE component's `renderFunc` — reached by patching
 * the memo's `type` and wrapping the props React renders it with — rather than
 * on the page component the section is inserted into. Why the page component's
 * own `type` is the wrong seam is on
 * `docs/architecture/frontend-bundles.md`: a tree patcher caches the wrapped
 * component per original type, so once any program has wrapped the page
 * component, every later render goes through that cached copy and a patch
 * installed on the original is never entered again.
 *
 * What `unpatch` takes back is the patch on the memo; a props object already
 * wrapped keeps its wrapper until its element is built again
 * ({@link wrapRouteRenderFunc}).
 */
export function installGamePagePatch(renderPatch: GenericPatchHandler): GamePagePatchHandle {
  const memos = AppDetailsRoute();
  if (memos.length === 0) return { installed: false, unpatch: () => {} };

  const wrapped = new WeakSet<object>();
  const patches: Patch[] = memos.map((memo) =>
    beforePatch(memo, "type", (args: unknown[]) => {
      wrapRouteRenderFunc(args, wrapped, (props) => {
        afterPatch(props, "renderFunc", renderPatch);
      });
    }),
  );
  adoptMountedPage(memos);

  return {
    installed: true,
    unpatch: () => {
      for (const patch of patches) patch.unpatch();
    },
  };
}
