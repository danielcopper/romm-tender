/**
 * Hook driving the per-save "Copy to slot…" flow. Returns an opener that shows
 * the CopyToSlotModal for one source save, runs `copySaveToSlot`, and routes
 * every discriminated status to the right feedback: `ok` and `upload_failed`
 * toast and dispatch `romm_data_changed` (so the parent re-fetches source AND
 * target views);
 * `conflict_blocked` opens the standard sync-conflict modal (as
 * VersionHistoryPanel does); `target_slot_busy` and each refusal toast.
 */

import { useCallback } from "react";
import { showModal } from "@decky/ui";
import { showToast } from "../../utils/toast";
import { copySaveToSlot, debugLog, isEndpointFailure } from "../../api/backend";
import type { CopySaveToSlotStatus, EndpointFailure, SaveSlotSummary } from "../../types";
import { showSyncConflictModal } from "../../shared/SyncConflictModal";
import { detach } from "../../utils/detach";
import { copyToSlotFeedback } from "../../utils/saveSlotWording";
import { CopyToSlotModal } from "./CopyToSlotModal";
import type { CopyToSlotHandler } from "./CopyToSlotButton";
import { announceSaveSync } from "../../utils/saveSyncEvents";

async function handleResult(
  result: CopySaveToSlotStatus | EndpointFailure,
  target: string,
  romId: number,
): Promise<void> {
  const feedback = copyToSlotFeedback(result, target);
  if (feedback === null) return;
  if (feedback.kind === "conflict") {
    await showSyncConflictModal(feedback.conflict);
    return;
  }
  showToast(feedback.message);
  // Only a copy refreshes the slot views: `already_present` copied nothing.
  if (!isEndpointFailure(result) && (result.status === "ok" || result.status === "upload_failed")) {
    announceSaveSync(romId);
  }
}

/** Returns an opener `openCopyModal(saveId, sourceSlot)` for the copy-to-slot flow. */
export function useCopyToSlot(romId: number, availableSlots: SaveSlotSummary[]): CopyToSlotHandler {
  return useCallback(
    (saveId: number, sourceSlot: string) => {
      const runCopy = async (target: string): Promise<void> => {
        try {
          const result = await copySaveToSlot(romId, saveId, target);
          await handleResult(result, target, romId);
        } catch (e) {
          detach(debugLog(`useCopyToSlot: copy failed for save ${saveId} into '${target}': ${e}`));
          showToast("Couldn't copy the save. Check your connection and try again.");
        }
      };
      showModal(
        <CopyToSlotModal
          availableSlots={availableSlots}
          sourceSlot={sourceSlot}
          onSubmit={(target: string) => {
            detach(runCopy(target));
          }}
        />,
      );
    },
    [romId, availableSlots],
  );
}
