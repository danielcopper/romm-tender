/**
 * Shared pre-launch gate (ADR-0015). One funnel both the Play button and the
 * global launch watcher run before a RomM ROM is allowed to start.
 *
 * `runLaunchGate` SEQUENCES the gate steps and returns a {@link GateVerdict} —
 * it shows NO modals or toasts itself. Every side-effecting operation is
 * injected as a callback (see {@link LaunchGateOps}), so the gate is a pure
 * decision tree that callers act on: each caller maps the verdict onto its own
 * UI (the Play button drives in-place button states; the watcher drives
 * imperative modals).
 *
 * The gate also owns the cross-cutting skip-set (`markLaunchSkipped` /
 * `consumeLaunchSkip`): a one-shot handshake by which a caller about to start a
 * game tells the global watcher "I have already handled this start — don't
 * gate it".
 */

import type { SyncConflict } from "../types";
import { logError } from "../api/backend";
import { TimeoutError } from "./withTimeout";

/** The limit on a launch-check call that stays on this machine. */
export const LOCAL_CALL_LIMIT_MS = 5000;

/**
 * The limit on a launch-check call that reaches RomM: the save-setup read and
 * the pre-launch sync.
 */
export const SERVER_CALL_LIMIT_MS = 15000;

/** What the fallback dialog says when a step of the check got no answer in time. */
export const NO_ANSWER_MESSAGE = "Couldn't check your saves in time";

/**
 * Outcome of the injected pre-launch sync, shaped after the
 * `pre_launch_sync` endpoint result the caller already consumes
 * (`{ success, message, conflicts? }`). The gate maps it onto the verdict:
 *   - `conflicts` non-empty            -> `{ decision: "conflict", conflicts }`
 *   - `success === false` (no conflict)-> `{ decision: "sync_failed", message }`
 *   - otherwise                        -> `{ decision: "allow" }`
 */
export interface PreLaunchSyncOutcome {
  success: boolean;
  message: string;
  conflicts?: SyncConflict[];
}

/**
 * The gate's decision. Callers act on it; the gate never renders.
 *
 *   - `allow`            — every gate passed (or sync produced no blocker, or an
 *                          internal error was swallowed). Launch may proceed.
 *   - `block`            — a hard precondition failed and the user has not yet
 *                          been shown UI for it. `reason` selects the caller's
 *                          message (`not_installed`, `migration_pending`,
 *                          `no_launch_target`).
 *   - `abort`            — the user was shown UI (tracking-setup or core-change)
 *                          and chose not to proceed. The caller bails silently,
 *                          with no further message — the user already decided.
 *   - `conflict`         — pre-launch sync surfaced save conflicts; the caller
 *                          resolves them (e.g. SyncConflictModal).
 *   - `offline_drift`    — server unreachable AND the local save has drifted
 *                          since the last sync; the caller asks whether to play
 *                          anyway (OfflineDriftModal).
 *   - `sync_failed`      — pre-launch sync ran online but failed; `message`
 *                          carries the backend reason for the caller's confirm.
 *                          With `noAnswer`, a step got no answer within its
 *                          limit instead, and `message` is
 *                          {@link NO_ANSWER_MESSAGE}.
 */
export type GateVerdict =
  | { decision: "allow" }
  | { decision: "block"; reason: "not_installed" | "migration_pending" | "no_launch_target" }
  | { decision: "abort" }
  | { decision: "conflict"; conflicts: SyncConflict[] }
  | { decision: "offline_drift" }
  | { decision: "sync_failed"; message: string; noAnswer?: true };

/**
 * Injected operations for {@link runLaunchGate}. Every side effect the gate
 * needs is a callback so the gate body itself touches no DOM, network, or
 * module state — which makes it fully unit-testable with stubs.
 *
 * An op bounds each backend call it makes ({@link LOCAL_CALL_LIMIT_MS},
 * {@link SERVER_CALL_LIMIT_MS}) and lets an expired limit's
 * {@link TimeoutError} through: its own fallback answers a call that failed,
 * the gate answers one that got no answer. A dialog an op shows is never
 * bounded — the user's answer is worth waiting for.
 */
