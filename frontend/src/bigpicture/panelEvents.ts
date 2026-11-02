/**
 * The game-detail panel's event lane: the listeners `RomMGameInfoPanel`
 * subscribes for the ROM it currently shows, and the per-event-type handlers
 * behind the `romm_data_changed` dispatch.
 *
 * Every handler reads the panel's live identity (`romIdRef` / `platformSlugRef`)
 * rather than a value captured at wiring time — these listeners outlive many
 * answers, and a version switch re-points the identity without re-subscribing
 * them.
 */

import { addEventListener, removeEventListener } from "../api/host";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import {
  invalidateCachedGameDetail,
  getCachedGameDetail,
  getRomMetadata,
  getBiosStatus,
  getPlatformCoreInfo,
  getSaveStatus,
  isEndpointFailure,
  debugLog,
  type BiosAnswer,
} from "../api/backend";
import type { CoreInfo, RomMetadata, SaveStatus, SyncConflict, DownloadCompleteEvent } from "../types";
import type { RommDataChangedDetail } from "../types/events";
import { detach } from "../utils/detach";
import { announceBiosChange } from "./bios/useBiosActions";
import {
  bindRom,
  biosFieldsFromCache,
  foldBiosRead,
  refreshBiosIfStale,
  refreshPanelCoreInfo,
  refreshSaveSyncAnswer,
  refreshCoverArtInBackground,
  refreshInstalledRomInBackground,
  refreshSlotState,
  saveStatusFromCache,
  takeReadTicket,
  type PanelReadSeqs,
  type PanelState,
  type RomBinding,
} from "./panelState";

/** Everything the event lane reaches into on the panel that wired it.
 *
 *  `cancelled` is asked when an answer LANDS, not when the lane was wired — a
 *  captured boolean would be read long after the panel that set it is gone. */
export interface PanelEventContext {
  readonly appId: number;
  readonly cancelled: () => boolean;
  readonly romIdRef: MutableRefObject<number | null>;
  /** The panel's own platform, so the broadcast `bios` handler can reject events
   *  for other platforms. bios events fan out to every mounted panel (#1082). */
  readonly platformSlugRef: MutableRefObject<string>;
  /** Load-once gate for the lazy SAVES tab data — a version switch releases it. */
  readonly slotsLoadedRef: MutableRefObject<boolean>;
  /** Orders two answers about the same ROM, which the rom binding admits — see
   *  `takeReadTicket`. Shared with the panel's loads and its slots lane, which
   *  race these handlers for the same fields. */
  readonly readSeqs: MutableRefObject<PanelReadSeqs>;
  readonly setState: Dispatch<SetStateAction<PanelState>>;
  /** Set for exactly the dispatch of a `bios` event this panel's own BIOS tab
   *  sends — see {@link announceOwnBiosChange}. */
  readonly announcingOwnBiosChange: MutableRefObject<boolean>;
}

/** Bind a read this panel is issuing now for the ROM it currently shows — see
 *  `bindRom` for what the binding refuses and why. */
function bindCurrentRom(ctx: PanelEventContext, romId: number): RomBinding {
  return bindRom(romId, ctx.romIdRef, ctx.cancelled, ctx.setState);
}

