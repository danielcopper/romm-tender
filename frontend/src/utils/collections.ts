/**
 * Steam collection management for RomM platforms.
 * Uses Steam's internal collectionStore API.
 *
 * Collection names are machine-scoped to prevent cross-device conflicts
 * when Steam Cloud syncs collections: "RomM: Platform (hostname)"
 */

import { logInfo, logWarn, logError } from "../api/backend";
import { foldCollectionName } from "./collectionName";

let _hostname = "";

export async function getHostname(): Promise<string> {
  if (_hostname) return _hostname;
  try {
    const info = await SteamClient.System.GetSystemInfo();
    _hostname = info.sHostname || "unknown";
  } catch {
    _hostname = "unknown";
  }
  return _hostname;
}

/** The Steam name of a platform's collection on this machine. */
export function platformCollectionName(platformName: string, hostname: string): string {
  return `RomM: ${platformName} (${hostname})`;
}

/** The Steam name of a RomM collection's collection on this machine, from its backend key. */
export function rommCollectionName(key: string, hostname: string): string {
  return `RomM: [${key}] (${hostname})`;
}

function getOverviews(appIds: number[]): AppStoreOverview[] {
  const overviews: AppStoreOverview[] = [];
  for (const appId of appIds) {
    if (typeof appStore !== "undefined") {
      const overview = appStore.GetAppOverviewByAppID(appId);
      if (overview) {
        overviews.push(overview);
        continue;
      }
    }
    // Fallback: construct a minimal overview
    overviews.push({ appid: appId, display_name: "", strDisplayName: "" });
  }
  return overviews;
}

/**
 * Replace an existing collection's apps. Returns false when the signal aborted
 * mid-write, so the caller stops the whole run instead of moving to the next
 * collection.
 */
async function replaceCollectionApps(
  existing: SteamCollection,
  overviews: AppStoreOverview[],
  signal?: AbortSignal,
): Promise<boolean> {
  const existingApps = existing.allApps;
  if (existingApps.length > 0) {
    if (signal?.aborted) return false;
    existing.AsDragDropCollection().RemoveApps(existingApps);
  }
  if (signal?.aborted) return false;
  existing.AsDragDropCollection().AddApps(overviews);
  if (signal?.aborted) return false;
  await existing.Save();
  return true;
}

/** Create a new collection holding exactly *overviews*. False means aborted mid-write. */
async function createCollectionWithApps(
  collectionName: string,
  overviews: AppStoreOverview[],
  signal?: AbortSignal,
): Promise<boolean> {
  const collection = collectionStore.NewUnsavedCollection(collectionName, undefined, []);
  if (signal?.aborted) return false;
  collection.AsDragDropCollection().AddApps(overviews);
  if (signal?.aborted) return false;
  await collection.Save();
  return true;
}

/** Point one collection name at exactly *overviews*, creating it if it is new. */
async function saveCollection(
  collectionName: string,
  noun: string,
  overviews: AppStoreOverview[],
  appCount: number,
  signal?: AbortSignal,
): Promise<boolean> {
  // A collection Steam holds as the same one ("RomM: [7 Up] (host)" vs
  // "RomM: [7 up] (host)") must be UPDATED, not shadowed by a colliding new
  // create that then overwrites it and loses its games (#1569).
  const target = foldCollectionName(collectionName);
  const existing = collectionStore.userCollections.find((c) => foldCollectionName(c.displayName) === target);

  if (existing) {
    logInfo(`Updating ${noun} "${collectionName}" with ${appCount} apps`);
    if (!(await replaceCollectionApps(existing, overviews, signal))) return false;
  } else {
    logInfo(`Creating ${noun} "${collectionName}" with ${appCount} apps`);
    if (!(await createCollectionWithApps(collectionName, overviews, signal))) return false;
  }
  logInfo(`Successfully saved ${noun} "${collectionName}"`);
  return true;
}

