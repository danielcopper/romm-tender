/**
 * The disc selection of the version bound to a game's shortcut, for both
 * surfaces' disc selectors: read when the selector mounts, again when a
 * download of that version completes (it may now be multi-disc) and when a
 * switch binds another version, dropped when it is uninstalled.
 *
 * Every answer is bound to the ROM it was asked for and lands only while that
 * ROM is the one bound (`docs/architecture/invariants/panel.md`, the entry on
 * per-rom detail state).
 */

import { useEffect, useRef, useState } from "react";
import { addEventListener, removeEventListener } from "../api/host";
import { getCachedGameDetail, logError } from "../api/backend";
import type { DiscSelection } from "../api/backend";
import type { DownloadCompleteEvent } from "../types";
import type { RommDataChangedDetail, RommRomUninstalledDetail } from "../types/events";
import { detach } from "./detach";
import { mountPruneLeaseOwner, releasePruneLeasesByOwner } from "./pruneLease";
import { type DiscOptionData, executeDiscSelection, fetchDiscSelection } from "./discSelection";

export interface UseDiscSelectionParams {
  appId: number;
  /** The prune lease owner the selector's reads and picks are admitted under. */
  leaseOwner: string;
  logTag: string;
}

export interface DiscSelectionState {
  /** The bound version's discs as last read; `null` before a read lands, and for a version not downloaded. */
  selection: DiscSelection | null;
  /** The pinned disc, as read and as a pick echoed it; `null` follows the default. */
  selected: DiscOptionData;
  /** Pin *data* on the bound version; nothing happens while no version is bound. */
  pickDisc: (data: DiscOptionData) => Promise<void>;
}

export function useDiscSelection({ appId, leaseOwner, logTag }: UseDiscSelectionParams): DiscSelectionState {
  const [selection, setSelection] = useState<DiscSelection | null>(null);
  const [selected, setSelected] = useState<DiscOptionData>(null);
  const romIdRef = useRef<number | null>(null);

  useEffect(() => {
    mountPruneLeaseOwner(leaseOwner);
    let cancelled = false;

    const fetchSelection = async (rid: number): Promise<void> => {
      const result = await fetchDiscSelection(rid, logTag);
      // A switch while it was asked binds another ROM, whose discs these are not.
      if (cancelled || !result || rid !== romIdRef.current) return;
      setSelection(result);
      setSelected(result.selected ?? null);
    };

    // Only the newest detail read binds: an older one landing late names the
    // version a switch has since left.
    let initSeq = 0;
    const init = async (): Promise<void> => {
      const seq = ++initSeq;
      try {
        const cached = await getCachedGameDetail(appId);
        if (cancelled || seq !== initSeq || !cached.found || cached.rom_id == null) return;
        romIdRef.current = cached.rom_id;
        if (!cached.installed) return;
        await fetchSelection(cached.rom_id);
      } catch (e) {
        logError(`${logTag}: init error: ${e}`);
      }
    };
    detach(init());

    const completeListener = addEventListener<DownloadCompleteEvent>("download_complete", (evt) => {
      if (evt.rom_id === romIdRef.current) detach(fetchSelection(evt.rom_id));
    });
    const onUninstall = (e: Event) => {
      if ((e as CustomEvent<RommRomUninstalledDetail>).detail.rom_id !== romIdRef.current) return;
      setSelection(null);
      setSelected(null);
    };
    globalThis.addEventListener("romm_rom_uninstalled", onUninstall);
    const onSwitched = (e: Event) => {
      const detail = (e as CustomEvent<RommDataChangedDetail>).detail;
      if (detail.type !== "version_switched" || detail.app_id !== appId) return;
      // The old version's discs are not the new one's, also where the read below fails.
      romIdRef.current = null;
      setSelection(null);
      setSelected(null);
      detach(init());
    };
    globalThis.addEventListener("romm_data_changed", onSwitched);

    return () => {
      cancelled = true;
      detach(releasePruneLeasesByOwner(leaseOwner));
      removeEventListener("download_complete", completeListener);
      globalThis.removeEventListener("romm_rom_uninstalled", onUninstall);
      globalThis.removeEventListener("romm_data_changed", onSwitched);
    };
  }, [appId, leaseOwner, logTag]);

  const pickDisc = async (data: DiscOptionData): Promise<void> => {
    const rid = romIdRef.current;
    if (rid == null) return;
    await executeDiscSelection({
      romId: rid,
      data,
      leaseOwner,
      // A switch while the pick was asked binds another ROM, whose pin this is not.
      onSelected: (pin) => {
        if (romIdRef.current === rid) setSelected(pin);
      },
      logTag,
    });
  };

  return { selection, selected, pickDisc };
}