async function handleSaveSyncSettingsChange(
  ctx: PanelEventContext,
  detail: Extract<RommDataChangedDetail, { type: "save_sync_settings" }>,
): Promise<void> {
  const enabled = detail.save_sync_enabled;
  if (!enabled) {
    // Switching save sync off is itself the newest word on it, so it takes the
    // sequence's next ticket: a status read an earlier event left in flight
    // would otherwise land afterwards and put the SAVES tab back.
    takeReadTicket(ctx.readSeqs, "saveStatus");
    ctx.setState((prev) => ({
      ...prev,
      saveSyncEnabled: false,
      // The SAVES tab goes with the flag. GAME INFO is shown in its place
      // either way (`GamePageTabs`); storing it too keeps the tab from coming
      // back to SAVES on its own when save sync is switched on again — the move
      // `handleVersionSwitched` makes too.
      activeTab: prev.activeTab === "saves" ? "info" : prev.activeTab,
    }));
    return;
  }
  // Switching save sync ON is a fact about the setting, not an answer about this
  // rom, so it lands before the read and outside its fence. Carried along with
  // the status it would be lost whenever the read is overtaken or fails — and
  // the SAVES tab, gated on this flag, would stay hidden with nothing left to
  // re-issue it.
  ctx.setState((prev) => ({ ...prev, saveSyncEnabled: true }));
  const romId = ctx.romIdRef.current;
  if (!romId) return;
  const binding = bindCurrentRom(ctx, romId);
  await Promise.all([refreshSaveSyncAnswer(binding, ctx.readSeqs), foldSaveStatusRead(ctx, binding)]);
}

/** Read the ROM's save status and fold it in, unless a later read has been issued since. */
async function foldSaveStatusRead(ctx: PanelEventContext, binding: RomBinding): Promise<void> {
  const overtaken = takeReadTicket(ctx.readSeqs, "saveStatus");
  const result = await getSaveStatus(binding.romId).catch(() => null);
  if (result && isEndpointFailure(result)) return;
  if (overtaken()) return;
  const updatedStatus: SaveStatus | null = result;
  const conflicts: SyncConflict[] = updatedStatus?.conflicts ?? [];
  binding.write((prev) => ({
    ...prev,
    saveStatus: updatedStatus,
    conflicts,
  }));
}

async function handleSaveSyncChange(
  ctx: PanelEventContext,
  detail: Extract<RommDataChangedDetail, { type: "save_sync" }>,
): Promise<void> {
  if (detail.rom_id && detail.rom_id !== ctx.romIdRef.current) return;
  await refreshSaveDetails(ctx, detail.save_status);
}

/** Fold this ROM's save status — the one `carried` on an event, or a fresh read
 *  — and re-check its slots. */
async function refreshSaveDetails(ctx: PanelEventContext, carried: SaveStatus | undefined): Promise<void> {
  const romId = ctx.romIdRef.current;
  if (!romId) return;
  const binding = bindCurrentRom(ctx, romId);
  // A status carried on the event needs no read, but still takes the ticket: it
  // is the newer answer, so a read an earlier event left open must not land on
  // top of it.
  const overtaken = takeReadTicket(ctx.readSeqs, "saveStatus");
  const result = carried ?? (await getSaveStatus(binding.romId).catch(() => null));
  if (result && isEndpointFailure(result)) return;
  const updatedStatus: SaveStatus | null = result;
  const conflicts: SyncConflict[] = updatedStatus?.conflicts ?? [];
  // Only the status fold is fenced. The slot refresh below issues its own reads
  // under their own sequences, so an overtaken run still re-checks them rather
  // than dropping the re-check on the floor.
  if (!overtaken()) {
    binding.write((prev) => ({
      ...prev,
      saveStatus: updatedStatus,
      conflicts,
    }));
  }
  // Also re-check slot configuration + refresh slot data
  refreshSlotState(binding, ctx.readSeqs);
}

async function handleBiosChange(
  ctx: PanelEventContext,
  detail: Extract<RommDataChangedDetail, { type: "bios" }>,
): Promise<void> {
  // bios events fan out to every mounted panel — ignore platforms other
  // than this panel's own, both to avoid cross-platform BIOS-list bleed
  // and to skip the wasted re-read (#1082). Read via ref to avoid a stale
  // closure.
  if (!detail.platform_slug || detail.platform_slug !== ctx.platformSlugRef.current) return;
  if (ctx.announcingOwnBiosChange.current) return;
  await rereadBiosAnswer(ctx);
}

/** Tell the other pages that *slug*'s firmware changed, after a press on this
 *  panel's own BIOS tab. The press has already read this panel's answer
 *  ({@link rereadBiosAnswer}), so the event costs no second read here; it is
 *  dispatched synchronously, which is what lets the flag cover exactly it. */
