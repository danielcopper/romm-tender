/**
 * The game-detail panel's lazy SAVES slot load.
 *
 * The slot list is fetched on the first activation of the SAVES tab and not
 * again for the same ROM once it succeeds — `slotsLoadedRef` is that gate. A
 * fetch that fails, or is torn down mid-flight, reopens it so a reconnect or a
 * later activation retries. The gate is owned by the panel because a version
 * switch resets it (`handleVersionSwitched` in `panelEvents.ts`) so the slots
 * re-fetch for the newly-bound rom_id.
 */

import { useEffect } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { debugLog } from "../api/backend";
import { useRommConnectionState } from "../utils/connectionState";
import { detach } from "../utils/detach";
import { releaseUnsettledRun, runSlotLoad, type SlotLoadRun } from "../utils/slotLoadRun";
import { takeReadTicket, type PanelReadSeqs, type PanelState } from "./panelState";

/** Load the ROM's save slots once the SAVES tab is first shown. */
export function useSaveSlotsLoad(
  state: PanelState,
  slotsLoadedRef: MutableRefObject<boolean>,
  readSeqs: MutableRefObject<PanelReadSeqs>,
  setState: Dispatch<SetStateAction<PanelState>>,
): void {
  // The shared connection state lets the fetch take the known-offline fast path
  // below and re-load automatically on reconnect (#1345).
  const isOffline = useRommConnectionState() === "offline";
  const { activeTab, saveSyncEnabled, romId } = state;

  useEffect(() => {
    if (activeTab !== "saves" || !saveSyncEnabled || !romId) return;
    if (slotsLoadedRef.current) return;

    // Known-offline fast path (#1345): the server slot fetch runs through the
    // retry+backoff ladder, so on a known-unreachable server it would hang
    // "Loading slots…" for tens of seconds before the local degraded view
    // renders. Skip the fetch entirely — slotsLoading is still false here (the
    // slotsLoadedRef guard above means the connected path never set it), so
    // SavesTab renders that view now: the slots an earlier answer left behind,
    // the active one carrying its offline summary — or, with nothing answered
    // for this ROM yet, the locally tracked save files under no slot at all,
    // since the only slot name on hand is the panel's placeholder (#1747).
    // slotsLoadedRef stays false, so a flip back to connected re-runs this effect
    // (isOffline dep) and loads.
    if (isOffline) return;
    slotsLoadedRef.current = true;

    const run: SlotLoadRun = { cancelled: false, settled: false };
    // Taken as the read is issued: it is what orders this lane against the
    // panel's slot refreshes (`refreshSlotState`'s `slots` ticket).
    const isOvertaken = takeReadTicket(readSeqs, "slots");
    detach(
      runSlotLoad<PanelState>(romId, run, {
        slotsLoadedRef,
        setState,
        isOvertaken,
        log: (msg) => detach(debugLog(msg)),
      }),
    );
    return () => {
      run.cancelled = true;
      releaseUnsettledRun(run, slotsLoadedRef, setState);
    };
    // `slotsLoadedRef`, `readSeqs` and `setState` are stable for the panel's
    // lifetime (two useRef objects and a useState setter) and never re-run this
    // effect; they are listed only because arriving as parameters puts them out
    // of reach of exhaustive-deps' stability inference.
  }, [activeTab, saveSyncEnabled, romId, isOffline, slotsLoadedRef, readSeqs, setState]);
}
