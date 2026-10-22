import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import {
  applyArtwork,
  applyAutomaticArtwork,
  applyTabSlotImage,
  cancelArtworkApply,
  fillEmptyArtwork,
  isArtworkApplyInFlight,
  setJobSlotArtwork,
} from "./artwork";

/** What `saveShortcutIcon` answers for an icon it wrote. */
const SAVED_ICON: backend.SavedShortcutIcon = {
  success: true,
  icon_path: "/grid/5000_icon.png",
  image_id: 31,
  sha256: "sha-icon",
  sgdb_id: 9999,
};

/** What `saveShortcutIcon` answers when it cannot write the icon. */
const ICON_NOT_WRITTEN: backend.EndpointFailure = {
  success: false,
  reason: "icon_write_failed",
  message: "Failed to write icon to Steam grid directory",
};

describe("applyArtwork", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "none" });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(SAVED_ICON);
  });

  it("requests all four SGDB asset types for the rom id", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });
    await applyArtwork(42, 5000);
    expect(vi.mocked(backend.getSgdbArtworkBase64)).toHaveBeenCalledWith(42, 1);
    expect(vi.mocked(backend.getSgdbArtworkBase64)).toHaveBeenCalledWith(42, 2);
    expect(vi.mocked(backend.getSgdbArtworkBase64)).toHaveBeenCalledWith(42, 3);
    expect(vi.mocked(backend.getSgdbSlotArtwork)).toHaveBeenCalledWith(42, 5000, "icon", true);
  });

  it("returns -1 when any asset reports no_api_key", async () => {
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce({ base64: null, no_api_key: false })
      .mockResolvedValueOnce({ base64: null, no_api_key: true })
      .mockResolvedValueOnce({ base64: null, no_api_key: false });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready" });
    await expect(applyArtwork(42, 5000)).resolves.toBe(-1);
    // Short-circuits before writing any artwork.
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.saveShortcutIcon)).not.toHaveBeenCalled();
  });

  it("maps types 1-3 to SetCustomArtworkForApp and writes the icon through saveShortcutIcon, returns count", async () => {
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce({ base64: "AA==", no_api_key: false })
      .mockResolvedValueOnce({ base64: "BB==", no_api_key: false })
      .mockResolvedValueOnce({ base64: "CC==", no_api_key: false });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready" });
    await expect(applyArtwork(42, 5000)).resolves.toBe(4);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledWith(5000, "AA==", "png", 1);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledWith(5000, "BB==", "png", 2);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledWith(5000, "CC==", "png", 3);
    expect(vi.mocked(backend.saveShortcutIcon)).toHaveBeenCalledWith(5000, 42);
    // The returned grid path is applied to the shortcut live via SteamClient.
    expect(vi.mocked(SteamClient.Apps.SetShortcutIcon)).toHaveBeenCalledWith(5000, "/grid/5000_icon.png");
  });

  it("holds every SGDB fetch lease through the final Steam artwork write", async () => {
    const write = deferred<void>();
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce({ base64: "AA==", no_api_key: false, prune_lease_token: "lease-1" })
      .mockResolvedValueOnce({ base64: null, no_api_key: false, prune_lease_token: "lease-2" })
      .mockResolvedValueOnce({ base64: null, no_api_key: false, prune_lease_token: "lease-3" });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready", prune_lease_token: "lease-4" });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(ICON_NOT_WRITTEN);
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockImplementationOnce(() => write.promise);

    const applying = applyArtwork(42, 5000);
    await vi.waitFor(() => expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalled());
    expect(backend.releasePruneConflictLease).not.toHaveBeenCalled();

    write.resolve(undefined);
    await expect(applying).resolves.toBe(1);
    expect(
      vi
        .mocked(backend.releasePruneConflictLease)
        .mock.calls.map(([token]) => token)
        .sort(),
    ).toEqual(["lease-1", "lease-2", "lease-3", "lease-4"]);
  });

  it("component cancellation retains artwork leases until a pending Steam write settles", async () => {
    const write = deferred<void>();
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({
      base64: "AA==",
      no_api_key: false,
      prune_lease_token: "artwork-lease",
    });
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockImplementationOnce(() => write.promise);

    const applying = applyArtwork(42, 5000);
    await vi.waitFor(() => expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalled());
    const cancelling = cancelArtworkApply(5000);
    await Promise.resolve();
    expect(backend.releasePruneConflictLease).not.toHaveBeenCalledWith("artwork-lease");

    write.resolve(undefined);
    await applying;
    await cancelling;
    expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("artwork-lease");
    expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalledTimes(1);
  });

  it("does not call SetShortcutIcon when saveShortcutIcon reports failure", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready" });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(ICON_NOT_WRITTEN);
    // Only the icon was there to set, and its write failed → count is 0 (the
    // failed icon is NOT counted).
    await expect(applyArtwork(42, 5000)).resolves.toBe(0);
    expect(vi.mocked(backend.saveShortcutIcon)).toHaveBeenCalledWith(5000, 42);
    expect(vi.mocked(SteamClient.Apps.SetShortcutIcon)).not.toHaveBeenCalled();
  });

  it("does not write the icon when its fetch failed", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "failed" });
    await expect(applyArtwork(42, 5000)).resolves.toBe(0);
    expect(vi.mocked(backend.saveShortcutIcon)).not.toHaveBeenCalled();
    expect(vi.mocked(SteamClient.Apps.SetShortcutIcon)).not.toHaveBeenCalled();
  });

  it("counts a successful hero but NOT a failed icon in the same apply (#L4)", async () => {
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce({ base64: "AA==", no_api_key: false }) // hero → applied
      .mockResolvedValueOnce({ base64: null, no_api_key: false })
      .mockResolvedValueOnce({ base64: null, no_api_key: false });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready" }); // icon → its write fails
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(ICON_NOT_WRITTEN);
    // Hero counts (1), the failed icon does not → 1, not 2.
    await expect(applyArtwork(42, 5000)).resolves.toBe(1);
    expect(vi.mocked(SteamClient.Apps.SetShortcutIcon)).not.toHaveBeenCalled();
  });

  it("returns 0 and writes nothing when all assets are null", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });
    await expect(applyArtwork(42, 5000)).resolves.toBe(0);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.saveShortcutIcon)).not.toHaveBeenCalled();
  });

  it("counts only the assets that returned base64", async () => {
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce({ base64: "AA==", no_api_key: false })
      .mockResolvedValueOnce({ base64: null, no_api_key: false })
      .mockResolvedValueOnce({ base64: "CC==", no_api_key: false });
    await expect(applyArtwork(42, 5000)).resolves.toBe(2);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledTimes(2);
    expect(vi.mocked(backend.saveShortcutIcon)).not.toHaveBeenCalled();
  });

  it("per-asset fetch rejection is swallowed → treated as null (returns 0)", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockRejectedValue(new Error("net"));
    vi.mocked(backend.getSgdbSlotArtwork).mockRejectedValue(new Error("net"));
    await expect(applyArtwork(42, 5000)).resolves.toBe(0);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.saveShortcutIcon)).not.toHaveBeenCalled();
  });
});

