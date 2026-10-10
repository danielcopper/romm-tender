import { describe, it, expect, beforeEach, vi } from "vitest";
import { getSettings } from "../api/backend";
import type { Settings } from "../types";
import {
  fetchQamEntryPositionState,
  getQamEntryPositionState,
  setQamEntryPositionState,
} from "./qamEntryPositionStore";

const settings = (over: Partial<Settings> = {}): Settings => ({
  romm_url: "",
  has_token: false,
  steam_input_mode: "default",
  sgdb_api_key_masked: "",
  log_level: "warn",
  romm_allow_insecure_ssl: false,
  ...over,
});

describe("qamEntryPositionStore", () => {
  beforeEach(() => {
    setQamEntryPositionState("above_decky");
    vi.mocked(getSettings).mockReset();
  });

  it("answers above Decky before anything has been read", async () => {
    // A fresh copy of the module: the shared one has been written by beforeEach.
    vi.resetModules();
    const fresh = await import("./qamEntryPositionStore");

    expect(fresh.getQamEntryPositionState()).toBe("above_decky");
  });

  it("answers what was last set", () => {
    setQamEntryPositionState("below_decky");
    expect(getQamEntryPositionState()).toBe("below_decky");

    setQamEntryPositionState("above_decky");
    expect(getQamEntryPositionState()).toBe("above_decky");
  });

  it("is filled from the backend's settings", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings({ qam_entry_position: "below_decky" }));

    await fetchQamEntryPositionState();

    expect(getQamEntryPositionState()).toBe("below_decky");
  });

  it("reads settings without the key as above Decky", async () => {
    setQamEntryPositionState("below_decky");
    vi.mocked(getSettings).mockResolvedValue(settings());

    await fetchQamEntryPositionState();

    expect(getQamEntryPositionState()).toBe("above_decky");
  });

  it("keeps what it held when the read fails", async () => {
    setQamEntryPositionState("below_decky");
    vi.mocked(getSettings).mockRejectedValue(new Error("down"));

    await expect(fetchQamEntryPositionState()).rejects.toThrow("down");

    expect(getQamEntryPositionState()).toBe("below_decky");
  });
});
