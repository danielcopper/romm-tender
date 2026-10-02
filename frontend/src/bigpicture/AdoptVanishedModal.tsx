/**
 * The dialog a download opens when the game page said a copy was on this device
 * and the search at press time could name nothing at all (#260, ADR-0028).
 *
 * This is the backstop, and it is what makes the button's promise keepable. The
 * page reads a `roms` row and must stay instant; the search reads the server's
 * payload and the path the download derived. The two have disagreed four times
 * over, so the button does not promise that they agree — it promises that
 * pressing ends in an answer, and this is the answer when nothing more specific
 * is known.
 */

import { FC } from "react";
import { ModalRoot, DialogButton, showModal } from "@decky/ui";
import {
  CANCEL_LABEL,
  VANISHED_DOWNLOAD_NOTE,
  VANISHED_INTRO,
  VANISHED_TITLE,
  vanishedDownloadLabel,
} from "../utils/adoptWording";
import type { VanishedChoice } from "../utils/adoptFlow";
import type { CandidateVanishedResult } from "../types";

interface AdoptVanishedModalProps {
  vanished: CandidateVanishedResult;
  closeModal?: () => void;
  onChoice: (choice: VanishedChoice) => void;
}

const LABEL_STYLE = { fontSize: "12px", color: "rgba(255,255,255,0.55)" };

export const AdoptVanishedModal: FC<AdoptVanishedModalProps> = ({ vanished, closeModal, onChoice }) => {
  const choose = (choice: VanishedChoice) => {
    closeModal?.();
    onChoice(choice);
  };

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ padding: "16px", minWidth: "420px" }}>
        <div style={{ fontSize: "16px", fontWeight: "bold", color: "#fff", marginBottom: "4px" }}>{VANISHED_TITLE}</div>
        <div style={{ fontSize: "13px", color: "rgba(255,255,255,0.7)", marginBottom: "12px" }}>{VANISHED_INTRO}</div>

        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <DialogButton onClick={() => choose("download")}>{vanishedDownloadLabel(vanished)}</DialogButton>
          <div style={LABEL_STYLE}>{VANISHED_DOWNLOAD_NOTE}</div>
          <DialogButton onClick={() => choose("cancel")} style={{ opacity: 0.5 }}>
            {CANCEL_LABEL}
          </DialogButton>
        </div>
      </div>
    </ModalRoot>
  );
};

/**
 * Show the dialog and resolve with the exit the user took. Dismissing it without
 * pressing anything never resolves, so the caller keeps its "nothing happened"
 * state — the same shape as its sibling dialogs.
 */
export function showAdoptVanishedModal(vanished: CandidateVanishedResult): Promise<VanishedChoice> {
  return new Promise<VanishedChoice>((resolve) => {
    showModal(<AdoptVanishedModal vanished={vanished} onChoice={resolve} />);
  });
}