type Art = { base64: string | null; no_api_key?: boolean };

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("applyArtwork — newest-apply-wins race guard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    // The icon is written, so it counts toward the applied total (these tests
    // exercise all four assets applying).
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready" });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(SAVED_ICON);
  });

  it("a superseded in-flight apply writes nothing — only the newer apply's art lands for that appId", async () => {
    const appId = 5000;
    const slow = deferred<Art>();
    // Apply A (rom 42) hangs on `slow` for every asset; apply B (rom 99) resolves
    // fast with distinct art and completes its writes first.
    vi.mocked(backend.getSgdbArtworkBase64).mockImplementation((romId: number): Promise<Art> => {
      if (romId === 42) return slow.promise;
      return Promise.resolve({ base64: "BB==", no_api_key: false });
    });

    // A starts first (claims the older generation), B starts second for the SAME appId.
    const aPromise = applyArtwork(42, appId);
    const bPromise = applyArtwork(99, appId);
    await bPromise;

    // B's art is on the tile.
    const write = vi.mocked(SteamClient.Apps.SetCustomArtworkForApp);
    expect(write).toHaveBeenCalledWith(appId, "BB==", "png", 1);

    // Now let A's slow fetches finally resolve with the OLD art.
    slow.resolve({ base64: "AA==", no_api_key: false });
    await aPromise;

    // Assert on call CONTENTS, not counts: every write carries B's art, none carry
    // A's — the stale apply performed no writes after being superseded.
    expect(write.mock.calls.every((c) => c[1] === "BB==")).toBe(true);
    expect(write.mock.calls.some((c) => c[1] === "AA==")).toBe(false);
    // Exactly one hero (assetType 1) write, and it is B's.
    expect(write.mock.calls.filter((c) => c[3] === 1)).toEqual([[appId, "BB==", "png", 1]]);
    // The icon (saveShortcutIcon) was only ever written for B's rom, never A's.
    expect(vi.mocked(backend.saveShortcutIcon).mock.calls.some((c) => c[1] === 42)).toBe(false);
    // A resolved to the count it managed to write (0) rather than throwing.
    await expect(aPromise).resolves.toBe(0);
  });

  it("sequential applies for the same appId both write (no over-suppression)", async () => {
    const appId = 5000;
    vi.mocked(backend.getSgdbArtworkBase64).mockImplementation((romId: number): Promise<Art> =>
      Promise.resolve({ base64: romId === 1 ? "AA==" : "BB==", no_api_key: false }),
    );

    await expect(applyArtwork(1, appId)).resolves.toBe(4); // A completes fully
    await expect(applyArtwork(2, appId)).resolves.toBe(4); // then B completes fully

    const write = vi.mocked(SteamClient.Apps.SetCustomArtworkForApp);
    // Both applies wrote their hero art — the guard only suppresses a call that a
    // NEWER one overtook while it was still in flight, not a finished predecessor.
    expect(write).toHaveBeenCalledWith(appId, "AA==", "png", 1);
    expect(write).toHaveBeenCalledWith(appId, "BB==", "png", 1);
  });

  it("an in-flight apply for one appId does not suppress a concurrent apply for a different appId", async () => {
    const slow = deferred<Art>();
    // appId X's rom (42) hangs; appId Y's rom (99) resolves fast.
    vi.mocked(backend.getSgdbArtworkBase64).mockImplementation((romId: number): Promise<Art> => {
      if (romId === 42) return slow.promise;
      return Promise.resolve({ base64: "YY==", no_api_key: false });
    });

    const xPromise = applyArtwork(42, 5000); // appId X, in flight
    await applyArtwork(99, 6000); // appId Y, different appId — completes

    const write = vi.mocked(SteamClient.Apps.SetCustomArtworkForApp);
    // Y wrote despite X being mid-flight (different appId → independent generation).
    expect(write).toHaveBeenCalledWith(6000, "YY==", "png", 1);

    // X is NOT superseded — Y bumped a different appId — so X still writes when it resolves.
    slow.resolve({ base64: "XX==", no_api_key: false });
    await expect(xPromise).resolves.toBe(4);
    expect(write).toHaveBeenCalledWith(5000, "XX==", "png", 1);
  });
});

