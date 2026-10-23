import type { FC } from "react";
import { Focusable, type GamepadEvent } from "@decky/ui";
import type { WideTabsProps } from "../utils/deckyUiInternals";

/**
 * A stand-in for Steam's tabbed page, which is a webpack probe with no answer
 * under happy-dom: a tab button per tab, marked `aria-selected` while it is the
 * active one, and the active tab's content. Steam's own falls back to the first
 * tab where the active id names none, and so does this. It takes no L1 or R1.
 *
 * B is wired the way Steam's is (`steamui/sp.js`, the tabbed page): the content
 * takes it to put focus on the active tab unless `cancelSkipTabHeader` is
 * passed, the page as a whole hands it to `onCancelFromTabHeader`, and a
 * handler stops it unless it answers `false`.
 */
export const StubTabs: FC<WideTabsProps> = ({
  tabs,
  activeTab,
  onShowTab,
  cancelSkipTabHeader,
  onCancelFromTabHeader,
}) => {
  const shown = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];
  const fromTabRow = onCancelFromTabHeader
    ? {
        onCancelButton: (evt: GamepadEvent) => {
          if (onCancelFromTabHeader() !== false) evt.stopPropagation();
        },
      }
    : {};
  const fromContent = cancelSkipTabHeader
    ? {}
    : {
        onCancelButton: (evt: GamepadEvent) => {
          const page = (evt.currentTarget as HTMLElement).closest(".stub-tabbed-page");
          page?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus();
          evt.stopPropagation();
        },
      };
  return (
    <div data-testid="steam-tabs">
      <Focusable className="stub-tabbed-page" {...fromTabRow}>
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
        <Focusable {...fromContent}>
          <div data-testid="steam-tab-content">{shown?.content}</div>
        </Focusable>
      </Focusable>
    </div>
  );
};
