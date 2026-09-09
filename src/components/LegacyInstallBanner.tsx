import { FC } from "react";
import { PanelSection, PanelSectionRow, ButtonItem } from "@decky/ui";
import { useLegacyInstallState } from "../utils/legacyInstallStore";
import { dismissLegacyRemovalNotice, useLauncherState } from "../utils/launcherStore";
import { useSyncStats } from "../utils/syncStatsStore";

/** Headline while the older install still owns the file the games launch through. */
export const LEGACY_INSTALL_TITLE = '"RomM Sync" is still installed, and your games need it.';

/** Headline once nothing launches through it but the user's library is still inside it. */
export const LEGACY_DATA_TITLE = '"RomM Sync" still holds your library.';

/** Headline once nothing in Tender depends on the older install any more. */
export const LEGACY_REMOVABLE_TITLE = '"RomM Sync" can be removed now.';

/**
 * What the card says, and whether it may be dismissed.
 *
 * Three statements, in the order the conditions clear. `relocated` is the
 * frontend's own answer — every shortcut of ours now names the launcher's home
 * outside the plugin folder — and `dataStranded` is the older install holding a
 * database while this one shows nothing.
 *
 * Only the last is dismissible. Keeping the older install is a legitimate
 * choice and must not cost a standing warning; the other two describe something
 * the user would lose by acting, and nothing about them is optional.
 */
export interface LegacyInstallStatement {
  title: string;
  body: string;
  dismissible: boolean;
}

export function legacyInstallStatement(relocated: boolean, dataStranded: boolean): LegacyInstallStatement {
  if (!relocated) {
    const launcher =
      "Every Steam shortcut launches through a file in that older plugin's folder, so removing it from Decky stops " +
      "your games from starting. Leave it in place until Tender has moved them onto its own copy of that file — it " +
      "tries at every start, so restarting Steam is usually all it takes.";
    return {
      title: LEGACY_INSTALL_TITLE,
      body: dataStranded
        ? `${launcher} Your library and settings are still in that older install too — this version starts empty ` +
          "until the move happens."
        : launcher,
      dismissible: false,
    };
  }
  if (dataStranded) {
    return {
      title: LEGACY_DATA_TITLE,
      body:
        "Your games no longer launch through it, so it is not in the way any more — but your library and settings " +
        "are still in that older install, and this version starts empty until the move happens. Leave it in place " +
        "until then.",
      dismissible: false,
    };
  }
  return {
    title: LEGACY_REMOVABLE_TITLE,
    body:
      "Your games no longer launch through it — Tender keeps its own copy of the file they need, outside any plugin " +
      "folder. Nothing here depends on the older install any more, so you can remove it from Decky's settings, " +
      "under Plugins. Keeping it is fine too; it costs disk space and nothing else.",
    dismissible: true,
  };
}

interface LegacyInstallBannerProps {
  dataStranded: boolean;
  relocated: boolean;
  onDismiss?: () => void;
}

/**
 * QAM PanelSection shown while the pre-rename plugin folder stands beside this
 * install. The condition ends when the folder does, not when the card is
 * acknowledged — so the only thing Dismiss ends is the one statement the user
 * is free to ignore, and the card carries no button in the other two.
 */
export const LegacyInstallBanner: FC<LegacyInstallBannerProps> = ({ dataStranded, relocated, onDismiss }) => {
  const statement = legacyInstallStatement(relocated, dataStranded);
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
            {"⚠️"} {statement.title}
          </div>
          <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.85)", lineHeight: 1.5 }}>{statement.body}</div>
        </div>
      </PanelSectionRow>
      {statement.dismissible && onDismiss ? (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onDismiss}>
            Dismiss
          </ButtonItem>
        </PanelSectionRow>
      ) : null}
    </PanelSection>
  );
};

/**
 * The notice as the panel renders it: the condition, the stranded-data join,
 * the relocation answer and the banner in one element.
 *
 * Three places render it — Main, and both full-page states, which carry it
 * inside their own content because a user is most likely to remove the older
 * install exactly while this plugin looks broken. It reaches FOUR surfaces from
 * those three, because `VersionErrorCard` is itself rendered twice: by Main and
 * by the game detail page. The condition lives here rather than at the
 * three call sites, which would drift apart.
 *
 * `pending` alone decides whether the card appears at all. The stranded-data
 * half needs a second half that is not the notice's own — THIS install shows
 * nothing — and `stats` is null until the first read lands, which reads as "not
 * known to be empty": the sentence appears a beat later rather than claiming an
 * emptiness nobody established. Nothing the library read can answer ever takes
 * the launcher warning down.
 *
 * A dismissal ends only the removable statement, so a card that later has
 * something un-optional to say still says it.
 */
export const LegacyInstallNotice: FC = () => {
  const legacyInstall = useLegacyInstallState();
  const launcher = useLauncherState();
  const stats = useSyncStats();
  if (!legacyInstall.pending) return null;
  const dataStranded = legacyInstall.legacyDataPresent && stats?.roms === 0;
  if (launcher.removalDismissed && legacyInstallStatement(launcher.relocated, dataStranded).dismissible) return null;
  return (
    <LegacyInstallBanner
      dataStranded={dataStranded}
      relocated={launcher.relocated}
      onDismiss={dismissLegacyRemovalNotice}
    />
  );
};
