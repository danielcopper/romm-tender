/**
 * Shared pre-launch gate (ADR-0015). One funnel both the Play button and the
 * global launch watcher run before a RomM ROM is allowed to start.
 *
 * `runLaunchGate` SEQUENCES the gate steps and returns a {@link GateVerdict} —
 * it shows NO modals or toasts itself. Every side-effecting operation is
 * injected as a callback (see {@link LaunchGateOps}, built by
 * `launchGateOps.ts`), so the gate is a pure decision tree; `launchVerdict.ts`
 * acts on its verdict, through prompts and hooks each launch path supplies.
 *
 * The gate also owns the cross-cutting skip-set (`markLaunchSkipped` /
 * `consumeLaunchSkip`): a one-shot handshake by which a caller about to start a
 * game tells the global watcher "I have already handled this start — don't
 * gate it". It also holds the `reason` slugs that mean a save sync was skipped
 * benignly — unrelated to that skip-set — because the gate's callers read the
 * pre-launch sync's outcome against them.
 */

import type { SyncConflict } from "../types";
import { logError, type PreLaunchSyncHolder } from "../api/backend";
import { TimeoutError } from "./withTimeout";

/**
 * The limit on a backend call that does not read or sync the server's saves:
 * a read that stays on this machine, or the reachability probe, whose own
 * heartbeat gives up after about 3 s.
 */
export const LOCAL_CALL_LIMIT_MS = 5000;

/**
 * The limit on a launch-check call that reads or syncs the server's saves: the
 * save-setup read, and how long the pre-launch sync is waited for before the
 * user is asked whether to wait longer.
 */
export const SERVER_CALL_LIMIT_MS = 15000;

/** How much longer the start waits for the pre-launch sync each time the user chooses Wait. */
export const SYNC_WAIT_MORE_MS = 15000;

/** What the fallback dialog says when a step of the check got no answer in time. */
export const NO_ANSWER_MESSAGE = "Couldn't check your saves in time";

/** The `reason` slug the sync endpoints return when save sync is blocked because
 *  a save Tender could otherwise sync is written beside the game file —
 *  RetroArch's "Write Saves to Content Directory" is the usual cause (#239).
 *  A BENIGN SKIP — the game still launches and no error is surfaced. Mirrors the backend
 *  `SAVE_SYNC_CONTENT_DIR_REASON` (`domain/save_answer.py`). */
export const SAVEFILES_IN_CONTENT_DIR_REASON = "savefiles_in_content_dir";

/** The `reason` slug the sync endpoints return when the game's emulator does not
 *  keep a per-game save file set Tender can carry — a shared card, a save
 *  written inside the game file, a name with a hole in it, or a shape nobody has
 *  established (#1858). A BENIGN SKIP for the same reason as the slug above:
 *  nothing went wrong, there is simply nothing to carry, and the game still
 *  launches. Mirrors the backend `SAVE_SHAPE_UNSUPPORTED_REASON` (`domain/save_answer.py`). */
export const SAVE_SHAPE_UNSUPPORTED_REASON = "save_shape_unsupported";

/** Every `reason` slug that means "sync did not run, and that is fine". A slug
 *  outside this set is a real failure and surfaces the fallback-launch confirm,
 *  so a new benign skip that forgets to join it nags the user on every launch.
 *  The backend holds the same set in `domain/save_answer.py`; its
 *  `TestTheBenignSkipListsAgreeAcrossTheWire` parses THIS array and fails if the
 *  two disagree, so a slug added on either side alone is caught. */
export const BENIGN_SYNC_SKIP_REASONS: readonly string[] = [
  SAVEFILES_IN_CONTENT_DIR_REASON,
  SAVE_SHAPE_UNSUPPORTED_REASON,
];

/**
 * The pre-launch sync's answer, shaped after the `pre_launch_sync` endpoint
 * result the caller already consumes (`{ success, message, conflicts? }`).
 */
export interface PreLaunchSyncAnswer {
  success: boolean;
  message: string;
  conflicts?: SyncConflict[];
}

