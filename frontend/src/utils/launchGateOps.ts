/**
 * The gate's inputs ({@link LaunchGateOps}) for one ROM — the parts every launch
 * path asks the same way, built once here. A path supplies the three steps that
 * put something to the user, since only it knows where it may ask: the tracking
 * setup, the core-change question and the question asked where the starting
 * source could not be checked.
 */

import {
  checkLocalDrift,
  confirmSlotChoice,
  debugLog,
  getSaveSetupInfo,
  isSaveTrackingConfigured,
  logError,
  preLaunchSync,
  probeReachability,
} from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { detach } from "./detach";
import { BENIGN_SYNC_SKIP_REASONS, LOCAL_CALL_LIMIT_MS, SERVER_CALL_LIMIT_MS } from "./launchGate";
import type { LaunchGateOps, PreLaunchSyncOutcome } from "./launchGate";
import { romHasLaunchTarget } from "./launchTarget";
import { confirmCoreChangeIfNeeded } from "./coreChange";
import { getMigrationState } from "./migrationStore";
import { readStartingSource } from "./startingSource";
import { saveSyncToastBody } from "./saveSyncToast";
import { applyLaunchGateSetupOutcome, resolveSaveSetupOutcome } from "./saveSetup";
import { showToast } from "./toast";
import { boundedOr, withTimeout } from "./withTimeout";

interface PreLaunchSyncOptions {
  /** Prefixes every log line, naming the launch path. */
  tag: string;
  /**
   * Toast what a successful sync moved (`saveSyncToastBody`). An automatic
   * background start (the launch watcher) passes `false` so a clean launch into
   * gameplay is silent; an explicit start through Tender's Play button passes `true`.
   */
  toastSyncResult: boolean;
}

/**
 * Run the pre-launch sync for `romId` and shape its answer for the gate.
 *
 * - A benign skip (`BENIGN_SYNC_SKIP_REASONS`) is a success: sync did not run and
 *   nothing is wrong, a standing fact about the machine that a confirm on every
 *   launch would only nag about.
 * - Conflicts pass through for the gate to route.
 * - Any other failure is `{ success: false, message }`, including one with no
 *   `errors` (`DEVICE_NOT_REGISTERED`, `blocked_by_migration`,
 *   `blocked_by_update`): sync did not run, so the user must not play on stale
 *   saves believing it did.
 * - A throw is `{ success: false, message: "" }`. It must not propagate: the
 *   gate fails open on a throw, which would launch silently on stale saves. The
 *   empty message leaves the fallback prompt its own default sentence.
 * - A sync that gets no answer within {@link SERVER_CALL_LIMIT_MS} is let
 *   through as a `TimeoutError`: the gate answers it.
 */
export async function runPreLaunchSync(romId: number, options: PreLaunchSyncOptions): Promise<PreLaunchSyncOutcome> {
  const { tag } = options;
  const result = await boundedOr(preLaunchSync(romId), SERVER_CALL_LIMIT_MS, (e) => {
    logError(`${tag}: pre-launch sync failed (surfacing fallback confirm): ${e}`);
    return null;
  });
  if (result === null) return { success: false, message: "" };

  detach(
    debugLog(
      `${tag}: preLaunchSync result: synced=${result.synced} conflicts=${result.conflicts?.length ?? 0} success=${result.success}`,
    ),
  );

  if (result.reason !== undefined && BENIGN_SYNC_SKIP_REASONS.includes(result.reason)) {
    detach(debugLog(`${tag}: pre-launch sync skipped (${result.reason}) — launching`));
    return { success: true, message: result.message };
  }

  if (result.conflicts && result.conflicts.length > 0) {
    return { success: result.success, message: result.message, conflicts: result.conflicts };
  }

  if (!result.success) {
    detach(
      debugLog(
        `${tag}: pre-launch sync failed: reason=${result.reason ?? ""} errors=[${result.errors?.join(", ") ?? ""}] message=${result.message}`,
      ),
    );
    return { success: false, message: result.message };
  }

  if (options.toastSyncResult) {
    const toastBody = saveSyncToastBody(result.uploaded, result.downloaded);
    if (toastBody) showToast(toastBody);
  }
  return { success: true, message: result.message };
}

