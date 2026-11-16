import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useContentCheck } from "./useContentCheck";
import * as backend from "../api/backend";
import { emitHostEvent, hostEventListenerCount } from "../test-utils/host-event-bus";
import { VERIFY_UNREACHABLE_MESSAGE } from "./adoptWording";
import type { VerifyContentResult, VerifyProgressEvent } from "../types";

vi.mock("../api/backend", () => ({
  verifyExistingContent: vi.fn(),
  logWarn: vi.fn(),
}));

const ROM_ID = 42;
const MATCH: VerifyContentResult = { status: "match", message: "Identical", differences: [] };

function heldCheck() {
  let answer!: (result: VerifyContentResult) => void;
  vi.mocked(backend.verifyExistingContent).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        answer = resolve;
      }),
  );
  return (result: VerifyContentResult) => answer(result);
}

describe("useContentCheck", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("checks the candidate's path, and holds the verdict once it lands", async () => {
    const answer = heldCheck();
    const { result } = renderHook(() => useContentCheck(ROM_ID, "/roms/snes/Other.sfc", "Tag"));
    expect(result.current).toMatchObject({ verifying: false, progress: null, verdict: null });

    act(() => void result.current.verify());
    expect(backend.verifyExistingContent).toHaveBeenCalledExactlyOnceWith(ROM_ID, "/roms/snes/Other.sfc");
    expect(result.current).toMatchObject({ verifying: true, progress: 0, verdict: null });

    await act(async () => answer(MATCH));
    expect(result.current).toMatchObject({ verifying: false, progress: null, verdict: MATCH });
  });

  it("checks the game's own location where there is no candidate", async () => {
    vi.mocked(backend.verifyExistingContent).mockResolvedValueOnce(MATCH);
    const { result } = renderHook(() => useContentCheck(ROM_ID, undefined, "Tag"));

    await act(async () => result.current.verify());

    expect(backend.verifyExistingContent).toHaveBeenCalledExactlyOnceWith(ROM_ID, null);
  });

  it("takes its progress from this ROM's frames only, and none from a frame with no total", () => {
    heldCheck();
    const { result } = renderHook(() => useContentCheck(ROM_ID, undefined, "Tag"));
    act(() => void result.current.verify());

    act(() => {
      emitHostEvent<VerifyProgressEvent>("verify_progress", { rom_id: ROM_ID, bytes_done: 1, bytes_total: 4 });
      emitHostEvent<VerifyProgressEvent>("verify_progress", { rom_id: 99, bytes_done: 9, bytes_total: 10 });
      emitHostEvent<VerifyProgressEvent>("verify_progress", { rom_id: ROM_ID, bytes_done: 3, bytes_total: 0 });
    });

    expect(result.current.progress).toBe(0.25);
  });

  it("asks nothing a second time while a check runs", () => {
    heldCheck();
    const { result } = renderHook(() => useContentCheck(ROM_ID, undefined, "Tag"));
    act(() => void result.current.verify());
    act(() => void result.current.verify());

    expect(backend.verifyExistingContent).toHaveBeenCalledTimes(1);
  });

  it("clears the last verdict and starts again from nothing on a second check", async () => {
    vi.mocked(backend.verifyExistingContent).mockResolvedValueOnce(MATCH);
    const { result } = renderHook(() => useContentCheck(ROM_ID, undefined, "Tag"));
    await act(async () => result.current.verify());
    const answer = heldCheck();
    act(() => {
      emitHostEvent<VerifyProgressEvent>("verify_progress", { rom_id: ROM_ID, bytes_done: 3, bytes_total: 4 });
    });

    act(() => void result.current.verify());
    expect(result.current).toMatchObject({ verifying: true, progress: 0, verdict: null });
    await act(async () => answer(MATCH));
  });

  it("answers an unreachable server when the check throws, and logs it as a warning", async () => {
    vi.mocked(backend.verifyExistingContent).mockRejectedValueOnce(new Error("socket closed"));
    const { result } = renderHook(() => useContentCheck(ROM_ID, undefined, "Tag"));

    await act(async () => result.current.verify());

    expect(result.current.verdict).toEqual({ status: "error", message: VERIFY_UNREACHABLE_MESSAGE, differences: [] });
    expect(result.current.verifying).toBe(false);
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the content check of romId=42 failed: Error: socket closed",
    );
  });

  it("stops listening for the check's progress once unmounted", () => {
    const { unmount } = renderHook(() => useContentCheck(ROM_ID, undefined, "Tag"));
    expect(hostEventListenerCount("verify_progress")).toBe(1);

    unmount();

    expect(hostEventListenerCount("verify_progress")).toBe(0);
  });
});
