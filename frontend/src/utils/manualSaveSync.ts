/** The per-game "Sync Saves" action: one `syncRomSaves` call and what it says. */

import { syncRomSaves } from "../api/backend";
import { showToast } from "./toast";
import { saveSyncToastBody } from "./saveSyncToast";
import { noteSaveSyncDisplay } from "./gameDetailStore";

export async function executeManualSaveSync(appId: number, romId: number): Promise<boolean> {
  try {
    const result = await syncRomSaves(romId);
    if (result.success) {
      // Directional completion toast via the shared helper — the single source
      // of that copy across every save-sync surface (#1481).
      const directionalBody = saveSyncToastBody(result.uploaded, result.downloaded);
      const c = result.conflicts?.length ?? 0;
      if (directionalBody) {
        showToast(directionalBody);
      } else if (c === 0) {
        // Manual surface only (#1486): an explicit per-game "Sync Saves" click
        // that moved nothing and hit no conflicts gets a short acknowledgement,
        // so the click doesn't read as a no-op. The automatic surfaces
        // (pre-launch, post-exit) stay silent on this zero-case.
        showToast("Saves already up to date");
      }
      // Preserve the conflict signal as its own additive toast (mirroring the
      // post-exit conflicts_toast) — it must stay visible even when nothing
      // transferred. Gated above so "up to date" never contradicts pending
      // conflicts.
      if (c > 0) {
        showToast(`${c} conflict(s) need resolution`);
      }
      globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: romId } }));
      // Refresh save sync status — last_sync_check_at was just set by the backend
      noteSaveSyncDisplay(appId, romId, { status: "synced", label: "Just now", last_sync_check_at: null });
      return true;
    }
    showToast(result.message || "Save sync failed");
    return false;
  } catch {
    showToast("Save sync failed");
    return false;
  }
}
