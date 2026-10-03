/**
 * The dialog a download opens when something in the platform folder carries this
 * game's name but cannot become this game's install (#260, ADR-0028).
 *
 * Two things reach it and they are one dialog, because the user's choice is the
 * same for both: an entry of the **other shape** — a folder where the server
 * sends one file, or a file where it sends a folder — and a **symlink**, which
 * is never adoptable whatever it points at, because an install row has to be
 * removable and the uninstall path refuses a link.
 *
 * Its question is whether to download a second copy beside the first, which
 * would otherwise be answered without the user. Nothing on disk is moved,
 * renamed or removed by either exit.
 */

import { FC } from "react";
import { ModalRoot, DialogButton, showModal } from "@decky/ui";
import { ENTRY_KIND_LABEL } from "../utils/formatters";
import {
  CANCEL_LABEL,
  UNUSABLE_DOWNLOAD_NOTE,
  UNUSABLE_TITLE,
  unusableDownloadLabel,
  unusableIntro,
  unusableTruncatedNote,
} from "../utils/adoptWording";
import type { UnusableChoice } from "../utils/adoptFlow";
import type { UnusableNamesakeResult } from "../types";

interface AdoptUnusableModalProps {
  unusable: UnusableNamesakeResult;
  closeModal?: () => void;
  onChoice: (choice: UnusableChoice) => void;
}

const LABEL_STYLE = { fontSize: "12px", color: "rgba(255,255,255,0.55)" };

export const AdoptUnusableModal: FC<AdoptUnusableModalProps> = ({ unusable, closeModal, onChoice }) => {
  const choose = (choice: UnusableChoice) => {
    closeModal?.();
    onChoice(choice);
  };

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ padding: "16px", minWidth: "420px" }}>
        <div style={{ fontSize: "16px", fontWeight: "bold", color: "#fff", marginBottom: "4px" }}>{UNUSABLE_TITLE}</div>
        <div style={{ fontSize: "13px", color: "rgba(255,255,255,0.7)", marginBottom: "12px" }}>
          {unusableIntro(unusable)}
        </div>

        <div style={{ marginBottom: "12px" }}>
          {unusable.existing.map((entry) => (
            <div key={entry.path} style={{ fontSize: "13px", color: "#fff", marginBottom: "2px" }}>
              {entry.name} <span style={LABEL_STYLE}>({ENTRY_KIND_LABEL[entry.kind]})</span>
            </div>
          ))}
        </div>

        {unusable.truncated && (
          <div style={{ ...LABEL_STYLE, marginBottom: "12px" }}>{unusableTruncatedNote(unusable)}</div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <DialogButton onClick={() => choose("download")}>{unusableDownloadLabel(unusable)}</DialogButton>
          <div style={LABEL_STYLE}>{UNUSABLE_DOWNLOAD_NOTE}</div>
          <DialogButton onClick={() => choose("cancel")} style={{ opacity: 0.5 }}>
            {CANCEL_LABEL}
          </DialogButton>
        </div>
      </div>
    </ModalRoot>
  );
};

/**
 * Show the dialog and resolve with the user's answer. Dismissing it without
 * pressing anything never resolves, so the caller keeps its "nothing happened"
 * state — the same shape as its sibling dialogs.
 */
export function showAdoptUnusableModal(unusable: UnusableNamesakeResult): Promise<UnusableChoice> {
  return new Promise<UnusableChoice>((resolve) => {
    showModal(<AdoptUnusableModal unusable={unusable} onChoice={resolve} />);
  });
}
