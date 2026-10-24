/**
 * What the navigation watcher draws below the play bar of a RomM game page
 * (docs/architecture/desktop-dom-architecture.md).
 */

import { useEffect, type CSSProperties, type FC } from "react";
import { useGameDetail } from "../../utils/gameDetailStore";
import { useRomMetadata } from "../../utils/useRomMetadata";
import { steamsCover } from "../../utils/artworkThumbs";
import { useArtworkSlotsChanges } from "../../utils/artworkSlotsStore";
import { useAutoArtwork } from "../../utils/autoArtwork";
import { cancelArtworkApply } from "../../utils/artwork";
import { detach } from "../../utils/detach";
import { registerConnectionHeartbeat } from "../../utils/connectionHeartbeat";
import { useMigrationStatus } from "../../utils/migrationStore";
import { useMigrationRefresh } from "../../utils/migrationRefresh";
import { AboutDetails } from "./AboutDetails";
import { MigrationBlockedCard } from "./MigrationBlockedCard";
import { CARD_STYLE } from "./styles";

interface GameViewProps {
  appId: number;
}

const VIEW_STYLE: CSSProperties = {
  marginTop: "16px",
  marginBottom: "24px",
};

const COLUMNS_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "2fr 1fr",
  gap: "16px",
  alignItems: "start",
};

const COLUMN_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "16px",
  minWidth: 0,
};

export const GameView: FC<GameViewProps> = ({ appId }) => {
  const detail = useGameDetail(appId);

  useEffect(() => registerConnectionHeartbeat(), []);
  useEffect(
    () => () => {
      detach(cancelArtworkApply(appId));
    },
    [appId],
  );
  useAutoArtwork(appId, detail.romId, "Desktop auto-artwork error");
  useArtworkSlotsChanges(appId);
  const migration = useMigrationStatus();
  useMigrationRefresh(appId);

  const metadata = useRomMetadata(detail.romId);
  const title = appStore.GetAppOverviewByAppID(appId)?.display_name || detail.romName;

  if (migration.pending) {
    return (
      <div style={VIEW_STYLE}>
        <MigrationBlockedCard />
      </div>
    );
  }

  return (
    <div style={VIEW_STYLE}>
      <div style={COLUMNS_STYLE}>
        <div style={COLUMN_STYLE}>
          <div style={CARD_STYLE}>
            <AboutDetails
              title={title}
              platformName={detail.platformName}
              metadata={metadata}
              covers={steamsCover(appId, detail.romId)}
            />
          </div>
        </div>
        <div style={COLUMN_STYLE} />
      </div>
    </div>
  );
};
