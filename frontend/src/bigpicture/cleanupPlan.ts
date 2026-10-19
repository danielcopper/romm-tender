/**
 * What a removed-game cleanup would do to each game and version, predicted from
 * the preview's rows and the dialog's options.
 *
 * The run's own check still decides and never removes a version this does not
 * predict removed (docs/architecture/removed-game-cleanup.md, "Discovery").
 */
import type { PrunePreviewItem } from "../api/backend";

export interface CleanupOptions {
  removeRows: boolean;
  removeDeadGames: boolean;
}

export type KeptReason = "shortcut" | "remove_rows_off" | "remove_games_off" | "only_chosen";

export type VersionOutcome = { kind: "stays" } | { kind: "kept"; reason: KeptReason } | { kind: "removed" };

export interface CleanupVersion {
  item: PrunePreviewItem;
  outcome: VersionOutcome;
  /** An old entry RomM replaced in a rescan: a version on RomM holds the same file name. */
  replaced: boolean;
  /** Removed with downloaded files, which go unless the row's backup is on. */
  deletesFile: boolean;
}

export type GameOutcome = "stays" | "nothing_removed" | "removed_with_shortcut" | "removed";

export interface CleanupGame {
  groupId: string;
  name: string;
  platformSlug: string;
  size: number;
  goneCount: number;
  outcome: GameOutcome;
  /** The gone versions, then the version a shortcut is on, then the others on RomM. */
  versions: CleanupVersion[];
  /** How many of the trailing on-RomM versions fold into one row until opened. */
  foldable: number;
}

/** More than this many other versions on RomM fold into one row. */
const MAX_UNFOLDED_ON_ROMM = 2;

function groupsInOrder(items: readonly PrunePreviewItem[]): PrunePreviewItem[][] {
  const groups = new Map<string, PrunePreviewItem[]>();
  for (const item of items) {
    const group = groups.get(item.group_id);
    if (group) group.push(item);
    else groups.set(item.group_id, [item]);
  }
  return [...groups.values()];
}

function versionOutcome(item: PrunePreviewItem, wholeGame: boolean, options: CleanupOptions): VersionOutcome {
  if (!item.gone_from_romm) return { kind: "stays" };
  if (wholeGame) return options.removeDeadGames ? { kind: "removed" } : { kind: "kept", reason: "remove_games_off" };
  // The shortcut's reason wins over every other: nothing else in this dialog would remove this version.
  if (item.bound) return { kind: "kept", reason: "shortcut" };
  if (!item.candidate) return { kind: "kept", reason: "only_chosen" };
  if (!options.removeRows) return { kind: "kept", reason: "remove_rows_off" };
  return { kind: "removed" };
}

function isReplaced(item: PrunePreviewItem, group: readonly PrunePreviewItem[]): boolean {
  return (
    item.gone_from_romm &&
    item.fs_name_no_ext !== "" &&
    group.some((other) => !other.gone_from_romm && other.fs_name_no_ext === item.fs_name_no_ext)
  );
}

/** The game's name: the version a shortcut is on, else RomM's main sibling, else the first row. */
function gameName(group: readonly PrunePreviewItem[]): string {
  const named = group.find((item) => item.bound) ?? group.find((item) => item.is_main_sibling) ?? group[0]!;
  return named.name || named.fs_name || `ROM ${named.rom_id}`;
}

function planGame(group: PrunePreviewItem[], options: CleanupOptions): CleanupGame {
  const first = group[0]!;
  const size = first.group_size;
  const goneCount = group.filter((item) => item.gone_from_romm).length;
  // A group whose pages have not all arrived is never taken for a whole game:
  // a version still to come may be on RomM.
  const wholeGame = group.length === size && group.every((item) => item.gone_from_romm && item.candidate);
  const versions = group.map((item): CleanupVersion => {
    const outcome = versionOutcome(item, wholeGame, options);
    return {
      item,
      outcome,
      replaced: isReplaced(item, group),
      deletesFile: outcome.kind === "removed" && item.installed,
    };
  });
  const gone = versions.filter((version) => version.item.gone_from_romm);
  const onRomm = versions.filter((version) => !version.item.gone_from_romm);
  const shortcutOnRomm = onRomm.filter((version) => version.item.bound);
  const othersOnRomm = onRomm.filter((version) => !version.item.bound);
  const anyRemoved = versions.some((version) => version.outcome.kind === "removed");
  let outcome: GameOutcome = anyRemoved ? "stays" : "nothing_removed";
  if (wholeGame && anyRemoved) outcome = first.bound_count > 0 ? "removed_with_shortcut" : "removed";
  return {
    groupId: first.group_id,
    name: gameName(group),
    platformSlug: first.platform_slug,
    size,
    goneCount,
    outcome,
    versions: [...gone, ...shortcutOnRomm, ...othersOnRomm],
    foldable: othersOnRomm.length > MAX_UNFOLDED_ON_ROMM ? othersOnRomm.length : 0,
  };
}

/** Every game the preview's rows name, in the order its first row arrived, with what a run would do to it. */
export function planCleanup(items: readonly PrunePreviewItem[], options: CleanupOptions): CleanupGame[] {
  return groupsInOrder(items).map((group) => planGame(group, options));
}

export interface CleanupCounts {
  versionsRemoved: number;
  gamesRemovedWithShortcut: number;
  /** Downloaded files deleted with no copy in the backup. */
  filesDeleted: number;
  keptForShortcut: number;
}

export function countCleanup(games: readonly CleanupGame[], backedUp: (romId: number) => boolean): CleanupCounts {
  const versions = games.flatMap((game) => game.versions);
  return {
    versionsRemoved: versions.filter((version) => version.outcome.kind === "removed").length,
    gamesRemovedWithShortcut: games.filter((game) => game.outcome === "removed_with_shortcut").length,
    filesDeleted: versions.filter((version) => version.deletesFile && !backedUp(version.item.rom_id)).length,
    keptForShortcut: versions.filter(
      (version) => version.outcome.kind === "kept" && version.outcome.reason === "shortcut",
    ).length,
  };
}

/** The versions whose downloaded files the run would delete — the only rows a file backup applies to. */
export function versionsDeletingFiles(games: readonly CleanupGame[]): CleanupVersion[] {
  return games.flatMap((game) => game.versions.filter((version) => version.deletesFile));
}
