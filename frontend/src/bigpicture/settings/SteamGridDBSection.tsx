/**
 * SteamGridDB API key entry. Pure renderer: the parent owns the masked-key
 * display value and the verify/save/remove handlers. The key is entered through
 * SgdbApiKeyModal, which validates it against SteamGridDB before saving, so this
 * section carries no status line of its own.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, DialogButton, Field, Focusable, showModal } from "@decky/ui";
import { SgdbApiKeyModal } from "./SgdbApiKeyModal";
import type { VerifyKeyResult } from "./SgdbApiKeyModal";
import { detach } from "../../utils/detach";

interface SteamGridDBSectionProps {
  sgdbApiKey: string;
  onVerifyKey: (key: string) => Promise<VerifyKeyResult>;
  onSaveKey: (key: string) => Promise<void>;
  onRemoveKey: () => Promise<void>;
}

export const SteamGridDBSection: FC<SteamGridDBSectionProps> = ({
  sgdbApiKey,
  onVerifyKey,
  onSaveKey,
  onRemoveKey,
}) => {
  return (
    <PanelSection title="SteamGridDB">
      <PanelSectionRow>
        <Field label="API Key" description={sgdbApiKey ? "••••" : "Not configured"}>
          <Focusable flow-children="horizontal" style={{ display: "flex", gap: "8px" }}>
            <DialogButton
              style={{ minWidth: "auto", width: "auto" }}
              onClick={() => showModal(<SgdbApiKeyModal onVerify={onVerifyKey} onSave={onSaveKey} />)}
            >
              Edit
            </DialogButton>
            {sgdbApiKey && (
              <DialogButton style={{ minWidth: "auto", width: "auto" }} onClick={() => detach(onRemoveKey())}>
                Remove
              </DialogButton>
            )}
          </Focusable>
        </Field>
      </PanelSectionRow>
    </PanelSection>
  );
};
