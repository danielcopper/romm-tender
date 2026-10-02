/**
 * The short list a download opens when this game is already on the device under
 * a different name (#260, ADR-0028). One row per file, strongest evidence first,
 * each stating what its offer rests on — nothing here has read a byte of
 * content, so the list is a starting point for the comparison dialog and never a
 * verdict.
 *
 * Shown only for two or more candidates: with exactly one there is nothing to
 * choose between, and the caller opens the comparison directly.
 */

import { FC } from "react";
import { ModalRoot, DialogButton, showModal } from "@decky/ui";
import {
  CANCEL_LABEL,
  CANDIDATES_INTRO,
  CANDIDATES_TITLE,
  candidateDetail,
  candidatesTruncatedNote,
  noneOfTheseLabel,
} from "../utils/adoptWording";
import type { CandidateChoice } from "../utils/adoptFlow";
import type { CandidatesFoundResult } from "../types";

interface AdoptCandidateModalProps {
  found: CandidatesFoundResult;
  closeModal?: () => void;
  onChoice: (choice: CandidateChoice) => void;
}

const LABEL_STYLE = { fontSize: "12px", color: "rgba(255,255,255,0.55)" };

export const AdoptCandidateModal: FC<AdoptCandidateModalProps> = ({ found, closeModal, onChoice }) => {
  const choose = (choice: CandidateChoice) => {
    closeModal?.();
    onChoice(choice);
  };

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ padding: "16px", minWidth: "420px" }}>
        <div style={{ fontSize: "16px", fontWeight: "bold", color: "#fff", marginBottom: "4px" }}>
          {CANDIDATES_TITLE}
        </div>
        <div style={{ fontSize: "13px", color: "rgba(255,255,255,0.7)", marginBottom: "12px" }}>{CANDIDATES_INTRO}</div>

        <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "12px" }}>
          {found.candidates.map((candidate) => (
            <DialogButton key={candidate.path} onClick={() => choose({ kind: "candidate", candidate })}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
                <div style={{ fontSize: "13px", color: "#fff" }}>{candidate.name}</div>
                <div style={LABEL_STYLE}>{candidateDetail(candidate)}</div>
              </div>
            </DialogButton>
          ))}
        </div>

        {found.truncated && (
          <div style={{ ...LABEL_STYLE, marginBottom: "12px" }}>{candidatesTruncatedNote(found)}</div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <DialogButton onClick={() => choose({ kind: "download" })}>{noneOfTheseLabel(found)}</DialogButton>
          <DialogButton onClick={() => choose({ kind: "cancel" })} style={{ opacity: 0.5 }}>
            {CANCEL_LABEL}
          </DialogButton>
        </div>
      </div>
    </ModalRoot>
  );
};

/**
 * Show the list and resolve with the exit the user took. Dismissing the modal
 * without pressing anything never resolves, so the caller keeps its "nothing
 * happened" state — the same shape as `showAdoptExistingModal`.
 */
export function showAdoptCandidateModal(found: CandidatesFoundResult): Promise<CandidateChoice> {
  return new Promise<CandidateChoice>((resolve) => {
    showModal(<AdoptCandidateModal found={found} onChoice={resolve} />);
  });
}
