/**
 * The game detail panel's tabs, each with its pane.
 *
 * Every pane is an element here, and only the shown tab's is mounted
 * (`docs/architecture/qam-panel.md`, "The game page's tabs"). That is
 * load-bearing for SAVES and ARTWORK: both fetch on mount (ARTWORK asks
 * SteamGridDB for the game's name and hashes the slots' images), so mounting
 * them for a tab nobody opened would fire those requests on every game page.
 */

import type { ReactElement } from "react";
import type { Tab } from "@decky/ui";
import { AchievementsTab } from "./AchievementsTab";
import type { AchievementsLoad } from "../utils/achievements";
import { ArtworkTab } from "./ArtworkTab";
import { BiosTab, type BiosTabState } from "./BiosTab";
import { GameInfoTab } from "./GameInfoTab";
import { SavesTab } from "./SavesTab";
import { SlotSetupWizard } from "./SlotSetupWizard";
import type { PanelState, RomBinding } from "./panelState";
import { announceSaveSync } from "../utils/saveSyncEvents";

interface TabContentContext {
  readonly appId: number;
  readonly binding: RomBinding;
  readonly state: PanelState;
  readonly achievements: AchievementsLoad;
  readonly bios: BiosTabState;
}

/**
 * The tabs shown, in their fixed order. ACHIEVEMENTS is there for a game
 * RetroAchievements knows (an `ra_id`), SAVES while save sync is on; BIOS is
 * there for every game from the moment the page opens, since its answer is read
 * after the page stands and the tab says so while it waits.
 */
export function buildPanelTabs(ctx: TabContentContext): Tab[] {
  const { state } = ctx;
  const tabs: Tab[] = [{ id: "info", title: "GAME INFO", content: infoPane(ctx) }];
  if (state.raId) {
    tabs.push({ id: "achievements", title: "ACHIEVEMENTS", content: <AchievementsTab state={ctx.achievements} /> });
  }
  if (state.saveSyncEnabled) tabs.push({ id: "saves", title: "SAVES", content: savesPane(ctx) });
  tabs.push(
    {
      id: "bios",
      title: "BIOS",
      content: (
        <BiosTab
          biosStatus={state.biosStatus}
          biosLevel={state.biosLevel}
          readFailed={state.biosReadFailed}
          coreInfo={state.coreInfo}
          platformName={state.platformName}
          tabState={ctx.bios}
        />
      ),
    },
    {
      id: "artwork",
      title: "ARTWORK",
      // Keyed by the ROM, so a version switch builds the pane for the version now showing.
      content: (
        <ArtworkTab
          key={`tab-artwork-${ctx.binding.romId}`}
          appId={ctx.appId}
          romId={ctx.binding.romId}
          romName={state.romName}
        />
      ),
    },
  );
  return tabs;
}

function infoPane({ appId, binding, state }: TabContentContext): ReactElement {
  return (
    <GameInfoTab
      key="tab-info"
      romName={state.romName}
      regions={state.regions}
      languages={state.languages}
      metadata={state.metadata}
      platformName={state.platformName}
      coverBase64={state.coverBase64}
      appId={appId}
      romId={binding.romId}
      installed={state.installed}
      installedRom={state.installedRom}
    />
  );
}

function savesPane({ appId, binding, state }: TabContentContext): ReactElement {
  const romId = binding.romId;
  if (!state.slotConfirmed) {
    return (
      <SlotSetupWizard
        romId={romId}
        onComplete={() => {
          // Bound for the same reason the slot switch below is: the wizard calls
          // this after awaiting the confirm, and it is unkeyed too. `slotConfirmed`
          // is the gate deciding which of the two panes this function builds, so a
          // stale `true` puts a saves tab in front of a version whose tracking is
          // not configured (#1754).
          binding.write((prev) => ({ ...prev, slotConfirmed: true }));
          announceSaveSync(romId);
        }}
      />
    );
  }

  return (
    <SavesTab
      appId={appId}
      romId={romId}
      saveStatus={state.saveStatus}
      conflicts={state.conflicts}
      activeSlot={state.activeSlot}
      activeSlotKnown={state.activeSlotKnown}
      availableSlots={state.availableSlots}
      lastKnownSlots={state.lastKnownSlots}
      slotsLoading={state.slotsLoading}
      onSlotSwitched={(newSlot, newStatus) => {
        // `SavesTab` calls this after awaiting the switch, and gets no React key
        // of its own: it survives the version switch that re-points the panel to
        // another rom_id under the same appId, still holding the callback it was
        // rendered with. Only the ROM this pane was built for separates that
        // answer from one about the version now showing (#1754).
        binding.write((prev) => ({
          ...prev,
          activeSlot: newSlot === "" ? null : newSlot,
          // A completed switch is an answer about the active slot in its own
          // right — the announce below re-reads the list, but the tab must not
          // fall back to "unknown" for the round trip in between (#1747).
          activeSlotKnown: true,
          saveStatus: newStatus,
          conflicts: newStatus.conflicts ?? [],
        }));
        announceSaveSync(romId);
      }}
    />
  );
}
