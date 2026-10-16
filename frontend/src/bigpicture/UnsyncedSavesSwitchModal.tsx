import { ConfirmModal, showModal } from "@decky/ui";
import type { UnsyncedSavesChoice, UnsyncedSavesPromptArgs } from "../utils/versionSwitch";
import {
  CANCEL_LABEL,
  SWITCH_ANYWAY_LABEL,
  SYNC_AND_SWITCH_LABEL,
  UNSYNCED_SAVES_TITLE,
  unsyncedSavesDescription,
} from "../utils/launchPromptWording";

/**
 * Unsynced-saves confirm for switching away from a downloaded version (#1298).
 * Shown by the version picker when the backend soft-blocks the switch
 * (`reason: "unsynced_saves"`): the bound install has local save changes that
 * were never uploaded to RomM.
 *
 * Two variants by reachability (one component, conditional middle/OK buttons),
 * mirroring `showOfflineDriftModal`'s `showModal(...)`-returns-a-Promise pattern:
 *
 * - Reachable (T4): OK = "Sync now & switch" (`sync_and_switch`), middle =
 *   "Switch anyway" (`switch_anyway`), Cancel (`cancel`).
 * - Offline (T5): OK = "Switch anyway" (`switch_anyway`), Cancel (`cancel`) — no
 *   sync option, since the saves can't be uploaded while RomM is unreachable.
 *
 * Outside-click / X routes through `ConfirmModal`'s `onCancel` → `cancel`.
 */
export function showUnsyncedSavesModal({
  versionName,
  serverReachable,
}: UnsyncedSavesPromptArgs): Promise<UnsyncedSavesChoice> {
  return new Promise<UnsyncedSavesChoice>((resolve) => {
    // Reachable: the middle button is the "strand it" escape hatch below the
    // primary "Sync now & switch". Offline: no middle button — OK IS "Switch
    // anyway" (spread nothing so the prop is absent, not `undefined`, which
    // `exactOptionalPropertyTypes` rejects).
    const middleButton = serverReachable
      ? { strMiddleButtonText: SWITCH_ANYWAY_LABEL, onMiddleButton: () => resolve("switch_anyway") }
      : {};

    showModal(
      <ConfirmModal
        strTitle={UNSYNCED_SAVES_TITLE}
        strDescription={unsyncedSavesDescription(versionName, serverReachable)}
        strOKButtonText={serverReachable ? SYNC_AND_SWITCH_LABEL : SWITCH_ANYWAY_LABEL}
        strCancelButtonText={CANCEL_LABEL}
        onOK={() => resolve(serverReachable ? "sync_and_switch" : "switch_anyway")}
        onCancel={() => resolve("cancel")}
        {...middleButton}
      />,
    );
  });
}
