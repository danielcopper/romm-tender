/**
 * A save surface's first slot load, shared by every surface: the caller owns
 * the load-once gate and when a run starts, and orders runs against its slot
 * refreshes through the `isOvertaken` it passes.
 */

import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { getSaveSlots } from "../api/backend";
import { beginServerLoad, reportServerReachable, setServerRetryProgress, settleServerLoad } from "./connectionState";
import { applyLoadSlotsResult, type LoadSlotsFields } from "./slotState";

/** One run of the slot fetch. The effect's cleanup can tear a run down while it
 *  is still in flight, so the two ends share this record rather than a closure
 *  over each other's locals. */
export interface SlotLoadRun {
  cancelled: boolean;
  settled: boolean;
}

export interface SlotLoadTarget<S extends LoadSlotsFields> {
  slotsLoadedRef: MutableRefObject<boolean>;
  setState: Dispatch<SetStateAction<S>>;
  /** True once a newer slot read owns the list. `run.cancelled` cannot say it:
   *  the effect sets that only when React commits the teardown, so an answer
   *  arriving before that commit would still fold in (#1717). */
  isOvertaken: () => boolean;
  log: (message: string) => void;
}

/** Fetch the slot list for `romId` and fold it into the caller's state. */
export async function runSlotLoad<S extends LoadSlotsFields>(
  romId: number,
  run: SlotLoadRun,
  { slotsLoadedRef, setState, isOvertaken, log }: SlotLoadTarget<S>,
): Promise<void> {
  const load = beginServerLoad();
  // Drop stale retry progress from a prior load so the connecting indicator
  // starts at plain "Connecting to RomM…" (#1345).
  setServerRetryProgress(null);
  setState((prev) => ({ ...prev, slotsLoading: true }));
  try {
    const result = await getSaveSlots(romId);
    if (run.cancelled) return;
    run.settled = true;
    // A completed slot fetch is a reachability signal (#1345): a success
    // proves the server is reachable; an unreachable server drives the store
    // offline (which then renders the caller's known-offline fast path on its
    // next dependent run). Any OTHER failure reason is a server-side "no" (the
    // server answered), not a connectivity verdict — leave the store untouched.
    if (result.success) {
      reportServerReachable(true);
    } else if (result.reason === "server_unreachable") {
      reportServerReachable(false);
    }
    if (isOvertaken()) {
      // A newer slot read owns this lane, so nothing this one answers may land
      // — a failure included: it carries the ROM's last-known slots (#1755),
      // and folding those in after a version switch would file one version's
      // slot names under another. Two things are still owed. The spinner this
      // run put up, always. And, on a failure, the load-once gate reset that
      // lets a later visit retry — done here because the return below skips
      // the branch that normally does it.
      if (!result.success) slotsLoadedRef.current = false;
      setState((prev) => ({ ...prev, slotsLoading: false }));
      return;
    }
    applyLoadSlotsResult<S>(result, setState, slotsLoadedRef, log);
  } catch (e) {
    log(`failed to load save slots for romId=${romId}: ${e}`);
    if (!run.cancelled) {
      run.settled = true;
      slotsLoadedRef.current = false;
      setState((prev) => ({ ...prev, slotsLoading: false }));
    }
  } finally {
    // Clear-on-settle in addition to clear-on-start (#1345 F2) — refused
    // when a newer load of any lane already owns the shared frame.
    settleServerLoad(load);
  }
}

/** Release a run torn down before it settled — e.g. a concurrent call flipped
 *  the store offline and re-ran the effect. Gives the load-once gate back and
 *  drops the spinner, so the re-run isn't wedged behind a stuck
 *  slotsLoading/slotsLoadedRef and a later reconnect reliably reloads (#1345
 *  F2). A settled run already set its own final state — leave it alone. */
export function releaseUnsettledRun<S extends LoadSlotsFields>(
  run: SlotLoadRun,
  slotsLoadedRef: MutableRefObject<boolean>,
  setState: Dispatch<SetStateAction<S>>,
): void {
  if (run.settled) return;
  slotsLoadedRef.current = false;
  setState((prev) => (prev.slotsLoading ? { ...prev, slotsLoading: false } : prev));
}
