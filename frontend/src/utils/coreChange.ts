/**
 * Whether a game's core changed since its last launch, and asking about it before
 * the launch goes on.
 */

import { checkCoreChange } from "../api/backend";
import { LOCAL_CALL_LIMIT_MS } from "./launchGate";
import { boundedOr } from "./withTimeout";

export type CoreChangePrompt = (oldLabel: string, newLabel: string) => Promise<boolean>;

/**
 * Checks for a core change on the specified ROM. If changed, invokes `prompt`
 * with human-readable labels (or core IDs if labels are absent).
 *
 * Returns `true` to proceed with launch, or `false` if the user cancelled.
 * If the check fails, hands the error to `onError` and assumes unchanged (`true`); a
 * check that gets no answer within `LOCAL_CALL_LIMIT_MS` throws instead, for
 * the launch gate to answer.
 */
export async function confirmCoreChangeIfNeeded(
  romId: number,
  prompt: CoreChangePrompt,
  onError?: (e: unknown) => void,
): Promise<boolean> {
  const coreCheck = await boundedOr(
    checkCoreChange(romId),
    LOCAL_CALL_LIMIT_MS,
    (e): { changed: boolean; old_core?: string; new_core?: string; old_label?: string; new_label?: string } => {
      onError?.(e);
      return { changed: false };
    },
  );
  if (!coreCheck.changed) return true;
  return prompt(
    coreCheck.old_label ?? coreCheck.old_core ?? "Unknown",
    coreCheck.new_label ?? coreCheck.new_core ?? "Unknown",
  );
}
