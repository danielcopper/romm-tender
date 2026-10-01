/**
 * Global launch watcher (ADR-0015 — the "full funnel").
 *
 * Every start of a RomM-owned shortcut that did NOT come from our Play button
 * is intercepted here. Because no Steam hook can pause a launch, run async
 * work, and then proceed, the watcher uses the cancel-then-relaunch mechanism:
 * it `CancelGameAction`s the launch IMMEDIATELY (synchronously, which wins the
 * race against the un-pausable launch), runs the shared {@link runLaunchGate}
 * funnel, and on approval relaunches via `RunGame`.
 *
 * The skip-set (`markLaunchSkipped` / `consumeLaunchSkip`, owned by
 * `launchGate.ts`) lets one start through that a caller has just handled — the
 * watcher's own relaunch and the Play button's start — so neither is gated
 * twice.
 *
 * Registered once, from the panel's factory; nothing unregisters it.
 */

import { showToast } from "./toast";
import { isRomMAppId } from "./rommAppIds";
import { appIdFromGameId } from "./gameId";
import {
  refreshMigrationState,
  getInstalledRom,
  getCachedGameDetail,
  isSaveTrackingConfigured,
  getSaveSetupInfo,
  confirmSlotChoice,
  checkCoreChange,
  probeReachability,
  preLaunchSync,
  checkLocalDrift,
  logInfo,
  logError,
} from "../api/backend";
import { getMigrationState, setMigrationStatus } from "./migrationStore";
import { reportServerReachable } from "./connectionState";
import { getAppIdRomIdMapSnapshot, readGameRunning, refreshAppIdMap } from "./sessionManager";
import { runLaunchGate, markLaunchSkipped, consumeLaunchSkip } from "./launchGate";
import { NO_LAUNCH_TARGET_TOAST_BODY, romHasLaunchTarget } from "./launchTarget";
import type { GateVerdict, LaunchGateOps, PreLaunchSyncOutcome } from "./launchGate";
import { reconfirmLaunchOptions } from "./launchOptionsReconcile";
import { capturePruneLeaseAdmission, isPruneLeaseAdmissionCurrent, type PruneLeaseAdmission } from "./pruneLease";
import { applyLaunchGateSetupOutcome, resolveSaveSetupOutcome } from "./saveSetup";
import { BENIGN_SYNC_SKIP_REASONS, type SyncConflict } from "../types";
import { detach } from "./detach";
import { TimeoutError, withTimeout } from "./withTimeout";

/**
 * The four decisions the funnel has to put to the user, as questions rather than
 * widgets. The interceptor runs outside the component tree and owns no UI, so it
 * declares what it needs to ask and the caller supplies the answering surface —
 * `index.tsx` wires the real modals in.
 *
 * Asking directly would mean reaching from here into the modal modules, which
 * puts the launch funnel's control flow and its presentation in one knot: every
 * test of a gate branch then has to stand up four modal modules to get at it.
 */
export interface LaunchPrompts {
  /** Core changed since the last session — proceed with the new core? */
  confirmCoreChange(oldLabel: string, newLabel: string): Promise<boolean>;
  /** Walk the save conflicts; "resolved" once all are settled, "cancel" on the first dismissal. */
  resolveConflicts(conflicts: SyncConflict[]): Promise<"cancel" | "resolved">;
  /** Server unreachable and the local save has drifted — start anyway, re-probe, or give up? */
  askOfflineDrift(): Promise<"start_anyway" | "retry" | "cancel">;
  /** Pre-launch sync failed — launch on the local save regardless? */
  confirmFallbackLaunch(message?: string): Promise<boolean>;
}

/**
 * A start the watcher cancelled: the appId it resolved, and the game ID Steam
 * reported it by, which a relaunch hands back to `RunGame` (see
 * {@link relaunchGameId}).
 */
interface CancelledStart {
  appId: number;
  gameId: string;
}

/** Migration block copy — surfaced as a toast (no relaunch). */
const MIGRATION_TOAST_BODY = "Pending RetroDECK migration. Open the Tender menu (QAM) to migrate or dismiss.";

