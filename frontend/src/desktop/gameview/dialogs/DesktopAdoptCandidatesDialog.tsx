/**
 * The desktop client's list of files that may be this game under another name.
 * Opened only for two or more; picking one leads on to the comparison. Big
 * Picture's `AdoptCandidateModal` is the other drawing of it.
 */

import type { FC } from "react";
import {
  CANCEL_LABEL,
  CANDIDATES_INTRO,
  CANDIDATES_TITLE,
  candidateDetail,
  candidatesTruncatedNote,
  noneOfTheseLabel,
} from "../../../utils/adoptWording";
import type { CandidateChoice } from "../../../utils/adoptFlow";
import type { CandidatesFoundResult } from "../../../types";
import {
  DIALOG_ACTIONS_STYLE,
  DIALOG_MUTED_STYLE,
  DIALOG_TEXT_STYLE,
  DIALOG_VALUE_STYLE,
  DesktopDialog,
  dialogButtonStyle,
} from "./DesktopDialog";

interface DesktopAdoptCandidatesDialogProps {
  found: CandidatesFoundResult;
  onChoice: (choice: CandidateChoice) => void;
}

export const DesktopAdoptCandidatesDialog: FC<DesktopAdoptCandidatesDialogProps> = ({ found, onChoice }) => (
  <DesktopDialog
    titleId="tender-desktop-adopt-candidates-title"
    title={CANDIDATES_TITLE}
    onDismiss={() => onChoice({ kind: "cancel" })}
  >
    <div style={DIALOG_TEXT_STYLE}>{CANDIDATES_INTRO}</div>

    <div style={{ ...DIALOG_ACTIONS_STYLE, marginBottom: "12px" }}>
      {found.candidates.map((candidate) => (
        <button
          key={candidate.path}
          type="button"
          className="tender-desktop-adopt-candidate"
          style={dialogButtonStyle("secondary")}
          onClick={() => onChoice({ kind: "candidate", candidate })}
        >
          <div style={DIALOG_VALUE_STYLE}>{candidate.name}</div>
          <div style={DIALOG_MUTED_STYLE}>{candidateDetail(candidate)}</div>
        </button>
      ))}
    </div>

    {found.truncated && (
      <div style={{ ...DIALOG_MUTED_STYLE, marginBottom: "12px" }}>{candidatesTruncatedNote(found)}</div>
    )}

    <div style={DIALOG_ACTIONS_STYLE}>
      <button type="button" style={dialogButtonStyle("primary")} onClick={() => onChoice({ kind: "download" })}>
        {noneOfTheseLabel(found)}
      </button>
      <button type="button" style={dialogButtonStyle("quiet")} onClick={() => onChoice({ kind: "cancel" })}>
        {CANCEL_LABEL}
      </button>
    </div>
  </DesktopDialog>
);
