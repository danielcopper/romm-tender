import { FC } from "react";
import { dismissStoppedUpdateCard, useStoppedUpdateAttempt } from "../utils/stoppedUpdateStore";
import { INSTALLER_STOPPED_SENTENCE } from "../utils/updateInstallView";
import { updateDidNotGoThrough } from "../utils/updateOutcomeStore";
import { logError } from "../api/backend";
import { AMBER, AMBER_WASH } from "./layout/pane";
import { UpdateCard } from "./UpdateCard";

/**
 * The notice on Main that an update's installer stopped without updating.
 *
 * The installer stops Tender before it replaces it, so an installer that gave
 * up after that point is only found by the next start. The card says so in the
 * words the rolled-back notice uses, points at the journal, and jumps to
 * Settings › Updates, where the attempt stands as failed with Try again.
 * Dismiss removes the backend's record, so the next start does not raise it
 * again.
 */
export const UpdateStoppedNotice: FC<{ onOpenUpdates: () => void }> = ({ onOpenUpdates }) => {
  const attempt = useStoppedUpdateAttempt();
  if (attempt === null) return null;

  const handleDismiss = () => {
    dismissStoppedUpdateCard().catch((e) => logError(`Failed to dismiss the stopped update notice: ${e}`));
  };

  return (
    <UpdateCard
      testId="update-stopped-notice"
      color={AMBER}
      wash={AMBER_WASH}
      title={updateDidNotGoThrough(attempt.attemptedVersion, attempt.fromVersion)}
      detail={INSTALLER_STOPPED_SENTENCE}
      onOpenUpdates={onOpenUpdates}
      onDismiss={handleDismiss}
    />
  );
};
