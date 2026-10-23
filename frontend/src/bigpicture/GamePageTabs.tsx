/**
 * The game page's tabs: Steam's own tabbed page, as on Steam's own game page.
 *
 * L1 and R1, the wrap-around, the bumper glyphs, a tab switching as soon as
 * focus lands on it, and each tab keeping where focus stood are all Steam's.
 * What this adds is the part of a game page Steam's game page does around its
 * tabbed page, and the two buttons Tender's section takes:
 *
 * - **Scrolling.** The tab area is one screen tall and a tab's pane scrolls
 *   inside it. Focus entering the tabs scrolls the game page so the tab row
 *   stands at the top; the play row scrolls it back (`RomMPlaySection`).
 * - **L2 and R2** step the BIOS tab's emulator selector from the tab row and
 *   from every pane: Steam's tab row and content pass the triggers on, so one
 *   handler around the tabbed page reaches both.
 * - **L1 and R1 from the play row**, which is outside the tabbed page:
 *   `tabStepping.tsx`.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, "The game page's
 * tabs".
 */

import { useCallback, useLayoutEffect, useRef, type FC, type FocusEvent } from "react";
import { Focusable, type Tab } from "@decky/ui";
import { Tabs } from "../utils/deckyUiInternals";
import { findOutermostScrollParent, offsetWithinScroller } from "../utils/scrollHelpers";
import { useTriggerHandler } from "./emulatorStepping";
import { steppedTab, useStepTabsOnBumpers } from "./tabStepping";

/**
 * How far below the top of the scrolling game page Steam's own game page stops
 * its tabbed page when focus enters it: its `headerPadding`, `"40px"` in the
 * app details' class module (`steamui/sp.js`).
 */
export const STEAM_HEADER_PADDING_PX = 40;

/** The delay `utils/scrollHelpers.ts` takes to land after Steam's own scroll to a focused element. */
const AFTER_STEAMS_OWN_SCROLL_MS = 50;

interface GamePageTabsProps {
  /** The tabs shown, in order; a tab not shown is not in the list. */
  tabs: Tab[];
  /** One of `tabs`' ids. */
  activeTab: string;
  onShowTab: (tabId: string) => void;
}

/** Scrolls the game page so the tabs stand at its top, where focus came into them from outside. */
function standAtTop(e: FocusEvent<HTMLDivElement>): void {
  const tabs = e.currentTarget;
  if (tabs.contains(e.relatedTarget as Node | null)) return;
  setTimeout(() => {
    const scroller = findOutermostScrollParent(tabs);
    scroller?.scrollTo({ top: offsetWithinScroller(tabs, scroller) - STEAM_HEADER_PADDING_PX, behavior: "smooth" });
  }, AFTER_STEAMS_OWN_SCROLL_MS);
}

export const GamePageTabs: FC<GamePageTabsProps> = ({ tabs, activeTab, onShowTab }) => {
  const onTrigger = useTriggerHandler();
  const latest = useRef({ tabs, activeTab, onShowTab });
  useLayoutEffect(() => {
    latest.current = { tabs, activeTab, onShowTab };
  });
  const step = useCallback((by: number) => {
    const { tabs: shown, activeTab: active, onShowTab: show } = latest.current;
    const next = steppedTab(
      shown.map((tab) => tab.id),
      active,
      by,
    );
    if (next !== undefined) show(next);
  }, []);
  useStepTabsOnBumpers(step);

  return (
    <Focusable className="romm-game-page-tabs" onButtonDown={onTrigger} onFocus={standAtTop}>
      {Tabs ? (
        // `cancelSkipTabHeader`: B does on a game page what it did before the
        // tabs were Steam's, so the pane leaves it alone.
        <Tabs
          tabs={tabs}
          activeTab={activeTab}
          onShowTab={onShowTab}
          autoFocusContents={false}
          cancelSkipTabHeader
          canBeHeaderBackground="on-outer-scroll"
        />
      ) : (
        tabs.find((tab) => tab.id === activeTab)?.content
      )}
    </Focusable>
  );
};
