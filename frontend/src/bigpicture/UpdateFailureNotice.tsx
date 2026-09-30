import { FC } from "react";
import {
  dismissUpdateFailureRecord,
  failureCardShows,
  updateFailureReason,
  updateFailureSentence,
  useUpdateOutcomeState,
} from "../utils/updateOutcomeStore";
import { logError } from "../api/backend";
import { AMBER, AMBER_WASH } from "./layout/pane";
import { UpdateCard } from "./UpdateCard";

/**
 * The notice on Main that an update did not go through: the installer rolled it
 * back, or its pre-install check refused the new version before anything was
 * replaced.
 *
 * States the fact and where its reason is, and jumps to its home, Settings ›
 * Updates, which states it too. Dismiss is per record, so the next record the
 * installer writes raises it again; the record going away — a later update that answered —
 * takes it down as well.
 */
export const UpdateFailureNotice: FC<{ onOpenUpdates: () => void }> = ({ onOpenUpdates }) => {
  const state = useUpdateOutcomeState();

  // `failureCardShows` implies a record; this is the type narrowing, not a second condition.
  if (!failureCardShows(state) || state.failure === null) return null;
  const failure = state.failure;

  const handleDismiss = () => {
    dismissUpdateFailureRecord(failure.rolledBackAt).catch((e) =>
      logError(`Failed to dismiss the rolled-back update notice: ${e}`),
    );
  };

  return (
    <UpdateCard
      testId="update-failure-notice"
      color={AMBER}
      wash={AMBER_WASH}
      title={updateFailureSentence(failure)}
      detail={updateFailureReason(failure)}
      onOpenUpdates={onOpenUpdates}
      onDismiss={handleDismiss}
    />
  );
};
