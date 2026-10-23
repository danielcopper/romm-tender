import type { FC } from "react";
import type { WideTabsProps } from "../utils/deckyUiInternals";

/**
 * A stand-in for Steam's tabbed page, which is a webpack probe with no answer
 * under happy-dom: a tab button per tab, marked `aria-selected` while it is the
 * active one, and the active tab's content. Steam's own falls back to the first
 * tab where the active id names none, and so does this. It takes no L1 or R1.
 */
export const StubTabs: FC<WideTabsProps> = ({ tabs, activeTab, onShowTab }) => {
  const shown = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];
  return (
    <div data-testid="steam-tabs">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          data-testid={`tab-${tab.id}`}
          aria-selected={tab.id === shown?.id}
          onClick={() => onShowTab(tab.id)}
        >
          {tab.title}
        </button>
      ))}
      <div data-testid="steam-tab-content">{shown?.content}</div>
    </div>
  );
};
