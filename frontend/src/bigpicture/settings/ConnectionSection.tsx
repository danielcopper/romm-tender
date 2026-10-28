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
import { FaExclamationTriangle } from "react-icons/fa";
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
import { RommUrlModal } from "./RommUrlModal";
import { ConnectModal } from "./ConnectModal";
import type { SignInResult } from "./ConnectModal";
import { CustomHeadersModal } from "./CustomHeadersModal";
import type { SaveHeadersResult } from "./CustomHeadersModal";
import { isHttpsUrl } from "../../utils/serverUrl";
import type { CustomHeaderEntry } from "../../types";
import type { ServerUrlResult } from "../../api/backend";
import { AMBER } from "../layout/pane";

// Sign-out only forgets the token on this device; it never revokes it in RomM.
const SIGN_OUT_CONFIRM_DESCRIPTION =
  "This only forgets the RomM token on this device. The token itself stays valid in RomM — " +
  "revoke it there (Settings › API Tokens) if you no longer want it.";

const warningStyle = { color: AMBER, display: "inline-flex", alignItems: "center", gap: "6px" } as const;
const warningIconStyle = { flex: "0 0 auto" } as const;

interface ConnectionSectionProps {
  url: string;
  hasToken: boolean;
  allowInsecureSsl: boolean;
  status: string;
  /** Whether {@link status} reports a refusal or a failure, which the line shows in amber with a warning sign. */
  statusWarns: boolean;
  /** Names of the configured proxy headers — values never reach the frontend. */
  customHeaderNames: string[];
  onSaveUrl: (value: string) => Promise<ServerUrlResult>;
  onSaveCustomHeaders: (headers: CustomHeaderEntry[]) => Promise<SaveHeadersResult>;
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
  statusWarns,
  customHeaderNames,
  onSaveUrl,
  onSaveCustomHeaders,
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
            onClick={() => showModal(<RommUrlModal value={url} save={onSaveUrl} />)}
          >
            Edit
          </DialogButton>
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field
          label="Custom headers"
          description={customHeaderNames.length > 0 ? `${customHeaderNames.length} set` : "(none)"}
        >
          <DialogButton
            style={{ minWidth: "auto", width: "auto" }}
            onClick={() =>
              showModal(<CustomHeadersModal storedNames={customHeaderNames} onSave={onSaveCustomHeaders} />)
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
            description="Skip certificate checks for a self-signed server. Anyone who can intercept the connection can read what Tender sends — your RomM token, your password when you sign in with it, and any custom headers — and use your account. Only on a network you trust."
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
          <Field
            label={
              statusWarns ? (
                <span style={warningStyle}>
                  <FaExclamationTriangle style={warningIconStyle} />
                  {status}
                </span>
              ) : (
                status
              )
            }
            focusable={true}
          />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
