import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import * as backend from "../../api/backend";
import { useRomAnswer } from "./useRomAnswer";

vi.mock("../../api/backend", () => ({ logWarn: vi.fn() }));

const OPTIONS = { owner: "Owner", what: "thing" };

describe("useRomAnswer", () => {
  it("asks again when rereadOn changes, and not on a re-render that keeps it", async () => {
    const ask = vi.fn((romId: number) => Promise.resolve(`answer ${romId}`));
    const { result, rerender } = renderHook(
      ({ installed }) => useRomAnswer(42, true, ask, { ...OPTIONS, rereadOn: installed }),
      { initialProps: { installed: false } },
    );
    await waitFor(() => expect(result.current).toBe("answer 42"));
    rerender({ installed: false });
    expect(ask).toHaveBeenCalledTimes(1);

    rerender({ installed: true });
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(2));
  });

  it("asks again on a change that can move the answer", async () => {
    const ask = vi.fn(() => Promise.resolve("a"));
    renderHook(() => useRomAnswer(42, true, ask, OPTIONS));
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(1));

    act(() => {
      globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "core_changed" } }));
    });
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(2));
  });

  it("reads another ROM's answer as none, and a failed read as none, logging it", async () => {
    const ask = vi.fn((romId: number) =>
      romId === 42 ? Promise.resolve("forty-two") : Promise.reject(new Error("boom")),
    );
    const { result, rerender } = renderHook(({ romId }) => useRomAnswer(romId, true, ask, OPTIONS), {
      initialProps: { romId: 42 },
    });
    await waitFor(() => expect(result.current).toBe("forty-two"));

    rerender({ romId: 99 });
    expect(result.current).toBeNull();
    await waitFor(() =>
      expect(backend.logWarn).toHaveBeenCalledWith("Owner: thing read for romId=99 failed: Error: boom"),
    );
    expect(result.current).toBeNull();
  });

  it("asks nothing and holds nothing while not wanted", async () => {
    const ask = vi.fn(() => Promise.resolve("a"));
    const { result, rerender } = renderHook(({ wanted }) => useRomAnswer(42, wanted, ask, OPTIONS), {
      initialProps: { wanted: true },
    });
    await waitFor(() => expect(result.current).toBe("a"));

    rerender({ wanted: false });
    expect(result.current).toBeNull();
    expect(ask).toHaveBeenCalledTimes(1);
  });
});
