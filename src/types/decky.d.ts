/**
 * `DeckyBackend` — Decky Loader's own frontend↔backend bridge, as it exists in
 * SharedJSContext. Decky's `static/index.js` assigns it onto `window`, so
 * plugin code running in the same realm reaches it as a bare global; `call`
 * and `callable` are both functions on it, and arguments after the route name
 * arrive at the Python side positionally.
 *
 * `@decky/api`'s own `callable` cannot stand in for it: that one is wired to
 * the calling plugin's own routes and can never address Decky's `utilities/`.
 *
 * Typed as possibly `undefined`, and read behind a `typeof` probe for the same
 * reason `SteamUIStore` is: a bare reference to a genuinely-absent global
 * throws `ReferenceError`, and this one belongs to the loader rather than to
 * this plugin, so a Decky that renamed or dropped it must degrade rather than
 * break the panel.
 */
declare var DeckyBackend:
  | {
      call(route: string, ...args: unknown[]): Promise<unknown>;
    }
  | undefined;
