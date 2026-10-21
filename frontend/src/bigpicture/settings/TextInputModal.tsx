/**
 * Reusable text-input confirmation modal for SettingsPage edit flows: OK, or
 * Enter in the field, hands the typed value to `onSubmit` and closes it.
 */

import { useState, FC, ChangeEvent, KeyboardEvent } from "react";
import { ConfirmModal, TextField } from "@decky/ui";

interface TextInputModalProps {
  label: string;
  value: string;
  bIsPassword?: boolean;
  closeModal?: () => void;
  onSubmit: (value: string) => void;
}

export const TextInputModal: FC<TextInputModalProps> = ({
  label,
  value: initial,
  bIsPassword,
  closeModal,
  onSubmit,
}) => {
  const [value, setValue] = useState(initial);
  // The value this modal edits (the Default Save Slot) must be non-empty, so a
  // whitespace-only value never counts as a submit.
  const canSubmit = value.trim() !== "";
  const handleOK = () => {
    onSubmit(value);
  };
  return (
    <ConfirmModal
      {...(closeModal !== undefined ? { closeModal } : {})}
      onOK={handleOK}
      bOKDisabled={!canSubmit}
      strTitle={label}
      bDisableBackgroundDismiss={true}
    >
      <TextField
        focusOnMount={true}
        label={label}
        value={value}
        {...(bIsPassword !== undefined ? { bIsPassword } : {})}
        onChange={(e: ChangeEvent<HTMLInputElement>) => setValue(e.target.value)}
        // Enter (the on-screen keyboard's "Eingabe" key) confirms, same as the OK
        // button. ConfirmModal auto-closes on its OK button but not on this manual
        // path, so close here too — handleOK is shared so the two never drift.
        // Gated on canSubmit so an empty/whitespace field is a no-op on Enter,
        // matching the disabled OK button (on the Deck, R2/Enter would otherwise
        // confirm an empty value and close the modal; why Steam does that:
        // ValidatingModalShell's `dialogOKSubmits`).
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === "Enter" && canSubmit) {
            handleOK();
            closeModal?.();
          }
        }}
      />
    </ConfirmModal>
  );
};
