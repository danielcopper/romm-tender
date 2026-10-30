/**
 * The panel's half of a sync's removals: report the ones Steam no longer lists
 * (docs/architecture/steam-non-steam-shortcuts.md, "A removal the panel did not finish").
 */

import { logError, reportSyncRemovals } from "../api/backend";
import type { SyncRemoval, SyncStaleData } from "../types";
import { isConvergedRemoval } from "./shortcutCarryOver";
import { shortcutsGoneFromSteam } from "./steamShortcuts";
import { refreshSyncStatsAfterChange } from "./syncStatsStore";
import { detach } from "./detach";

/**
 * Report every one of *removals* whose shortcut Steam no longer lists; *removedAppIds* are the ones a removal was
 * tried for, which the read waits for. A report that unbound a row has changed the panel's game count, so the
 * count is read again.
 */
export async function reportSyncRemovalsGone(
  removals: SyncStaleData["remove"],
  removedAppIds: readonly number[],
): Promise<void> {
  const gone = new Set(
    await shortcutsGoneFromSteam(
      removals.map((removal) => removal.app_id),
      removedAppIds,
    ),
  );
  const stale: SyncRemoval[] = [];
  const converged: SyncRemoval[] = [];
  for (const removal of removals) {
    if (!gone.has(removal.app_id)) continue;
    (isConvergedRemoval(removal) ? converged : stale).push({ rom_id: removal.rom_id, app_id: removal.app_id });
  }
  if (stale.length === 0 && converged.length === 0) return;
  const answer = await reportSyncRemovals(stale, converged);
  if (!answer.success) {
    logError(`sync_stale: the backend did not take the removals' report: ${answer.message}`);
    return;
  }
  if ((answer.unbound_count ?? 0) > 0) detach(refreshSyncStatsAfterChange());
}
