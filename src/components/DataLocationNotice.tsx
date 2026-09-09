import { FC } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, Focusable } from "@decky/ui";
import { useDataLocationState } from "../utils/dataLocationStore";
import { showDataLocationModal } from "./DataLocationModal";

/** Headline of the notice raised when two older installs both hold a library. */
export const DATA_LOCATION_CHOICE_TITLE = "Tender found two copies of your library";

/** Headline of the notice raised when a start's copy did not finish. */
export const DATA_LOCATION_FAILED_TITLE = "Tender could not move your data";

const CHOICE_BODY =
  "Two older installs of this plugin both hold a library, so Tender has not moved anything and is still using the " +
  "copy it was given. Pick the one to keep — nothing is deleted either way.";

/**
 * Body text for the failed variant. The reason comes from the backend verbatim
 * because it is the only thing that says what to fix — a disk that is full and a
 * folder that cannot be written need different answers from the user.
 *
 * It says "the folders it could not move" rather than naming all of them: the
 * settings and the library are moved independently, so one of the two can be at
 * its new home while the other is not.
 */
export function dataLocationFailedMessage(reason: string | null): string {
  const tail =
    "Nothing was lost — Tender is still reading and writing the folders it could not move, and will try again the " +
    "next time it starts.";
  return reason ? `${reason}. ${tail}` : tail;
}

/**
 * What a card whose condition carries no action does when it is activated:
 * nothing. The handler is not decoration — a bare `Focusable` is a container
 * and not a focus stop, so without one the card is unreachable with a
 * controller, and a QAM region scrolls only by moving focus into it. The
 * failure variant sits at the bottom of the status block, which is exactly
 * where "unreachable" also means "never seen".
 */
const REACHABLE_ONLY = () => {};

interface DataLocationCardProps {
  title: string;
  body: string;
  /** What A does on the card. See {@link REACHABLE_ONLY} for the actionless case. */
  onActivate: () => void;
}

const DataLocationCard: FC<DataLocationCardProps> = ({ title, body, onActivate }) => (
  <PanelSectionRow>
    <Focusable onActivate={onActivate}>
      <div
        data-testid="data-location-notice"
        style={{
          padding: "8px 12px",
          backgroundColor: "rgba(212, 167, 44, 0.15)",
          borderLeft: "3px solid #d4a72c",
          borderRadius: "4px",
          fontSize: "12px",
        }}
      >
        <div style={{ fontWeight: "bold", color: "#d4a72c", marginBottom: "4px" }}>
          {"⚠️"} {title}
        </div>
        <div style={{ color: "rgba(255, 255, 255, 0.7)" }}>{body}</div>
      </div>
    </Focusable>
  </PanelSectionRow>
);

/**
 * Main's notice for the plugin's own data location.
 *
 * Two conditions, one card. The **choice** is answered once and for all, so its
 * home is the modal the button opens rather than a page — there is nothing to
 * come back to once the user has picked. The **failure** has no home at all: the
 * next start retries by itself, and there is no action here that would help.
 *
 * Neither carries a Dismiss. Both end by a start completing the move, not by
 * being acknowledged, so a dismissed card would only hide something still true.
 */
export const DataLocationNotice: FC = () => {
  const state = useDataLocationState();
  if (!state.pending) return null;
  if (state.kind === "failed") {
    return (
      <DataLocationCard
        title={DATA_LOCATION_FAILED_TITLE}
        body={dataLocationFailedMessage(state.message)}
        onActivate={REACHABLE_ONLY}
      />
    );
  }
  return (
    <>
      {/* A on the card opens the same modal the button below does, so reaching
          the card and reaching the action are one press rather than two. */}
      <DataLocationCard title={DATA_LOCATION_CHOICE_TITLE} body={CHOICE_BODY} onActivate={showDataLocationModal} />
      <PanelSectionRow>
        <ButtonItem layout="below" bottomSeparator="none" onClick={showDataLocationModal}>
          Choose a copy
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
};

/**
 * The same notice as a section of its own, for a page with no status block to
 * sit in — the RetroDECK-migration full-page state, which the panel cannot be
 * left without a user action, so a condition invisible there is invisible for
 * however long that takes.
 *
 * The pending check is repeated rather than delegated to the element inside: an
 * always-rendered `PanelSection` costs a padded empty row on a page that is
 * already nothing but an explanation. Both live here so the two cannot drift.
 */
export const DataLocationNoticeSection: FC = () => {
  const state = useDataLocationState();
  if (!state.pending) return null;
  return (
    <PanelSection>
      <DataLocationNotice />
    </PanelSection>
  );
};
