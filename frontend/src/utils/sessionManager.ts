/**
 * Session manager — detects game start/stop for RomM shortcuts and triggers
 * save sync + playtime tracking via backend endpoints.
 *
 * Uses SteamClient.GameSessions.RegisterForAppLifetimeNotifications to detect
 * game lifecycle events — the notification's own `unAppID` identifies the app on
 * both edges. The guarded `runningApps` reader (`SteamUIStore.RunningApps`) is
 * used only for LIVENESS — at reload-adoption and in {@link readGameRunning} —
 * never to identify a launching app.
 */

import { showToast } from "./toast";
import {
  recordSessionStart,
  getAppIdRomIdMap,
  finalizeGameSession,
  logInfo,
  logWarn,
  logError,
  debugLog,
} from "../api/backend";
import { saveSyncToastBody } from "./saveSyncToast";
import { setMigrationStatus } from "./migrationStore";
import { updatePlaytimeDisplay } from "./metadataPatches";
import { detach } from "./detach";
import { readRunningApps, type RunningAppsReading } from "./runningApps";
import { delay } from "./pacedOps";
import { LOCAL_CALL_LIMIT_MS, SERVER_CALL_LIMIT_MS } from "./launchGate";
import { withTimeout } from "./withTimeout";

// Active session tracking — ONE ENTRY PER RUNNING APP (#1624). Two RomM games at
// once each hold their own entry, so a second start no longer displaces the
// first (which dropped its playtime and its post-exit save sync entirely).
export interface ActiveSession {
  /** The Steam app that opened the session — what a lifecycle stop is matched against. */
  appId: number;
  romId: number;
  /** Wall-clock start, mirrored into the durable breadcrumb. */
  startMs: number;
}

// Keyed by appId, because that is what every write path is handed: the lifetime
// notification's `unAppID` on both edges, and the running-app reading at
// adoption. The stop path in particular must match against the appId RECORDED
// WITH THE SESSION (#1621) — a romId key would force a reverse lookup through
// the `appId → romId` map, which can go stale mid-session and would then drop a
// live session instead of an unrelated one.
const activeSessions = new Map<number, ActiveSession>();

// Serialization chain — ensures lifecycle events don't interleave
let lifecycleChain: Promise<void> = Promise.resolve();

// Cached app ID -> rom ID map, refreshed on init, when an app starts, and by the
// launch watcher once for an appId Tender owns that the map does not hold.
let appIdToRomId: Record<string, number> = {};

function getRomIdForApp(appId: number): number | null {
  const romId = appIdToRomId[String(appId)];
  return romId ?? null;
}

// The ROM Tender's button named for an app right before it started it. Which
// source names a start's ROM, why, and for how long:
// `docs/architecture/save-file-sync-architecture.md`, "App ID to ROM ID mapping".
const NOTED_START_WINDOW_MS = 60_000;
const notedStarts = new Map<number, { romId: number; notedAtMs: number }>();

/** Name the ROM `appId` belongs to, for the start Tender's button is about to make. */
export function noteAppRom(appId: number, romId: number): void {
  notedStarts.set(appId, { romId, notedAtMs: Date.now() });
}

/** One-shot, and only within the window. */
function takeNotedRom(appId: number): number | null {
  const noted = notedStarts.get(appId);
  notedStarts.delete(appId);
  if (!noted || Date.now() - noted.notedAtMs > NOTED_START_WINDOW_MS) return null;
  return noted.romId;
}

/**
 * Snapshot of the cached appId -> romId map (the same shape the backend's
 * `get_app_id_rom_id_map` endpoint returns — string-keyed appIds). The global
 * launch watcher reads this synchronously, so its already-running guard decides
 * before the start is cancelled, with no await in between. Returns the live
 * reference; callers treat it as read-only.
 */
export function getAppIdRomIdMapSnapshot(): Record<string, number> {
  return appIdToRomId;
}

/**
 * Does this manager currently track an active session for `romId`? One signal
 * of {@link readGameRunning}; a caller asking whether a game is running asks
 * that instead.
 */
export function isSessionActive(romId: number): boolean {
  for (const session of activeSessions.values()) {
    if (session.romId === romId) return true;
  }
  return false;
}

