/**
 * The emulator selector over a BIOS table: a row that steps through
 * every emulator the resolver answered for, the launching one first, and under
 * it a carousel of all of them, each with its own verdict as a dot.
 *
 * The row reads "L2 ◀ ● <emulator> 1 / 5 ▶ R2"; the arrows and the triggers
 * step to the previous and next emulator and wrap around. A name is set in the
 * accent where that emulator launches and in red where it is not installed.
 * The carousel keeps the shown emulator in its middle, outlined, with the
 * others on either side in stepping order (`utils/emulatorCarousel.ts`), and an
 * entry is chosen by pressing it — moving focus onto it chooses nothing.
 * Choosing an emulator changes which one the table below shows, never which
 * one launches.
 *
 * Steam's own `Dropdown` is not used: in Game Mode it opens its menu over the
 * whole screen.
 *
 * Every entry is drawn from the answer it is rendered with, looked up by its
 * identity, so a fresh answer after a download recolours its dot.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, type FC } from "react";
import { DialogButton, Focusable } from "@decky/ui";
import type { EmulatorTab } from "../types";
import { stepTab, tabDotColor, tabIndex, tabLabel } from "../utils/biosTabs";
import { ControllerGlyph, GLYPH_TRIGGER_LEFT, GLYPH_TRIGGER_RIGHT } from "../utils/deckyUiInternals";
import { ringLayout, slideFrom, type RingLayout } from "../utils/emulatorCarousel";
import { useStepOnTriggers } from "./emulatorStepping";
import { MUTED, NOTE_FONT, RED, SECONDARY_FONT, SELECTION_ACCENT } from "./layout/pane";

/** The top row's columns: two equal sides around the shown emulator's name, which
 *  is held to at most 60% of the row and cut short beyond it. */
const TOP_ROW_COLUMNS = "minmax(0, 1fr) fit-content(60%) minmax(0, 1fr)";

/** The attribute the carousel's shown entry carries, for a page to put focus on. */
export const SHOWN_ENTRY_ATTR = "data-shown-entry";

/** The space between two entries of the carousel, in px. */
const ENTRY_GAP = 3;
const SLIDE = "transform 180ms ease";
/** Room above and below the entries, so the outline and the focus highlight are not clipped. */
const FRAME_INSET = 2;

/** `DialogButton` is Steam's `.DialogButton`, which sets `width: 100%`, a 2px margin and a 32px line height. */
const ARROW = {
  width: "26px",
  minWidth: 0,
  margin: 0,
  padding: 0,
  lineHeight: "24px",
  fontSize: SECONDARY_FONT,
  flexShrink: 0,
} as const;
const ENTRY = {
  width: "auto",
  minWidth: 0,
  margin: 0,
  padding: "1px 5px",
  lineHeight: "14px",
  fontSize: NOTE_FONT,
  whiteSpace: "nowrap",
} as const;

const Dot: FC<{ met: boolean | null; size: string }> = ({ met, size }) => (
  <span
    data-testid="emulator-dot"
    style={{ width: size, height: size, borderRadius: "50%", backgroundColor: tabDotColor(met), flexShrink: 0 }}
  />
);

/**
 * The trigger that steps the selector, drawn as Steam draws L1 and R1 over its
 * own tabs: its controller glyph, for whatever controller is connected
 * (`ControllerGlyph`). Where that probe misses, a small key box with the
 * trigger's name stands in.
 */
const TriggerGlyph: FC<{ button: number; label: string }> = ({ button, label }) =>
  ControllerGlyph ? (
    <span data-testid="trigger-glyph" style={{ display: "inline-flex", flexShrink: 0 }}>
      <ControllerGlyph button={button} style={{ height: "16px" }} />
    </span>
  ) : (
    <span
      data-testid="trigger-glyph"
      style={{
        flexShrink: 0,
        fontSize: NOTE_FONT,
        fontWeight: 700,
        lineHeight: 1.4,
        padding: "0 3px",
        borderRadius: "3px",
        background: "#fff",
        color: "#000",
      }}
    >
      {label}
    </span>
  );

