import { FC } from "react";
import { ModalRoot, DialogButton, showModal } from "@decky/ui";
import {
  CANCEL_LABEL,
  CORE_CHANGE_CONTINUE_LABEL,
  CORE_CHANGE_TITLE,
  CORE_CHANGE_WARNING,
  CORE_CHANGE_WARNING_HEADING,
} from "../utils/launchPromptWording";

interface CoreChangeModalProps {
  oldLabel: string;
  newLabel: string;
  closeModal?: () => void;
  onDone: (proceed: boolean) => void;
}

const CoreChangeModalContent: FC<CoreChangeModalProps> = ({ oldLabel, newLabel, closeModal, onDone }) => {
  const handleChoice = (proceed: boolean) => {
    closeModal?.();
    onDone(proceed);
  };

  return (
    <ModalRoot
      closeModal={() => {
        closeModal?.();
        onDone(false);
      }}
    >
      <div style={{ padding: "16px", minWidth: "320px" }}>
        <div
          style={{
            fontSize: "16px",
            fontWeight: "bold",
            marginBottom: "4px",
            color: "#fff",
          }}
        >
          {CORE_CHANGE_TITLE}
        </div>
        <div
          style={{
            fontSize: "13px",
            color: "rgba(255, 255, 255, 0.6)",
            marginBottom: "16px",
          }}
        >
          {oldLabel} → {newLabel}
        </div>

        <div
          style={{
            padding: "10px",
            background: "rgba(255, 152, 0, 0.15)",
            borderRadius: "4px",
            border: "1px solid rgba(255, 152, 0, 0.3)",
            marginBottom: "12px",
          }}
        >
          <div style={{ fontSize: "12px", color: "#ffb74d", marginBottom: "6px", fontWeight: "bold" }}>
            {CORE_CHANGE_WARNING_HEADING}
          </div>
          <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.7)", lineHeight: "1.4" }}>
            {CORE_CHANGE_WARNING}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <DialogButton onClick={() => handleChoice(true)}>{CORE_CHANGE_CONTINUE_LABEL}</DialogButton>
          <DialogButton onClick={() => handleChoice(false)} style={{ opacity: 0.5 }}>
            {CANCEL_LABEL}
          </DialogButton>
        </div>
      </div>
    </ModalRoot>
  );
};

export function showCoreChangeModal(oldLabel: string, newLabel: string): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    showModal(<CoreChangeModalContent oldLabel={oldLabel} newLabel={newLabel} onDone={resolve} />);
  });
}
