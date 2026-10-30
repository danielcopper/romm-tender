import type { SyncAddItem } from "../types";
import { getAppIdRomIdMap, syncHeartbeat, logError, logInfo, logWarn } from "../api/backend";
import { delay } from "./pacedOps";
import { readShortcutFile, type ShortcutFileReader, type ShortcutFileReading } from "./shortcutFile";
import { TimeoutError, withTimeout } from "./withTimeout";

/**
 * Ownership marker: Tender-managed shortcuts launch through the
 * `tender-rom-launcher` exec wrapper. A shortcut whose `strShortcutExe` ends
 * with this suffix is ours regardless of its launch options (which carry the
 * full RetroDECK command, not a `romm:<id>` marker).
 *
 * This one ending is the whole of ownership. A shortcut naming any other
 * launcher is foreign, including one an earlier version of this program wrote.
 */
const ROM_LAUNCHER_SUFFIX = "/bin/tender-rom-launcher";

export function isRomMShortcutDetails(details: SteamAppDetails | null): details is SteamAppDetails {
  return (
    typeof details?.strShortcutExe === "string" &&
    details.strShortcutExe.replace(/^"|"$/g, "").endsWith(ROM_LAUNCHER_SUFFIX)
  );
}

const HEARTBEAT_INTERVAL_MS = 10_000;

/**
 * Resolve a shortcut's `SteamAppDetails` via the one-shot RegisterForAppDetails
 * pattern. Resolves with the first details object the runtime delivers, or
 * ``null`` if none arrives within ``timeoutMs`` (the runtime can fire with no
 * details before the app's data loads — those early ``undefined`` fires are
 * ignored).
 */
export function getAppDetails(appId: number, timeoutMs = 2000): Promise<SteamAppDetails | null> {
  return new Promise((resolve) => {
    let resolved = false;
    // Declared with `let` BEFORE RegisterForAppDetails so a (hypothetical)
    // synchronous callback fire can't hit the temporal dead zone when finish()
    // reads reg.
    // eslint-disable-next-line prefer-const -- the `let`-before-register ordering is the TDZ guard; `prefer-const` only sees the single assignment and can't model the closure reading `reg` before the assignment line executes.
    let reg: { unregister: () => void } | undefined;
    const finish = (value: SteamAppDetails | null) => {
      if (resolved) return;
      resolved = true;
      reg?.unregister();
      resolve(value);
    };
    reg = SteamClient.Apps.RegisterForAppDetails(appId, (details) => {
      if (details) finish(details);
    });
    setTimeout(() => finish(null), timeoutMs);
  });
}

/**
 * How long a confirm waits for Steam's shortcut file to be read. An endpoint
 * call waits out a backend that is down (`api/hostSocket.ts`), so without a
 * bound of its own the confirm would wait with it.
 */
const FILE_READ_BOUND_MS = 5000;

/**
 * Set an existing shortcut's launch options and confirm the write landed.
 *
 * Every Steam ``Set*`` returns ``void`` with no success signal, so we fire
 * ``SetAppLaunchOptions`` then poll ``RegisterForAppDetails`` until the
 * read-back ``strLaunchOptions`` matches ``value``. When no matching report
 * arrives within ``timeoutMs``, the shortcut's details are read once more
 * through {@link getAppDetails}, bounded by ``timeoutMs`` again, and when that
 * re-read does not show ``value`` either, Steam's shortcut file is read through
 * ``readFile`` — by default one backend call for this shortcut alone — bounded
 * by {@link FILE_READ_BOUND_MS}, and a read that does not answer in time counts
 * as one that could not be done. Resolves ``true`` when any of the three shows
 * ``value``, ``false`` otherwise. Setting ``""`` (the uninstalled-placeholder
 * value) is valid and confirms against an empty read-back. A confirm only the
 * re-read or the file gave is logged at info, the file's with how long it
 * waited for the reading; before resolving ``false`` it logs a warning saying
 * what the re-read and the file each showed, and wherever either shows a value,
 * the value written beside it, both in full.
 */