export function announceOwnBiosChange(ctx: PanelEventContext, slug: string): void {
  ctx.announcingOwnBiosChange.current = true;
  try {
    announceBiosChange(slug);
  } finally {
    ctx.announcingOwnBiosChange.current = false;
  }
}

/** Read the panel's BIOS answer again and fold it in — after a `bios` event, and
 *  after a press on the panel's own BIOS tab, which awaits it before the press
 *  ends. */
export async function rereadBiosAnswer(ctx: PanelEventContext): Promise<void> {
  const romId = ctx.romIdRef.current;
  if (!romId) return;
  // Keyed on the rom rather than the platform: the requirement depends on the
  // core, and a per-game core pin is keyed on rom_id. Read directly —
  // the firmware just changed, so joining a read issued before it would hand
  // back the pre-change answer.
  const binding = bindCurrentRom(ctx, romId);
  const overtaken = takeReadTicket(ctx.readSeqs, "bios");
  const answer = await getBiosStatus(binding.romId).catch((): BiosAnswer | null => null);
  if (overtaken()) return;
  foldBiosRead(binding, answer);
}

async function handleCoreChange(ctx: PanelEventContext): Promise<void> {
  // Re-fetch cached game detail to pick up the new core-aware BIOS status.
  invalidateCachedGameDetail(ctx.appId);
  const rid = ctx.romIdRef.current;
  if (!rid) return;
  // Core info comes from its own path (#923), keyed on the rom_id from a ref
  // to avoid a stale `state` closure. The active core reflects the per-game
  // DB override (epic #945). BIOS status is re-read from the (now core-free)
  // cache. Both land in ONE write: two writes would render the previous core
  // as the highlighted, active one against the new core's requirements.
  const binding = bindCurrentRom(ctx, rid);
  const [coreInfo, cached] = await Promise.all([
    getPlatformCoreInfo(binding.romId).catch((): CoreInfo | null => null),
    getCachedGameDetail(ctx.appId),
  ]);
  // A version switch landing inside the two reads above re-keys the panel, and
  // everything this handler still has to say is then about the ROM it moved off.
  // The rom binding already refuses the write, but it cannot give back the
  // sequence ticket the re-read below would claim on the previous version's
  // behalf — and that ticket would fence the switch's OWN re-read, dropping the
  // answer the new version's requirement depends on with nothing left to
  // re-issue it. `cancelled` does not cover this: a switch re-keys the rom
  // without changing the appId (#1713).
  if (ctx.cancelled() || ctx.romIdRef.current !== rid || !cached.found) return;
  // A cached detail carries no BIOS answer, so the shown status stands until
  // the read below answers.
  const biosFields = biosFieldsFromCache(cached);
  binding.write((prev) => ({ ...prev, ...biosFields, coreInfo: coreInfo ?? prev.coreInfo }));
  // Whose saves they are, and whether they sync, is the emulator's answer, so
  // it is asked again for the emulator now chosen.
  const saveSync = cached.save_sync_enabled ? refreshSaveSyncAnswer(binding, ctx.readSeqs) : undefined;
  // The core the requirement is computed against just changed, so a re-read that
  // could not answer leaves the PREVIOUS core's requirement on the page. Going
  // back for it is what keeps that from standing until the next page open
  // (#1752). Read directly: this handler runs because the requirement may have
  // just changed, so it must not join a read issued before the change.
  await Promise.all([refreshBiosIfStale(cached, binding, ctx.readSeqs, getBiosStatus), saveSync]);
}

/** A switch or a move of an emulator source can change which source answers for
 *  this game, and with it everything `handleCoreChange` re-reads and the save's
 *  shape. The save details are read only while save sync is on, as the cached
 *  detail says; `handleCoreChange` has invalidated it and asked for it again
 *  before its first await, so this joins that read rather than opening a
 *  second one. */
