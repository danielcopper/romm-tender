import { describe, it, expect, vi } from "vitest";
import { announceSaveSync } from "./saveSyncEvents";

describe("announceSaveSync", () => {
  it("dispatches the save_sync change for the ROM", () => {
    const listener = vi.fn();
    globalThis.addEventListener("romm_data_changed", listener);
    try {
      announceSaveSync(42);
    } finally {
      globalThis.removeEventListener("romm_data_changed", listener);
    }
    expect((listener.mock.calls[0]![0] as CustomEvent).detail).toEqual({ type: "save_sync", rom_id: 42 });
  });

  it("carries whether the ROM has a conflict when the caller knows it", () => {
    const listener = vi.fn();
    globalThis.addEventListener("romm_data_changed", listener);
    try {
      announceSaveSync(42, true);
      announceSaveSync(42, false);
    } finally {
      globalThis.removeEventListener("romm_data_changed", listener);
    }
    expect(listener.mock.calls.map(([e]) => (e as CustomEvent).detail)).toEqual([
      { type: "save_sync", rom_id: 42, has_conflict: true },
      { type: "save_sync", rom_id: 42, has_conflict: false },
    ]);
  });
});
