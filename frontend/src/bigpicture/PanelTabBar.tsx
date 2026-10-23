/**
 * PanelTabBar — the tab strip at the top of the RomM game detail panel.
 *
 * ACHIEVEMENTS and SAVES exist only when their pane has something to show, which
 * is why the panel passes their presence rather than the state it is derived
 * from. BIOS is there for every game from the moment the page opens: its answer
 * is read after the page stands, and the tab says so while it waits.
 *
 * CSS classes prefixed with `romm-tab` are injected separately by styleInjector.
 */

import { FC } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import { useTriggerHandler } from "./emulatorStepping";

interface PanelTabBarProps {
  activeTab: string;
  hasAchievements: boolean;
  hasSaves: boolean;
  onSelect: (tabId: string) => void;
}

export const PanelTabBar: FC<PanelTabBarProps> = ({ activeTab, hasAchievements, hasSaves, onSelect }) => {
  const onTrigger = useTriggerHandler();
  const tabs: { id: string; label: string; visible: boolean }[] = [
    { id: "info", label: "GAME INFO", visible: true },
    { id: "achievements", label: "ACHIEVEMENTS", visible: hasAchievements },
    { id: "saves", label: "SAVES", visible: hasSaves },
    { id: "bios", label: "BIOS", visible: true },
    { id: "artwork", label: "ARTWORK", visible: true },
  ];

  return (
    <Focusable className="romm-tab-bar" flow-children="right" data-romm="true" onButtonDown={onTrigger}>
      {tabs
        .filter((t) => t.visible)
        .map((t) => (
          <DialogButton
            key={`tab-${t.id}`}
            className={`romm-tab ${activeTab === t.id ? "romm-tab-active" : ""}`}
            onClick={() => onSelect(t.id)}
            style={{
              background: "transparent",
              border: "none",
              borderBottom: activeTab === t.id ? "2px solid #1a9fff" : "2px solid transparent",
              padding: "10px 16px",
              minWidth: "auto",
              width: "auto",
            }}
            noFocusRing={false}
          >
            {t.label}
          </DialogButton>
        ))}
    </Focusable>
  );
};