// The apps whose lifetime stop has been observed since their last observed
// start. Written straight off the notification, never on the lifecycle chain:
// the chain can be held by a finalize's post-exit sync, and a press in that
// window must already see the stop.
const stoppedSinceStart = new Set<number>();

/** The signal that answered {@link readGameRunning}. */
export type GameRunningSignal = "session" | "store" | "stop" | "none";

export interface GameRunningReading {
  running: boolean;
  decidedBy: GameRunningSignal;
  /** What every signal said, for the log line of a caller that acts on the answer. */
  diagnostics: string;
}

/**
 * Is this game running? Answers synchronously, with no await.
 *
 * An active session for `romId` answers yes. Otherwise
 * `SteamUIStore.RunningApps` answers, unless a lifetime stop for `appId` has
 * been observed since its last observed start. Who asks, why a stop overrules
 * the store, and why the store is still asked:
 * `docs/architecture/save-file-sync-architecture.md`, "Is the game running".
 */
export function readGameRunning(appId: number, romId: number | null | undefined): GameRunningReading {
  const sessionActive = romId != null && isSessionActive(romId);
  const stopObserved = stoppedSinceStart.has(appId);
  const store = readRunningApps();
  const listed = store.apps.some((app) => app.appid === appId);
  let decidedBy: GameRunningSignal;
  if (sessionActive) decidedBy = "session";
  else if (listed) decidedBy = stopObserved ? "stop" : "store";
  else decidedBy = "none";
  return {
    running: decidedBy === "session" || decidedBy === "store",
    decidedBy,
    diagnostics:
      `decided by ${decidedBy}: session=${sessionActive ? "active" : "none"}, ` +
      `stopObserved=${stopObserved ? "yes" : "no"}, ${store.diagnostics}`,
  };
}

/**
 * Re-read the appId -> romId map from the backend. A failed read is logged and
 * leaves the map as it was.
 */
export async function refreshAppIdMap(): Promise<void> {
  await readAppIdMap();
}

/** {@link refreshAppIdMap}, answering whether the backend answered and the map was replaced. */
async function readAppIdMap(): Promise<boolean> {
  try {
    appIdToRomId = await getAppIdRomIdMap();
    return true;
  } catch (e) {
    logError(`Failed to refresh app ID map: ${e}`);
    return false;
  }
}

// Durable attestation of the open sessions — survives a JS-context rebuild so
// the re-initialized manager can adopt the still-running games and finalize
// their stops. A single versioned localStorage row; every access is wrapped so
// a storage failure degrades to the no-attestation path instead of throwing.
const SESSION_BREADCRUMB_KEY = "romm-tender:active-session";
const SESSION_BREADCRUMB_VERSION = 2;

/** Read one stored entry into the current shape, or `null` if it isn't one. */
function toSessionEntry(value: unknown): ActiveSession | null {
  if (typeof value !== "object" || value === null) return null;
  const { appId, romId, startMs } = value as Record<string, unknown>;
  if (typeof appId !== "number" || typeof romId !== "number" || typeof startMs !== "number") return null;
  return { appId, romId, startMs };
}

/**
 * The sessions the durable row attests, or an empty list when it attests none.
 *
 * The stored version is branched on FIRST, before any field is looked at, and
 * every version a released build could have written gets its own lift into the
 * current shape. Validating version and fields in one expression would make
 * every row written by the previous version fail — and a failed validation is
 * indistinguishable from "no attestation", so the live session's pre-upgrade
 * span would be silently discarded on the first reload after an upgrade.
 *
 * A KEY rename is outside what versioning can carry: a row under another key is
 * not read at all, so no branch here can lift it. The key was renamed away from
 * `decky-romm-sync:active-session`, under which v1 and v2 both shipped — so
 * every row written under it is orphaned, and no row this program writes can
 * reach the `v === 1` branch below. It stays because the argument above is
 * about the next format change, not that one.
 *
 * Entries are read individually: one malformed entry never voids its siblings.
 */
