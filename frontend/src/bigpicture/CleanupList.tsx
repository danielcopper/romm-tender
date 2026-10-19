import { useState, FC, type CSSProperties, type ReactNode } from "react";
import { DialogButton, Toggle } from "@decky/ui";
import { scrollNearestToBottom } from "../utils/scrollHelpers";
import type { CleanupGame, CleanupVersion, GameOutcome, KeptReason } from "./cleanupPlan";
import {
  AMBER,
  FOCUSED_ROW,
  GREEN,
  MUTED,
  NOTE_FONT,
  PaneTableHeader,
  PaneTableRow,
  RED,
  ROW_BUTTON,
  SECONDARY_FONT,
  type TableCell,
  type TableRegister,
  UNFOCUSED_ROW,
  useFocusWithin,
} from "./layout/pane";

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index++;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
}

/** Game and versions, What happens, Downloaded, Back up file. */
const COLUMNS = "minmax(0, 3fr) minmax(0, 2fr) 64px 72px";

/** The pane's register without its gutter: the dialog body is padded already. */
const REGISTER: TableRegister = { rowPadding: "4px 0", headerPadding: "0 0 4px", rule: true };

const SECONDARY: CSSProperties = { color: MUTED, fontSize: SECONDARY_FONT };

const NUMERIC: CSSProperties = { ...SECONDARY, fontVariantNumeric: "tabular-nums", textAlign: "right" };

/** Wraps rather than clips: docs/architecture/qam-panel.md, "The removed-games review". */
const LINES: CSSProperties = { minWidth: 0, overflowWrap: "anywhere" };

const OUTCOME: CSSProperties = { fontSize: SECONDARY_FONT, fontWeight: 600 };
const OUTCOME_WHY: CSSProperties = { color: MUTED, fontSize: NOTE_FONT };

const VERSION_INSET = "12px";

const GAME_NAME_FONT = "13px";

const KEPT_BLUE = "#66c0f4";
const REMOVED_LIGHT = "#c7d5e0";

const GAME_OUTCOME: Record<GameOutcome, { text: string; color: string }> = {
  stays: { text: "Game stays", color: GREEN },
  nothing_removed: { text: "Nothing removed", color: KEPT_BLUE },
  removed_with_shortcut: { text: "Game removed, with its shortcut", color: RED },
  removed: { text: "Game removed", color: REMOVED_LIGHT },
};

const KEPT_REASON: Record<KeptReason, string> = {
  shortcut: "Switch to another version first, then this one can be removed.",
  remove_rows_off: "“Remove gone versions” is off.",
  remove_games_off: "“Remove games that are gone entirely” is off.",
  only_chosen: "Only the version you chose is removed here.",
};

const REPLACED_WHERE = "Replaced on RomM by the same file under a new ID";
const REPLACED_SAVES = "Its save files stay; the new entry uses them.";

/** What the list's rows need beyond the plan: who backs up which file, and whether a backup can be switched. */
export interface CleanupListBackup {
  recovery: boolean;
  runInFlight: boolean;
  isBackedUp: (romId: number) => boolean;
  onBackUp: (romId: number, checked: boolean) => void;
}

function gameLine(game: CleanupGame): string {
  const gone = game.goneCount === game.size ? "every version gone" : `${game.goneCount} of ${game.size} versions gone`;
  return `${game.platformSlug} · ${gone}`;
}

function versionName(version: CleanupVersion): string {
  const { item } = version;
  return item.fs_name || item.name || `ROM ${item.rom_id}`;
}

function whereLine(version: CleanupVersion): string {
  const { item } = version;
  let where = "On RomM";
  if (version.replaced) where = REPLACED_WHERE;
  else if (item.gone_from_romm) where = "Gone from RomM";
  return item.bound ? `${where} · your shortcut` : where;
}

/** The outcome in words, and the lines under it that say why or what goes with it. */
function whatHappens(
  version: CleanupVersion,
  backup: CleanupListBackup,
): { text: string; color: string; why: string[] } {
  const { item, outcome } = version;
  if (outcome.kind === "stays") {
    return { text: "Stays", color: GREEN, why: item.installed ? ["Its files stay on this device."] : [] };
  }
  if (outcome.kind === "kept") return { text: "Kept", color: KEPT_BLUE, why: [KEPT_REASON[outcome.reason]] };
  const why = version.replaced ? [REPLACED_SAVES] : [];
  if (!version.deletesFile) return { text: "Removed", color: REMOVED_LIGHT, why };
  if (backup.isBackedUp(item.rom_id)) {
    return { text: "Removed", color: REMOVED_LIGHT, why: [...why, "A copy of its file goes into the backup first."] };
  }
  return {
    text: "Removed, file deleted",
    color: RED,
    why: backup.recovery ? [...why, "Switch on “Back up file” to keep a copy."] : why,
  };
}

function downloadedCell(version: CleanupVersion): TableCell {
  const { item } = version;
  if (!item.installed) return { content: "—", title: "No game files on this device", style: NUMERIC };
  if (item.installed_bytes === null) return { content: "unknown", title: "Size unavailable", style: NUMERIC };
  return { content: formatBytes(item.installed_bytes), style: NUMERIC };
}

