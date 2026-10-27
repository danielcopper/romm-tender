/**
 * The launch prompts drawn as Steam's gamepad modals — the set both launch
 * paths ask through.
 */

import type { LaunchPrompts } from "../utils/launchVerdict";
import { showCoreChangeModal } from "./CoreChangeModal";
import { showFallbackLaunchModal } from "./FallbackLaunchModal";
import { showOfflineDriftModal } from "./OfflineDriftModal";
import { handleConflicts } from "./SyncConflictModal";
import { showSyncWaitModal } from "./SyncWaitModal";
import { showUncheckedSourceModal } from "./UncheckedSourceModal";

export const gamepadLaunchPrompts: LaunchPrompts = {
  confirmCoreChange: (oldLabel, newLabel) => showCoreChangeModal(oldLabel, newLabel),
  resolveConflicts: (conflicts) => handleConflicts(conflicts),
  askOfflineDrift: () => showOfflineDriftModal(),
  confirmFallbackLaunch: (message) => showFallbackLaunchModal(message),
  confirmUncheckedSource: () => showUncheckedSourceModal(),
  askSyncWait: (reason, finished) => showSyncWaitModal(reason, finished),
};