describe("isArtworkApplyInFlight", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "none" });
  });

  it("answers yes for an appId while its apply runs, and no for any other", async () => {
    let finish: (value: { base64: null; no_api_key: boolean }) => void = () => {};
    vi.mocked(backend.getSgdbArtworkBase64).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );

    const applying = applyArtwork(42, 5000);

    expect(isArtworkApplyInFlight(5000)).toBe(true);
    expect(isArtworkApplyInFlight(6000)).toBe(false);
    finish({ base64: null, no_api_key: false });
    await applying;
    expect(isArtworkApplyInFlight(5000)).toBe(false);
  });

  it("answers no once an apply that failed has ended", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: "AA==", no_api_key: false });
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockRejectedValue(new Error("Steam refused"));

    await expect(applyArtwork(42, 5000)).rejects.toThrow("Steam refused");

    expect(isArtworkApplyInFlight(5000)).toBe(false);
  });

  it("stays yes while a second apply for the same appId still runs", async () => {
    let finishSecond: (value: { base64: null; no_api_key: boolean }) => void = () => {};
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce({ base64: null, no_api_key: false })
      .mockResolvedValueOnce({ base64: null, no_api_key: false })
      .mockResolvedValueOnce({ base64: null, no_api_key: false })
      .mockResolvedValueOnce({ base64: null, no_api_key: false })
      .mockReturnValue(
        new Promise((resolve) => {
          finishSecond = resolve;
        }),
      );

    const first = applyArtwork(42, 5000);
    const second = applyArtwork(42, 5000);
    await first;

    expect(isArtworkApplyInFlight(5000)).toBe(true);
    finishSecond({ base64: null, no_api_key: false });
    await second;
    expect(isArtworkApplyInFlight(5000)).toBe(false);
  });
});

