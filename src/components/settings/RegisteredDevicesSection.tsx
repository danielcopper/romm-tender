/**
 * The devices currently registered with the RomM save-sync backend, as a table.
 * Visible only when save-sync is enabled; parent owns the device list, the
 * loading flag and the error message.
 *
 * Three facts per row, so three columns with a header row rather than a label
 * and a description carrying all of them — `docs/architecture/qam-panel.md`,
 * "Building blocks → Tables". The device's OS and the head of its id are
 * deliberately not among them: `register_device` passes a hardcoded
 * `platform="linux"` (`services/saves/sync_engine/devices.py`), so the OS
 * column would repeat one value down every row this plugin registers, and the
 * id only separates two devices that share a name.
 *
 * Every device row is a focus stop and the header is not: a wide pane scrolls
 * only by moving focus, so a group with no stop in it is a group nobody can
 * scroll to — while a stop on the column names would be a step that leads
 * nowhere. The states that are not rows (loading, error, no devices) keep their
 * own stop for the first reason and draw no header, having no columns.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, Field } from "@decky/ui";
import type { RegisteredDevice } from "../../types";
import { CELL_CLIP, MUTED, PaneTableHeader, PaneTableRow, SECONDARY_FONT } from "../qam/pane";
import { formatRelativeTime } from "./helpers";

// The name is the column with something to say, so it takes what the other two
// leave.
//
// `Client` is sized for the longest string the SERVER can hand back, which is
// not what this plugin registers under today. `register_device` passes
// `client=DISPLAY_NAME` — "Tender" (`domain/identity.py`) — but the rows come
// from RomM, which keeps what earlier versions wrote, and up to 0.32 that was
// `decky-romm-sync`. A real listing shows both spellings side by side, plus
// `web` for the rows RomM's own frontend registers, and the column has to hold
// the widest of them: `decky-romm-sync v0.32.0`, 23 characters. Sizing it to
// today's name would clip every row a Deck registered before the rename, and
// those do not expire. At
// the 11px these cells are set in that measures 131px in Noto Sans and 143px in
// DejaVu Sans; the device renders it in Steam's own Motiva Sans, which is on
// neither this machine nor any check here, so the track takes the wider of the
// two plus room for a three-digit minor version. Under-sizing it clips the
// VERSION, which is the only part of the column that differs between rows.
//
// `Last seen` holds every answer `formatRelativeTime` gives — `never`,
// `unknown`, `just now`, `59m ago` at the widest of its minute forms, `23h ago`
// at the widest of its hour forms, and a date like `15 Jun`. The widest of them
// measured the same way is `unknown` at 48px in Noto Sans and 50px in DejaVu,
// which is character count and glyph width disagreeing: it is a character
// shorter than `just now` and wider on screen.
const TABLE_COLUMNS = "1fr 144px 72px";

/** Digits that sit under each other down the column rather than shifting with
 *  the glyph widths of the row above. */
const TABULAR = { fontVariantNumeric: "tabular-nums" } as const;

/** What the two right-hand columns are set in: the pane's secondary line, so
 *  they read as the quieter half of the row the name leads. */
const SECONDARY_CELL = { color: MUTED, fontSize: SECONDARY_FONT, ...TABULAR } as const;

interface RegisteredDevicesSectionProps {
  devicesLoading: boolean;
  devicesError: string | null;
  registeredDevices: RegisteredDevice[] | null;
}

const DeviceRow: FC<{ device: RegisteredDevice }> = ({ device }) => {
  const name = device.name ?? "(unnamed)";
  const client = `${device.client ?? "unknown client"} v${device.client_version ?? "?"}`;
  const lastSeen = formatRelativeTime(device.last_seen);
  return (
    <PaneTableRow
      columns={TABLE_COLUMNS}
      testId="device-row"
      cells={[
        {
          // This cell is a flex container holding the name and the marker, so
          // the clip it inherits cannot do the work alone: hiding the
          // container's overflow would take the marker away with it. The name
          // is a flex ITEM, blockified like a grid one, so the same three
          // properties apply to it — and the marker keeps its width beside a
          // name of any length.
          content: (
            <>
              <span style={{ flex: "1 1 auto", ...CELL_CLIP }}>{name}</span>
              {device.is_current_device && (
                <span style={{ flexShrink: 0, color: "#6ab04c", fontSize: SECONDARY_FONT }}>(this device)</span>
              )}
            </>
          ),
          style: { display: "flex", alignItems: "center", gap: "8px" },
          title: name,
        },
        { content: client, style: SECONDARY_CELL, title: client },
        { content: lastSeen, style: SECONDARY_CELL, title: lastSeen },
      ]}
    />
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
      {devices !== null && devices.length > 0 && (
        <PaneTableHeader columns={TABLE_COLUMNS} cells={["Device", "Client", "Last seen"]} testId="devices-header" />
      )}
      {devices?.map((device, i) => (
        <DeviceRow key={device.id || `idx-${i}`} device={device} />
      ))}
    </PanelSection>
  );
};
