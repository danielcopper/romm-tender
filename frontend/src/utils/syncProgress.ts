/**
 * Module-level sync progress store — single source of truth.
 *
 * Updated by:
 *   - sync_progress events from the backend (persistent listener in index.tsx)
 *   - syncManager.ts during the frontend applying phase
 *   - MainPage on mount via getSyncStatus() (backend-authoritative seed)
 *   - the Sync page's own start and apply presses (optimistic running:true, and
 *     the retraction when a preview answers)
 *
 * Read by:
 *   - utils/syncRunView.ts, whose hook subscribes via onSyncProgressChange and
 *     re-renders its page on every notify (no setInterval polling)
 *   - utils/runUnitsStore.ts, whose mirror advances the run's per-unit rows
 *   - MainPage.tsx, which reads ``runId`` to scope a Cancel click to the active
 *     run (#1202).
 *
 * The ``runId`` field is fed straight from the backend ``sync_progress`` payload
 * (the persistent listener in index.tsx passes the whole event through), so it
 * is the single source of run identity frontend-side.
 *
 * **A run that has ended stays ended** — the one rule this store enforces on its
 * writers rather than merely holding what they say. See {@link
 * _terminatedRunIds}.
 */

import type { SyncProgress, SyncStage } from "../types";

const TERMINAL_STAGES: ReadonlySet<SyncStage> = new Set<SyncStage>(["done", "cancelled", "error"]);

/**
 * Whether a stage stops the run — the three the backend pairs with
 * ``running: false``, and the only frames that may end a watch.
 *
 * It lives here rather than with the run view because the store's own rule below
 * asks the same question, and a page that passes no run-end callbacks still has
 * to tell a run's end from its own optimistic frame being retracted: both are
 * ``running: false``, and only one of them ended anything.
 */
export function isTerminalStage(stage: SyncProgress["stage"]): boolean {
  return !!stage && TERMINAL_STAGES.has(stage);
}

/**
 * Static sub-slice shares of a running unit's coarse-bar width (#1407). A unit
 * is worked in three sequential phases — fetch (paginate the ROM list), covers
 * (download/refresh cover art), apply (create/update the Steam shortcuts) — and
 * each phase owns a fixed fraction of the unit's slice, so the bar advances
 * continuously through the whole unit instead of resting frozen until
 * ``applying``. The three sum to 1; apply dominates because it is the phase the
 * user waits on longest per shortcut. First-cut static weights (the issue's
 * agreed 15/25/60); the #1382 plan already knows per-unit item counts if
 * smarter per-phase apportioning is ever wanted.
 */
export const FETCH_SHARE = 0.15;
export const COVERS_SHARE = 0.25;
export const APPLY_SHARE = 0.6;

/**
 * The within-unit fill fraction (0..1) for the running unit, placed in the
 * phase's own sub-slice so the coarse bar never jumps backwards at a
 * fetch→covers→apply boundary (#1407). Each phase's slice fills by that phase's
 * own ``current/total``; a later phase's slice floor sits at the sum of the
 * earlier phases' shares, so transitions only ever move the bar forward — the
 * fetch and cover frames each restart ``current/total`` from zero, but land in a
 * strictly-higher band than the phase before.
 *
 * Phase resolution:
 *   - ``applying`` → the whole fetch+covers width is done; fill the apply slice
 *     (keyed on the stage alone, so a merged frontend apply frame that still
 *     carries a stale ``subStage`` is unaffected).
 *   - ``fetching`` + ``subStage: "covers"`` → fetch slice done, fill covers.
 *   - ``fetching`` + ``subStage: "fetch"`` → fill the fetch slice.
 *   - ``fetching`` with no sub-stage (the unit's coarse anchor, or an old
 *     backend) → 0: rest at the unit floor, the pre-#1407 behaviour.
 *   - any other stage (discovering, finalizing, …) → 0.
 *
 * A falsy ``current``/``total`` yields the phase's floor (its share sum so far),
 * e.g. a covers frame with ``total: 0`` reads ``FETCH_SHARE`` — never a divide.
 */
export function withinUnitFraction(progress: SyncProgress | null | undefined): number {
  const current = progress?.current ?? 0;
  const total = progress?.total ?? 0;
  const frac = current > 0 && total > 0 ? Math.min(1, current / total) : 0;
  if (progress?.stage === "applying") {
    return FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * frac;
  }
  if (progress?.stage === "fetching") {
    if (progress.subStage === "covers") return FETCH_SHARE + COVERS_SHARE * frac;
    if (progress.subStage === "fetch") return FETCH_SHARE * frac;
  }
  return 0;
}