describe("fillEmptyArtwork", () => {
  const image = (base64: string, imageId: number, token?: string): backend.SgdbSlotArtwork => ({
    status: "image",
    base64,
    image_id: imageId,
    sha256: `sha-${base64}`,
    sgdb_id: 9999,
    ...(token ? { prune_lease_token: token } : {}),
  });

  /** Answer each slot the fetch is asked for from *answers*; a slot not named answers `skip`. */
  const answerSlots = (answers: Partial<Record<backend.SgdbArtworkSlot, backend.SgdbSlotArtwork>>) => {
    vi.mocked(backend.getSgdbSlotArtwork).mockImplementation(
      async (_romId, _appId, slot) => answers[slot] ?? { status: "skip" },
    );
  };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
  });

  it("asks for the hero, the logo, the wide grid and the icon of the shortcut", async () => {
    answerSlots({});

    await fillEmptyArtwork(42, 5000);

    expect(vi.mocked(backend.getSgdbSlotArtwork).mock.calls).toEqual([
      [42, 5000, "hero"],
      [42, 5000, "logo"],
      [42, 5000, "grid"],
      [42, 5000, "icon"],
    ]);
    expect(backend.getSgdbArtworkBase64).not.toHaveBeenCalled();
  });

  it("sets only the slots the backend serves an image for, each under its Steam asset type", async () => {
    answerSlots({ logo: image("LL==", 5), grid: image("GG==", 12) });

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(2);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mock.calls).toEqual([
      [5000, "LL==", "png", 2],
      [5000, "GG==", "png", 3],
    ]);
  });

  it("writes nothing into a slot the backend skips, so an image set by hand stays", async () => {
    answerSlots({});

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(0);

    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(backend.reportSgdbSlotArtwork).not.toHaveBeenCalled();
  });

  it("reports a set hero, logo and wide grid with the fetch's own answer", async () => {
    answerSlots({ hero: image("HH==", 7), logo: image("LL==", 5), grid: image("GG==", 12) });

    await fillEmptyArtwork(42, 5000);

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([
      [42, "hero", "set", 9999, 7, "sha-HH=="],
      [42, "logo", "set", 9999, 5, "sha-LL=="],
      [42, "grid", "set", 9999, 12, "sha-GG=="],
    ]);
  });

  it("reports an image Steam refuses as refused and goes on to the next slot", async () => {
    answerSlots({ logo: image("LL==", 5), grid: image("GG==", 12) });
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockRejectedValueOnce(new Error("Steam refused"));

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(1);

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([
      [42, "logo", "refused", 9999, 5, "sha-LL=="],
      [42, "grid", "set", 9999, 12, "sha-GG=="],
    ]);
  });

  it("fetches the next slot only once the image before it is set", async () => {
    const write = deferred<void>();
    answerSlots({ hero: image("HH==", 7), logo: image("LL==", 5) });
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockImplementationOnce(() => write.promise);

    const filling = fillEmptyArtwork(42, 5000);
    await vi.waitFor(() => expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalledTimes(1));
    expect(vi.mocked(backend.getSgdbSlotArtwork).mock.calls.map(([, , slot]) => slot)).toEqual(["hero"]);

    write.resolve(undefined);
    await filling;
    expect(vi.mocked(backend.getSgdbSlotArtwork).mock.calls.map(([, , slot]) => slot)).toEqual([
      "hero",
      "logo",
      "grid",
      "icon",
    ]);
  });

  it("holds each image's lease through its Steam write", async () => {
    const write = deferred<void>();
    answerSlots({ grid: image("GG==", 12, "grid-lease") });
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockImplementationOnce(() => write.promise);

    const filling = fillEmptyArtwork(42, 5000);
    await vi.waitFor(() => expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalled());
    expect(backend.releasePruneConflictLease).not.toHaveBeenCalled();

    write.resolve(undefined);
    await filling;
    expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("grid-lease");
  });

  it("returns -1 and writes nothing without an API key", async () => {
    answerSlots({ hero: { status: "no_api_key" } });

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(-1);

    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(vi.mocked(backend.getSgdbSlotArtwork).mock.calls.map(([, , slot]) => slot)).not.toContain("icon");
    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
  });

  it("treats a fetch that rejects as nothing to set and asks for the next slot", async () => {
    vi.mocked(backend.getSgdbSlotArtwork)
      .mockRejectedValueOnce(new Error("net"))
      .mockResolvedValueOnce(image("LL==", 5))
      .mockResolvedValueOnce({ status: "none" })
      .mockResolvedValueOnce({ status: "skip" });

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(1);

    expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalledWith(5000, "LL==", "png", 2);
  });

  it("stops writing once a newer apply for the shortcut has started", async () => {
    const slow = deferred<backend.SgdbSlotArtwork>();
    vi.mocked(backend.getSgdbSlotArtwork).mockReturnValueOnce(slow.promise).mockResolvedValue({ status: "skip" });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });

    const filling = fillEmptyArtwork(42, 5000);
    await applyArtwork(42, 5000);
    slow.resolve(image("OLD=", 7));

    await expect(filling).resolves.toBe(0);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mock.calls.some((c) => c[1] === "OLD=")).toBe(false);
  });

  it("gives the icon the backend answers ready, through the same write as applyArtwork", async () => {
    answerSlots({ icon: { status: "ready" } });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(SAVED_ICON);

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(1);

    expect(backend.saveShortcutIcon).toHaveBeenCalledWith(5000, 42);
    expect(SteamClient.Apps.SetShortcutIcon).toHaveBeenCalledWith(5000, "/grid/5000_icon.png");
  });

  it("counts as an apply under way while it runs", async () => {
    const slow = deferred<backend.SgdbSlotArtwork>();
    vi.mocked(backend.getSgdbSlotArtwork).mockReturnValueOnce(slow.promise).mockResolvedValue({ status: "skip" });

    const filling = fillEmptyArtwork(42, 5000);
    expect(isArtworkApplyInFlight(5000)).toBe(true);
    slow.resolve({ status: "skip" });
    await filling;
    expect(isArtworkApplyInFlight(5000)).toBe(false);
  });
});