/**
 * An entry's outline: the launching emulator's in the accent its name takes,
 * whether shown or not, and the shown one's in the light grey otherwise.
 */
function entryOutline(tab: EmulatorTab, shown: boolean): string {
  if (tab.launching) return `1px solid ${SELECTION_ACCENT}`;
  return shown ? "1px solid #dcdedf" : "none";
}

/**
 * The colour an emulator's name is set in, here and in the carousel: red where
 * it is not installed, the accent where it launches, else the text's own. Red
 * wins where both hold — the launching entry stays known by its outline.
 */
function nameColour(tab: EmulatorTab): string | undefined {
  if (tab.installed === false) return RED;
  return tab.launching ? SELECTION_ACCENT : undefined;
}

/** What keeps an entry's element across answers: its role, or its emulator's identity. */
function entryKey(tab: EmulatorTab): string {
  return tab.launching ? "launching" : `emulator:${tab.emulator ?? ""}`;
}

/**
 * Every emulator as one element in stepping order, each moved into its place on
 * the ring rather than rendered there: React keeps an element by its key, so a
 * step moves the same elements and the move is what slides.
 */
const Carousel: FC<{ tabs: readonly EmulatorTab[]; shown: number; onSelect: (tab: EmulatorTab) => void }> = ({
  tabs,
  shown,
  onSelect,
}) => {
  const frame = useRef<HTMLDivElement>(null);
  const entries = useRef<(HTMLDivElement | null)[]>([]);
  const placed = useRef<RingLayout | null>(null);
  const place = useRef<() => void>(() => {});

  useLayoutEffect(() => {
    place.current = () => {
      const elements = tabs.map((_, i) => entries.current[i] ?? null);
      const next = ringLayout(
        elements.map((el) => el?.offsetWidth ?? 0),
        ENTRY_GAP,
        shown,
      );
      const from = slideFrom(placed.current, next);
      elements.forEach((el, i) => {
        if (el === null) return;
        el.style.transition = "none";
        el.style.transform = `translateX(${from?.[i] ?? next.xs[i] ?? 0}px)`;
      });
      if (from !== null) {
        // Reading a layout value commits the start positions, so the transition
        // runs from them rather than from wherever the entries stood before.
        frame.current?.getBoundingClientRect();
        elements.forEach((el, i) => {
          if (el === null) return;
          el.style.transition = SLIDE;
          el.style.transform = `translateX(${next.xs[i] ?? 0}px)`;
        });
      }
      const height = Math.max(0, ...elements.map((el) => el?.offsetHeight ?? 0));
      if (frame.current) frame.current.style.height = `${height + 2 * FRAME_INSET}px`;
      placed.current = next;
    };
    place.current();
  });

  // An entry measured before it was laid out has no width, which would stack
  // the ring in the middle until the next step. The observer is the frame's own
  // window's: the panel's code runs in another realm than the nodes it renders.
  useEffect(() => {
    const Observer = frame.current?.ownerDocument.defaultView?.ResizeObserver;
    if (Observer === undefined) return;
    const observer = new Observer(() => place.current());
    for (const el of entries.current.slice(0, tabs.length)) if (el) observer.observe(el);
    return () => observer.disconnect();
  }, [tabs.length]);

  return (
    <Focusable
      flow-children="geometric"
      data-testid="emulator-carousel"
      ref={frame}
      // `overflow: hidden` still lets the browser scroll the frame to bring a
      // focused entry into view, which would shift the whole ring off the
      // positions it is placed at; the entries are placed, never scrolled to.
      onScroll={(event) => {
        event.currentTarget.scrollLeft = 0;
      }}
      style={{
        position: "relative",
        overflow: "hidden",
        maskImage: "linear-gradient(90deg, transparent, #000 14%, #000 86%, transparent)",
        WebkitMaskImage: "linear-gradient(90deg, transparent, #000 14%, #000 86%, transparent)",
      }}
    >
      {tabs.map((tab, i) => (
        <div
          key={entryKey(tab)}
          ref={(el) => {
            entries.current[i] = el;
          }}
          style={{
            position: "absolute",
            top: `${FRAME_INSET}px`,
            left: "50%",
            outline: entryOutline(tab, i === shown),
            outlineOffset: "-1px",
            borderRadius: "2px",
          }}
          {...(i === shown ? { [SHOWN_ENTRY_ATTR]: "" } : {})}
        >
          <DialogButton style={ENTRY} onClick={() => onSelect(tab)}>
            <span
              data-testid="emulator-entry"
              data-shown={i === shown ? "true" : undefined}
              style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
            >
              <Dot met={tab.requirements_met} size="6px" />
              <span data-testid="emulator-entry-name" style={{ color: nameColour(tab) }}>
                {tabLabel(tab)}
              </span>
            </span>
          </DialogButton>
        </div>
      ))}
    </Focusable>
  );
};

