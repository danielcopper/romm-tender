import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  resetSyncProgressStoreForTests,
  setSyncProgress,
  updateSyncProgress,
  onSyncProgressChange,
  getSyncProgress,
  withinUnitFraction,
  FETCH_SHARE,
  COVERS_SHARE,
  APPLY_SHARE,
} from "./syncProgress";
import type { SyncProgress } from "../types";

// The store's notify() runs every subscriber inside its own try/catch so a
// throwing listener can neither starve later listeners nor break the emitting
// call site (the syncManager per-item apply loop calls updateSyncProgress inside
// its per-game try block — a propagated throw there would skip that game's
// shortcut creation entirely). The listener array is module-level and persists
// across tests; every test unsubscribes what it subscribes so none leak.
describe("syncProgress store notify hardening", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("a throwing first listener does not starve a later-subscribed listener", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const thrower = vi.fn(() => {
      throw new Error("boom");
    });
    const later = vi.fn();
    const unsub1 = onSyncProgressChange(thrower);
    const unsub2 = onSyncProgressChange(later);
    try {
      setSyncProgress({ running: true, stage: "applying" });
      // The earlier throw was isolated — the later listener still fired...
      expect(thrower).toHaveBeenCalledTimes(1);
      expect(later).toHaveBeenCalledTimes(1);
      // ...and the throw was reported to the console, not propagated.
      expect(errSpy).toHaveBeenCalledWith("[RomM] sync-progress listener threw:", expect.any(Error));
    } finally {
      unsub1();
      unsub2();
    }
  });

  it("updateSyncProgress does not throw when a subscriber throws (emitter is protected)", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const unsub = onSyncProgressChange(() => {
      throw new Error("boom");
    });
    try {
      // The emitting call site (e.g. syncManager's per-item apply loop) must not
      // see the subscriber's throw.
      expect(() => updateSyncProgress({ etaSeconds: 1000 })).not.toThrow();
      // The partial update still landed on the store.
      expect(getSyncProgress().etaSeconds).toBe(1000);
    } finally {
      unsub();
    }
  });

  it("unsubscribe removes the listener so it is no longer notified", () => {
    const fn = vi.fn();
    const unsub = onSyncProgressChange(fn);
    setSyncProgress({ running: false, stage: "" });
    expect(fn).toHaveBeenCalledTimes(1);
    unsub();
    setSyncProgress({ running: false, stage: "" });
    // No further notification after unsubscribe.
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

// The pure within-unit sub-slice model (#1407). The running unit's width is
// split into fetch → covers → apply bands, each filling by its own current/total
// within a strictly-higher band than the phase before, so the bar never jumps
// backwards at a fetch→covers→apply boundary even though each phase restarts
// current/total from zero.
describe("withinUnitFraction sub-slice model (#1407)", () => {
  const frame = (p: Partial<SyncProgress>): SyncProgress => ({ running: true, ...p });

  it("the three shares sum to the full unit width", () => {
    expect(FETCH_SHARE + COVERS_SHARE + APPLY_SHARE).toBeCloseTo(1, 10);
  });

  it("fetch sub-stage fills only the fetch share", () => {
    expect(withinUnitFraction(frame({ stage: "fetching", subStage: "fetch", current: 3, total: 10 }))).toBeCloseTo(
      FETCH_SHARE * 0.3,
      10,
    );
  });

  it("covers sub-stage starts at the fetch ceiling and fills the covers share", () => {
    expect(withinUnitFraction(frame({ stage: "fetching", subStage: "covers", current: 1, total: 4 }))).toBeCloseTo(
      FETCH_SHARE + COVERS_SHARE * 0.25,
      10,
    );
  });

  it("applying starts at the fetch+covers ceiling and fills the apply share", () => {
    expect(withinUnitFraction(frame({ stage: "applying", current: 1, total: 2 }))).toBeCloseTo(
      FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * 0.5,
      10,
    );
  });

  it("applying ignores a stale merged subStage (keyed on the stage alone)", () => {
    // A frontend apply frame merges over a prior covers frame, so it can still
    // carry subStage "covers"; the apply band must win regardless.
    expect(withinUnitFraction(frame({ stage: "applying", subStage: "covers", current: 1, total: 1 }))).toBeCloseTo(
      1,
      10,
    );
  });

  it("a full unit's apply completes exactly at 1.0", () => {
    expect(withinUnitFraction(frame({ stage: "applying", current: 10, total: 10 }))).toBeCloseTo(1, 10);
  });

  it("fetching with no sub-stage rests at the unit floor (0) — legacy/anchor behaviour", () => {
    expect(withinUnitFraction(frame({ stage: "fetching", current: 30, total: 62 }))).toBe(0);
    expect(withinUnitFraction(frame({ stage: "fetching", current: 0, total: 0 }))).toBe(0);
  });

  it("a falsy current/total yields the phase floor, never a divide", () => {
    // covers with total 0 → the fetch ceiling (its own share contributes 0).
    expect(withinUnitFraction(frame({ stage: "fetching", subStage: "covers", current: 0, total: 0 }))).toBeCloseTo(
      FETCH_SHARE,
      10,
    );
    // applying with total 0 → the fetch+covers ceiling.
    expect(withinUnitFraction(frame({ stage: "applying", current: 0, total: 0 }))).toBeCloseTo(
      FETCH_SHARE + COVERS_SHARE,
      10,
    );
    // fetch with total 0 → 0.
    expect(withinUnitFraction(frame({ stage: "fetching", subStage: "fetch", current: 0, total: 0 }))).toBe(0);
  });

  it("clamps an overshooting current/total to the phase ceiling", () => {
    expect(withinUnitFraction(frame({ stage: "applying", current: 99, total: 10 }))).toBeCloseTo(1, 10);
  });

  it("non-unit stages (discovering/finalizing) contribute no within-unit fill", () => {
    expect(withinUnitFraction(frame({ stage: "discovering", current: 1, total: 2 }))).toBe(0);
    expect(withinUnitFraction(frame({ stage: "finalizing", current: 1, total: 2 }))).toBe(0);
  });

  it("returns 0 for a null/undefined progress", () => {
    expect(withinUnitFraction(null)).toBe(0);
    expect(withinUnitFraction(undefined)).toBe(0);
  });

  it("is non-decreasing across a fetch → covers → apply frame sequence", () => {
    const frames: SyncProgress[] = [
      frame({ stage: "fetching", current: 0, total: 0 }),
      ...Array.from({ length: 7 }, (_, i) => frame({ stage: "fetching", subStage: "fetch", current: i + 1, total: 7 })),
      ...Array.from({ length: 100 }, (_, i) =>
        frame({ stage: "fetching", subStage: "covers", current: i + 1, total: 100 }),
      ),
      ...Array.from({ length: 50 }, (_, i) => frame({ stage: "applying", current: i + 1, total: 50 })),
    ];
    let previous = -1;
    for (const f of frames) {
      const value = withinUnitFraction(f);
      expect(value).toBeGreaterThanOrEqual(previous);
      previous = value;
    }
    expect(previous).toBeCloseTo(1, 10);
  });
});

// The freeze the device showed (#1814): a run ends while the frontend's own
// apply loop is still working, and the loop's next item writes the finished run
// back into the store. That write is the last thing ever put there, so the page
// stands on a run that is over — "Applying shortcuts", a Cancel stuck on
// "Cancelling…" — until it is left and reopened. The writers are many and the
// store is where they meet, so the rule is the store's.
//
// Every frame below is copied from a real writer: the apply loop's per-item
// write and the cover-refresh line (`utils/syncManager.ts`), the `sync_complete`
// merge (`index.tsx`), the backend's own terminal frame, and the Sync page's
// optimistic start and its retraction (`components/sync/useSyncPage.ts`).
describe("a run that has ended stays ended", () => {
  beforeEach(() => {
    resetSyncProgressStoreForTests();
  });

  /** The apply loop's per-item write, verbatim in shape. */
  const applyItem = (runId: string, current: number) =>
    updateSyncProgress({
      running: true,
      stage: "applying",
      current,
      total: 40,
      message: `Apple I: ${current}/40`,
      step: 1,
      totalSteps: 16,
      runId,
    });

  it("refuses the apply loop's next item after the run has been cancelled", () => {
    setSyncProgress({ running: true, stage: "applying", current: 0, total: 40, message: "", runId: "run-1" });
    // Both terminal signals, in the order the backend sends them.
    updateSyncProgress({ running: false, stage: "cancelled" });
    setSyncProgress({ running: false, stage: "cancelled", message: "Sync cancelled", runId: "run-1" });

    // Four seconds later, the item the loop was already inside completes.
    applyItem("run-1", 1);

    const frame = getSyncProgress();
    expect(frame.running).toBe(false);
    expect(frame.stage).toBe("cancelled");
    expect(frame.message).toBe("Sync cancelled");
  });

  it("refuses every later one too, so the page cannot be walked back into the run", () => {
    setSyncProgress({ running: true, stage: "applying", current: 0, total: 40, message: "", runId: "run-1" });
    setSyncProgress({ running: false, stage: "done", message: "Sync complete: 40 games", runId: "run-1" });

    applyItem("run-1", 1);
    applyItem("run-1", 2);
    applyItem("run-1", 3);

    expect(getSyncProgress().running).toBe(false);
    expect(getSyncProgress().message).toBe("Sync complete: 40 games");
  });

  it("notifies nobody for a refused write, because nothing changed", () => {
    setSyncProgress({ running: true, stage: "applying", runId: "run-1" });
    setSyncProgress({ running: false, stage: "done", message: "Sync complete", runId: "run-1" });
    const heard = vi.fn();
    const unsub = onSyncProgressChange(heard);
    try {
      applyItem("run-1", 1);
      expect(heard).not.toHaveBeenCalled();
    } finally {
      unsub();
    }
  });

  it("refuses the cover-refresh line, which runs on for an ending nobody cancelled", () => {
    // A heartbeat timeout, a budget pause or a backend error ends the run
    // without the frontend's cancel flag ever being set, so that loop runs to
    // completion writing over the terminal.
    setSyncProgress({ running: true, stage: "applying", runId: "run-1" });
    setSyncProgress({ running: false, stage: "error", message: "Sync failed", runId: "run-1" });

    updateSyncProgress({
      running: true,
      stage: "applying",
      message: "Apple I: covers 3/9",
      step: 1,
      totalSteps: 16,
      runId: "run-1",
      coverRefresh: true,
    });

    expect(getSyncProgress().running).toBe(false);
    expect(getSyncProgress().message).toBe("Sync failed");
  });

  it("lets the next run start: an optimistic frame names no run at all", () => {
    setSyncProgress({ running: true, stage: "applying", runId: "run-1" });
    setSyncProgress({ running: false, stage: "done", message: "Sync complete", runId: "run-1" });

    // What the Sync page writes at the press, before the backend has stamped an
    // id on anything.
    setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runKind: "preview" });

    expect(getSyncProgress().running).toBe(true);
    expect(getSyncProgress().message).toBe("Fetching library...");
  });

  it("records nothing for a run that ended before it was ever named", () => {
    // `sync_complete` merges its terminal stage over whatever frame stands, and
    // the frame standing can be the page's own optimistic start, which carries
    // no id because the backend had not stamped one. Recording THAT would make
    // every later optimistic start a resurrection of it, and the panel could
    // never show a run beginning again.
    setSyncProgress({ running: true, stage: "fetching", message: "Fetching library..." });
    updateSyncProgress({ running: false, stage: "cancelled" });

    setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runKind: "apply" });

    expect(getSyncProgress().running).toBe(true);
    expect(getSyncProgress().runKind).toBe("apply");
  });

  it("lets a different run through", () => {
    setSyncProgress({ running: true, stage: "applying", runId: "run-1" });
    setSyncProgress({ running: false, stage: "done", message: "Sync complete", runId: "run-1" });

    applyItem("run-2", 1);

    expect(getSyncProgress().running).toBe(true);
    expect(getSyncProgress().runId).toBe("run-2");
  });

  it("ends no run on a stop that carries no terminal stage", () => {
    // The Sync page retracting the optimistic frame it wrote itself: a preview
    // answered, so the frame goes — but no run ended, and the run whose id the
    // frame still carries must be able to go on.
    setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runId: "run-1" });
    updateSyncProgress({ running: false, stage: "" });

    applyItem("run-1", 1);

    expect(getSyncProgress().running).toBe(true);
    expect(getSyncProgress().stage).toBe("applying");
  });
});