const GameRow: FC<{ game: CleanupGame }> = ({ game }) => {
  const outcome = GAME_OUTCOME[game.outcome];
  return (
    <PaneTableRow
      columns={COLUMNS}
      register={REGISTER}
      testId={`cleanup-game-${game.groupId}`}
      style={{ marginTop: "6px" }}
      cells={[
        {
          content: (
            <>
              <div style={{ fontSize: GAME_NAME_FONT, fontWeight: 700 }}>{game.name}</div>
              <div style={SECONDARY}>{gameLine(game)}</div>
            </>
          ),
          clip: false,
          style: LINES,
        },
        {
          content: outcome.text,
          clip: false,
          style: { ...LINES, ...OUTCOME, gridColumn: "2 / span 3", color: outcome.color },
        },
      ]}
    />
  );
};

const VersionRow: FC<{ version: CleanupVersion; backup: CleanupListBackup }> = ({ version, backup }) => {
  const { item } = version;
  const happens = whatHappens(version, backup);
  const { focused: switchFocused, handlers: switchFocus } = useFocusWithin();
  const backUp: TableCell = version.deletesFile
    ? {
        content: (
          // Not a ToggleField: its Field's box and empty label take more width
          // than the column holds.
          <Toggle
            value={backup.isBackedUp(item.rom_id)}
            disabled={backup.runInFlight || !backup.recovery}
            onChange={(checked) => backup.onBackUp(item.rom_id, checked)}
          />
        ),
        // A control draws its focus ring outside its own box.
        clip: false,
        style: { display: "flex", justifyContent: "flex-end" },
      }
    : { content: "—", style: NUMERIC };
  const row = (
    <PaneTableRow
      columns={COLUMNS}
      register={REGISTER}
      testId={`cleanup-row-${item.rom_id}`}
      // A row with a Back up file switch is reached through it.
      focusStop={!version.deletesFile}
      {...(version.deletesFile ? { style: switchFocused ? FOCUSED_ROW : UNFOCUSED_ROW } : {})}
      cells={[
        {
          content: (
            <div style={{ paddingLeft: VERSION_INSET }}>
              <div style={{ fontSize: SECONDARY_FONT }}>{versionName(version)}</div>
              <div style={SECONDARY}>{whereLine(version)}</div>
            </div>
          ),
          clip: false,
          style: LINES,
        },
        {
          content: (
            <>
              <div style={{ ...OUTCOME, color: happens.color }}>{happens.text}</div>
              {happens.why.map((line) => (
                <div key={line} style={OUTCOME_WHY}>
                  {line}
                </div>
              ))}
            </>
          ),
          clip: false,
          style: { minWidth: 0 },
        },
        downloadedCell(version),
        backUp,
      ]}
    >
      {item.warning && <div style={{ ...SECONDARY, color: AMBER, paddingLeft: VERSION_INSET }}>{item.warning}</div>}
    </PaneTableRow>
  );
  if (!version.deletesFile) return row;
  return <div {...switchFocus}>{row}</div>;
};

const FoldedRow: FC<{ count: number; onShow: () => void }> = ({ count, onShow }) => (
  <div style={{ padding: REGISTER.rowPadding, paddingLeft: VERSION_INSET }}>
    <DialogButton style={{ ...ROW_BUTTON, textAlign: "left" }} onClick={onShow}>
      {count} more versions, all on RomM · Show
    </DialogButton>
  </div>
);

/**
 * The cleanup's list, grouped by game: a row per game saying what happens to it,
 * then its versions. The version a shortcut is on is always shown; the others on
 * RomM fold into one row when there are more than two of them.
 */
export const CleanupList: FC<{ games: readonly CleanupGame[]; backup: CleanupListBackup }> = ({ games, backup }) => {
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  const rows: { key: string; node: ReactNode }[] = [];
  for (const game of games) {
    rows.push({ key: `game-${game.groupId}`, node: <GameRow game={game} /> });
    const folded = game.foldable > 0 && !opened.has(game.groupId);
    const shown = folded ? game.versions.slice(0, game.versions.length - game.foldable) : game.versions;
    for (const version of shown) {
      rows.push({
        key: `rom-${version.item.rom_id}`,
        node: <VersionRow version={version} backup={backup} />,
      });
    }
    if (folded) {
      rows.push({
        key: `fold-${game.groupId}`,
        node: (
          <FoldedRow count={game.foldable} onShow={() => setOpened((current) => new Set(current).add(game.groupId))} />
        ),
      });
    }
  }
  return (
    <div data-testid="cleanup-table" style={{ marginTop: "14px" }}>
      <PaneTableHeader
        columns={COLUMNS}
        register={REGISTER}
        testId="cleanup-header"
        cells={[
          "Game and versions",
          "What happens",
          { content: "Downloaded", style: { textAlign: "right" } },
          { content: "Back up file", style: { textAlign: "right" } },
        ]}
      />
      {/* Steam scrolls only far enough to reveal the focused control, so
          reaching the last row brings up what sits under it — its own lines,
          and the note after the list. */}
      {rows.map((row, index) => (
        <div key={row.key} {...(index === rows.length - 1 ? { onFocus: scrollNearestToBottom } : {})}>
          {row.node}
        </div>
      ))}
      <div style={{ ...SECONDARY, padding: "8px 0" }}>
        Downloaded: the game files of that version on this device. A file is deleted only where the row says so.
      </div>
    </div>
  );
};