describe("applyArtwork records the hero, the wide grid and the logo it set", () => {
  const replaced = (base64: string, imageId: number) => ({
    base64,
    no_api_key: false,
    image_id: imageId,
    sha256: `sha-${base64}`,
    sgdb_id: 9999,
  });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce(replaced("HH==", 7))
      .mockResolvedValueOnce(replaced("LL==", 5))
      .mockResolvedValueOnce(replaced("GG==", 12));
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "none" });
  });

  it("reports each as set under the ROM it was applied for, with its image id and hash", async () => {
    await expect(applyArtwork(42, 5000)).resolves.toBe(3);

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([
      [42, "hero", "set", 9999, 7, "sha-HH=="],
      [42, "logo", "set", 9999, 5, "sha-LL=="],
      [42, "grid", "set", 9999, 12, "sha-GG=="],
    ]);
  });

  it("reports an image Steam refuses as refused, and fails the apply as before", async () => {
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("Steam refused"));

    await expect(applyArtwork(42, 5000)).rejects.toThrow("Steam refused");

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([
      [42, "hero", "set", 9999, 7, "sha-HH=="],
      [42, "logo", "refused", 9999, 5, "sha-LL=="],
    ]);
  });

  it("goes on when a report fails", async () => {
    vi.mocked(backend.reportSgdbSlotArtwork).mockRejectedValue(new Error("net"));

    await expect(applyArtwork(42, 5000)).resolves.toBe(3);
  });
});

describe("setJobSlotArtwork", () => {
  const image = (base64: string, token?: string): backend.SgdbSlotArtwork => ({
    status: "image",
    base64,
    image_id: 12,
    sha256: `sha-${base64}`,
    sgdb_id: 9999,
    ...(token ? { prune_lease_token: token } : {}),
  });
  const notStopped = () => false;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
  });

  it("sets the image the per-slot fetch serves under its Steam asset type, and reports it set", async () => {
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue(image("LL=="));

    await expect(setJobSlotArtwork(42, 5000, "logo", notStopped)).resolves.toBe(true);

    expect(backend.getSgdbSlotArtwork).toHaveBeenCalledWith(42, 5000, "logo");
    expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalledWith(5000, "LL==", "png", 2);
    expect(backend.reportSgdbSlotArtwork).toHaveBeenCalledWith(42, "logo", "set", 9999, 12, "sha-LL==");
  });

  it("writes nothing into a slot that has filled since the job looked", async () => {
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "skip" });

    await expect(setJobSlotArtwork(42, 5000, "grid", notStopped)).resolves.toBe(false);

    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(backend.reportSgdbSlotArtwork).not.toHaveBeenCalled();
  });

  it("reports an image Steam refuses as refused", async () => {
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue(image("GG=="));
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockRejectedValueOnce(new Error("Steam refused"));

    await expect(setJobSlotArtwork(42, 5000, "grid", notStopped)).resolves.toBe(false);

    expect(backend.reportSgdbSlotArtwork).toHaveBeenCalledWith(42, "grid", "refused", 9999, 12, "sha-GG==");
  });

  it("writes nothing once a game page's apply for the shortcut has started", async () => {
    const slow = deferred<backend.SgdbSlotArtwork>();
    vi.mocked(backend.getSgdbSlotArtwork).mockReturnValueOnce(slow.promise).mockResolvedValue({ status: "skip" });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });

    const setting = setJobSlotArtwork(42, 5000, "grid", notStopped);
    await fillEmptyArtwork(42, 5000);
    slow.resolve(image("JOB="));

    await expect(setting).resolves.toBe(false);
    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(backend.reportSgdbSlotArtwork).not.toHaveBeenCalled();
  });

  it("writes nothing once the job's batch has stopped", async () => {
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue(image("GG=="));

    await expect(setJobSlotArtwork(42, 5000, "grid", () => true)).resolves.toBe(false);

    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
  });

  it("holds the image's lease through its Steam write", async () => {
    const write = deferred<void>();
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue(image("GG==", "grid-lease"));
    vi.mocked(SteamClient.Apps.SetCustomArtworkForApp).mockImplementationOnce(() => write.promise);

    const setting = setJobSlotArtwork(42, 5000, "grid", notStopped);
    await vi.waitFor(() => expect(SteamClient.Apps.SetCustomArtworkForApp).toHaveBeenCalled());
    expect(backend.releasePruneConflictLease).not.toHaveBeenCalled();

    write.resolve(undefined);
    await setting;
    expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("grid-lease");
  });
});

