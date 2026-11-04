import { logError, prepareGameStart } from "../api/backend";
import type { GameStartResult } from "../api/backend";
import { sharedShortcutFileReader } from "./shortcutFile";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import {
  isPruneLeaseAdmissionCurrent,
  isPruneLeaseCancellation,
  releasePruneLease,
  withPruneLease,
  type PruneLeaseAdmission,
} from "./pruneLease";
import { startRefusal } from "./emulatorStart";

// Apply in bounded-concurrency batches (mirrors getExistingRomMShortcuts) so a
// reconcile touching many ROMs doesn't serialize worst-case per-shortcut
// confirm-poll timeouts.
const CONCURRENCY = 10;

/** Bound on preparing one start before it is refused. */
const PREPARE_TIMEOUT_MS = 3000;

/** What a start of a shortcut Tender places no game for says (#2221 D19). */
export const UNKNOWN_GAME_START = "Couldn't start the game: Tender has no game for this shortcut.";

/** What a start whose preparation got no answer in time says (#2221 D19). */
export const NOT_RESPONDING_START = "Tender isn't responding — couldn't start the game.";

/**
 * What preparing one start came to: `ready` — the launcher has the command and
 * the shortcut its appId, so Steam may start it; `refused` — no start, and
 * `sentence` says why; `cancelled` — the owner of the press is gone.
 */
export type PreparedStart = { status: "ready" } | { status: "refused"; sentence: string } | { status: "cancelled" };

type UnownedPreparedStart = Exclude<PreparedStart, { status: "cancelled" }>;

function cancelledByOwner(admission: PruneLeaseAdmission | undefined): boolean {
  return admission !== undefined && !isPruneLeaseAdmissionCurrent(admission);
}

/**
 * Keep watching a preparation that timed out: its lease is still held by the
 * backend, so a late success has to hand it back or it refuses every
 * cleanup's start until its TTL.
 */
function releaseLatePrepareLease(
  fetchOutcome: Promise<{ kind: "result"; item: GameStartResult } | { kind: "error"; error: unknown }>,
  context: string,
): void {
  void fetchOutcome
    .then(async (late) => {
      if (late.kind === "result" && late.item.success) {
        await releasePruneLease(late.item.prune_lease_token, `${context}: timed-out start preparation`);
      }
    })
    .catch((error: unknown) => logError(`${context}: late start preparation cleanup failed: ${error}`));
}

/** A start refused for an error, in the error's own words (#2221 D19). */
export function couldNotStart(error: unknown): string {
  return `Couldn't start the game: ${error instanceof Error ? error.message : String(error)}`;
}

/** Why the backend refused the start, in the words the game page uses for it where it names the game's start. */
function refusalSentence(item: Extract<GameStartResult, { success: false }>): string {
  return (item.start ? startRefusal(item.start, null, false) : null) ?? item.message;
}

type Prepared =
  { kind: "prepared"; item: Extract<GameStartResult, { success: true }> } | { kind: "verdict"; result: PreparedStart };

/**
 * Ask the backend to prepare the start, bounded by a timeout. Anything that is
 * not a prepared start is already the whole answer: no answer in time, a
 * failed call and a refusal each refuse the start (#2221 D19), and the
 * teardown of the owner whose admission is passed outranks them all.
 */