export async function setLaunchOptionsConfirmed(
  appId: number,
  value: string,
  timeoutMs = 2000,
  readFile: ShortcutFileReader = () => readShortcutFile([appId]),
): Promise<boolean> {
  if (await writeAndAwaitReport(appId, value, timeoutMs)) return true;
  const details = await getAppDetails(appId, timeoutMs);
  const reread =
    details === null
      ? `the re-read delivered no details within ${timeoutMs}ms`
      : `the re-read shows ${JSON.stringify(launchOptionsOf(details))}`;
  if (details !== null && launchOptionsOf(details) === value) {
    logInfo(`setLaunchOptionsConfirmed: appId ${appId} confirmed by the re-read after the report missed the wait`);
    return true;
  }
  const started = Date.now();
  const reading = await withTimeout(readFile(), FILE_READ_BOUND_MS).catch((e: unknown): ShortcutFileReading => ({
    status: "unreadable",
    why: e instanceof TimeoutError ? `no answer within ${FILE_READ_BOUND_MS}ms` : String(e),
  }));
  const held = reading.status === "read" ? reading.launchOptions.get(appId) : undefined;
  if (held === value) {
    logInfo(
      `setLaunchOptionsConfirmed: appId ${appId} confirmed by Steam's shortcut file after ${reread}; ` +
        `waited ${Date.now() - started}ms for the file`,
    );
    return true;
  }
  const written = details === null && held === undefined ? "" : `, the write was ${JSON.stringify(value)}`;
  logWarn(
    `setLaunchOptionsConfirmed: appId ${appId} not confirmed: ${reread}${written}; ${describeFile(reading, appId)}`,
  );
  return false;
}

function describeFile(reading: ShortcutFileReading, appId: number): string {
  if (reading.status === "unreadable") return `the file could not be read (${reading.why})`;
  const held = reading.launchOptions.get(appId);
  return held === undefined ? "the file holds no such shortcut" : `the file shows ${JSON.stringify(held)}`;
}

function launchOptionsOf(details: SteamAppDetails): string {
  return details.strLaunchOptions ?? details.LaunchOptions ?? "";
}

function writeAndAwaitReport(appId: number, value: string, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    let resolved = false;
    // Declared with `let` BEFORE RegisterForAppDetails so a (hypothetical)
    // synchronous callback fire can't hit the temporal dead zone when finish()
    // reads reg.
    // eslint-disable-next-line prefer-const -- the `let`-before-register ordering is the TDZ guard; `prefer-const` only sees the single assignment and can't model the closure reading `reg` before the assignment line executes.
    let reg: { unregister: () => void } | undefined;
    const finish = (matched: boolean) => {
      if (resolved) return;
      resolved = true;
      reg?.unregister();
      resolve(matched);
    };

    SteamClient.Apps.SetAppLaunchOptions(appId, value);

    reg = SteamClient.Apps.RegisterForAppDetails(appId, (details) => {
      if (!details) return;
      if (launchOptionsOf(details) === value) finish(true);
    });

    setTimeout(() => finish(false), timeoutMs);
  });
}

/**
 * Scan Steam's live shortcut store and return the appIds of every RomM-owned
 * shortcut — those whose `strShortcutExe` ends with `/bin/tender-rom-launcher` (the
 * live-in-Steam ownership marker), regardless of any backend binding.
 *
 * Returns the raw live appId list, or `null` when the scan could **not** run
 * because Steam's shortcut store was unreadable (`collectionStore` /
 * `deckDesktopApps.apps` absent). Its callers ask which shortcuts are OURS: the
 * sync's existing-shortcut map and its orphan-adoption pool
 * (`syncManager.ts`), and the remove-all sweep's search for shortcuts the
 * backend did not list (`bigpicture/data/useDataPage.ts`). Two of them tell
 * `null` from `[]`: the orphan-adoption pool disables adoption for the run on
 * `null`, and the sweep falls back to the backend's list, where `[]` would say
 * "looked, found none". The existing-shortcut map
 * ({@link getExistingRomMShortcuts}) answers an empty map for both.
 *
 * The sweep itself is {@link scanShortcutOwnership}; this drops the entries it
 * could not identify, which is right for a caller asking only which shortcuts
 * are OURS and wrong for one deciding which are NOT — the sync-start reconcile
 * reads the sweep itself for that reason.
 */
export async function getLiveRomMShortcutAppIds(): Promise<number[] | null> {
  const scan = await scanShortcutOwnership();
  return scan === null ? null : scan.owned;
}

/**
 * What one sweep of Steam's shortcut store established about each entry.
 *
 * `owned` is proven ours; `unresolved` is the entries Steam did not answer for
 * before `getAppDetails` gave up. Everything else — the entries this sweep saw
 * and proved are not ours — is neither, and is the only set a caller may treat
 * as foreign.
 */
export interface ShortcutOwnership {
  owned: number[];
  unresolved: number[];
}

/**
 * The same sweep as {@link getLiveRomMShortcutAppIds}, keeping the entries it
 * could not identify apart from the ones it identified as not ours.
 *
 * `getAppDetails` resolves `null` when Steam does not answer within its
 * timeout, which on a large library is ordinary rather than exotic. Reading
 * that absence as an exe of `""` classifies a shortcut of OURS as not-ours, so
 * a caller deciding what is foreign needs the third answer: nothing was
 * established about this entry. Whether the whole store could be read is the
 * `null` return, exactly as above.
 *
 * Detection runs in parallel batches (RegisterForAppDetails is ~2s serial per
 * shortcut); a heartbeat every 10s keeps the backend's per-unit timeout from
 * cancelling a long scan over a large library.
 */
