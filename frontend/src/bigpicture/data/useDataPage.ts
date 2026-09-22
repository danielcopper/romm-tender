/**
 * Everything the Data Management page knows and does: the figures each
 * inventory row carries, and the action each detail pane offers.
 *
 * It lives above the panes because the page renders only the selected one — a
 * pane owning its own reads would re-issue them on every move through the list,
 * and focus selects here, so the stick passes every row on the way down.
 *
 * Which figures are fetched and which are waited for is the page's rule rather
 * than a pane's: the inventory read and the shortcut count are asked once when
 * the page opens, the non-Steam scan is Steam's own store and costs nothing, and
 * the two that cost a round trip read `scan` until the reader presses for them.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Data
 * Management.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  cleanupOrphanedGridImages,
  getDataInventory,
  getSyncStats,
  getWhitelistSettings,
  logError,
  logInfo,
  logWarn,
  removeAllShortcuts,
  reportRemovalResults,
  uninstallAllRoms,
  updateWhitelistSettings,
} from "../../api/backend";
import type { DataInventory } from "../../types";
import { clearAllRomMCollections } from "../../utils/collections";
import { detach } from "../../utils/detach";
import { formatUninstallStatus } from "../../utils/formatters";
import { batchConfirmLaunchOptions } from "../../utils/launchOptionsReconcile";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancellation,
  isPruneLeaseCancelled,
  mountPruneLeaseOwner,
  releasePruneLeasesByOwner,
  withPruneLease,
} from "../../utils/pruneLease";
import { removeShortcutsPaced } from "../../utils/shortcutRemoval";
import { getAllNonSteamShortcutAppIds, getLiveRomMShortcutAppIds } from "../../utils/steamShortcuts";
import { withTimeout } from "../../utils/withTimeout";

const REMOVAL_REPORT_TIMEOUT_MS = 15000;
// Process-local: the owner is a key in `pruneLease`'s own map, so it has to be
// unique among the pages that hold leases and means nothing outside this run.
const DATA_PAGE_LEASE_OWNER = "data-management";

export const DEFAULT_WHITELIST_PATTERNS: string[] = [
  "retrodeck",
  "moonlight",
  "chiaki",
  "chrome",
  "chromium",
  "firefox",
  "vivaldi",
  "heroic",
  "lutris",
  "bottles",
  "protonup",
  "emudeck",
  "desktop mode",
  "return to gaming mode",
  "nonsteamlaunchers",
];

export interface NonSteamApp {
  appId: number;
  name: string;
}

const SETTLE_POLL_MS = 250;
const SETTLE_TIMEOUT_MS = 3000;

const readShortcutStoreSize = (): number | null => {
  if (typeof collectionStore === "undefined") return null;
  const apps = collectionStore.deckDesktopApps?.apps;
  return apps ? apps.size : null;
};

// Steam drops a removed shortcut from `deckDesktopApps.apps` a beat after
// `RemoveShortcut` fires — the store settles asynchronously. Poll until the
// store size has fallen by `removedCount` (or a short timeout elapses), THEN
// re-count via `loadNonSteamApps`, so the row's count isn't left showing the
// pre-removal number. Deliberately dumb: fixed cadence, single timeout, no
// retries. If the store is unreadable or nothing was removed, re-count
// immediately.
async function recountAfterStoreSettles(removedCount: number, loadNonSteamApps: () => void): Promise<void> {
  const baseline = readShortcutStoreSize();
  if (baseline !== null && removedCount > 0) {
    const target = Math.max(0, baseline - removedCount);
    const deadline = Date.now() + SETTLE_TIMEOUT_MS;
    for (;;) {
      const size = readShortcutStoreSize();
      if (size === null || size <= target || Date.now() >= deadline) break;
      await new Promise<void>((resolve) => setTimeout(resolve, SETTLE_POLL_MS));
    }
  }
  loadNonSteamApps();
}

/** Live progress of an in-flight bulk removal. */
export interface RemovalProgress {
  removed: number;
  total: number;
}

/**
 * A population figure that costs a round trip: `null` until the reader asks for
 * it, a number once an answer has come back.
 *
 * Focus selects on this layout, so a figure fetched on selection would put a
 * round trip under every row the stick passes.
 */
export type ScannedCount = number | null;

