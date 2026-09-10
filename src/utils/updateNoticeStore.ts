/**
 * Module-level store for the newer release the backend found, if any.
 *
 * Mirrors legacyInstallStore: a backend-state-driven notice surfaced as a QAM
 * card. What it carries beyond the condition is everything an install needs —
 * the address, the name Decky matches the existing installation against, and
 * the asset digest — because the card's button hands all three to Decky and has
 * nowhere else to read them from.
 *
 * Updated by:
 *   - plugin load init in index.tsx (fetchUpdateNotice)
 *   - the card's Dismiss (dismissUpdateForVersion), after the backend persisted it
 *   - the Settings toggle (setUpdateCheckSwitch), after the backend persisted it
 *
 * Read by:
 *   - components/UpdateNotice.tsx through {@link useUpdateNoticeState} — the
 *     card Main renders, which shows nothing unless `available`
 *   - components/SettingsPage.tsx, for the switch's own position
 *
 * `enabled` starts true, which is the documented default rather than a guess:
 * an install that has never touched the switch carries no key at all. It is
 * also the one value that cannot be read back quickly — with the switch OFF the
 * backend answers without touching the network, and only the ON answer can
 * spend up to a request timeout on GitHub — so the optimistic value is the one
 * whose slow confirmation agrees with it.
 *
 * Every write installs a NEW state object and notifies. That is what lets
 * {@link getUpdateNoticeState} serve as a `useSyncExternalStore` snapshot —
 * React compares snapshots by identity, so a getter handing back a fresh object
 * per call would re-render forever.
 */

import { useSyncExternalStore } from "react";
import { detach } from "./detach";
import { dismissUpdateNotice, getUpdateNotice, setUpdateCheckEnabled } from "../api/backend";

export interface UpdateNoticeState {
  /** A newer release exists, it was not dismissed, and the check is on. */
  available: boolean;
  /** The newer release's bare version. `null` until a check has succeeded. */
  latestVersion: string | null;
  currentVersion: string;
  /** Always shown in the card, because the button can fail and this cannot. */
  downloadUrl: string;
  /** plugin.json's name — what Decky matches the existing installation against. */
  pluginName: string;
  /** The asset's bare sha256 hex, or `null` where the release carried none. */
  digest: string | null;
  enabled: boolean;
}

const INITIAL: UpdateNoticeState = {
  available: false,
  latestVersion: null,
  currentVersion: "",
  downloadUrl: "",
  pluginName: "",
  digest: null,
  enabled: true,
};

let _state: UpdateNoticeState = INITIAL;
let _listeners: Array<() => void> = [];

export function setUpdateNoticeState(state: UpdateNoticeState): void {
  _state = state;
  _listeners.forEach((fn) => fn());
}

/** Test seam: drop the answer, as a fresh plugin load has it. */
export function resetUpdateNoticeStoreForTests(): void {
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

/**
 * Ask the backend what it knows about a newer release and update the store.
 *
 * The call can spend a GitHub request timeout on the one day the check is due,
 * so no surface may await it before rendering: the plugin-load caller detaches
 * it and the card appears when the answer does.
 */
export async function fetchUpdateNotice(): Promise<UpdateNoticeState> {
  const notice = await getUpdateNotice();
  const next: UpdateNoticeState = {
    available: notice.available,
    latestVersion: notice.latest_version,
    currentVersion: notice.current_version,
    downloadUrl: notice.download_url,
    pluginName: notice.plugin_name,
    digest: notice.digest,
    enabled: notice.enabled,
  };
  setUpdateNoticeState(next);
  return next;
}

/**
 * Wave the card away for one release, then take it down here.
 *
 * The store is written only after the backend has accepted the write, so a
 * dismissal that did not persist leaves the card up rather than hiding it until
 * the next start and bringing it back. The version dismissed is the one the
 * card is showing — the next release raises it again on its own.
 */
export async function dismissUpdateForVersion(version: string): Promise<void> {
  await dismissUpdateNotice(version);
  setUpdateNoticeState({ ..._state, available: false });
}

/**
 * Persist the switch, then reflect it here.
 *
 * Switching OFF also clears `available`, because the card's condition includes
 * the switch. Switching ON cannot restore it from anything held here — the
 * backend answers a disabled check with no version at all — so a fresh read is
 * started and deliberately not awaited: it is the one that may sit on a GitHub
 * timeout, and the toggle the user just pressed would sit there with it.
 */
export async function setUpdateCheckSwitch(enabled: boolean): Promise<void> {
  await setUpdateCheckEnabled(enabled);
  setUpdateNoticeState({ ..._state, enabled, available: enabled && _state.available });
  if (enabled) detach(fetchUpdateNotice());
}
