import { ConfirmModal, showModal } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import type { SyncFinish, SyncWaitChoice, SyncWaitReason } from "../utils/launchGate";
import {
  CANCEL_LABEL,
  SYNC_READY_DESCRIPTION,
  SYNC_READY_PLAY_LABEL,
  SYNC_READY_TITLE,
  SYNC_WAIT_LABEL,
  SYNC_WAIT_PLAY_ANYWAY_LABEL,
  SYNC_WAIT_TITLE,
  syncWaitDescription,
} from "../utils/launchPromptWording";

interface SyncWaitModalProps {
  reason: SyncWaitReason;
  finished: Promise<SyncFinish>;
  closeModal?: () => void;
  onDone: (choice: SyncWaitChoice) => void;
}

/**
 * The question a start asks once its pre-launch sync has run past the launch
 * check's limit. A sync that finishes while it is open turns it into "your
 * saves are ready" — never into a start by itself — or, where the answer needs
 * acting on first, closes it.
 */
const SyncWaitModal: FC<SyncWaitModalProps> = ({ reason, finished, closeModal, onDone }) => {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let open = true;
    void finished.then((finish) => {
      if (!open) return;
      if (finish === "ready") {
        setReady(true);
        return;
      }
      open = false;
      closeModal?.();
      onDone("answered");
    });
    return () => {
      open = false;
    };
  }, [finished, closeModal, onDone]);

  if (ready) {
    return (
      <ConfirmModal
        strTitle={SYNC_READY_TITLE}
        strDescription={SYNC_READY_DESCRIPTION}
        strOKButtonText={SYNC_READY_PLAY_LABEL}
        strCancelButtonText={CANCEL_LABEL}
        {...(closeModal === undefined ? {} : { closeModal })}
        onOK={() => onDone("answered")}
        onCancel={() => onDone("cancel")}
      />
    );
  }
  return (
    <ConfirmModal
      strTitle={SYNC_WAIT_TITLE}
      strDescription={syncWaitDescription(reason)}
      strOKButtonText={SYNC_WAIT_LABEL}
      strMiddleButtonText={SYNC_WAIT_PLAY_ANYWAY_LABEL}
      strCancelButtonText={CANCEL_LABEL}
      {...(closeModal === undefined ? {} : { closeModal })}
      onOK={() => onDone("wait")}
      onMiddleButton={() => onDone("play_anyway")}
      onCancel={() => onDone("cancel")}
    />
  );
};

/**
 * Ask whether to wait for the pre-launch sync, give up the start, or play on the
 * local saves. Resolves with the choice; outside-click / X is Cancel, which
 * `ConfirmModal` routes through `onCancel`.
 */
export function showSyncWaitModal(reason: SyncWaitReason, finished: Promise<SyncFinish>): Promise<SyncWaitChoice> {
  return new Promise<SyncWaitChoice>((resolve) => {
    showModal(<SyncWaitModal reason={reason} finished={finished} onDone={resolve} />);
  });
}
