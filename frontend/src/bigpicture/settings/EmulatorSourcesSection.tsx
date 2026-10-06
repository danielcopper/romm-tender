/**
 * Emulator sources — every source the resolver detects, in the user's order,
 * as one card each: its place in the order, its name with Move up and Move
 * down beside it (a controller cannot drag), the folder it lives in, what its
 * health says, and the switch "Use this source". Pure renderer: the parent owns
 * the listing and the writes.
 */

import { FC, ReactElement } from "react";
import { PanelSection, PanelSectionRow, DialogButton, Field, Focusable, ToggleField } from "@decky/ui";
import { FaCheckCircle, FaChevronDown, FaChevronUp, FaExclamationTriangle, FaInfoCircle } from "react-icons/fa";
import type { EmulatorSource, EmulatorSourceDirection, EmulatorSourcesListing } from "../../types";
import {
  NO_SOURCE_BANNER,
  SOURCES_READING,
  SOURCES_UNREAD,
  sourceName,
  sourceRowLines,
  type SourceRowTone,
} from "../../utils/emulatorSourceWording";
import { AMBER, GREEN, MUTED, SECONDARY_FONT, SELECTION_ACCENT, TABLE_LINE } from "../layout/pane";

interface EmulatorSourcesSectionProps {
  /** `undefined` while the read is in flight, `null` where it failed. */
  listing: EmulatorSourcesListing | null | undefined;
  /** A switch or a move is in flight; the parent refuses a second one as well. */
  busy: boolean;
  onSwitch: (kind: string, enabled: boolean) => void;
  onMove: (kind: string, direction: EmulatorSourceDirection) => void;
}

const NAME_ON = "#ffffff";

const TONE_ICONS: Record<SourceRowTone, { Icon: typeof FaCheckCircle; color: string }> = {
  ok: { Icon: FaCheckCircle, color: GREEN },
  info: { Icon: FaInfoCircle, color: MUTED },
  warning: { Icon: FaExclamationTriangle, color: AMBER },
};

const ARROW_BUTTON = {
  width: "32px",
  height: "32px",
  minWidth: 0,
  padding: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
} as const;

const PlaceBadge: FC<{ kind: string; place: number; on: boolean }> = ({ kind, place, on }) => (
  <span
    data-testid={`source-place-${kind}`}
    style={{
      flex: "0 0 auto",
      width: "24px",
      height: "24px",
      boxSizing: "border-box",
      borderRadius: "50%",
      border: `2px solid ${on ? SELECTION_ACCENT : MUTED}`,
      background: on ? SELECTION_ACCENT : "transparent",
      color: on ? NAME_ON : MUTED,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontSize: "12px",
      fontWeight: 700,
    }}
  >
    {place}
  </span>
);

interface SourceCardProps {
  source: EmulatorSource;
  index: number;
  last: number;
  busy: boolean;
  onSwitch: EmulatorSourcesSectionProps["onSwitch"];
  onMove: EmulatorSourcesSectionProps["onMove"];
}

const SourceCard: FC<SourceCardProps> = ({ source, index, last, busy, onSwitch, onMove }) => {
  const name = sourceName(source.kind);
  const arrow = (direction: EmulatorSourceDirection, atEnd: boolean): ReactElement => (
    <DialogButton
      aria-label={`Move ${name} ${direction}`}
      title={`Move ${name} ${direction}`}
      style={ARROW_BUTTON}
      disabled={busy || atEnd}
      // Dead at either end, in the handler too: a disabled control still
      // reports a press on the device.
      onClick={() => {
        if (!busy && !atEnd) onMove(source.kind, direction);
      }}
    >
      {direction === "up" ? <FaChevronUp size={12} /> : <FaChevronDown size={12} />}
    </DialogButton>
  );
  return (
    <div
      data-testid={`source-card-${source.kind}`}
      style={{ background: "rgba(255, 255, 255, 0.04)", borderRadius: "4px", padding: "8px 12px", marginBottom: "8px" }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
        <PlaceBadge kind={source.kind} place={index + 1} on={source.enabled} />
        <div style={{ flex: "1 1 auto", minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span
              data-testid={`source-name-${source.kind}`}
              style={{ flex: "1 1 auto", minWidth: 0, fontWeight: 600, color: source.enabled ? NAME_ON : MUTED }}
            >
              {name}
            </span>
            <Focusable flow-children="horizontal" style={{ display: "flex", gap: "4px", flex: "0 0 auto" }}>
              {arrow("up", index === 0)}
              {arrow("down", index === last)}
            </Focusable>
          </div>
          {/* Read-only, and focusable for the reason every such row on a wide
              pane is: the region scrolls by moving focus. The root line is
              left out where the root is a default (the settings file is
              missing or broken), which the lines below it say. */}
          <Field
            description={
              <span data-testid={`source-lines-${source.kind}`}>
                {source.root !== null && (
                  <div
                    data-testid={`source-root-${source.kind}`}
                    style={{
                      fontFamily: "monospace",
                      fontSize: SECONDARY_FONT,
                      color: MUTED,
                      overflowWrap: "anywhere",
                    }}
                  >
                    {source.root}
                  </div>
                )}
                {sourceRowLines(source).map(({ tone, text }) => {
                  const { Icon, color } = TONE_ICONS[tone];
                  return (
                    <div key={text} style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                      <span
                        data-testid="source-line-icon"
                        data-tone={tone}
                        style={{ flex: "0 0 auto", color, position: "relative", top: "1px" }}
                      >
                        <Icon size={11} aria-hidden={true} />
                      </span>
                      <span>{text}</span>
                    </div>
                  );
                })}
              </span>
            }
            padding="compact"
            bottomSeparator="none"
            focusable={true}
          />
        </div>
      </div>
      <div style={{ borderTop: TABLE_LINE, marginTop: "4px" }}>
        <ToggleField
          label="Use this source"
          checked={source.enabled}
          disabled={busy}
          bottomSeparator="none"
          onChange={(enabled) => {
            if (!busy) onSwitch(source.kind, enabled);
          }}
        />
      </div>
    </div>
  );
};

export const EmulatorSourcesSection: FC<EmulatorSourcesSectionProps> = ({ listing, busy, onSwitch, onMove }) => {
  if (listing === undefined || listing === null || listing.sources.length === 0) {
    const line = listing === undefined ? SOURCES_READING : listing === null ? SOURCES_UNREAD : NO_SOURCE_BANNER;
    return (
      <PanelSection title="Emulator sources">
        <PanelSectionRow>
          <Field label={<span data-testid="sources-notice">{line}</span>} focusable={true} />
        </PanelSectionRow>
      </PanelSection>
    );
  }
  const last = listing.sources.length - 1;
  return (
    <PanelSection title="Emulator sources">
      {listing.sources.map((source, index) => (
        <PanelSectionRow key={source.kind}>
          <SourceCard source={source} index={index} last={last} busy={busy} onSwitch={onSwitch} onMove={onMove} />
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
};
