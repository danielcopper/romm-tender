/**
 * DiscSelector — inline disc picker for multi-disc ROMs (#865).
 *
 * Sits immediately to the right of CustomPlayButton in the play-section row.
 * For a multi-disc install it renders a compact, icon-only trigger whose face
 * IS the badge: a stacked-discs glyph (neutral) for the m3u "all discs" default,
 * or a single disc + number (accent) when a specific disc is pinned. Clicking it
 * opens an anchored `showContextMenu` list of discs. Picking a disc persists the
 * choice in the backend DB, and every start asks with it, so the Play button
 * always launches the currently-selected disc.
 *
 * Single-disc / unknown / not-installed ROMs render nothing (zero footprint).
 * When its discs are read: `utils/useDiscSelection.ts`.
 */

import type { FC } from "react";
import { Menu, MenuItem, showContextMenu, DialogButton } from "@decky/ui";
import { FaChevronDown } from "react-icons/fa";
import { getEventTarget } from "../utils/events";
import { detach } from "../utils/detach";
import { computeDiscDisplayState } from "../utils/discSelection";
import { useDiscSelection } from "../utils/useDiscSelection";
import { DISC_GREY, DISC_ACCENT, DiscStack, DiscWithNumber, buildDiscOptions } from "../shared/DiscGlyphs";

interface DiscSelectorProps {
  appId: number;
}

export const DiscSelector: FC<DiscSelectorProps> = ({ appId }) => {
  const leaseOwner = `disc-selector:${appId}`;
  const { selection, selected, pickDisc } = useDiscSelection({ appId, leaseOwner, logTag: "DiscSelector" });

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
            <MenuItem key={String(o.data)} onClick={() => detach(pickDisc(o.data))}>
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
