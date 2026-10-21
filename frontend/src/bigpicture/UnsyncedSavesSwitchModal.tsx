import { ConfirmModal, showModal } from "@decky/ui";
import type { UnsyncedSavesChoice, UnsyncedSavesPromptArgs } from "../utils/versionSwitch";
import {
  CANCEL_LABEL,
  SWITCH_ANYWAY_LABEL,
  SYNC_AND_SWITCH_LABEL,
  UNSYNCED_SAVES_TITLE,
  unsyncedSavesDescription,
  type UnsyncedSavesDescriptionArgs,
} from "../utils/launchPromptWording";

/** Why the saves can't be synced before the switch (a version gone from RomM first), or `null` when they can. */
function noSyncReason(serverReachable: boolean, versionGone: boolean): UnsyncedSavesDescriptionArgs["noSync"] {
  if (versionGone) return "version_gone";
  return serverReachable ? null : "unreachable";
}

/**
 * Unsynced-saves confirm for switching away from a downloaded version (#1298).
 * Shown by the version picker when the backend soft-blocks the switch
 * (`reason: "unsynced_saves"`): the bound install has local saves that haven't
 * been synced to RomM, or Tender couldn't check whether it has.
 *
 * Two variants (one component, conditional middle/OK buttons), mirroring
 * `showOfflineDriftModal`'s `showModal(...)`-returns-a-Promise pattern:
 *
 * - Sync offered (T4): OK = "Sync now & switch" (`sync_and_switch`), middle =
 *   "Switch anyway" (`switch_anyway`), Cancel (`cancel`).
 * - No sync (T5): OK = "Switch anyway" (`switch_anyway`), Cancel (`cancel`) —
 *   while RomM is unreachable, or when the version being left is gone from
 *   RomM, the saves can't be uploaded, and the description says which.
 *
 * Outside-click / X routes through `ConfirmModal`'s `onCancel` → `cancel`.
 */
export function showUnsyncedSavesModal({
  versionName,
  stranding,
  serverReachable,
  versionGone,
}: UnsyncedSavesPromptArgs): Promise<UnsyncedSavesChoice> {
  const noSync = noSyncReason(serverReachable, versionGone);
  const offerSync = noSync === null;
  return new Promise<UnsyncedSavesChoice>((resolve) => {
    // Sync offered: the middle button is the "strand it" escape hatch below
    // the primary "Sync now & switch". No sync: no middle button — OK IS
    // "Switch anyway" (spread nothing so the prop is absent, not `undefined`,
    // which `exactOptionalPropertyTypes` rejects).
    const middleButton = offerSync
      ? { strMiddleButtonText: SWITCH_ANYWAY_LABEL, onMiddleButton: () => resolve("switch_anyway") }
      : {};

    showModal(
      <ConfirmModal
        strTitle={UNSYNCED_SAVES_TITLE}
        strDescription={unsyncedSavesDescription({ versionName, stranding, noSync })}
        strOKButtonText={offerSync ? SYNC_AND_SWITCH_LABEL : SWITCH_ANYWAY_LABEL}
        strCancelButtonText={CANCEL_LABEL}
        onOK={() => resolve(offerSync ? "sync_and_switch" : "switch_anyway")}
        onCancel={() => resolve("cancel")}
        {...middleButton}
      />,
    );
  });
}
