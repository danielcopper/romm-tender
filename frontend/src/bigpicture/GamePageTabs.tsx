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

/**
 * How far below the tab row a pane's content starts. A section or a connecting
 * line that opens a pane gives up its own top padding (`.romm-game-page-pane`
 * in `utils/styleInjector.ts`), and the panes' other opening blocks carry none.
 */
const PANE_TOP_GAP = "20px";

/** The delay `utils/scrollHelpers.ts` takes to land after Steam's own scroll to a focused element. */
const AFTER_STEAMS_OWN_SCROLL_MS = 50;

interface GamePageTabsProps {
  /** The tabs shown, in order; a tab not shown is not in the list. */
  tabs: Tab[];
  /**
   * The tab to show. Where it names none of `tabs` — ACHIEVEMENTS after a switch
   * to a version without them — the first tab stands in for it before Steam's
   * tabbed page is handed it: that page shows its first tab then, but its L1 and
   * R1 inside a pane step from the id as handed (`steamui/sp.js`), and so would
   * go nowhere or two tabs back.
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

export const GamePageTabs: FC<GamePageTabsProps> = ({ tabs, activeTab: asked, onShowTab }) => {
  const activeTab = tabs.some((tab) => tab.id === asked) ? asked : (tabs[0]?.id ?? asked);
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
  const panes = tabs.map((tab) => ({
    ...tab,
    content: (
      <div className="romm-game-page-pane" style={{ paddingTop: PANE_TOP_GAP }}>
        {tab.content}
      </div>
    ),
  }));

  return (
    <Focusable
      className={`romm-game-page-tabs ${SCROLL_BOUNDARY_CLASS}`}
      onButtonDown={onTrigger}
      onFocus={standAtTop}
      {...{ scrollIntoViewType: NO_TRANSFORM }}
    >
      {Tabs ? (
        <Tabs
          tabs={panes}
          activeTab={activeTab}
          onShowTab={onShowTab}
          autoFocusContents={false}
          onCancelFromTabHeader={focusPlayRow}
          canBeHeaderBackground="on-outer-scroll"
        />
      ) : (
        panes.find((tab) => tab.id === activeTab)?.content
      )}
    </Focusable>
  );
};