export interface LaunchGateOps {
  /**
   * Synchronous in-memory check: is a RetroDECK migration pending? When true
   * the gate blocks immediately (`block`/`migration_pending`) — launching with
   * a pending migration risks silent save-data loss.
   */
  migrationPending: () => boolean;

  /**
   * Does this ROM have a launch target at all? `false` for a ROM that is
   * downloaded and on disk but whose content the system cannot boot — its
   * shortcut carries no launch command, so letting the launch through would
   * start nothing and say nothing. Blocks with `block`/`no_launch_target`.
   */
  hasLaunchTarget: () => Promise<boolean>;

  /**
   * Ensure save-slot tracking is configured for this ROM. Returns `"proceed"`
   * to continue, or `"abort"` when the user was shown setup UI and declined
   * (the gate then returns `{ decision: "abort" }`).
   */
  ensureTrackingConfigured: () => Promise<"proceed" | "abort">;

  /**
   * Surface the emulator core-change confirm if the core changed since the last
   * launch. Returns `true` to proceed, `false` when the user cancelled (the
   * gate then returns `{ decision: "abort" }`).
   */
  checkCoreChange: () => Promise<boolean>;

  /**
   * Fresh reachability probe (wraps `probe_reachability`). `true` routes to the
   * online pre-launch sync; `false` routes to the offline drift check.
   */
  checkReachability: () => Promise<boolean>;

  /**
   * Online branch: run pre-launch save sync. The gate maps its outcome onto
   * `conflict` / `sync_failed` / `allow` (see {@link PreLaunchSyncOutcome}).
   */
  preLaunchSync: () => Promise<PreLaunchSyncOutcome>;

  /**
   * Offline branch: has the local save drifted since the last sync (wraps
   * `check_local_drift`)? `true` -> `{ decision: "offline_drift" }`, else
   * `{ decision: "allow" }`.
   */
  checkLocalDrift: () => Promise<boolean>;
}

/**
 * Run the pre-launch gate for `appId` / `romId` and return a verdict. Shows no
 * UI — the caller acts on the verdict.
 *
 * Step order (each step's failure short-circuits the rest):
 *   1. migration pending      -> block / migration_pending
 *   2. hasLaunchTarget        -> block / no_launch_target
 *   3. ensureTrackingConfigured -> "abort" => abort
 *   4. checkCoreChange        -> cancel => abort
 *   5. checkReachability      -> online vs offline split
 *   6a. online:  preLaunchSync -> conflict | sync_failed | allow
 *   6b. offline: checkLocalDrift -> offline_drift | allow
 *
 * The gate NEVER throws and NEVER blocks the user on an internal error: the
 * whole body is wrapped so any thrown error (from an injected callback or
 * otherwise) resolves to `{ decision: "allow" }`. A bug in the gate must never
 * trap the user's game behind it. A step that got no answer within its limit is
 * not such an error: it resolves to `sync_failed` with `noAnswer`, so the
 * caller asks the user rather than starting on a save nothing checked or
 * waiting forever.
 *
 * `_appId` / `_romId` are accepted so callers pass the identifiers the injected
 * ops were bound for (and to keep the signature stable as ops grow); the gate
 * itself routes purely through the callbacks, so they are intentionally unused.
 */
