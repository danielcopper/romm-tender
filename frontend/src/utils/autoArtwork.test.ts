import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useAutoArtwork, _resetArtworkAppliedForTests } from "./autoArtwork";
import * as artwork from "./artwork";
import * as backend from "../api/backend";

vi.mock("./artwork", () => ({
  applyArtwork: vi.fn(),
  fillEmptyArtwork: vi.fn(),
}));

vi.mock("../api/backend", () => ({
  debugLog: vi.fn(),
}));

describe("useAutoArtwork", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(artwork.applyArtwork).mockResolvedValue(4);
    vi.mocked(artwork.fillEmptyArtwork).mockResolvedValue(4);
    _resetArtworkAppliedForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("applies nothing if romId is null", () => {
    renderHook(() => useAutoArtwork(100, null));
    expect(artwork.fillEmptyArtwork).not.toHaveBeenCalled();
    expect(artwork.applyArtwork).not.toHaveBeenCalled();
  });

  it("fills only the empty slots on a first visit, once per (appId, romId) pair", async () => {
    const { rerender } = renderHook(({ appId, romId }) => useAutoArtwork(appId, romId), {
      initialProps: { appId: 100, romId: 42 },
    });

    await waitFor(() => {
      expect(artwork.fillEmptyArtwork).toHaveBeenCalledTimes(1);
      expect(artwork.fillEmptyArtwork).toHaveBeenCalledWith(42, 100);
    });
    expect(artwork.applyArtwork).not.toHaveBeenCalled();

    // Re-render with same (appId, romId) does not apply again
    rerender({ appId: 100, romId: 42 });
    expect(artwork.fillEmptyArtwork).toHaveBeenCalledTimes(1);
  });

  it("does not apply again when the page is opened again for a pair already applied", async () => {
    const first = renderHook(() => useAutoArtwork(100, 42));
    await waitFor(() => expect(artwork.fillEmptyArtwork).toHaveBeenCalledTimes(1));
    first.unmount();

    // A fresh mount runs the effect again; only the session's record of the
    // applied pair stops a second apply.
    renderHook(() => useAutoArtwork(100, 42));
    await Promise.resolve();

    expect(artwork.fillEmptyArtwork).toHaveBeenCalledTimes(1);
    expect(artwork.applyArtwork).not.toHaveBeenCalled();
  });

  it("replaces every slot when romId changes for the same appId (version switch)", async () => {
    const { rerender } = renderHook(({ appId, romId }) => useAutoArtwork(appId, romId), {
      initialProps: { appId: 100, romId: 42 },
    });

    await waitFor(() => {
      expect(artwork.fillEmptyArtwork).toHaveBeenCalledWith(42, 100);
    });

    // Version switch: romId changes to 43
    rerender({ appId: 100, romId: 43 });

    await waitFor(() => {
      expect(artwork.applyArtwork).toHaveBeenCalledTimes(1);
      expect(artwork.applyArtwork).toHaveBeenCalledWith(43, 100);
    });
    expect(artwork.fillEmptyArtwork).toHaveBeenCalledTimes(1);
  });

  it("logs with debugLog and does not mark as applied if the apply throws, so the next visit applies again", async () => {
    vi.mocked(artwork.fillEmptyArtwork).mockRejectedValue(new Error("network failure"));

    const { unmount } = renderHook(() => useAutoArtwork(100, 42, "CustomAutoArtwork"));

    await waitFor(() => {
      expect(backend.debugLog).toHaveBeenCalledWith(
        expect.stringContaining("CustomAutoArtwork: Error: network failure"),
      );
    });

    unmount();

    // Because it threw, it wasn't recorded as applied, so the next visit applies again
    vi.mocked(artwork.fillEmptyArtwork).mockResolvedValue(4);
    renderHook(() => useAutoArtwork(100, 42, "CustomAutoArtwork"));

    await waitFor(() => {
      expect(artwork.fillEmptyArtwork).toHaveBeenCalledTimes(2);
    });
  });
});
