/**
 * Body content for an inactive (collapsed-by-default, lazy-loaded) save slot.
 * Renders the slot's saved files plus the Activate/Delete controls, with
 * inline switch-error and offline-hint feedback. Owned exclusively by SlotPanel.
 */

import { FC, type ReactElement } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import type { SlotSaveFile } from "../../types";
import { scrollFocusedToCenter } from "../../utils/scrollHelpers";
import { MUTED_COLOR } from "../../utils/saveHelpers";
import { NO_SAVES_IN_SLOT_TEXT } from "../../utils/saveSlotWording";
import { renderServerSaveRow } from "./ServerSaveRow";
import type { CopyToSlotRowProps } from "./CopyToSlotButton";
import { ThrobberLabel } from "../../shared/Throbber";

export interface InactiveSlotBodyProps {
  loadingSlot: boolean;
  slotFiles: SlotSaveFile[] | null;
  /** Why the slot's saves could not be read, which stands in place of the list. */
  loadError: string | null;
  switching: boolean;
  switchError: string | null;
  isOffline: boolean;
  /**
   * The slot-less legacy bucket — fully read-only (#1276, #1478). Drops the
   * Activate/Delete controls and switch hints, keeping only the file list.
   * Note + muted styling live in SlotPanel.
   */
  isLegacy: boolean;
  handleActivate: () => void;
  handleDelete: () => void;
  deleting: boolean;
  /** Copy-to-slot binding for each server save row (source = this slot). */
  copy: CopyToSlotRowProps;
}

/** The slot's saves as read: Loading..., the line saying why they could not be read, the rows, or that there are none. */
function renderSlotSaves(
  loadingSlot: boolean,
  loadError: string | null,
  slotFiles: SlotSaveFile[] | null,
  copy: CopyToSlotRowProps,
): ReactElement[] {
  if (loadingSlot) {
    return [
      <div key="loading" style={{ fontSize: "13px", color: MUTED_COLOR }}>
        <ThrobberLabel busy>Loading...</ThrobberLabel>
      </div>,
    ];
  }
  if (loadError !== null) {
    return [
      <div key="load-error" style={{ fontSize: "11px", color: "#c46161", fontStyle: "italic" as const }}>
        {loadError}
      </div>,
    ];
  }
  if (slotFiles === null) return [];
  if (slotFiles.length > 0) return slotFiles.map((f) => renderServerSaveRow(f, copy));
  return [
    <div key="no-server-files" style={{ fontSize: "13px", color: MUTED_COLOR, fontStyle: "italic" }}>
      {NO_SAVES_IN_SLOT_TEXT}
    </div>,
  ];
}

export const InactiveSlotBody: FC<InactiveSlotBodyProps> = ({
  loadingSlot,
  slotFiles,
  loadError,
  switching,
  switchError,
  isOffline,
  isLegacy,
  handleActivate,
  handleDelete,
  deleting,
  copy,
}) => {
  const children: (ReactElement | null)[] = renderSlotSaves(loadingSlot, loadError, slotFiles, copy);

  // Slot-less legacy bucket: read-only — no Activate/Delete, no switch hints.
  if (!isLegacy) {
    const activateLabel = switching ? "Switching..." : "Activate Slot";
    const deleteLabel = deleting ? "Deleting..." : "Delete Slot";

    children.push(
      <Focusable
        key="activate-row"
        flow-children="row"
        style={{ marginTop: "10px", display: "flex", gap: "8px", alignItems: "center" }}
      >
        <DialogButton
          key="activate-btn"
          style={{ padding: "4px 12px", minWidth: "auto", fontSize: "12px", width: "auto" }}
          noFocusRing={false}
          onFocus={scrollFocusedToCenter}
          disabled={switching || isOffline}
          onClick={handleActivate}
        >
          <ThrobberLabel busy={switching}>{activateLabel}</ThrobberLabel>
        </DialogButton>
        <DialogButton
          key="delete-btn"
          style={{ padding: "4px 12px", minWidth: "auto", fontSize: "12px", width: "auto", color: "#d94126" }}
          noFocusRing={false}
          onFocus={scrollFocusedToCenter}
          disabled={deleting || switching}
          onClick={handleDelete}
        >
          <ThrobberLabel busy={deleting}>{deleteLabel}</ThrobberLabel>
        </DialogButton>
      </Focusable>,
      isOffline ? (
        <div
          key="offline-hint"
          style={{ fontSize: "11px", color: MUTED_COLOR, fontStyle: "italic" as const, marginTop: "4px" }}
        >
          Offline — slot switching unavailable
        </div>
      ) : null,
      switchError ? (
        <div key="switch-error" style={{ fontSize: "11px", color: "#d94126", marginTop: "4px" }}>
          {switchError}
        </div>
      ) : null,
    );
  }

  return <div className="romm-slot-body">{children.filter(Boolean)}</div>;
};