function readSessionBreadcrumbs(): ActiveSession[] {
  try {
    const raw = localStorage.getItem(SESSION_BREADCRUMB_KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return [];
    const crumb = parsed as Record<string, unknown>;
    if (crumb.v === 2) {
      if (!Array.isArray(crumb.sessions)) return [];
      return crumb.sessions.map(toSessionEntry).filter((s) => s !== null);
    }
    if (crumb.v === 1) {
      // v1 carried a single session inline — lift it into a one-entry list.
      // No row under the current key reaches here: the key rename above
      // orphaned every v1 row. Kept as the shape the next format change takes.
      const lifted = toSessionEntry(crumb);
      return lifted === null ? [] : [lifted];
    }
    // Unknown version: written by a build this one knows nothing about, so its
    // fields cannot be trusted. Same standing as no attestation at all.
    return [];
  } catch (e) {
    logError(`Failed to read session breadcrumb: ${e}`);
    return [];
  }
}

/**
 * Rewrite the durable row from the session map.
 *
 * A projection, never a read-modify-write: the map is the source of truth and
 * the whole row is rewritten after every mutation, before any await. A failed
 * write leaves the previous row intact (`setItem` is atomic per key) and
 * self-heals at the next mutation.
 *
 * An empty map removes the row rather than storing an empty list, so "no live
 * session" stays the absent state every reader already understands.
 */
function persistSessions(): void {
  if (activeSessions.size === 0) {
    try {
      localStorage.removeItem(SESSION_BREADCRUMB_KEY);
    } catch (e) {
      logError(`Failed to clear session breadcrumb: ${e}`);
    }
    return;
  }
  try {
    const row = { v: SESSION_BREADCRUMB_VERSION, sessions: [...activeSessions.values()] };
    localStorage.setItem(SESSION_BREADCRUMB_KEY, JSON.stringify(row));
  } catch (e) {
    logError(`Failed to write session breadcrumb: ${e}`);
  }
}

/**
 * Notify open surfaces that a RomM play session started or ended, so they can
 * flip to/from the state-aware Resume button without polling (#1313). A frontend
 * DOM CustomEvent — `CustomPlayButton` matches it on `romId`.
 */
function dispatchSessionChanged(running: boolean, appId: number, romId: number): void {
  globalThis.dispatchEvent(new CustomEvent("romm_session_changed", { detail: { running, appId, romId } }));
}

/**
 * Open a session for the app that just started — unless it already has one.
 *
 * The idempotency is load-bearing (#1589). `record_session_start` RE-OPENS the
 * durable marker rather than extending it, so a second call for a live session
 * silently discards the span already played. Steam can report an app as started
 * twice (notably when a launch lands during the panel load and reload-adoption
 * has already opened the session), and the re-open is deliberate backend
 * behaviour that adoption relies on — so the guard belongs here.
 *
 * It is keyed on the appId and checked BEFORE the romId lookup: a map that
 * emptied mid-session must not be able to drop a live entry.
 */
async function handleGameStart(appId: number, mapAnswered: boolean, notedRomId: number | null): Promise<void> {
  const open = activeSessions.get(appId);
  if (open) {
    detach(debugLog(`Session start ignored: appId=${appId} already has an open session (romId=${open.romId})`));
    // Re-announce it — a surface that missed the first event self-heals, and the
    // earlier startMs is kept.
    dispatchSessionChanged(true, open.appId, open.romId);
    return;
  }

  const romId = mapAnswered ? getRomIdForApp(appId) : (notedRomId ?? getRomIdForApp(appId));
  if (!romId) return; // Not a RomM shortcut

  logInfo(`Session start: romId=${romId}, appId=${appId}`);
  activeSessions.set(appId, { appId, romId, startMs: Date.now() });
  dispatchSessionChanged(true, appId, romId);

  // Attest the open sessions so a reload mid-game can adopt and finalize them.
  persistSessions();

  // Record session start for playtime tracking
  try {
    await withTimeout(recordSessionStart(romId), LOCAL_CALL_LIMIT_MS);
  } catch (e) {
    logError(`Failed to record session start: ${e}`);
  }
  // Pre-launch sync moved to CustomPlayButton.handlePlay
}

/**
 * Finalize the session the stopped app opened, if it opened one.
 *
 * `RegisterForAppLifetimeNotifications` fires for EVERY app Steam tracks, so an
 * unrelated app's exit reaches here too (#1621). The lookup is by the appId
 * RECORDED WITH THE SESSION, never a fresh `getRomIdForApp` lookup: the cached
 * map can be stale at stop time, and a stale miss would drop a real session —
 * recording no playtime and skipping the post-exit sync entirely.
 *
 * Any other app's exit — a foreign app, or a concurrent RomM game — leaves every
 * open session untouched. Finalizing one of those would record its playtime
 * early AND run the post-exit save sync while its emulator still holds the save
 * file open, capturing a half-written file.
 */
async function handleGameStop(stoppedAppId: number): Promise<void> {
  const session = activeSessions.get(stoppedAppId);
  if (!session) {
    detach(debugLog(`Session stop ignored: appId=${stoppedAppId} opened no session`));
    return;
  }

  const { appId, romId } = session;
  logInfo(`Session end: romId=${romId}`);

  // Flip any open Resume button back to Play (#1313). The appId comes from the
  // session itself, so this needs no reverse-map lookup.
  dispatchSessionChanged(false, appId, romId);

  // Drop this session immediately to avoid double-processing, and rewrite the
  // row before the finalize await — the stop is observed, so there is nothing
  // left to adopt for this app; any concurrent session stays attested.
  activeSessions.delete(appId);
  persistSessions();

  // A late answer is applied after the chain has moved on, so it must touch no
  // session state. Why it can be late: `docs/architecture/save-file-sync-architecture.md`,
  // "Post-exit sync".
  const applied = finalizeGameSession(romId)
    .then((result) => applyFinalizeResult(appId, romId, result))
    .catch((e: unknown) => logError(`Failed to finalize game session: ${e}`));
  await withTimeout(applied, SERVER_CALL_LIMIT_MS).catch((e: unknown) =>
    logWarn(`Finalize for romId=${romId} has not answered (${e}) — moving on; its answer is applied when it arrives`),
  );
}

function applyFinalizeResult(
  appId: number,
  romId: number,
  result: Awaited<ReturnType<typeof finalizeGameSession>>,
): void {
  // Playtime display update — appStore mutation must stay frontend.
  if (result.total_seconds != null) {
    updatePlaytimeDisplay(appId, result.total_seconds);
  }

  // Post-exit save-sync toast. The directional success toast is rendered
  // frontend-side from the transfer counts via the shared helper (the single
  // source of that copy, #1481); the offline/failure body stays backend-owned
  // (failure_toast). The two are mutually exclusive — a successful run carries
  // failure_toast=null — so only one fires.
  const directionalBody = result.sync.success ? saveSyncToastBody(result.sync.uploaded, result.sync.downloaded) : null;
  if (directionalBody) {
    showToast(directionalBody);
  } else if (result.sync.failure_toast) {
    showToast(result.sync.failure_toast);
  }

  // Save-sync event dispatch — fires unconditionally so open surfaces refresh
  // to the honest post-sync state. A failed post-exit sync must refresh too
  // (#1334): the panel would otherwise keep showing a stale green "synced" for
  // a file that is now pending upload.
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: romId } }));

  // Additive conflicts toast — backend renders the count string.
  if (result.sync.conflicts_toast) {
    showToast(result.sync.conflicts_toast);
  }

  // Migration store update — backend ran refresh_state, frontend just
  // feeds the typed payload into the store. When backend refresh failed
  // (``migration == null``) leave the store untouched: a failed refresh
  // must not clear a stale "pending" badge it could not re-check.
  if (result.migration) {
    setMigrationStatus(result.migration.retrodeck);
  }
}