export async function scanShortcutOwnership(): Promise<ShortcutOwnership | null> {
  if (typeof collectionStore === "undefined") return null;

  const deckApps = collectionStore.deckDesktopApps?.apps;
  if (!deckApps) return null;

  const appIds = Array.from(deckApps.keys());

  const owned: number[] = [];
  const unresolved: number[] = [];
  const CONCURRENCY = 10;
  let lastHeartbeat = Date.now();
  for (let i = 0; i < appIds.length; i += CONCURRENCY) {
    const batch = appIds.slice(i, i + CONCURRENCY);
    const entries = await Promise.all(
      batch.map((appId) => getAppDetails(appId).then((details) => ({ appId, details }))),
    );
    for (const { appId, details } of entries) {
      if (!details) unresolved.push(appId);
      else if ((details.strShortcutExe ?? "").endsWith(ROM_LAUNCHER_SUFFIX)) owned.push(appId);
    }
    if (Date.now() - lastHeartbeat > HEARTBEAT_INTERVAL_MS) {
      syncHeartbeat().catch(() => {});
      lastHeartbeat = Date.now();
    }
  }

  return { owned, unresolved };
}

/**
 * Enumerate the appIds of ALL live non-Steam shortcuts — RomM-owned AND
 * foreign — from Steam's collection store. This is the keep-set for the
 * orphaned grid-image cleanup, so unlike `getLiveRomMShortcutAppIds` there is
 * deliberately NO launcher filter: every live shortcut's artwork must be
 * protected, not just ours.
 *
 * Returns `null` when the scan could not run (`collectionStore` /
 * `deckDesktopApps.apps` unreadable). The `null`-vs-`[]` distinction is
 * load-bearing: `[]` means "scan ran, zero shortcuts exist" (a real keep-set),
 * `null` means "could not look" — callers must abort and delete nothing.
 */
export function getAllNonSteamShortcutAppIds(): number[] | null {
  if (typeof collectionStore === "undefined") return null;

  const deckApps = collectionStore.deckDesktopApps?.apps;
  if (!deckApps) return null;

  return Array.from(deckApps.keys());
}

/**
 * Scan all non-Steam shortcuts and return those managed by RomM.
 *
 * A shortcut is RomM-owned when BOTH hold: its `strShortcutExe` ends with
 * `/bin/tender-rom-launcher` (live-in-Steam ownership marker) AND its appId is bound
 * to a rom_id in the backend's `get_app_id_rom_id_map()` (the authoritative
 * rom_id↔appId binding now that launch options no longer carry the id). After
 * a DB reset the backend map is empty, so our shortcuts are detected by exe but
 * remain unmapped — they're treated as orphans and re-sync recreates them.
 *
 * *preScanned* lets a caller that already ran the once-per-run
 * `getLiveRomMShortcutAppIds()` sweep hand its result in, so the expensive
 * per-shortcut `RegisterForAppDetails` scan runs at most once per sync run
 * (#1366). Omit it to scan internally; a `null` *preScanned* (store unreadable)
 * yields an empty map, exactly like a `null` internal scan.
 *
 * Returns Map<romId, steamAppId>.
 */
export async function getExistingRomMShortcuts(preScanned?: number[] | null): Promise<Map<number, number>> {
  const result = new Map<number, number>();

  const ourAppIds = preScanned !== undefined ? preScanned : await getLiveRomMShortcutAppIds();
  if (!ourAppIds || ourAppIds.length === 0) return result;

  // Resolve rom_id for each of our appIds via the authoritative backend map.
  // The map is keyed by appId-string → rom_id; keep only the intersection of
  // (our exe) AND (bound in the backend).
  let appIdToRomId: Record<string, number>;
  try {
    appIdToRomId = await getAppIdRomIdMap();
  } catch (e) {
    logError(`getExistingRomMShortcuts: failed to load app-id↔rom-id map: ${e}`);
    return result;
  }

  for (const appId of ourAppIds) {
    const romId = appIdToRomId[String(appId)];
    if (typeof romId === "number") result.set(romId, appId);
  }

  return result;
}

/**
 * Poll ``appStore`` until the new shortcut's overview is registered, or
 * ``timeoutMs`` elapses. A freshly created shortcut's overview appears
 * asynchronously; polling for readiness (MoonDeck pattern) replaces a fixed
 * worst-case wait — the common case proceeds in ~100ms instead of a blind
 * 500ms, and the timeout ceiling keeps the net behaviour of the old wait when
 * the overview is slow to appear. Resolves ``true`` once the overview exists,
 * ``false`` on timeout (the caller proceeds regardless).
 */
