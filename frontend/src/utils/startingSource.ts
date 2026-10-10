/**
 * The switched-off-source probe both launch paths run before starting a ROM.
 *
 * Every game starts through RetroDECK yet, whatever the emulator source switch
 * says, so a start while RetroDECK is switched off would go through a source the
 * user turned off. The backend answers whether it is (`check_start_source`);
 * this is the read the gate blocks on. Apart from `launchGate.ts` for the same
 * reason as `launchTarget.ts`: the gate performs no I/O itself.
 */

import { checkStartSource, logError } from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { boundedOr } from "./withTimeout";

/**
 * Is the source games start through switched off? `context` names the caller in
 * the error log.
 *
 * Fails **open**, as the launch-target probe does: a read that fails lets the
 * start through, because only an answer from the backend establishes that the
 * user switched the source off. A read that gets no answer within
 * {@link LOCAL_CALL_LIMIT_MS} rejects this call with its `TimeoutError`, for the
 * launch gate to answer.
 */
export async function startingSourceSwitchedOff(context: string): Promise<boolean> {
  const answer = await boundedOr(checkStartSource(), LOCAL_CALL_LIMIT_MS, (e) => {
    logError(`${context} start-source check threw (allowing launch): ${e}`);
    return null;
  });
  return answer?.switched_off != null;
}
