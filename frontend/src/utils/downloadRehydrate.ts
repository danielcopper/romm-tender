/**
 * Reading a ROM's download from the backend's queue into the download store, for
 * a game page's Play button.
 */

import { getDownloadQueue, logWarn } from "../api/backend";
import type { DownloadItem } from "../types";
import { getDownloadState, isDownloadUnderWay, updateDownload } from "./downloadStore";

/**
 * Put a ROM's download from the backend's queue into the download store, when it
 * is under way and the store carries none of it yet. An entry the store already
 * holds is left alone: frames keep it current, and this answer may be older than
 * the last frame. A read that fails is logged as a warning. Why a page reads the
 * queue at all: docs/architecture/desktop-dom-architecture.md.
 */
export async function seedDownloadFromQueue(romId: number, tag: string): Promise<void> {
  let downloads: DownloadItem[];
  try {
    ({ downloads } = await getDownloadQueue());
  } catch (e) {
    logWarn(`${tag}: could not read the download queue for romId=${romId}: ${e}`);
    return;
  }
  const entry = downloads.find((d) => d.rom_id === romId);
  if (!isDownloadUnderWay(entry) || getDownloadState().some((d) => d.rom_id === romId)) return;
  updateDownload(entry);
}