/**
 * The selector, drawn only where the answer names two emulators or more. It is
 * also what the page's L2 and R2 step (`emulatorStepping.tsx`).
 */
export const EmulatorSelector: FC<{
  tabs: readonly EmulatorTab[] | undefined;
  shown: EmulatorTab | null;
  onSelect: (tab: EmulatorTab) => void;
  padding?: string;
  /** A width it is held to and centred within, where the page is wider. */
  maxWidth?: string;
}> = ({ tabs, shown, onSelect, padding = "0", maxWidth }) => {
  const choosable = tabs !== undefined && shown !== null && tabs.length > 1;
  const index = choosable ? Math.max(tabIndex(tabs, shown), 0) : 0;
  const current = choosable ? (tabs[index] ?? shown) : null;
  const step = useCallback(
    (by: number) => {
      if (tabs !== undefined && current !== null) onSelect(stepTab(tabs, current, by));
    },
    [tabs, current, onSelect],
  );
  useStepOnTriggers(choosable ? step : null);
  if (!choosable || current === null) return null;
  return (
    <div
      data-testid="emulator-selector"
      style={{
        padding,
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        ...(maxWidth === undefined ? {} : { maxWidth, margin: "0 auto" }),
      }}
    >
      {/* The shown emulator's dot and name stand on the row's own centre, the
          axis the carousel centres its shown entry on: the side columns are
          equal. */}
      <Focusable
        flow-children="row"
        style={{ display: "grid", gridTemplateColumns: TOP_ROW_COLUMNS, alignItems: "center", columnGap: "6px" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <TriggerGlyph button={GLYPH_TRIGGER_LEFT} label="L2" />
          <DialogButton style={ARROW} aria-label="Previous emulator" onClick={() => step(-1)}>
            ◀
          </DialogButton>
        </div>
        <span
          data-testid="emulator-shown"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            minWidth: 0,
            whiteSpace: "nowrap",
            overflow: "hidden",
          }}
        >
          <Dot met={current.requirements_met} size="8px" />
          <span
            data-testid="emulator-name"
            style={{ overflow: "hidden", textOverflow: "ellipsis", color: nameColour(current) }}
          >
            {tabLabel(current)}
          </span>
        </span>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "6px", minWidth: 0 }}>
          <span data-testid="emulator-position" style={{ color: MUTED, fontSize: SECONDARY_FONT, flexShrink: 0 }}>
            {`${index + 1} / ${tabs.length}`}
          </span>
          <DialogButton style={ARROW} aria-label="Next emulator" onClick={() => step(1)}>
            ▶
          </DialogButton>
          <TriggerGlyph button={GLYPH_TRIGGER_RIGHT} label="R2" />
        </div>
      </Focusable>
      <Carousel tabs={tabs} shown={index} onSelect={onSelect} />
    </div>
  );
};