/**
 * Write one family of collections, reporting progress per entry. A failure on
 * one collection is logged and the rest still run; an abort stops the run.
 */
async function saveCollectionFamily(
  entries: Array<[string, number[]]>,
  noun: string,
  collectionNameFor: (source: string) => string,
  onProgress?: (current: number, total: number, name: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  let idx = 0;
  for (const [source, appIds] of entries) {
    if (signal?.aborted) return;
    idx++;
    onProgress?.(idx, entries.length, source);
    const collectionName = collectionNameFor(source);
    const overviews = getOverviews(appIds);

    try {
      if (!(await saveCollection(collectionName, noun, overviews, appIds.length, signal))) return;
    } catch (colErr) {
      logError(`Failed to save ${noun} "${collectionName}": ${colErr}`);
    }
  }
}

export async function createOrUpdateCollections(
  platformAppIds: Record<string, number[]>,
  onProgress?: (current: number, total: number, name: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, skipping collections");
      return;
    }

    const hostname = await getHostname();
    if (signal?.aborted) return;
    logInfo(
      `Creating/updating collections for platforms: ${Object.keys(platformAppIds).join(", ")} (hostname: ${hostname})`,
    );

    await saveCollectionFamily(
      Object.entries(platformAppIds),
      "collection",
      (platformName) => platformCollectionName(platformName, hostname),
      onProgress,
      signal,
    );
  } catch (e) {
    logError(`Failed to update collections: ${e}`);
  }
}

export async function createOrUpdateRomMCollections(
  collectionAppIds: Record<string, number[]>,
  onProgress?: (current: number, total: number, name: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, skipping RomM collections");
      return;
    }

    const hostname = await getHostname();
    if (signal?.aborted) return;
    logInfo(`Creating/updating RomM collections: ${Object.keys(collectionAppIds).join(", ")} (hostname: ${hostname})`);

    await saveCollectionFamily(
      Object.entries(collectionAppIds),
      "RomM collection",
      (collName) => rommCollectionName(collName, hostname),
      onProgress,
      signal,
    );
  } catch (e) {
    logError(`Failed to update RomM collections: ${e}`);
  }
}

/**
 * The collections {@link addAppToCollections} created in one run, by folded
 * name, consulted where `collectionStore.userCollections` does not list one —
 * why is docs/architecture/steam-non-steam-shortcuts.md, "Collection
 * management". Emptied when a run starts ({@link startCollectionsRun}), and
 * keyed by `run_id` besides.
 */
let _createdThisRun: { runId: string; byFold: Map<string, SteamCollection> } | null = null;

/**
 * How long the add-only pass gathers games before it writes their collections
 * again. Why the writes are grouped, and what one costs:
 * docs/architecture/steam-non-steam-shortcuts.md, "Collection management".
 */
export const COLLECTION_WRITE_INTERVAL_MS = 2000;

/** The games {@link addAppToCollections} gathered since the last write, by collection name. */
let _pending: { runId: string; appIdsByName: Map<string, number[]> } | null = null;
/** When the add-only pass last wrote, or `null` where this run has not written yet. */
let _lastWriteAt: number | null = null;

/** Begin *runId*'s record of the collections it creates, forgetting any earlier run's; its first game is written at once. */
export function startCollectionsRun(runId: string): void {
  beginCollectionsRun(runId);
}

function beginCollectionsRun(runId: string): Map<string, SteamCollection> {
  const byFold = new Map<string, SteamCollection>();
  _createdThisRun = { runId, byFold };
  _lastWriteAt = null;
  return byFold;
}

function collectionsCreatedIn(runId: string): Map<string, SteamCollection> {
  return _createdThisRun?.runId === runId ? _createdThisRun.byFold : beginCollectionsRun(runId);
}

/**
 * Add *appIds* to one collection, creating it holding them if it is missing.
 * Only the appIds it does not hold yet are written; nothing is removed.
 * *created* records the collections a run created, or is `null` where only the
 * store is asked.
 */
