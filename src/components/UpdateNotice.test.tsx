import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { toaster } from "@decky/api";
import { UpdateNotice, UPDATE_GAME_RUNNING_REASON, UPDATE_MANUAL_HINT } from "./UpdateNotice";
import * as backend from "../api/backend";
import { isAnySessionActive } from "../utils/sessionManager";
import {
  resetUpdateNoticeStoreForTests,
  setUpdateNoticeState,
  getUpdateNoticeState,
  type UpdateNoticeState,
} from "../utils/updateNoticeStore";

// The whole module, because the card imports exactly one thing from it and the
// real one registers Steam lifecycle hooks on import.
vi.mock("../utils/sessionManager", () => ({ isAnySessionActive: vi.fn(() => false) }));

const AVAILABLE: UpdateNoticeState = {
  available: true,
  latestVersion: "0.33.0",
  currentVersion: "0.32.0",
  downloadUrl: "https://example.invalid/Tender.zip",
  pluginName: "Tender",
  digest: "abc123",
  enabled: true,
};

const flushAsync = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

/** The card's Update button, or `null` where the card offers none. */
function updateButton(container: HTMLElement): HTMLButtonElement | null {
  const buttons = Array.from(container.querySelectorAll("button"));
  return (buttons.find((b) => b.textContent.startsWith("Update now") || b.textContent.startsWith("Waiting")) ??
    null) as HTMLButtonElement | null;
}

function dismissButton(container: HTMLElement): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Dismiss");
  if (!button) throw new Error("no Dismiss button rendered");
  return button as HTMLButtonElement;
}

// `logError` is a plain function over a callable, not a callable itself, so it
// is spied rather than reset like the wire mocks around it. Through a helper,
// because `vi.spyOn`'s generic constraint does not survive being written out as
// a type annotation under this TS config (SettingsPage.test.tsx says the same).
const spyLogError = () => vi.spyOn(backend, "logError").mockImplementation(() => {});

