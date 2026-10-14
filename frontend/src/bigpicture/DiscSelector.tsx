/**
 * DiscSelector — inline disc picker for multi-disc ROMs (#865).
 *
 * Sits immediately to the right of CustomPlayButton in the play-section row.
 * For a multi-disc install it renders a compact, icon-only trigger whose face
 * IS the badge: a stacked-discs glyph (neutral) for the m3u "all discs" default,
 * or a single disc + number (accent) when a specific disc is pinned. Clicking it
 * opens an anchored `showContextMenu` list of discs. Picking a disc rewrites the
 * Steam shortcut's `launch_options` to that disc's file (emulator-agnostic) and
 * persists the choice in the backend DB, so the Play button always launches the
 * currently-selected disc.
 *
 * Single-disc / unknown / not-installed ROMs render nothing (zero footprint).
 * The picker re-fetches on `download_complete` (a newly installed ROM may now
 * be multi-disc) and hides on `romm_rom_uninstalled`.
 */

import { useState, useEffect, useRef, type FC } from "react";
import { addEventListener, removeEventListener } from "../api/host";
import { Menu, MenuItem, showContextMenu, DialogButton } from "@decky/ui";
import { FaChevronDown } from "react-icons/fa";
import { getCachedGameDetail, logError } from "../api/backend";
import type { DiscSelection } from "../api/backend";
import { getEventTarget } from "../utils/events";
import { detach } from "../utils/detach";
import type { DownloadCompleteEvent } from "../types";
import { mountPruneLeaseOwner, releasePruneLeasesByOwner } from "../utils/pruneLease";
import {
  type DiscOptionData,
  computeDiscDisplayState,
  fetchDiscSelection,
  executeDiscSelection,
} from "../utils/discSelection";
import { DISC_GREY, DISC_ACCENT, DiscStack, DiscWithNumber, buildDiscOptions } from "../shared/DiscGlyphs";

interface DiscSelectorProps {
  appId: number;
}

export const DiscSelector: FC<DiscSelectorProps> = ({ appId }) => {
  const leaseOwner = `disc-selector:${appId}`;
  const [selection, setSelection] = useState<DiscSelection | null>(null);
  // Locally-tracked pin: `selected` echoed by a successful selectDisc. Mirrors
  // the persisted `roms.selected_disc` (null = following the default).
  const [selected, setSelected] = useState<DiscOptionData>(null);
  const romIdRef = useRef<number | null>(null);

  // Resolve rom_id from the cached detail and fetch the disc selection.
  const fetchSelection = async (rid: number): Promise<void> => {
    const result = await fetchDiscSelection(rid, "DiscSelector");
    if (result) {
      setSelection(result);
      setSelected(result.selected ?? null);
    }
  };

  // Initial load: resolve rom_id from cache (instant), then fetch selection.
  useEffect(() => {
    mountPruneLeaseOwner(leaseOwner);
    let cancelled = false;

    async function init() {
      try {
        const cached = await getCachedGameDetail(appId);
        if (cancelled || !cached.found || cached.rom_id == null) return;
        romIdRef.current = cached.rom_id;
        if (!cached.installed) return;
        await fetchSelection(cached.rom_id);
      } catch (e) {
        logError(`DiscSelector init error: ${e}`);
      }
    }

    detach(init());
    return () => {
      cancelled = true;
      detach(releasePruneLeasesByOwner(leaseOwner));
    };
  }, [appId, leaseOwner]);

  // Re-fetch on download_complete (a newly installed ROM may now be multi-disc);
  // hide on uninstall.
  useEffect(() => {
    const completeListener = addEventListener<DownloadCompleteEvent>(
      "download_complete",
      (evt: DownloadCompleteEvent) => {
        if (evt.rom_id !== romIdRef.current) return;
        detach(fetchSelection(evt.rom_id));
      },
    );

    const onUninstall = (e: Event) => {
      const rid = (e as CustomEvent).detail?.rom_id;
      if (rid !== romIdRef.current) return;
      setSelection(null);
      setSelected(null);
    };
    globalThis.addEventListener("romm_rom_uninstalled", onUninstall);

    return () => {
      removeEventListener("download_complete", completeListener);
      globalThis.removeEventListener("romm_rom_uninstalled", onUninstall);
    };
  }, []);

  const handleChange = async (data: DiscOptionData): Promise<void> => {
    const rid = romIdRef.current;
    if (rid == null) return;
    await executeDiscSelection({
      appId,
      romId: rid,
      data,
      leaseOwner,
      onSelected: setSelected,
      logTag: "DiscSelector",
    });
  };

  const displayState = computeDiscDisplayState(selection, selected);
  if (!displayState || !selection) return null;

  const { effectiveSelected, isPinned, showPlaylistFace, activeNum } = displayState;
  const options = buildDiscOptions(selection);

  // A custom compact trigger + showContextMenu for the anchored list. Steam's
  // <Dropdown> renders full-width and clips a custom icon face, so we own the
  // trigger button outright (sized to its content via .romm-disc-btn). The
  // active option is tinted + check-marked in the list.
  const openMenu = (e: MouseEvent): void => {
    showContextMenu(
      <Menu label="Disc">
        {options.map((o) => {
          const active = o.data === effectiveSelected;
          return (
            <MenuItem key={String(o.data)} onClick={() => detach(handleChange(o.data))}>
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "10px",
                  color: active ? DISC_ACCENT : undefined,
                }}
              >
                {o.icon}
                <span>{o.text}</span>
                {active ? <span style={{ marginLeft: "6px", fontWeight: 700 }}>✓</span> : null}
              </span>
            </MenuItem>
          );
        })}
      </Menu>,
      getEventTarget(e),
    );
  };

  return (
    <DialogButton className="romm-disc-btn" onClick={openMenu}>
      {showPlaylistFace ? (
        <DiscStack size={22} color={DISC_GREY} />
      ) : (
        <DiscWithNumber size={22} color={isPinned ? DISC_ACCENT : DISC_GREY} num={activeNum} />
      )}
      <FaChevronDown size={10} color="#cfd3d8" />
    </DialogButton>
  );
};
