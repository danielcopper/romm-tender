/**
 * What the panel takes from `api/host`, against the real module.
 *
 * `test-setup.ts` replaces `api/host` for the whole suite, so this file has to
 * take the stub off again — otherwise it would assert that a `vi.fn()` behaves
 * like a `vi.fn()`. Every member here is a delegation: `endpoint`,
 * `addEventListener` and `removeEventListener` to `HostSocket`, which
 * `hostSocket.test.ts` drives against a socket of its own, and `toaster` to
 * `utils/steamToaster.tsx`, which its own file drives against supplied seams.
 * What this file holds is what the delegation itself does — the handle a toast
 * answers with, the listener handed straight back, a call made from a bundle
 * served without a token, and the wording an answer is given on its way back.
 * The socket's word on a stranded panel is driven against a socket of
 * `hostSocket.test.ts`'s own; here, only that the delegation asks nothing of a
 * panel nobody refused.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

vi.unmock("./host");

import {
  addEventListener,
  definePanel,
  endpoint,
  onStrandedAnswerChange,
  recheckStranded,
  removeEventListener,
  strandedAnswer,
  toaster,
  type PanelDefinition,
} from "./host";
import { HostSocket } from "./hostSocket";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("definePanel", () => {
  it("answers with the factory it was given", () => {
    // Whoever mounts the panel calls it, which is `qam/installEntry.tsx`:
    // exactly once, behind Tender's own Quick Access entry.
    const factory = (): PanelDefinition => ({ name: "Tender", icon: null });
    expect(definePanel(factory)).toBe(factory);
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
    // A view that subscribes while it is mounted keeps the returned reference
    // and hands the same one back to `removeEventListener` when it unmounts. A
    // wrapper returned here instead would make every such removal a silent no-op.
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
    const askBackend = endpoint<[], unknown>("get_sync_stats");

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

describe("the socket's word on a stranded panel", () => {
  it("holds no answer and asks nothing before any backend refused the panel", async () => {
    const open = vi.fn();
    vi.stubGlobal("WebSocket", open);
    const heard: string[] = [];
    const stop = onStrandedAnswerChange((answer) => heard.push(answer));

    await recheckStranded();
    stop();

    expect(strandedAnswer()).toBeNull();
    expect(heard).toEqual([]);
    expect(open).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("an answer on its way back", () => {
  it("words a press refused for one of RetroDECK's findings the way that finding's banner does", async () => {
    vi.spyOn(HostSocket.prototype, "call").mockResolvedValue({
      success: false,
      reason: "retrodeck_finding",
      message: "Problem with RetroDECK: marker-missing",
      finding: { code: "marker-missing", data: { path: "/home/deck/retrodeck.json" } },
    });

    const answer = await endpoint<[number], { message: string }>("start_download")(7);

    expect(answer.message).toBe("RetroDECK: its settings file /home/deck/retrodeck.json is missing.");
  });

  it("hands every other answer back as it came", async () => {
    const refusal = { success: false, reason: "no_rom_folder", message: "RetroDECK names no ROM folder for gba." };
    vi.spyOn(HostSocket.prototype, "call").mockResolvedValue(refusal);

    expect(await endpoint<[number], unknown>("start_download")(7)).toBe(refusal);
  });
});
