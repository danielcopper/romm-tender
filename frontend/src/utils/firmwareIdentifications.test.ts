// The identification of the library section's files: asked after the page is
// shown, one at a time, kept by MD5 for the page, and forgotten when it is left.

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as backend from "../api/backend";
import { forgetIdentifications, IDENTIFICATION_TTL_MS, useIdentifications } from "./firmwareIdentifications";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("useIdentifications", () => {
  beforeEach(() => {
    vi.mocked(backend.identifyLibraryFirmware).mockReset();
  });

  afterEach(() => {
    forgetIdentifications();
    vi.useRealTimers();
  });

  it("reads Checking… until each answer arrives, asking one file at a time", async () => {
    const first = deferred<{ success: true; known_as: string[] | null }>();
    vi.mocked(backend.identifyLibraryFirmware).mockReturnValueOnce(first.promise);
    vi.mocked(backend.identifyLibraryFirmware).mockResolvedValueOnce({ success: true, known_as: [] });

    const { result } = renderHook(() => useIdentifications(["a".repeat(32), "b".repeat(32)]));

    expect(result.current).toEqual({ ["a".repeat(32)]: "checking", ["b".repeat(32)]: "checking" });
    expect(vi.mocked(backend.identifyLibraryFirmware)).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve({ success: true, known_as: ["gb_bios.bin"] }));
    await waitFor(() => expect(result.current).toEqual({ ["a".repeat(32)]: ["gb_bios.bin"], ["b".repeat(32)]: [] }));
  });

  it("keeps an answer for the page and asks nothing twice", async () => {
    vi.mocked(backend.identifyLibraryFirmware).mockResolvedValue({ success: true, known_as: ["x.bin"] });
    const { result, rerender } = renderHook(({ md5s }) => useIdentifications(md5s), {
      initialProps: { md5s: ["c".repeat(32)] },
    });
    await waitFor(() => expect(result.current["c".repeat(32)]).toEqual(["x.bin"]));

    rerender({ md5s: ["c".repeat(32), "d".repeat(32)] });
    await waitFor(() => expect(result.current["d".repeat(32)]).toEqual(["x.bin"]));

    expect(vi.mocked(backend.identifyLibraryFirmware).mock.calls.map(([md5]) => md5)).toEqual([
      "c".repeat(32),
      "d".repeat(32),
    ]);
  });

  it("asks again once the page was left", async () => {
    vi.mocked(backend.identifyLibraryFirmware).mockResolvedValue({ success: true, known_as: [] });
    const first = renderHook(() => useIdentifications(["e".repeat(32)]));
    await waitFor(() => expect(first.result.current["e".repeat(32)]).toEqual([]));
    first.unmount();

    const second = renderHook(() => useIdentifications(["e".repeat(32)]));

    expect(second.result.current["e".repeat(32)]).toBe("checking");
    await waitFor(() => expect(vi.mocked(backend.identifyLibraryFirmware)).toHaveBeenCalledTimes(2));
  });

  it("asks again once an answer is older than half an hour", async () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1_000);
    vi.mocked(backend.identifyLibraryFirmware).mockResolvedValue({ success: true, known_as: [] });
    const { result, rerender } = renderHook(({ md5s }) => useIdentifications(md5s), {
      initialProps: { md5s: ["f".repeat(32)] },
    });
    await waitFor(() => expect(result.current["f".repeat(32)]).toEqual([]));

    now.mockReturnValue(1_000 + IDENTIFICATION_TTL_MS + 1);
    rerender({ md5s: ["f".repeat(32), "0".repeat(32)] });

    await waitFor(() => expect(vi.mocked(backend.identifyLibraryFirmware)).toHaveBeenCalledTimes(3));
    now.mockRestore();
  });

  it("reads a question nothing could answer as nothing established", async () => {
    vi.mocked(backend.identifyLibraryFirmware).mockRejectedValue(new Error("gone"));

    const { result } = renderHook(() => useIdentifications(["9".repeat(32)]));

    await waitFor(() => expect(result.current["9".repeat(32)]).toBe("unknown"));
  });
});
