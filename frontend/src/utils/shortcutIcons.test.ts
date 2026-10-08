import { describe, it, expect, beforeEach, vi } from "vitest";
import * as backend from "../api/backend";
import { applyArtwork } from "./artwork";
import { applyShortcutIcons } from "./shortcutIcons";

describe("applyShortcutIcons", () => {
  const setShortcutIcon = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("SteamClient", {
      Apps: { SetShortcutIcon: setShortcutIcon, SetCustomArtworkForApp: vi.fn().mockResolvedValue(undefined) },
    });
  });

  const batch = {
    icons: [
      { app_id: 100, icon_path: "/grid/100_icon.png" },
      { app_id: 200, icon_path: "/grid/tender-icon.png" },
    ],
    prune_lease_token: "icon-lease",
  };

  it("points each shortcut in the batch at its icon file", async () => {
    await expect(applyShortcutIcons(batch)).resolves.toBe(2);

    expect(setShortcutIcon.mock.calls).toEqual([
      [100, "/grid/100_icon.png"],
      [200, "/grid/tender-icon.png"],
    ]);
  });

  it("gives the batch's lease back after the last icon is set", async () => {
    await applyShortcutIcons(batch);

    const release = vi.mocked(backend.releasePruneConflictLease);
    expect(release).toHaveBeenCalledWith("icon-lease");
    expect(release.mock.invocationCallOrder[0]).toBeGreaterThan(setShortcutIcon.mock.invocationCallOrder[1]!);
  });

  it("leaves a shortcut whose artwork the game page is applying right now", async () => {
    let finish: (value: { base64: null; no_api_key: boolean }) => void = () => {};
    vi.mocked(backend.getSgdbArtworkBase64).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const applying = applyArtwork(42, 200);

    await expect(applyShortcutIcons(batch)).resolves.toBe(1);

    expect(setShortcutIcon.mock.calls).toEqual([[100, "/grid/100_icon.png"]]);
    finish({ base64: null, no_api_key: false });
    await applying;
  });

  it("goes on past an icon Steam refuses", async () => {
    setShortcutIcon.mockImplementationOnce(() => {
      throw new Error("no such app");
    });

    await expect(applyShortcutIcons(batch)).resolves.toBe(1);

    expect(setShortcutIcon).toHaveBeenCalledWith(200, "/grid/tender-icon.png");
  });
});
