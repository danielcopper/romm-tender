/**
 * Acting on the launch gate's verdict — the half of a game start that every
 * launch path shares once {@link runLaunchGate} has decided. Each path supplies
 * only what is genuinely its own: the prompts it asks through, how it starts the
 * game, and what its trigger shows when a start ends without one.
 */

import {
  runLaunchGate,
  type GateVerdict,
  type LaunchGateOps,
  type SyncFinish,
  type SyncWaitChoice,
  type SyncWaitReason,
} from "./launchGate";
import { startingSourceOffSentence } from "./emulatorSourceWording";
import { announceSaveSync } from "./saveSyncEvents";
import { showToast } from "./toast";
import type { SyncConflict } from "../types";

/**
 * The six decisions a game start may have to put to the user, as questions
 * rather than widgets. `utils/` owns no UI, so a launch path declares what it
 * needs to ask and its surface supplies the answering dialogs.
 *
 * Asking directly would mean reaching from here into the modal modules, which
 * puts the launch funnel's control flow and its presentation in one knot: every
 * test of a gate branch then has to stand up six modal modules to get at it.
 */
export interface LaunchPrompts {
  /** Core changed since the last session — proceed with the new core? */
  confirmCoreChange(oldLabel: string, newLabel: string): Promise<boolean>;
  /** Walk the save conflicts; "resolved" once all are settled, "cancel" on the first dismissal. */
  resolveConflicts(conflicts: SyncConflict[]): Promise<"cancel" | "resolved">;
  /** Server unreachable and the local save has drifted — start anyway, re-probe, or give up? */
  askOfflineDrift(): Promise<"start_anyway" | "retry" | "cancel">;
  /** Pre-launch sync failed, or the check got no answer in time — launch on the local save regardless? */
  confirmFallbackLaunch(message?: string): Promise<boolean>;
  /** Whether the source that would start the game is switched off could not be checked — start anyway? */
  confirmUncheckedSource(): Promise<boolean>;
  /**
   * The pre-launch sync has run past the check's limit — wait longer, give up the start, or play on the local
   * saves? `finished` settles if the sync answers while the question is open: `ready` turns it into "your saves
   * are ready" (Play / Cancel), `answered` closes it with `answered`.
   */
  askSyncWait(reason: SyncWaitReason, finished: Promise<SyncFinish>): Promise<SyncWaitChoice>;
}

/**
 * What one launch path does around a verdict. Every settle callback is called
 * at most once per verdict and only on a branch that starts nothing.
 */
interface VerdictHooks {
  /** The core-change, unchecked-source and sync-wait questions are asked inside the gate, not here. */
  prompts: Pick<LaunchPrompts, "resolveConflicts" | "askOfflineDrift" | "confirmFallbackLaunch">;
  /**
   * Start the game. Every approving branch ends here; `skipReconfirm` is set
   * after a check that got no answer in time.
   */
  launch(options?: { skipReconfirm?: boolean }): Promise<void>;
  /**
   * The start ended without a launch, and the trigger has nothing else to show:
   * the gate aborted (the setup step stopped the start, or the user declined the
   * core-change or unchecked-source question), the user declined the
   * offline-drift or fallback prompt, or the gate blocked for a reason other
   * than a pending migration.
   */
  onDeclined(): void;
  /** The user dismissed a save conflict, so the conflict still stands. */
  onConflictCancelled(): void;
  /** The user asked to re-probe from the offline-drift prompt; the gate runs again next. */
  onRetry(): void;
  /** The gate blocked on a pending RetroDECK migration. */
  onMigrationBlocked(): void;
}

/**
 * Act on `verdict` for ROM `romId`. Returns "retry" only when the user asked to
 * re-probe from the offline-drift prompt — the caller re-runs the gate and acts
 * on the new verdict ({@link runGateLoop}); every other outcome is "done".
 */
export async function actOnGateVerdict(
  verdict: GateVerdict,
  romId: number,
  hooks: VerdictHooks,
): Promise<"done" | "retry"> {
  switch (verdict.decision) {
    case "allow":
      await hooks.launch();
      return "done";
    case "abort":
      hooks.onDeclined();
      return "done";
    case "block":
      if (verdict.reason === "migration_pending") {
        hooks.onMigrationBlocked();
        return "done";
      }
      // A switched-off source is stated nowhere on the game page, so the start
      // says why.
      if (verdict.reason === "source_switched_off") showToast(startingSourceOffSentence(verdict.source));
      hooks.onDeclined();
      return "done";
    case "conflict": {
      if ((await hooks.prompts.resolveConflicts(verdict.conflicts)) === "cancel") {
        hooks.onConflictCancelled();
        return "done";
      }
      announceSaveSync(romId);
      await hooks.launch();
      return "done";
    }
    case "offline_drift": {
      const choice = await hooks.prompts.askOfflineDrift();
      if (choice === "start_anyway") {
        await hooks.launch();
        return "done";
      }
      if (choice === "retry") {
        hooks.onRetry();
        return "retry";
      }
      hooks.onDeclined();
      return "done";
    }
    case "sync_failed": {
      if (await hooks.prompts.confirmFallbackLaunch(verdict.message)) {
        await hooks.launch({ skipReconfirm: verdict.noAnswer === true });
        return "done";
      }
      hooks.onDeclined();
      return "done";
    }
    case "play_anyway":
      await hooks.launch({ skipReconfirm: verdict.noAnswer });
      return "done";
  }
}

/**
 * Run the gate for `appId` / `romId` and act on its verdict, running it again for
 * as long as the user keeps choosing "Retry" on the offline-drift prompt. Each
 * retry re-probes connectivity, so a server back online launches through the
 * normal path and one still away asks again.
 *
 * `runLaunchGate` fails open to `allow` on internal errors, so a gate error
 * allows the launch. Any unexpected throw while acting on the verdict (a prompt
 * or launch rejecting) propagates out to the caller.
 */
export async function runGateLoop(
  appId: number,
  romId: number,
  ops: LaunchGateOps,
  hooks: VerdictHooks,
): Promise<void> {
  let verdict = await runLaunchGate(appId, romId, ops);
  // Each pass starts only after the user answered the previous verdict's prompt with "retry", so
  // the awaits are sequential by design. S9382 is raised on the two await lines, so their NOSONARs
  // must stay there; prettier-ignore stops Prettier from moving them into the bodies.
  // prettier-ignore
  while ((await actOnGateVerdict(verdict, romId, hooks)) === "retry") { // NOSONAR(typescript:S9382)
    verdict = await runLaunchGate(appId, romId, ops); // NOSONAR(typescript:S9382)
  }
}
