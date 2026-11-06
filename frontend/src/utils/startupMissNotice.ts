/**
 * The notice on Main for everything the start-up checks missed that the panel
 * survives — one home for both checks (#2455 D3, D4) — and the toast for the
 * one miss that notice cannot be reached past.
 *
 * Written by the panel factory in `index.tsx`: once from the searches into
 * Steam's module registry, before anything mounts, and once from the Steam
 * internals, after Steam reports its services initialised — which can be after
 * Main has mounted, so Main subscribes (`useStartupMissNotice`). Each write
 * installs a new state object and notifies, for the reason
 * `settingsResetStore.ts` gives.
 *
 * It holds the losses as the notice words them; the missing names themselves
 * go to the backend log only (`boot/startupMiss.ts`).
 */

import { useSyncExternalStore } from "react";

/** The two start-up checks whose misses reach the notice. */
export type StartupCheck = "modules" | "internals";

interface StartupMissState {
  /** The checks that missed something, each with the losses it named. */
  readonly misses: Readonly<Partial<Record<StartupCheck, readonly string[]>>>;
}

const NOTHING_MISSED: StartupMissState = { misses: {} };

let state: StartupMissState = NOTHING_MISSED;
let listeners: (() => void)[] = [];

/** Record that *check* missed something the panel survives, with the losses it costs (none for a diagnostic miss). */
export function recordStartupMiss(check: StartupCheck, features: readonly string[]): void {
  state = { misses: { ...state.misses, [check]: features } };
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.push(listener);
  return () => {
    listeners = listeners.filter((candidate) => candidate !== listener);
  };
}

const getState = (): StartupMissState => state;

/** Put the module back to what a fresh process holds. */
export function resetStartupMissNoticeForTests(): void {
  state = NOTHING_MISSED;
}

/** The notice's copy, or `null` while nothing missed. */
export interface StartupMissNotice {
  readonly title: string;
  readonly message: string;
}

const TITLE = "Steam has changed";

/**
 * The notice for *state*: the losses of both checks, each once, or — where
 * every miss was one nothing the user sees depends on — the notice without a
 * list. It names no action because there is none inside Tender.
 */
function noticeFor(current: StartupMissState): StartupMissNotice | null {
  const recorded = Object.values(current.misses);
  if (recorded.length === 0) return null;
  const features = [...new Set(recorded.flat())];
  if (features.length === 0) {
    return {
      title: TITLE,
      message: "This Steam version is missing parts Tender uses. Everything still works; please report this.",
    };
  }
  return {
    title: TITLE,
    message: `This Steam version is missing parts Tender uses. Not working: ${features.join(", ")}. Please report this.`,
  };
}

/**
 * The toast raised at start-up where Tender's Quick Access entry could not be
 * added: without the entry neither the panel nor this notice can be reached
 * (#2509 D10).
 */
export const QUICK_ACCESS_MISSING_TOAST =
  "Tender couldn't add its Quick Access entry with this Steam version. Please report this.";

/** The notice as a component reads it, re-rendered whenever a check records a miss. */
export function useStartupMissNotice(): StartupMissNotice | null {
  return noticeFor(useSyncExternalStore(subscribe, getState));
}
