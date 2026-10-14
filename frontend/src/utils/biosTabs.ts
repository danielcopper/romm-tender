/**
 * Which rows a BIOS table shows for each emulator the selector offers.
 *
 * The backend answers one entry per emulator the resolver answered for, the
 * launching emulator first. The launching emulator's rows are the payload's own
 * `files` that it declares, and every other emulator's entry carries its own
 * requirements. Nothing is folded across emulators, and choosing an emulator
 * never changes which emulator launches. The library files no emulator here
 * declares are no emulator's rows: they stand in a section of their own, the
 * same whichever emulator is shown ({@link isUnclaimedLibraryRow}).
 */

import type { EmulatorTab, FirmwareWanted } from "../types/firmware";
import { biosColorForLevel } from "./biosColor";

/**
 * Is *row* a RomM library file no emulator here declares — one whose need is
 * the reading's answer (`not_needed`, `unknown`) rather than a declaration's?
 */
export function isUnclaimedLibraryRow(row: { declared_by?: string | null; wanted?: FirmwareWanted }): boolean {
  return (row.declared_by ?? null) === null && (row.wanted === "not_needed" || row.wanted === "unknown");
}

/** The rows of the selected emulator, or every declared row where the payload carries no entries. */
export function tabRows<Row extends { declared_by?: string | null; wanted?: FirmwareWanted }>(
  files: readonly Row[],
  tabs: readonly EmulatorTab[] | undefined,
  selected: EmulatorTab | null,
): readonly (Row | NonNullable<EmulatorTab["rows"]>[number])[] {
  const declared = files.filter((row) => !isUnclaimedLibraryRow(row));
  if (!tabs || tabs.length === 0 || selected === null) return declared;
  if (selected.launching) {
    const launching = selected.emulator;
    return declared.filter((row) => (row.declared_by ?? null) === null || row.declared_by === launching);
  }
  return selected.rows ?? [];
}

/** The entry a surface shows: the one *key* names where it still exists, else the launching emulator's. */
export function selectedTab(tabs: readonly EmulatorTab[] | undefined, key: string | null): EmulatorTab | null {
  if (!tabs || tabs.length === 0) return null;
  return (
    tabs.find((tab) => !tab.launching && tab.emulator === key) ?? tabs.find((tab) => tab.launching) ?? tabs[0] ?? null
  );
}

/**
 * What an entry is called: its emulator's label, or its identity where the
 * picker names none. `null` only for an entry with neither, which the backend
 * never sends: an emulator nothing identifies has no entry, rather than one
 * under a made-up name.
 */
export function tabLabel(tab: EmulatorTab): string | null {
  if (tab.label) return tab.label;
  if (tab.emulator) return tab.emulator.replace(/\.so$/, "").replace(/_libretro$/, "");
  return null;
}

/** The dot an entry carries: its own emulator's verdict, in the colours every BIOS level uses. */
export function tabDotColor(met: boolean | null): string {
  if (met === true) return biosColorForLevel("ok");
  return biosColorForLevel(met === false ? "missing" : "unknown");
}

/** Where *tab* stands among *tabs*: matched by role and identity, never by object, so a fresh answer finds it. */
export function tabIndex(tabs: readonly EmulatorTab[], tab: EmulatorTab): number {
  return tabs.findIndex((entry) => entry.launching === tab.launching && entry.emulator === tab.emulator);
}

/** The entry *by* steps from *tab*, wrapping around at either end. */
export function stepTab(tabs: readonly EmulatorTab[], tab: EmulatorTab, by: number): EmulatorTab {
  const count = tabs.length;
  const from = Math.max(tabIndex(tabs, tab), 0);
  return tabs[(((from + by) % count) + count) % count] ?? tab;
}
