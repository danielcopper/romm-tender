/**
 * The words the Platforms header prints for the emulator a platform launches
 * with — its core clause. What a `null` label means, and which states the games
 * cannot start in, is `docs/architecture/qam-panel.md`, "The clause names the
 * core".
 */

import type { SystemCoreInfo } from "../types";
import { startRefused } from "./emulatorStart";

/** What the clause reads, and whether it names a state the games cannot start
 *  in — the one a surface draws in red. */
export interface EmulatorClause {
  text: string;
  gamesCannotStart: boolean;
}

/** The part of a core answer the clause reads. */
export type EmulatorClauseSource = Pick<
  SystemCoreInfo,
  "emulator_data_available" | "emulators" | "active_core_label" | "start"
>;

/**
 * The clause for one core answer, or `null` where nothing can be said.
 *
 * Everything the clause says rests on the emulator list having been READ.
 * `get_emulator_options` answers `available: false` with an EMPTY list when the
 * answering source gives no emulator list, so a clause keyed on the list's
 * length alone said "no emulator" over a state where nothing was established —
 * beside a sentence saying why the list was not established. So no answer, or
 * an unread list, is no clause at all.
 */
export function emulatorClause(core: EmulatorClauseSource | null): EmulatorClause | null {
  if (!core?.emulator_data_available) return null;
  if (core.emulators.length === 0) return { text: "no emulator", gamesCannotStart: true };
  if (core.active_core_label === null) return { text: "no default", gamesCannotStart: true };
  return { text: core.active_core_label, gamesCannotStart: startRefused(core.start) };
}
