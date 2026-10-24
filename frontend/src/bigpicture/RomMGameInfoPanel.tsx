/**
 * RomMGameInfoPanel — the RomM panel injected below the PlaySection on game
 * detail pages.
 *
 * The shell around the GAME INFO / ACHIEVEMENTS / SAVES / BIOS / ARTWORK panes:
 * it owns the panel's ROM identity, its event lane, the tab selection, what the
 * ACHIEVEMENTS and BIOS panes hold across a tab switch, and the whole-panel
 * replacements (stranded panel, version mismatch, corrupt-settings
 * reset, pending RetroDECK migration) that pre-empt all of them.
 *
 * The panel body itself takes no ROM-level actions — download, play, uninstall,
 * version and core selection live in the RomMPlaySection gear menu. The panes
 * below it are not all passive: the SAVES pane switches slots and rolls back
 * versions, and the BIOS pane downloads, replaces and deletes BIOS files for the
 * game's platform.
 *
 * CSS classes prefixed with `romm-panel-` are injected separately by styleInjector.
 */

import { useState, useEffect, useRef, useCallback, FC } from "react";
import { useAchievements } from "../utils/achievements";
import { useBiosTab } from "./BiosTab";
import { GamePageTabs } from "./GamePageTabs";
import { bindRomInState, loadData, openingTab, type PanelReadSeqs, type PanelState } from "./panelState";
import { announceOwnBiosChange, rereadBiosAnswer, wirePanelEvents, type PanelEventContext } from "./panelEvents";
import { useSaveSlotsLoad } from "./panelSlotsLoad";
import { buildPanelTabs } from "./panelTabContent";
import { useMigrationStatus } from "../utils/migrationStore";
import { useMigrationRefresh } from "../utils/migrationRefresh";
import { useSettingsResetState } from "../utils/settingsResetStore";
import { VersionErrorCard } from "./VersionErrorCard";
import { useVersionError } from "../utils/connectionState";
import { MigrationBlockedCard } from "./MigrationBlockedCard";
import { SettingsResetCard } from "./SettingsResetCard";
import { StrandedPanelCard } from "./StrandedPanelCard";
import { useStrandedAnswer } from "../utils/strandedPanelStore";
import { detach } from "../utils/detach";
import { refreshBiosStatus } from "../utils/gameDetailStore";
import { announceBiosChange } from "./bios/useBiosActions";

interface RomMGameInfoPanelProps {
  appId: number;
}

