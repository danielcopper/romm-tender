/**
 * The table pieces the Sync page's two tables are built from — the preview's
 * change counts and the run's units.
 *
 * They are here rather than in `qam/pane.tsx` because only this page has two
 * tables that have to read as one family: both are set in the flat, small
 * register a plan of seventeen units needs to fit the Deck's column, and one
 * place holding it is what keeps that a decision rather than a drift.
 *
 * **Every row is a focus stop.** A region scrolls only by moving focus, so a row
 * nothing can focus is a row nothing can scroll to — and these rows carry no
 * control of their own, so the activate handler is the only thing that makes the
 * `Focusable` a stop rather than a container that passes focus through.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Sync.
 */

import type { CSSProperties, FC, ReactNode } from "react";
import { Focusable } from "@decky/ui";
import { MUTED, SECONDARY_FONT } from "../qam/pane";

/** The rule under a table header and above a total row — Steam's own hairline
 *  weight, the one the panel already separates its blocks with. */
export const TABLE_LINE = "1px solid rgba(255, 255, 255, 0.12)";

/** The pane's horizontal gutter, matching `SectionTitle` and `Muted` so a table
 *  lines up with the section it sits under. */
const GUTTER = "16px";

/**
 * The register both tables are set in — flat enough that a plan of seventeen
 * units fits the column under the whole-run bar, and the same on both so the
 * preview and the run read as one family. The type size and leading are the
 * layout study's `.tbl.compact` (`docs/assets/sync-layouts.html`); the
 * horizontal padding is the pane's own gutter rather than the study's 6 px, so a
 * row lines up with the section title over it.
 */
const ROW_PADDING = `2px ${GUTTER}`;
const ROW_FONT = "12px";
const ROW_LINE_HEIGHT = 1.25;

/**
 * Every cell clips rather than overflows.
 *
 * A grid track sized `minmax(0, 1fr)` shrinks under its content, and the content
 * then spills across the track beside it — on the Deck a platform name ran into
 * the New column's digit. The clip belongs on the CELL rather than on whatever a
 * caller puts inside it: a grid item is blockified, so `text-overflow` applies
 * to it, where an inline `span` nested in it is not and the same three
 * properties do nothing at all. Callers hand the full string to a `title`.
 */
const CELL_BASE: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const numericStyle: CSSProperties = { ...CELL_BASE, textAlign: "right", fontVariantNumeric: "tabular-nums" };

function cellStyle(index: number, numericFrom: number): CSSProperties {
  return index >= numericFrom ? numericStyle : CELL_BASE;
}

/**
 * Column names over a table. Plain text: they accompany the rows below them and
 * scroll with them, and making the header a focus stop would add a step that
 * leads nowhere. `ScrollRegion` reveals them when focus reaches the first row.
 */
export const TableHeader: FC<{ columns: string; cells: string[]; numericFrom: number }> = ({
  columns,
  cells,
  numericFrom,
}) => (
  <div
    style={{
      display: "grid",
      gridTemplateColumns: columns,
      gap: "8px",
      padding: `0 ${GUTTER} 3px`,
      borderBottom: TABLE_LINE,
      fontSize: SECONDARY_FONT,
      color: MUTED,
    }}
  >
    {cells.map((cell, index) => (
      <span key={cell} style={cellStyle(index, numericFrom)}>
        {cell}
      </span>
    ))}
  </div>
);

/**
 * One row of a table, and a focus stop.
 *
 * *subline* is the full-width line under the cells, for what a 60px column
 * cannot hold — the collection names behind a count, the error a run ended with.
 * It is inside the row rather than beside it, so it travels with the focus
 * highlight instead of being stranded between two stops.
 */
export const TableRow: FC<{
  columns: string;
  cells: ReactNode[];
  numericFrom: number;
  subline?: string | undefined;
  style?: CSSProperties | undefined;
  testId?: string | undefined;
}> = ({ columns, cells, numericFrom, subline, style, testId }) => (
  <Focusable
    onActivate={() => {}}
    data-testid={testId}
    style={{
      padding: ROW_PADDING,
      fontSize: ROW_FONT,
      lineHeight: ROW_LINE_HEIGHT,
      ...style,
    }}
  >
    <div style={{ display: "grid", gridTemplateColumns: columns, gap: "8px", alignItems: "center" }}>
      {cells.map((cell, index) => (
        // The index IS the identity here: a cell is the column it sits in, and
        // the columns of one table never reorder.
        <span key={index} style={cellStyle(index, numericFrom)}>
          {cell}
        </span>
      ))}
    </div>
    {subline !== undefined && (
      <div
        title={subline}
        style={{
          fontSize: SECONDARY_FONT,
          color: MUTED,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {subline}
      </div>
    )}
  </Focusable>
);

/** A full-width line inside the pane's gutter that is not a table row — a
 *  summary sentence under a table, an estimate. Plain text, so it rides along
 *  with the stops around it rather than adding one of its own. */
export const PaneRow: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ padding: `2px ${GUTTER} 4px` }}>{children}</div>
);

/** The thin bar a running row draws under its status, and the whole-run bar's
 *  smaller twin. Drawn here rather than with Steam's `ProgressBar` because the
 *  height is the point: 4px is what keeps a table of fourteen units in the
 *  column at the Deck's height. */
export const InlineBar: FC<{ fraction: number }> = ({ fraction }) => (
  <div
    data-testid="unit-bar"
    style={{ height: "4px", borderRadius: "2px", background: "rgba(255, 255, 255, 0.12)", marginTop: "3px" }}
  >
    <div
      style={{
        height: "100%",
        borderRadius: "2px",
        width: `${Math.max(0, Math.min(100, fraction * 100))}%`,
        background: "#1a9fff",
      }}
    />
  </div>
);
