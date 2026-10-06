/**
 * Emulator sources — every source the resolver detects, in the user's order,
 * as one card each: its place in the order, its name with Move up and Move
 * down beside it (a controller cannot drag), the folder it lives in, what its
 * health says, and the switch "Use this source". Pure renderer: the parent owns
 * the listing and the writes.
 */

import { FC, ReactElement, RefObject, useEffect, useLayoutEffect, useRef } from "react";
import { PanelSection, PanelSectionRow, DialogButton, Field, Focusable, ToggleField } from "@decky/ui";
import { FaCheckCircle, FaChevronDown, FaExclamationTriangle, FaInfoCircle } from "react-icons/fa";
import type { EmulatorSource, EmulatorSourceDirection, EmulatorSourcesListing } from "../../types";
import {
  NO_SOURCE_BANNER,
  SOURCES_READING,
  SOURCES_UNREAD,
  sourceName,
  sourceRowLines,
  type SourceRowTone,
} from "../../utils/emulatorSourceWording";
import { ENTRY_FOCUS_DELAY_MS, placeEntryFocus } from "../../utils/entryFocus";
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

// The up arrow is the down chevron turned over rather than a glyph of its own:
// the chevron is symmetric, and a second one would cost bundle bytes.
const TURNED_OVER = { transform: "rotate(180deg)" } as const;

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
  cardRef: (card: HTMLDivElement | null) => void;
  onSwitch: EmulatorSourcesSectionProps["onSwitch"];
  onMove: EmulatorSourcesSectionProps["onMove"];
}

