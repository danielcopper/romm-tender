/**
 * Modal that asks the user for a new save slot name. Owns its own text-field
 * state and submits the trimmed value via the `onSubmit` callback; all
 * slot-creation side effects belong in the parent.
 */

import { useState, FC, ChangeEvent, KeyboardEvent } from "react";
import { ConfirmModal, TextField } from "@decky/ui";

export const NewSlotModal: FC<{
  closeModal?: () => void;
  onSubmit: (name: string) => void;
}> = ({ closeModal, onSubmit }) => {
  const [value, setValue] = useState("");
  return (
    <ConfirmModal
      {...(closeModal !== undefined ? { closeModal } : {})}
      onOK={() => {
        onSubmit(value.trim());
      }}
      strTitle="New Save Slot"
      bDisableBackgroundDismiss={true}
      // Disable confirm on an empty/whitespace name (parent's no-op is the backstop).
      bOKDisabled={value.trim() === ""}
    >
      <TextField
        focusOnMount={true}
        label="Slot Name"
        value={value}
        onChange={(e: ChangeEvent<HTMLInputElement>) => setValue(e.target.value)}
        // Enter is the on-screen keyboard's "Eingabe" key. ConfirmModal doesn't
        // auto-close this manual path.
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === "Enter" && value.trim() !== "") {
            onSubmit(value.trim());
            closeModal?.();
          }
        }}
      />
    </ConfirmModal>
  );
};
