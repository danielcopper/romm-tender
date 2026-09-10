import { describe, it, expect, vi } from "vitest";
import { INSTALL_TYPE_UPDATE, requestPluginInstall } from "./deckyInstall";

const REQUEST = {
  artifact: "https://example.invalid/Tender.zip",
  name: "Tender",
  version: "0.33.0",
  hash: "abc123",
};

describe("requestPluginInstall", () => {
  it("hands Decky the five arguments in order, with the digest untouched", async () => {
    const call = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("DeckyBackend", { call });

    await requestPluginInstall(REQUEST);

    expect(call).toHaveBeenCalledTimes(1);
    expect(call).toHaveBeenCalledWith(
      "utilities/install_plugin",
      "https://example.invalid/Tender.zip",
      "Tender",
      "0.33.0",
      "abc123",
      2,
    );
    expect(INSTALL_TYPE_UPDATE).toBe(2);
  });

  it("passes a missing digest through as null rather than inventing one", async () => {
    const call = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("DeckyBackend", { call });

    await requestPluginInstall({ ...REQUEST, hash: null });

    expect(call.mock.calls[0]?.[4]).toBeNull();
  });

  it("rejects when the loader's bridge is not there at all", async () => {
    vi.stubGlobal("DeckyBackend", undefined);
    await expect(requestPluginInstall(REQUEST)).rejects.toThrow("not reachable");
  });

  it("rejects rather than throwing ReferenceError when the global was never declared", async () => {
    // No stub at all — the global is genuinely absent, which is the case the
    // `typeof` probe exists for. A bare reference would throw synchronously and
    // never reach the caller's catch.
    expect("DeckyBackend" in globalThis).toBe(false);
    await expect(requestPluginInstall(REQUEST)).rejects.toThrow("not reachable");
  });

  it("rejects when the bridge is there but carries no call", async () => {
    vi.stubGlobal("DeckyBackend", {});
    await expect(requestPluginInstall(REQUEST)).rejects.toThrow("not reachable");
  });

  it("surfaces an unknown route rather than resolving as if it had installed", async () => {
    const call = vi
      .fn()
      .mockRejectedValue(new Error("Python RouteNotFoundError: Route utilities/install_plugin does not exist."));
    vi.stubGlobal("DeckyBackend", { call });

    await expect(requestPluginInstall(REQUEST)).rejects.toThrow("RouteNotFoundError");
  });
});