/**
 * Watcher variant of the tracking-setup gate. On a cold grid launch there is no
 * game page open, so — unlike the Play button — this MUST NOT route the user
 * to the saves tab. Instead it silently auto-adopts the default/recommended
 * slot (via `confirmSlotChoice`) and proceeds. A direct launch is never blocked
 * on setup — it always proceeds (the gate op below maps this to "proceed"); any
 * failure (server unreachable, needs-user-choice, a thrown error) is swallowed.
 */
async function ensureTrackingConfiguredWatcher(romId: number): Promise<void> {
  const trackingResult = await isSaveTrackingConfigured(romId).catch((e) => {
    logError(`Watcher tracking check failed (assuming configured): ${e}`);
    return { configured: true };
  });
  if (trackingResult.configured) return;

  let setupInfo;
  try {
    setupInfo = await getSaveSetupInfo(romId);
  } catch (e) {
    // Network/backend failure — never block a direct launch on setup.
    logError(`Watcher save-setup fetch failed (proceeding unconfigured): ${e}`);
    return;
  }

  // Reuse the shared outcome handler with a no-op saves-tab dispatch and a
  // swallowed toast: the auto_confirm branch fires `confirmSlotChoice`; every
  // other (abort) branch is irrelevant here because the watcher never aborts a
  // direct launch on tracking — an unconfigured slot the user can't resolve on
  // a cold launch must still let the game start (the next visit to the game
  // page's saves tab configures it).
  await applyLaunchGateSetupOutcome(resolveSaveSetupOutcome(setupInfo), {
    rid: romId,
    confirmSlotChoice,
    toast: () => undefined,
    dispatchSavesTab: () => undefined,
  }).catch((e) => logError(`Watcher auto-adopt slot failed (proceeding): ${e}`));
}

/**
 * Core-change gate — reuses the same check the Play button uses, and puts the
 * same question to the user through the injected prompt. Returns `true` to
 * proceed, `false` when the user cancelled.
 */
async function checkCoreChangeWatcher(romId: number, prompts: LaunchPrompts): Promise<boolean> {
  const coreCheck = await checkCoreChange(romId).catch(
    (e): { changed: boolean; old_core?: string; new_core?: string; old_label?: string; new_label?: string } => {
      logError(`Watcher core-change check failed (assuming unchanged): ${e}`);
      return { changed: false };
    },
  );
  if (!coreCheck.changed) return true;
  return prompts.confirmCoreChange(
    coreCheck.old_label ?? coreCheck.old_core ?? "Unknown",
    coreCheck.new_label ?? coreCheck.new_core ?? "Unknown",
  );
}

/** Pre-launch sync hard timeout — mirrors the Play button's `runPreLaunchSync`. */
const PRE_LAUNCH_SYNC_TIMEOUT_MS = 15000;

/**
 * Online pre-launch sync, mapped onto the gate's {@link PreLaunchSyncOutcome}.
 * A benign skip is treated as a successful proceed (no conflict, no failure) —
 * exactly as the Play button does — so it never surfaces a fallback confirm.
 * Two slugs are benign: saves written to the content directory, and an emulator
 * whose save is not a per-game file set this plugin can carry. Both mean sync
 * did not run and nothing is wrong, which is a different thing from sync
 * failing.
 *
 * Critically, this MUST NOT fail open: a throw or a hang in `preLaunchSync`
 * would otherwise propagate to the gate's blanket catch → `allow` → a silent
 * relaunch on stale saves. So the call is wrapped in a 15s timeout race AND a
 * try/catch, and on throw/timeout it returns `{ success: false, ... }` — which
 * the gate maps to `sync_failed`, surfacing the fallback confirm instead of
 * silently launching.
 */
async function preLaunchSyncWatcher(romId: number): Promise<PreLaunchSyncOutcome> {
  let result: Awaited<ReturnType<typeof preLaunchSync>>;
  try {
    result = await Promise.race([
      preLaunchSync(romId),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), PRE_LAUNCH_SYNC_TIMEOUT_MS)),
    ]);
  } catch (e) {
    logError(`Watcher pre-launch sync failed (surfacing fallback confirm): ${e}`);
    return { success: false, message: "Couldn't sync saves with RomM server." };
  }
  if (result.reason !== undefined && BENIGN_SYNC_SKIP_REASONS.includes(result.reason)) {
    return { success: true, message: result.message };
  }
  const outcome: PreLaunchSyncOutcome = { success: result.success, message: result.message };
  if (result.conflicts) outcome.conflicts = result.conflicts;
  return outcome;
}