describe("a refused artwork fetch", () => {
  const refusal: backend.EndpointFailure = {
    success: false,
    reason: "prune_active",
    message: "A removed-game cleanup is in progress; wait for it to finish before changing local game data.",
  };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "none" });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(SAVED_ICON);
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
    vi.mocked(backend.debugLog).mockResolvedValue(undefined);
  });

  it("makes applyArtwork answer the refusal, apply nothing and give every lease back", async () => {
    vi.mocked(backend.getSgdbArtworkBase64)
      .mockResolvedValueOnce({ base64: "AA==", no_api_key: false, prune_lease_token: "lease-1" })
      .mockResolvedValueOnce(refusal)
      .mockResolvedValueOnce({ base64: "CC==", no_api_key: false, prune_lease_token: "lease-3" });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready", prune_lease_token: "lease-4" });

    await expect(applyArtwork(42, 5000)).resolves.toEqual(refusal);

    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
    expect(
      vi
        .mocked(backend.releasePruneConflictLease)
        .mock.calls.map(([token]) => token)
        .sort(),
    ).toEqual(["lease-1", "lease-3", "lease-4"]);
  });

  it("is logged by the first visit's fill, which goes on to the next slot", async () => {
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });
    vi.mocked(backend.getSgdbSlotArtwork).mockImplementation(async (_romId, _appId, slot) =>
      slot === "hero" ? refusal : { status: "skip" },
    );

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(0);

    expect(vi.mocked(backend.getSgdbSlotArtwork).mock.calls.map(([, , slot]) => slot)).toEqual([
      "hero",
      "logo",
      "grid",
      "icon",
    ]);
    expect(backend.debugLog).toHaveBeenCalledWith(
      expect.stringContaining(`hero fetch for appId 5000 refused: ${refusal.message}`),
    );
  });

  it("is logged for the first visit's icon, which is then not applied", async () => {
    vi.mocked(backend.getSgdbSlotArtwork).mockImplementation(async (_romId, _appId, slot) =>
      slot === "icon" ? refusal : { status: "skip" },
    );

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(0);

    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
    expect(backend.debugLog).toHaveBeenCalledWith(
      expect.stringContaining(`icon fetch for appId 5000 refused: ${refusal.message}`),
    );
  });

  it("stops the first visit's fill at a refused key, asking for no further slot or icon", async () => {
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "key_refused" });

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(0);

    expect(vi.mocked(backend.getSgdbSlotArtwork).mock.calls.map(([, , slot]) => slot)).toEqual(["hero"]);
    expect(backend.getSgdbSlotArtwork).not.toHaveBeenCalledWith(42, 5000, "icon");
    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
  });

  it("makes the shortcut artwork job's slot write nothing, and is logged", async () => {
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue(refusal);

    await expect(setJobSlotArtwork(42, 5000, "logo", () => false)).resolves.toBe(false);

    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(backend.debugLog).toHaveBeenCalledWith(
      expect.stringContaining(`logo fetch for appId 5000 refused: ${refusal.message}`),
    );
  });
});

