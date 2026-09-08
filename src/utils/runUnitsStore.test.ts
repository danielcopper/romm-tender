import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  attachRunUnitsMirror,
  clearRunUnits,
  getRunUnitsSnapshot,
  onRunUnitsChange,
  recordUnitCreated,
  recordUnitUpdated,
  resetRunUnitsStoreForTests,
  seedRunUnits,
  useRunUnits,
} from "./runUnitsStore";
import { resetSyncProgressStoreForTests, setSyncProgress } from "./syncProgress";
import type { SyncPlanUnit } from "../types";

const RUN_ID = "run-1";

function planUnit(name: string, over: Partial<SyncPlanUnit> = {}): SyncPlanUnit {
  return {
    type: "platform",
    id: name,
    name,
    slug: name.toLowerCase(),
    rom_count: 10,
    ...over,
  };
}

/** The plan the frames below belong to: three platforms, so a frame's
 *  ``totalSteps`` of 3 matches the seeded row count. */
function seedThree(): void {
  seedRunUnits([planUnit("PSX"), planUnit("SNES"), planUnit("Game Boy")], RUN_ID);
}

/** A running frame for one unit, in the shape the backend and the apply loop
 *  emit: the run's id, a 1-based ``step``, the plan's unit total, and a message
 *  naming the unit. */
function frame(step: number, name: string, over: Record<string, unknown> = {}): void {
  setSyncProgress({
    running: true,
    stage: "applying",
    step,
    totalSteps: 3,
    current: 0,
    total: 0,
    message: `${name}: 1/10`,
    runId: RUN_ID,
    ...over,
  });
}