/** No run, nothing said about one: what the store holds until something writes. */
function idleFrame(): SyncProgress {
  return { running: false, stage: "", current: 0, total: 0, message: "", runId: "" };
}

let _progress: SyncProgress = idleFrame();
let _listeners: Array<() => void> = [];

/**
 * The runs that have ended, by id. A run named here can never be put back in
 * flight.
 *
 * **Why the store carries this rather than the writers.** A run ends while the
 * frontend's own apply loop is still working: ``pacedForEach`` tests the cancel
 * flag at the END of an item (``utils/pacedOps.ts``) and the item before it is
 * entered from a shortcut scan that takes seconds, so at least one item is
 * always processed after the run is over — and every item writes
 * ``{running: true, stage: "applying", …, runId}`` (``utils/syncManager.ts``).
 * That write lands after both of the run's terminal signals, is the LAST thing
 * ever put in the store, and nothing follows it, so the page renders a run that
 * ended seconds ago until it is left and reopened (measured on a device, #1814:
 * four seconds late, the panel frozen on "Applying shortcuts" and its Cancel
 * stuck on "Cancelling…"). Two more writers have the same shape — the
 * cover-refresh loop and the chunk-init seed — and the frontend cancel flag they
 * consult is not even set for an ending that was nobody's cancel: a heartbeat
 * timeout, a budget pause, a backend error.
 *
 * So the rule belongs where the writers meet. Testing the cancel flag more often
 * would not be this rule: it narrows the window, and the write can always land
 * one instruction later.
 *
 * **What is recorded is a run's own account of its ending**: a stopping frame
 * carrying a terminal stage AND naming a run. Neither half alone will do. A stop
 * without a terminal stage is a page retracting the optimistic frame it wrote
 * itself, which ended no run; an unnamed frame is one the backend has not
 * stamped an id on yet, and recording ``""`` would make every later optimistic
 * start — ``running: true`` with no id — a resurrection of it.
 *
 * It grows by one short string per run that ends while the plugin is loaded.
 */
const _terminatedRunIds = new Set<string>();

/**
 * Notify every subscriber, each inside its own try/catch — a throwing subscriber
 * can neither starve later listeners nor break the emitting call site (e.g. the
 * per-item apply loop in syncManager, where a subscriber throw would otherwise
 * skip that game's shortcut creation). Console, not the ``logError`` backend
 * callable, at this store layer.
 */
function notify(): void {
  _listeners.forEach((fn) => {
    try {
      fn();
    } catch (e) {
      console.error("[RomM] sync-progress listener threw:", e);
    }
  });
}

/**
 * The one point both writers pass through, and where a terminated run is
 * refused.
 *
 * A refusal drops the frame WHOLE rather than correcting it to
 * ``running: false``: every field in it describes a run that is over, and the
 * terminal frame it would have replaced is exactly what the page should be
 * showing. Nothing is notified, because nothing changed.
 *
 * The question is asked of the frame the store would end up holding, not of the
 * patch — a merge that says nothing about ``running`` inherits it, so it is the
 * result that either asserts a run is in flight or does not.
 */
function commit(next: SyncProgress): void {
  const runId = next.runId ?? "";
  const named = runId !== "";
  if (named && next.running && _terminatedRunIds.has(runId)) return;
  if (named && !next.running && isTerminalStage(next.stage)) _terminatedRunIds.add(runId);
  _progress = next;
  notify();
}

export function setSyncProgress(p: SyncProgress): void {
  commit(p);
}

export function updateSyncProgress(p: Partial<SyncProgress>): void {
  commit({ ..._progress, ...p });
}

export function getSyncProgress(): SyncProgress {
  return _progress;
}

/**
 * Reset the module state between tests. Not for production use.
 *
 * The run ids are the half a test cannot reach any other way: a suite that
 * reuses one run id across cases would have the first case's ending refuse the
 * next case's start, which is the rule working rather than a rule to work
 * around. Subscribers are left alone — they are installed and torn down by the
 * hooks that own them.
 */
export function resetSyncProgressStoreForTests(): void {
  _progress = idleFrame();
  _terminatedRunIds.clear();
}

export function onSyncProgressChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}
