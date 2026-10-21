import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import * as backend from "../api/backend";
import type { TenderFolderState } from "../types";
import { TENDER_FOLDER_AT_HOME } from "../test-utils/tenderFolder";
import { showForgetRecordedModal } from "./settings/TenderFolderModals";
import { showToast } from "../utils/toast";
import { useTenderFolder } from "./useTenderFolder";

vi.mock("../utils/toast", () => ({ showToast: vi.fn() }));

vi.mock("./settings/TenderFolderModals", () => ({
  showForgetRecordedModal: vi.fn(),
}));

const ON_SD: TenderFolderState = {
  ...TENDER_FOLDER_AT_HOME,
  place: "/run/media/deck/SD",
  folder: "/run/media/deck/SD/Tender",
  default: false,
  drive: { kind: "sd_card", name: "Emulation", mount_point: "/run/media/deck/SD", connected: true },
  drives: [
    {
      name: "Emulation",
      mount_point: "/run/media/deck/SD",
      kind: "sd_card",
      folder: "/run/media/deck/SD/Tender",
      free_bytes: 1,
    },
  ],
};

async function mounted() {
  const hook = renderHook(() => useTenderFolder());
  await waitFor(() => expect(hook.result.current.state).toEqual(TENDER_FOLDER_AT_HOME));
  return hook;
}

describe("useTenderFolder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(backend.getTenderFolder).mockResolvedValue(TENDER_FOLDER_AT_HOME);
    vi.mocked(backend.chooseTenderPlace).mockResolvedValue(ON_SD);
  });

  it("reads the folder on mount, and holds null where the read failed", async () => {
    await mounted();

    vi.mocked(backend.getTenderFolder).mockRejectedValue(new Error("boom"));
    const failed = renderHook(() => useTenderFolder());
    await waitFor(() => expect(failed.result.current.state).toBeNull());
  });

  it("a choice answers the state as it now stands", async () => {
    const hook = await mounted();

    act(() => hook.result.current.chooseDrive("/run/media/deck/SD"));

    await waitFor(() => expect(hook.result.current.state).toEqual(ON_SD));
    expect(backend.chooseTenderPlace).toHaveBeenCalledWith("/run/media/deck/SD");
    expect(hook.result.current.refusal).toBeNull();
  });

  it("the default is chosen as no place", async () => {
    const hook = await mounted();

    act(() => hook.result.current.chooseDefault());

    await waitFor(() => expect(backend.chooseTenderPlace).toHaveBeenCalledWith(null));
  });

  it("a refusal keeps the state and holds its sentence", async () => {
    vi.mocked(backend.chooseTenderPlace).mockResolvedValue({
      success: false,
      reason: "place_not_writable",
      message: "/locked cannot be written to.",
    });
    const hook = await mounted();

    act(() => hook.result.current.chooseDrive("/locked"));

    await waitFor(() => expect(hook.result.current.refusal).toBe("/locked cannot be written to."));
    expect(hook.result.current.state).toEqual(TENDER_FOLDER_AT_HOME);
  });

  it("a typed place the window accepted becomes the state held, and the last refusal goes", async () => {
    vi.mocked(backend.chooseTenderPlace).mockResolvedValue({
      success: false,
      reason: "place_not_writable",
      message: "/locked cannot be written to.",
    });
    const accepted = {
      ...TENDER_FOLDER_AT_HOME,
      place: "/home/deck/Games",
      folder: "/home/deck/Games/Tender",
      default: false,
    };
    const hook = await mounted();
    act(() => hook.result.current.chooseDrive("/locked"));
    await waitFor(() => expect(hook.result.current.refusal).toBe("/locked cannot be written to."));

    act(() => hook.result.current.acceptTyped(accepted));

    expect(hook.result.current.state).toEqual(accepted);
    expect(hook.result.current.refusal).toBeNull();
  });

  it("no switch raises a toast: a tile, the default, a recorded folder or a typed place", async () => {
    vi.mocked(backend.switchToRecordedTenderFolder).mockResolvedValue(ON_SD);
    const hook = await mounted();

    act(() => hook.result.current.chooseDrive("/run/media/deck/SD"));
    await waitFor(() => expect(hook.result.current.state).toEqual(ON_SD));
    await waitFor(() => expect(hook.result.current.busy).toBe(false));
    vi.mocked(backend.chooseTenderPlace).mockResolvedValue(TENDER_FOLDER_AT_HOME);
    act(() => hook.result.current.chooseDefault());
    await waitFor(() => expect(hook.result.current.state).toEqual(TENDER_FOLDER_AT_HOME));
    await waitFor(() => expect(hook.result.current.busy).toBe(false));
    act(() => hook.result.current.switchToRecorded("/run/media/deck/SD/Tender"));
    await waitFor(() => expect(hook.result.current.state).toEqual(ON_SD));
    act(() => hook.result.current.acceptTyped(TENDER_FOLDER_AT_HOME));

    expect(showToast).not.toHaveBeenCalled();
  });

  it("forgets only after the confirmation", async () => {
    vi.mocked(backend.forgetTenderFolderRecords).mockResolvedValue(TENDER_FOLDER_AT_HOME);
    vi.mocked(showForgetRecordedModal).mockResolvedValue(false);
    const hook = await mounted();

    act(() => hook.result.current.forget("/sd/Tender"));
    await waitFor(() => expect(hook.result.current.busy).toBe(false));
    expect(backend.forgetTenderFolderRecords).not.toHaveBeenCalled();

    vi.mocked(showForgetRecordedModal).mockResolvedValue(true);
    act(() => hook.result.current.forget("/sd/Tender"));
    await waitFor(() => expect(backend.forgetTenderFolderRecords).toHaveBeenCalledWith("/sd/Tender"));
  });

  it("goes back to a folder something is recorded under", async () => {
    vi.mocked(backend.switchToRecordedTenderFolder).mockResolvedValue(ON_SD);
    const hook = await mounted();

    act(() => hook.result.current.switchToRecorded("/run/media/deck/SD/Tender"));

    await waitFor(() => expect(hook.result.current.state).toEqual(ON_SD));
  });

  it("a change that fails is logged and keeps the state", async () => {
    vi.mocked(backend.chooseTenderPlace).mockRejectedValue(new Error("socket closed"));
    const logError = vi.spyOn(backend, "logError");
    const hook = await mounted();

    act(() => hook.result.current.chooseDefault());

    await waitFor(() =>
      expect(logError).toHaveBeenCalledWith("Failed to change Tender's folder: Error: socket closed"),
    );
    expect(hook.result.current.state).toEqual(TENDER_FOLDER_AT_HOME);
  });
});