export interface DataPageState {
  /** Bound RomM shortcuts, from the same stats read Main makes. */
  shortcutCount: number | null;
  /** The installed-ROM and recovery-bundle figures, or `null` while unread. */
  inventory: DataInventory | null;
  nonSteamApps: NonSteamApp[];
  whitelistedIds: Set<number>;
  disabledDefaults: string[];
  customNames: string[];
  settingsLoaded: boolean;
  orphanedGridImages: ScannedCount;
  /** A bulk removal is in flight — every removal button is dead while it is. */
  busy: boolean;
  removalProgress: RemovalProgress | null;
  shortcutStatus: string;
  uninstallStatus: string;
  gridStatus: string;
  nonSteamStatus: string;
  removeAllShortcuts: () => Promise<void>;
  uninstallAllRoms: () => Promise<void>;
  /** Scans on the first call and removes on the second — the arming is the caller's. */
  cleanupGridImages: (execute: boolean) => Promise<void>;
  removeNonSteamApps: (apps: NonSteamApp[]) => Promise<void>;
  persistWhitelist: (disabled: string[], custom: string[]) => void;
}

export function useDataPage(): DataPageState {
  const [shortcutCount, setShortcutCount] = useState<number | null>(null);
  const [inventory, setInventory] = useState<DataInventory | null>(null);
  const [nonSteamApps, setNonSteamApps] = useState<NonSteamApp[]>([]);
  const [disabledDefaults, setDisabledDefaults] = useState<string[]>([]);
  const [customNames, setCustomNames] = useState<string[]>([]);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [orphanedGridImages, setOrphanedGridImages] = useState<ScannedCount>(null);
  const [busy, setBusy] = useState(false);
  const [removalProgress, setRemovalProgress] = useState<RemovalProgress | null>(null);
  const [shortcutStatus, setShortcutStatus] = useState("");
  const [uninstallStatus, setUninstallStatus] = useState("");
  const [gridStatus, setGridStatus] = useState("");
  const [nonSteamStatus, setNonSteamStatus] = useState("");

  const loadNonSteamApps = useCallback(() => {
    const apps: NonSteamApp[] = [];
    try {
      if (typeof collectionStore === "undefined") {
        logWarn("collectionStore not available");
        setNonSteamApps([]);
        return;
      }
      const deckApps = collectionStore.deckDesktopApps?.apps;
      if (!deckApps) {
        logWarn("deckDesktopApps.apps not available");
        setNonSteamApps([]);
        return;
      }
      logInfo(`deckDesktopApps.apps size: ${deckApps.size}`);
      const appIds = Array.from(deckApps.keys());
      for (const appId of appIds) {
        let name = `Unknown (${appId})`;
        if (typeof appStore !== "undefined") {
          const overview = appStore.GetAppOverviewByAppID(appId);
          if (overview) {
            name = overview.strDisplayName || overview.display_name || name;
          }
        }
        apps.push({ appId, name });
      }
    } catch (e) {
      logError(`Failed to enumerate non-steam games: ${e}`);
    }
    apps.sort((a, b) => a.name.localeCompare(b.name));
    setNonSteamApps(apps);
  }, []);

  useEffect(() => {
    mountPruneLeaseOwner(DATA_PAGE_LEASE_OWNER);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial async data loads on mount are the standard React pattern; the rule is overzealous here
    loadNonSteamApps();
    getWhitelistSettings()
      .then((s) => {
        setDisabledDefaults(s.disabled_defaults);
        setCustomNames(s.custom_names);
        setSettingsLoaded(true);
      })
      .catch((e) => logError(`Failed to load whitelist settings: ${e}`));
    getSyncStats()
      .then((stats) => setShortcutCount(stats.total_shortcuts))
      .catch((e) => logError(`Failed to read the shortcut count: ${e}`));
    getDataInventory()
      .then(setInventory)
      .catch((e) => logError(`Failed to read the data inventory: ${e}`));
    return () => {
      detach(releasePruneLeasesByOwner(DATA_PAGE_LEASE_OWNER));
    };
  }, [loadNonSteamApps]);

  const activeDefaults = useMemo(
    () => DEFAULT_WHITELIST_PATTERNS.filter((p) => !disabledDefaults.includes(p)),
    [disabledDefaults],
  );

  const whitelistedIds = useMemo(() => {
    const set = new Set<number>();
    for (const app of nonSteamApps) {
      const lower = app.name.toLowerCase();
      const matchesDefault = activeDefaults.some((p) => lower.includes(p));
      if (matchesDefault || customNames.includes(app.name)) {
        set.add(app.appId);
      }
    }
    return set;
  }, [nonSteamApps, activeDefaults, customNames]);

  /**
   * Run a bulk removal wrapped in the page's busy affordance: the removal
   * buttons disable and the progress counter shows for its duration. *work*
   * receives an `onProgress(removed, total)` reporter to thread into
   * `removeShortcutsPaced`; busy + progress are always cleared when it settles.
   */
  const runRemoval = async (
    work: (onProgress: (removed: number, total: number) => void) => Promise<void>,
  ): Promise<void> => {
    setBusy(true);
    try {
      await work((removed, total) => setRemovalProgress({ removed, total }));
    } finally {
      setBusy(false);
      setRemovalProgress(null);
    }
  };

  const handleRemoveAllShortcuts = async () => {
    await runRemoval(async (onProgress) => {
      setShortcutStatus("Removing all shortcuts...");
      let removedCount = 0;
      const admission = capturePruneLeaseAdmission(DATA_PAGE_LEASE_OWNER);
      try {
        const result = await removeAllShortcuts();
        if (!result.success) {
          // A gate refusal (@sync_active_blocked / @migration_blocked) carries
          // no app_ids/rom_ids — surface its message and remove nothing.
          setShortcutStatus(result.message ?? "Failed to remove shortcuts");
        } else {
          // The backend list is the DB binding map (roms.shortcut_app_id). A
          // crashed sync run's in-flight chunk can leave RomM-owned shortcuts in
          // Steam (exe = bin/tender-rom-launcher) that were never committed — no binding,
          // so the backend never returns them. The live exe-ownership scan sees
          // them; remove the UNION so no orphan is left behind.
          const backendAppIds = result.app_ids ?? [];
          const removed = new Set<number>(backendAppIds);
          await withPruneLease(
            result.prune_lease_token,
            "All-shortcut removal",
            async (signal) => {
              await removeShortcutsPaced(backendAppIds, onProgress, signal);
              if (isPruneLeaseCancelled(signal)) return;
              const liveAppIds = await getLiveRomMShortcutAppIds();
              if (isPruneLeaseCancelled(signal)) return;
              if (liveAppIds === null) {
                // The scan could not run (Steam's shortcut store was unreadable) —
                // fall back to the backend-bound list alone rather than skip removal.
                logWarn("Live RomM shortcut scan unavailable — removed backend-bound shortcuts only.");
              } else {
                const orphans = liveAppIds.filter((appId) => !removed.has(appId));
                logInfo(`Remove-all: ${orphans.length} live-scanned RomM shortcut(s) were not in the backend list.`);
                // Continue the counter across the orphan sweep: offset by the backend
                // count so "removed of total" stays cumulative (orphans are usually none).
                await removeShortcutsPaced(
                  orphans,
                  (done, orphanTotal) => onProgress(backendAppIds.length + done, backendAppIds.length + orphanTotal),
                  signal,
                );
                if (isPruneLeaseCancelled(signal)) return;
                for (const appId of orphans) removed.add(appId);
              }
              removedCount = removed.size;
              await clearAllRomMCollections(signal);
              if (isPruneLeaseCancelled(signal)) return;
              // rom_ids are backend DB rows — orphans have none, so report only the
              // backend set exactly as before.
              if (result.rom_ids?.length || result.prune_lease_token) {
                await withTimeout(
                  reportRemovalResults(result.rom_ids ?? [], result.prune_lease_token ?? null),
                  REMOVAL_REPORT_TIMEOUT_MS,
                );
              }
            },
            DATA_PAGE_LEASE_OWNER,
            admission,
          );
          setShortcutStatus(result.message ?? "All shortcuts removed");
          setShortcutCount(0);
        }
      } catch (e) {
        // Teardown cancellation, not a failed removal — the backend work already
        // committed and there is no panel left to refresh or report into.
        if (isPruneLeaseCancellation(e, admission)) {
          logWarn(`All-shortcut removal continuation was cancelled: ${e}`);
          return;
        }
        setShortcutStatus("Failed to remove shortcuts");
      }
      await recountAfterStoreSettles(removedCount, loadNonSteamApps);
    });
  };

  const handleUninstallAllRoms = async () => {
    try {
      setUninstallStatus("Uninstalling...");
      const admission = capturePruneLeaseAdmission(DATA_PAGE_LEASE_OWNER);
      const result = await uninstallAllRoms();
      if (!result.success && result.app_ids === undefined) {
        // A gate refusal (@sync_active_blocked / @migration_blocked) carries no
        // removal payload — surface its message before touching app_ids. A
        // PARTIAL failure (success false WITH payload) still falls through to
        // the launch-options reset + count display below.
        setUninstallStatus(result.message ?? "Failed to uninstall ROMs");
      } else {
        // Reset every kept shortcut's now-stale launch command to the uninstalled
        // "" placeholder so a raced-past not_installed can't exec a stale
        // `flatpak run … "<deleted path>"` into a deleted path. Batched to avoid
        // serializing the per-shortcut confirm-poll timeouts; best-effort — a
        // failed confirm is logged, not fatal.
        await withPruneLease(
          result.prune_lease_token,
          "Bulk uninstall",
          (signal) =>
            batchConfirmLaunchOptions(
              (result.app_ids ?? []).map((appId) => ({ app_id: appId, launch_options: "" })),
              "uninstall-all",
              signal,
            ),
          DATA_PAGE_LEASE_OWNER,
          admission,
        );
        setUninstallStatus(formatUninstallStatus(result.removed_count ?? 0, (result.errors ?? []).length));
        // The files are gone, so the figures the row carried are stale. Re-read
        // rather than subtract: a partial failure left some of them behind.
        getDataInventory()
          .then(setInventory)
          .catch((e) => logError(`Failed to re-read the data inventory: ${e}`));
      }
    } catch {
      setUninstallStatus("Failed to uninstall ROMs");
    }
    loadNonSteamApps();
  };

  // Removed or re-created shortcuts leave their grid images ({app_id}p.png and
  // the hero/logo/icon/wide companions) behind forever. Safety model: the
  // keep-set is the frontend's FULL live-shortcut scan (RomM-owned AND
  // foreign); a null scan aborts before the backend is ever called ("scan
  // couldn't run → delete nothing"); the backend range-checks every candidate
  // (store-game art is never touched) and refuses outright when any bound
  // shortcut is missing from the submitted set.
  const handleCleanupGridImages = async (execute: boolean) => {
    const liveAppIds = getAllNonSteamShortcutAppIds();
    if (liveAppIds === null) {
      // The scan could not run — without the live keep-set, nothing can be
      // proven orphaned. Abort without calling the backend.
      setGridStatus("Could not read Steam's shortcut list — nothing was removed.");
      return;
    }
    if (!execute) {
      try {
        const result = await cleanupOrphanedGridImages(liveAppIds, true);
        if (!result.success) {
          setGridStatus(result.message ?? "Failed to scan for orphaned images");
          return;
        }
        const count = result.candidate_count ?? 0;
        setOrphanedGridImages(count);
        setGridStatus(count === 0 ? "No orphaned grid images found" : "");
      } catch {
        setGridStatus("Failed to scan for orphaned images");
      }
      return;
    }
    try {
      const result = await cleanupOrphanedGridImages(liveAppIds, false);
      if (!result.success) {
        setGridStatus(result.message ?? "Failed to remove orphaned images");
        return;
      }
      const removed = result.removed_count ?? 0;
      setOrphanedGridImages(0);
      setGridStatus(`Removed ${removed} orphaned image${removed === 1 ? "" : "s"}`);
    } catch {
      setGridStatus("Failed to remove orphaned images");
    }
  };

  const handleRemoveNonSteamApps = async (apps: NonSteamApp[]) => {
    await runRemoval(async (onProgress) => {
      setNonSteamStatus(`Removing ${apps.length} non-steam games...`);
      await removeShortcutsPaced(
        apps.map((a) => a.appId),
        onProgress,
      );
      setNonSteamStatus(`Removed ${apps.length} non-steam game${apps.length === 1 ? "" : "s"}`);
      await recountAfterStoreSettles(apps.length, loadNonSteamApps);
    });
  };

  const persistWhitelist = (newDisabled: string[], newCustom: string[]) => {
    setDisabledDefaults(newDisabled);
    setCustomNames(newCustom);
    updateWhitelistSettings(newDisabled, newCustom).catch((e) => logError(`Failed to update whitelist settings: ${e}`));
  };

  return {
    shortcutCount,
    inventory,
    nonSteamApps,
    whitelistedIds,
    disabledDefaults,
    customNames,
    settingsLoaded,
    orphanedGridImages,
    busy,
    removalProgress,
    shortcutStatus,
    uninstallStatus,
    gridStatus,
    nonSteamStatus,
    removeAllShortcuts: handleRemoveAllShortcuts,
    uninstallAllRoms: handleUninstallAllRoms,
    cleanupGridImages: handleCleanupGridImages,
    removeNonSteamApps: handleRemoveNonSteamApps,
    persistWhitelist,
  };
}
