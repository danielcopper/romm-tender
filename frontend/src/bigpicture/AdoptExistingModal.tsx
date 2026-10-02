/**
 * The dialog a download opens instead of writing over content the plugin did not
 * put there, and the one it opens for a file already on the device under a
 * different name (#260, ADR-0028). It states both sides of the comparison,
 * offers the content check on a button — never as a wait before the dialog
 * appears — and has three exits: use what is there, download instead, or do
 * nothing.
 *
 * Downloading is the only destructive exit, so it takes a second confirmation
 * that names the deletion. That confirmation is a step *inside* this modal
 * rather than a nested one: the comparison the user is deciding from stays on
 * screen behind it.
 */

import { FC, useEffect, useState } from "react";
import { ModalRoot, DialogButton, showModal } from "@decky/ui";
import { addEventListener, removeEventListener } from "../api/host";
import { debugLog, verifyExistingContent } from "../api/backend";
import {
  CANCEL_LABEL,
  CHECK_AGAINST_SERVER_LABEL,
  DELETE_AND_DOWNLOAD_LABEL,
  DOWNLOAD_INSTEAD_LABEL,
  EXISTING_TITLE,
  GO_BACK_LABEL,
  ON_THE_SERVER_HEADING,
  ON_THIS_DEVICE_HEADING,
  VERIFY_UNREACHABLE_MESSAGE,
  adoptButtonLabel,
  existingIntro,
  existingSize,
  incomingSize,
  lastChangedLine,
  renameNotice,
  replaceWarning,
  sizeVerdict,
  verifyProgressLabel,
} from "../utils/adoptWording";
import type { AdoptChoice } from "../utils/adoptFlow";
import { detach } from "../utils/detach";
import type { TargetOccupiedResult, VerifyContentResult, VerifyProgressEvent } from "../types";

interface AdoptExistingModalProps {
  romId: number;
  occupied: TargetOccupiedResult;
  /** As on `AdoptionDialogs.showExisting`; also the path the content check runs against. */
  candidatePath?: string | undefined;
  closeModal?: () => void;
  onChoice: (choice: AdoptChoice) => void;
}

const LABEL_STYLE = { fontSize: "12px", color: "rgba(255,255,255,0.55)" };
const VALUE_STYLE = { fontSize: "13px", color: "#fff" };

const VERIFY_COLORS: Record<VerifyContentResult["status"], string> = {
  match: "#6dd36d",
  mismatch: "#ff8a80",
  unverifiable: "rgba(255,255,255,0.7)",
  missing: "#ff8a80",
  error: "#ff8a80",
};

