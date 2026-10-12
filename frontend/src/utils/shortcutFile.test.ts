import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as backend from "../api/backend";
import { readShortcutFile, sharedShortcutFileReader } from "./shortcutFile";

/** The endpoint is one `vi.fn` for the whole file: start each test from its suite default. */
function resetFileEndpoint() {
  vi.mocked(backend.getShortcutLaunchOptions)
    .mockReset()
    .mockResolvedValue(undefined as unknown as backend.ShortcutLaunchOptions);
}

describe("sharedShortcutFileReader", () => {
  beforeEach(resetFileEndpoint);
  afterEach(resetFileEndpoint);

  it("reads nothing until it is first asked", () => {
    const ask = vi.mocked(backend.getShortcutLaunchOptions);

    sharedShortcutFileReader([1, 2]);

    expect(ask).not.toHaveBeenCalled();
  });

  it("answers every caller from one backend call for all its app IDs", async () => {
    const ask = vi
      .spyOn(backend, "getShortcutLaunchOptions")
      .mockResolvedValue({ success: true, launch_options: [{ app_id: 2, launch_options: "two" }] });
    const readFile = sharedShortcutFileReader([1, 2]);

    const [first, second] = await Promise.all([readFile(), readFile()]);

    expect(ask).toHaveBeenCalledTimes(1);
    expect(ask).toHaveBeenCalledWith([1, 2]);
    expect(first).toBe(second);
    expect(first).toEqual({ status: "read", launchOptions: new Map([[2, "two"]]) });
  });
});

describe("readShortcutFile", () => {
  beforeEach(resetFileEndpoint);
  afterEach(resetFileEndpoint);

  it("reads a refusal as a file that could not be read, with the refusal's message", async () => {
    vi.mocked(backend.getShortcutLaunchOptions).mockResolvedValue({
      success: false,
      reason: "shortcut_file_unreadable",
      message: "Steam's shortcut file could not be read.",
    });

    await expect(readShortcutFile([1])).resolves.toEqual({
      status: "unreadable",
      why: "Steam's shortcut file could not be read.",
    });
  });

  it("reads no answer at all as a file that could not be read", async () => {
    await expect(readShortcutFile([1])).resolves.toEqual({ status: "unreadable", why: "no answer" });
  });
});
