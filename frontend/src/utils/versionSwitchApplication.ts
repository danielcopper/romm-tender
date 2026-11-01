import {
  fetchCoverBase64,
  invalidateCachedGameDetail,
  logError,
  logWarn,
  type SwitchVersionSuccess,
} from "../api/backend";
import { setChosenCover, setSteamGridDbSwitchCover } from "./artwork";
import { writeSlotArtwork } from "./artworkSlotsStore";
import { setLaunchOptionsConfirmed } from "./steamShortcuts";
import { isPruneLeaseCancelled, withPruneLease, type PruneLeaseAdmission } from "./pruneLease";
import { refreshAppIdMap } from "./sessionManager";
import { detach } from "./detach";

export async function applyCommittedVersionSwitch(
  result: SwitchVersionSuccess,
  onCover: ((romId: number, cover: string) => void) | undefined,
  admission: PruneLeaseAdmission,
): Promise<boolean> {
  // The binding moved when the backend answered; Steam's Play must not wait on
  // the Steam writes below to be judged by it.
  detach(refreshAppIdMap());
  return withPruneLease(
    result.prune_lease_token,
    "Version switch",
    async (signal) => {
      let confirmed = false;
      if (isPruneLeaseCancelled(signal)) return confirmed;
      try {
        confirmed = await setLaunchOptionsConfirmed(result.app_id, result.launch_options);
      } catch (e) {
        logError(
          `Version switch: launch-options confirm threw for rom ${result.rom_id} (appId ${result.app_id}): ${e}`,
        );
      }
      if (!confirmed) {
        logError(`Version switch: could not confirm launch options for rom ${result.rom_id} (appId ${result.app_id})`);
      }
      if (!isPruneLeaseCancelled(signal)) {
        await publishCommittedVersionSwitch(result.app_id, result.rom_id, onCover, signal);
      }
      return confirmed;
    },
    admission.owner,
    admission,
  );
}

export async function publishCommittedVersionSwitch(
  appId: number,
  romId: number,
  onCover?: (romId: number, cover: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  try {
    // A cover the user chose for this version is its cover; RomM's only where there is none (#2381 D16).
    const chosen = await setChosenCover(romId, appId, (base64) => onCover?.(romId, base64));
    if (chosen.kind === "unavailable") {
      logWarn(`Version switch: the cover chosen for rom ${romId} cannot be served now; the cover Steam shows stays`);
    } else if (chosen.kind === "unread") {
      logWarn(
        `Version switch: whether a cover was chosen for rom ${romId} could not be read (${chosen.reason}); ` +
          "the cover Steam shows stays",
      );
    }
    const cover = chosen.kind === "none" ? await fetchCoverBase64(romId) : null;
    if (signal?.aborted) return;
    if (cover?.base64) {
      onCover?.(romId, cover.base64);
      const base64 = cover.base64;
      await writeSlotArtwork(appId, "cover", () => SteamClient.Apps.SetCustomArtworkForApp(appId, base64, "png", 0));
    } else if (cover) {
      // Neither a chosen cover nor RomM's: the version's cover is Tender's SteamGridDB pick (#2381 D28).
      await setSteamGridDbSwitchCover(romId, appId, (base64) => onCover?.(romId, base64));
    }
  } catch (e) {
    logWarn(`Version switch: cover apply after switch failed for rom ${romId}: ${e}`);
  }
  if (signal?.aborted) return;
  invalidateCachedGameDetail(appId);
  globalThis.dispatchEvent(
    new CustomEvent("romm_data_changed", {
      detail: { type: "version_switched", app_id: appId, rom_id: romId },
    }),
  );
}
