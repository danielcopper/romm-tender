/**
 * The Data Management page's rows: which populations it lists, in which order,
 * and what each is called.
 *
 * A row names a POPULATION — a set of things this device holds — and never an
 * operation; the verb lives on the button in the detail pane, which is what
 * lets a row exist that offers no action at all. The order is flat and
 * ungrouped: six rows that each name a thing are their own order.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Data
 * Management, and CONTEXT.md → Inventory row (Data Management).
 */

export type DataRowId = "shortcuts" | "rom-files" | "grid-images" | "non-steam" | "removed-games" | "recovery-bundles";

export interface DataRow {
  id: DataRowId;
  label: string;
}

// A tuple rather than an array: the page opens on the first row, and a tuple
// is what makes that first element typed as present.
export const DATA_ROWS = [
  { id: "shortcuts", label: "Tender's shortcuts" },
  { id: "rom-files", label: "Installed ROMs" },
  { id: "grid-images", label: "Grid images" },
  { id: "non-steam", label: "Other non-Steam games" },
  { id: "removed-games", label: "Gone from RomM" },
  { id: "recovery-bundles", label: "Recovery bundles" },
] as const satisfies readonly DataRow[];

/**
 * The list hands its ids back as plain strings; this is where one becomes a row
 * again — by lookup rather than by assertion, so an id no row answers to opens
 * the first row instead of typing as one that is absent.
 */
export const asDataRowId = (id: string | null): DataRowId =>
  DATA_ROWS.find((row) => row.id === id)?.id ?? DATA_ROWS[0].id;