async function addAppsToCollection(
  appIds: number[],
  collectionName: string,
  created: Map<string, SteamCollection> | null,
): Promise<void> {
  const target = foldCollectionName(collectionName);
  const existing =
    collectionStore.userCollections.find((c) => foldCollectionName(c.displayName) === target) ?? created?.get(target);
  const missing = existing ? appIds.filter((appId) => !existing.apps.has(appId)) : appIds;
  if (missing.length === 0) return;
  const overviews = getOverviews(missing);
  if (existing) {
    existing.AsDragDropCollection().AddApps(overviews);
    await existing.Save();
    return;
  }
  const collection = collectionStore.NewUnsavedCollection(collectionName, undefined, []);
  collection.AsDragDropCollection().AddApps(overviews);
  await collection.Save();
  created?.set(target, collection);
  logInfo(`Created collection "${collectionName}" with ${missing.length} apps`);
}

/**
 * File one shortcut into the collections the backend named for it, while a sync
 * runs. The game is gathered with the others for its collections, and every
 * gathered collection is written when {@link COLLECTION_WRITE_INTERVAL_MS} has
 * passed since the last write — at once for a run's first game — or when
 * {@link flushCollections} is called. Only adds: a collection that is missing is
 * created holding its gathered games, and nothing is ever removed — the
 * complete set of each collection is written once a run completes
 * (`onSyncComplete`). Nothing here throws.
 */
export async function addAppToCollections(
  appId: number,
  platformCollection: string | null,
  rommCollections: string[],
  runId: string,
): Promise<void> {
  if (typeof collectionStore === "undefined") return;
  const hostname = await getHostname();
  const names = [
    ...(platformCollection === null ? [] : [platformCollectionName(platformCollection, hostname)]),
    ...rommCollections.map((key) => rommCollectionName(key, hostname)),
  ];
  if (_pending?.runId !== runId) await flushCollections();
  collectionsCreatedIn(runId);
  const pending = _pending?.runId === runId ? _pending : { runId, appIdsByName: new Map<string, number[]>() };
  _pending = pending;
  for (const name of names) {
    const appIds = pending.appIdsByName.get(name);
    if (appIds) appIds.push(appId);
    else pending.appIdsByName.set(name, [appId]);
  }
  if (_lastWriteAt === null || Date.now() - _lastWriteAt >= COLLECTION_WRITE_INTERVAL_MS) await flushCollections();
}

/**
 * Write every collection {@link addAppToCollections} has gathered games for and
 * not written yet: one `AddApps` and one `Save` each. Called at the end of each
 * apply chunk, before its acknowledgement, and before the end-of-run write, so
 * nothing gathered is lost. A failure on one collection is logged and the
 * others still run; nothing here throws.
 */
export async function flushCollections(): Promise<void> {
  const pending = _pending;
  if (!pending || pending.appIdsByName.size === 0 || typeof collectionStore === "undefined") return;
  _pending = { runId: pending.runId, appIdsByName: new Map() };
  _lastWriteAt = Date.now();
  // A run's games reach the record of the collections that run created, and no
  // other run's: games still gathered when the next run started go by the store.
  const created = _createdThisRun?.runId === pending.runId ? _createdThisRun.byFold : null;
  for (const [name, appIds] of pending.appIdsByName) {
    try {
      await addAppsToCollection(appIds, name, created);
    } catch (e) {
      logError(`Failed to add ${appIds.length} apps to collection "${name}": ${e}`);
    }
  }
}

/**
 * Add the games of a cancelled run's maps to their collections, removing none:
 * such a run's maps hold only what it reached, so they are not each collection's
 * complete set. The collections the run created are consulted as during the
 * run: `sync_complete` names no run, and no other run starts before it. A
 * failure on one collection is logged and the rest still run; an abort stops
 * the run.
 */