describe("UpdateNotice", () => {
  let logSpy: ReturnType<typeof spyLogError>;

  beforeEach(() => {
    resetUpdateNoticeStoreForTests();
    vi.mocked(isAnySessionActive).mockReturnValue(false);
    vi.mocked(toaster.toast).mockClear();
    vi.mocked(backend.dismissUpdateNotice).mockReset().mockResolvedValue({ success: true });
    logSpy = spyLogError();
    vi.stubGlobal("DeckyBackend", { call: vi.fn().mockResolvedValue(undefined) });
  });

  afterEach(() => {
    logSpy.mockRestore();
  });

  describe("when it appears at all", () => {
    it("renders nothing while no newer release is available", () => {
      const { queryByTestId } = render(<UpdateNotice />);
      expect(queryByTestId("update-notice")).toBeNull();
    });

    it("renders nothing when the notice carries no version to name", () => {
      setUpdateNoticeState({ ...AVAILABLE, latestVersion: null });
      const { queryByTestId } = render(<UpdateNotice />);
      expect(queryByTestId("update-notice")).toBeNull();
    });

    it("names both versions and shows the address once a release is available", () => {
      setUpdateNoticeState(AVAILABLE);
      const { getByTestId } = render(<UpdateNotice />);
      expect(getByTestId("update-notice").textContent).toContain("Tender 0.33.0 is available");
      expect(getByTestId("update-notice").textContent).toContain("You have 0.32.0.");
      expect(getByTestId("update-download-url").textContent).toBe("https://example.invalid/Tender.zip");
    });

    it("is a focus stop, so a reader can scroll the notice into view", () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<UpdateNotice />);
      const stop = container.querySelector('[data-testid="focusable"]');
      expect(stop?.getAttribute("data-activate")).toBe("true");
    });
  });

  describe("the button", () => {
    it("hands Decky the five install arguments in order, unchanged", async () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<UpdateNotice />);

      fireEvent.click(updateButton(container)!);
      await flushAsync();

      const call = vi.mocked(DeckyBackend!.call);
      expect(call).toHaveBeenCalledTimes(1);
      expect(call).toHaveBeenCalledWith(
        "utilities/install_plugin",
        "https://example.invalid/Tender.zip",
        "Tender",
        "0.33.0",
        "abc123",
        2,
      );
    });

    it("does not sit on the handover — the button reports it before the call settles", () => {
      setUpdateNoticeState(AVAILABLE);
      // A promise that never settles, which is what an update the loader is
      // already unloading us for looks like from here.
      vi.stubGlobal("DeckyBackend", { call: vi.fn(() => new Promise<never>(() => {})) });
      const { container } = render(<UpdateNotice />);

      fireEvent.click(updateButton(container)!);

      expect(updateButton(container)?.textContent).toBe("Waiting for Decky…");
      expect(updateButton(container)?.disabled).toBe(true);
    });

    it("is absent when the plugin could not read its own name, leaving the address", () => {
      setUpdateNoticeState({ ...AVAILABLE, pluginName: "" });
      const { container, getByTestId } = render(<UpdateNotice />);
      expect(updateButton(container)).toBeNull();
      expect(getByTestId("update-download-url").textContent).toBe("https://example.invalid/Tender.zip");
      expect(getByTestId("update-manual-hint").textContent).toContain(UPDATE_MANUAL_HINT);
      // Dismiss survives — a card that cannot install is still a card to answer.
      expect(dismissButton(container)).not.toBeNull();
    });

    it("points at the address when Decky's own route is not there", async () => {
      setUpdateNoticeState(AVAILABLE);
      const call = vi
        .fn()
        .mockRejectedValue(new Error("Python RouteNotFoundError: Route utilities/install_plugin does not exist."));
      vi.stubGlobal("DeckyBackend", { call });
      const { container, getByTestId, queryByTestId } = render(<UpdateNotice />);
      expect(queryByTestId("update-manual-hint")).toBeNull();

      fireEvent.click(updateButton(container)!);
      await flushAsync();

      expect(getByTestId("update-manual-hint").textContent).toContain("Decky's installer could not be reached.");
      expect(getByTestId("update-manual-hint").textContent).toContain(UPDATE_MANUAL_HINT);
      expect(getByTestId("update-download-url").textContent).toBe("https://example.invalid/Tender.zip");
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to hand the update to Decky's installer"));
      // Not a dead button: the press can be repeated once the reason is gone.
      expect(updateButton(container)?.disabled).toBe(false);
    });

    it("points at the address when the loader's bridge is missing entirely", async () => {
      setUpdateNoticeState(AVAILABLE);
      vi.stubGlobal("DeckyBackend", undefined);
      const { container, getByTestId } = render(<UpdateNotice />);

      fireEvent.click(updateButton(container)!);
      await flushAsync();

      expect(getByTestId("update-manual-hint").textContent).toContain(UPDATE_MANUAL_HINT);
    });
  });

  describe("while a game is running", () => {
    it("disables the button and says why", () => {
      setUpdateNoticeState(AVAILABLE);
      vi.mocked(isAnySessionActive).mockReturnValue(true);
      const { container } = render(<UpdateNotice />);
      expect(updateButton(container)?.disabled).toBe(true);
      expect(container.querySelector('[data-testid="button-desc"]')?.textContent).toBe(UPDATE_GAME_RUNNING_REASON);
    });

    it("refuses the press and names the reason when a game started after the render", async () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<UpdateNotice />);
      // Nothing re-renders this card when a session opens, so the press is where
      // the refusal has to hold.
      vi.mocked(isAnySessionActive).mockReturnValue(true);

      fireEvent.click(updateButton(container)!);
      await flushAsync();

      expect(vi.mocked(DeckyBackend!.call)).not.toHaveBeenCalled();
      expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: UPDATE_GAME_RUNNING_REASON });
      expect(updateButton(container)?.textContent).toBe("Update now");
    });
  });

  describe("Dismiss", () => {
    it("waves away the version the card is showing", async () => {
      setUpdateNoticeState(AVAILABLE);
      const { container } = render(<UpdateNotice />);

      fireEvent.click(dismissButton(container));
      await flushAsync();

      expect(backend.dismissUpdateNotice).toHaveBeenCalledWith("0.33.0");
      expect(getUpdateNoticeState().available).toBe(false);
    });

    it("leaves the card standing and logs when the dismissal does not persist", async () => {
      setUpdateNoticeState(AVAILABLE);
      vi.mocked(backend.dismissUpdateNotice).mockRejectedValue(new Error("disk full"));
      const { container, queryByTestId } = render(<UpdateNotice />);

      fireEvent.click(dismissButton(container));
      await flushAsync();

      expect(getUpdateNoticeState().available).toBe(true);
      expect(queryByTestId("update-notice")).not.toBeNull();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to dismiss the update notice"));
    });
  });
});
