/**
 * GameInfoTab — the GAME INFO pane of the RomM game detail panel: the
 * descriptive rows about the game, and the ROM File section underneath.
 *
 * Render only, from props — nothing here fetches. The panel streams state in as
 * its background reads land, so this pane is routinely open before the cover,
 * the installed-rom record and a stale metadata refresh arrive.
 *
 * CSS classes prefixed with `romm-panel-` are injected separately by
 * styleInjector.
 */

import { FC, useState, type ReactElement } from "react";
import type { InstalledRom, RomMetadata } from "../types";
import { steamsCover } from "../utils/artworkThumbs";
import { useArtworkSlotsChanges } from "../utils/artworkSlotsStore";
import { metadataRows, type MetadataRow } from "../utils/metadataRows";
import { infoRow, section } from "./panelSection";

interface GameInfoTabProps {
  /** The RomM game name (distinct from the Steam shortcut hero title, which can differ). */
  romName: string;
  /** Region / Languages of the active version (ADR-0021) — an empty list hides its row. */
  regions: string[];
  languages: string[];
  metadata: RomMetadata | null;
  platformName: string;
  /** RomM's cover, shown where Steam's own copy of the shortcut's cover does not load. */
  coverBase64: string | null;
  /** The shortcut whose cover Steam shows — the one this tab shows first (#2381 D18). */
  appId?: number;
  /** The ROM showing, whose ARTWORK tab read — where one is held — signs the cover's URLs. */
  romId?: number;
  installed: boolean;
  installedRom: InstalledRom | null;
}

/** A metadata row as this pane draws it: a list as tags, anything else as a labelled value. */
function metadataRow(row: MetadataRow): ReactElement {
  if (typeof row.value === "string") return infoRow(row.key, row.label, row.value);
  return (
    <div key={row.key} className="romm-panel-info-row">
      <span className="romm-panel-label">{row.label}</span>
      <div className="romm-panel-tags">
        {row.value.map((g) => (
          <span key={g} className="romm-panel-tag">
            {g}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Every descriptive row the pane shows, in display order. */
function gameInfoRows(props: GameInfoTabProps): ReactElement[] {
  const rows: ReactElement[] = [];

  if (props.romName) {
    rows.push(
      <div key="rom-name" className="romm-panel-rom-name">
        {props.romName}
      </div>,
    );
  }

  if (props.regions.length > 0) {
    rows.push(infoRow("regions", "Region", props.regions.join("/")));
  }
  if (props.languages.length > 0) {
    rows.push(infoRow("languages", "Languages", props.languages.join(", ")));
  }

  if (props.metadata?.summary) {
    rows.push(
      <div key="summary" className="romm-panel-summary">
        {props.metadata.summary}
      </div>,
    );
  }
  rows.push(...metadataRows(props.metadata, props.platformName).map(metadataRow));

  // "No metadata available" fires only when NO descriptive row was added (name,
  // region/languages, metadata, or platform) — a plain count of the rows above.
  if (rows.length === 0) {
    rows.push(
      <div key="no-meta" className="romm-panel-muted">
        No metadata available
      </div>,
    );
  }

  return rows;
}

/** The ROM File section — present only once the ROM is on disk. */
function romFileSection(installed: boolean, installedRom: InstalledRom | null): ReactElement | null {
  if (!installed || !installedRom) return null;
  return section("rom-file", "ROM File", infoRow("filename", "Filename", installedRom.file_name));
}

/** The cover Steam shows — the first of its URLs that loads — else RomM's cover. */
const Cover: FC<{ sources: string[] }> = ({ sources }) => {
  const [failed, setFailed] = useState(0);
  const src = sources[failed];
  if (!src) return null;
  return (
    <img
      alt=""
      src={src}
      onError={() => setFailed((n) => n + 1)}
      style={{ width: "120px", borderRadius: "4px", flexShrink: 0, objectFit: "cover" as const }}
    />
  );
};

export const GameInfoTab: FC<GameInfoTabProps> = (props) => {
  const rows = gameInfoRows(props);
  useArtworkSlotsChanges(props.appId ?? 0);
  const sources = [
    ...(props.appId === undefined ? [] : steamsCover(props.appId, props.romId ?? null)),
    ...(props.coverBase64 ? [`data:image/png;base64,${props.coverBase64}`] : []),
  ];

  const gameInfoSection = sources.length
    ? section(
        "game-info",
        null,
        <div key="game-info-row" style={{ display: "flex", gap: "16px", alignItems: "flex-start" }}>
          <Cover key={sources.join("|")} sources={sources} />
          <div key="details" style={{ flex: 1 }}>
            {rows}
          </div>
        </div>,
      )
    : section("game-info", null, ...rows);

  return (
    <div>
      {gameInfoSection}
      {romFileSection(props.installed, props.installedRom)}
    </div>
  );
};