async function waitForAppOverview(appId: number, timeoutMs: number): Promise<boolean> {
  const POLL_INTERVAL_MS = 100;
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (appStore.GetAppOverviewByAppID(appId)) return true;
    if (Date.now() >= deadline) return false;
    await delay(POLL_INTERVAL_MS);
  }
}

/**
 * Add a single Steam shortcut. Returns the new steam app_id, or null on failure.
 */
export async function addShortcut(data: SyncAddItem): Promise<number | null> {
  try {
    // AddShortcut ignores most params (confirmed by MoonDeck plugin) —
    // must use Set* calls after creation to apply name, exe, startDir, launchOptions.
    const appId = await SteamClient.Apps.AddShortcut(data.name, data.exe, "", "");

    if (!appId) return null;

    // Wait for Steam to register the new app's overview before setting
    // properties. Poll for readiness instead of a blind 500ms; on timeout,
    // proceed anyway (same net behaviour as the old fixed wait).
    if (!(await waitForAppOverview(appId, 1000))) {
      logInfo(`addShortcut: overview for ${appId} not ready within 1000ms; proceeding anyway`);
    }

    SteamClient.Apps.SetShortcutName(appId, data.name);
    SteamClient.Apps.SetShortcutExe(appId, data.exe);
    SteamClient.Apps.SetShortcutStartDir(appId, data.start_dir);
    // A freshly created shortcut's launch options are already empty. For an
    // uninstalled ROM (launch_options ""), there is nothing to write or confirm,
    // so skip both SetAppLaunchOptions and the confirm poll — the confirm poll's
    // RegisterForAppDetails forces Steam to load+cache a fat AppDetails object
    // per call, so skipping it for the majority uninstalled case avoids that
    // heap hit. A non-empty command (installed ROM) still takes the confirmed
    // write (#827).
    if (data.launch_options !== "") {
      await setLaunchOptionsConfirmed(appId, data.launch_options);
    }

    return appId;
  } catch (e) {
    logError(`Failed to add shortcut for ${data.name}: ${e}`);
    return null;
  }
}

/**
 * Remove a single Steam shortcut by app_id.
 */
export function removeShortcut(appId: number): void {
  try {
    SteamClient.Apps.RemoveShortcut(appId);
  } catch (e) {
    logError(`Failed to remove shortcut ${appId}: ${e}`);
  }
}

/** Steam's live non-Steam shortcut map, or `null` when it can't be read. */
function readDesktopAppStore(): Map<number, unknown> | null {
  if (typeof collectionStore === "undefined") return null;
  return collectionStore.deckDesktopApps?.apps ?? null;
}

/**
 * Which of *appIds* Steam's shortcut store no longer holds, read once Steam has settled the removals just made.
 *
 * Polls every 100ms until no appId in *removed* (the ones a removal was tried for) is listed any more, or
 * *timeoutMs* has passed, then answers every appId of *appIds* absent from that last read — one tried for or
 * not. An unreadable store answers none: nothing was seen gone.
 */
export async function shortcutsGoneFromSteam(
  appIds: readonly number[],
  removed: readonly number[],
  timeoutMs = 3000,
): Promise<number[]> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const apps = readDesktopAppStore();
    if (!apps) return [];
    if (removed.every((appId) => !apps.has(appId)) || Date.now() >= deadline) {
      return appIds.filter((appId) => !apps.has(appId));
    }
    await delay(100);
  }
}

export interface ShortcutRemovalOutcome {
  status: "confirmed" | "not_attempted" | "attempted_unconfirmed";
}

/** Distinguish a pre-mutation refusal from an unconfirmed mutation attempt. */
export async function removeShortcutConfirmedOutcome(
  appId: number,
  timeoutMs = 3000,
  ownershipAlreadyChecked = false,
): Promise<ShortcutRemovalOutcome> {
  const store = readDesktopAppStore();
  if (!store?.has(appId)) return { status: "not_attempted" };
  if (!ownershipAlreadyChecked && !isRomMShortcutDetails(await getAppDetails(appId))) {
    return { status: "not_attempted" };
  }
  try {
    SteamClient.Apps.RemoveShortcut(appId);
  } catch (e) {
    logError(`Failed to remove shortcut ${appId}: ${e}`);
    return { status: "not_attempted" };
  }
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const apps = readDesktopAppStore();
    if (!apps) return { status: "attempted_unconfirmed" };
    if (!apps.has(appId)) return { status: "confirmed" };
    if (Date.now() >= deadline) return { status: "attempted_unconfirmed" };
    await delay(100);
  }
}
