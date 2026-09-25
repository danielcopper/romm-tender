/**
 * What the Sync page's two tables — the preview's change counts and the run's
 * units — add to the pane's own table.
 *
 * The table itself is `layout/pane.tsx`'s, and so is the compact register both
 * of this page's tables are set in ({@link COMPACT_TABLE_REGISTER}). What stays
 * here is what is this page's alone: the numeric-column split both tables take,
 * and the pieces that are not tables at all.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Sync.
 */

import type { CSSProperties, FC, ReactNode } from "react";
import {
  COMPACT_TABLE_REGISTER,
  MUTED,
  PANE_GUTTER,
  PaneTableHeader,
  PaneTableRow,
  SECONDARY_FONT,
  type TableCell,
} from "../layout/pane";

/** The rule this page draws over a total row, which is the pane's own — passed
 *  straight through so a caller reaches it beside the table it belongs to. */
export { TABLE_LINE } from "../layout/pane";

/** The columns from `numericFrom` rightwards hold numbers, which are read down
 *  the column rather than across the row: right-aligned so their digits line up,
 *  and tabular so they do not shift as the values change. */
const numericStyle: CSSProperties = { textAlign: "right", fontVariantNumeric: "tabular-nums" };

const splitNumeric = (cells: readonly ReactNode[], numericFrom: number): TableCell[] =>
  cells.map((content, index) => (index >= numericFrom ? { content, style: numericStyle } : { content }));

/** Column names over one of this page's tables. */
export const TableHeader: FC<{ columns: string; cells: string[]; numericFrom: number }> = ({
  columns,
  cells,
  numericFrom,
}) => <PaneTableHeader columns={columns} cells={splitNumeric(cells, numericFrom)} register={COMPACT_TABLE_REGISTER} />;

/**
 * One row of one of this page's tables.
 *
 * *subline* is the full-width line under the cells, for what a 60px column
 * cannot hold — the collection names behind a count, the error a run ended with.
 */
export const TableRow: FC<{
  columns: string;
  cells: ReactNode[];
  numericFrom: number;
  subline?: string | undefined;
  style?: CSSProperties | undefined;
  testId?: string | undefined;
}> = ({ columns, cells, numericFrom, subline, style, testId }) => (
  <PaneTableRow
    columns={columns}
    cells={splitNumeric(cells, numericFrom)}
    register={COMPACT_TABLE_REGISTER}
    {...(style === undefined ? {} : { style })}
    {...(testId === undefined ? {} : { testId })}
  >
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
  </PaneTableRow>
);

/** A full-width line inside the pane's gutter that is not a table row — a
 *  summary sentence under a table, an estimate. Plain text, so it rides along
 *  with the stops around it rather than adding one of its own. */
export const PaneRow: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ padding: `2px ${PANE_GUTTER} 4px` }}>{children}</div>
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
