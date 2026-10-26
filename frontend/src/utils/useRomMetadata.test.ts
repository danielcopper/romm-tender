import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useRomMetadata } from "./useRomMetadata";
import * as backend from "../api/backend";
import * as sharedReads from "../api/sharedReads";
import type { RomMetadata } from "../types";

vi.mock("../api/sharedReads", () => ({
  getRomMetadataShared: vi.fn(),
}));

const record = (summary: string): RomMetadata => ({
  summary,
  genres: [],
  companies: [],
  first_release_date: null,
  average_rating: null,
  game_modes: [],
  player_count: "",
  cached_at: 1000,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("useRomMetadata", () => {
  beforeEach(() => {
    vi.mocked(sharedReads.getRomMetadataShared).mockReset();
    vi.spyOn(backend, "logWarn")
      .mockReset()
      .mockImplementation(() => {});
  });

  it("answers nothing and reads nothing for no ROM", () => {
    const { result } = renderHook(() => useRomMetadata(null));

    expect(result.current).toBeNull();
    expect(sharedReads.getRomMetadataShared).not.toHaveBeenCalled();
  });

  it("answers the ROM's record once the read lands", async () => {
    vi.mocked(sharedReads.getRomMetadataShared).mockResolvedValue(record("A golf game."));

    const { result } = renderHook(() => useRomMetadata(42));

    expect(result.current).toBeNull();
    await waitFor(() => expect(result.current?.summary).toBe("A golf game."));
    expect(sharedReads.getRomMetadataShared).toHaveBeenCalledExactlyOnceWith(42);
  });

  it("answers nothing, and logs the read, where it throws", async () => {
    vi.mocked(sharedReads.getRomMetadataShared).mockRejectedValue(new Error("socket closed"));

    const { result } = renderHook(() => useRomMetadata(42));

    await waitFor(() =>
      expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
        "useRomMetadata: the metadata read for rom 42 failed: Error: socket closed",
      ),
    );
    expect(result.current).toBeNull();
  });

  it("never answers one ROM's record for another, while the other's read is open", async () => {
    const second = deferred<RomMetadata>();
    vi.mocked(sharedReads.getRomMetadataShared)
      .mockResolvedValueOnce(record("First."))
      .mockReturnValueOnce(second.promise);

    const { result, rerender } = renderHook(({ romId }) => useRomMetadata(romId), { initialProps: { romId: 42 } });
    await waitFor(() => expect(result.current?.summary).toBe("First."));

    rerender({ romId: 43 });
    expect(result.current).toBeNull();

    await act(async () => second.resolve(record("Second.")));
    expect(result.current?.summary).toBe("Second.");
  });

  it("answers nothing for a ROM whose read threw, though an earlier read of it landed", async () => {
    vi.mocked(sharedReads.getRomMetadataShared)
      .mockResolvedValueOnce(record("First."))
      .mockReturnValueOnce(new Promise(() => {}))
      .mockRejectedValueOnce(new Error("socket closed"));

    const { result, rerender } = renderHook(({ romId }) => useRomMetadata(romId), { initialProps: { romId: 42 } });
    await waitFor(() => expect(result.current?.summary).toBe("First."));
    rerender({ romId: 43 });
    rerender({ romId: 42 });
    expect(result.current?.summary).toBe("First.");

    await waitFor(() => expect(result.current).toBeNull());
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "useRomMetadata: the metadata read for rom 42 failed: Error: socket closed",
    );
  });

  it("drops a failed read that lands after its ROM was left", async () => {
    const first = deferred<RomMetadata>();
    let rejectFirst!: (e: Error) => void;
    vi.mocked(sharedReads.getRomMetadataShared)
      .mockReturnValueOnce(
        new Promise<RomMetadata>((_, reject) => {
          rejectFirst = reject;
        }),
      )
      .mockReturnValueOnce(first.promise);

    const { result, rerender } = renderHook(({ romId }) => useRomMetadata(romId), { initialProps: { romId: 42 } });
    rerender({ romId: 43 });
    await act(async () => first.resolve(record("Second.")));
    expect(result.current?.summary).toBe("Second.");

    await act(async () => rejectFirst(new Error("socket closed")));
    expect(result.current?.summary).toBe("Second.");
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "useRomMetadata: the metadata read for rom 42 failed: Error: socket closed",
    );
  });

  it("drops a read that lands after its ROM was left", async () => {
    const first = deferred<RomMetadata>();
    vi.mocked(sharedReads.getRomMetadataShared)
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(record("Second."));

    const { result, rerender } = renderHook(({ romId }) => useRomMetadata(romId), { initialProps: { romId: 42 } });
    rerender({ romId: 43 });
    await waitFor(() => expect(result.current?.summary).toBe("Second."));

    await act(async () => first.resolve(record("First.")));
    expect(result.current?.summary).toBe("Second.");
  });
});
