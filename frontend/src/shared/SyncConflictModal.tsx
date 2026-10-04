import { FC, useState } from "react";
import { showToast } from "../utils/toast";
import { ModalRoot, DialogButton, showModal } from "@decky/ui";
import type { SyncConflict } from "../types";
import {
  CONFLICT_EXPLANATION,
  conflictTitle,
  localSaveDetail,
  resolveConflictsSequentially,
  resolveOneConflict,
  serverSaveDetail,
  serverSaveLabel,
  type SyncConflictAction,
  type SyncConflictResolution,
} from "../utils/saveConflictFlow";

interface SyncConflictModalProps {
  conflict: SyncConflict;
  onResolve: (action: SyncConflictAction) => Promise<void>;
  onCancel: () => void;
  isLoading?: boolean;
  errorMessage?: string | null;
}

/**
 * Controlled modal: parent owns isLoading + errorMessage. Three actions:
 *   - Keep Local  -> onResolve("keep_local")  -> backend POSTs local to server (overwrite=true)
 *   - Use Server  -> onResolve("use_server")  -> backend downloads server, overwrites local
 *   - Cancel      -> onCancel()               -> pure UI close, no backend call.
 *                                                Conflict re-fires on next sync if state still holds.
 *
 * If `onResolve` throws, the parent should set `errorMessage` and keep the modal
 * mounted so the user can retry. Buttons are disabled while `isLoading` is true.
 */
const SyncConflictModal: FC<SyncConflictModalProps> = ({
  conflict,
  onResolve,
  onCancel,
  isLoading = false,
  errorMessage = null,
}) => {
  const handleResolve = (action: SyncConflictAction) => {
    onResolve(action).catch(() => {
      // onResolve owns its own error handling; swallow rejections at the
      // event-handler boundary so React doesn't see an unhandled promise.
    });
  };

  return (
    <ModalRoot closeModal={isLoading ? undefined : onCancel}>
      <div style={{ padding: "16px", minWidth: "360px" }}>
        <div
          style={{
            fontSize: "16px",
            fontWeight: "bold",
            marginBottom: "4px",
            color: "#fff",
          }}
        >
          {conflictTitle(conflict)}
        </div>
        <div
          style={{
            fontSize: "12px",
            color: "rgba(255, 255, 255, 0.6)",
            marginBottom: "16px",
            lineHeight: "1.4",
          }}
        >
          {CONFLICT_EXPLANATION}
        </div>

        {/* Local save block */}
        <div
          style={{
            padding: "10px",
            background: "rgba(76, 175, 80, 0.15)",
            borderRadius: "4px",
            border: "1px solid rgba(76, 175, 80, 0.3)",
            marginBottom: "10px",
          }}
        >
          <div style={{ fontSize: "12px", fontWeight: "bold", color: "#81c784", marginBottom: "6px" }}>
            Your local save
          </div>
          <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.7)", marginBottom: "2px" }}>
            {localSaveDetail(conflict)}
          </div>
          <div style={{ marginTop: "8px" }}>
            <DialogButton onClick={() => handleResolve("keep_local")} disabled={isLoading}>
              Keep Local
            </DialogButton>
          </div>
        </div>

        {/* Server save block */}
        <div
          style={{
            padding: "10px",
            background: "rgba(33, 150, 243, 0.15)",
            borderRadius: "4px",
            border: "1px solid rgba(33, 150, 243, 0.3)",
            marginBottom: "10px",
          }}
        >
          <div style={{ fontSize: "12px", fontWeight: "bold", color: "#64b5f6", marginBottom: "6px" }}>
            {serverSaveLabel(conflict)}
          </div>
          <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.7)", marginBottom: "2px" }}>
            {serverSaveDetail(conflict)}
          </div>
          <div style={{ marginTop: "8px" }}>
            <DialogButton onClick={() => handleResolve("use_server")} disabled={isLoading}>
              Use Server
            </DialogButton>
          </div>
        </div>

        {errorMessage ? (
          <div
            style={{
              padding: "8px 10px",
              background: "rgba(244, 67, 54, 0.15)",
              borderRadius: "4px",
              border: "1px solid rgba(244, 67, 54, 0.3)",
              marginBottom: "12px",
              fontSize: "12px",
              color: "#ef9a9a",
              lineHeight: "1.4",
            }}
          >
            {errorMessage}
          </div>
        ) : null}

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <DialogButton onClick={onCancel} disabled={isLoading} style={{ opacity: 0.7 }}>
            Cancel
          </DialogButton>
        </div>
      </div>
    </ModalRoot>
  );
};

interface SyncConflictModalHostProps {
  conflict: SyncConflict;
  closeModal?: () => void;
  onDone: (resolution: SyncConflictResolution) => void;
}

/**
 * Stateful wrapper: handles the resolveSyncConflict endpoint, error display,
 * and modal close timing. Used by `showSyncConflictModal`.
 */
const SyncConflictModalHost: FC<SyncConflictModalHostProps> = ({ conflict, closeModal, onDone }) => {
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleResolve = async (action: SyncConflictAction): Promise<void> => {
    setIsLoading(true);
    setErrorMessage(null);
    const outcome = await resolveOneConflict(conflict, action);
    if (!outcome.ok) {
      setErrorMessage(outcome.message);
      setIsLoading(false);
      return;
    }
    showToast(outcome.toast);
    closeModal?.();
    onDone(action);
  };

  const handleCancel = () => {
    if (isLoading) return;
    closeModal?.();
    onDone("cancel");
  };

  return (
    <SyncConflictModal
      conflict={conflict}
      onResolve={handleResolve}
      onCancel={handleCancel}
      isLoading={isLoading}
      errorMessage={errorMessage}
    />
  );
};

/**
 * Show the sync-conflict modal and return a Promise that resolves once the
 * user picks an action (or cancels).
 */
export function showSyncConflictModal(conflict: SyncConflict): Promise<SyncConflictResolution> {
  return new Promise<SyncConflictResolution>((resolve) => {
    showModal(<SyncConflictModalHost conflict={conflict} onDone={resolve} />);
  });
}

/**
 * Walk a list of conflicts sequentially, showing the resolution modal for each.
 * Bails on the first cancel so the caller can decide what to do (e.g. not
 * relaunch). Called by the Play button (pre-launch gate and Resolve) and by the
 * launch watcher through `LaunchPrompts`. Returns "resolved" once every
 * conflict was resolved (or the list was empty), "cancel" on the first
 * dismissal.
 */
export function handleConflicts(conflicts: SyncConflict[]): Promise<"cancel" | "resolved"> {
  return resolveConflictsSequentially(conflicts, showSyncConflictModal);
}