describe("the icon through the per-slot fetch", () => {
  /** Answer the icon's fetch with *icon*, and every other slot `skip`. */
  const answerIcon = (icon: backend.SgdbSlotArtwork | backend.EndpointFailure) => {
    vi.mocked(backend.getSgdbSlotArtwork).mockImplementation(async (_romId, _appId, slot) =>
      slot === "icon" ? icon : { status: "skip" },
    );
  };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: null, no_api_key: false });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(SAVED_ICON);
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
    vi.mocked(backend.releasePruneConflictLease).mockResolvedValue({ success: true, message: "released" });
    vi.mocked(backend.debugLog).mockResolvedValue(undefined);
  });

  it("keeps an icon the backend skips on a first visit, so an icon somebody set stays", async () => {
    answerIcon({ status: "skip" });

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(0);

    expect(backend.getSgdbSlotArtwork).toHaveBeenCalledWith(42, 5000, "icon");
    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
    expect(SteamClient.Apps.SetShortcutIcon).not.toHaveBeenCalled();
    expect(backend.reportSgdbSlotArtwork).not.toHaveBeenCalled();
  });

  it("writes, sets and records the icon the backend answers ready on a first visit", async () => {
    answerIcon({ status: "ready" });

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(1);

    expect(backend.saveShortcutIcon).toHaveBeenCalledWith(5000, 42);
    expect(SteamClient.Apps.SetShortcutIcon).toHaveBeenCalledWith(5000, "/grid/5000_icon.png");
    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([[42, "icon", "set", 9999, 31, "sha-icon"]]);
  });

  it("records an icon Steam refuses on a first visit as refused, and goes on", async () => {
    answerIcon({ status: "ready" });
    vi.mocked(SteamClient.Apps.SetShortcutIcon).mockImplementation(() => {
      throw new Error("no such app");
    });

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(0);

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([
      [42, "icon", "refused", 9999, 31, "sha-icon"],
    ]);
  });

  it("sets and records nothing when the icon's write is refused", async () => {
    answerIcon({ status: "ready" });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(ICON_NOT_WRITTEN);

    await expect(fillEmptyArtwork(42, 5000)).resolves.toBe(0);

    expect(SteamClient.Apps.SetShortcutIcon).not.toHaveBeenCalled();
    expect(backend.reportSgdbSlotArtwork).not.toHaveBeenCalled();
    expect(backend.debugLog).toHaveBeenCalledWith(expect.stringContaining(ICON_NOT_WRITTEN.message));
  });

  it("holds the icon's lease through its write", async () => {
    const write = deferred<backend.SavedShortcutIcon>();
    answerIcon({ status: "ready", prune_lease_token: "icon-lease" });
    vi.mocked(backend.saveShortcutIcon).mockReturnValue(write.promise);

    const filling = fillEmptyArtwork(42, 5000);
    await vi.waitFor(() => expect(backend.saveShortcutIcon).toHaveBeenCalled());
    expect(backend.releasePruneConflictLease).not.toHaveBeenCalled();

    write.resolve(SAVED_ICON);
    await filling;
    expect(backend.releasePruneConflictLease).toHaveBeenCalledWith("icon-lease");
  });

  it("writes no icon once a newer apply for the shortcut has started", async () => {
    const slow = deferred<backend.SgdbSlotArtwork>();
    vi.mocked(backend.getSgdbSlotArtwork).mockImplementation(async (_romId, _appId, slot, replace) =>
      slot === "icon" && !replace ? slow.promise : { status: "skip" },
    );

    const filling = fillEmptyArtwork(42, 5000);
    await vi.waitFor(() => expect(backend.getSgdbSlotArtwork).toHaveBeenCalledWith(42, 5000, "icon"));
    await applyArtwork(42, 5000);
    slow.resolve({ status: "ready" });

    await expect(filling).resolves.toBe(0);
    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
  });

  it("asks for the icon past the filled check and its record on the replacing apply, and records it", async () => {
    answerIcon({ status: "ready" });

    await expect(applyArtwork(42, 5000)).resolves.toBe(1);

    expect(backend.getSgdbSlotArtwork).toHaveBeenCalledWith(42, 5000, "icon", true);
    expect(backend.saveShortcutIcon).toHaveBeenCalledWith(5000, 42);
    expect(SteamClient.Apps.SetShortcutIcon).toHaveBeenCalledWith(5000, "/grid/5000_icon.png");
    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([[42, "icon", "set", 9999, 31, "sha-icon"]]);
  });

  it("writes no icon on a replacing apply a newer one has overtaken", async () => {
    const slow = deferred<{ base64: null; no_api_key: boolean }>();
    vi.mocked(backend.getSgdbArtworkBase64).mockImplementation(async (romId) =>
      romId === 42 ? slow.promise : { base64: null, no_api_key: false },
    );
    answerIcon({ status: "ready" });

    const older = applyArtwork(42, 5000);
    await applyArtwork(99, 5000);
    slow.resolve({ base64: null, no_api_key: false });

    await expect(older).resolves.toBe(0);
    expect(vi.mocked(backend.saveShortcutIcon).mock.calls).toEqual([[5000, 99]]);
  });

  it("records an icon Steam refuses on the replacing apply as refused, and fails the apply as the other slots do", async () => {
    answerIcon({ status: "ready" });
    vi.mocked(SteamClient.Apps.SetShortcutIcon).mockImplementation(() => {
      throw new Error("no such app");
    });

    await expect(applyArtwork(42, 5000)).rejects.toThrow("no such app");

    expect(vi.mocked(backend.reportSgdbSlotArtwork).mock.calls).toEqual([
      [42, "icon", "refused", 9999, 31, "sha-icon"],
    ]);
  });

  it("makes the replacing apply answer -1 when the icon's fetch finds no API key", async () => {
    answerIcon({ status: "no_api_key" });

    await expect(applyArtwork(42, 5000)).resolves.toBe(-1);

    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
  });

  it("makes the replacing apply answer a refused icon fetch and apply nothing", async () => {
    const refusal: backend.EndpointFailure = {
      success: false,
      reason: "prune_active",
      message: "A removed-game cleanup is in progress; wait for it to finish before changing local game data.",
    };
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: "AA==", no_api_key: false });
    answerIcon(refusal);

    await expect(applyArtwork(42, 5000)).resolves.toEqual(refusal);

    expect(SteamClient.Apps.SetCustomArtworkForApp).not.toHaveBeenCalled();
    expect(backend.saveShortcutIcon).not.toHaveBeenCalled();
  });

  it("fetches no image through asset type 4 on a first visit or a replacing apply", async () => {
    answerIcon({ status: "ready" });

    await fillEmptyArtwork(42, 5000);
    await applyArtwork(42, 5000);

    expect(vi.mocked(backend.getSgdbArtworkBase64).mock.calls.map(([, assetType]) => assetType)).toEqual([1, 2, 3]);
  });
});

