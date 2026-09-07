/**
 * Module-level owner of one sync run's per-unit state — one row per unit of the
 * run's work queue, in plan order, holding how far the run has got with that
 * unit and what its apply produced.
 *
 * **Why a module store.** A QAM page mounts and unmounts as the user navigates,
 * while the run keeps going. A page opened mid-run therefore sees the plan only
 * if something outside the page kept it, and sees a finished unit's created and
 * updated counts only if something outside the page recorded them — the apply
 * events they were derived from are long gone. This store is that something. The
 * one case it cannot cover is a plugin reload mid-run: the plan arrives once per
 * run, so a store that starts empty after a reload stays empty for the rest of
 * the run and refuses every frame that follows.
 *
 * **Three writers.** {@link seedRunUnits} takes the plan (`sync_plan`, once per
 * run, before any unit); {@link recordUnitCreated} / {@link recordUnitUpdated}
 * take the apply loop's per-item outcome; and {@link attachRunUnitsMirror}
 * subscribes to the sync-progress store. It mirrors that store rather than the
 * backend's `sync_progress` event because three writers feed it — the backend
 * listener in `index.tsx`, the apply loop's per-item updates, and Main's own
 * writes (its re-seed from `get_sync_status` on mount, its optimistic start and
 * that start's retraction) — and the store is the one place all three meet.
 *
 * **The rows belong to ONE run, by its id.** A row is seeded with the run id the
 * plan carried, and a frame moves nothing unless it names that same run. Nothing
 * weaker will do: `sync_preview` emits no plan but does emit a FETCHING frame per
 * unit, over the same work queue, carrying a running flag, a 1-based `step`, the
 * unit count and the unit's name (`services/library/sync_orchestrator.py`), so a
 * preview started after a completed run would otherwise walk that run's rows
 * forward a second time. Its own run id — minted per run and claimed through
 * `try_begin_run`, like every run's — is what tells the two apart. `totalSteps` is still checked against the
 * row count as a cheap sanity check, and a frame carrying none skips that check;
 * a frame naming no unit (`step` 0, which is what the backend sends before the
 * queue) moves nothing either way.
 *
 * **Only a running frame moves a row**, which is what leaves a stopped run
 * readable: the `sync_complete` frame `index.tsx` merges into the store keeps
 * the step the run stopped on, so without that guard it would mark the
 * interrupted unit `done`. (The reporter's own CANCELLED frame that follows
 * carries step 0 and would move nothing either way.) The rows instead
 * stand as the last running frame left them. A clean run passes through the
 * FINALIZING frame at the plan's last step first, so its rows all read `done`
 * before the terminal frame lands.
 *
 * **There is no `skipped` state**, because no frame carries one. A wholesale
 * incremental skip emits nothing at all between the unit's own fetch anchor and
 * the next unit's (`services/library/sync_orchestrator.py` logs the skip and
 * returns), so a skipped unit reads `done` the moment the run moves past it. The
 * plan's own `predictedSkip` rides the row and is what a reader can say
 * something about — it is a plan-time prediction, never the run's verdict
 * (ADR-0023).
 *
 * **A row carries no live position.** Exactly one unit runs at a time, so the
 * running unit's stage and within-unit fraction are the frame the whole panel is
 * already reading: a reader takes them from `useSyncRunView` and pairs them with
 * the row it finds in `running`. Mirroring them here would replace the snapshot
 * on every frame, so every reader of the rows would re-render for all of it,
 * including the ones that show none of it.
 */

import { useSyncExternalStore } from "react";
import { getSyncProgress, onSyncProgressChange } from "./syncProgress";
import type { SyncPlanUnit, SyncProgress } from "../types";

/**
 * How far the run has got with one unit: `waiting` until a frame names it,
 * `running` while a frame names it, `done` once the run has moved past it.
 *
 * `running` after the run has stopped is not a stale value but the answer: it is
 * the unit a cancelled, interrupted or errored run was working when it
 * stopped. Whether
 * the run is still going is the frame's to say, never a row's.
 */
export type RunUnitState = "waiting" | "running" | "done";

/** One unit of the run's work queue: what the plan said about it, how far the
 *  run has got with it, and what its apply produced. */
export interface RunUnit {
  readonly id: number | string;
  readonly type: "platform" | "collection";
  readonly name: string;
  readonly romCount: number;
  /** The plan's prediction that this unit will skip wholesale — an estimate, not
   *  the fetch-time skip decision. `false` where the plan carried none. */
  readonly predictedSkip: boolean;
  /** This unit's ROMs that already carry a Steam shortcut, or `null` where the
   *  plan carried no count (collections with no stamped member set, older
   *  backends). `0` is knowledge; `null` is its absence. */
  readonly boundCount: number | null;
  /** Shortcuts this unit's apply was expected to create, or `null` where the
   *  plan carried no count (collections, older backends). */
  readonly newShortcutCount: number | null;
  readonly state: RunUnitState;
  /** Shortcuts this unit's apply brought under management — a fresh create or an
   *  adopted orphan — counted as the apply loop resolves each item. */
  readonly created: number;
  /** Shortcuts this unit's apply rewrote in place. */
  readonly updated: number;
}

