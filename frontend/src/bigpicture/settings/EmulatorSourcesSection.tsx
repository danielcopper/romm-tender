/**
 * Emulator sources — every source the resolver detects, in the user's order:
 * its name, the folder it lives in, what its health says, the switch "Use this
 * source", and Move up / Move down, since a controller cannot drag. Pure
 * renderer: the parent owns the listing and the writes.
 */

import { FC, Fragment } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, Field, ToggleField } from "@decky/ui";
import type { EmulatorSourceDirection, EmulatorSourcesListing } from "../../types";
import {
  NO_SOURCE_BANNER,
  SOURCES_READING,
  SOURCES_UNREAD,
  sourceName,
  sourceRowLines,
} from "../../utils/emulatorSourceWording";

interface EmulatorSourcesSectionProps {
  /** `undefined` while the read is in flight, `null` where it failed. */
  listing: EmulatorSourcesListing | null | undefined;
  /** A switch or a move is in flight; the parent refuses a second one as well. */
  busy: boolean;
  onSwitch: (kind: string, enabled: boolean) => void;
  onMove: (kind: string, direction: EmulatorSourceDirection) => void;
}

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
        <Fragment key={source.kind}>
          <PanelSectionRow>
            {/* Read-only, and focusable for the reason every such row on a wide
                pane is: the region scrolls by moving focus. The root line is
                left out where the root is a default (the settings file is
                missing or broken), which the lines below it say. */}
            <Field
              label={<span data-testid={`source-name-${source.kind}`}>{sourceName(source.kind)}</span>}
              description={
                <span data-testid={`source-lines-${source.kind}`}>
                  {source.root !== null && <div data-testid={`source-root-${source.kind}`}>{source.root}</div>}
                  {sourceRowLines(source).map((line) => (
                    <div key={line}>{line}</div>
                  ))}
                </span>
              }
              focusable={true}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Use this source"
              checked={source.enabled}
              disabled={busy}
              onChange={(enabled) => {
                if (!busy) onSwitch(source.kind, enabled);
              }}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            {/* Dead at either end, in the handler too: a disabled control still
                reports a press on the device. */}
            <ButtonItem
              layout="below"
              disabled={busy || index === 0}
              onClick={() => {
                if (!busy && index > 0) onMove(source.kind, "up");
              }}
            >
              Move up
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || index === last}
              onClick={() => {
                if (!busy && index < last) onMove(source.kind, "down");
              }}
            >
              Move down
            </ButtonItem>
          </PanelSectionRow>
        </Fragment>
      ))}
    </PanelSection>
  );
};
