import { FC } from "react";
import { UpdateCard } from "./UpdateCard";
import { dismissUpdateForVersion, useUpdateNoticeState } from "../utils/updateNoticeStore";
import { failureTakesThePlaceOf, useUpdateOutcomeState } from "../utils/updateOutcomeStore";
import { stoppedAttemptTakesThePlaceOf, useStoppedUpdateAttempt } from "../utils/stoppedUpdateStore";
import { logError } from "../api/backend";

/**
 * The notice on Main that a newer Tender release is out.
 *
 * A notice and nothing more: it names the release and jumps to its home,
 * Settings › Updates, which states both versions and holds the check's
 * controls. Dismiss is per version, so the next release raises it again. It
 * gives way to the rolled-back notice and to the stopped-installer notice for
 * the version that update tried.
 */
export const UpdateNotice: FC<{ onOpenUpdates: () => void }> = ({ onOpenUpdates }) => {
  const state = useUpdateNoticeState();
  const outcome = useUpdateOutcomeState();
  const stopped = useStoppedUpdateAttempt();

  // `available` is decided by comparing a version, so it implies one; this is
  // the type narrowing, not a second condition.
  if (!state.available || state.latestVersion === null) return null;
  if (failureTakesThePlaceOf(state.latestVersion, outcome)) return null;
  if (stoppedAttemptTakesThePlaceOf(state.latestVersion, stopped)) return null;
  const latestVersion = state.latestVersion;

  const handleDismiss = () => {
    dismissUpdateForVersion(latestVersion).catch((e) => logError(`Failed to dismiss the update notice: ${e}`));
  };

  return (
    <UpdateCard
      testId="update-notice"
      color="#3d9df6"
      wash="rgba(61, 157, 246, 0.15)"
      title={`Tender ${latestVersion} is available`}
      detail={`Installed version: ${state.currentVersion}.`}
      onOpenUpdates={onOpenUpdates}
      onDismiss={handleDismiss}
    />
  );
};
