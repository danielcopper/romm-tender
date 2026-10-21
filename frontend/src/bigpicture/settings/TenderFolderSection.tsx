/**
 * Tender folder — where Tender keeps its own files: a status line naming the
 * place in use, its state and its folder; one tile per place, two to a row —
 * the internal storage, tagged Default, and each writable drive by its kind,
 * tagged with its name — the place in use outlined; and "Another folder…". Choosing switches
 * the place and nothing else. The parent owns the folder's state and the
 * changes a tile makes; "Another folder…" opens its window, which checks and
 * chooses a typed place through the backend itself and hands the parent the
 * state it was answered with.
 *
 * The tiles and the "Another folder…" row are `Focusable` stops that draw
 * their own look rather than `DialogButton`s, whose focused state Steam paints
 * white. The status line is no stop of its own: it sits above the first tile,
 * and the detail column's `ScrollRegion` brings it into view when focus reaches
 * that tile (docs/architecture/qam-panel.md, "Two widths": content outside the
 * focusable rows).
 */

import { CSSProperties, FC, Fragment, ReactNode } from "react";
import { Focusable, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import type { TenderFolderState } from "../../types";
import type { TenderFolderView } from "../useTenderFolder";
import {
  ANOTHER_FOLDER,
  ANOTHER_FOLDER_LINE,
  DEFAULT_TAG,
  INTERNAL_FOLDER,
  INTERNAL_KIND,
  IN_USE_BADGE,
  NOT_CONNECTED,
  NO_DRIVE_NOTE,
  TENDER_FOLDER_SECTION,
  conditionPill,
  driveInUse,
  driveKind,
  freeLine,
  inUseLabel,
} from "../../utils/tenderFolderWording";
import { AMBER, GREEN, MUTED, SELECTION_ACCENT, VIOLET, useFocusWithin } from "../layout/pane";
import { WarningCard } from "../WarningCard";
import { checkTenderPlace, chooseTenderPlace } from "../../api/backend";
import { AnotherFolderModal } from "./AnotherFolderModal";

// The section's own palette, a tile's fill at rest and under focus, and the
// line under the status line.
const TILE = "#262d37";
const TILE_FOCUSED = "#323a46";
const TILE_IN_USE = "#24364a";
const DIVIDER = "#2a323d";
const TEXT = "#dfe3e8";

// The tag under a tile's kind: green for the default place, violet for a
// drive's name — neither blue, which the In use badge and outline are. Each
// fill is its colour at about a fifth of its strength.
const TAG_DEFAULT = { color: GREEN, background: `${GREEN}2e` } as const;
const TAG_DRIVE = { color: VIOLET, background: `${VIOLET}33` } as const;

const MONO: CSSProperties = { fontFamily: "monospace", fontSize: "12px", color: MUTED };

interface Tile {
  key: string;
  kind: string;
  /** The tag under the kind: Default on the internal tile, the drive's own name on a drive's. */
  tag: string;
  folder: string;
  /** `null` where the space free is not known: a drive in use the drives list does not carry. */
  freeBytes: number | null;
  isDefault: boolean;
  inUse: boolean;
  /** A drive in use that is not connected: greyed, "Not connected" for its free space. */
  notConnected: boolean;
  choose: () => void;
}

/** *path* with a break allowed after each slash and nowhere else, so a folder name never splits. */
function breakablePath(path: string): ReactNode {
  const parts = path.split("/");
  return parts.map((part, index) => (
    <Fragment key={index}>
      {part}
      {index < parts.length - 1 && (
        <>
          /<wbr />
        </>
      )}
    </Fragment>
  ));
}

const Path: FC<{ path: string }> = ({ path }) => (
  <span style={{ ...MONO, wordBreak: "keep-all", overflowWrap: "normal" }}>{breakablePath(path)}</span>
);

function tilesOf(state: TenderFolderState, folder: TenderFolderView): Tile[] {
  const internal: Tile = {
    key: "internal",
    kind: INTERNAL_KIND,
    tag: DEFAULT_TAG,
    folder: INTERNAL_FOLDER,
    freeBytes: state.home_free_bytes,
    isDefault: true,
    inUse: state.default,
    notConnected: false,
    choose: folder.chooseDefault,
  };
  const drives = state.drives.map((drive): Tile => ({
    key: drive.mount_point,
    kind: driveKind(drive),
    tag: drive.name,
    folder: drive.folder,
    freeBytes: drive.free_bytes,
    isDefault: false,
    inUse: !state.default && state.place === drive.mount_point,
    notConnected: false,
    choose: () => folder.chooseDrive(drive.mount_point),
  }));
  // The drive in use keeps its tile where the list of writable drives does
  // not carry it: greyed where it is not connected, and as any tile in use
  // where it is (one mounted read-only), its state on the status line's pill.
  const unlisted = driveInUse(state);
  if (unlisted !== null && !state.drives.some((drive) => drive.mount_point === unlisted.mount_point)) {
    drives.push({
      key: unlisted.mount_point,
      kind: driveKind(unlisted),
      tag: unlisted.name,
      folder: state.folder,
      freeBytes: null,
      isDefault: false,
      inUse: true,
      notConnected: !unlisted.connected,
      choose: () => undefined,
    });
  }
  return [internal, ...drives];
}

/** The look a focused tile or row takes: a lighter fill and the accent outline, never Steam's white button. */
function focusLook(focused: boolean): CSSProperties {
  return focused ? { background: TILE_FOCUSED, outline: `2px solid ${SELECTION_ACCENT}`, outlineOffset: "2px" } : {};
}

const TileStop: FC<{ tile: Tile; busy: boolean }> = ({ tile, busy }) => {
  const { focused, handlers } = useFocusWithin();
  const free = tile.notConnected ? NOT_CONNECTED : tile.freeBytes === null ? null : freeLine(tile.freeBytes);
  return (
    <Focusable
      {...handlers}
      // The place in use is chosen already; pressing it does nothing.
      onActivate={() => {
        if (!busy && !tile.inUse) tile.choose();
      }}
      style={{
        flex: "1 1 0",
        minWidth: 0,
        // The tile fills its row's height, so its free line can sit at the bottom.
        display: "flex",
        flexDirection: "column",
        position: "relative",
        boxSizing: "border-box",
        padding: "14px 16px",
        borderRadius: "6px",
        border: `2px solid ${tile.inUse ? SELECTION_ACCENT : "transparent"}`,
        background: tile.inUse ? TILE_IN_USE : TILE,
        color: TEXT,
        opacity: busy || tile.notConnected ? 0.6 : 1,
        ...focusLook(focused),
      }}
    >
      <div
        data-testid={`tender-folder-tile-${tile.key}`}
        data-not-connected={tile.notConnected ? "true" : undefined}
        data-in-use={tile.inUse ? "true" : undefined}
        style={{ display: "flex", flexDirection: "column", flex: "1 1 auto", gap: "6px", minWidth: 0 }}
      >
        <span
          style={{
            fontWeight: 600,
            fontSize: "16px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            // Room for the badge in the corner, so the kind never runs under it.
            paddingRight: tile.inUse ? "64px" : 0,
          }}
        >
          {tile.kind}
        </span>
        {tile.inUse && (
          <span
            style={{
              position: "absolute",
              top: "12px",
              right: "12px",
              whiteSpace: "nowrap",
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              color: SELECTION_ACCENT,
            }}
          >
            {IN_USE_BADGE}
          </span>
        )}
        <span style={{ display: "flex", minWidth: 0 }}>
          <span
            data-testid="tender-folder-tag"
            data-tag={tile.isDefault ? "default" : "drive"}
            style={{
              ...(tile.isDefault ? TAG_DEFAULT : TAG_DRIVE),
              fontSize: "12px",
              fontWeight: 600,
              padding: "2px 9px",
              borderRadius: "999px",
              maxWidth: "100%",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {tile.tag}
          </span>
        </span>
        <Path path={tile.folder} />
        {/* At the foot of the tile and to its right, so the tiles of a row line up whatever their heights. */}
        {free !== null && (
          <span style={{ marginTop: "auto", alignSelf: "flex-end", fontSize: "13px", color: MUTED }}>{free}</span>
        )}
      </div>
    </Focusable>
  );
};

/** Two tiles to a row: one horizontal focus group per row, so the pad moves across a row and down to the next. */
function tileRows(tiles: Tile[], busy: boolean): ReactNode[] {
  const rows: ReactNode[] = [];
  for (let index = 0; index < tiles.length; index += 2) {
    const pair = tiles.slice(index, index + 2);
    rows.push(
      <PanelSectionRow key={pair.map((tile) => tile.key).join("|")}>
        <Focusable flow-children="horizontal" style={{ display: "flex", gap: "12px", marginBottom: "12px" }}>
          {pair.map((tile) => (
            <TileStop key={tile.key} tile={tile} busy={busy} />
          ))}
          {/* An odd tile keeps half the row, as in a two-column grid. */}
          {pair.length === 1 && <span style={{ flex: "1 1 0" }} />}
        </Focusable>
      </PanelSectionRow>,
    );
  }
  return rows;
}

const StatusLine: FC<{ state: TenderFolderState }> = ({ state }) => {
  const color = state.condition === null ? GREEN : AMBER;
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "baseline",
        gap: "6px 10px",
        paddingBottom: "16px",
        marginBottom: "18px",
        borderBottom: `1px solid ${DIVIDER}`,
      }}
    >
      <span style={{ fontSize: "16px", color: TEXT }}>{inUseLabel(state)}</span>
      <span
        data-testid="tender-folder-pill"
        style={{
          fontSize: "11px",
          fontWeight: 600,
          letterSpacing: "0.04em",
          padding: "2px 8px",
          borderRadius: "999px",
          color,
          background: `${color}2e`,
        }}
      >
        {conditionPill(state.condition)}
      </span>
      <span style={{ flexBasis: "100%" }}>
        <Path path={state.folder} />
      </span>
    </div>
  );
};

