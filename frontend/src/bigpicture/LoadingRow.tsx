import { FC } from "react";
import { PanelSectionRow } from "@decky/ui";
import { Throbber } from "../shared/Throbber";

/** The throbber's size in a section's loading row, larger than a label's. */
export const LOADING_SPINNER_SIZE = 24;

interface LoadingRowProps {
  /** Optional caption rendered beside the throbber (e.g. "Loading platforms…"). */
  label?: string;
}

/** The canonical QAM loading state: the throbber centered in a PanelSectionRow. */
export const LoadingRow: FC<LoadingRowProps> = ({ label }) => (
  <PanelSectionRow>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", padding: "8px 0" }}>
      <Throbber size={`${LOADING_SPINNER_SIZE}px`} />
      {label ? <span style={{ fontSize: "13px", opacity: 0.7 }}>{label}</span> : null}
    </div>
  </PanelSectionRow>
);
