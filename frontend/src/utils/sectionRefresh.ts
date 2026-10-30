/**
 * Fire-and-forget background refresh helpers for the play-section row.
 *
 * Each helper hits a single backend endpoint, merges the response into the
 * caller's state via a typed setter, and swallows errors (logging where it's
 * useful for debugging). Generic over the consumer's state shape so the
 * helpers stay decoupled from any particular component's full state.
 */

import type { Dispatch, SetStateAction } from "react";
import { getAchievementProgress, debugLog } from "../api/backend";
import { getBiosStatusShared, getPlatformCoreInfoShared } from "../api/sharedReads";
import { detach } from "./detach";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { extractBiosInfo, extractCoreInfo, type BiosInfoFields, type CoreInfoFields } from "./playSection";
import { withTimeout } from "./withTimeout";

interface AchievementFields {
  achievementEarned: number;
  achievementTotal: number;
}

/** Re-read this ROM's BIOS status and fold the answer in — including an answer
 *  reporting no requirement, which clears the shown one (#1690). Nothing is
 *  written when the read fails or comes back carrying no answer, so the shown
 *  level stands: neither is "no BIOS need", both are "we don't know" (#1693).
 *
 *  Its one caller is the load lane, reaching it off the cached detail's `bios`
 *  stale mark — the same mark the info panel's load reads microtasks away, so
 *  the two share one request (`api/sharedReads.ts`). A caller that re-reads
 *  BECAUSE the BIOS state just changed does not belong here: it would join the
 *  pre-change answer. */
export function refreshBiosInBackground<S extends BiosInfoFields>(
  romId: number,
  cancelled: () => boolean,
  setter: Dispatch<SetStateAction<S>>,
): void {
  getBiosStatusShared(romId)
    .then((result) => {
      if (cancelled()) return;
      const biosInfo = extractBiosInfo(result);
      if (!biosInfo) return;
      setter((prev) => ({ ...prev, ...biosInfo }));
    })
    .catch((e) => debugLog(`Background BIOS status fetch error: ${e}`));
}

/** Refresh core-selection state from the dedicated `get_platform_core_info`
 *  path (#923), fully decoupled from BIOS status. Keyed on the rom_id so the
 *  active core reflects a per-game DB override (epic #945) when one is pinned.
 *
 *  Shared with the info panel's load, which reads the same ROM's core info on
 *  the same page open — see `api/sharedReads.ts`. The read is the page's check
 *  of whether the game can start (#2195 D32), so one that gets no answer within
 *  {@link LOCAL_CALL_LIMIT_MS} counts as failed; `onFailure` hears a failed
 *  read, unless the caller cancelled it. */
export function refreshCoreInfoInBackground<S extends CoreInfoFields>(
  romId: number,
  cancelled: () => boolean,
  setter: Dispatch<SetStateAction<S>>,
  onFailure?: (e: unknown) => void,
): void {
  withTimeout(getPlatformCoreInfoShared(romId), LOCAL_CALL_LIMIT_MS)
    .then((coreInfo) => {
      if (!cancelled()) {
        setter((prev) => ({
          ...prev,
          ...extractCoreInfo(coreInfo),
        }));
      }
    })
    .catch((e: unknown) => {
      detach(debugLog(`Background core info fetch error: ${e}`));
      if (!cancelled()) onFailure?.(e);
    });
}

export function refreshAchievementsInBackground<S extends AchievementFields>(
  romId: number,
  cancelled: () => boolean,
  setter: Dispatch<SetStateAction<S>>,
): void {
  getAchievementProgress(romId)
    .then((result) => {
      if (!cancelled() && result.success) {
        setter((prev) => ({
          ...prev,
          achievementEarned: result.earned,
          achievementTotal: result.total,
        }));
      }
    })
    .catch((e) => debugLog(`Background achievement progress fetch error: ${e}`));
}
