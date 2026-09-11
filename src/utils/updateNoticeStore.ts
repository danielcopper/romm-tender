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
  /**
   * The `releases/latest` address. Always shown in the card, because the button
   * can fail and this cannot — and never installed from: it resolves to
   * whatever is newest, so pairing it with {@link digest} would fetch one
   * release and verify it against another.
   */
  downloadUrl: string;
  /** The version-bound address the button installs from. `""` where unknown. */
  installUrl: string;
  /** plugin.json's name — what Decky matches the existing installation against. */
  pluginName: string;
  /** The bare sha256 hex of {@link installUrl}'s asset, `null` where unknown. */
  digest: string | null;
  enabled: boolean;
}

const INITIAL: UpdateNoticeState = {
  available: false,
  latestVersion: null,
  currentVersion: "",
  downloadUrl: "",
  installUrl: "",
  pluginName: "",
  digest: null,
  enabled: true,
};

let _state: UpdateNoticeState = INITIAL;
let _listeners: Array<() => void> = [];

/**
 * Ordering fence, in the shape `gameDetailStore`'s `loadSeq` established.
 *
 * The rule is one sentence and has no exceptions: **every write that crosses an
 * `await` takes the number before the `await` and writes nothing if the number
 * has moved by the time it lands.** Reads and presses alike — a half-rule that
 * fenced only reads is what the next person adding a writer would carry on.
 *
 * Against a read, the press wins. A read can sit on a GitHub request for as long
 * as that request takes and its payload carries `enabled`, which belongs to the
 * user and not to the answer: with no fence, switching the check on and straight
 * back off leaves the first read in flight with `enabled: true` on it, landing
 * after the switch is off and putting the toggle back on and the card back up
 * while `settings.json` says off.
 *
 * Against another press, the later press wins — and two are genuinely in flight
 * at once, because Steam's `Toggle` keeps its own state unless it is given
 * `controlled` and reports the already-flipped value, so a double press sends a
 * value and then its opposite with neither settled. The loser also skips its own
 * trailing read, and that is right whichever press won: a winning toggle-on
 * issues a read of its own, and a winning Dismiss needs none, because it sets
 * `available` itself.
 *
 * Almost nothing legitimate is discarded, and the incrementers say why: the
 * number moves only when a press or a read is ISSUED, reads are issued in two
 * places, and the one at plugin load flies before any press can exist.
 *
 * The exception is the one thing ordering by ISSUE TIME cannot do — tell a stale
 * intent from a current truth — and a press and a Dismiss can overtake each
 * other in both directions.
 *
 * A DISMISS that a later press overtakes ends right either way, and heals
 * itself: the `await` in `dismissUpdateForVersion` IS the persist, so only the
 * local write is dropped and the backend already holds the dismissal. A later
 * toggle-off forces `available` false regardless; a later toggle-on issues a
 * read that comes back with it.
 *
 * A TOGGLE PRESS that a later Dismiss overtakes is the case that does not heal.
 * No other PRESS writes `enabled` — `fetchUpdateNotice` does, which is why this
 * says press and not writer — so the dropped value was not replaced by a newer
 * intent; it was the one the backend had just accepted, and this store is left
 * disagreeing with `settings.json`.
 *
 * It is accepted rather than closed, and what decides that is the blast radius.
 * **`enabled` gates nothing.** Outside this module it has exactly one reader,
 * `SettingsPage` handing it to `AdvancedSection` as `checked`; no fetch consults
 * it — `fetchUpdateNotice` always asks the backend, and whether GitHub is asked
 * at all is decided backend-side by `UpdateCheckService._enabled()` off
 * `settings.json`. So a stranded `enabled: true` cannot cause one request the
 * user switched off: the promise the switch makes is untouched, and only its
 * DISPLAY goes wrong. `settings.json` stays right, the visible toggle stays
 * right, and the next plugin load reads the store right again. Reaching it at
 * all takes two different controls on two different pages, pressed within one
 * local call of each other.
 *
 * The visible toggle stays right for a narrower reason than "the store cannot
 * move it", and the difference matters to whoever reads this next.
 * `AdvancedSection` passes `checked` and not `controlled` (which
 * `ToggleFieldProps` does not even declare, so passing it needs a type
 * exception), and Steam's base class reads `props.checked` through its getter
 * only under `controlled` — but its `componentDidUpdate` pushes the prop into
 * its own state whenever the prop CHANGES. A discarded write changes nothing, so
 * it does not fire here. It is immune to this case, not to the store.
 *
 * A counter per field would close it, and is deliberately not here: the rule
 * above is one sentence that cannot be applied wrongly, where three counters
 * would make the next writer pick the right one. That is the same drift this
 * fence exists to stop, one level up.
 *
 * An answer that fails to persist has still spent the number. That costs a
 * refresh — the card appears one read later than it might have — and never a
 * wrong value, which is the direction to fail in.
 */
let _seq = 0;

export function setUpdateNoticeState(state: UpdateNoticeState): void {
  _state = state;
  _listeners.forEach((fn) => fn());
}

/** Test seam: drop the answer, as a fresh plugin load has it. */
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

/**
 * Ask the backend what it knows about a newer release and update the store.
 *
 * The call can spend a GitHub request timeout on the one day the check is due,
 * so no surface may await it before rendering: the plugin-load caller detaches
 * it and the card appears when the answer does. A read that a later read or a
 * user answer has overtaken writes nothing at all — see {@link _seq}.
 *
 * Resolves with what the store holds afterwards, which for an overtaken read is
 * whatever overtook it.
 */
export async function fetchUpdateNotice(): Promise<UpdateNoticeState> {
  const seq = ++_seq;
  const notice = await getUpdateNotice();
  if (seq !== _seq) return _state;
  setUpdateNoticeState({
    available: notice.available,
    latestVersion: notice.latest_version,
    currentVersion: notice.current_version,
    downloadUrl: notice.download_url,
    installUrl: notice.install_url,
    pluginName: notice.plugin_name,
    digest: notice.digest,
    enabled: notice.enabled,
  });
  return _state;
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
  const seq = ++_seq;
  await dismissUpdateNotice(version);
  if (seq !== _seq) return;
  setUpdateNoticeState({ ..._state, available: false });
}

/**
 * Persist the switch, then reflect it here.
 *
 * Switching OFF also clears `available`, because the card's condition includes
 * the switch. Switching ON cannot restore it from anything held here — the
 * backend answers a disabled check with no version at all — so a fresh read is
 * started and deliberately not awaited: it is the one that may sit on a GitHub
 * timeout, and the toggle the user just pressed would sit there with it. That
 * read takes a newer number than this press, so it is the one read a press does
 * not overtake.
 *
 * A press a later press overtook writes nothing and starts no read — see
 * {@link _seq} for why two are in flight at once.
 */
export async function setUpdateCheckSwitch(enabled: boolean): Promise<void> {
  const seq = ++_seq;
  await setUpdateCheckEnabled(enabled);
  if (seq !== _seq) return;
  setUpdateNoticeState({ ..._state, enabled, available: enabled && _state.available });
  if (enabled) detach(fetchUpdateNotice());
}
