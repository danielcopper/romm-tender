import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import * as backend from "../api/backend";
import type { TenderFolderState, TenderPlaceAsks } from "../types";
import { TENDER_FOLDER_AT_HOME } from "../test-utils/tenderFolder";
import { showCreateMissingModal, showForgetRecordedModal, showTenderNamedModal } from "./settings/TenderFolderModals";
import { useTenderFolder } from "./useTenderFolder";

vi.mock("./settings/TenderFolderModals", () => ({
  showCreateMissingModal: vi.fn(),
  showForgetRecordedModal: vi.fn(),
  showTenderNamedModal: vi.fn(),
}));

const ON_SD: TenderFolderState = {
  ...TENDER_FOLDER_AT_HOME,
  place: "/run/media/deck/SD",
  folder: "/run/media/deck/SD/Tender",
  default: false,
};

function asks(place: string, overrides: Partial<TenderPlaceAsks> = {}): TenderPlaceAsks {
  return { place, folder: `${place}/Tender`, tender_parent: null, existing: place, missing: null, ...overrides };
}

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

  describe("a typed place", () => {
    it("that exists is chosen without a question", async () => {
      vi.mocked(backend.checkTenderPlace).mockResolvedValue(asks("/home/deck/Games"));
      const hook = await mounted();

      act(() => hook.result.current.chooseTyped("~/Games"));

      await waitFor(() => expect(backend.chooseTenderPlace).toHaveBeenCalledWith("~/Games"));
      expect(showTenderNamedModal).not.toHaveBeenCalled();
      expect(showCreateMissingModal).not.toHaveBeenCalled();
    });

    it("whose folders are missing is chosen only once Create is pressed", async () => {
      vi.mocked(backend.checkTenderPlace).mockResolvedValue(
        asks("/home/deck/Games", { existing: "/home/deck", missing: "Games" }),
      );
      vi.mocked(showCreateMissingModal).mockResolvedValue(false);
      const hook = await mounted();

      act(() => hook.result.current.chooseTyped("~/Games"));

      await waitFor(() => expect(hook.result.current.busy).toBe(false));
      expect(showCreateMissingModal).toHaveBeenCalledWith("/home/deck/Games", "/home/deck", "Games");
      expect(backend.chooseTenderPlace).not.toHaveBeenCalled();

      vi.mocked(showCreateMissingModal).mockResolvedValue(true);
      act(() => hook.result.current.chooseTyped("~/Games"));
      await waitFor(() => expect(backend.chooseTenderPlace).toHaveBeenCalledWith("~/Games"));
    });

    it("named Tender is replaced by its parent when asked to, which is checked again", async () => {
      vi.mocked(backend.checkTenderPlace).mockImplementation(async (text) =>
        text === "/sd/Tender" ? asks("/sd/Tender", { tender_parent: "/sd" }) : asks("/sd"),
      );
      vi.mocked(showTenderNamedModal).mockResolvedValue("parent");
      const hook = await mounted();

      act(() => hook.result.current.chooseTyped("/sd/Tender"));

      await waitFor(() => expect(backend.chooseTenderPlace).toHaveBeenCalledWith("/sd"));
      expect(backend.checkTenderPlace).toHaveBeenLastCalledWith("/sd");
    });

    it("named Tender is kept as typed when asked to", async () => {
      vi.mocked(backend.checkTenderPlace).mockResolvedValue(asks("/sd/Tender", { tender_parent: "/sd" }));
      vi.mocked(showTenderNamedModal).mockResolvedValue("typed");
      const hook = await mounted();

      act(() => hook.result.current.chooseTyped("/sd/Tender"));

      await waitFor(() => expect(backend.chooseTenderPlace).toHaveBeenCalledWith("/sd/Tender"));
    });

    it("named Tender chooses nothing when the question is dismissed", async () => {
      vi.mocked(backend.checkTenderPlace).mockResolvedValue(asks("/sd/Tender", { tender_parent: "/sd" }));
      vi.mocked(showTenderNamedModal).mockResolvedValue("cancel");
      const hook = await mounted();

      act(() => hook.result.current.chooseTyped("/sd/Tender"));

      await waitFor(() => expect(hook.result.current.busy).toBe(false));
      expect(backend.chooseTenderPlace).not.toHaveBeenCalled();
    });

    it("that is refused when checked is shown with its sentence and chooses nothing", async () => {
      vi.mocked(backend.checkTenderPlace).mockResolvedValue({
        success: false,
        reason: "place_in_rom_folder",
        message: "fallback",
        path: "/r/roms/Tender",
        source: "emudeck",
        folder: "/r/roms",
        system: null,
      });
      const hook = await mounted();

      act(() => hook.result.current.chooseTyped("/r/roms"));

      await waitFor(() =>
        expect(hook.result.current.refusal).toBe(
          "/r/roms/Tender lies inside EmuDeck's ROM folder (/r/roms). Choose a place outside it.",
        ),
      );
      expect(backend.chooseTenderPlace).not.toHaveBeenCalled();
    });
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
