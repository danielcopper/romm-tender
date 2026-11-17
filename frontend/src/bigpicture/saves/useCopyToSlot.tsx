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
import type { SaveSlotSummary } from "../../types";
import { showSyncConflictModal } from "../../shared/SyncConflictModal";
import { detach } from "../../utils/detach";
import { executeCopySaveToSlot } from "../../utils/saveSlotFlow";
import { CopyToSlotModal } from "./CopyToSlotModal";
import type { CopyToSlotHandler } from "./CopyToSlotButton";

/** Returns an opener `openCopyModal(saveId, sourceSlot)` for the copy-to-slot flow. */
export function useCopyToSlot(romId: number, availableSlots: SaveSlotSummary[]): CopyToSlotHandler {
  return useCallback(
    (saveId: number, sourceSlot: string) => {
      showModal(
        <CopyToSlotModal
          availableSlots={availableSlots}
          sourceSlot={sourceSlot}
          onSubmit={(target: string) => {
            detach(executeCopySaveToSlot(romId, saveId, target, showSyncConflictModal));
          }}
        />,
      );
    },
    [romId, availableSlots],
  );
}
