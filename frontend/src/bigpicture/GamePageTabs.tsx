/**
 * The game page's tabs: Steam's own tabbed page, as Steam's own game page has
 * it, with the scroll that brings the tabs to the top of the game page as focus
 * enters them, B on the tab row putting focus on the play row, and one handler
 * around it through which L2 and R2 reach the BIOS tab's emulator selector from
 * the tab row and from every pane.
 *
 * What is Steam's here, what is Tender's, and why: `docs/architecture/qam-panel.md`,
 * "The game page's tabs".
 */

import { useCallback, useLayoutEffect, useRef, type FC, type FocusEvent } from "react";
import { Focusable, type Tab } from "@decky/ui";
import { Tabs } from "../utils/deckyUiInternals";
import { SCROLL_BOUNDARY_CLASS } from "../utils/scrollBoundary";
import { findOutermostScrollParent, offsetWithinScroller } from "../utils/scrollHelpers";
import { useTriggerHandler } from "./emulatorStepping";
import { steppedTab, useFocusPlayRow, useStepTabsOnBumpers } from "./tabStepping";

/**
 * How far below the top of the scrolling game page Steam's own game page stops
 * its tabbed page when focus enters it: its `headerPadding`, `"40px"` in the
 * app details' class module (`steamui/sp.js`).
 */
export const STEAM_HEADER_PADDING_PX = 40;

/**
 * Steam's `EScrollIntoViewType.NoTransform` (`steamui/sp.js`; `@decky/ui`
 * declares the enum without exporting it): on a navigation node, Steam brings a
 * focused element inside it into view by its laid-out position rather than with
 * the browser's own `scrollIntoView`. Why the tabs need it:
 * `docs/architecture/qam-panel.md`, "The game page's tabs".
 */
const NO_TRANSFORM = 1;

/** The delay `utils/scrollHelpers.ts` takes to land after Steam's own scroll to a focused element. */
const AFTER_STEAMS_OWN_SCROLL_MS = 50;

interface GamePageTabsProps {
  /** The tabs shown, in order; a tab not shown is not in the list. */
  tabs: Tab[];
  /**
   * Where it names none of `tabs` — SAVES once save sync is off — Steam's tabbed
   * page shows the first tab (`steamui/sp.js`), and the play row's L1 and R1
   * step from there.
   */
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
  const focusPlayRow = useFocusPlayRow();
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
    <Focusable
      className={`romm-game-page-tabs ${SCROLL_BOUNDARY_CLASS}`}
      onButtonDown={onTrigger}
      onFocus={standAtTop}
      {...{ scrollIntoViewType: NO_TRANSFORM }}
    >
      {Tabs ? (
        // B steps back as on Steam's own game page: the pane hands it to the
        // tab row, and the tab row to the play row.
        <Tabs
          tabs={tabs}
          activeTab={activeTab}
          onShowTab={onShowTab}
          autoFocusContents={false}
          onCancelFromTabHeader={focusPlayRow}
          canBeHeaderBackground="on-outer-scroll"
        />
      ) : (
        tabs.find((tab) => tab.id === activeTab)?.content
      )}
    </Focusable>
  );
};
