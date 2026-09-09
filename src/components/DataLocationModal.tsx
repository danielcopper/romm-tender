import { FC, useEffect, useState } from "react";
import { ConfirmModal, ModalRoot, DialogButton, showModal } from "@decky/ui";
import { chooseDataLocation, getDataLocationCandidates, logError, type DataLocationCandidate } from "../api/backend";
import { formatBytes, formatTimestampWithYear } from "../utils/formatters";
import { isAnyAppRunning } from "../utils/runningApps";
import { canRestartDevice, restartDevice } from "../utils/steamRestart";

/** Headline of the modal that resolves the two-libraries condition. */
export const DATA_LOCATION_MODAL_TITLE = "Which copy of your library should Tender keep?";

/**
 * What the modal says once a copy has been picked.
 *
 * It names the plugin's own next start, not a Steam restart: restarting the
 * Steam client reloads the frontend and does not start the backend again, and
 * this move runs before the database is opened.
 */
export const DATA_LOCATION_CHOSEN_BODY =
  "Tender copies the library you choose the next time it starts. Restarting your Steam Deck is the surest way to get " +
  "there. Nothing changes until then — Tender keeps using the copy it started with, and you can pick the other one " +
  "any time before you restart.";

const RESTART_CONFIRM_DESCRIPTION =
  "This closes your games and reboots the device. Tender copies the library you picked while it starts back up.";

/**
 * Ask before rebooting, then reboot.
 *
 * The confirm opens over the choice modal, which stays exactly as it was:
 * `showModal` anchors a modal to the SP window (`@decky/ui`'s `showModal` hands
 * `findSP()` to Steam's modal manager as the parent), not to the subtree of the
 * modal it was called from, so the two stand side by side and this one closing
 * leaves the other open. Cancel is given no handler at all — there is nothing to
 * undo, the recorded answer having been written when the copy was picked.
 */
function confirmRestartDevice(): void {
  showModal(
    <ConfirmModal
      strTitle="Restart your Steam Deck?"
      strDescription={RESTART_CONFIRM_DESCRIPTION}
      strOKButtonText="Restart now"
      strCancelButtonText="Cancel"
      onOK={restartDevice}
    />,
  );
}

/** What one candidate's size and date read as, including when neither could be established. */
export function candidateDetail(candidate: DataLocationCandidate): string {
  if (!candidate.present) return "no longer on disk";
  if (candidate.size_bytes == null) return "size could not be measured";
  return `${formatBytes(candidate.size_bytes)} · last changed ${formatTimestampWithYear(candidate.changed_at)}`;
}

interface DataLocationModalProps {
  candidates: DataLocationCandidate[] | null;
  /** The folder name already recorded, or `null` while the question is open. */
  chosen: string | null;
  onChoose: (source: string) => void;
  onClose: () => void;
  isLoading?: boolean;
  errorMessage?: string | null;
}

/**
 * Controlled modal: the parent owns the candidate read, the recorded answer, and
 * the error. Two phases, because the copy cannot happen while the plugin is
 * running from one of the candidates with its database open — the answer is
 * recorded, and the plugin's next start acts on it:
 *
 *   - open        -> a block per candidate, each with **Use this copy**
 *   - answered    -> what happens next, plus **Restart device now**
 *
 * A candidate that is no longer on disk is still listed — a choice shown with a
 * single option is not the question that was asked — and cannot be picked.
 * Cancel is a pure UI close and records nothing, so the condition re-fires.
 */
