/**
 * Which picker entry a stored emulator label belongs to, and what to call it.
 *
 * A label is what an override stores, and one entry can carry several of them
 * (`EmulatorOption.labels`): RetroDECK lists LRPS2 and PCSX2 under one launch
 * command, so a game pinned to "PCSX2" before the two were one entry still
 * reads as "LRPS2 / PCSX2". The grouping itself is the backend's
 * (`options_to_payload`); this only looks a label up in it.
 */

import type { EmulatorOption } from "../types";

/** The entry an override stored under *label* reads as, if any lists it. */
export function entryForLabel(emulators: readonly EmulatorOption[], label: string | null): EmulatorOption | undefined {
  if (label === null) return undefined;
  return emulators.find((entry) => entry.labels.includes(label));
}

/** What a surface calls the emulator stored under *label*: its entry's name, or the label where no entry lists it. */
export function emulatorName(emulators: readonly EmulatorOption[], label: string): string {
  return entryForLabel(emulators, label)?.name ?? label;
}
