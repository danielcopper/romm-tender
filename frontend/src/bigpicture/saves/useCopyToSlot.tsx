/**
 * Hook driving the per-save "Copy to slot…" flow. Returns an opener that shows
 * the CopyToSlotModal for one source save, runs `copySaveToSlot`, and routes
 * every discriminated status to the right feedback: `ok` toasts and dispatches
 * `romm_data_changed` (so the parent re-fetches source AND target views);
 * `conflict_blocked` opens the standard sync-conflict modal (as
 * VersionHistoryPanel does); `target_slot_busy` and each refusal toast.
 */

import { useCallback } from "react";
import { showModal } from "@decky/ui";
import { showToast } from "../../utils/toast";
import { copySaveToSlot, debugLog } from "../../api/backend";
import type { CopySaveToSlotStatus, SaveSlotSummary } from "../../types";
import { showSyncConflictModal } from "../../shared/SyncConflictModal";
import { detach } from "../../utils/detach";
import { copyToSlotFeedback } from "../../utils/saveSlotWording";
import { CopyToSlotModal } from "./CopyToSlotModal";
import type { CopyToSlotHandler } from "./CopyToSlotButton";

/** Refresh both the source and target slot views after a successful copy. */
function dispatchDataChanged(romId: number): void {
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: romId } }));
}

async function handleResult(result: CopySaveToSlotStatus, target: string, romId: number): Promise<void> {
  const feedback = copyToSlotFeedback(result, target);
  if (feedback.kind === "conflict") {
    await showSyncConflictModal(feedback.conflict);
    return;
  }
  showToast(feedback.message);
  if (result.status === "ok") {
    dispatchDataChanged(romId);
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
