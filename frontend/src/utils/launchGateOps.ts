/**
 * The gate's inputs ({@link LaunchGateOps}) for one ROM — the parts every launch
 * path asks the same way, built once here. A path supplies what puts something
 * to the user, since only it knows where it may ask: its tracking setup, and the
 * prompts for a core change and for a starting source that could not be checked.
 */

import {
  checkLocalDrift,
  confirmSlotChoice,
  debugLog,
  getSaveSetupInfo,
  isSaveTrackingConfigured,
  logError,
  preLaunchSync,
  preLaunchSyncState,
  probeReachability,
  stopPreLaunchSync,
} from "../api/backend";
import { reportServerReachable } from "./connectionState";
import { detach } from "./detach";
import { BENIGN_SYNC_SKIP_REASONS, LOCAL_CALL_LIMIT_MS, SERVER_CALL_LIMIT_MS, SYNC_WAIT_MORE_MS } from "./launchGate";
import type { LaunchGateOps, PreLaunchSyncAnswer, PreLaunchSyncOutcome, SyncWaitReason } from "./launchGate";
import type { LaunchPrompts } from "./launchVerdict";
import { romHasLaunchTarget } from "./launchTarget";
import { confirmCoreChangeIfNeeded } from "./coreChange";
import { getMigrationState } from "./migrationStore";
import { readStartingSource } from "./startingSource";
import { announceSaveSync } from "./saveSyncEvents";
import { saveSyncToastBody } from "./saveSyncToast";
import { applyLaunchGateSetupOutcome, resolveSaveSetupOutcome } from "./saveSetup";
import { showToast } from "./toast";
import { TimeoutError, boundedOr, withTimeout } from "./withTimeout";

interface PreLaunchSyncOptions {
  /** Prefixes every log line, naming the launch path. */
  tag: string;
  /** Whether a successful sync that moved saves is toasted (`saveSyncToastBody`). */
  toastSyncResult: boolean;
  /** The question put to the user once the sync has run past its limit. */
  askSyncWait: LaunchPrompts["askSyncWait"];
}

type PreLaunchSyncResult = Awaited<ReturnType<typeof preLaunchSync>>;

/** How the call settled: its answer, or why it failed. */
type PreLaunchSyncSettled = { result: PreLaunchSyncResult } | { failure: unknown };

/** How the call settled within `ms`, `undefined` past it. */
async function settledWithin(
  call: Promise<PreLaunchSyncSettled>,
  ms: number,
): Promise<PreLaunchSyncSettled | undefined> {
  try {
    return await withTimeout(call, ms);
  } catch (e) {
    if (e instanceof TimeoutError) return undefined;
    throw e;
  }
}

function isBenignSkip(result: PreLaunchSyncResult): boolean {
  return result.reason !== undefined && BENIGN_SYNC_SKIP_REASONS.includes(result.reason);
}

/** Whether a start may go ahead on how the call settled with nothing more to ask. */
function startsOn(settled: PreLaunchSyncSettled): boolean {
  if (!("result" in settled)) return false;
  const { result } = settled;
  return isBenignSkip(result) || (result.success && !result.conflicts?.length);
}

/**
 * Why the start still waits, read from the backend; a read that fails or gets
 * no answer within {@link LOCAL_CALL_LIMIT_MS} is Tender not answering. A sync
 * that just ended is still answered as syncing: its own answer is on its way.
 */
async function readSyncWaitReason(romId: number, tag: string): Promise<SyncWaitReason> {
  try {
    const state = await withTimeout(preLaunchSyncState(romId), LOCAL_CALL_LIMIT_MS);
    if (state.state === "waiting_for_sync") return { kind: "another_sync", holder: state.holder };
    if (state.state === "waiting_for_rom") return { kind: "this_game_busy" };
    return { kind: "syncing" };
  } catch (e) {
    logError(`${tag}: what the pre-launch sync is doing could not be read: ${e}`);
    return { kind: "not_answering" };
  }
}

/**
 * Stop the pre-launch sync before the game starts. Where Tender did not answer
 * what it is doing, the stop is sent without waiting for it: the start must not
 * hang on the backend that did not answer.
 */
async function stopTheSync(romId: number, tag: string, reason: SyncWaitReason): Promise<void> {
  const stop = withTimeout(stopPreLaunchSync(romId), LOCAL_CALL_LIMIT_MS).catch((e: unknown) => {
    logError(`${tag}: the pre-launch sync could not be stopped: ${e}`);
  });
  if (reason.kind !== "not_answering") await stop;
}

/**
 * Let the call the start no longer waits for run to its end: its answer only tells the game's page
 * to re-read its saves, and is toasted as an answered one would be.
 */
function finishInBackground(call: Promise<PreLaunchSyncSettled>, romId: number, options: PreLaunchSyncOptions) {
  detach(
    call.then((settled) => {
      announceSaveSync(romId);
      if (!("result" in settled)) {
        logError(`${options.tag}: the pre-launch sync the start no longer waits for failed: ${settled.failure}`);
        return;
      }
      const { result } = settled;
      detach(
        debugLog(
          `${options.tag}: the pre-launch sync the start no longer waits for answered: success=${result.success} reason=${result.reason ?? ""}`,
        ),
      );
      if (result.success && options.toastSyncResult) {
        const toastBody = saveSyncToastBody(result.uploaded, result.downloaded);
        if (toastBody) showToast(toastBody);
      }
    }),
  );
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
 * - A sync that has not answered within {@link SERVER_CALL_LIMIT_MS} goes on,
 *   and the user is asked why the start waits (`askSyncWait`). Wait waits
 *   {@link SYNC_WAIT_MORE_MS} more and asks again; Play anyway stops the sync
 *   and answers `play_anyway`; Cancel answers `cancel` and leaves the sync to
 *   finish in the background.
 */
export async function runPreLaunchSync(romId: number, options: PreLaunchSyncOptions): Promise<PreLaunchSyncOutcome> {
  const { tag } = options;
  const call: Promise<PreLaunchSyncSettled> = preLaunchSync(romId).then(
    (result) => ({ result }),
    (failure: unknown) => ({ failure }),
  );
  let settled = await settledWithin(call, SERVER_CALL_LIMIT_MS);
  while (settled === undefined) {
    const reason = await readSyncWaitReason(romId, tag);
    const choice = await options.askSyncWait(
      reason,
      call.then((s) => (startsOn(s) ? "ready" : "answered")),
    );
    if (choice === "cancel") {
      finishInBackground(call, romId, options);
      return { chose: "cancel" };
    }
    if (choice === "play_anyway") {
      await stopTheSync(romId, tag, reason);
      finishInBackground(call, romId, options);
      return { chose: "play_anyway", noAnswer: reason.kind === "not_answering" };
    }
    settled = choice === "answered" ? await call : await settledWithin(call, SYNC_WAIT_MORE_MS);
  }
  return shapePreLaunchAnswer(settled, options);
}

function shapePreLaunchAnswer(settled: PreLaunchSyncSettled, options: PreLaunchSyncOptions): PreLaunchSyncAnswer {
  const { tag } = options;
  if (!("result" in settled)) {
    logError(`${tag}: pre-launch sync failed (surfacing fallback confirm): ${settled.failure}`);
    return { success: false, message: "" };
  }
  const { result } = settled;

  detach(
    debugLog(
      `${tag}: preLaunchSync result: synced=${result.synced} conflicts=${result.conflicts?.length ?? 0} success=${result.success}`,
    ),
  );

  if (isBenignSkip(result)) {
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
