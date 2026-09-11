import { FC, useEffect, useState } from "react";
import { PanelSectionRow, ButtonItem, Focusable } from "@decky/ui";
import { requestPluginInstall } from "../utils/deckyInstall";
import { isAnySessionActive } from "../utils/sessionManager";
import { showToast } from "../utils/toast";
import { dismissUpdateForVersion, useUpdateNoticeState } from "../utils/updateNoticeStore";
import { logError } from "../api/backend";

/** Refusal shown when a game is running — on the button and, at a press, as a toast. */
export const UPDATE_GAME_RUNNING_REASON =
  "Close your running game first — updating reloads Tender, which can drop this session's play time and its save sync.";

/** Where the address is pasted when the button is not an option. */
export const UPDATE_MANUAL_HINT =
  "Paste this address into Decky's Developer tab, under Install Plugin from URL, to update by hand.";

/**
 * How long the button stays down after a request is filed.
 *
 * It is a double-press guard, not a wait: nothing here can observe whether the
 * user confirmed, so the button has to come back on its own. Long enough that a
 * second press is a decision rather than a bounce, short enough that a reader
 * who declined Decky's dialog and changed their mind is not left with a dead
 * control.
 */
export const REQUEST_COOLDOWN_MS = 5000;

/** How far the press got. `filed` means Decky has been asked and not yet answered. */
type RequestState = "idle" | "filed" | "unavailable";

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
  const [request, setRequest] = useState<RequestState>("idle");

  // Above the early return, so the hook order does not depend on whether there
  // is a release to show.
  useEffect(() => {
    if (request !== "filed") return;
    const timer = setTimeout(() => setRequest("idle"), REQUEST_COOLDOWN_MS);
    return () => clearTimeout(timer);
  }, [request]);

  // A card that cannot name the release has nothing to say. `available` already
  // implies a version — it is decided by comparing one — so this is the type
  // narrowing, not a second condition.
  if (!state.available || state.latestVersion === null) return null;
  const latestVersion = state.latestVersion;

  // Two ways to have nothing to offer, both ending in the address alone.
  // No name: Decky matches the existing installation by plugin.json's name, and
  // an empty one misses the match exactly as a wrong one would. No version-bound
  // address: the only address left is `releases/latest`, which resolves to
  // whatever is newest — pair it with this notice's digest and Decky fetches one
  // release, verifies it against another, and refuses to unpack.
  const canInstall = state.pluginName !== "" && state.installUrl !== "";
  const gameRunning = isAnySessionActive();

  const handleUpdate = () => {
    // Nothing re-renders this card when a session opens — it reads the answer at
    // render and subscribes to nothing — so the disabled state can be stale by
    // the time of the press, and the press is where the refusal has to hold. It
    // says why rather than doing nothing: a request filed here puts Decky's own
    // dialog in front of the reader, which is no place to raise a warning about
    // a decision they have already made.
    if (isAnySessionActive()) {
      showToast(UPDATE_GAME_RUNNING_REASON);
      return;
    }
    setRequest("filed");
    // `install_plugin` files the request and emits the dialog event; it does not
    // wait for the user's confirmation, so this promise settles at once and says
    // only that Decky was ASKED. The outcome — confirmed, declined, installed —
    // reaches this card through nothing at all, which is why the button is not
    // gated on it: it goes down to stop a double press filing a second request
    // and comes back up on its own timer.
    requestPluginInstall({
      artifact: state.installUrl,
      name: state.pluginName,
      version: latestVersion,
      hash: state.digest,
    }).catch((e) => {
      logError(`Failed to hand the update to Decky's installer: ${e}`);
      setRequest("unavailable");
    });
  };

  const handleDismiss = () => {
    dismissUpdateForVersion(latestVersion).catch((e) => logError(`Failed to dismiss the update notice: ${e}`));
  };

  const showManualHint = !canInstall || request === "unavailable";
  const buttonDescription = gameRunning
    ? UPDATE_GAME_RUNNING_REASON
    : request === "filed"
      ? "Confirm the update in Decky's own dialog."
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
                : "This release cannot be installed from here, so it has to be done by hand."}
            </div>
            {showManualHint && (
              <div data-testid="update-manual-hint" style={{ color: "rgba(255, 255, 255, 0.7)", marginTop: "6px" }}>
                {request === "unavailable" ? "Decky's installer could not be reached. " : ""}
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
            disabled={gameRunning || request === "filed"}
            description={buttonDescription}
            onClick={handleUpdate}
          >
            {request === "filed" ? "Handed to Decky…" : "Update now"}
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
