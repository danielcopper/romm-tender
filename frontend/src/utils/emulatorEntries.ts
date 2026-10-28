/**
 * Which picker entry a stored emulator label names. Every entry is a row of
 * its own and a label is unique within one system's list, so the lookup is by
 * label alone (`options_to_payload`).
 */

import type { EmulatorOption } from "../types";

/** The entry *label* names, if any lists it. */
export function entryForLabel(emulators: readonly EmulatorOption[], label: string | null): EmulatorOption | undefined {
  if (label === null) return undefined;
  return emulators.find((entry) => entry.label === label);
}
