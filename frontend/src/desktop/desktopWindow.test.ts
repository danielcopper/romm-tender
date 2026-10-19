import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { findDesktopWindow, findReactClient } from "./desktopWindow";
import * as deckyUi from "@decky/ui";

describe("desktopWindow", () => {
  const originalPopupManager = (window as unknown as { g_PopupManager?: unknown }).g_PopupManager;

  beforeEach(() => {
    delete (window as unknown as { g_PopupManager?: unknown }).g_PopupManager;
  });

  afterEach(() => {
    (window as unknown as { g_PopupManager?: unknown }).g_PopupManager = originalPopupManager;
    vi.restoreAllMocks();
  });

  describe("findDesktopWindow", () => {
    it("returns undefined when g_PopupManager is missing", () => {
      expect(findDesktopWindow()).toBeUndefined();
    });

    it("returns undefined when GetPopups is empty or has no SP Desktop popup", () => {
      (window as unknown as { g_PopupManager?: unknown }).g_PopupManager = {
        GetPopups: () => [{ m_strName: "QuickAccess-NA", m_popup: {} as Window }],
      };
      expect(findDesktopWindow()).toBeUndefined();
    });

    it("returns the desktop window when an SP Desktop popup is present", () => {
      const mockWin = { document: {} } as Window;
      (window as unknown as { g_PopupManager?: unknown }).g_PopupManager = {
        GetPopups: () => [
          { m_strName: "Notification", m_popup: {} as Window },
          { m_strName: "SP Desktop - Library", m_popup: mockWin },
        ],
      };
      expect(findDesktopWindow()).toBe(mockWin);
    });
  });

  describe("findReactClient", () => {
    it("resolves the client module carrying createRoot", () => {
      const mockClient = { createRoot: vi.fn() };
      vi.spyOn(deckyUi, "findModule").mockImplementation((fn: (m: unknown) => boolean) => {
        return fn(mockClient) ? mockClient : undefined;
      });

      const client = findReactClient();
      expect(client).toBeDefined();
      expect(client?.createRoot).toBe(mockClient.createRoot);
    });

    it("returns undefined when no module exports createRoot", () => {
      vi.spyOn(deckyUi, "findModule").mockReturnValue(undefined);
      expect(findReactClient()).toBeUndefined();
    });
  });
});
