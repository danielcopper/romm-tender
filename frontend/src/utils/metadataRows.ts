/**
 * The rows a game page shows from a RomM metadata record and the platform name,
 * as labels and values: which fields show, under what label, and joined how.
 * Drawing them is the caller's.
 */

import type { RomMetadata } from "../types";
import { formatReleaseDate } from "./formatters";

/** One labelled row. A list value is answered as the list rather than joined. */
export interface MetadataRow {
  key: string;
  label: string;
  value: string | readonly string[];
}

/**
 * The rows for *meta* and *platformName*, in display order — the platform alone
 * where there is no record. The summary is no row; drawing it is the caller's.
 */
export function metadataRows(meta: RomMetadata | null, platformName: string): MetadataRow[] {
  const rows: MetadataRow[] = [];

  if (platformName) {
    rows.push({ key: "platform", label: "Platform", value: platformName });
  }
  if (!meta) return rows;

  if (meta.companies.length > 0) {
    rows.push({ key: "companies", label: "Companies", value: meta.companies.join(", ") });
  }

  if (meta.genres.length > 0) {
    rows.push({ key: "genres", label: "Genres", value: meta.genres });
  }

  const releaseDate = formatReleaseDate(meta.first_release_date);
  if (releaseDate) {
    rows.push({ key: "release-date", label: "Release Date", value: releaseDate });
  }

  if (meta.game_modes.length > 0) {
    rows.push({ key: "game-modes", label: "Game Modes", value: meta.game_modes.join(", ") });
  }

  if (meta.player_count) {
    rows.push({ key: "players", label: "Players", value: meta.player_count });
  }

  if (meta.average_rating != null && meta.average_rating > 0) {
    rows.push({ key: "rating", label: "Metadata rating", value: `${Math.round(meta.average_rating)}%` });
  }

  return rows;
}