describe("the ARTWORK tab's writes", () => {
  const SLOTS = (states: Partial<Record<backend.ArtworkTabSlot, backend.ArtworkSlotState>>): backend.ArtworkSlots => ({
    success: true,
    has_api_key: true,
    game: { sgdb_id: 9999, source: "romm", name: "Game" },
    superseded: null,
    slots: (["cover", "grid", "hero", "logo", "icon"] as const).map((slot) => ({
      slot,
      state: states[slot] ?? "automatic",
      source: "tender",
      image_id: null,
      has_choice: false,
    })),
  });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: {
        SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        ClearCustomArtworkForApp: vi.fn().mockResolvedValue(undefined),
        SetShortcutIcon: vi.fn(),
      },
    });
    vi.mocked(backend.getSgdbArtworkBase64).mockResolvedValue({ base64: "AA==", no_api_key: false });
    vi.mocked(backend.getSgdbSlotArtwork).mockResolvedValue({ status: "ready" });
    vi.mocked(backend.saveShortcutIcon).mockResolvedValue(SAVED_ICON);
    vi.mocked(backend.reportSgdbSlotArtwork).mockResolvedValue({ success: true });
  });

  const IMAGE: backend.ArtworkSlotImage = {
    status: "image",
    base64: "IMG=",
    image_id: 12,
    sha256: "sha",
    sgdb_id: 9999,
    prune_lease_token: "lease",
  };

  it("applies only the slots on automatic, leaving a chosen slot and one set in Steam", async () => {
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(SLOTS({ logo: "chosen", hero: "set_in_steam" }));

    await expect(applyAutomaticArtwork(42, 5000)).resolves.toBe(2);

    expect(vi.mocked(backend.getSgdbArtworkBase64)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.getSgdbArtworkBase64)).toHaveBeenCalledWith(42, 3);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledWith(5000, "AA==", "png", 3);
    expect(vi.mocked(backend.saveShortcutIcon)).toHaveBeenCalledWith(5000, 42);
  });

  it("applies nothing and answers the read's refusal where the slots cannot be read", async () => {
    const refused: backend.EndpointFailure = { success: false, reason: "no_grid_dir", message: "no grid" };
    vi.mocked(backend.getArtworkSlots).mockResolvedValue(refused);

    await expect(applyAutomaticArtwork(42, 5000)).resolves.toEqual(refused);
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
  });

  it("sets a picked image and reports it as set", async () => {
    await expect(applyTabSlotImage(42, 5000, "logo", IMAGE, false)).resolves.toBe("set");

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledWith(5000, "IMG=", "png", 2);
    expect(vi.mocked(SteamClient.Apps.ClearCustomArtworkForApp)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.reportSgdbSlotArtwork)).toHaveBeenCalledWith(42, "logo", "set", 9999, 12, "sha");
  });

  it("removes the image set in Steam first, and only after the user confirmed it", async () => {
    await applyTabSlotImage(42, 5000, "hero", IMAGE, true);

    const clear = vi.mocked(SteamClient.Apps.ClearCustomArtworkForApp);
    const set = vi.mocked(SteamClient.Apps.SetCustomArtworkForApp);
    expect(clear).toHaveBeenCalledWith(5000, 1);
    expect(clear.mock.invocationCallOrder[0]).toBeLessThan(set.mock.invocationCallOrder[0]!);
  });

  it("sets a chosen cover as Steam's portrait and reports it", async () => {
    await applyTabSlotImage(42, 5000, "cover", IMAGE, false);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledWith(5000, "IMG=", "png", 0);
    expect(vi.mocked(backend.reportSgdbSlotArtwork)).toHaveBeenCalledWith(42, "cover", "set", 9999, 12, "sha");
  });

  it("sets RomM's cover again for an automatic cover, and reports nothing", async () => {
    vi.mocked(backend.getArtworkBase64).mockResolvedValue({ base64: "ROMM=" });

    await applyTabSlotImage(42, 5000, "cover", { status: "cover", prune_lease_token: "lease" }, false);

    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).toHaveBeenCalledWith(5000, "ROMM=", "png", 0);
    expect(vi.mocked(backend.reportSgdbSlotArtwork)).not.toHaveBeenCalled();
  });

  it("writes a chosen icon through the backend and points the shortcut at it, removing nothing", async () => {
    const ready: backend.ArtworkSlotImage = { status: "ready", image_id: 4, sgdb_id: 9999, prune_lease_token: "l" };

    await expect(applyTabSlotImage(42, 5000, "icon", ready, false)).resolves.toBe("set");

    expect(vi.mocked(backend.saveShortcutIcon)).toHaveBeenCalledWith(5000, 42);
    expect(vi.mocked(SteamClient.Apps.SetShortcutIcon)).toHaveBeenCalledWith(5000, "/grid/5000_icon.png");
    expect(vi.mocked(SteamClient.Apps.ClearCustomArtworkForApp)).not.toHaveBeenCalled();
  });

  it("sets nothing for a slot SteamGridDB has nothing for", async () => {
    await expect(applyTabSlotImage(42, 5000, "logo", { status: "none" }, false)).resolves.toBe("none");
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
  });

  it("is overtaken by a later apply for the shortcut and writes nothing", async () => {
    const lease = deferred<void>();
    vi.mocked(backend.renewPruneConflictLease).mockReturnValue(lease.promise as never);
    const first = applyTabSlotImage(42, 5000, "logo", IMAGE, false);
    await cancelArtworkApply(5000);
    lease.resolve();

    await expect(first).resolves.toBe("left");
    expect(vi.mocked(SteamClient.Apps.SetCustomArtworkForApp)).not.toHaveBeenCalled();
  });
});
