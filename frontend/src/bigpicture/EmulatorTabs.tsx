/**
 * The tab strip over a BIOS table: one tab per emulator the resolver answered
 * for, the launching emulator first (#2190).
 *
 * Each tab carries its own emulator's verdict as a dot and its label, and the
 * launching emulator's says that it launches. Choosing a tab changes which rows
 * the table below shows and nothing else — never which emulator launches; that
 * is the emulator picker's, and the header, the counts and the downloads stay
 * the launching emulator's whichever tab is open.
 *
 * Every tab is a `DialogButton`, so each is a focus stop for the controller, and
 * the strip sits above the table it switches, where focus reaches it first.
 */

import type { FC } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import type { EmulatorTab } from "../types";
import { tabDotColor, tabLabel } from "../utils/biosTabs";

const TAB_STYLE = { width: "auto", minWidth: 0, padding: "4px 10px", fontSize: "12px" } as const;

export const EmulatorTabs: FC<{
  tabs: readonly EmulatorTab[];
  selected: EmulatorTab;
  onSelect: (tab: EmulatorTab) => void;
  padding?: string;
}> = ({ tabs, selected, onSelect, padding = "0" }) => (
  <Focusable
    flow-children="horizontal"
    data-testid="emulator-tabs"
    style={{ display: "flex", flexWrap: "wrap", gap: "6px", padding }}
  >
    {tabs.map((tab) => {
      const isSelected = tab === selected;
      return (
        <DialogButton
          key={`${tab.emulator ?? "launching"}-${tab.launching}`}
          style={{ ...TAB_STYLE, fontWeight: isSelected ? 600 : 400, opacity: isSelected ? 1 : 0.75 }}
          onClick={() => onSelect(tab)}
        >
          <span
            data-testid="emulator-tab"
            aria-selected={isSelected}
            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
          >
            <span
              data-testid="emulator-tab-dot"
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                backgroundColor: tabDotColor(tab.requirements_met),
                flexShrink: 0,
              }}
            />
            {tabLabel(tab)}
            {tab.launching && <span style={{ opacity: 0.7 }}>· launches</span>}
          </span>
        </DialogButton>
      );
    })}
  </Focusable>
);