export async function addToCollections(
  platformAppIds: Record<string, number[]>,
  collectionAppIds: Record<string, number[]>,
  signal?: AbortSignal,
): Promise<void> {
  if (typeof collectionStore === "undefined") return;
  const hostname = await getHostname();
  const entries: Array<[string, number[]]> = [
    ...Object.entries(platformAppIds).map(([name, ids]): [string, number[]] => [
      platformCollectionName(name, hostname),
      ids,
    ]),
    ...Object.entries(collectionAppIds).map(([key, ids]): [string, number[]] => [
      rommCollectionName(key, hostname),
      ids,
    ]),
  ];
  for (const [name, appIds] of entries) {
    if (signal?.aborted) return;
    try {
      await addAppsToCollection(appIds, name, _createdThisRun?.byFold ?? null);
    } catch (e) {
      logError(`Failed to add ${appIds.length} apps to collection "${name}": ${e}`);
    }
  }
}

export async function clearPlatformCollection(platformName: string, signal?: AbortSignal): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, cannot clear platform collection");
      return;
    }
    const hostname = await getHostname();
    if (signal?.aborted) return;
    const scopedName = platformCollectionName(platformName, hostname);
    const legacyName = `RomM: ${platformName}`;

    // A case variant of this platform's collection is the SAME Steam
    // collection and must still be found for deletion (#1569).
    const scopedTarget = foldCollectionName(scopedName);
    const legacyTarget = foldCollectionName(legacyName);

    // Delete the machine-scoped collection
    const scoped = collectionStore.userCollections.find((c) => foldCollectionName(c.displayName) === scopedTarget);
    if (scoped) {
      logInfo(`Deleting collection "${scopedName}" (id=${scoped.id})`);
      if (signal?.aborted) return;
      await scoped.Delete();
    }

    // Also clean up legacy collection (without hostname suffix) if it exists
    const legacy = collectionStore.userCollections.find((c) => foldCollectionName(c.displayName) === legacyTarget);
    if (legacy) {
      logInfo(`Deleting legacy collection "${legacyName}" (id=${legacy.id})`);
      if (signal?.aborted) return;
      await legacy.Delete();
    }

    if (!scoped && !legacy) {
      logInfo(`Collection "${scopedName}" not found, nothing to clear`);
    }
  } catch (e) {
    logError(`Failed to clear platform collection: ${e}`);
  }
}

export async function clearAllRomMCollections(signal?: AbortSignal): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, cannot clear collections");
      return;
    }
    const hostname = await getHostname();
    if (signal?.aborted) return;
    const suffix = ` (${hostname})`;
    const foldedSuffix = foldCollectionName(suffix);

    // Match collections belonging to this machine OR legacy ones without any hostname suffix.
    // Covers both platform collections ("RomM: PlatformName (hostname)") and
    // RomM collection-based collections ("RomM: [CollectionName] (hostname)").
    // Legacy collections match "RomM: ..." but do NOT have a parenthesized suffix.
    // This avoids deleting collections from other devices like "RomM: N64 (othermachine)".
    // Prefix + host-suffix are folded too: a case variant of one of our names
    // is the same Steam collection and must still be swept (#1569).
    const rommCollections = collectionStore.userCollections.filter((c) => {
      const folded = foldCollectionName(c.displayName);
      if (!folded.startsWith("romm: ")) return false;
      // This machine's scoped collections (both platform and RomM collection style)
      if (folded.endsWith(foldedSuffix)) return true;
      // Legacy collections: start with "RomM: " but have no " (...)" suffix at all
      if (!/\s\([^)]+\)$/.test(c.displayName)) return true;
      return false;
    });

    logInfo(`Deleting ${rommCollections.length} RomM collections (hostname: ${hostname})`);
    for (const c of rommCollections) {
      if (signal?.aborted) return;
      logInfo(`Deleting collection "${c.displayName}" (id=${c.id})`);
      await c.Delete();
    }
  } catch (e) {
    logError(`Failed to clear collections: ${e}`);
  }
}
