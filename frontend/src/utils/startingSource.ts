/**
 * The switched-off-source probe both launch paths run before starting a ROM.
 *
 * A shortcut starts its game through its source whatever the emulator source
 * switch says, so a start while that source is switched off would go through a
 * source the user turned off. The backend names such a source
 * (`check_start_source`); this is the read the gate blocks on. Apart from
 * `launchGate.ts` for the same reason as `launchTarget.ts`: the gate performs
 * no I/O itself.
 */

import { checkStartSource, logError } from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import type { StartingSourceAnswer } from "./launchGate";
import { withTimeout } from "./withTimeout";

/**
 * What the backend says of the source that would start the game. `context`
 * names the caller in the error log.
 *
 * Unchecked — never on or off — wherever the backend did not say: a read that
 * gets no answer within {@link LOCAL_CALL_LIMIT_MS}, one that fails, and the
 * backend's own refusal when detecting the sources failed. Reading any of
 * them as "switched on" would start a game through a source that may be
 * switched off, and as "switched off" would refuse one that may not be.
 */
export async function readStartingSource(context: string): Promise<StartingSourceAnswer> {
  // Read as `unknown`: an answer outside the typed shapes must read as
  // unchecked rather than throw, which the gate would take as a pass.
  let answer: unknown;
  try {
    answer = await withTimeout(checkStartSource(), LOCAL_CALL_LIMIT_MS);
  } catch (e) {
    logError(`${context} start-source check got no answer: ${e}`);
    return { checked: false };
  }
  const switchedOff = switchedOffIn(answer);
  if (switchedOff === undefined) {
    logError(`${context} start-source check could not tell: ${JSON.stringify(answer)}`);
    return { checked: false };
  }
  return { checked: true, switchedOff };
}

/** The `switched_off` of an answer in the endpoint's answer shape; `undefined` for every other answer. */
function switchedOffIn(answer: unknown): string | null | undefined {
  if (typeof answer !== "object" || answer === null || !("switched_off" in answer)) return undefined;
  const value = answer.switched_off;
  return value === null || typeof value === "string" ? value : undefined;
}
