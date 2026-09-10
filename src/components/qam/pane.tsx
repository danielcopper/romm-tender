/**
 * The pieces a wide page's detail pane is built from: the type scale it sets
 * secondary lines in, the colours it says things with, the two button shapes,
 * the table every page with more than two facts per row draws, and the small
 * components every pane repeats — a section title, a muted line, a row of
 * buttons, and the two lines that report an action.
 *
 * They are here rather than on a page because the next pane is written against
 * the same scale: a second literal for the same size is how two panes drift
 * apart, and a reader moving between them reads the drift as meaning.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`.
 */

import type { CSSProperties, FC, ReactElement, ReactNode } from "react";
import { Focusable } from "@decky/ui";

/** The size every secondary LINE on a pane is set in: a header's counts clause,
 *  the under-row description and note lines, a table cell beside them, the muted
 *  sentences, the table header and the legend. One constant, because the device
 *  pass asked for the cell to match those lines and a second literal is how they
 *  drift apart again. Button labels are not lines and keep their own sizes. */
export const SECONDARY_FONT = "11px";

export const MUTED = "#8f98a0";
export const RED = "#d94126";
export const GREEN = "#5ba32b";
export const AMBER = "#d4a72c";
/** The BIOS table's verdict mark for a file that is present and that the core
 *  the platform launches with does not require — green's quieter twin, so
 *  "there and needed" and "there and spare" are one glance apart rather than one
 *  reading apart. */
export const PALE_GREEN = "#8fc46b";
/** The BIOS table's library mark, deliberately outside the verdict palette's
 *  traffic light: what it reports — that the RomM library does not hold the
 *  file — is not a degree of wrongness. */
export const VIOLET = "#a48fd4";

/**
 * The bar down the left edge of the selected row of a list, and the gap between
 * it and the row's content — together, how far every row of a list-and-detail
 * page is inset from its column's edge.
 *
 * Here rather than on a page because every such list has to be inset by the same
 * pair, and two lists that differ read as two kinds of list. A list header spans
 * exactly what a row spans, so {@link ROW_CONTENT_INSET} is its left padding:
 * happy-dom lays nothing out, so a drift between a list's rows and its header —
 * or between one page's list and another's — is invisible to every test here.
 */
export const ROW_MARKER_WIDTH = 3;
export const ROW_MARKER_GAP = 5;
export const ROW_CONTENT_INSET = ROW_MARKER_WIDTH + ROW_MARKER_GAP;

/** What the marker is drawn in while its row is the selected one. Outside the
 *  verdict palette above: it reports where the reader is, not how anything is. */
export const SELECTION_ACCENT = "#1a9fff";

/** The horizontal gutter a pane's content sits in — what `SectionTitle` and
 *  `Muted` are padded by, so a table lines up with the section it sits under. */
export const PANE_GUTTER = "16px";

/** The rule under a table header and above a total row — Steam's own hairline
 *  weight, the one the panel already separates its blocks with. */
export const TABLE_LINE = "1px solid rgba(255, 255, 255, 0.12)";

/**
 * How tightly a page sets its table — the one thing the three tables genuinely
 * differ in, and therefore a value a page passes rather than a reason to write
 * a second table.
 *
 * The Sync page is the one that needs its own: a plan of seventeen units has to
 * fit the column under the whole-run bar, so its rows are flatter and smaller
 * than a pane's default type, and both of its tables take the same one so the
 * preview and the run read as one family.
 */
export interface TableRegister {
  /** Padding on the row wrapper, gutter included. */
  rowPadding: string;
  /** Padding on the header, gutter included — its bottom is the air between the
   *  column names and the first row. */
  headerPadding: string;
  /** Set where a page wants a tighter type than the pane's own; left off, a row
   *  inherits the pane's. */
  rowFont?: CSSProperties["fontSize"];
  rowLineHeight?: CSSProperties["lineHeight"];
  /** A hairline under the column names. */
  rule?: boolean;
}

/** What a table is set in unless a page says otherwise. */
export const PANE_TABLE_REGISTER: TableRegister = {
  rowPadding: `4px ${PANE_GUTTER}`,
  headerPadding: `0 ${PANE_GUTTER} 4px`,
};

/**
 * The three properties that make a cell clip rather than spill across the track
 * beside it, plus the floor reset that lets it shrink at all.
 *
 * A grid track sized `minmax(0, 1fr)` shrinks under its content and the content
 * then spills sideways — on the Deck a platform name ran into the New column's
 * digit. They belong on the grid ITEM, which is blockified, so `text-overflow`
 * applies to it where an inline `span` nested inside it is not and the same
 * three do nothing at all. What the clip takes away is handed back in a `title`.
 */
export const CELL_CLIP: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

/** One cell of a table row or header. */
export interface TableCell {
  content: ReactNode;
  /** Merged over the clip, so a cell can be right-aligned, muted, or made a flex
   *  container without restating it. */
  style?: CSSProperties;
  /** What the clip took away, for the mouse. */
  title?: string;
  /**
   * Off for a cell whose content is not a run of text — a row of glyphs, a
   * button. There is nothing to ellipsise, and hidden overflow would cut the
   * focus ring off a control.
   */
  clip?: boolean;
}

