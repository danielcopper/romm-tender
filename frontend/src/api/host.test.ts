/**
 * The five exports, against the real module.
 *
 * `test-setup.ts` replaces `api/host` for the whole suite, so this file has to
 * take the stub off again — otherwise it would assert that a `vi.fn()` behaves
 * like a `vi.fn()`. Every member here is a one-line delegation: `callable`,
 * `addEventListener` and `removeEventListener` to `HostSocket`, which
 * `hostSocket.test.ts` drives against a socket of its own, and `toaster` to
 * `utils/steamToaster.tsx`, which its own file drives against supplied seams.
 * What this file holds is what the delegation itself does — the handle a toast
 * answers with, the listener handed straight back, and a call made from a
 * bundle served without a token.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

vi.unmock("./host");

import { addEventListener, callable, definePlugin, removeEventListener, toaster, type Plugin } from "./host";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("definePlugin", () => {
  it("answers with the factory it was given", () => {
    // Whoever mounts the panel calls it, which is `qam/installEntry.tsx`:
    // exactly once, behind Tender's own Quick Access entry.
    const factory = (): Plugin => ({ name: "Tender", icon: null });
    expect(definePlugin(factory)).toBe(factory);
  });
});

describe("the toaster", () => {
  // There is no Steam under the test runner, so every lookup behind a toast
  // misses here and what this file can reach is the answer to THAT — which is
  // the answer a device gives after a Steam update has moved them. The push
  // itself, the tray and the renderer's patch chain are driven against supplied
  // seams in `utils/steamToaster.test.tsx`.
  it("hands back a dismissable handle carrying the toast, and logs what it could not show", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const data = { title: "Tender", body: "Downloaded Chrono Trigger" };

    const raised = toaster.toast(data);

    expect(raised.data).toBe(data);
    expect(() => raised.dismiss()).not.toThrow();
    // The log is the whole of what a notice does when Steam answers for
    // nothing — and the body is in it, because a line that says only "a toast
    // happened" is worth nothing to whoever reads it.
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("Downloaded Chrono Trigger"));
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("what Tender raises toasts through is missing"));
  });

  it("reaches no loader API even where one exists", () => {
    const loader = { connect: vi.fn() };
    vi.stubGlobal("__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit", loader);
    vi.spyOn(console, "warn").mockImplementation(() => {});

    toaster.toast({ title: "Tender", body: "anything" });

    expect(loader.connect).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
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
