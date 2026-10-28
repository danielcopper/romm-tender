/**
 * The desktop dialogs in the shape a launch asks for, {@link LaunchPrompts}.
 * Every one of them settles with its cancel exit when dismissed.
 */

import { resolveConflictsSequentially, type SyncConflictResolution } from "../../../utils/saveConflictFlow";
import type { LaunchPrompts } from "../../../utils/launchVerdict";
import type { SyncFinish, SyncWaitChoice, SyncWaitReason } from "../../../utils/launchGate";
import type { SyncConflict } from "../../../types";
import type { AskDialog } from "./askDialog";
import { DesktopSaveConflictDialog } from "./DesktopSaveConflictDialog";
import { DesktopOfflineDriftDialog } from "./DesktopOfflineDriftDialog";
import { DesktopFallbackLaunchDialog } from "./DesktopFallbackLaunchDialog";
import { DesktopCoreChangeDialog } from "./DesktopCoreChangeDialog";
import { DesktopUncheckedSourceDialog } from "./DesktopUncheckedSourceDialog";
import { DesktopSyncWaitDialog } from "./DesktopSyncWaitDialog";

function desktopSaveConflictDialog(
  ask: AskDialog,
  beforeStart: boolean,
): (conflict: SyncConflict) => Promise<SyncConflictResolution> {
  return (conflict) =>
    ask<SyncConflictResolution>("cancel", (resolve) => (
      <DesktopSaveConflictDialog conflict={conflict} beforeStart={beforeStart} onDone={resolve} />
    ));
}

function desktopOfflineDriftDialog(ask: AskDialog): () => Promise<"start_anyway" | "retry" | "cancel"> {
  return () =>
    ask<"start_anyway" | "retry" | "cancel">("cancel", (resolve) => <DesktopOfflineDriftDialog onChoice={resolve} />);
}

function desktopFallbackLaunchDialog(ask: AskDialog): (message?: string) => Promise<boolean> {
  return (message) =>
    ask<boolean>(false, (resolve) => <DesktopFallbackLaunchDialog message={message} onChoice={resolve} />);
}

function desktopCoreChangeDialog(ask: AskDialog): (oldLabel: string, newLabel: string) => Promise<boolean> {
  return (oldLabel, newLabel) =>
    ask<boolean>(false, (resolve) => (
      <DesktopCoreChangeDialog oldLabel={oldLabel} newLabel={newLabel} onChoice={resolve} />
    ));
}

function desktopUncheckedSourceDialog(ask: AskDialog): () => Promise<boolean> {
  return () => ask<boolean>(false, (resolve) => <DesktopUncheckedSourceDialog onChoice={resolve} />);
}

function desktopSyncWaitDialog(
  ask: AskDialog,
): (reason: SyncWaitReason, finished: Promise<SyncFinish>) => Promise<SyncWaitChoice> {
  return (reason, finished) =>
    ask<SyncWaitChoice>("cancel", (resolve) => (
      <DesktopSyncWaitDialog reason={reason} finished={finished} onChoice={resolve} />
    ));
}

export function desktopLaunchPrompts(ask: AskDialog): LaunchPrompts {
  return {
    confirmCoreChange: desktopCoreChangeDialog(ask),
    resolveConflicts: (conflicts) => resolveConflictsSequentially(conflicts, desktopSaveConflictDialog(ask, true)),
    askOfflineDrift: desktopOfflineDriftDialog(ask),
    confirmFallbackLaunch: desktopFallbackLaunchDialog(ask),
    confirmUncheckedSource: desktopUncheckedSourceDialog(ask),
    askSyncWait: desktopSyncWaitDialog(ask),
  };
}
