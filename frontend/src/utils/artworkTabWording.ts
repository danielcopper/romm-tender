/**
 * The words of the game page's ARTWORK tab and its picker: each slot's name, where its image comes from, its state,
 * and the tab's SteamGridDB game line (#2381 D2, D3).
 */

import type { ArtworkSlotSource, ArtworkSlotState, ArtworkSlots, ArtworkTabSlot } from "../api/backend";

export const SLOT_NAMES: Readonly<Record<ArtworkTabSlot, string>> = {
  cover: "Cover",
  grid: "Wide grid",
  hero: "Hero",
  logo: "Logo",
  icon: "Icon",
};

export const SOURCE_LINES: Readonly<Record<ArtworkSlotSource, string>> = {
  romm: "RomM cover — kept current when RomM changes it",
  chosen: "SteamGridDB — chosen by you",
  tender: "SteamGridDB — Tender's pick",
  steam: "Set through Steam's “Set custom artwork”",
  needs_game: "Needs a SteamGridDB game",
  none: "SteamGridDB has no image for this slot",
  empty: "Not set yet",
};

/** An icon set in Steam: a path the shortcut's properties name, not Steam's grid folder. */
export const ICON_SET_IN_STEAM = "Set in the shortcut's properties";

/** The source line of *slot*, whose source is *source*. */
export function sourceLine(slot: ArtworkTabSlot, source: ArtworkSlotSource): string {
  return slot === "icon" && source === "steam" ? ICON_SET_IN_STEAM : SOURCE_LINES[source];
}

/** What the tab says where no SteamGridDB API key is configured, so no image can be chosen. */
export const NO_API_KEY = "Add a SteamGridDB API key in Settings to choose images.";

export const STATE_CHIPS: Readonly<Record<ArtworkSlotState, string>> = {
  automatic: "Automatic",
  chosen: "Chosen by you",
  set_in_steam: "Set in Steam",
};

/** The picker's first tile, which sets the slot back to automatic. */
export function automaticTileLabel(slot: ArtworkTabSlot): string {
  return slot === "cover"
    ? "Automatic — RomM cover, follows RomM when it changes"
    : "Automatic — Tender picks by its own rule";
}

/**
 * Why the cover's picker offers only "Automatic" (#2381 D19): without a key or a SteamGridDB game no SteamGridDB cover
 * can be listed, and RomM's cover needs neither. `null` where SteamGridDB's covers can be listed.
 */
export function coverOnlyAutomatic(slots: Pick<ArtworkSlots, "has_api_key" | "game">): string | null {
  if (!slots.has_api_key) return "A SteamGridDB API key is needed for its covers.";
  if (slots.game.sgdb_id === null) return "Choose a SteamGridDB game to see its covers.";
  return null;
}

/** The sentence the picker asks before it replaces an image set through Steam (#2381 D13). */
export const REPLACE_SET_IN_STEAM = "This image was set through Steam. Replacing it removes it; Tender keeps no copy.";

/** The sentence the game picker says before a change of game discards the user's chosen images (#2381 D12). */
export const GAME_CHANGE_DISCARDS_CHOICES =
  "Changing the game sets every image you chose for this game back to automatic; an image set through Steam stays.";

/** Where Refresh Artwork sends the user while the ROM has no SteamGridDB game (#2381 D3). */
export const CHOOSE_GAME_FIRST = "Choose a SteamGridDB game in the ARTWORK tab first.";

/** The tab's game line: what it says, an explanation under it, and the button it offers, if any. */
export interface GameLine {
  text: string;
  explanation: string | null;
  button: "Choose game…" | "Change game…" | null;
  /** Highlighted: the ROM has no SteamGridDB game yet. */
  notice: boolean;
}

export function gameLine(game: ArtworkSlots["game"]): GameLine {
  const name = game.name ?? (game.sgdb_id === null ? "" : `#${game.sgdb_id}`);
  if (game.source === "romm") {
    return { text: `SteamGridDB game: ${name} (from RomM)`, explanation: null, button: null, notice: false };
  }
  if (game.source === "chosen") {
    return {
      text: `SteamGridDB game: ${name} (chosen by you)`,
      explanation:
        "RomM has no SteamGridDB entry for this game, so you chose this one. If RomM gets one later, RomM's entry is used.",
      button: "Change game…",
      notice: false,
    };
  }
  return {
    text: "No SteamGridDB game yet",
    explanation: "RomM has no SteamGridDB entry for this game. Choose the game to see its artwork.",
    button: "Choose game…",
    notice: true,
  };
}

/** A candidate's size, as the picker labels each tile; empty where SteamGridDB states none. */
export function sizeLabel(width: number | null, height: number | null): string {
  return width !== null && height !== null ? `${width}×${height}` : "";
}
