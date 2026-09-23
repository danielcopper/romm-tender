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
  isCallableFailure,
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
import { pluralize } from "../../utils/pluralize";
import { getPruneState, onPruneStateChange } from "../../utils/pruneStore";
import { removeShortcutsPaced } from "../../utils/shortcutRemoval";
import {
  getAllNonSteamShortcutAppIds,
  getLiveRomMShortcutAppIds,
  scanShortcutOwnership,
  type ShortcutOwnership,
} from "../../utils/steamShortcuts";
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
// re-count via `recount`, so the row's count isn't left showing the
// pre-removal number. Deliberately dumb: fixed cadence, single timeout, no
// retries. If the store is unreadable or nothing was removed, re-count
// immediately.
async function recountAfterStoreSettles(removedCount: number, recount: () => void): Promise<void> {
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
  recount();
}

/** Live progress of an in-flight bulk removal. */
export interface RemovalProgress {
  removed: number;
  total: number;
}

/**
 * A figure the page reads: still being read, could not be read, or answered.
 *
 * Reading and failed are kept apart because they tell the reader opposite
 * things — one is work that will finish on its own, the other asks them to try
 * again.
 */
export type PageRead<T> = { state: "reading" } | { state: "failed" } | { state: "answered"; value: T };

const READING = { state: "reading" } as const;
const FAILED = { state: "failed" } as const;

/**
 * A population figure that costs a round trip: not asked until the reader
 * presses for it, then read like any other figure.
 *
 * Focus selects on this layout, so a figure fetched on selection would put a
 * round trip under every row the stick passes.
 */
export type ScannedCount = { state: "not-asked" } | PageRead<number>;

const NOT_ASKED = { state: "not-asked" } as const;

export interface DataPageState {
  /** Bound RomM shortcuts, from the same stats read Main makes. */
  shortcutCount: PageRead<number>;
  /** The installed-game and recovery-bundle figures. */
  inventory: PageRead<DataInventory>;
  /**
   * The non-Steam entries this plugin did NOT create — answered only once
   * ownership has been established.
   *
   * `failed` is not "none": it means Steam's shortcut store could not be read,
   * so nothing here can be proven foreign. The row reads as unavailable and the
   * removal is refused — the same abort the grid cleanup takes when its scan
   * cannot run, and for the same reason: without the ownership answer a removal
   * would take this plugin's whole library with it. `reading` refuses too, for
   * the same want of an answer. An entry the sweep could not identify is the
   * same refusal one entry wide: see `unidentifiedCount`.
   */
  foreignApps: PageRead<NonSteamApp[]>;
  /**
   * How many of Steam's non-Steam entries the sweep could not identify.
   *
   * Steam does not always answer for a shortcut before the read gives up, and
   * an unanswered entry is not evidence that it is foreign — so it is in
   * neither `foreignApps` nor the removal, and the pane says how many are
   * being left alone.
   */
  unidentifiedCount: number;
  whitelistedIds: Set<number>;
  disabledDefaults: string[];
  customNames: string[];
  settingsLoaded: boolean;
  orphanedGridImages: ScannedCount;
  /** What the last Gone-from-RomM scan found, held for the visit. */
  removedGames: ScannedCount;
  /** A bulk removal is in flight — every removal button is dead while it is. */
  busy: boolean;
  /** What the running removal is called, for the one busy line the list shows. */
  busyLabel: string;
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
  /** Where a Gone-from-RomM scan stands, reported by the section that runs it. */
  recordRemovedGamesScan: (read: PageRead<number>) => void;
}

