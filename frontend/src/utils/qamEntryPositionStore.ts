/**
 * Where Tender's Quick Access entry sits beside Decky Loader's, as the render
 * handler in `qam/installEntry.tsx` reads it synchronously on every pass of the
 * menu. What it holds before the backend answers, and why it is no second home
 * of the value: `docs/architecture/qam-panel.md` § The entry.
 *
 * Updated by:
 *   - panel load (`fetchQamEntryPositionState`, in `index.tsx`)
 *   - the Settings page, once the backend has accepted a change
 */

import { getSettings } from "../api/backend";
import type { QamEntryPosition } from "../types";

const DEFAULT_POSITION: QamEntryPosition = "above_decky";

let _position: QamEntryPosition = DEFAULT_POSITION;

export function getQamEntryPositionState(): QamEntryPosition {
  return _position;
}

export function setQamEntryPositionState(position: QamEntryPosition): void {
  _position = position;
}

/** Fill the store from the backend's settings. */
export async function fetchQamEntryPositionState(): Promise<void> {
  const settings = await getSettings();
  setQamEntryPositionState(settings.qam_entry_position ?? DEFAULT_POSITION);
}