/**
 * The tracking-setup step for a start pressed on Big Picture's game-detail
 * page. What the backend's setup answer does to the start is
 * `applyLaunchGateSetupOutcome`'s. A read that throws proceeds; a read or slot
 * confirm that gets no answer in time is let through for the gate.
 *
 * The fallbacks guard only the network reads. The branching on their answer sits
 * outside them so that a throw from a side effect (the toast, the tab switch, the
 * slot confirm) is not swallowed into "proceed" here; what a throw means is the
 * gate's.
 */
export async function ensureTrackingConfiguredBigPicture(romId: number): Promise<"proceed" | "abort"> {
  const trackingResult = await boundedOr(isSaveTrackingConfigured(romId), LOCAL_CALL_LIMIT_MS, () => ({
    configured: true,
  }));
  if (trackingResult.configured) return "proceed";

  const setupInfo = await boundedOr(getSaveSetupInfo(romId), SERVER_CALL_LIMIT_MS, () => null);
  if (setupInfo === null) return "proceed";

  return applyLaunchGateSetupOutcome(resolveSaveSetupOutcome(setupInfo), {
    rid: romId,
    confirmSlotChoice: (...args) => withTimeout(confirmSlotChoice(...args), LOCAL_CALL_LIMIT_MS),
    toast: (body) => showToast(body),
    dispatchSavesTab: () => globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "saves" } })),
  });
}

interface LaunchGateOpsOptions extends PreLaunchSyncOptions {
  ensureTrackingConfigured: () => Promise<"proceed" | "abort">;
  confirmCoreChange: (oldLabel: string, newLabel: string) => Promise<boolean>;
  confirmUncheckedStartingSource: () => Promise<boolean>;
  /** Called as the pre-launch sync starts, for a trigger that shows it. */
  onSyncStart?: () => void;
}

/**
 * The gate's inputs for `romId`. Reachability is a fresh probe at start time;
 * a probe that answers feeds the shared connection store, while a probe that
 * throws is a bridge error rather than a server verdict, so it leaves the store
 * alone and the start treats it as offline. A drift check that throws reads as
 * not drifted. A call that gets no answer within its limit is not a failure:
 * its `TimeoutError` reaches the gate.
 */
export function makeLaunchGateOps(romId: number, options: LaunchGateOpsOptions): LaunchGateOps {
  const { tag } = options;
  return {
    migrationPending: () => getMigrationState().pending,
    hasLaunchTarget: () => romHasLaunchTarget(romId, tag),
    readStartingSource: () => readStartingSource(tag),
    confirmUncheckedStartingSource: options.confirmUncheckedStartingSource,
    ensureTrackingConfigured: options.ensureTrackingConfigured,
    checkCoreChange: () =>
      confirmCoreChangeIfNeeded(romId, options.confirmCoreChange, (e) => {
        logError(`${tag}: core-change check failed (assuming unchanged): ${e}`);
      }),
    checkReachability: async () => {
      const probe = await boundedOr(probeReachability(), LOCAL_CALL_LIMIT_MS, (e) => {
        logError(`${tag}: reachability probe failed (treating as offline): ${e}`);
        return null;
      });
      if (probe === null) return false;
      reportServerReachable(probe.online);
      return probe.online;
    },
    preLaunchSync: () => {
      try {
        options.onSyncStart?.();
      } catch (e) {
        // A throwing trigger hook must not abort or bypass the pre-launch sync.
        logError(`${tag}: onSyncStart threw: ${e}`);
      }
      return runPreLaunchSync(romId, options);
    },
    checkLocalDrift: async () =>
      (
        await boundedOr(checkLocalDrift(romId), LOCAL_CALL_LIMIT_MS, (e) => {
          logError(`${tag}: local-drift check failed (treating as not-drifted): ${e}`);
          return { drifted: false };
        })
      ).drifted,
  };
}