const AnotherFolderRow: FC<{ disabled: boolean; open: () => void }> = ({ disabled, open }) => {
  const { focused, handlers } = useFocusWithin();
  return (
    <Focusable
      {...handlers}
      onActivate={() => {
        if (!disabled) open();
      }}
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: "12px",
        padding: "12px 16px",
        borderRadius: "4px",
        background: TILE,
        color: TEXT,
        opacity: disabled ? 0.6 : 1,
        ...focusLook(focused),
      }}
    >
      <span>
        {ANOTHER_FOLDER}
        <br />
        <span style={{ color: MUTED, fontSize: "13px" }}>{ANOTHER_FOLDER_LINE}</span>
      </span>
      <span style={{ color: MUTED }}>›</span>
    </Focusable>
  );
};

export const TenderFolderSection: FC<{ folder: TenderFolderView }> = ({ folder }) => {
  const { state, busy, refusal } = folder;
  const askTyped = (drivesRoot: string) =>
    showModal(
      <AnotherFolderModal
        drivesRoot={drivesRoot}
        check={checkTenderPlace}
        choose={chooseTenderPlace}
        onAccepted={folder.acceptTyped}
      />,
    );
  return (
    <PanelSection title={TENDER_FOLDER_SECTION}>
      {state && <StatusLine state={state} />}
      {refusal !== null && (
        <PanelSectionRow>
          <Focusable onActivate={() => {}}>
            <WarningCard title={refusal} compact />
          </Focusable>
        </PanelSectionRow>
      )}
      {state && tileRows(tilesOf(state, folder), busy)}
      {state && state.drives.length === 0 && (
        <div style={{ color: MUTED, fontSize: "13px", margin: "0 0 12px" }}>{NO_DRIVE_NOTE}</div>
      )}
      <PanelSectionRow>
        <AnotherFolderRow
          disabled={busy || !state}
          open={() => {
            if (state) askTyped(state.drives_root);
          }}
        />
      </PanelSectionRow>
    </PanelSection>
  );
};
