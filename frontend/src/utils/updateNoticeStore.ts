/**
 * Module-level store for the newer release the backend found, if any.
 *
 * Updated by:
 *   - panel load in index.tsx (fetchUpdateNotice), detached
 *   - the card's Dismiss (dismissUpdateForVersion), after the backend persisted it
 *   - the Settings switch (setUpdateCheckSwitch), after the backend persisted it
 *   - Settings' Check now (runUpdateCheckNow), with the answer it asked for
 *
 * Read by:
 *   - bigpicture/UpdateNotice.tsx, the card on Main, which shows nothing unless `available`
 *   - bigpicture/settings/UpdatesSection.tsx through SettingsPage, the card's home
 *
 * `enabled` starts true because that is the default: an install that never
 * touched the switch carries no key at all.
 *
 * Every write installs a NEW state object and notifies, which is what lets
 * {@link getUpdateNoticeState} serve as a `useSyncExternalStore` snapshot.
 */

import { useSyncExternalStore } from "react";
import { detach } from "./detach";
import {
  checkForUpdateNow,
  dismissUpdateNotice,
  getUpdateNotice,
  setUpdateCheckEnabled,
  type UpdateNotice,
  type UpdateSettingWrite,
} from "../api/backend";

export interface UpdateNoticeState {
  /** The card: a newer release exists, it was not dismissed, and the check is on. */
  available: boolean;
  /** A newer release exists, dismissed or not. */
  newer: boolean;
  /** The newest available release. `null` until a check established one. */
  latestVersion: string | null;
  /** `""` until the backend has answered once. */
  currentVersion: string;
  enabled: boolean;
  /** This process can be updated in place — false for a run from a checkout. */
  installedProgram: boolean;
}

const INITIAL: UpdateNoticeState = {
  available: false,
  newer: false,
  latestVersion: null,
  currentVersion: "",
  enabled: true,
  installedProgram: false,
};

let _state: UpdateNoticeState = INITIAL;
let _listeners: Array<() => void> = [];

/**
 * Ordering fence: every write that crosses an `await` takes the number before
 * the `await` and writes nothing if the number has moved by the time it lands.
 *
 * A read can sit on a GitHub request for up to its timeout, and its answer
 * carries `enabled`, which belongs to the user: without the fence, switching the
 * check on and straight back off lets the first read land after the switch is
 * off and put the card back up while `settings.json` says off. Two presses are
 * genuinely in flight at once too — Steam's `Toggle` keeps its own state and
 * reports the flipped value — so the later one wins, and the loser also skips
 * its trailing read.
 *
 * One case ordering by issue time cannot settle: a toggle press overtaken by a
 * Dismiss leaves `enabled` here disagreeing with `settings.json` until the next
 * read. It is accepted because `enabled` here gates nothing — whether GitHub is
 * asked is decided backend-side off `settings.json` — so only the display can
 * be wrong, and only after two presses on two pages within one call of each
 * other. A counter per field would close it at the price of every future writer
 * having to pick the right one.
 */
let _seq = 0;

export function setUpdateNoticeState(state: UpdateNoticeState): void {
  _state = state;
  _listeners.forEach((fn) => fn());
}

/** Test seam: drop the answer, as a fresh panel load has it. */
export function resetUpdateNoticeStoreForTests(): void {
  _seq = 0;
  setUpdateNoticeState(INITIAL);
}

export function getUpdateNoticeState(): UpdateNoticeState {
  return _state;
}

export function onUpdateNoticeChange(fn: () => void): () => void {
  _listeners.push(fn);
  return () => {
    _listeners = _listeners.filter((l) => l !== fn);
  };
}

/** Subscribe to the update notice from a component. */
export function useUpdateNoticeState(): UpdateNoticeState {
  return useSyncExternalStore(onUpdateNoticeChange, getUpdateNoticeState);
}

/** The wire answer as this store holds it — one mapper, so the two writers cannot drift. */
function stateFromNotice(notice: UpdateNotice): UpdateNoticeState {
  return {
    available: notice.available,
    newer: notice.newer,
    latestVersion: notice.latest_version,
    currentVersion: notice.current_version,
    enabled: notice.enabled,
    installedProgram: notice.installed_program,
  };
}

/**
 * Ask the backend what it knows about a newer release and update the store.
 *
 * Whenever the daily check is due this sits on a GitHub request for up to its
 * timeout, so no surface may await it before rendering — the panel-load caller
 * detaches it. A read that a later read or press overtook writes nothing — see
 * {@link _seq}.
 */
export async function fetchUpdateNotice(): Promise<void> {
  const seq = ++_seq;
  const notice = await getUpdateNotice();
  if (seq !== _seq) return;
  setUpdateNoticeState(stateFromNotice(notice));
}

/**
 * What an asked-for check found. `none` is a reading that found nothing newer,
 * `unreachable` is no reading at all, and `off` a question that was never asked
 * — collapsing any two says something nothing established. `superseded` means a
 * later press overtook this one, which then has nothing to report.
 */
export type UpdateCheckOutcome = "found" | "none" | "unreachable" | "off" | "superseded";

/**
 * Ask now, past the daily throttle and past a Dismiss — the backend forgets the
 * dismissed version, so a card that was waved away comes back.
 */
export async function runUpdateCheckNow(): Promise<UpdateCheckOutcome> {
  const seq = ++_seq;
  const answer = await checkForUpdateNow();
  if (seq !== _seq) return "superseded";
  setUpdateNoticeState(stateFromNotice(answer));
  if (!answer.enabled) return "off";
  if (!answer.reached) return "unreachable";
  return answer.newer ? "found" : "none";
}

/** Throws the backend's refusal, so the caller's log names it. */
function requireAccepted(write: UpdateSettingWrite): void {
  if (!write.success) throw new Error(`${write.reason}: ${write.message}`);
}

/**
 * Wave the card away for one release, then take it down here — only once the
 * backend answered that it persisted the dismissal. A refused or failed write
 * rejects and leaves the card up, rather than hiding it until the next start
 * brings it back.
 */
export async function dismissUpdateForVersion(version: string): Promise<void> {
  const seq = ++_seq;
  const write = await dismissUpdateNotice(version);
  if (seq !== _seq) return;
  requireAccepted(write);
  setUpdateNoticeState({ ..._state, available: false });
}

/**
 * Persist the switch, then reflect it here — only once the backend answered
 * that it persisted it. A refused or failed write rejects and changes nothing.
 *
 * Off drops what the backend drops for a switched-off check — the card and the
 * version. On cannot restore them from anything held here, so a fresh read is
 * started and not awaited: it may sit on a GitHub timeout, and the toggle just
 * pressed would sit there with it.
 */
export async function setUpdateCheckSwitch(enabled: boolean): Promise<void> {
  const seq = ++_seq;
  const write = await setUpdateCheckEnabled(enabled);
  if (seq !== _seq) return;
  requireAccepted(write);
  if (enabled) {
    setUpdateNoticeState({ ..._state, enabled });
    detach(fetchUpdateNotice());
  } else {
    setUpdateNoticeState({ ..._state, enabled, available: false, newer: false, latestVersion: null });
  }
}