async function askToPrepare(
  romId: number,
  context: string,
  admission: PruneLeaseAdmission | undefined,
): Promise<Prepared> {
  const fetchOutcome = prepareGameStart(romId).then(
    (item) => ({ kind: "result" as const, item }),
    (error: unknown) => ({ kind: "error" as const, error }),
  );
  let timer!: ReturnType<typeof setTimeout>;
  const timeout = new Promise<{ kind: "timeout" }>((resolve) => {
    timer = setTimeout(() => resolve({ kind: "timeout" }), PREPARE_TIMEOUT_MS);
  });
  const outcome = await Promise.race([fetchOutcome, timeout]);
  if (outcome.kind === "timeout") {
    logError(`${context}: preparing the start timed out (start refused)`);
    releaseLatePrepareLease(fetchOutcome, context);
    if (cancelledByOwner(admission)) return { kind: "verdict", result: { status: "cancelled" } };
    return { kind: "verdict", result: { status: "refused", sentence: NOT_RESPONDING_START } };
  }
  clearTimeout(timer);
  if (outcome.kind === "result" && outcome.item.success) return { kind: "prepared", item: outcome.item };
  if (cancelledByOwner(admission)) return { kind: "verdict", result: { status: "cancelled" } };
  if (outcome.kind === "error") {
    logError(`${context}: preparing the start failed (start refused): ${outcome.error}`);
    return { kind: "verdict", result: { status: "refused", sentence: couldNotStart(outcome.error) } };
  }
  const item = outcome.item;
  if (item.success) return { kind: "prepared", item };
  logError(`${context}: the start was refused (${item.reason}): ${item.message}`);
  return { kind: "verdict", result: { status: "refused", sentence: refusalSentence(item) } };
}

/**
 * Prepare one start right before Steam starts the shortcut: the backend hands
 * the resolver's command to the launcher for this start (`prepare_game_start`,
 * #2221 D15), and the shortcut's launch options are confirm-set back to what it
 * answers, the appId (D16). A start whose preparation does not come about is
 * refused with a sentence saying why (D19). A failed Steam write does not stop
 * the start: the launcher reads only the appId, which the shortcut carries
 * already unless something replaced it. A timed-out call stays observed so a
 * late lease is released.
 */
export function prepareStart(romId: number, appId: number, context: string): Promise<UnownedPreparedStart>;
export function prepareStart(
  romId: number,
  appId: number,
  context: string,
  admission: PruneLeaseAdmission,
): Promise<PreparedStart>;
export async function prepareStart(
  romId: number,
  appId: number,
  context: string,
  admission?: PruneLeaseAdmission,
): Promise<PreparedStart> {
  if (cancelledByOwner(admission)) return { status: "cancelled" };
  const prepared = await askToPrepare(romId, context, admission);
  if (prepared.kind === "verdict") return prepared.result;
  const item = prepared.item;
  try {
    await withPruneLease(
      item.prune_lease_token,
      context,
      async (signal) => {
        if (signal.aborted) return;
        if (!(await setLaunchOptionsConfirmed(appId, item.launch_options))) {
          logError(`${context}: launch options of appId ${appId} not confirmed (starting anyway)`);
        }
      },
      context,
      admission,
    );
  } catch (e) {
    if (admission && isPruneLeaseCancellation(e, admission)) return { status: "cancelled" };
    logError(`${context}: setting the launch options failed (starting anyway): ${e}`);
  }
  return cancelledByOwner(admission) ? { status: "cancelled" } : { status: "ready" };
}

/**
 * Confirm-set the launch command on every shortcut in `items`, batching the
 * per-item confirm-polls so a large set doesn't serialize their timeouts.
 * The confirms of one batch share one reading of Steam's shortcut file, taken
 * at the batch's first confirm that needs it and never in a batch where every
 * confirm is settled by Steam's own reports. No-ops on a non-array or empty
 * list. A failed confirm (false return) or a thrown error is logged via
 * `logError` with the `context` prefix and the offending appId; the remaining
 * items are still processed.
 */
export async function batchConfirmLaunchOptions(
  items: { app_id: number; launch_options: string }[],
  context: string,
  signal?: AbortSignal,
): Promise<void> {
  if (!Array.isArray(items) || items.length === 0) return;
  for (let i = 0; i < items.length; i += CONCURRENCY) {
    const batch = items.slice(i, i + CONCURRENCY);
    const readFile = sharedShortcutFileReader(batch.map((item) => item.app_id));
    await Promise.all(
      batch.map(async (item) => {
        if (signal?.aborted) return;
        try {
          const ok = await setLaunchOptionsConfirmed(item.app_id, item.launch_options, undefined, readFile);
          if (!ok) {
            logError(`${context}: failed to confirm launch options for appId ${item.app_id}`);
          }
        } catch (e) {
          logError(`${context}: failed to set launch options for appId ${item.app_id}: ${e}`);
        }
      }),
    );
  }
}
