/**
 * The devices currently registered with the RomM save-sync backend, as a table.
 * Visible only when save-sync is enabled; parent owns the device list, the
 * loading flag and the error message.
 *
 * Three facts per row, so three columns with a header row rather than a label
 * and a description carrying all of them — `docs/architecture/qam-panel.md`,
 * "Building blocks → Tables". The device's OS and the head of its id are
 * deliberately not among them: the OS is the same value on every row a Deck
 * will ever show, and the id only separates two devices that share a name.
 *
 * Every device row is a focus stop and the header is not: a wide pane scrolls
 * only by moving focus, so a group with no stop in it is a group nobody can
 * scroll to — while a stop on the column names would be a step that leads
 * nowhere. The states that are not rows (loading, error, no devices) keep their
 * own stop for the first reason and draw no header, having no columns.
 */

import { FC } from "react";
import { Focusable, PanelSection, PanelSectionRow, Field } from "@decky/ui";
import type { RegisteredDevice } from "../../types";
import { MUTED, SECONDARY_FONT } from "../qam/pane";
import { formatRelativeTime } from "./helpers";

// The name is the column with something to say, so it takes what the other two
// leave. `Client` is sized for "tender v0.32.0" and `Last seen` for the longest
// thing `formatRelativeTime` answers.
const TABLE_COLUMNS = "1fr 104px 72px";

/** The three properties that make a cell clip instead of spilling across the
 *  track beside it, plus the floor reset that lets it shrink at all. They belong
 *  on the grid ITEM, which is blockified; on an inline span nested inside one
 *  they do nothing. */
const CLIP = { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

/** Digits that sit under each other down the column rather than shifting with
 *  the glyph widths of the row above. */
const TABULAR = { fontVariantNumeric: "tabular-nums" } as const;

interface RegisteredDevicesSectionProps {
  devicesLoading: boolean;
  devicesError: string | null;
  registeredDevices: RegisteredDevice[] | null;
}

const TableHeader: FC = () => (
  // Plain text: the column names accompany the rows below them and scroll with
  // them, so a focus stop here would add a step that leads nowhere.
  <div
    data-testid="devices-header"
    style={{
      display: "grid",
      gridTemplateColumns: TABLE_COLUMNS,
      gap: "8px",
      padding: "0 16px 4px",
      fontSize: SECONDARY_FONT,
      color: MUTED,
    }}
  >
    <span>Device</span>
    <span>Client</span>
    <span>Last seen</span>
  </div>
);

const DeviceRow: FC<{ device: RegisteredDevice }> = ({ device }) => {
  const name = device.name ?? "(unnamed)";
  const client = `${device.client ?? "unknown client"} v${device.client_version ?? "?"}`;
  const lastSeen = formatRelativeTime(device.last_seen);
  return (
    // A row with nothing to press still has to be reachable, or the reader
    // cannot scroll past it to the rows below: the activate handler is what
    // makes a Focusable a focus stop.
    <Focusable onActivate={() => {}} style={{ padding: "4px 16px" }}>
      <div
        data-testid="device-row"
        style={{ display: "grid", gridTemplateColumns: TABLE_COLUMNS, gap: "8px", alignItems: "center" }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: "8px", ...CLIP }} title={name}>
          {/* The clip is on this span rather than only on the cell around it:
              that cell is a flex container, and clipping it would take the
              marker beside the name away with the overflow. A flex ITEM is
              blockified too, so the three properties still apply here. */}
          <span style={{ flex: "1 1 auto", ...CLIP }}>{name}</span>
          {device.is_current_device && (
            <span style={{ flexShrink: 0, color: "#6ab04c", fontSize: SECONDARY_FONT }}>(this device)</span>
          )}
        </span>
        <span style={{ color: MUTED, fontSize: SECONDARY_FONT, ...TABULAR, ...CLIP }} title={client}>
          {client}
        </span>
        <span style={{ color: MUTED, fontSize: SECONDARY_FONT, ...TABULAR, ...CLIP }} title={lastSeen}>
          {lastSeen}
        </span>
      </div>
    </Focusable>
  );
};

export const RegisteredDevicesSection: FC<RegisteredDevicesSectionProps> = ({
  devicesLoading,
  devicesError,
  registeredDevices,
}) => {
  const devices = devicesLoading || devicesError ? null : registeredDevices;
  return (
    <PanelSection title="Registered Devices">
      {devicesLoading && (
        <PanelSectionRow>
          <Field label="Loading..." focusable={true} />
        </PanelSectionRow>
      )}
      {!devicesLoading && devicesError && (
        <PanelSectionRow>
          <Field label="Could not load devices" description={devicesError} focusable={true} />
        </PanelSectionRow>
      )}
      {devices !== null && devices.length === 0 && (
        <PanelSectionRow>
          <Field label="No devices registered" focusable={true} />
        </PanelSectionRow>
      )}
      {devices !== null && devices.length > 0 && <TableHeader />}
      {devices?.map((device, i) => (
        <DeviceRow key={device.id || `idx-${i}`} device={device} />
      ))}
    </PanelSection>
  );
};