/** Build the funnel callbacks for a given romId. */
function makeWatcherOps(romId: number, prompts: LaunchPrompts): LaunchGateOps {
  return {
    migrationPending: () => getMigrationState().pending,
    hasLaunchTarget: () => romHasLaunchTarget(romId, "Watcher"),
    ensureTrackingConfigured: async (): Promise<"proceed"> => {
      await ensureTrackingConfiguredWatcher(romId);
      return "proceed";
    },
    checkCoreChange: () => checkCoreChangeWatcher(romId, prompts),
    checkReachability: async () => {
      // A resolved probe feeds the shared store (#1345); a throw is a bridge
      // error, not a server verdict, so it leaves the store untouched but the
      // launch still treats it as offline (fail-safe).
      try {
        const { online } = await probeReachability();
        reportServerReachable(online);
        return online;
      } catch (e) {
        logError(`Watcher reachability probe failed (treating as offline): ${e}`);
        return false;
      }
    },
    preLaunchSync: () => preLaunchSyncWatcher(romId),
    checkLocalDrift: async () =>
      (
        await checkLocalDrift(romId).catch((e) => {
          logError(`Watcher local-drift check failed (treating as not-drifted): ${e}`);
          return { drifted: false };
        })
      ).drifted,
  };
}

/** Relaunch a previously-cancelled launch. Marks the appId as skipped FIRST so
 *  this RunGame doesn't re-enter the watcher and re-gate. Used directly only for
 *  the paths with nothing to re-confirm (unknown appId, no backend answer, error
 *  fallback); the gated path goes through {@link relaunch}. */
function bareRelaunch(start: CancelledStart): void {
  markLaunchSkipped(start.appId);
  SteamClient.Apps.RunGame(relaunchGameId(start), "", -1, 100);
}

/**
 * The id a relaunch starts the game by. A start reported by its 64-bit game ID
 * is started by that ID. A start reported by a value that fits in 32 bits — the
 * appId itself, a form Steam has not been seen using for a shortcut — is
 * started by the shortcut's game ID from Steam's app store where it has an
 * overview, and by the reported value otherwise.
 */
function relaunchGameId(start: CancelledStart): string {
  if (BigInt(start.gameId) > 0xffffffffn) return start.gameId;
  return appStore.GetAppOverviewByAppID(start.appId)?.GetGameID?.() ?? start.gameId;
}

/** Relaunch a previously-cancelled, now-approved launch. Heals any mid-session
 *  `launch_options` drift on the shortcut first (shared bounded-race re-confirm;
 *  ordinary I/O failure is best-effort, timeout/lifecycle cancellation stops), then marks the appId as
 *  skipped immediately before this RunGame so it doesn't re-enter the watcher
 *  and re-gate. The re-confirm runs in the already-detached post-cancel portion,
 *  so it only adds a bounded (≤3s) wait to the cancel→relaunch window. */
async function relaunch(start: CancelledStart, romId: number, admission: PruneLeaseAdmission): Promise<void> {
  const reconfirm = await reconfirmLaunchOptions(romId, start.appId, "Watcher", admission);
  if (reconfirm.status === "cancelled") return;
  if (reconfirm.status === "timeout") {
    // The watcher has no game-page UI to fall back to, so the refusal would
    // otherwise be a silently dead Play press — say it out loud instead
    // (CustomPlayButton's twin path returns its trigger to "Play").
    showToast("Launch cancelled — try again");
    return;
  }
  bareRelaunch(start);
}

/**
 * Act on the funnel's verdict for a cancelled launch. The launch is already
 * stopped, so each branch either relaunches (`relaunch`) or does nothing.
 *
 * Returns "retry" only from the offline-drift branch when the user asks to
 * re-probe — {@link runWatcherGate} loops on that and re-runs the gate (which
 * re-probes via the fast reachability check); every other outcome returns
 * "done".
 */
