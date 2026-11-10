/**
 * The `core_changed` event: an emulator choice changed for one RomM platform,
 * and the game pages of that platform read their core again (#2452 D16).
 */

import type { RommDataChangedDetail } from "../types/events";

/**
 * Announce that an emulator choice changed — a platform's pick or one game's
 * own — for the platform kept in *platformFolder*. A game RomM gives no
 * fs_slug has none: `null`, `undefined` and `""` are all sent as `null`, and
 * the pages of such games match one another.
 */
export function announceCoreChange(platformFolder: string | null | undefined): void {
  const detail: RommDataChangedDetail = { type: "core_changed", platform_fs_slug: platformFolder || null };
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail }));
}

/** Whether a `core_changed` event is for the page of a game on the platform kept in *platformFolder*. */
export function coreChangeIsFor(
  detail: Extract<RommDataChangedDetail, { type: "core_changed" }>,
  platformFolder: string | null | undefined,
): boolean {
  return detail.platform_fs_slug === (platformFolder || null);
}
