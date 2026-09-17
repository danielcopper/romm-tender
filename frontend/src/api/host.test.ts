/**
 * The six exports, against the real module.
 *
 * `test-setup.ts` replaces `api/host` for the whole suite, so this file has to
 * take the stub off again — otherwise it would assert that a `vi.fn()` behaves
 * like a `vi.fn()`. The two placeholders are the only members here with a body
 * worth reading; `callable`, `addEventListener` and `removeEventListener` are
 * one-line delegations to `HostSocket`, which `hostSocket.test.ts` drives
 * against a socket of its own.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

vi.unmock("./host");

import {
  addEventListener,
  callable,
  definePlugin,
  removeEventListener,
  routerHook,
  toaster,
  type Plugin,
} from "./host";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("definePlugin", () => {
  it("answers with the factory it was given", () => {
    // Whoever mounts the panel calls it — which today is nobody in this tree,
    // since the Quick Access entry that will is #1901.
    const factory = (): Plugin => ({ name: "Tender", icon: null });
    expect(definePlugin(factory)).toBe(factory);
  });
});

describe("the toaster placeholder", () => {
  it("hands back a dismissable handle carrying the toast, and shows nothing", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const data = { title: "Tender", body: "Downloaded Chrono Trigger" };

    const raised = toaster.toast(data);

    expect(raised.data).toBe(data);
    expect(() => raised.dismiss()).not.toThrow();
    // Nothing reaches the screen until #1901, so the log is the whole of what a
    // notice does — and the body is in it, because a log line that says only
    // "a toast happened" is worth nothing to whoever reads it.
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("Downloaded Chrono Trigger"));
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("#1901"));
  });

  it("reaches no loader API even where one exists", () => {
    // The disqualifier is not purity: both placeholders stand in for the
    // loader's own API, #1901 replaces them with Tender's, and one that
    // borrowed wherever it found one would behave differently on a machine
    // running Decky from one without. The reference machine DOES run the loader
    // (plugin_loader active, 127.0.0.1:1337 listening, measured 2026-09-17), so
    // that borrowing would show up on a device rather than hide there.
    const loader = { connect: vi.fn() };
    vi.stubGlobal("__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit", loader);
    vi.spyOn(console, "warn").mockImplementation(() => {});

    toaster.toast({ title: "Tender", body: "anything" });

    expect(loader.connect).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("the routerHook placeholder", () => {
  it("answers with the patch unapplied, and names the route it did not patch", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const patch = (tree: unknown) => tree;

    const installed = routerHook.addPatch("/library/app/:appid", patch);

    // Handed back rather than refused, so that the registration and the teardown
    // in `gameDetailPatch.tsx` stay symmetrical: a null here would make the
    // remove path unreachable and hide the day it starts mattering.
    expect(installed).toBe(patch);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("/library/app/:appid"));
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("#1901"));
  });

  it("takes the patch back without complaint", () => {
    expect(() => routerHook.removePatch("/library/app/:appid", (tree: unknown) => tree)).not.toThrow();
  });
});

describe("subscribing to a backend event", () => {
  it("answers with the listener unchanged, which is what every teardown holds on to", () => {
    // `index.tsx` keeps the returned reference and hands the same one back to
    // `removeEventListener` on dismount — twenty times. A wrapper returned here
    // instead would make every one of those removals a silent no-op.
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const listener = (frame: never) => frame;

    expect(addEventListener("sync_progress", listener)).toBe(listener);
  });

  it("removes without complaint, including one that was never registered", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(() => removeEventListener("never_subscribed", () => {})).not.toThrow();
  });
});

describe("a bundle served without a token", () => {
  it("fails its calls rather than throwing past every call site's handler", async () => {
    // Under the test runner this module's own URL carries no `?token=`, which is
    // exactly the shape a bundle loaded by hand would have. The 150 call sites
    // are written to `await` and `.catch()`, so the failure has to arrive as a
    // rejection; a synchronous throw would sail past all of them.
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const askBackend = callable<[], unknown>("get_sync_stats");

    let threw = false;
    const answer = (() => {
      try {
        return askBackend();
      } catch {
        threw = true;
        return Promise.resolve();
      }
    })();

    expect(threw).toBe(false);
    await expect(answer).rejects.toThrow(/no token/);
  });
});