// Adoption polls Steam's running-app before deciding a session's fate: a single
// early read can find the store empty with the game still running
// (`utils/runningApps.ts` states the measurement). Poll the reader until a
// running app appears or the window elapses.
const ADOPTION_POLL_INTERVAL_MS = 500;
export const ADOPTION_POLL_MAX_MS = 15_000;

/**
 * Poll the running-app reader until its reading has SETTLED or the window
 * elapses, and return that last reading (its `diagnostics` say what the store
 * reported — absent / empty / threw / the appids found; each round's are also
 * emitted at debug level).
 *
 * Settled means: something is running AND every app the breadcrumbs attest has
 * surfaced. Stopping at the first non-empty reading is not enough — the store
 * omits apps whose overview has not loaded yet, so a reading that already lists
 * one concurrent game can still be missing its sibling, which would then be
 * orphaned. The wait for stragglers shares the one existing budget.
 */
async function pollForRunningApps(wanted: Set<number>): Promise<RunningAppsReading> {
  const started = Date.now();
  for (;;) {
    const reading = readRunningApps();
    detach(debugLog(`adoption poll round: ${reading.diagnostics}`));
    const surfaced = new Set(reading.apps.map((app) => app.appid));
    if (surfaced.size > 0 && [...wanted].every((appId) => surfaced.has(appId))) return reading;
    if (Date.now() - started >= ADOPTION_POLL_MAX_MS) return reading;
    await delay(ADOPTION_POLL_INTERVAL_MS);
  }
}