async function handleWatcherVerdict(
  verdict: GateVerdict,
  start: CancelledStart,
  romId: number,
  admission: PruneLeaseAdmission,
  prompts: LaunchPrompts,
): Promise<"done" | "retry"> {
  switch (verdict.decision) {
    case "allow":
      await relaunch(start, romId, admission);
      return "done";
    case "abort":
      // The user saw setup/core UI and declined — already cancelled, nothing to do.
      return "done";
    case "block":
      if (verdict.reason === "migration_pending") {
        showToast(MIGRATION_TOAST_BODY);
      } else if (verdict.reason === "no_launch_target") {
        showToast(NO_LAUNCH_TARGET_TOAST_BODY);
      }
      return "done";
    case "conflict": {
      const resolution = await prompts.resolveConflicts(verdict.conflicts);
      if (resolution === "cancel") return "done";
      // Conflicts resolved — notify sibling components to refresh, then relaunch.
      globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: romId } }));
      await relaunch(start, romId, admission);
      return "done";
    }
    case "offline_drift": {
      const choice = await prompts.askOfflineDrift();
      if (choice === "start_anyway") await relaunch(start, romId, admission);
      if (choice === "retry") return "retry";
      return "done";
    }
    case "sync_failed": {
      const proceed = await prompts.confirmFallbackLaunch(verdict.message);
      if (proceed) await relaunch(start, romId, admission);
      return "done";
    }
  }
}

/**
 * Run the shared launch gate and act on its verdict, looping while the user
 * keeps choosing "Retry" on the offline-drift modal. Each retry re-runs
 * {@link runLaunchGate} (re-probing connectivity via the fast reachability
 * check) and acts on the NEW verdict — online now relaunches via the normal
 * path; still offline + drift re-shows the offline modal. A gate throw fails
 * open to `allow` so a gate bug never traps the user's already-cancelled launch.
 */
async function runWatcherGate(
  start: CancelledStart,
  romId: number,
  admission: PruneLeaseAdmission,
  prompts: LaunchPrompts,
): Promise<void> {
  let verdict = await runLaunchGate(start.appId, romId, makeWatcherOps(romId, prompts)).catch((e): GateVerdict => {
    logError(`Watcher gate threw (failing open to allow): ${e}`);
    return { decision: "allow" };
  });
  // Each pass starts only after the user answered the previous verdict's prompt with "retry", so
  // the awaits are sequential by design. S9382 is raised on the two await lines, so their NOSONARs
  // must stay there; prettier-ignore stops Prettier from moving them into the bodies.
  // prettier-ignore
  while ((await handleWatcherVerdict(verdict, start, romId, admission, prompts)) === "retry") { // NOSONAR(typescript:S9382)
    verdict = await runLaunchGate(start.appId, romId, makeWatcherOps(romId, prompts)).catch((e): GateVerdict => { // NOSONAR(typescript:S9382)
      logError(`Watcher gate threw (failing open to allow): ${e}`);
      return { decision: "allow" };
    });
  }
}

/**
 * Is this ROM installed on disk? The funnel assumes an installed ROM; an
 * uninstalled one is a hard block (the ROM is gone — no "Start Anyway").
 * `get_installed_rom` is the live truth: a returned record means installed, a
 * `null` means not installed. Only when the call THROWS (transport hiccup) do we
 * fall back to the cached detail's `installed` flag, so a transient error never
 * trap-blocks a genuinely-installed ROM.
 */
async function isRomInstalled(appId: number, romId: number): Promise<boolean> {
  try {
    return (await getInstalledRom(romId)) != null;
  } catch (e) {
    logError(`Watcher installed check threw (falling back to cached install flag): ${e}`);
    const cached = await getCachedGameDetail(appId).catch((cacheErr) => {
      logError(`Watcher cached-detail fallback failed (treating as not installed): ${cacheErr}`);
      return null;
    });
    return cached?.installed === true;
  }
}

/**
 * How long a cancelled start waits for the watcher's first backend answer
 * before it goes ahead without the pre-launch sync. Why the wait is bounded:
 * `docs/architecture/save-file-sync-architecture.md`, "Pre-launch sync".
 */
export const FIRST_CONTACT_DEADLINE_MS = 5000;

const NOT_RESPONDING_TOAST_BODY = "Tender isn't responding — started without syncing saves.";

