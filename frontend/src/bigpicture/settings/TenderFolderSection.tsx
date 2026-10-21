/**
 * Tender folder — where Tender keeps its own files: a status line naming the
 * place in use, its state and its folder; one tile per place, two to a row —
 * the internal storage, marked Default, and each writable drive by its kind and
 * name — the place in use outlined; and "Another folder…". Choosing switches
 * the place and nothing else. Pure renderer: the parent owns the folder's state
 * and the changes.
 */

import { FC, ReactNode } from "react";
import { ButtonItem, DialogButton, Field, Focusable, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import type { TenderFolderState } from "../../types";
import type { TenderFolderView } from "../useTenderFolder";
import {
  ANOTHER_FOLDER,
  ANOTHER_FOLDER_FIELD,
  ANOTHER_FOLDER_LINE,
  DEFAULT_TAG,
  INTERNAL_FOLDER,
  INTERNAL_KIND,
  INTERNAL_NAME,
  IN_USE_BADGE,
  NO_DRIVE_NOTE,
  TENDER_FOLDER_SECTION,
  anotherFolderExample,
  conditionPill,
  driveKind,
  freeLine,
  inUseLabel,
} from "../../utils/tenderFolderWording";
import { AMBER, GREEN, MUTED, SELECTION_ACCENT } from "../layout/pane";
import { WarningCard } from "../WarningCard";
import { TextInputModal } from "./TextInputModal";

interface Tile {
  key: string;
  kind: string;
  name: string;
  folder: string;
  freeBytes: number;
  isDefault: boolean;
  inUse: boolean;
  choose: () => void;
}

const MONO = { fontFamily: "monospace", fontSize: "12px", color: MUTED, overflowWrap: "anywhere" } as const;

function tilesOf(state: TenderFolderState, folder: TenderFolderView): Tile[] {
  const internal: Tile = {
    key: "internal",
    kind: INTERNAL_KIND,
    name: INTERNAL_NAME,
    folder: INTERNAL_FOLDER,
    freeBytes: state.home_free_bytes,
    isDefault: true,
    inUse: state.default,
    choose: folder.chooseDefault,
  };
  const drives = state.drives.map((drive): Tile => ({
    key: drive.mount_point,
    kind: driveKind(drive),
    name: drive.name,
    folder: drive.folder,
    freeBytes: drive.free_bytes,
    isDefault: false,
    inUse: !state.default && state.place === drive.mount_point,
    choose: () => folder.chooseDrive(drive.mount_point),
  }));
  return [internal, ...drives];
}

const TileButton: FC<{ tile: Tile; busy: boolean }> = ({ tile, busy }) => (
  <DialogButton
    disabled={busy}
    style={{
      flex: "1 1 0",
      minWidth: 0,
      textAlign: "left",
      padding: "12px 14px",
      border: `2px solid ${tile.inUse ? SELECTION_ACCENT : "transparent"}`,
    }}
    // The place in use is chosen already; pressing it does nothing.
    onClick={() => {
      if (!busy && !tile.inUse) tile.choose();
    }}
  >
    {/* The marks sit on the body rather than on the button, whose own props
        Steam's DialogButton does not all put on the element it renders. */}
    <span
      data-testid={`tender-folder-tile-${tile.key}`}
      data-in-use={tile.inUse ? "true" : undefined}
      style={{ display: "flex", flexDirection: "column", gap: "4px" }}
    >
      <span style={{ display: "flex", gap: "8px", alignItems: "center", fontSize: "11px", color: MUTED }}>
        <span style={{ textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 600 }}>{tile.kind}</span>
        {tile.isDefault && (
          <span style={{ border: `1px solid ${MUTED}`, borderRadius: "3px", padding: "0 5px" }}>{DEFAULT_TAG}</span>
        )}
        {tile.inUse && (
          <span style={{ marginLeft: "auto", color: SELECTION_ACCENT, fontWeight: 700, textTransform: "uppercase" }}>
            {IN_USE_BADGE}
          </span>
        )}
      </span>
      <span style={{ fontWeight: 600, fontSize: "16px" }}>{tile.name}</span>
      <span style={MONO}>{tile.folder}</span>
      <span style={{ fontSize: "12px", color: MUTED }}>{freeLine(tile.freeBytes)}</span>
    </span>
  </DialogButton>
);

/** Two tiles to a row: one horizontal focus group per row, so the pad moves across a row and down to the next. */
function tileRows(tiles: Tile[], busy: boolean): ReactNode[] {
  const rows: ReactNode[] = [];
  for (let index = 0; index < tiles.length; index += 2) {
    const pair = tiles.slice(index, index + 2);
    rows.push(
      <PanelSectionRow key={pair.map((tile) => tile.key).join("|")}>
        <Focusable flow-children="horizontal" style={{ display: "flex", gap: "12px", marginBottom: "12px" }}>
          {pair.map((tile) => (
            <TileButton key={tile.key} tile={tile} busy={busy} />
          ))}
          {/* An odd tile keeps half the row, as in a two-column grid. */}
          {pair.length === 1 && <span style={{ flex: "1 1 0" }} />}
        </Focusable>
      </PanelSectionRow>,
    );
  }
  return rows;
}

export const TenderFolderSection: FC<{ folder: TenderFolderView }> = ({ folder }) => {
  const { state, busy, refusal } = folder;
  const askTyped = (drivesRoot: string) =>
    showModal(
      <TextInputModal
        label={ANOTHER_FOLDER}
        fieldLabel={ANOTHER_FOLDER_FIELD}
        description={anotherFolderExample(drivesRoot)}
        value=""
        onSubmit={folder.chooseTyped}
      />,
    );
  return (
    <PanelSection title={TENDER_FOLDER_SECTION}>
      {state && (
        <PanelSectionRow>
          <Field
            label={
              <span style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                <span>{inUseLabel(state)}</span>
                <span
                  data-testid="tender-folder-pill"
                  style={{
                    fontSize: "11px",
                    fontWeight: 600,
                    padding: "2px 8px",
                    borderRadius: "999px",
                    color: state.condition === null ? GREEN : AMBER,
                    border: `1px solid ${state.condition === null ? GREEN : AMBER}`,
                  }}
                >
                  {conditionPill(state.condition)}
                </span>
              </span>
            }
            description={<span style={MONO}>{state.folder}</span>}
            focusable
          />
        </PanelSectionRow>
      )}
      {refusal !== null && (
        <PanelSectionRow>
          <Focusable onActivate={() => {}}>
            <WarningCard title={refusal} compact />
          </Focusable>
        </PanelSectionRow>
      )}
      {state && tileRows(tilesOf(state, folder), busy)}
      {state && state.drives.length === 0 && (
        <PanelSectionRow>
          <Field description={NO_DRIVE_NOTE} focusable />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          description={ANOTHER_FOLDER_LINE}
          disabled={busy || !state}
          onClick={() => {
            if (state) askTyped(state.drives_root);
          }}
        >
          {ANOTHER_FOLDER}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};