export const DataLocationModal: FC<DataLocationModalProps> = ({
  candidates,
  chosen,
  onChoose,
  onClose,
  isLoading = false,
  errorMessage = null,
}) => {
  const gameRunning = isAnyAppRunning();
  // Asked at render: a button that cannot do what it says is worse than a
  // sentence standing on its own, and the sentence already names the reboot.
  const rebootable = canRestartDevice();
  return (
    <ModalRoot closeModal={isLoading ? undefined : onClose}>
      <div style={{ padding: "16px", minWidth: "360px" }} data-testid="data-location-modal">
        <div style={{ fontSize: "16px", fontWeight: "bold", marginBottom: "4px", color: "#fff" }}>
          {DATA_LOCATION_MODAL_TITLE}
        </div>
        <div
          style={{
            fontSize: "12px",
            color: "rgba(255, 255, 255, 0.6)",
            marginBottom: "16px",
            lineHeight: "1.4",
          }}
        >
          {chosen === null
            ? "Two older installs of this plugin both hold a library, so Tender has not moved anything. Pick the one " +
              "to keep. Nothing is deleted — the other stays on disk exactly as it is."
            : DATA_LOCATION_CHOSEN_BODY}
        </div>

        {chosen === null && candidates === null ? (
          <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.7)", marginBottom: "12px" }}>
            Measuring both copies…
          </div>
        ) : null}

        {chosen === null
          ? (candidates ?? []).map((candidate) => (
              <div
                key={candidate.source}
                data-testid={`data-location-candidate-${candidate.source}`}
                style={{
                  padding: "10px",
                  background: "rgba(33, 150, 243, 0.15)",
                  borderRadius: "4px",
                  border: "1px solid rgba(33, 150, 243, 0.3)",
                  marginBottom: "10px",
                  opacity: candidate.present ? 1 : 0.6,
                }}
              >
                <div style={{ fontSize: "12px", fontWeight: "bold", color: "#64b5f6", marginBottom: "6px" }}>
                  {candidate.source}
                </div>
                <div
                  style={{
                    fontSize: "12px",
                    color: "rgba(255, 255, 255, 0.7)",
                    marginBottom: "2px",
                    wordBreak: "break-all",
                  }}
                >
                  {candidate.path}
                </div>
                <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.7)" }}>{candidateDetail(candidate)}</div>
                {candidate.present ? (
                  <div style={{ marginTop: "8px" }}>
                    <DialogButton onClick={() => onChoose(candidate.source)} disabled={isLoading}>
                      Use this copy
                    </DialogButton>
                  </div>
                ) : null}
              </div>
            ))
          : null}

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

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
          {/* The reboot would close a running game. `restartDevice` guards its
              own call too, so disabling here only spares a walk through the
              confirmation to a refusal toast. */}
          {chosen !== null && rebootable && (
            <DialogButton onClick={confirmRestartDevice} disabled={gameRunning}>
              Restart device now
            </DialogButton>
          )}
          <DialogButton onClick={onClose} disabled={isLoading} style={{ opacity: 0.7 }}>
            {chosen === null ? "Cancel" : "Later"}
          </DialogButton>
        </div>
      </div>
    </ModalRoot>
  );
};

interface DataLocationModalHostProps {
  closeModal?: () => void;
}

/**
 * Stateful wrapper: reads the candidates on mount, drives `choose_data_location`,
 * and keeps the modal open on an error so the choice can be retried.
 */
export const DataLocationModalHost: FC<DataLocationModalHostProps> = ({ closeModal }) => {
  const [candidates, setCandidates] = useState<DataLocationCandidate[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getDataLocationCandidates()
      .then((result) => {
        if (!live) return;
        setCandidates(result.candidates);
      })
      .catch((e: unknown) => {
        if (!live) return;
        const msg = e instanceof Error ? e.message : String(e);
        logError(`getDataLocationCandidates threw: ${msg}`);
        setErrorMessage("Could not read the older data folders.");
        setCandidates([]);
      });
    return () => {
      live = false;
    };
  }, []);

  const handleChoose = (source: string): void => {
    setIsLoading(true);
    setErrorMessage(null);
    chooseDataLocation(source)
      .then((result) => {
        if (!result.success) {
          logError(`chooseDataLocation(${source}) failed (${result.reason}): ${result.message}`);
          setErrorMessage(result.message);
          setIsLoading(false);
          return;
        }
        setChosen(source);
        setIsLoading(false);
      })
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e);
        logError(`chooseDataLocation(${source}) threw: ${msg}`);
        setErrorMessage(msg || "Could not record your choice.");
        setIsLoading(false);
      });
  };

  return (
    <DataLocationModal
      candidates={candidates}
      chosen={chosen}
      onChoose={handleChoose}
      onClose={() => closeModal?.()}
      isLoading={isLoading}
      errorMessage={errorMessage}
    />
  );
};

/** Open the data-location choice modal. */
export function showDataLocationModal(): void {
  showModal(<DataLocationModalHost />);
}