const asCell = (cell: string | TableCell): TableCell => (typeof cell === "string" ? { content: cell } : cell);

const cellStyle = (cell: TableCell): CSSProperties => ({ ...(cell.clip === false ? {} : CELL_CLIP), ...cell.style });

// The gap between columns is the same on every table here and is not a knob: two
// tables whose columns breathe differently read as two kinds of table.
const COLUMN_GAP = "8px";

const Cells: FC<{ cells: readonly TableCell[] }> = ({ cells }) => (
  <>
    {cells.map((cell, index) => (
      <span
        // The index IS the identity: a cell is the column it sits in. A table's
        // columns are fixed by the `columns` declaration it is drawn from — they
        // never reorder, none is inserted or removed while the table is mounted,
        // and a cell is a `span` holding no state, no ref and no uncontrolled
        // input. So there is nothing a stable key would preserve that this does
        // not, and content changing in place is what an index key handles best.
        //
        // A caller-supplied key was weighed and is not better HERE: it would be
        // a real name for the two tables whose cells have one, and `col-${index}`
        // for the Sync page's, which passes its cells positionally beside a
        // `numericFrom` split — the same information, laundered into a string.
        // Any of those three conditions changing is what makes this wrong again.
        key={index} // NOSONAR(typescript:S6479) — fixed, non-reordering columns of stateless cells; see above.
        style={cellStyle(cell)}
        {...(cell.title === undefined ? {} : { title: cell.title })}
      >
        {cell.content}
      </span>
    ))}
  </>
);

/**
 * Column names over a table.
 *
 * Plain text, and never a focus stop: the names accompany the rows below them
 * and scroll with them, so a stop here would add a step that leads nowhere.
 * `ScrollRegion` reveals them when focus reaches the first row.
 */
export const PaneTableHeader: FC<{
  columns: string;
  cells: readonly (string | TableCell)[];
  register?: TableRegister;
  testId?: string;
}> = ({ columns, cells, register = PANE_TABLE_REGISTER, testId }) => (
  <div
    {...(testId === undefined ? {} : { "data-testid": testId })}
    style={{
      display: "grid",
      gridTemplateColumns: columns,
      gap: COLUMN_GAP,
      padding: register.headerPadding,
      ...(register.rule ? { borderBottom: TABLE_LINE } : {}),
      fontSize: SECONDARY_FONT,
      color: MUTED,
    }}
  >
    <Cells cells={cells.map(asCell)} />
  </div>
);

/**
 * One row of a table, and — unless one of its own cells carries a control — a
 * focus stop.
 *
 * **A region scrolls only by moving focus**, so a row nothing can focus is a row
 * nothing can scroll to. A row whose cells carry no control of their own has
 * nothing else that could hold that focus, so the activate handler is what makes
 * the `Focusable` a stop rather than a container that passes focus through to
 * children it does not have.
 *
 * `focusStop={false}` is for the other case, and it is not an exemption from
 * that rule: a row that DOES carry a control is already reachable through it,
 * and a stop on the wrapper as well would put a dead step in front of every one
 * of those controls.
 *
 * `children` are rendered inside the row and under its cells — the line a
 * narrow column cannot hold, the note under a name. Inside rather than beside,
 * so it travels with the focus highlight instead of being stranded between two
 * stops.
 */
export const PaneTableRow: FC<{
  columns: string;
  cells: readonly TableCell[];
  register?: TableRegister;
  style?: CSSProperties;
  testId?: string;
  focusStop?: boolean;
  children?: ReactNode;
}> = ({ columns, cells, register = PANE_TABLE_REGISTER, style, testId, focusStop = true, children }) => {
  const body = (
    <>
      <div style={{ display: "grid", gridTemplateColumns: columns, gap: COLUMN_GAP, alignItems: "center" }}>
        <Cells cells={cells} />
      </div>
      {children}
    </>
  );
  const wrapperStyle: CSSProperties = {
    padding: register.rowPadding,
    ...(register.rowFont === undefined ? {} : { fontSize: register.rowFont }),
    ...(register.rowLineHeight === undefined ? {} : { lineHeight: register.rowLineHeight }),
    ...style,
  };
  const marker = testId === undefined ? {} : { "data-testid": testId };
  return focusStop ? (
    <Focusable onActivate={() => {}} style={wrapperStyle} {...marker}>
      {body}
    </Focusable>
  ) : (
    <div style={wrapperStyle} {...marker}>
      {body}
    </div>
  );
};