export const RomMGameInfoPanel: FC<RomMGameInfoPanelProps> = ({ appId }) => {
  // Subscribe to version error — re-renders when global state changes
  const versionError = useVersionError();

  const [state, setState] = useState<PanelState>({
    loading: true,
    romId: null,
    romName: "",
    platformName: "",
    installed: false,
    installedRom: null,
    metadata: null,
    coverBase64: null,
    biosStatus: null,
    biosLevel: null,
    biosReadFailed: false,
    coreInfo: null,
    saveSyncEnabled: false,
    saveStatus: null,
    conflicts: [],
    error: false,
    activeTab: openingTab(appId),
    raId: null,
    slotConfirmed: false,
    activeSlot: "default",
    activeSlotKnown: false,
    availableSlots: [],
    lastKnownSlots: null,
    slotsLoading: false,
    regions: [],
    languages: [],
  });
  const romIdRef = useRef<number | null>(null);
  // Tracks the panel's own platform so the broadcast `bios` data-changed
  // handler can reject events for other platforms without a stale closure
  // (mirrors romIdRef). bios events fan out to every mounted panel (#1082).
  const platformSlugRef = useRef<string>("");
  // The same platform for the render, which the BIOS tab acts on.
  const [platformSlug, setPlatformSlug] = useState("");
  // Load-once gate for the lazy SAVES tab data; the rule it enforces is stated
  // at `panelSlotsLoad.ts`. It lives here because a version switch resets it.
  const slotsLoadedRef = useRef(false);
  // Read sequences ordering two answers about the same ROM; the rule they
  // enforce is stated at `takeReadTicket`. They live here because the loads, the
  // event lane and the lazy slots lane take tickets from the same counters.
  const readSeqs = useRef<PanelReadSeqs>({ detail: 0, saveStatus: 0, slots: 0, slotTracking: 0, bios: 0 });
  // The event lane's context for the appId shown, which a press on the BIOS tab
  // reads the tab's answer through — bound and ordered like the lane's own reads.
  const eventContext = useRef<PanelEventContext | null>(null);
  const announcingOwnBiosChange = useRef(false);
  // After a press on the BIOS tab: the tab's answer and the play row's.
  const rereadBios = useCallback(async () => {
    const ctx = eventContext.current;
    await Promise.all([ctx === null ? undefined : rereadBiosAnswer(ctx), refreshBiosStatus(appId)]);
  }, [appId]);
  const announceBios = useCallback((slug: string) => {
    const ctx = eventContext.current;
    if (ctx === null) announceBiosChange(slug);
    else announceOwnBiosChange(ctx, slug);
  }, []);
  const migration = useMigrationStatus();
  const settingsReset = useSettingsResetState();
  const stranded = useStrandedAnswer() !== null;

  useMigrationRefresh(appId);

  useEffect(() => {
    let cancelled = false;

    detach(loadData(appId, () => cancelled, romIdRef, platformSlugRef, readSeqs, setState, setPlatformSlug));

    const ctx: PanelEventContext = {
      appId,
      cancelled: () => cancelled,
      romIdRef,
      platformSlugRef,
      slotsLoadedRef,
      readSeqs,
      setState,
      announcingOwnBiosChange,
    };
    eventContext.current = ctx;
    const unwire = wirePanelEvents(ctx);

    return () => {
      cancelled = true;
      if (eventContext.current === ctx) eventContext.current = null;
      unwire();
    };
  }, [appId]);

  useSaveSlotsLoad(state, slotsLoadedRef, readSeqs, setState);
  const achievements = useAchievements(state.romId, state.raId, state.activeTab === "achievements");
  const bios = useBiosTab({
    biosStatus: state.biosStatus,
    platformName: state.platformName,
    platformSlug,
    romId: state.romId,
    onReread: rereadBios,
    onAnnounce: announceBios,
  });

  // --- Stranded panel — nothing below the play row can reach the backend, so
  // nothing is offered: this outranks every other card, and a page loaded before
  // the strand turns to it as soon as the answer arrives ---
  if (stranded) {
    return (
      <div data-romm="true">
        <StrandedPanelCard />
      </div>
    );
  }

  // --- Version mismatch — replace entire panel with polished error card ---
  if (versionError) {
    return (
      <div data-romm="true">
        <VersionErrorCard message={versionError} />
      </div>
    );
  }

  // --- Corrupt-settings reset — surface the reason instead of a bare "offline" ---
  if (settingsReset.pending) {
    return (
      <div data-romm="true">
        <SettingsResetCard backedUpTo={settingsReset.backedUpTo} compact={true} />
      </div>
    );
  }

  // --- Pending RetroDECK migration — block the page until resolved ---
  if (migration.pending) {
    return (
      <div data-romm="true">
        <MigrationBlockedCard />
      </div>
    );
  }

  // --- Loading state ---
  // Use minHeight so Steam's scroll container allocates enough space
  // before async data loads and expands the panel.
  if (state.loading) {
    return (
      <div data-romm="true" className="romm-panel-container" style={{ minHeight: "500px" }}>
        <div className="romm-panel-loading">Loading...</div>
      </div>
    );
  }

  // --- Error / not found state ---
  if (state.error || !state.romId) {
    return null;
  }

  return (
    <div data-romm="true">
      <GamePageTabs
        tabs={buildPanelTabs({ appId, binding: bindRomInState(state.romId, setState), state, achievements, bios })}
        activeTab={state.activeTab}
        onShowTab={(tabId) => setState((prev) => ({ ...prev, activeTab: tabId }))}
      />
    </div>
  );
};
