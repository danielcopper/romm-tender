/**
 * RomM server connection settings — URL, account sign-in/token, and SSL toggle.
 * Pure renderer: the parent owns the field values, the has-token flag, the
 * status string, and the save/sign-in logic. The QAM connection row probes the
 * server automatically, so there is no manual test affordance here.
 *
 * It is one group of the Settings page's Connections section, and its title
 * names the SERVICE rather than the section: SteamGridDB is the other service
 * on that pane and both are connections.
 */

import { FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  ConfirmModal,
  DialogButton,
  Field,
  showModal,
  ToggleField,
} from "@decky/ui";
import { TextInputModal } from "./TextInputModal";
import { ConnectModal } from "./ConnectModal";
import type { SignInResult } from "./ConnectModal";
import { isHttpsUrl } from "../../utils/serverUrl";

// Sign-out only forgets the token on this device; it never revokes it in RomM.
const SIGN_OUT_CONFIRM_DESCRIPTION =
  "This only forgets the RomM token on this device. The token itself stays valid in RomM — " +
  "revoke it there (Settings → API Tokens) if you no longer want it.";

interface ConnectionSectionProps {
  url: string;
  hasToken: boolean;
  allowInsecureSsl: boolean;
  status: string;
  onUrlChange: (value: string) => void;
  onConnect: (username: string, password: string) => Promise<SignInResult>;
  onConnectToken: (token: string) => Promise<SignInResult>;
  onConnectPairing: (code: string) => Promise<SignInResult>;
  onAllowInsecureSslChange: (value: boolean) => void;
  onSignOut: () => void;
}

export const ConnectionSection: FC<ConnectionSectionProps> = ({
  url,
  hasToken,
  allowInsecureSsl,
  status,
  onUrlChange,
  onConnect,
  onConnectToken,
  onConnectPairing,
  onAllowInsecureSslChange,
  onSignOut,
}) => {
  return (
    <PanelSection title="RomM">
      <PanelSectionRow>
        <Field label="RomM URL" description={url || "(not set)"}>
          <DialogButton
            style={{ minWidth: "auto", width: "auto" }}
            onClick={() =>
              showModal(<TextInputModal label="RomM URL" value={url} field="url" onSubmit={onUrlChange} />)
            }
          >
            Edit
          </DialogButton>
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="RomM Account" description={hasToken ? "Signed in" : "Not signed in"}>
          <DialogButton
            style={{ minWidth: "auto", width: "auto" }}
            onClick={() =>
              showModal(
                <ConnectModal
                  onConnect={onConnect}
                  onConnectToken={onConnectToken}
                  onConnectPairing={onConnectPairing}
                />,
              )
            }
          >
            {hasToken ? "Sign in again" : "Sign in"}
          </DialogButton>
        </Field>
      </PanelSectionRow>
      {hasToken && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Forgets the RomM token on this device. The token stays valid in RomM."
            onClick={() =>
              showModal(
                <ConfirmModal
                  strTitle="Sign out of RomM?"
                  strDescription={SIGN_OUT_CONFIRM_DESCRIPTION}
                  strOKButtonText="Sign out"
                  strCancelButtonText="Cancel"
                  onOK={onSignOut}
                />,
              )
            }
          >
            Sign out
          </ButtonItem>
        </PanelSectionRow>
      )}
      {isHttpsUrl(url) && (
        <PanelSectionRow>
          <ToggleField
            label="Allow Insecure SSL"
            description="Skip certificate verification for self-signed certs (LAN only)"
            checked={allowInsecureSsl}
            onChange={onAllowInsecureSslChange}
          />
        </PanelSectionRow>
      )}
      {status && (
        <PanelSectionRow>
          {/* Focusable because the pane scrolls by moving focus and this line
              is not the last thing on it — the SteamGridDB group follows, so a
              reader walking down would step straight over the answer to the
              sign-in they just made. */}
          <Field label={status} focusable={true} />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