/**
 * The ROM a cancelled start belongs to and whether it is installed, or `null`
 * for an appId the map does not know even after one refresh. Why an owned
 * appId can be missing from the map: `docs/architecture/save-file-sync-architecture.md`,
 * "Pre-launch sync".
 */
async function readStartRom(appId: number): Promise<{ romId: number; installed: boolean } | null> {
  let romId = getAppIdRomIdMapSnapshot()[String(appId)];
  if (romId == null) {
    await refreshAppIdMap();
    romId = getAppIdRomIdMapSnapshot()[String(appId)];
    if (romId == null) return null;
  }
  return { romId, installed: await isRomInstalled(appId, romId) };
}

export function registerLaunchInterceptor(prompts: LaunchPrompts): void {
  SteamClient.Apps.RegisterForGameActionStart(
    (gameActionId: number, gameId: string, action: string, _launchSource: number) => {
      if (action !== "LaunchApp") return;

      const appId = appIdFromGameId(gameId);
      if (appId === null || !isRomMAppId(appId)) return;

      // One-shot skip: a start a caller has already handled (the watcher's own
      // relaunch, or a Play-button start) — do NOT gate it again.
      if (consumeLaunchSkip(appId)) return;

      // Already-running guard. A Play press on a game that is ALREADY running
      // still fires GameActionStart. Intercepting it would cancel the launch, run
      // the pre-launch gate, and upload the save MID-SESSION while the emulator
      // holds the file open. Skip the whole funnel while the game is running.
      const pressedRomId = getAppIdRomIdMapSnapshot()[String(appId)];
      if (readGameRunning(appId, pressedRomId).running) {
        logInfo(`Launch interceptor: appId=${appId} already running — skipping pre-launch sync`);
        return;
      }

      // CANCEL FIRST — synchronously, before any await. This wins the race
      // against the un-pausable launch: from here the launch is stopped and we
      // relaunch only on approval.
      SteamClient.Apps.CancelGameAction(gameActionId);
      const admission = capturePruneLeaseAdmission();
      const start: CancelledStart = { appId, gameId };

      detach(
        (async () => {
          try {
            // Fire-and-forget migration refresh — picks up a RetroDECK home
            // change made since the prior session.
            refreshMigrationState()
              .then(({ retrodeck }) => {
                setMigrationStatus(retrodeck);
              })
              .catch((e) => logError(`Pre-launch migration refresh failed: ${e}`));

            // Losing this race abandons the read rather than cancelling it, and
            // the start is not waited on again: an answer arriving after the
            // deadline changes nothing.
            let rom: Awaited<ReturnType<typeof readStartRom>>;
            try {
              rom = await withTimeout(readStartRom(appId), FIRST_CONTACT_DEADLINE_MS);
            } catch (e) {
              if (!(e instanceof TimeoutError)) throw e;
              logError(
                `Launch interceptor: no backend answer for appId=${appId} within ${FIRST_CONTACT_DEADLINE_MS}ms — starting without the pre-launch sync`,
              );
              if (!isPruneLeaseAdmissionCurrent(admission)) return;
              showToast(NOT_RESPONDING_TOAST_BODY);
              bareRelaunch(start);
              return;
            }

            // An appId the map does not know is not ours to gate — relaunch and bail.
            if (rom === null) {
              if (!isPruneLeaseAdmissionCurrent(admission)) return;
              bareRelaunch(start);
              return;
            }

            // The funnel assumes an installed ROM. Not installed → hard block
            // (no relaunch): the ROM is gone.
            if (!rom.installed) {
              if (!isPruneLeaseAdmissionCurrent(admission)) return;
              showToast("ROM not downloaded. Download it from its game page first.");
              return;
            }

            await runWatcherGate(start, rom.romId, admission, prompts);
          } catch (e) {
            // An unexpected error must not trap a current launch. A stale launch
            // belongs to a torn-down generation and must remain cancelled.
            // Use the bare relaunch (no re-confirm): the failure may be the
            // re-confirm's own dependency, and the priority here is escaping the
            // cancelled state, not healing drift.
            logError(`Launch interceptor error: ${e}`);
            if (!isPruneLeaseAdmissionCurrent(admission)) return;
            bareRelaunch(start);
          }
        })(),
      );
    },
  );

  logInfo("Launch interceptor registered");
}