describe("runUnitsStore", () => {
  let detachMirror: () => void;

  beforeEach(() => {
    resetRunUnitsStoreForTests();
    // The whole store, not just an idle frame: these cases reuse one run id, and
    // a run this store has seen END can never be put back in flight.
    resetSyncProgressStoreForTests();
    detachMirror = attachRunUnitsMirror();
  });

  afterEach(() => {
    detachMirror();
  });

  describe("seeding from the plan", () => {
    it("holds one waiting row per plan unit, in plan order, carrying the plan's riders", () => {
      seedRunUnits(
        [
          planUnit("PSX", { rom_count: 2091, predicted_skip: true, bound_count: 12, new_shortcut_count: 3 }),
          planUnit("Sci-Fi", { type: "collection", id: 7, rom_count: 40 }),
        ],
        RUN_ID,
      );

      expect(getRunUnitsSnapshot()).toEqual([
        {
          id: "PSX",
          type: "platform",
          name: "PSX",
          romCount: 2091,
          predictedSkip: true,
          boundCount: 12,
          newShortcutCount: 3,
          state: "waiting",
          created: 0,
          updated: 0,
        },
        {
          id: 7,
          type: "collection",
          name: "Sci-Fi",
          romCount: 40,
          predictedSkip: false,
          boundCount: null,
          newShortcutCount: null,
          state: "waiting",
          created: 0,
          updated: 0,
        },
      ]);
    });

    it("a fresh plan replaces the previous run's rows, results included", () => {
      seedThree();
      frame(1, "PSX");
      recordUnitCreated(0);

      seedRunUnits([planUnit("Mega Drive")], "run-2");

      expect(getRunUnitsSnapshot().map((u) => [u.name, u.state, u.created])).toEqual([["Mega Drive", "waiting", 0]]);
    });
  });

  describe("the running unit advances with the frames", () => {
    it("marks the frame's unit running and every unit before it done", () => {
      seedThree();

      frame(1, "PSX", { current: 4, total: 10 });
      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["running", "waiting", "waiting"]);

      frame(3, "Game Boy", { current: 1, total: 6 });
      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["done", "done", "running"]);
    });

    it("a finalizing frame at the plan's last step leaves every unit done", () => {
      seedThree();
      frame(3, "Game Boy", { current: 6, total: 6 });

      setSyncProgress({
        running: true,
        stage: "finalizing",
        step: 3,
        totalSteps: 3,
        message: "Finalizing…",
        runId: RUN_ID,
      });

      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["done", "done", "done"]);
    });

    it("ignores a frame whose unit count does not match the plan it holds", () => {
      seedThree();
      // This run's own frame, so the run-id guard passes and the count check is
      // what refuses it.
      frame(2, "SNES", { totalSteps: 8 });

      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["waiting", "waiting", "waiting"]);
    });

    it("ignores a frame from another run", () => {
      seedThree();
      frame(2, "SNES", { runId: "run-other" });

      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["waiting", "waiting", "waiting"]);
    });

    it("a preview started after the run does not walk the finished run's rows", () => {
      // A completed run: every unit done.
      seedThree();
      frame(3, "Game Boy", { current: 6, total: 6 });
      setSyncProgress({
        running: true,
        stage: "finalizing",
        step: 3,
        totalSteps: 3,
        message: "Finalizing…",
        runId: RUN_ID,
      });
      setSyncProgress({
        running: false,
        stage: "done",
        message: "Sync complete: 26 games from 3 platforms",
        runId: RUN_ID,
      });
      const afterTheRun = getRunUnitsSnapshot();

      // Pressing Sync again runs a preview: no plan, but a running FETCHING frame
      // per unit over the same work queue, under the preview's own run id.
      setSyncProgress({
        running: true,
        stage: "fetching",
        current: 0,
        step: 1,
        totalSteps: 3,
        message: "Fetching PSX... (1/3)",
        runId: "run-preview",
      });

      expect(getRunUnitsSnapshot()).toBe(afterTheRun);
      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["done", "done", "done"]);
    });

    it("ignores a frame that names no unit yet", () => {
      seedThree();
      setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runId: RUN_ID });

      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["waiting", "waiting", "waiting"]);
    });
  });

  describe("results recorded per unit", () => {
    it("counts creates and updates against the unit that produced them", () => {
      seedThree();
      recordUnitCreated(0);
      recordUnitCreated(0);
      recordUnitUpdated(0);
      recordUnitUpdated(2);

      expect(getRunUnitsSnapshot().map((u) => [u.created, u.updated])).toEqual([
        [2, 1],
        [0, 0],
        [0, 1],
      ]);
    });

    it("drops a result for a unit the plan it holds does not have", () => {
      seedThree();
      recordUnitCreated(9);

      expect(getRunUnitsSnapshot().map((u) => u.created)).toEqual([0, 0, 0]);
    });
  });

  describe("the rows outlive the run", () => {
    it("keeps what a cancelled run got through — the unit it stopped on still reads running", () => {
      seedThree();
      frame(2, "SNES", { current: 40, total: 200 });

      setSyncProgress({
        running: false,
        stage: "cancelled",
        step: 2,
        totalSteps: 3,
        message: "Sync cancelled: 40 of 200 games processed",
        runId: RUN_ID,
      });

      expect(getRunUnitsSnapshot().map((u) => u.state)).toEqual(["done", "running", "waiting"]);
    });
  });

  describe("clearing the rows at a run boundary", () => {
    it("empties them, results and all, and tells its subscribers", () => {
      seedThree();
      frame(2, "SNES");
      recordUnitCreated(0);
      let notified = 0;
      const unsubscribe = onRunUnitsChange(() => {
        notified++;
      });
      try {
        clearRunUnits();

        expect(getRunUnitsSnapshot()).toEqual([]);
        // The page watching the rows has to hear them empty: a clear that only
        // dropped them would leave the last render standing.
        expect(notified).toBe(1);
      } finally {
        unsubscribe();
      }
    });

    it("leaves nothing a frame can walk — only a plan fills the rows again", () => {
      seedThree();
      frame(2, "SNES");

      clearRunUnits();
      frame(3, "Game Boy");
      expect(getRunUnitsSnapshot()).toEqual([]);

      seedRunUnits([planUnit("Mega Drive")], "run-2");
      expect(getRunUnitsSnapshot().map((u) => [u.name, u.state])).toEqual([["Mega Drive", "waiting"]]);
    });

    it("keeps its subscribers, because a run boundary is not a teardown", () => {
      let notified = 0;
      const unsubscribe = onRunUnitsChange(() => {
        notified++;
      });
      try {
        clearRunUnits();
        seedThree();
        expect(notified).toBe(2);
      } finally {
        unsubscribe();
      }
    });
  });

  describe("subscribers", () => {
    it("notifies on a seed, on a frame that moves a unit, and on a recorded result", () => {
      let notified = 0;
      const unsubscribe = onRunUnitsChange(() => {
        notified++;
      });
      try {
        seedThree();
        frame(1, "PSX", { current: 1, total: 10 });
        recordUnitCreated(0);
        expect(notified).toBe(3);
      } finally {
        unsubscribe();
      }
    });

    it("does not notify while the run works within one unit", () => {
      seedThree();
      frame(1, "PSX", { current: 1, total: 10 });
      let notified = 0;
      const unsubscribe = onRunUnitsChange(() => {
        notified++;
      });
      try {
        frame(1, "PSX", { current: 2, total: 10 });
        frame(1, "PSX", { current: 3, total: 10 });
        expect(notified).toBe(0);
      } finally {
        unsubscribe();
      }
    });

    it("a component mounting mid-run reads the rows the run has already filled in", () => {
      seedThree();
      frame(2, "SNES", { current: 5, total: 200 });
      recordUnitCreated(0);

      const { result, unmount } = renderHook(() => useRunUnits());
      try {
        expect(result.current.map((u) => [u.name, u.state, u.created])).toEqual([
          ["PSX", "done", 1],
          ["SNES", "running", 0],
          ["Game Boy", "waiting", 0],
        ]);

        act(() => {
          frame(3, "Game Boy", { current: 1, total: 6 });
        });
        expect(result.current.map((u) => u.state)).toEqual(["done", "done", "running"]);
      } finally {
        unmount();
      }
    });
  });
});