/**
 * Outcome of the injected pre-launch sync. The gate maps it onto the verdict:
 *   - `conflicts` non-empty            -> `{ decision: "conflict", conflicts }`
 *   - `success === false` (no conflict)-> `{ decision: "sync_failed", message }`
 *   - `chose: "cancel"`                -> `{ decision: "abort" }`
 *   - `chose: "play_anyway"`           -> `{ decision: "play_anyway", noAnswer }`
 *   - otherwise                        -> `{ decision: "allow" }`
 *
 * `chose` is the user's answer to the question the sync raises once it has run
 * past {@link SERVER_CALL_LIMIT_MS} ({@link SyncWaitReason}); `noAnswer` says
 * Tender did not answer what the sync was doing.
 */
export type PreLaunchSyncOutcome =
  PreLaunchSyncAnswer | { chose: "cancel" } | { chose: "play_anyway"; noAnswer: boolean };

/**
 * Why a start still waits for its pre-launch sync, as the question to the user
 * names it: another save sync holds the device (`holder` says which), something
 * else is busy with this game's saves, this game's sync itself is running, or
 * Tender did not answer what it is doing.
 */
export type SyncWaitReason =
  | { kind: "another_sync"; holder: PreLaunchSyncHolder }
  | { kind: "this_game_busy" }
  | { kind: "syncing" }
  | { kind: "not_answering" };

/**
 * How the pre-launch sync ended while the question was open: `ready` when the
 * start can go ahead on its answer, `answered` when that answer needs acting on
 * first (a conflict, a failure).
 */
export type SyncFinish = "ready" | "answered";

/**
 * The user's answer to the question: wait {@link SYNC_WAIT_MORE_MS} more, give
 * up the start, play on the local saves, or go on with the sync's answer — the
 * ready question's Play, or the question closing itself on an answer to act on.
 */
export type SyncWaitChoice = "wait" | "cancel" | "play_anyway" | "answered";

/**
 * What the launch check learned of the source that would start the game:
 * `switchedOff` is its kind while the user switched it off, `null` while not.
 * `checked: false` where the check could not tell.
 */
export type StartingSourceAnswer = { checked: true; switchedOff: string | null } | { checked: false };

/**
 * The gate's decision. Callers act on it; the gate never renders.
 *
 *   - `allow`            — every gate passed (or sync produced no blocker, or an
 *                          internal error was swallowed). Launch may proceed.
 *   - `block`            — a hard precondition failed and the user has not yet
 *                          been shown UI for it. `reason` selects the caller's
 *                          message (`not_installed`, `migration_pending`,
 *                          `no_launch_target`, `source_switched_off` with
 *                          the kind of the `source` switched off).
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
 *   - `play_anyway`      — the pre-launch sync ran past its limit and the user
 *                          chose to play on the local saves; it was asked to
 *                          stop. `noAnswer` when Tender did not answer what the
 *                          sync was doing.
 */
export type GateVerdict =
  | { decision: "allow" }
  | { decision: "block"; reason: "not_installed" | "migration_pending" | "no_launch_target" }
  | { decision: "block"; reason: "source_switched_off"; source: string }
  | { decision: "abort" }
  | { decision: "conflict"; conflicts: SyncConflict[] }
  | { decision: "offline_drift" }
  | { decision: "sync_failed"; message: string; noAnswer?: true }
  | { decision: "play_anyway"; noAnswer: boolean };

