// What the game page's tabs add around Steam's tabbed page: the props they hand
// it, and the scroll that brings them to the top of the game page when focus
// enters them. Which tabs a game shows is the panel's, pinned beside it
// (RomMGameInfoPanel.test.tsx).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import type { FC } from "react";
import { StubTabs } from "../test-utils/steamTabs";
import type { WideTabsProps } from "../utils/deckyUiInternals";
import { GamePageTabs, STEAM_HEADER_PADDING_PX } from "./GamePageTabs";

const seen: WideTabsProps[] = [];
const RecordingTabs: FC<WideTabsProps> = (props) => {
  seen.push(props);
  return <StubTabs {...props} />;
};
// Read when the tabs render, so a test can take Steam's tabbed page away.
let tabsProbe: FC<WideTabsProps> | undefined = RecordingTabs;

vi.mock("../utils/deckyUiInternals", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/deckyUiInternals")>()),
  get Tabs() {
    return tabsProbe;
  },
}));

const tabs = [
  { id: "info", title: "GAME INFO", content: <button type="button">game info</button> },
  { id: "bios", title: "BIOS", content: <button type="button">bios pane</button> },
];

describe("GamePageTabs", () => {
  beforeEach(() => {
    seen.length = 0;
    tabsProbe = RecordingTabs;
  });

  it("hands Steam's tabbed page a screen-tall area, B left alone and entry focus left where it is", () => {
    render(<GamePageTabs tabs={tabs} activeTab="info" onShowTab={vi.fn()} />);
    const props = seen[seen.length - 1];
    expect(props?.canBeHeaderBackground).toBe("on-outer-scroll");
    expect(props?.cancelSkipTabHeader).toBe(true);
    expect(props?.autoFocusContents).toBe(false);
    expect(props?.tabs.map((tab) => tab.id)).toEqual(["info", "bios"]);
    expect(props?.activeTab).toBe("info");
  });

  it("shows the active tab's pane alone where Steam's tabbed page was not found", () => {
    tabsProbe = undefined;
    const { container } = render(<GamePageTabs tabs={tabs} activeTab="bios" onShowTab={vi.fn()} />);
    expect(container.textContent).toBe("bios pane");
  });

  describe("scrolling the game page", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    /** The game page as Steam scrolls it: a play row's stand-in above the tabs, the tabs 300 px down. */
    const gamePage = () => {
      const utils = render(
        <div data-testid="game-page" style={{ overflowY: "auto" }}>
          <button type="button">play</button>
          <GamePageTabs tabs={tabs} activeTab="info" onShowTab={vi.fn()} />
        </div>,
      );
      const page = utils.getByTestId("game-page");
      Object.defineProperty(page, "scrollHeight", { configurable: true, value: 2000 });
      Object.defineProperty(page, "clientHeight", { configurable: true, value: 800 });
      const scrollTo = vi.fn();
      page.scrollTo = scrollTo;
      const tabsEl = page.querySelector(".romm-game-page-tabs") as HTMLElement;
      tabsEl.getBoundingClientRect = () => ({ top: 300 }) as DOMRect;
      page.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
      return { ...utils, scrollTo };
    };

    it("scrolls the game page so the tabs stand at its top when focus comes into them from outside", () => {
      const { getByText, scrollTo } = gamePage();
      fireEvent.focusIn(getByText("game info"), { relatedTarget: getByText("play") });
      act(() => {
        vi.advanceTimersByTime(60);
      });
      expect(scrollTo).toHaveBeenCalledWith({ top: 300 - STEAM_HEADER_PADDING_PX, behavior: "smooth" });
    });

    it("leaves the game page where it is while focus moves inside the tabs", () => {
      const { getByText, scrollTo } = gamePage();
      fireEvent.focusIn(getByText("game info"), { relatedTarget: getByText("GAME INFO") });
      act(() => {
        vi.advanceTimersByTime(60);
      });
      expect(scrollTo).not.toHaveBeenCalled();
    });
  });
});
