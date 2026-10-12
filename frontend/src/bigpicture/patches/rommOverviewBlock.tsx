import type { ErrorInfo, ReactElement } from "react";
import { RomMPlaySection } from "../RomMPlaySection";
import { RomMGameInfoPanel } from "../RomMGameInfoPanel";
import { PanelErrorBoundary } from "../../shared/PanelErrorBoundary";
import { debugLog } from "../../api/backend";
import { detach } from "../../utils/detach";
import { DISPLAY_NAME } from "../../utils/toast";

/** The key the game-page patch tells its own block apart by, so a re-render does not splice in a second one. */
export const ROMM_BLOCK_KEY = "romm-play-section";

function failureLine(): ReactElement {
  return (
    <div style={{ fontSize: "12px", opacity: 0.6, padding: "12px 0" }}>
      {`${DISPLAY_NAME} could not show this game's section.`}
    </div>
  );
}

function logFailure(failure: string, info: ErrorInfo): void {
  detach(
    debugLog(
      `gameDetailPatch: ${DISPLAY_NAME}'s section threw while rendering: ${failure}${info.componentStack ?? ""}`,
    ),
  );
}

/**
 * Tender's block on a RomM shortcut's game page, in the place of Steam's own
 * overview panel. `className` is Steam's class for that panel; the boundary sits
 * inside the wrapper so a failure keeps the wrapper's place in Steam's layout.
 */
export function rommOverviewBlock(appId: number, className: string): ReactElement {
  return (
    <div key={ROMM_BLOCK_KEY} className={className} data-romm="true">
      <PanelErrorBoundary fallback={failureLine} onCatch={logFailure}>
        <RomMPlaySection appId={appId} />
        <RomMGameInfoPanel appId={appId} />
      </PanelErrorBoundary>
    </div>
  );
}