/**
 * Injected operations for {@link runLaunchGate}. Every side effect the gate
 * needs is a callback so the gate body itself touches no DOM, network, or
 * module state — which makes it fully unit-testable with stubs.
 *
 * An op bounds each backend call it makes ({@link LOCAL_CALL_LIMIT_MS},
 * {@link SERVER_CALL_LIMIT_MS}) and lets an expired limit's
 * {@link TimeoutError} through: its own fallback answers a call that failed,
 * the gate answers one that got no answer. The pre-launch sync is the
 * exception: past its limit the call goes on and its op asks the user. A
 * dialog an op shows is never bounded — the user's answer is worth
 * waiting for.
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
   * Is the source that would start the game switched off in Settings ›
   * Emulator sources? A kind blocks with `block`/`source_switched_off`: the
   * shortcut would start the game through that source regardless. Where the
   * check could not tell, the gate asks {@link confirmUncheckedStartingSource}.
   * Answers rather than rejects on an expired limit, so this step never ends in
   * the no-answer `sync_failed`.
   */
  readStartingSource: () => Promise<StartingSourceAnswer>;

  /**
   * Ask whether to start though the check could not tell whether the source is
   * switched off. `true` runs the rest of the gate; `false` aborts.
   */
  confirmUncheckedStartingSource: () => Promise<boolean>;

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
   * `conflict` / `sync_failed` / `abort` / `play_anyway` / `allow` (see
   * {@link PreLaunchSyncOutcome}).
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
 * Step 3 of {@link runLaunchGate}: the verdict on the source that would start
 * the game, or `null` to go on. Where that could not be checked, the user
 * decides; Cancel bails silently.
 */
async function startingSourceVerdict(ops: LaunchGateOps): Promise<GateVerdict | null> {
  const source = await ops.readStartingSource();
  if (!source.checked) {
    return (await ops.confirmUncheckedStartingSource()) ? null : { decision: "abort" };
  }
  if (source.switchedOff !== null) {
    return { decision: "block", reason: "source_switched_off", source: source.switchedOff };
  }
  return null;
}

/** Step 7a of {@link runLaunchGate}: the verdict on the pre-launch sync's outcome ({@link PreLaunchSyncOutcome}). */
function preLaunchSyncVerdict(sync: PreLaunchSyncOutcome): GateVerdict {
  if ("chose" in sync) {
    return sync.chose === "cancel" ? { decision: "abort" } : { decision: "play_anyway", noAnswer: sync.noAnswer };
  }
  if (sync.conflicts && sync.conflicts.length > 0) {
    return { decision: "conflict", conflicts: sync.conflicts };
  }
  if (!sync.success) {
    return { decision: "sync_failed", message: sync.message };
  }
  return { decision: "allow" };
}

/**
 * Run the pre-launch gate for `appId` / `romId` and return a verdict. Shows no
 * UI — the caller acts on the verdict.
 *
 * Step order (each step's failure short-circuits the rest):
 *   1. migration pending      -> block / migration_pending
 *   2. hasLaunchTarget        -> block / no_launch_target
 *   3. readStartingSource     -> block / source_switched_off;
 *                                unchecked => confirmUncheckedStartingSource, cancel => abort
 *   4. ensureTrackingConfigured -> "abort" => abort
 *   5. checkCoreChange        -> cancel => abort
 *   6. checkReachability      -> online vs offline split
 *   7a. online:  preLaunchSync -> conflict | sync_failed | abort | play_anyway | allow
 *   7b. offline: checkLocalDrift -> offline_drift | allow
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

    // 3. The source that would start the game is switched off — the start
    //    would go through it anyway. Block before the save-sync work, for the
    //    same reason as step 2.
    const sourceVerdict = await startingSourceVerdict(ops);
    if (sourceVerdict !== null) {
      return sourceVerdict;
    }

    // 4. Save-slot tracking setup. "abort" means the user saw setup UI and
    //    declined — bail silently.
    if ((await ops.ensureTrackingConfigured()) === "abort") {
      return { decision: "abort" };
    }

    // 5. Emulator core-change confirm. Cancel => bail silently.
    if (!(await ops.checkCoreChange())) {
      return { decision: "abort" };
    }

    // 6. Fresh reachability probe decides the sync branch.
    const online = await ops.checkReachability();

    if (online) {
      // 7a. Online — run pre-launch sync and map its outcome.
      return preLaunchSyncVerdict(await ops.preLaunchSync());
    }

    // 7b. Offline — block only when the local save has drifted; otherwise allow.
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
    // swallowed with zero trace. Each step answers a failed backend read itself,
    // so a throw that reaches here, timeouts aside, is one no step answered: the
    // setup step's slot confirm or side effects, a prompt, or a bug.
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
