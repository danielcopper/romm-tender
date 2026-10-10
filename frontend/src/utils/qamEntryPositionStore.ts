/**
 * Where Tender's Quick Access entry sits beside Decky Loader's, as the render
 * handler in `qam/installEntry.tsx` reads it on every pass of the menu.
 *
 * The handler runs inside Steam's render and cannot wait for the backend, so
 * it reads this synchronously, and until the backend has answered it reads the
 * default. `settings.json` is the value's only home: this holds what the
 * backend last said, nothing writes it anywhere else, and a change reaches the
 * strip at the menu's next render pass rather than by forcing one.
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
