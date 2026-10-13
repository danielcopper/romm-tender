/**
 * The words a surface prints for the emulator a platform or a game launches
 * with — the Platforms header's core clause and the game page's BIOS tab under
 * its `Emulator` heading. One derivation, so the two never read a payload two
 * ways. Why a `null` label is two states rather than one, and which of them the
 * games start in, is `docs/architecture/qam-panel.md`, "The clause names the
 * core".
 */

import type { SystemCoreInfo } from "../types";

/** What the clause reads, and whether it names a state the games cannot start
 *  in — the one a surface draws in red. */
export interface EmulatorClause {
  text: string;
  gamesCannotStart: boolean;
}

/** The part of a core answer the clause reads, which both `SystemCoreInfo` and
 *  `CoreInfo` carry. */
export type EmulatorClauseSource = Pick<SystemCoreInfo, "emulator_data_available" | "emulators" | "active_core_label">;

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
  const noEmulator = core.emulators.length === 0;
  // The fallback RetroDECK would use is missing, so the clause says that rather
  // than naming a core nothing can run. Same shape as the empty menu: a state
  // the games do not start in.
  const fallbackMissing = core.active_core_label === null && core.emulators[0]?.reason === "not_installed";
  return {
    text: core.active_core_label ?? fallbackText(noEmulator, fallbackMissing),
    gamesCannotStart: noEmulator || fallbackMissing,
  };
}

/** Which of the three unpinnable states the answer is in, said in the fewest
 *  words the state supports. */
function fallbackText(noEmulator: boolean, fallbackMissing: boolean): string {
  if (noEmulator) return "no emulator";
  return fallbackMissing ? "no emulator installed" : "RetroDECK decides";
}