/** What reconciling the attested sessions against the running apps decided. */
export interface AdoptionPlan {
  /** Attested and still running — restored exactly as attested. */
  adopted: ActiveSession[];
  /** Running and ours but unattested — restored with the marker re-stamped. */
  restamped: ActiveSession[];
  /** Attested but no longer running — dropped, never finalized. */
  orphans: ActiveSession[];
}

/**
 * Decide the fate of every attested session and every running app (#1624).
 *
 * Pure: reads no module state, performs no I/O, mutates nothing it is handed.
 * The caller commits the plan — this only says what should happen:
 *
 * - attested AND running → adopted as attested. The crumb's own romId is
 *   trusted, which is why `resolveRomId` is not consulted for it: the durable
 *   marker belongs to THAT rom, and re-stamping the map's current romId instead
 *   would open a marker on a different row and leave the original dangling (the
 *   rule #1621 established for the stop path — an `appId → romId` binding is 1:1
 *   at any instant but not stable over time).
 * - running, ours, but unattested → re-stamped: adopted with the marker moved to
 *   `nowMs`, a truthful lower bound, and attested afterwards so a later reload
 *   adopts it as attested rather than re-stamping again.
 * - attested but NOT running → orphaned. Its stop was never observed, and a
 *   truthful finalize is impossible without an observed end, so the attestation
 *   is dropped rather than an end time fabricated.
 * - running but not ours (`resolveRomId` returns `null`) → ignored entirely. A
 *   foreign app is never adopted and never causes anything else to be orphaned.
 *
 * `tracked` names the apps that already hold an open session; both passes skip
 * them, so adoption can never re-open one a start notification opened first
 * (#1589).
 *
 * That skip is UNREACHABLE as the manager is currently wired, and deliberately
 * kept: adoption and every notification handler run on the one serialized
 * `lifecycleChain`, adoption is enqueued in the same synchronous block that
 * registers the hook, and init runs once per JS context, whose session map
 * starts empty — so a fresh init always reconciles against an empty
 * `tracked`. Two wiring changes would make it live: taking adoption off that
 * chain (giving a notification a window to complete during the up-to-15s
 * poll), or a handler that acts directly instead of enqueueing. Do not delete
 * it as dead code without making one of those orderings impossible instead.
 */
export function planAdoption(
  crumbs: readonly ActiveSession[],
  runningAppIds: readonly number[],
  tracked: ReadonlySet<number>,
  resolveRomId: (appId: number) => number | null,
  nowMs: number,
): AdoptionPlan {
  const running = new Set(runningAppIds);
  const adopted: ActiveSession[] = [];
  const orphans: ActiveSession[] = [];
  for (const crumb of crumbs) {
    if (tracked.has(crumb.appId)) continue;
    (running.has(crumb.appId) ? adopted : orphans).push(crumb);
  }

  const attested = new Set(adopted.map((session) => session.appId));
  const restamped: ActiveSession[] = [];
  for (const appId of runningAppIds) {
    if (tracked.has(appId) || attested.has(appId)) continue;
    const romId = resolveRomId(appId);
    if (romId !== null) restamped.push({ appId, romId, startMs: nowMs });
  }

  return { adopted, restamped, orphans };
}

/**
 * Adopt the play sessions orphaned by a JS-context rebuild mid-game.
 *
 * The in-memory sessions live in the JS context, so the game-stops after a
 * rebuild would otherwise never finalize — the pre-rebuild playtime is lost and
 * the post-exit sync never runs. Steam's running-state
 * (`SteamUIStore.RunningApps`) is the liveness authority; the localStorage
 * breadcrumbs are the attestations of starts we actually observed. Every
 * finalize fold thus stays anchored to a marker stamped by an observed start.
 *
 * The liveness read is POLLED, not a single read: the store can report an EMPTY
 * running-app list for seconds while the game is still up
 * (`utils/runningApps.ts` states the measurement).
 *
 * This is the orchestration around that: poll, ask {@link planAdoption} what to
 * do, then commit / dispatch / log / re-stamp. The reconcile matrix itself lives
 * in that pure function.
 */
