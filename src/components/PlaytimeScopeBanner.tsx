import { FC } from "react";
import { PanelSection, PanelSectionRow, DialogButton, Focusable } from "@decky/ui";
import { setPlaytimeScopeState } from "../utils/playtimeScopeStore";

/** Title of the account-wide QAM playtime-scope banner. */
export const PLAYTIME_SCOPE_TITLE = "Cross-device playtime";

/** Body text prompting the user to re-mint a scoped Client API Token. */
export const PLAYTIME_SCOPE_MESSAGE = "Sign in again to enable cross-device playtime sync.";

/**
 * QAM PanelSection shown while the Client API Token lacks the `roms.user.read`
 * scope needed to read cross-device playtime. Account-wide (no per-game card).
 *
 * Dismiss is local-only — it just clears the shared store for this view. There
 * is no backend dismiss callable: the durable flag clears itself once a scoped
 * token is minted (a fresh sign-in or a later successful reconcile), so the
 * next MainPage mount re-fetches the real state.
 *
 * The condition's home is Settings › Connections, where the accounts are: the
 * sign-in that ends it exists only there, and this notice is the door to it.
 */
export const PlaytimeScopeBanner: FC<{ onOpenConnections: () => void }> = ({ onOpenConnections }) => {
  const handleDismiss = () => {
    setPlaytimeScopeState({ pending: false });
  };

  return (
    <PanelSection title={PLAYTIME_SCOPE_TITLE}>
      <PanelSectionRow>
        <div
          style={{
            padding: "8px 12px",
            backgroundColor: "rgba(212, 167, 44, 0.15)",
            borderLeft: "3px solid #d4a72c",
            borderRadius: "4px",
          }}
        >
          <div
            style={{
              fontSize: "13px",
              fontWeight: "bold",
              color: "#d4a72c",
              marginBottom: "6px",
            }}
          >
            {"⚠️"} {PLAYTIME_SCOPE_TITLE}
          </div>
          <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.85)", lineHeight: 1.5 }}>
            {PLAYTIME_SCOPE_MESSAGE}
          </div>
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        {/* Two buttons on one row rather than two full-width rows: Main is the
            narrow page, and a notice that costs three rows pushes the status
            block it sits above off the screen. */}
        <Focusable flow-children="horizontal" style={{ display: "flex", gap: "8px" }}>
          <DialogButton style={{ flex: "1 1 auto", minWidth: 0 }} onClick={onOpenConnections}>
            Open Connections
          </DialogButton>
          <DialogButton style={{ flex: "1 1 auto", minWidth: 0 }} onClick={handleDismiss}>
            Dismiss
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
    </PanelSection>
  );
};
