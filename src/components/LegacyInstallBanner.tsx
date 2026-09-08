import { FC } from "react";
import { PanelSection, PanelSectionRow } from "@decky/ui";
import { useLegacyInstallState } from "../utils/legacyInstallStore";
import { useSyncStats } from "../utils/syncStatsStore";

/** Headline of the QAM banner warning against removing the pre-rename install. */
export const LEGACY_INSTALL_TITLE = '"RomM Sync" is still installed, and your games need it.';

/**
 * Body text for the legacy-install banner. The second sentence is shown only
 * when the older install still holds the database, because that is the one
 * thing the user can see for themselves — an empty library — and would
 * otherwise read as data loss.
 */
export function legacyInstallMessage(dataStranded: boolean): string {
  const warning =
    "Every Steam shortcut launches through a file in that older plugin's folder, so removing it from Decky stops " +
    "your games from starting. Leave it in place — a future version will move everything over and clean it up for you.";
  if (!dataStranded) return warning;
  return (
    `${warning} Your library and settings are still in that older install too — this version starts empty until the ` +
    "move happens."
  );
}

interface LegacyInstallBannerProps {
  dataStranded: boolean;
}

/**
 * QAM PanelSection shown while the pre-rename plugin folder stands beside this
 * install. It carries no action: the condition ends when a future version moves
 * the data across and removes the folder, not when the user acknowledges it, so
 * there is nothing to dismiss and nowhere to jump to.
 */
export const LegacyInstallBanner: FC<LegacyInstallBannerProps> = ({ dataStranded }) => {
  return (
    <PanelSection>
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
            {"⚠️"} {LEGACY_INSTALL_TITLE}
          </div>
          <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.85)", lineHeight: 1.5 }}>
            {legacyInstallMessage(dataStranded)}
          </div>
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
};

/**
 * The notice as the panel renders it: the condition, the stranded-data join and
 * the banner in one element.
 *
 * Three places render it — Main, and both full-page states, which carry it
 * inside their own content because a user is most likely to remove the older
 * install exactly while this plugin looks broken. It reaches FOUR surfaces from
 * those three, because `VersionErrorCard` is itself rendered twice: by Main and
 * by the game detail page. The condition lives here rather than at the
 * three call sites, which would drift apart.
 *
 * `pending` alone decides whether the card appears. The stranded-data sentence
 * needs a second half that is not the notice's own — THIS install shows nothing
 * — and `stats` is null until the first read lands, which reads as "not known
 * to be empty": the sentence appears a beat later rather than claiming an
 * emptiness nobody established. Nothing the library read can answer ever takes
 * the launcher warning down.
 */
export const LegacyInstallNotice: FC = () => {
  const legacyInstall = useLegacyInstallState();
  const stats = useSyncStats();
  if (!legacyInstall.pending) return null;
  return <LegacyInstallBanner dataStranded={legacyInstall.legacyDataPresent && stats?.roms === 0} />;
};
