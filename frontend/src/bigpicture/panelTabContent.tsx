/**
 * The game detail panel's tabs, each with its pane.
 *
 * Every pane is an element here, and only the shown tab's is mounted
 * (`docs/architecture/qam-panel.md`, "The game page's tabs"). That is
 * load-bearing for SAVES and ARTWORK: both fetch on mount (ARTWORK asks
 * SteamGridDB for the game's name and hashes the slots' images), so mounting
 * them for a tab nobody opened would fire those requests on every game page.
 */

import { Fragment, type ReactElement } from "react";
import type { Tab } from "@decky/ui";
import { FaExclamationTriangle } from "react-icons/fa";
import { AchievementsTab } from "./AchievementsTab";
import type { AchievementsLoad } from "../utils/achievements";
import { ArtworkTab } from "./ArtworkTab";
import { BiosTab, type BiosTabState } from "./BiosTab";
import { GameInfoTab } from "./GameInfoTab";
import { SavesTab } from "./SavesTab";
import { SlotSetupWizard } from "./SlotSetupWizard";
import { SaveSyncRefusal } from "./saves/SaveSyncRefusal";
import type { PanelState, RomBinding } from "./panelState";
import { announceSaveSync } from "../utils/saveSyncEvents";
import { noSaveLine, notDownloadedStatement, refusalHint, refusesDownloadedGame } from "../utils/saveSyncStatement";

interface TabContentContext {
  readonly appId: number;
  readonly binding: RomBinding;
  readonly state: PanelState;
  readonly achievements: AchievementsLoad;
  readonly bios: BiosTabState;
}

/** The mark beside the SAVES tab's title of a game whose saves cannot sync (#2149 D1). */
function savesWarningMark(): ReactElement {
  return (
    <FaExclamationTriangle
      aria-hidden="true"
      data-testid="saves-tab-warning"
      size={12}
      style={{ color: "#ffaa00", marginLeft: "6px", flexShrink: 0 }}
    />
  );
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
  if (state.saveSyncEnabled) {
    tabs.push({
      id: "saves",
      title: "SAVES",
      content: savesPane(ctx),
      ...(refusesDownloadedGame(state.saveSyncAnswer) ? { renderTabAddon: savesWarningMark } : {}),
    });
  }
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

/**
 * The SAVES pane: a game whose saves cannot sync says why and offers no slots, set up or not (#2149 D5); any other
 * game shows its slots, or the setup while none is set up, under what a game that is not downloaded reads (D4).
 * While no answer is held the slots or the setup show as they would for a game that syncs.
 */
function savesPane(ctx: TabContentContext): ReactElement {
  const answer = ctx.state.saveSyncAnswer;
  if (answer && refusesDownloadedGame(answer)) {
    return <SaveSyncRefusal key="saves-refusal" message={answer.message ?? ""} reason={answer.reason} />;
  }
  const notDownloaded = notDownloadedStatement(answer);
  const hint = notDownloaded?.refusal == null ? null : refusalHint(answer?.reason);
  // Keyed children of one Fragment, so the line arriving after the pane is up
  // does not remount the setup, which asks RomM again on every mount.
  return (
    <Fragment>
      {notDownloaded === null ? null : (
        <div key="not-downloaded" data-testid="saves-not-downloaded" style={{ padding: "0 0 12px" }}>
          <div className="romm-panel-muted" style={{ fontSize: "12px" }}>
            {notDownloaded.line}
          </div>
          {notDownloaded.refusal === null ? null : (
            <div className="romm-panel-muted" style={{ fontSize: "12px", marginTop: "4px" }}>
              {notDownloaded.refusal}
            </div>
          )}
          {hint === null ? null : (
            <div className="romm-panel-muted" style={{ fontSize: "12px", marginTop: "6px" }}>
              {hint}
            </div>
          )}
        </div>
      )}
      {slotsPane(ctx)}
    </Fragment>
  );
}

function slotsPane({ appId, binding, state }: TabContentContext): ReactElement {
  const romId = binding.romId;
  if (!state.slotConfirmed) {
    return (
      <SlotSetupWizard
        key="saves-setup"
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
      key="saves-slots"
      appId={appId}
      romId={romId}
      noSaveLine={noSaveLine(state.saveSyncAnswer)}
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