export const AdoptExistingModal: FC<AdoptExistingModalProps> = ({
  romId,
  occupied,
  candidatePath,
  closeModal,
  onChoice,
}) => {
  const [verifying, setVerifying] = useState(false);
  const [verifyProgress, setVerifyProgress] = useState<number | null>(null);
  const [verdict, setVerdict] = useState<VerifyContentResult | null>(null);
  const [confirmingReplace, setConfirmingReplace] = useState(false);

  useEffect(() => {
    const listener = addEventListener<VerifyProgressEvent>("verify_progress", (payload) => {
      if (payload.rom_id !== romId || !payload.bytes_total) return;
      setVerifyProgress(payload.bytes_done / payload.bytes_total);
    });
    return () => removeEventListener("verify_progress", listener);
  }, [romId]);

  const handleVerify = async () => {
    if (verifying) return;
    setVerifying(true);
    setVerdict(null);
    setVerifyProgress(0);
    try {
      setVerdict(await verifyExistingContent(romId, candidatePath ?? null));
    } catch (e) {
      detach(debugLog(`AdoptExistingModal: verify failed: ${e}`));
      setVerdict({ status: "error", message: VERIFY_UNREACHABLE_MESSAGE, differences: [] });
    } finally {
      setVerifying(false);
      setVerifyProgress(null);
    }
  };

  const choose = (choice: AdoptChoice) => {
    closeModal?.();
    onChoice(choice);
  };

  const candidate = Boolean(candidatePath);
  const lastChanged = lastChangedLine(occupied);

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ padding: "16px", minWidth: "420px" }}>
        <div style={{ fontSize: "16px", fontWeight: "bold", color: "#fff", marginBottom: "4px" }}>{EXISTING_TITLE}</div>
        <div style={{ fontSize: "13px", color: "rgba(255,255,255,0.7)", marginBottom: "12px" }}>
          {existingIntro(occupied, candidate)}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "8px" }}>
          <div>
            <div style={LABEL_STYLE}>{ON_THIS_DEVICE_HEADING}</div>
            <div style={VALUE_STYLE}>{occupied.existing.name}</div>
            <div style={VALUE_STYLE}>{existingSize(occupied, candidate)}</div>
            {lastChanged && <div style={LABEL_STYLE}>{lastChanged}</div>}
          </div>
          <div>
            <div style={LABEL_STYLE}>{ON_THE_SERVER_HEADING}</div>
            <div style={VALUE_STYLE}>{occupied.incoming.name}</div>
            <div style={VALUE_STYLE}>{incomingSize(occupied)}</div>
          </div>
        </div>
        <div style={{ fontSize: "13px", color: "#fff", marginBottom: "12px" }}>{sizeVerdict(occupied, candidate)}</div>
        {candidate && (
          <div style={{ fontSize: "13px", color: "rgba(255,255,255,0.7)", marginBottom: "12px" }}>
            {renameNotice(occupied)}
          </div>
        )}

        {verifying && (
          <div style={{ fontSize: "13px", color: "rgba(255,255,255,0.7)", marginBottom: "12px" }}>
            {verifyProgressLabel(verifyProgress)}
          </div>
        )}
        {!verifying && verdict && (
          <div style={{ marginBottom: "12px" }}>
            <div style={{ fontSize: "13px", color: VERIFY_COLORS[verdict.status] }}>{verdict.message}</div>
            {verdict.differences.map((d) => (
              <div key={d.name} style={{ ...LABEL_STYLE, marginTop: "4px" }}>
                {d.name}: {d.detail}
              </div>
            ))}
          </div>
        )}

        {confirmingReplace ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <div style={{ fontSize: "13px", color: "#ff8a80" }}>{replaceWarning(occupied, candidate)}</div>
            <DialogButton onClick={() => choose("replace")}>{DELETE_AND_DOWNLOAD_LABEL}</DialogButton>
            <DialogButton onClick={() => setConfirmingReplace(false)} style={{ opacity: 0.5 }}>
              {GO_BACK_LABEL}
            </DialogButton>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <DialogButton onClick={() => choose("adopt")} disabled={!occupied.adoptable}>
              {adoptButtonLabel(occupied)}
            </DialogButton>
            <DialogButton
              onClick={() => {
                detach(handleVerify());
              }}
              disabled={verifying}
            >
              {CHECK_AGAINST_SERVER_LABEL}
            </DialogButton>
            <DialogButton onClick={() => setConfirmingReplace(true)}>{DOWNLOAD_INSTEAD_LABEL}</DialogButton>
            <DialogButton onClick={() => choose("cancel")} style={{ opacity: 0.5 }}>
              {CANCEL_LABEL}
            </DialogButton>
          </div>
        )}
      </div>
    </ModalRoot>
  );
};

/**
 * Show the dialog and resolve with the exit the user took. Dismissing the modal
 * without pressing anything (outside click, back button) never resolves, so the
 * caller keeps its "nothing happened" state — the same shape as
 * `showCoreChangeModal`.
 */
export function showAdoptExistingModal(
  romId: number,
  occupied: TargetOccupiedResult,
  candidatePath?: string,
): Promise<AdoptChoice> {
  return new Promise<AdoptChoice>((resolve) => {
    showModal(
      <AdoptExistingModal romId={romId} occupied={occupied} candidatePath={candidatePath} onChoice={resolve} />,
    );
  });
}
