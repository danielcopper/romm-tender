// The Library page's frame: the wide page, its Back row, when each tab's reads
// are asked, and what entering Platforms again does to its list line — the one
// Platforms behaviour that needs a second tab to drive. What each tab shows and
// does is otherwise pinned beside it — `library/PlatformsTab.test.tsx` and
// `library/CollectionsTab.test.tsx`.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { LibraryPage } from "./LibraryPage";
import * as backend from "../api/backend";
import type { PluginSettings } from "../types";

// The wide frame reaches Steam's tabbed page through this module, which is a
// webpack probe with no answer under happy-dom. The stub renders a button per
// tab plus the active tab's content, which is what makes a switch drivable.
vi.mock("../utils/deckyUiInternals", async () => {
  const { createElement: ce } = await import("react");
  type TabShape = { id: string; title: string; content: unknown };
  return {
    quickAccessMenuClasses: undefined,
    ScrollPanel: undefined,
    findSP: () => undefined,
    ControllerGlyph: undefined,
    GLYPH_BUTTON_B: 1,
    Tabs: ({ tabs, activeTab, onShowTab }: { tabs: TabShape[]; activeTab: string; onShowTab: (id: string) => void }) =>
      ce(
        "div",
        { "data-testid": "steam-tabs" },
        ...tabs.map((tab) =>
          ce("button", { key: tab.id, "data-testid": `tab-${tab.id}`, onClick: () => onShowTab(tab.id) }, tab.title),
        ),
        ce("div", { key: "content" }, tabs.find((tab) => tab.id === activeTab)?.content as never),
      ),
  };
});

const flushAsync = () =>
  act(async () => {
    for (let i = 0; i < 6; i++) await Promise.resolve();
  });

function settings(): PluginSettings {
  return {
    romm_url: "",
    has_token: true,
    steam_input_mode: "default",
    sgdb_api_key_masked: "",
    log_level: "warn",
    romm_allow_insecure_ssl: false,
  };
}

describe("LibraryPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.getPlatforms).mockResolvedValue({ success: true, platforms: [] });
    vi.mocked(backend.getFirmwareStatus).mockResolvedValue({ success: true, platforms: [] });
    vi.mocked(backend.getRegistryPlatforms).mockResolvedValue({ platforms: [] });
    vi.mocked(backend.getCollections).mockResolvedValue({ success: true, collections: [] });
    vi.mocked(backend.getSettings).mockResolvedValue(settings());
  });

  const showTab = async (container: HTMLElement, id: string) => {
    fireEvent.click(container.querySelector(`[data-testid="tab-${id}"]`) as HTMLElement);
    await flushAsync();
  };

  it("renders as a wide page titled Library with a Back row", async () => {
    const onBack = vi.fn();
    const { getByText } = render(<LibraryPage onBack={onBack} />);
    await flushAsync();
    expect(getByText("Library")).toBeTruthy();
    fireEvent.click(getByText("‹ Back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("opens on the Platforms tab and leaves the collections read until it is asked for", async () => {
    render(<LibraryPage onBack={vi.fn()} />);
    await flushAsync();
    expect(vi.mocked(backend.getPlatforms)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.getCollections)).not.toHaveBeenCalled();
    expect(vi.mocked(backend.getSettings)).not.toHaveBeenCalled();
  });

  it("asks for the collections and the owner switch's setting when Collections is entered", async () => {
    const { container } = render(<LibraryPage onBack={vi.fn()} />);
    await flushAsync();
    await showTab(container, "collections");
    expect(vi.mocked(backend.getCollections)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.getSettings)).toHaveBeenCalledTimes(1);
  });

  it("does not ask again on the way back once both reads have answered", async () => {
    // What is held is kept, with its frozen order, for as long as the page is
    // open; only a read that failed is asked again (CollectionsTab.test.tsx).
    const { container } = render(<LibraryPage onBack={vi.fn()} />);
    await flushAsync();
    await showTab(container, "collections");
    await showTab(container, "platforms");
    await showTab(container, "collections");
    expect(vi.mocked(backend.getCollections)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.getSettings)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.getPlatforms)).toHaveBeenCalledTimes(1);
  });

  describe("the Platforms list line on entering the tab again", () => {
    const REFUSED = { success: false, reason: "blocked_by_migration", message: "Migration pending" };
    const listStatus = (c: HTMLElement) => c.querySelector('[data-testid="status-list"]')?.textContent ?? null;
    const toggle = (c: HTMLElement) => c.querySelector<HTMLInputElement>('[data-testid="toggle-input"]')!;

    beforeEach(() => {
      vi.mocked(backend.getPlatforms).mockResolvedValue({
        success: true,
        platforms: [{ id: 1, name: "Game Boy Advance", slug: "gba", rom_count: 12, sync_enabled: false }],
      });
      vi.mocked(backend.getPlatformFirmwareStatus).mockResolvedValue({ success: true, platform: null });
      vi.mocked(backend.getSystemCoreInfo).mockRejectedValue(new Error("net"));
      vi.mocked(backend.countPlatformSaves).mockResolvedValue({ count: 0 });
    });

    it("is gone once Collections is entered and Platforms entered again", async () => {
      vi.mocked(backend.savePlatformSync).mockResolvedValueOnce(REFUSED);
      const { container } = render(<LibraryPage onBack={vi.fn()} />);
      await flushAsync();
      fireEvent.click(toggle(container));
      await flushAsync();
      expect(listStatus(container)).toBe("Migration pending");

      await showTab(container, "collections");
      await showTab(container, "platforms");

      expect(listStatus(container)).toBeNull();
    });

    it("takes no refusal issued before leaving, while the switch still goes back to what is stored", async () => {
      let answer: (value: typeof REFUSED) => void = () => {};
      vi.mocked(backend.savePlatformSync).mockReturnValueOnce(
        new Promise((r) => {
          answer = r;
        }),
      );
      const { container } = render(<LibraryPage onBack={vi.fn()} />);
      await flushAsync();
      fireEvent.click(toggle(container));
      await flushAsync();
      expect(toggle(container).checked).toBe(true);

      await showTab(container, "collections");
      await showTab(container, "platforms");
      await act(async () => {
        answer(REFUSED);
        for (let i = 0; i < 6; i++) await Promise.resolve();
      });

      expect(listStatus(container)).toBeNull();
      expect(toggle(container).checked).toBe(false);
    });
  });
});