/**
 * The padding a `DialogButton` is given wherever a pane puts buttons in a row.
 *
 * `ButtonItem` — the full-width control most of the panel uses — takes no style
 * or class of its own: its props are `ItemProps`, which has neither
 * (`@decky/ui/dist/components/Item.d.ts`), so its height is Steam's and cannot
 * be argued with from here. `DialogButton` does take `style`
 * (`DialogButtonProps extends DialogCommonProps`, `Dialog.d.ts`), and is Steam's
 * own button component rather than a lookalike of one.
 *
 * They are the same button, and the difference is the row around it. In
 * `chunk~2dcc5aaf7.js` module 12316, the `forwardRef` decky's prop-list regex
 * matches (`highlightOnFocus` then `childrenContainerWidth`) renders a `Field`
 * whose first child is a second `forwardRef`, and that one renders `o.$n`;
 * module 64608 re-exports `$n` from module 44351, where it is the `forwardRef`
 * whose className is `"DialogButton","_DialogLayout","Secondary"` — the exact
 * string `@decky/ui` searches for to bind its own `DialogButton`
 * (`components/Dialog.js`, `DialogButton = DialogButtonSecondary`). So
 * `ButtonItem` IS a `Field` wrapped around this component, and what a `Field`
 * costs is the row's own padding: 10px top and bottom inside the QAM, where it
 * renders in its `Classic` mode.
 */
export const FLAT_BUTTON = { flex: "1 1 auto", minWidth: 0, padding: "6px 10px", fontSize: "13px" } as const;

/** The button in a table row's action column. Narrow because the column is
 *  sized for it and the name beside it is the thing worth width: 4px of
 *  horizontal padding on the BIOS table's 92px action column still leaves a
 *  target wider than it is tall, which is what keeps it pressable at the Deck's
 *  scale. */
export const ROW_BUTTON = { width: "100%", minWidth: 0, padding: "4px", fontSize: "11px" } as const;

export const SectionTitle: FC<{ title: string; note?: string; noteColor?: string }> = ({ title, note, noteColor }) => (
  <div style={{ display: "flex", alignItems: "baseline", gap: "8px", padding: "12px 16px 4px" }}>
    <span style={{ fontSize: "12px", fontWeight: 600, letterSpacing: "0.5px", color: "#dcdedf" }}>
      {title.toUpperCase()}
    </span>
    {note && <span style={{ fontSize: SECONDARY_FONT, color: noteColor ?? MUTED }}>{note}</span>}
  </div>
);

export const Muted: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ fontSize: SECONDARY_FONT, color: MUTED, padding: "0 16px 6px" }}>{children}</div>
);

/**
 * A row of side-by-side buttons, crossed horizontally by the stick.
 *
 * The padding is the caller's because it is the row's place on the pane rather
 * than the row's own shape — how much air it needs above and below depends on
 * what it sits between.
 */
export const ButtonRow: FC<{ padding: string; children: ReactNode }> = ({ padding, children }) => (
  <Focusable flow-children="horizontal" style={{ display: "flex", gap: "8px", padding }}>
    {children}
  </Focusable>
);

/**
 * A line reporting what an action did, bound to the entry it was produced for
 * and the group of the pane it belongs under.
 *
 * `key` is the pane's own identity — a platform slug, a section id — and `scope`
 * the group. Both halves matter: a failed core switch must not be reported under
 * Remove, and an action's result must not follow the reader onto the next
 * entry's pane.
 *
 * A page names its own set of groups as `Scope`, so the literals it writes at
 * each call site are checked against that set. Defaulting to `string` keeps a
 * page that has only one group from having to declare one.
 */
export interface ScopedStatus<Scope extends string = string> {
  key: string;
  scope: Scope;
  text: string;
}

/**
 * A generic function rather than an `FC`, because `FC` takes no type parameter.
 *
 * `Scope` is inferred from `status` alone and `scope` is only checked against
 * it, which is what `NoInfer` buys: with both as inference sites a literal
 * naming no group of the page's set is simply a second candidate, `Scope` widens
 * to the union of the two, and the typo passes. Catching it is the whole point
 * of the parameter, because a scope matching nothing renders no line and says
 * nothing about why.
 */
export function GroupStatus<Scope extends string>({
  status,
  forKey,
  scope,
}: {
  status: ScopedStatus<Scope> | null;
  forKey: string;
  scope: NoInfer<Scope>;
}): ReactElement | null {
  return status?.key === forKey && status.scope === scope ? (
    <div data-testid={`status-${scope}`} style={{ fontSize: "12px", color: "#dcdedf", padding: "0 16px 8px" }}>
      {status.text}
    </div>
  ) : null;
}

/**
 * Why this pane's buttons are dead while nothing on it is running.
 *
 * A page that holds one status line, one progress and one busy key runs one
 * action at a time across every entry: a second would clobber the first's line
 * and the first `finally` would clear the busy state under the second. The line
 * that would explain the wait — {@link GroupStatus} — is bound to the entry the
 * action belongs to, so walking away from a running action leaves a pane full of
 * disabled buttons and nothing said. This is what it says.
 *
 * `busyName` is what the page calls the entry that is working, and is required:
 * a page that cannot name it has to choose the words the reader sees rather than
 * inherit them from here.
 */
export const BusyElsewhere: FC<{ busyKey: string | null; ownKey: string; busyName: string }> = ({
  busyKey,
  ownKey,
  busyName,
}) => {
  if (busyKey === null || busyKey === ownKey) return null;
  return <Muted>{`Working on ${busyName} — actions here are paused until it finishes.`}</Muted>;
};
