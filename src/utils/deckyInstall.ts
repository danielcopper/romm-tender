/**
 * Handing an update over to Decky's own installer.
 *
 * Decky's `utilities/install_plugin` does not install anything. It files a
 * REQUEST, and the install happens only once the user confirms in Decky's own
 * dialog — so what this module does is ask Decky to offer the update, never
 * perform one.
 *
 * The route is reached through {@link DeckyBackend} because `@decky/api`'s
 * `callable` addresses this plugin's own routes and cannot reach the loader's.
 * Every failure is surfaced as a rejection rather than swallowed: with no
 * installer to hand the update to, the only thing left for the user is the
 * address, and a button that quietly did nothing would never tell them that.
 */

/**
 * Decky's `install_plugin` install type for replacing an already-installed
 * plugin (UPDATE). Read off Decky's own bundle; it steers only the wording of
 * the dialog Decky puts up, not what gets written.
 */
export const INSTALL_TYPE_UPDATE = 2;

const INSTALL_ROUTE = "utilities/install_plugin";

export interface PluginInstallRequest {
  /** The zip to fetch — the release's download address. */
  artifact: string;
  /**
   * plugin.json's name, exactly as the backend read it. This is what Decky
   * matches an already-installed plugin against: any other spelling misses the
   * match, so the previous installation is never removed and a second plugin
   * folder appears beside it with nothing failing loudly.
   */
  name: string;
  /**
   * The release's version. Never `"dev"` — Decky branches on that value and
   * installs from a different source entirely.
   */
  version: string;
  /**
   * The asset's bare sha256 hex, passed through untouched: Decky compares it
   * against `sha256(zip).hexdigest()`, so a `sha256:` prefix would never match.
   * `null` where the release carried no digest.
   */
  hash: string | null;
}

/**
 * Ask Decky to offer this update.
 *
 * Resolves once the request is filed, which is BEFORE any install happens.
 * Callers must not gate their UI on it either way: the loader unloads this
 * plugin as part of applying the update, so the promise may never settle at
 * all and the surface that started it can be torn down mid-call.
 *
 * Rejects when the loader's bridge is absent and when the route is not there —
 * an unknown route answers `Python RouteNotFoundError: Route <name> does not
 * exist.` — so a caller can fall back to telling the user the address.
 */
export async function requestPluginInstall(request: PluginInstallRequest): Promise<void> {
  // NOSONAR(typescript:S7741) — DeckyBackend is the loader's global; a direct
  // `=== undefined` would throw ReferenceError when it is genuinely absent.
  if (typeof DeckyBackend === "undefined" || typeof DeckyBackend.call !== "function") {
    throw new Error("Decky's plugin installer is not reachable from here.");
  }
  await DeckyBackend.call(
    INSTALL_ROUTE,
    request.artifact,
    request.name,
    request.version,
    request.hash,
    INSTALL_TYPE_UPDATE,
  );
}
