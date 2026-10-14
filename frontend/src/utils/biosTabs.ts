/**
 * Which rows a BIOS table shows under each emulator's tab (#2190).
 *
 * The backend answers one tab per emulator the resolver answered for, the
 * launching emulator first. The launching tab's rows are the payload's own
 * `files` — its requirements and every library file no emulator claims — and
 * every other tab carries its emulator's own requirements. Nothing is folded
 * across emulators, and choosing a tab never changes which emulator launches:
 * the header, the counts, the badge and the downloads stay the launching one's.
 */

import type { EmulatorTab } from "../types/firmware";
import { biosColorForLevel } from "./biosColor";

/** The rows of the selected tab, or every row where the payload carries no tabs. */
export function tabRows<Row extends { declared_by?: string | null }>(
  files: readonly Row[],
  tabs: readonly EmulatorTab[] | undefined,
  selected: EmulatorTab | null,
): readonly (Row | NonNullable<EmulatorTab["rows"]>[number])[] {
  if (!tabs || tabs.length === 0 || selected === null) return files;
  if (selected.launching) {
    const launching = selected.emulator;
    return files.filter(
      (row) => row.declared_by === undefined || row.declared_by === null || row.declared_by === launching,
    );
  }
  return selected.rows ?? [];
}

/** The tab a surface shows: the one *key* names where it still exists, else the launching emulator's. */
export function selectedTab(tabs: readonly EmulatorTab[] | undefined, key: string | null): EmulatorTab | null {
  if (!tabs || tabs.length === 0) return null;
  return (
    tabs.find((tab) => !tab.launching && tab.emulator === key) ?? tabs.find((tab) => tab.launching) ?? tabs[0] ?? null
  );
}

/** What a tab is called: its emulator's label, or its identity where the picker names none. */
export function tabLabel(tab: EmulatorTab): string {
  if (tab.label) return tab.label;
  if (tab.emulator) return tab.emulator.replace(/\.so$/, "").replace(/_libretro$/, "");
  return "Emulator";
}

/** The dot a tab carries: its own emulator's verdict, in the colours every BIOS level uses. */
export function tabDotColor(met: boolean | null): string {
  if (met === true) return biosColorForLevel("ok");
  return biosColorForLevel(met === false ? "missing" : "unknown");
}