/** The rows as they stand. Handed out as the `useSyncExternalStore` snapshot,
 *  which React compares by identity, so it is only ever REPLACED — and replaced
 *  only when a row actually changes, so a reader re-renders at a unit transition
 *  and per recorded item rather than per frame. */
let _units: readonly RunUnit[] = [];
/** The run the rows belong to, or `null` before any plan is seeded. */
let _runId: string | null = null;
const _listeners = new Set<() => void>();

function notify(): void {
  _listeners.forEach((fn) => fn());
}

/** The rows for the run in flight, or the last run's rows once it has ended. */
export function getRunUnitsSnapshot(): readonly RunUnit[] {
  return _units;
}

export function onRunUnitsChange(fn: () => void): () => void {
  _listeners.add(fn);
  return () => {
    _listeners.delete(fn);
  };
}

/** Subscribe a component to the run's units. Renders what the run has filled in
 *  so far immediately — the store outlives the page, so a page opened mid-run
 *  shows the units behind the running one rather than starting from nothing. */
export function useRunUnits(): readonly RunUnit[] {
  return useSyncExternalStore(onRunUnitsChange, getRunUnitsSnapshot);
}

/** Take one run's work queue from its plan, discarding the previous run's rows.
 *  `sync_plan` fires once per run, before any unit, which is what makes this the
 *  per-run reset; *runId* is the plan's own, and every frame is measured against
 *  it from here. */
export function seedRunUnits(units: readonly SyncPlanUnit[], runId: string): void {
  _runId = runId;
  _units = units.map((unit) => ({
    id: unit.id,
    type: unit.type,
    name: unit.name,
    romCount: unit.rom_count,
    predictedSkip: unit.predicted_skip ?? false,
    boundCount: unit.bound_count ?? null,
    newShortcutCount: unit.new_shortcut_count ?? null,
    state: "waiting" as const,
    created: 0,
    updated: 0,
  }));
  notify();
}

/** Count one shortcut this unit's apply brought under management. Plain counts,
 *  not a deduplicated set like the run-wide delta: the backend emits each rom_id
 *  in exactly one unit's shortcuts per run, so a unit's items are counted once. */
export function recordUnitCreated(unitIndex: number): void {
  recordUnitOutcome(unitIndex, 1, 0);
}

/** Count one shortcut this unit's apply rewrote in place. */
export function recordUnitUpdated(unitIndex: number): void {
  recordUnitOutcome(unitIndex, 0, 1);
}

function recordUnitOutcome(unitIndex: number, created: number, updated: number): void {
  const unit = _units[unitIndex];
  if (unit === undefined) return;
  _units = _units.map((row, i) =>
    i === unitIndex ? { ...row, created: row.created + created, updated: row.updated + updated } : row,
  );
  notify();
}

/** Start mirroring the frame stream into the rows, and hand back the teardown.
 *  Called once where the plugin's other long-lived listeners are installed, so
 *  the store's lifetime is the plugin's and its writers stay explicit. */
export function attachRunUnitsMirror(): () => void {
  return onSyncProgressChange(() => observeRunFrame(getSyncProgress()));
}

/** Reset the module state between tests. Not for production use. */
export function resetRunUnitsStoreForTests(): void {
  _units = [];
  _runId = null;
  _listeners.clear();
}

/** Whether a frame is being emitted from inside a unit rather than around the
 *  queue. It says nothing about whether the run is still going — `cancelled` and
 *  `error` stop it mid-queue, and a frame may carry no stage at all; that
 *  question is the frame's `running` flag, which {@link observeRunFrame} checks
 *  first. */
function isActiveUnitStage(stage: SyncProgress["stage"]): boolean {
  return stage === "fetching" || stage === "applying";
}

function withState(unit: RunUnit, state: RunUnitState): RunUnit {
  return unit.state === state ? unit : { ...unit, state };
}

/**
 * Move the rows to where *progress* says the run is. A frame belonging to
 * another run — or to no run this store was given a plan for — moves nothing at
 * all: it is never evidence that a unit is finished, only that this store is not
 * the one it is about.
 */
function observeRunFrame(progress: SyncProgress): void {
  if (!progress.running || _runId === null || _units.length === 0) return;
  if ((progress.runId ?? "") !== _runId) return;
  const step = progress.step ?? 0;
  if (step <= 0 || step > _units.length) return;
  if (progress.totalSteps !== undefined && progress.totalSteps !== _units.length) return;
  const active = isActiveUnitStage(progress.stage);
  // The running unit is `step - 1` while one is being worked; a frame past the
  // queue (FINALIZING at the plan's last step) counts the whole of `step` as
  // complete, the same reading the coarse bar takes.
  const runningIndex = active ? step - 1 : -1;
  const completed = active ? step - 1 : step;
  const next = _units.map((unit, i) => {
    if (i < completed) return withState(unit, "done");
    if (i === runningIndex) return withState(unit, "running");
    return unit;
  });
  if (next.every((unit, i) => unit === _units[i])) return;
  _units = next;
  notify();
}
