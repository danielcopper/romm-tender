/**
 * Read-only list of devices currently registered with the RomM save-sync
 * backend. Visible only when save-sync is enabled; parent owns the device
 * list, loading flag, and error message.
 *
 * Every row here is a focus stop, and the group's whole content is read-only —
 * a wide pane scrolls only by moving focus, so a group with no stop in it is a
 * group nobody can scroll to.
 */

import { FC } from "react";
import { PanelSection, PanelSectionRow, Field } from "@decky/ui";
import type { RegisteredDevice } from "../../types";
import { formatRelativeTime } from "./helpers";

interface RegisteredDevicesSectionProps {
  devicesLoading: boolean;
  devicesError: string | null;
  registeredDevices: RegisteredDevice[] | null;
}

export const RegisteredDevicesSection: FC<RegisteredDevicesSectionProps> = ({
  devicesLoading,
  devicesError,
  registeredDevices,
}) => {
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
      {!devicesLoading && !devicesError && registeredDevices !== null && registeredDevices.length === 0 && (
        <PanelSectionRow>
          <Field label="No devices registered" focusable={true} />
        </PanelSectionRow>
      )}
      {!devicesLoading &&
        !devicesError &&
        registeredDevices !== null &&
        registeredDevices.map((device, i) => {
          const parts: string[] = [
            `${device.client ?? "unknown client"} v${device.client_version ?? "?"}`,
            ...(device.platform ? [device.platform] : []),
            `last seen ${formatRelativeTime(device.last_seen)}`,
            `ID ${String(device.id).slice(0, 8) || "—"}`,
          ];
          return (
            <PanelSectionRow key={device.id || `idx-${i}`}>
              <Field
                focusable={true}
                label={
                  <span>
                    {device.name ?? "(unnamed)"}
                    {device.is_current_device && (
                      <span style={{ color: "#6ab04c", marginLeft: "8px", fontSize: "12px" }}>(this device)</span>
                    )}
                  </span>
                }
                description={parts.join(" · ")}
              />
            </PanelSectionRow>
          );
        })}
    </PanelSection>
  );
};
