/**
 * The launch prompts drawn as Steam's gamepad modals — the set Big Picture's
 * Play button asks through, and the launch watcher's fallback
 * (`launchPromptsForThisStart`).
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
  // The launch check resolves before the game starts, while Steam may still list a start it cancelled.
  resolveConflicts: (conflicts) => handleConflicts(conflicts, { beforeStart: true }),
  askOfflineDrift: () => showOfflineDriftModal(),
  confirmFallbackLaunch: (message) => showFallbackLaunchModal(message),
  confirmUncheckedSource: () => showUncheckedSourceModal(),
  askSyncWait: (reason, finished) => showSyncWaitModal(reason, finished),
};
