import { FC, useState } from "react";
import { PanelSectionRow, ButtonItem, Focusable } from "@decky/ui";
import { requestPluginInstall } from "../utils/deckyInstall";
import { isAnySessionActive } from "../utils/sessionManager";
import { showToast } from "../utils/toast";
import { dismissUpdateForVersion, useUpdateNoticeState } from "../utils/updateNoticeStore";
import { logError } from "../api/backend";

/** Refusal shown when a game is running — on the button and, at a press, as a toast. */
export const UPDATE_GAME_RUNNING_REASON =
  "Close your running game first — updating now would lose this session's play time.";

/** Where the address is pasted when the button is not an option. */
export const UPDATE_MANUAL_HINT =
  "Paste this address into Decky's Developer tab, under Install Plugin from URL, to update by hand.";

/** How far the press got. `handed` means Decky owns the rest of it. */
type Handover = "idle" | "handed" | "unavailable";

/**
 * The QAM card that says a newer Tender release exists.
 *
 * It is the only channel there is: Tender is not in Decky's plugin catalogue
 * and cannot be, and Decky's own update detection finds an installed plugin by
 * looking its name up there — so with no card, nobody ever learns a release
 * happened.
 *
 * The address is on the card whatever else it shows, because everything else
 * can fail and it cannot: an empty `pluginName` means plugin.json could not be
 * read and there is no name to hand Decky, and Decky's own installer route can
 * be gone. Both leave the reader with a URL and one instruction rather than a
 * dead button.
 */
export const UpdateNotice: FC = () => {
  const state = useUpdateNoticeState();
  const [handover, setHandover] = useState<Handover>("idle");

  // A card that cannot name the release has nothing to say. `available` already
  // implies a version — it is decided by comparing one — so this is the type
  // narrowing, not a second condition.
  if (!state.available || state.latestVersion === null) return null;
  const latestVersion = state.latestVersion;

  // No name, no button: Decky matches the existing installation by plugin.json's
  // name, and an empty one misses the match exactly as a wrong one would.
  const canInstall = state.pluginName !== "";
  const gameRunning = isAnySessionActive();

  const handleUpdate = () => {
    // The button is disabled while a game runs, but nothing re-renders this card
    // when one starts — so the press is where the refusal has to hold, and it
    // has to say why: after the handover Decky's dialog owns the screen and this
    // panel is torn down, so there is no later moment to warn in.
    if (isAnySessionActive()) {
      showToast(UPDATE_GAME_RUNNING_REASON);
      return;
    }
    setHandover("handed");
    // Deliberately not awaited. The call files a request; Decky's own dialog
    // asks for confirmation and the loader unloads this plugin before it
    // replaces the folder, so this promise may never settle and an await would
    // leave the card frozen mid-press.
    requestPluginInstall({
      artifact: state.downloadUrl,
      name: state.pluginName,
      version: latestVersion,
      hash: state.digest,
    }).catch((e) => {
      logError(`Failed to hand the update to Decky's installer: ${e}`);
      setHandover("unavailable");
    });
  };

  const handleDismiss = () => {
    dismissUpdateForVersion(latestVersion).catch((e) => logError(`Failed to dismiss the update notice: ${e}`));
  };

  const showManualHint = !canInstall || handover === "unavailable";
  const buttonDescription = gameRunning
    ? UPDATE_GAME_RUNNING_REASON
    : handover === "handed"
      ? "Decky is asking you to confirm the update."
      : undefined;

  return (
    <>
      <PanelSectionRow>
        {/* QAM-only, so the no-op activation is what makes the card a focus stop
            for scrolling — a bare Focusable is a container Steam never stops on. */}
        <Focusable onActivate={() => {}}>
          <div
            data-testid="update-notice"
            style={{
              padding: "8px 12px",
              backgroundColor: "rgba(61, 157, 246, 0.15)",
              borderLeft: "3px solid #3d9df6",
              borderRadius: "4px",
              fontSize: "12px",
            }}
          >
            <div style={{ fontWeight: "bold", color: "#3d9df6", marginBottom: "4px" }}>
              Tender {latestVersion} is available
            </div>
            <div style={{ color: "rgba(255, 255, 255, 0.7)", lineHeight: 1.5 }}>
              You have {state.currentVersion}.{" "}
              {canInstall
                ? "Update now hands this to Decky, which asks you to confirm before installing."
                : "This plugin could not read its own name, so it cannot ask Decky to install the update."}
            </div>
            {showManualHint && (
              <div data-testid="update-manual-hint" style={{ color: "rgba(255, 255, 255, 0.7)", marginTop: "6px" }}>
                {handover === "unavailable" ? "Decky's installer could not be reached. " : ""}
                {UPDATE_MANUAL_HINT}
              </div>
            )}
            <div
              data-testid="update-download-url"
              style={{
                color: "rgba(255, 255, 255, 0.85)",
                marginTop: "6px",
                wordBreak: "break-all",
                fontFamily: "monospace",
              }}
            >
              {state.downloadUrl}
            </div>
          </div>
        </Focusable>
      </PanelSectionRow>
      {canInstall && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            bottomSeparator="none"
            disabled={gameRunning || handover === "handed"}
            description={buttonDescription}
            onClick={handleUpdate}
          >
            {handover === "handed" ? "Waiting for Decky…" : "Update now"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" bottomSeparator="none" onClick={handleDismiss}>
          Dismiss
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
};