export async function runLaunchGate(_appId: number, _romId: number, ops: LaunchGateOps): Promise<GateVerdict> {
  try {
    // 1. Pending RetroDECK migration — hard block before any other work.
    if (ops.migrationPending()) {
      return { decision: "block", reason: "migration_pending" };
    }

    // 2. No launch target — the ROM is downloaded but nothing in it is
    //    something this system can boot. Block before the save-sync work: there
    //    is no session coming that a synced save would belong to.
    if (!(await ops.hasLaunchTarget())) {
      return { decision: "block", reason: "no_launch_target" };
    }

    // 3. Save-slot tracking setup. "abort" means the user saw setup UI and
    //    declined — bail silently.
    if ((await ops.ensureTrackingConfigured()) === "abort") {
      return { decision: "abort" };
    }

    // 4. Emulator core-change confirm. Cancel => bail silently.
    if (!(await ops.checkCoreChange())) {
      return { decision: "abort" };
    }

    // 5. Fresh reachability probe decides the sync branch.
    const online = await ops.checkReachability();

    if (online) {
      // 6a. Online — run pre-launch sync and map its outcome.
      const sync = await ops.preLaunchSync();
      if (sync.conflicts && sync.conflicts.length > 0) {
        return { decision: "conflict", conflicts: sync.conflicts };
      }
      if (!sync.success) {
        return { decision: "sync_failed", message: sync.message };
      }
      return { decision: "allow" };
    }

    // 6b. Offline — block only when the local save has drifted; otherwise allow.
    if (await ops.checkLocalDrift()) {
      return { decision: "offline_drift" };
    }
    return { decision: "allow" };
  } catch (e) {
    if (e instanceof TimeoutError) {
      logError(`runLaunchGate: a step got no answer (${e.message}) — asking whether to launch on local saves`);
      return { decision: "sync_failed", message: NO_ANSWER_MESSAGE, noAnswer: true };
    }
    // Never trap the user's game behind a gate bug — fail open to "allow". The
    // log leaves a breadcrumb so a gate bug that should have blocked isn't
    // swallowed with zero trace. After the watcher's preLaunchSync op handles
    // its own throws, this catch is only reached on a truly-unexpected error.
    logError(`runLaunchGate threw (failing open to allow): ${e}`);
    return { decision: "allow" };
  }
}

// ---------------------------------------------------------------------------
// Skip-set — shared one-shot handshake between a caller that starts a game
// itself and the watcher.
//
// When a caller (Play button or watcher) has already handled a start — run the
// gate, or decided the start needs none — and is about to start the game
// itself, it marks the appId here. The global watcher checks (and consumes) the
// mark at its entry so it does not gate a start the caller already handled. The
// check is one-shot: `consumeLaunchSkip` deletes the mark as it reads it, so a
// later genuine launch of the same appId is gated normally.
//
// A mark lets a start through only within LAUNCH_SKIP_WINDOW_MS of being set.
// Steam reported the start that follows the Play button's RunGame well inside
// that window. A start Steam never reports would otherwise leave the mark
// standing, and the next direct start of that game — minutes or days later —
// would go through ungated.
// ---------------------------------------------------------------------------

export const LAUNCH_SKIP_WINDOW_MS = 10_000;

const _skipMarkedAt = new Map<number, number>();

function isWithinSkipWindow(markedAt: number, now: number): boolean {
  return now - markedAt <= LAUNCH_SKIP_WINDOW_MS;
}

/**
 * Mark `appId` as a start a caller has already handled, so the next watcher
 * pass within the window lets it through once.
 */
export function markLaunchSkipped(appId: number): void {
  const now = Date.now();
  for (const [markedId, markedAt] of _skipMarkedAt) {
    if (!isWithinSkipWindow(markedAt, now)) _skipMarkedAt.delete(markedId);
  }
  _skipMarkedAt.set(appId, now);
}

/**
 * One-shot check-and-delete: returns `true` if `appId` was marked skipped
 * within the window, else `false`. The mark is cleared either way, so the next
 * launch of the same appId is gated normally.
 */
export function consumeLaunchSkip(appId: number): boolean {
  const markedAt = _skipMarkedAt.get(appId);
  if (markedAt === undefined) return false;
  _skipMarkedAt.delete(appId);
  return isWithinSkipWindow(markedAt, Date.now());
}