async function handleEmulatorSourcesChange(ctx: PanelEventContext): Promise<void> {
  const core = handleCoreChange(ctx);
  const saves = getCachedGameDetail(ctx.appId).then(
    (cached) => (cached.found && cached.save_sync_enabled ? refreshSaveDetails(ctx, undefined) : undefined),
    () => undefined,
  );
  await Promise.all([core, saves]);
}

async function handleVersionSwitched(
  ctx: PanelEventContext,
  detail: Extract<RommDataChangedDetail, { type: "version_switched" }>,
): Promise<void> {
  // A version switch moved the group's binding to a new rom_id. Re-read the
  // cached detail (invalidated by the picker) so the panel reflects the new
  // active version — its RomM name (the injected panel title), Region /
  // Languages rows, and cover — while the Steam hero title stays sticky.
  //
  // The per-rom TAB data must follow the new active version too. The SAVES
  // tab loads once behind slotsLoadedRef and holds per-rom state; without
  // re-keying it keeps showing the previous version's data. Reset the
  // load-once gate and re-key the tab state off the fresh cache (mirroring a
  // fresh mount) so the tab-activation effect re-fetches for the new rom_id —
  // covering both the sitting-on-a-tab case (romId changes → the effect
  // re-runs) and the open-a-tab-later case. The ACHIEVEMENTS load follows
  // the switch by itself (`useAchievements`, `utils/achievements.ts`).
  //
  // BIOS is the third per-rom tab: the requirement is core-dependent and the
  // core override is keyed on rom_id, so the new version's core may need
  // different files, or none.
  //
  // Two switches in quick succession read the cache twice and can finish in
  // either order, so this read is ordered like the mount load's — see `loadData`.
  if (detail.app_id !== ctx.appId) return;
  const overtaken = takeReadTicket(ctx.readSeqs, "detail");
  const cached = await getCachedGameDetail(ctx.appId);
  if (ctx.cancelled() || overtaken() || !cached.found) return;
  const newRomId = cached.rom_id ?? ctx.romIdRef.current;
  ctx.romIdRef.current = newRomId;
  ctx.slotsLoadedRef.current = false;
  const saveStatus = newRomId != null ? saveStatusFromCache(newRomId, cached.save_status) : null;
  // The shown BIOS answer is the previous version's, so a detail carrying no
  // answer for this one leaves the tab waiting for the live read below rather
  // than showing another ROM's.
  const biosFields = biosFieldsFromCache(cached) ?? { biosStatus: null, biosLevel: null };
  const saveSyncEnabled = cached.save_sync_enabled ?? false;
  ctx.setState((prev) => {
    // The SAVES tab can go with this switch. GAME INFO is shown in its place
    // either way (`GamePageTabs`); storing it keeps a later version with save
    // sync from opening on SAVES by itself.
    const activeTabGone = prev.activeTab === "saves" && !saveSyncEnabled;
    return {
      ...prev,
      romId: newRomId,
      romName: cached.rom_name || prev.romName,
      installed: cached.installed ?? false,
      // Cleared rather than overwritten: `refreshInstalledRomInBackground`
      // writes only on a truthy answer, so a failed or empty read would leave
      // the previous version's file name standing under this version's
      // `installed` until a remount. The record describes ONE version, and a
      // sibling download supersedes the install it names without the panel
      // hearing about it (#1742). Re-read below.
      installedRom: null,
      regions: cached.regions ?? [],
      languages: cached.languages ?? [],
      // Re-key per-rom tab state so nothing lingers from the previous version.
      saveSyncEnabled,
      saveSyncAnswer: null,
      saveStatus,
      conflicts: cached.save_status?.conflicts ?? [],
      raId: cached.ra_id ?? null,
      activeSlot: "default",
      activeSlotKnown: false,
      availableSlots: [],
      lastKnownSlots: null,
      slotsLoading: false,
      ...biosFields,
      biosReadFailed: false,
      activeTab: activeTabGone ? "info" : prev.activeTab,
    };
  });
  // Re-fetch slot configuration (slotConfirmed) + slots for the new rom_id,
  // mirroring loadData's save-sync branch — this is the authority that keeps
  // the SlotSetupWizard-vs-SavesTab gate correct across the switch.
  if (saveSyncEnabled && newRomId != null) {
    refreshSlotState(bindCurrentRom(ctx, newRomId), ctx.readSeqs);
  }
  if (newRomId) {
    const binding = bindCurrentRom(ctx, newRomId);
    const reads = [
      // The switched-to version's requirement is its own, and the tab waits
      // for it from the switch on.
      //
      // Read directly even though the store's own `version_switched` load reads
      // this same new rom_id microtasks away, which a join would collapse: a
      // switch to a version the page showed earlier can join the request opened
      // back then, and a BIOS download or a core pin since has made that answer
      // wrong. Both page-open lanes take that residual (`api/sharedReads.ts`
      // documents it) because they pay a duplicate on every open; one switch is
      // the cheaper side of the same trade.
      refreshBiosIfStale(cached, binding, ctx.readSeqs, getBiosStatus),
      refreshCoverArtInBackground(binding),
      // The BIOS tab's emulator-list line comes from the dedicated core-info
      // path, keyed on rom_id so a per-game override follows the
      // switch. A failed read keeps the previous reading rather than
      // blanking it.
      refreshPanelCoreInfo(binding),
    ];
    // Read only when this version has an install to describe, mirroring the
    // mount load — the ROM File row and its launch-target note are hidden
    // either way, and an uninstalled version has no record to fetch.
    if (cached.installed) reads.push(refreshInstalledRomInBackground(binding));
    if (saveSyncEnabled) reads.push(refreshSaveSyncAnswer(binding, ctx.readSeqs));
    await Promise.all(reads);
  }
}

