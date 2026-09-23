/**
 * The Data Management page: an inventory of what this device holds.
 *
 * Six rows, flat and ungrouped, each naming a population with its count; the
 * focused row's pane says what that population is, the numbers about it, and
 * offers what can be done with it. The rows are NOT the operations — the verb
 * lives on the button in the pane, which is what lets Recovery bundles be a row
 * that offers nothing at all.
 *
 * The rows carry no control of their own, so `selectOnActivate` is what makes
 * each one a focus stop; without it a reader could not reach a row, and a
 * region scrolls only by focus moving into it.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Data
 * Management.
 */

import { useState, type FC } from "react";
import { Field, Spinner } from "@decky/ui";
import { WidePage } from "./layout/WidePage";
import { ListDetail, type ListDetailItem } from "./layout/ListDetail";
import { LoadingRow } from "./LoadingRow";
import { MUTED, ROW_MARKER_GAP, ROW_MARKER_WIDTH, SECONDARY_FONT, SELECTION_ACCENT } from "./layout/pane";
import { DataDetail } from "./data/DataDetail";
import { DATA_ROWS, asDataRowId, type DataRowId } from "./data/rows";
import { useDataPage, type DataPageState, type PageRead, type ScannedCount } from "./data/useDataPage";

/**
 * What a row's count slot says: a figure, `—` for a read that failed, or `null`
 * while the read is still in flight, which the slot draws as a spinner.
 *
 * A figure that costs a round trip or a backend scan reads `scan` until the
 * reader asks for it, because focus selects on this layout: a figure fetched on
 * selection would put that round trip under every row the stick passes. From
 * the press on it is read like any other figure. Grid images keeps what its
 * scan found for the rest of the visit; Gone from RomM keeps it until a cleanup
 * run finishes, which is what makes the number wrong, and then reads `scan`
 * again.
 *
 * The dash is not a zero, and no row prints one for an emptiness.
 */
function rowCount(id: DataRowId, state: DataPageState): string | null {
  switch (id) {
    case "shortcuts":
      return figure(state.shortcutCount, (count) => count);
    case "rom-files":
      return figure(state.inventory, (inventory) => inventory.installed_roms);
    case "grid-images":
      return scanned(state.orphanedGridImages);
    case "non-steam":
      return figure(state.foreignApps, (apps) => apps.length);
    case "removed-games":
      return scanned(state.removedGames);
    case "recovery-bundles":
      return figure(state.inventory, (inventory) => inventory.recovery_bundles);
  }
}

function figure<T>(read: PageRead<T>, count: (value: T) => number): string | null {
  if (read.state === "reading") return null;
  if (read.state === "failed") return "—";
  return `${count(read.value)}`;
}

function scanned(read: ScannedCount): string | null {
  return read.state === "not-asked" ? "scan" : figure(read, (count) => count);
}

const RowLabel: FC<{ label: string; count: string | null; selected: boolean }> = ({ label, count, selected }) => (
  <span style={{ display: "flex", alignItems: "baseline", gap: "8px", width: "100%" }}>
    <span
      style={{
        flex: "1 1 auto",
        minWidth: 0,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        fontWeight: selected ? 600 : 400,
      }}
    >
      {label}
    </span>
    <span style={{ flex: "0 0 auto", fontSize: SECONDARY_FONT, color: MUTED }}>
      {/* `1em` of the slot's own font: the spinner's SVG fills whatever it is
          not held to, and the count it stands in for is set in that size. */}
      {count === null ? <Spinner width="1em" height="1em" /> : count}
    </span>
  </span>
);

export const DataManagementPage: FC<{ onBack: () => void }> = ({ onBack }) => {
  const state = useDataPage();
  const [selectedRow, setSelectedRow] = useState<DataRowId>(DATA_ROWS[0].id);

  const items: ListDetailItem[] = DATA_ROWS.map((row) => ({
    id: row.id,
    render: (selected: boolean) => (
      <div
        data-testid={`data-row-${row.id}`}
        style={{
          borderLeft: `${ROW_MARKER_WIDTH}px solid ${selected ? SELECTION_ACCENT : "transparent"}`,
          paddingLeft: `${ROW_MARKER_GAP}px`,
        }}
      >
        <Field
          label={<RowLabel label={row.label} count={rowCount(row.id, state)} selected={selected} />}
          bottomSeparator="none"
        />
      </div>
    ),
  }));

  // One line for the whole list rather than one per pane: a bulk removal
  // disables every removal button on every pane, and the reader who walked away
  // from the pane that started it would otherwise see a page of dead buttons
  // with nothing said. It names the running operation in that operation's own
  // verb, so the line, the pane's status and its button say one word for one
  // thing.
  const listHeader = state.busy ? (
    <LoadingRow
      label={
        state.removalProgress
          ? `${state.busyLabel}: ${state.removalProgress.removed} of ${state.removalProgress.total}...`
          : `${state.busyLabel}...`
      }
    />
  ) : undefined;

  return (
    <WidePage title="Data Management" onBack={onBack} ownRegions>
      <ListDetail
        items={items}
        {...(listHeader === undefined ? {} : { listHeader })}
        selectedId={selectedRow}
        onSelect={(id) => setSelectedRow(asDataRowId(id))}
        selectOnActivate
        renderDetail={(id) => <DataDetail rowId={asDataRowId(id)} state={state} />}
      />
    </WidePage>
  );
};
