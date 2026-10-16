/**
 * Shared BIOS status-dot color mapping. The level is a single backend decision
 * (`domain/bios_status.py::compute_bios_level`) — the resolver's verdict for the
 * launching emulator — and every surface that renders it maps it to a colour
 * through this one helper so the colours never drift apart.
 *
 * The play row's BIOS badge is not such a surface. It is a warning with one
 * appearance, raised by a red verdict, so it takes {@link BIOS_MISSING_RED}
 * directly.
 *
 * Per-surface phrasing (the verbose label strings) stays in each component —
 * only the colour mapping is shared here.
 */

import type { BiosLevel } from "../types";

/** The red a requirement the resolver says is not met is drawn in. Named
 *  because one surface needs the colour WITHOUT the level: the play row's badge
 *  has a single appearance and asks {@link biosColorForLevel} nothing. Sharing
 *  the spelling is what keeps that badge and the tab's red dot the same red. */
export const BIOS_MISSING_RED = "#d94126";

/** Map a backend BIOS level to the status-dot hex color.
 *  - `ok` (met) → green
 *  - `partial` (open only because the disc's region decides it) → amber
 *  - `missing` (not met) → red
 *  - `unknown` (not established) → neutral grey
 *  - `null` (no level data) → neutral grey */
export function biosColorForLevel(level: BiosLevel | null): string {
  switch (level) {
    case "ok":
      return "#5ba32b";
    case "partial":
      return "#d4a72c";
    case "missing":
      return BIOS_MISSING_RED;
    case "unknown":
      return "#8f98a0";
    default:
      return "#8f98a0";
  }
}