async function adoptOrphanedSessions(): Promise<void> {
  const pollStart = Date.now();
  const crumbs = readSessionBreadcrumbs();
  const reading = await pollForRunningApps(new Set(crumbs.map((c) => c.appId)));
  const waitedMs = Date.now() - pollStart;
  logInfo(
    reading.apps.length > 0
      ? `adoption: running app appeared after ${waitedMs}ms [${reading.diagnostics}]`
      : `adoption: no running app after ${waitedMs}ms [${reading.diagnostics}]`,
  );

  const { adopted, restamped, orphans } = planAdoption(
    crumbs,
    reading.apps.map((app) => app.appid),
    new Set(activeSessions.keys()),
    getRomIdForApp,
    Date.now(),
  );

  const recovered = [...adopted, ...restamped];
  for (const session of recovered) activeSessions.set(session.appId, session);
  // One rewrite for the whole reconcile: it drops the orphans and lifts a row
  // written by an older schema version into the current one.
  persistSessions();

  for (const session of recovered) dispatchSessionChanged(true, session.appId, session.romId);
  for (const s of adopted) logInfo(`Adopted running session from breadcrumb: romId=${s.romId}, appId=${s.appId}`);
  for (const s of orphans) logInfo(`Session orphaned — playtime not recorded (romId=${s.romId})`);

  for (const session of restamped) {
    try {
      await recordSessionStart(session.romId);
    } catch (e) {
      logError(`Failed to record session start on adoption: ${e}`);
    }
    logInfo(
      `Adopted running session without breadcrumb, re-stamped marker: romId=${session.romId}, appId=${session.appId}`,
    );
  }
}

/**
 * Initialize session manager — registers all lifecycle hooks.
 * Call once during the panel load.
 */
export async function initSessionManager(): Promise<void> {
  // Load initial app ID map
  await refreshAppIdMap();

  // Game lifecycle notifications
  SteamClient.GameSessions.RegisterForAppLifetimeNotifications((update) => {
    if (update.bRunning) stoppedSinceStart.delete(update.unAppID);
    else stoppedSinceStart.add(update.unAppID);
    // Taken at the notification, not when the chain reaches it: the window is
    // about how long Steam took to report the start.
    const notedRomId = update.bRunning ? takeNotedRom(update.unAppID) : null;
    lifecycleChain = lifecycleChain
      .then(async () => {
        if (update.bRunning) {
          // The notification's own appid identifies the app that started. Do NOT
          // consult `SteamUIStore.RunningApps` here: its head is the most recently
          // FOREGROUNDED app and a fresh arrival is appended at the tail, so the
          // head names some other running game — attributing the start to it opens
          // a session on the wrong rom and never opens one for this app, whose
          // stop then finalizes nothing. Reading it also cost a 500ms delay that
          // stalled the whole serialized lifecycle chain (a stop queued behind a
          // start waited for it too); both are gone.
          const appId = update.unAppID;
          if (appId) {
            // Refresh map in case a sync happened since init
            const mapAnswered = await withTimeout(readAppIdMap(), LOCAL_CALL_LIMIT_MS).catch((e: unknown) => {
              logError(`Failed to refresh app ID map: ${e}`);
              return false;
            });
            await handleGameStart(appId, mapAnswered, notedRomId);
          }
        } else {
          // An app stopped — `handleGameStop` decides whether it is ours.
          await handleGameStop(update.unAppID);
        }
      })
      .catch((e) => {
        logError(`Lifecycle event error: ${e}`);
      });
  });

  // Adopt a session orphaned by a JS-context rebuild mid-game. Serialized on the
  // lifecycle chain so a stop notification arriving during adoption finalizes
  // after it rather than interleaving with the in-memory state it restores.
  const adoption = lifecycleChain
    .then(() => adoptOrphanedSessions())
    .catch((e) => {
      logError(`Session adoption error: ${e}`);
    });
  lifecycleChain = adoption;
  await adoption;

  logInfo("Session manager initialized");
}