export function useDataPage(): DataPageState {
  const [shortcutCount, setShortcutCount] = useState<PageRead<number>>(READING);
  const [inventory, setInventory] = useState<PageRead<DataInventory>>(READING);
  const [nonSteamApps, setNonSteamApps] = useState<PageRead<NonSteamApp[]>>(READING);
  const [disabledDefaults, setDisabledDefaults] = useState<string[]>([]);
  const [customNames, setCustomNames] = useState<string[]>([]);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [orphanedGridImages, setOrphanedGridImages] = useState<ScannedCount>(NOT_ASKED);
  const [removedGames, setRemovedGames] = useState<ScannedCount>(NOT_ASKED);
  // What Steam could be made to say about each of its non-Steam entries, read
  // by exe rather than from the database: a crashed run can leave one of ours
  // in Steam with no binding, and a removal that took it for a foreign entry
  // would delete a game the next sync expects to find. `failed` is "the store
  // could not be read at all"; the reading's own `unresolved` is the entries
  // within it that Steam did not answer for.
  const [ownership, setOwnership] = useState<PageRead<ShortcutOwnership>>(READING);
  const [busy, setBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState("");
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
        setNonSteamApps(FAILED);
        return;
      }
      const deckApps = collectionStore.deckDesktopApps?.apps;
      if (!deckApps) {
        logWarn("deckDesktopApps.apps not available");
        setNonSteamApps(FAILED);
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
      // Whatever was listed before the throw is part of a list, not the list.
      logError(`Failed to enumerate non-steam games: ${e}`);
      setNonSteamApps(FAILED);
      return;
    }
    apps.sort((a, b) => a.name.localeCompare(b.name));
    setNonSteamApps({ state: "answered", value: apps });
  }, []);

  /**
   * Re-ask Steam which of its non-Steam entries are this plugin's.
   *
   * Read every time the enumeration is, because both answers describe the same
   * moment: a removal that shrinks one must not leave the other standing.
   */
  const loadOwnership = useCallback(() => {
    // No ordering guard: two sweeps in flight can land out of order, and the
    // older one's owned set is the LARGER one (it saw shortcuts a removal has
    // since taken out), so accepting it offers fewer entries for removal and
    // never one of ours. An older sweep that FAILED can likewise land over a
    // newer answer, and that too errs the safe way: the removal is refused and
    // the pane asks for the page to be opened again. The direction that matters
    // cannot be made wrong by losing this race.
    scanShortcutOwnership()
      .then((scan) => setOwnership(scan === null ? FAILED : { state: "answered", value: scan }))
      .catch((e) => {
        logWarn(`Live RomM shortcut scan failed: ${e}`);
        setOwnership(FAILED);
      });
  }, []);

  // Unguarded like `loadOwnership`: the mount read failing after the re-read an
  // uninstall issues has answered leaves `failed` over a true figure — a
  // reopen, never a wrong number.
  const readInventory = useCallback(() => {
    getDataInventory()
      // No callable reached here resolves `{success: false}` today — a raising
      // one rejects instead — so this test is defensive: were one to, it would
      // carry none of the figures its declared type names.
      .then((answer) => setInventory(isCallableFailure(answer) ? FAILED : { state: "answered", value: answer }))
      .catch((e) => {
        logError(`Failed to read the data inventory: ${e}`);
        setInventory(FAILED);
      });
  }, []);

  const refreshNonSteam = useCallback(() => {
    loadNonSteamApps();
    loadOwnership();
  }, [loadNonSteamApps, loadOwnership]);

  useEffect(() => {
    mountPruneLeaseOwner(DATA_PAGE_LEASE_OWNER);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial async data loads on mount are the standard React pattern; the rule is overzealous here
    refreshNonSteam();
    getWhitelistSettings()
      .then((s) => {
        setDisabledDefaults(s.disabled_defaults);
        setCustomNames(s.custom_names);
        setSettingsLoaded(true);
      })
      .catch((e) => logError(`Failed to load whitelist settings: ${e}`));
    getSyncStats()
      // The failure-shape test is defensive, for the reason at `readInventory`.
      .then((stats) =>
        setShortcutCount(isCallableFailure(stats) ? FAILED : { state: "answered", value: stats.total_shortcuts }),
      )
      .catch((e) => {
        logError(`Failed to read the shortcut count: ${e}`);
        setShortcutCount(FAILED);
      });
    readInventory();
    // A finished cleanup can make both scanned numbers wrong, and a wrong
    // number is worse than none; why Grid images is one of them:
    // `docs/architecture/qam-panel.md`, section Data Management.
    const unsubscribePrune = onPruneStateChange(() => {
      if (getPruneState().complete === null) return;
      setRemovedGames(NOT_ASKED);
      setOrphanedGridImages(NOT_ASKED);
    });
    return () => {
      unsubscribePrune();
      detach(releasePruneLeasesByOwner(DATA_PAGE_LEASE_OWNER));
    };
  }, [refreshNonSteam, readInventory]);

  const activeDefaults = useMemo(
    () => DEFAULT_WHITELIST_PATTERNS.filter((p) => !disabledDefaults.includes(p)),
    [disabledDefaults],
  );

  /**
   * Steam's non-Steam entries minus the ones this plugin put there.
   *
   * This row is about what ELSE is in the library; removing what this plugin
   * created is the Tender's-shortcuts row's job, and it removes by binding and
   * ownership rather than by name. Without both the enumeration and the
   * ownership answer the set cannot be formed at all, which is what an
   * unanswered read says — failed where either failed, else reading.
   */
  const foreignApps = useMemo((): PageRead<NonSteamApp[]> => {
    if (nonSteamApps.state === "failed" || ownership.state === "failed") return FAILED;
    if (nonSteamApps.state !== "answered" || ownership.state !== "answered") return READING;
    const accountedFor = new Set([...ownership.value.owned, ...ownership.value.unresolved]);
    return { state: "answered", value: nonSteamApps.value.filter((app) => !accountedFor.has(app.appId)) };
  }, [nonSteamApps, ownership]);

  /** Entries Steam did not answer for, which are therefore offered to nothing. */
  const unidentifiedCount = useMemo(() => {
    if (nonSteamApps.state !== "answered" || ownership.state !== "answered") return 0;
    const listed = new Set(nonSteamApps.value.map((app) => app.appId));
    return ownership.value.unresolved.filter((appId) => listed.has(appId)).length;
  }, [nonSteamApps, ownership]);

  const whitelistedIds = useMemo(() => {
    const set = new Set<number>();
    for (const app of foreignApps.state === "answered" ? foreignApps.value : []) {
      const lower = app.name.toLowerCase();
      const matchesDefault = activeDefaults.some((p) => lower.includes(p));
      if (matchesDefault || customNames.includes(app.name)) {
        set.add(app.appId);
      }
    }
    return set;
  }, [foreignApps, activeDefaults, customNames]);

  /**
   * Run a bulk removal wrapped in the page's busy affordance: the removal
   * buttons disable and the progress counter shows for its duration. *work*
   * receives an `onProgress(removed, total)` reporter to thread into
   * `removeShortcutsPaced`; busy + progress are always cleared when it settles.
   */
  const runRemoval = async (
    label: string,
    work: (onProgress: (removed: number, total: number) => void) => Promise<void>,
  ): Promise<void> => {
    setBusy(true);
    setBusyLabel(label);
    try {
      await work((removed, total) => setRemovalProgress({ removed, total }));
    } finally {
      setBusy(false);
      setBusyLabel("");
      setRemovalProgress(null);
    }
  };

  const handleRemoveAllShortcuts = async () => {
    await runRemoval("Removing shortcuts", async (onProgress) => {
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
          // Why a shortcut removal un-asks the grid count:
          // `docs/architecture/qam-panel.md`, section Data Management.
          setOrphanedGridImages(NOT_ASKED);
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
          setShortcutCount({ state: "answered", value: 0 });
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
      await recountAfterStoreSettles(removedCount, refreshNonSteam);
    });
  };

  const handleUninstallAllRoms = async () => {
    // No per-item progress to report: this is one backend call over the whole
    // library, so the page shows the busy line without a counter.
    await runRemoval("Uninstalling ROM files", async () => {
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
          readInventory();
        }
      } catch {
        setUninstallStatus("Failed to uninstall ROMs");
      }
      refreshNonSteam();
    });
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
      if (execute) {
        setGridStatus("Could not read Steam's shortcut list — nothing was removed.");
      } else {
        setOrphanedGridImages(FAILED);
        setGridStatus("Steam's shortcut list could not be read, so nothing could be checked.");
      }
      return;
    }
    if (!execute) {
      // A failed scan is said once, by the pane's retry line; the status line
      // carries only a cause that line cannot give.
      setOrphanedGridImages(READING);
      setGridStatus("");
      try {
        const result = await cleanupOrphanedGridImages(liveAppIds, true);
        if (!result.success) {
          setOrphanedGridImages(FAILED);
          setGridStatus(result.message ?? "");
          return;
        }
        const count = result.candidate_count ?? 0;
        setOrphanedGridImages({ state: "answered", value: count });
        setGridStatus(count === 0 ? "No orphaned grid images found" : "");
      } catch (e) {
        logWarn(`Orphaned grid image scan failed: ${e}`);
        setOrphanedGridImages(FAILED);
      }
      return;
    }
    // What each answer of the removal leaves on the row, and why:
    // `docs/architecture/qam-panel.md`, section Data Management.
    await runRemoval("Removing orphaned images", async () => {
      try {
        const result = await cleanupOrphanedGridImages(liveAppIds, false);
        if (!result.success) {
          setGridStatus(result.message ?? "Failed to remove orphaned images");
          return;
        }
        const { candidate_count: candidates, removed_count: removed } = result;
        if (candidates === undefined || removed === undefined || removed > candidates) {
          setOrphanedGridImages(NOT_ASKED);
          setGridStatus("The removal did not say whether every image went. Scan again to count what remains.");
          return;
        }
        if (removed === candidates) {
          setOrphanedGridImages({ state: "answered", value: 0 });
          setGridStatus(`Removed ${pluralize(removed, "orphaned image")}`);
          return;
        }
        setOrphanedGridImages(NOT_ASKED);
        setGridStatus(
          `Removed ${removed} of ${pluralize(candidates, "orphaned image")} — ${candidates - removed} could not be deleted. Scan again to count what remains.`,
        );
      } catch (e) {
        logError(`Orphaned grid image removal failed: ${e}`);
        setOrphanedGridImages(NOT_ASKED);
        setGridStatus("Whether the images were removed could not be established. Scan again to count what remains.");
      }
    });
  };

  const handleRemoveNonSteamApps = async (apps: NonSteamApp[]) => {
    if (foreignApps.state !== "answered") {
      // Ownership has not been established, so nothing here can be proven
      // foreign — refuse rather than remove a set this plugin may be in.
      setNonSteamStatus("Could not read Steam's shortcut list — nothing was removed.");
      return;
    }
    await runRemoval("Removing non-Steam games", async (onProgress) => {
      setOrphanedGridImages(NOT_ASKED);
      setNonSteamStatus(`Removing ${apps.length} non-Steam games...`);
      await removeShortcutsPaced(
        apps.map((a) => a.appId),
        onProgress,
      );
      setNonSteamStatus(`Removed ${pluralize(apps.length, "non-Steam game")}`);
      await recountAfterStoreSettles(apps.length, refreshNonSteam);
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
    foreignApps,
    unidentifiedCount,
    whitelistedIds,
    disabledDefaults,
    customNames,
    settingsLoaded,
    orphanedGridImages,
    removedGames,
    busy,
    busyLabel,
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
    recordRemovedGamesScan: setRemovedGames,
  };
}