const SourceCard: FC<SourceCardProps> = ({ source, index, last, busy, cardRef, onSwitch, onMove }) => {
  const name = sourceName(source.kind);
  const arrow = (direction: EmulatorSourceDirection, atEnd: boolean): ReactElement => (
    // In a wrapper of its own, so focus can be handed back to it after a move
    // (`arrowOf`); `display: contents` keeps the wrapper out of the line.
    <span data-move={direction} style={{ display: "contents" }}>
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
        <FaChevronDown size={12} style={direction === "up" ? TURNED_OVER : undefined} />
      </DialogButton>
    </span>
  );
  return (
    <div
      ref={cardRef}
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
          {/* No focus stop of its own: the arrows above it and the switch
              below it are stops of the same card, and the region brings it
              into view on the way between them. The root line is left out
              where the root is a default (the settings file is missing or
              broken), which the lines below it say. */}
          <div data-testid={`source-lines-${source.kind}`} style={{ color: MUTED, fontSize: "12px", marginTop: "4px" }}>
            {source.root !== null && (
              <div
                data-testid={`source-root-${source.kind}`}
                style={{
                  fontFamily: "monospace",
                  fontSize: SECONDARY_FONT,
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
          </div>
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
  return (
    <PanelSection title="Emulator sources">
      <SourceCards sources={listing.sources} busy={busy} onSwitch={onSwitch} onMove={onMove} />
    </PanelSection>
  );
};

/** The cards, mounted only while there are sources to list, so a list that
 *  comes back after a notice starts from no remembered places. */
const SourceCards: FC<Omit<EmulatorSourcesSectionProps, "listing"> & { sources: EmulatorSource[] }> = ({
  sources,
  busy,
  onSwitch,
  onMove,
}) => {
  const cards = useSlideOnReorder(sources.map((source) => source.kind).join("\n"));
  const pressed = useFocusFollowsMove(cards, sources, busy);
  const last = sources.length - 1;
  return sources.map((source, index) => (
    <PanelSectionRow key={source.kind}>
      <SourceCard
        source={source}
        index={index}
        last={last}
        busy={busy}
        cardRef={(card) => {
          if (card === null) cards.current.delete(source.kind);
          else cards.current.set(source.kind, card);
        }}
        onSwitch={onSwitch}
        onMove={(kind, direction) => {
          pressed(kind, direction);
          onMove(kind, direction);
        }}
      />
    </PanelSectionRow>
  ));
};

const SLIDE = "transform 200ms ease-out";

/**
 * Slide every card whose place changed with the order from where it stood to
 * where it stands: the card is drawn back at its old place, then let go.
 * Answers the cards it slides, by kind, which each card enters itself into.
 *
 * Places are layout offsets (`offsetTop`), which neither a scroll of the region
 * nor a slide still under way changes. The frame is asked of the card's own
 * window: the panel's code runs in another one (CLAUDE.md, the `instanceof`
 * trap).
 */
function useSlideOnReorder(order: string): RefObject<Map<string, HTMLDivElement>> {
  const cards = useRef(new Map<string, HTMLDivElement>());
  const placed = useRef<{ order: string; tops: Map<string, number> } | null>(null);
  useLayoutEffect(() => {
    const tops = new Map([...cards.current].map(([kind, card]) => [kind, layoutTop(card)]));
    const before = placed.current;
    placed.current = { order, tops };
    if (before === null || before.order === order) return;
    for (const [kind, card] of cards.current) {
      const from = before.tops.get(kind);
      const to = tops.get(kind);
      const view = card.ownerDocument.defaultView;
      if (from === undefined || to === undefined || from === to || view === null) continue;
      card.style.transition = "";
      card.style.transform = `translateY(${from - to}px)`;
      // Reading the box makes the browser apply the old place now; without it
      // both writes land in the same frame and nothing slides.
      card.getBoundingClientRect();
      view.requestAnimationFrame(() => {
        card.style.transition = SLIDE;
        card.style.transform = "";
      });
    }
  });
  return cards;
}

function layoutTop(element: HTMLElement): number {
  let top = 0;
  let node: HTMLElement | null = element;
  while (node) {
    top += node.offsetTop;
    node = node.offsetParent as HTMLElement | null;
  }
  return top;
}

/**
 * Hand focus back to the arrow that was pressed once the listing it asked for
 * has arrived — or to the card's other arrow, where the move took the source to
 * the end its own arrow is dead at. Answers the function a press is recorded
 * with.
 *
 * Two things stand between the press and the arrow keeping focus: every arrow
 * is disabled while the change is in flight, and React reorders by taking a
 * card's node out and putting it back, which drops the focus it held. Focus is
 * placed the way entry focus is, after Steam has settled its own pointer — and
 * not where the reader has gone somewhere outside the cards in the meantime,
 * which they chose.
 */
function useFocusFollowsMove(
  cards: RefObject<Map<string, HTMLDivElement>>,
  sources: EmulatorSource[],
  busy: boolean,
): (kind: string, direction: EmulatorSourceDirection) => void {
  const press = useRef<{ kind: string; direction: EmulatorSourceDirection; sources: EmulatorSource[] } | null>(null);
  useEffect(() => {
    const pressed = press.current;
    if (pressed === null || busy || pressed.sources === sources) return;
    press.current = null;
    const card = cards.current.get(pressed.kind);
    if (card === undefined) return;
    setTimeout(() => {
      const doc = card.ownerDocument;
      const active = doc.activeElement;
      if (
        active !== null &&
        active !== doc.body &&
        ![...cards.current.values()].some((other) => other.contains(active))
      ) {
        return;
      }
      const back = pressed.direction === "up" ? "down" : "up";
      placeEntryFocus(card, (root) => arrowOf(root, pressed.direction) ?? arrowOf(root, back));
    }, ENTRY_FOCUS_DELAY_MS);
  }, [cards, sources, busy]);
  return (kind, direction) => {
    press.current = { kind, direction, sources };
  };
}

function arrowOf(card: ParentNode, direction: EmulatorSourceDirection): HTMLElement | null {
  return card.querySelector<HTMLElement>(`[data-move="${direction}"] button:not([disabled])`);
}