async function handleMetadataChange(
  ctx: PanelEventContext,
  detail: Extract<RommDataChangedDetail, { type: "metadata" }>,
): Promise<void> {
  if (detail.rom_id !== ctx.romIdRef.current) return;
  const romId = ctx.romIdRef.current;
  if (!romId) return;
  const binding = bindCurrentRom(ctx, romId);
  const meta = await getRomMetadata(binding.romId).catch((): RomMetadata | null => null);
  binding.write((prev) => ({ ...prev, metadata: meta }));
}

async function handleCoverRefreshed(
  ctx: PanelEventContext,
  detail: Extract<RommDataChangedDetail, { type: "cover_refreshed" }>,
): Promise<void> {
  if (detail.rom_id !== ctx.romIdRef.current) return;
  const romId = ctx.romIdRef.current;
  if (!romId) return;
  // Re-fetch RomM's cover, which GAME INFO falls back to where Steam's own copy
  // does not load. A cover set from the ARTWORK tab dispatches this too: Steam's
  // copy then changes on its own, and RomM's is re-read all the same.
  await refreshCoverArtInBackground(bindCurrentRom(ctx, romId));
}

/** Route one `romm_data_changed` payload to the handler that owns its type.
 *
 *  Nothing is dispatched before the panel's identity has landed: every handler
 *  below reads `romIdRef` as the ROM it answers for.
 *
 *  Returns the chosen handler's own promise rather than awaiting it, so the
 *  caller's `await` resolves on the same turn it did when the switch sat in the
 *  caller. */
function dispatchDataChanged(ctx: PanelEventContext, detail: RommDataChangedDetail | undefined): Promise<void> {
  if (!ctx.romIdRef.current) return Promise.resolve();
  switch (detail?.type) {
    case "save_sync_settings":
      return handleSaveSyncSettingsChange(ctx, detail);
    case "save_sync":
      return handleSaveSyncChange(ctx, detail);
    case "bios":
      return handleBiosChange(ctx, detail);
    case "core_changed":
      return handleCoreChange(ctx);
    case "emulator_sources":
      return handleEmulatorSourcesChange(ctx);
    case "metadata":
      return handleMetadataChange(ctx, detail);
    case "cover_refreshed":
      return handleCoverRefreshed(ctx, detail);
    case "version_switched":
      return handleVersionSwitched(ctx, detail);
    default:
      return Promise.resolve();
  }
}

