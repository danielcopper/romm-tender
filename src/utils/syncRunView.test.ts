import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useSyncRunView } from "./syncRunView";
import {
  resetSyncProgressStoreForTests,
  setSyncProgress,
  updateSyncProgress,
  FETCH_SHARE,
  COVERS_SHARE,
  APPLY_SHARE,
} from "./syncProgress";
import { beginEtaRun, liveEtaSeconds, resetEta } from "./syncEta";
import { attachRunUnitsMirror, recordUnitCreated, resetRunUnitsStoreForTests, seedRunUnits } from "./runUnitsStore";
import type { SyncPlanUnit } from "../types";

function planUnit(name: string): SyncPlanUnit {
  return { type: "platform", id: name, name, slug: name.toLowerCase(), rom_count: 10 };
}

describe("useSyncRunView", () => {
  let detachMirror: () => void;

  beforeEach(() => {
    resetEta();
    resetRunUnitsStoreForTests();
    // Not an idle frame but the whole store: these cases reuse one run id, and a
    // run this store has seen END can never be put back in flight.
    resetSyncProgressStoreForTests();
    detachMirror = attachRunUnitsMirror();
  });

  afterEach(() => {
    detachMirror();
  });

  describe("the coarse bar", () => {
    it("apportions the run by the plan's per-unit weights, not by unit count", () => {
      // Three units of unequal size. Unit 1 is done, unit 2 is half-applied.
      beginEtaRun("run-1", [300, 100, 100], 500);
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 3,
        current: 50,
        total: 100,
        message: "SNES: 50/100",
        runId: "run-1",
      });

      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        // Within the running unit the apply phase sits above the fetch and cover
        // shares, half-filled: 0.15 + 0.25 + 0.6 * 0.5.
        const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * 0.5;
        expect(result.current.coarseFraction).toBeCloseTo(((300 + within * 100) / 500) * 100, 5);
        // Equal-per-unit weighting would have read a much lower number, so the
        // assertion above is about the weights and not about arithmetic that
        // happens to agree.
        expect(result.current.coarseFraction).not.toBeCloseTo(((1 + within) / 3) * 100, 5);
      } finally {
        unmount();
      }
    });

    it("falls back to an equal share per unit when no plan is measured", () => {
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 3,
        current: 50,
        total: 100,
        message: "SNES: 50/100",
        runId: "run-1",
      });

      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * 0.5;
        expect(result.current.coarseFraction).toBeCloseTo(((1 + within) / 3) * 100, 5);
      } finally {
        unmount();
      }
    });

    it("is undefined before the run reaches a unit, which the bar reads as indeterminate", () => {
      setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runId: "" });

      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        expect(result.current.coarseFraction).toBeUndefined();
      } finally {
        unmount();
      }
    });
  });

  describe("the run's kind", () => {
    it("passes the backend's word through, and answers null where none was stated", () => {
      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        act(() => {
          setSyncProgress({ running: true, stage: "fetching", step: 1, totalSteps: 2, runId: "r", runKind: "apply" });
        });
        expect(result.current.runKind).toBe("apply");

        // The idle default carries an empty string, which is "not established"
        // and never one of the two answers.
        act(() => {
          setSyncProgress({ running: true, stage: "fetching", step: 1, totalSteps: 2, runId: "r", runKind: "" });
        });
        expect(result.current.runKind).toBeNull();
      } finally {
        unmount();
      }
    });
  });

  describe("the fine-detail line", () => {
    it("keeps the fine-detail row mounted across a unit boundary, and drops it when the run ends", () => {
      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        act(() => {
          setSyncProgress({
            running: true,
            stage: "applying",
            step: 1,
            totalSteps: 2,
            current: 40,
            total: 200,
            message: "PSX: 40/200",
            runId: "run-1",
          });
        });
        expect(result.current.hasFineDetail).toBe(true);
        expect(result.current.fineDetailText).toBe("PSX: 40/200");

        // The next unit's anchor frame resets the within-unit counters, so the
        // row would unmount for a frame without the carry.
        act(() => {
          setSyncProgress({
            running: true,
            stage: "fetching",
            step: 2,
            totalSteps: 2,
            current: 0,
            total: 0,
            message: "Fetching SNES",
            runId: "run-1",
          });
        });
        expect(result.current.hasFineDetail).toBe(true);
        expect(result.current.fineDetailText).toBe("Fetching SNES");

        act(() => {
          setSyncProgress({ running: false, stage: "done", message: "Sync complete: 200 games", runId: "run-1" });
        });
        expect(result.current.hasFineDetail).toBe(false);
        expect(result.current.fineDetailText).toBe("");
      } finally {
        unmount();
      }
    });

    it("keeps the prior line when a boundary anchor carries no message of its own", () => {
      // The carry REPLACES, never removes: an anchor with an empty message says
      // nothing about the next unit, so blanking the line on it would leave the
      // row mounted and empty for the rest of that unit's fetch.
      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        act(() => {
          setSyncProgress({
            running: true,
            stage: "applying",
            step: 1,
            totalSteps: 2,
            current: 40,
            total: 200,
            message: "PSX: 40/200",
            runId: "run-1",
          });
        });
        expect(result.current.fineDetailText).toBe("PSX: 40/200");

        act(() => {
          setSyncProgress({
            running: true,
            stage: "fetching",
            step: 2,
            totalSteps: 2,
            current: 0,
            total: 0,
            message: "",
            runId: "run-1",
          });
        });
        expect(result.current.hasFineDetail).toBe(true);
        expect(result.current.fineDetailText).toBe("PSX: 40/200");
      } finally {
        unmount();
      }
    });
  });

  describe("the estimate", () => {
    it("reads nothing while the run carries no estimate at all", () => {
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 1, message: "X: 1/2", runId: "run-1" });

      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        expect(result.current.etaText).toBeNull();
      } finally {
        unmount();
      }
    });

    it("reads the static seed as an upper bound until a rate is measured", () => {
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 1,
        totalSteps: 1,
        current: 1,
        total: 100,
        message: "X: 1/100",
        runId: "run-1",
        etaSeconds: 600,
      });

      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        expect(result.current.etaText).toBe("up to 10 min");
      } finally {
        unmount();
      }
    });

    it("replaces the seed with the measured countdown once the rate is known", () => {
      vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
      try {
        vi.setSystemTime(0);
        beginEtaRun("run-1", [54700], 54700);
        setSyncProgress({
          running: true,
          stage: "applying",
          step: 1,
          totalSteps: 1,
          current: 100,
          total: 54700,
          message: "X: 100/54700",
          runId: "run-1",
          etaSeconds: 600,
        });

        const { result, unmount } = renderHook(() => useSyncRunView());
        try {
          act(() => {
            updateSyncProgress({ current: 100 });
          });
          // One sample so far — the estimator has no span to measure yet.
          expect(result.current.etaText).toBe("up to 10 min");

          // 600 items in 6 s = 100/s over the remaining 54000 → 540 s.
          vi.setSystemTime(6000);
          act(() => {
            updateSyncProgress({ current: 700, message: "X: 700/54700" });
          });
          expect(result.current.etaText).toBe("9 min left");
        } finally {
          unmount();
        }
      } finally {
        vi.useRealTimers();
      }
    });

    it("is not measured from fetch frames, whose counters count pages and not items", () => {
      vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
      try {
        vi.setSystemTime(0);
        beginEtaRun("run-1", [54700], 54700);
        setSyncProgress({
          running: true,
          stage: "fetching",
          step: 1,
          totalSteps: 1,
          current: 5,
          total: 62,
          message: "Fetching (page 5/62)",
          runId: "run-1",
          etaSeconds: 600,
        });

        const { result, unmount } = renderHook(() => useSyncRunView());
        try {
          // A page-counter jump spanning the readiness window. Sampled as apply
          // progress it would read as a rate; the stage is what refuses it.
          vi.setSystemTime(8000);
          act(() => {
            updateSyncProgress({ current: 60, message: "Fetching (page 60/62)" });
          });
          expect(result.current.etaText).toBe("up to 10 min");
        } finally {
          unmount();
        }
      } finally {
        vi.useRealTimers();
      }
    });

    it("holds the countdown across a gap that re-arms the estimator, rather than blinking back to the seed", () => {
      // The estimator's readiness gate re-arms after every inter-unit fetch gap,
      // and a run's tail is small units that each apply in under five seconds and
      // never re-arm it. What holds the readout is the sticky deadline: a null
      // measurement keeps the last good one rather than falling to the seed.
      vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
      try {
        vi.setSystemTime(0);
        beginEtaRun("run-1", [54700], 54700);
        setSyncProgress({
          running: true,
          stage: "applying",
          step: 1,
          totalSteps: 1,
          current: 100,
          total: 54700,
          message: "X: 100/54700",
          runId: "run-1",
          etaSeconds: 600,
        });

        const { result, unmount } = renderHook(() => useSyncRunView());
        try {
          // The t=0 sample the estimator measures the first span against.
          act(() => {
            updateSyncProgress({ current: 100 });
          });
          vi.setSystemTime(6000);
          act(() => {
            updateSyncProgress({ current: 700, message: "X: 700/54700" });
          });
          expect(result.current.etaText).toBe("9 min left");

          // A fetch gap, then two applying frames close enough together that the
          // window ages down to a span under the readiness threshold.
          vi.setSystemTime(30000);
          act(() => {
            updateSyncProgress({ stage: "fetching", current: 20, total: 62, message: "Fetching (page 20/62)" });
          });
          vi.setSystemTime(33000);
          act(() => {
            updateSyncProgress({ stage: "applying", current: 800, total: 54700, message: "X: 800/54700" });
          });
          vi.setSystemTime(37000);
          act(() => {
            updateSyncProgress({ current: 900, message: "X: 900/54700" });
          });

          // Precondition: the estimator really has re-armed to null.
          expect(liveEtaSeconds()).toBeNull();
          expect(result.current.etaText).toContain("left");
          expect(result.current.etaText).not.toContain("up to");
        } finally {
          unmount();
        }
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("the run's end", () => {
    it("tears the live countdown down, so the next run does not inherit it", () => {
      vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
      try {
        vi.setSystemTime(0);
        beginEtaRun("run-1", [54700], 54700);
        setSyncProgress({
          running: true,
          stage: "applying",
          step: 1,
          totalSteps: 1,
          current: 100,
          total: 54700,
          message: "X: 100/54700",
          runId: "run-1",
          etaSeconds: 600,
        });

        const { result, unmount } = renderHook(() => useSyncRunView());
        try {
          act(() => {
            updateSyncProgress({ current: 100 });
          });
          vi.setSystemTime(6000);
          act(() => {
            updateSyncProgress({ current: 700, message: "X: 700/54700" });
          });
          expect(result.current.etaText).toBe("9 min left");

          // The run ends: the measured deadline goes with it, and a frame of the
          // next run reads its own seed rather than the dead run's countdown.
          act(() => {
            setSyncProgress({ running: false, stage: "done", message: "Sync complete", runId: "run-1" });
          });
          vi.setSystemTime(7000);
          act(() => {
            setSyncProgress({
              running: true,
              stage: "applying",
              step: 1,
              totalSteps: 1,
              current: 1,
              total: 100,
              message: "Y: 1/100",
              runId: "run-2",
              etaSeconds: 600,
            });
          });
          expect(result.current.etaText).toBe("up to 10 min");
        } finally {
          unmount();
        }
      } finally {
        vi.useRealTimers();
      }
    });

    function startedRun(runId: string): void {
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 1,
        totalSteps: 1,
        current: 1,
        total: 10,
        message: "PSX: 1/10",
        runId,
      });
    }

    it("calls onRunEnd once for the run it is watching, with the terminal frame", () => {
      const onRunEnd = vi.fn();
      startedRun("run-1");
      const { unmount } = renderHook(() => useSyncRunView({ onRunEnd }));
      try {
        act(() => {
          setSyncProgress({ running: false, stage: "done", message: "Sync complete: 10 games", runId: "run-1" });
        });

        expect(onRunEnd).toHaveBeenCalledTimes(1);
        expect(onRunEnd.mock.calls[0]?.[0]).toMatchObject({ stage: "done", message: "Sync complete: 10 games" });
      } finally {
        unmount();
      }
    });

    it("does not end the run twice — the second terminal frame only corrects the wording", () => {
      const onRunEnd = vi.fn();
      const onTerminalWording = vi.fn();
      startedRun("run-1");
      const { unmount } = renderHook(() => useSyncRunView({ onRunEnd, onTerminalWording }));
      try {
        // The merged sync_complete frame ends the run, keeping the mid-run message.
        act(() => {
          updateSyncProgress({ running: false, stage: "done" });
        });
        // The run's own terminal frame follows with the authoritative wording.
        act(() => {
          setSyncProgress({ running: false, stage: "done", message: "Sync complete: 10 games", runId: "run-1" });
        });

        expect(onRunEnd).toHaveBeenCalledTimes(1);
        expect(onTerminalWording).toHaveBeenCalledTimes(1);
        expect(onTerminalWording).toHaveBeenCalledWith("Sync complete: 10 games", "done");
      } finally {
        unmount();
      }
    });

    it("does not end the watched run on another run's terminal frame", () => {
      const onRunEnd = vi.fn();
      startedRun("run-1");
      const { unmount } = renderHook(() => useSyncRunView({ onRunEnd }));
      try {
        act(() => {
          setSyncProgress({ running: false, stage: "cancelled", message: "Sync cancelled", runId: "run-2" });
        });

        expect(onRunEnd).not.toHaveBeenCalled();
      } finally {
        unmount();
      }
    });

    it("announces nothing for the terminal frame a mount merely finds in the store", () => {
      const onRunEnd = vi.fn();
      setSyncProgress({ running: false, stage: "done", message: "Sync complete: 10 games", runId: "run-0" });
      const { unmount } = renderHook(() => useSyncRunView({ onRunEnd }));
      try {
        act(() => {
          updateSyncProgress({ message: "Sync complete: 10 games" });
        });

        expect(onRunEnd).not.toHaveBeenCalled();
      } finally {
        unmount();
      }
    });

    it("a second consumer without callbacks reads the same run and announces nothing", () => {
      const onRunEnd = vi.fn();
      startedRun("run-1");
      const owner = renderHook(() => useSyncRunView({ onRunEnd }));
      const reader = renderHook(() => useSyncRunView());
      try {
        act(() => {
          setSyncProgress({ running: false, stage: "done", message: "Sync complete: 10 games", runId: "run-1" });
        });

        // The run ended once, for the consumer that owns the side effects.
        expect(onRunEnd).toHaveBeenCalledTimes(1);
        // The reader saw the same run end.
        expect(reader.result.current.running).toBe(false);
        expect(reader.result.current.stage).toBe("done");
        expect(owner.result.current.running).toBe(false);
      } finally {
        owner.unmount();
        reader.unmount();
      }
    });
  });

  describe("what it does not read", () => {
    it("re-renders for a frame, and not for a unit result recorded against the rows", () => {
      // The rows are a store a page reads for itself. If this hook subscribed to
      // them — as it did for a unit's display name — every page reading the run
      // would re-render per applied item for rows it may not show at all.
      seedRunUnits([planUnit("PSX")], "run-1");
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 1,
        totalSteps: 1,
        current: 1,
        total: 10,
        message: "PSX: 1/10",
        runId: "run-1",
      });

      let renders = 0;
      const { unmount } = renderHook(() => {
        renders++;
        return useSyncRunView();
      });
      try {
        const afterMount = renders;
        act(() => {
          updateSyncProgress({ current: 2, message: "PSX: 2/10" });
        });
        // Non-vacuous: a frame really does re-render the consumer, so the count
        // below is measuring something.
        expect(renders).toBeGreaterThan(afterMount);

        const afterFrame = renders;
        act(() => {
          recordUnitCreated(0);
        });
        expect(renders).toBe(afterFrame);
      } finally {
        unmount();
      }
    });
  });

  describe("what the run is working on", () => {
    it("exposes the running unit's within-unit position, which no row carries", () => {
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 2,
        current: 25,
        total: 100,
        message: "SNES: 25/100",
        runId: "run-1",
      });

      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        // The apply phase sits above the fetch and cover shares, a quarter filled.
        expect(result.current.withinUnitFraction).toBeCloseTo(FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * 0.25, 5);
        expect(result.current.stage).toBe("applying");
        expect(result.current.stageLabel).toBe("Applying shortcuts");
        expect(result.current.step).toBe(2);
        expect(result.current.totalSteps).toBe(2);
        expect(result.current.runId).toBe("run-1");
      } finally {
        unmount();
      }
    });

    it("rests the position at the unit floor before the run reaches a unit", () => {
      setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runId: "" });

      const { result, unmount } = renderHook(() => useSyncRunView());
      try {
        expect(result.current.withinUnitFraction).toBe(0);
        expect(result.current.step).toBe(0);
        expect(result.current.totalSteps).toBe(0);
      } finally {
        unmount();
      }
    });
  });
});
