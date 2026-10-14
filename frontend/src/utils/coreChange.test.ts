import { describe, it, expect, vi, beforeEach } from "vitest";
import { confirmCoreChangeIfNeeded } from "./coreChange";
import * as backend from "../api/backend";

vi.mock("../api/backend", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/backend")>();
  return {
    ...actual,
    checkCoreChange: vi.fn(),
    debugLog: vi.fn(),
  };
});

describe("confirmCoreChangeIfNeeded", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns true without prompting when core is unchanged", async () => {
    vi.mocked(backend.checkCoreChange).mockResolvedValueOnce({ changed: false });
    const prompt = vi.fn().mockResolvedValue(false);

    const result = await confirmCoreChangeIfNeeded(101, prompt);

    expect(backend.checkCoreChange).toHaveBeenCalledWith(101);
    expect(prompt).not.toHaveBeenCalled();
    expect(result).toBe(true);
  });

  it("prompts with labels and returns true when user accepts change", async () => {
    vi.mocked(backend.checkCoreChange).mockResolvedValueOnce({
      changed: true,
      old_label: "Beetle PSX",
      new_label: "DuckStation",
    });
    const prompt = vi.fn().mockResolvedValue(true);

    const result = await confirmCoreChangeIfNeeded(101, prompt);

    expect(prompt).toHaveBeenCalledWith("Beetle PSX", "DuckStation");
    expect(result).toBe(true);
  });

  it("prompts with fallback to core identifiers when labels are missing", async () => {
    vi.mocked(backend.checkCoreChange).mockResolvedValueOnce({
      changed: true,
      old_core: "mednafen_psx_libretro",
      new_core: "duckstation_libretro",
    });
    const prompt = vi.fn().mockResolvedValue(false);

    const result = await confirmCoreChangeIfNeeded(101, prompt);

    expect(prompt).toHaveBeenCalledWith("mednafen_psx_libretro", "duckstation_libretro");
    expect(result).toBe(false);
  });

  it("prompts with 'Unknown' when both labels and cores are missing", async () => {
    vi.mocked(backend.checkCoreChange).mockResolvedValueOnce({
      changed: true,
    });
    const prompt = vi.fn().mockResolvedValue(true);

    const result = await confirmCoreChangeIfNeeded(101, prompt);

    expect(prompt).toHaveBeenCalledWith("Unknown", "Unknown");
    expect(result).toBe(true);
  });

  it("proceeds without prompting when the check fails", async () => {
    vi.mocked(backend.checkCoreChange).mockRejectedValueOnce(new Error("RPC failed"));
    const prompt = vi.fn();

    const result = await confirmCoreChangeIfNeeded(101, prompt);

    expect(result).toBe(true);
    expect(prompt).not.toHaveBeenCalled();
  });

  it("hands a failed check's error to onError", async () => {
    vi.mocked(backend.checkCoreChange).mockRejectedValueOnce(new Error("RPC failed"));
    const prompt = vi.fn();
    const onError = vi.fn();

    const result = await confirmCoreChangeIfNeeded(101, prompt, onError);

    expect(result).toBe(true);
    expect(prompt).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "RPC failed" }));
  });
});