/** Ask again whether the ROM's saves sync once it was downloaded or uninstalled:
 *  a game that is not downloaded is answered about the path it would occupy, so
 *  the answer says which (#2149 D4). Asked only while save sync is on, as the
 *  cached detail says, which the SAVES tab is shown for. */
async function rereadSaveSyncAnswer(ctx: PanelEventContext, romId: number): Promise<void> {
  const cached = await getCachedGameDetail(ctx.appId).catch(() => null);
  if (cached?.found && cached.save_sync_enabled) await refreshSaveSyncAnswer(bindCurrentRom(ctx, romId), ctx.readSeqs);
}

/** Ask again whether the ROM's saves sync as the SAVES tab opens: the emulator's
 *  settings may have changed since the last answer, which stays shown until this
 *  one lands (#2149 D8). */
export function rereadSaveSyncOnTabOpen(ctx: PanelEventContext): Promise<void> {
  const romId = ctx.romIdRef.current;
  if (!romId) return Promise.resolve();
  return refreshSaveSyncAnswer(bindCurrentRom(ctx, romId), ctx.readSeqs);
}

/** Subscribe the panel's event listeners; the returned teardown unsubscribes
 *  them all. */
export function wirePanelEvents(ctx: PanelEventContext): () => void {
  // Listen for uninstall events to update state (uses ref to avoid stale closure)
  const onUninstall = (e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail?.rom_id === ctx.romIdRef.current) {
      ctx.setState((prev) => ({ ...prev, installed: false, installedRom: null }));
      detach(rereadSaveSyncAnswer(ctx, detail.rom_id));
    }
  };
  globalThis.addEventListener("romm_rom_uninstalled", onUninstall);

  // Listen for download completion — the install counterpart to onUninstall.
  // The "ROM File" section is gated on installed + installedRom; a fresh
  // download must flip both so the section (with the local path) appears
  // without a detail-page re-mount (#1340). A backend event (`api/host`),
  // not a DOM CustomEvent — mirrors DiscSelector's download_complete wiring.
  const onDownloadComplete = addEventListener<DownloadCompleteEvent>(
    "download_complete",
    (evt: DownloadCompleteEvent) => {
      if (evt.rom_id !== ctx.romIdRef.current) return;
      // Invalidate the cached detail so a fast re-mount inside the 3s TTL
      // doesn't briefly re-serve the stale installed:false.
      invalidateCachedGameDetail(ctx.appId);
      ctx.setState((prev) => ({ ...prev, installed: true }));
      detach(refreshInstalledRomInBackground(bindCurrentRom(ctx, evt.rom_id)));
      detach(rereadSaveSyncAnswer(ctx, evt.rom_id));
    },
  );

  const onDataChanged = (e: Event) => {
    detach(
      (async () => {
        try {
          await dispatchDataChanged(ctx, (e as CustomEvent).detail);
        } catch (err) {
          detach(debugLog(`RomMGameInfoPanel: onDataChanged error: ${err}`));
        }
      })(),
    );
  };
  globalThis.addEventListener("romm_data_changed", onDataChanged);

  const onTabSwitch = (e: Event) => {
    const tab = (e as CustomEvent).detail?.tab;
    if (tab) ctx.setState((prev) => ({ ...prev, activeTab: tab }));
  };
  globalThis.addEventListener("romm_tab_switch", onTabSwitch);

  return () => {
    globalThis.removeEventListener("romm_rom_uninstalled", onUninstall);
    removeEventListener("download_complete", onDownloadComplete);
    globalThis.removeEventListener("romm_data_changed", onDataChanged);
    globalThis.removeEventListener("romm_tab_switch", onTabSwitch);
  };
}
