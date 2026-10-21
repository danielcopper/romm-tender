/**
 * ArtworkPickerModal — the ARTWORK tab's picker for one slot (#2381 D2).
 *
 * A grid of SteamGridDB's candidates for the slot, in SteamGridDB's order, each labelled with its size; the first tile
 * is "Automatic". The image the slot shows is marked CURRENT and the one Tender's own rule takes today "Tender's pick
 * today". Thumbnails load straight from SteamGridDB's `thumb` URLs, so no candidate passes through the backend; more
 * candidates come a page at a time. The user selects a tile and presses "Use this image"; over a slot set in Steam the
 * picker asks once more before anything is replaced (D13), except for the icon, whose file stays where it is.
 *
 * The picker only answers what was chosen; the tab sets it.
 */

import { FC, useCallback, useEffect, useState } from "react";
import { DialogButton, Focusable, ModalRoot, Spinner, showModal } from "@decky/ui";
import {
  getArtworkSlotCandidates,
  isEndpointFailure,
  debugLog,
  type ArtworkCandidate,
  type ArtworkCandidatePage,
  type ArtworkSlotRow,
  type EndpointFailure,
} from "../api/backend";
import { detach } from "../utils/detach";
import { scrollFocusedToCenter } from "../utils/scrollHelpers";
import { REPLACE_SET_IN_STEAM, SLOT_NAMES, automaticTileLabel, sizeLabel } from "../utils/artworkTabWording";

/** What the user chose for the slot: automatic again, or one SteamGridDB image; and whether to replace Steam's. */
export type ArtworkPick = { kind: "automatic" } | { kind: "image"; imageId: number };

export interface ArtworkPickerModalProps {
  romId: number;
  row: ArtworkSlotRow;
  gameName: string;
  onPick: (pick: ArtworkPick, replaceSetInSteam: boolean) => void;
  /** Injected by showModal. */
  closeModal?: () => void;
}

const AUTOMATIC = "automatic";

const TILE_STYLE = {
  display: "flex",
  flexDirection: "column" as const,
  alignItems: "center",
  gap: "4px",
  padding: "6px",
  width: "100%",
  height: "auto",
  minWidth: "0",
};

const MARK_STYLE = {
  fontSize: "10px",
  fontWeight: "bold" as const,
  color: "#1a9fff",
  textTransform: "uppercase" as const,
};

export const ArtworkPickerModal: FC<ArtworkPickerModalProps> = ({ romId, row, gameName, onPick, closeModal }) => {
  const [candidates, setCandidates] = useState<ArtworkCandidate[]>([]);
  const [tendersPick, setTendersPick] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | typeof AUTOMATIC | null>(null);
  const [confirming, setConfirming] = useState(false);

  const listPage = useCallback(
    (next: number): Promise<ArtworkCandidatePage | EndpointFailure | null> =>
      getArtworkSlotCandidates(romId, row.slot, next).catch((e: unknown) => {
        detach(debugLog(`ArtworkPickerModal: the ${row.slot} candidates could not be listed: ${e}`));
        return null;
      }),
    [romId, row.slot],
  );

  const takePage = (next: number, answer: ArtworkCandidatePage | EndpointFailure | null) => {
    setLoading(false);
    if (!answer || isEndpointFailure(answer)) {
      setFailure(answer?.message ?? "SteamGridDB's images could not be listed.");
      return;
    }
    setCandidates((held) => (next === 0 ? answer.candidates : [...held, ...answer.candidates]));
    if (next === 0) setTendersPick(answer.tenders_pick);
    setHasMore(answer.has_more);
    setPage(next);
  };

  useEffect(() => {
    let open = true;
    detach(
      listPage(0).then((answer) => {
        if (open) takePage(0, answer);
      }),
    );
    return () => {
      open = false;
    };
  }, [listPage]);

  const loadMore = async () => {
    setLoading(true);
    takePage(page + 1, await listPage(page + 1));
  };

  const pick = (): ArtworkPick | null => {
    if (selected === AUTOMATIC) return { kind: "automatic" };
    return selected === null ? null : { kind: "image", imageId: selected };
  };

  const finish = (replaceSetInSteam: boolean) => {
    const chosen = pick();
    if (!chosen) return;
    closeModal?.();
    onPick(chosen, replaceSetInSteam);
  };

  const use = () => {
    if (row.state === "set_in_steam" && row.slot !== "icon") {
      setConfirming(true);
      return;
    }
    finish(false);
  };

  const name = SLOT_NAMES[row.slot];
  return (
    <ModalRoot {...(closeModal !== undefined ? { closeModal } : {})}>
      <div style={{ padding: "16px", minWidth: "560px" }}>
        <div style={{ fontSize: "16px", fontWeight: "bold", color: "#fff" }}>{`${name} — choose an image`}</div>
        <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)", marginBottom: "12px" }}>
          {`SteamGridDB · ${gameName} · in SteamGridDB's order`}
        </div>
        {confirming ? (
          <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <div style={{ fontSize: "13px", color: "#ff8a80" }}>{REPLACE_SET_IN_STEAM}</div>
            <DialogButton onClick={() => finish(true)}>Replace</DialogButton>
            <DialogButton onClick={() => setConfirming(false)} style={{ opacity: 0.5 }}>
              Cancel
            </DialogButton>
          </Focusable>
        ) : (
          <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <Focusable
              flow-children="right"
              style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "8px" }}
            >
              <DialogButton
                onClick={() => setSelected(AUTOMATIC)}
                onFocus={scrollFocusedToCenter}
                style={{ ...TILE_STYLE, outline: selected === AUTOMATIC ? "2px solid #1a9fff" : "none" }}
              >
                <div style={{ fontSize: "12px", color: "#fff", textAlign: "center" }}>
                  {automaticTileLabel(row.slot)}
                </div>
                {row.state === "automatic" && <div style={MARK_STYLE}>CURRENT</div>}
              </DialogButton>
              {candidates.map((candidate) => (
                <DialogButton
                  key={candidate.image_id}
                  onClick={() => setSelected(candidate.image_id)}
                  onFocus={scrollFocusedToCenter}
                  style={{ ...TILE_STYLE, outline: selected === candidate.image_id ? "2px solid #1a9fff" : "none" }}
                >
                  {candidate.thumb ? (
                    <img
                      src={candidate.thumb}
                      alt={sizeLabel(candidate.width, candidate.height)}
                      style={{ width: "100%", height: "90px", objectFit: "contain" }}
                    />
                  ) : (
                    <div style={{ height: "90px", fontSize: "11px", color: "rgba(255,255,255,0.5)" }}>No preview</div>
                  )}
                  <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.7)" }}>
                    {sizeLabel(candidate.width, candidate.height)}
                  </div>
                  {row.state !== "automatic" && row.image_id === candidate.image_id && (
                    <div style={MARK_STYLE}>CURRENT</div>
                  )}
                  {tendersPick === candidate.image_id && <div style={MARK_STYLE}>Tender&apos;s pick today</div>}
                </DialogButton>
              ))}
            </Focusable>
            {loading && (
              <div style={{ display: "flex", justifyContent: "center" }}>
                <div style={{ width: "32px", height: "32px" }}>
                  <Spinner />
                </div>
              </div>
            )}
            {failure && <div style={{ fontSize: "12px", color: "#ff8800" }}>{failure}</div>}
            {hasMore && !loading && (
              <DialogButton
                onClick={() => {
                  detach(loadMore());
                }}
              >
                Load more
              </DialogButton>
            )}
            <Focusable flow-children="right" style={{ display: "flex", gap: "8px" }}>
              <DialogButton onClick={() => closeModal?.()} style={{ opacity: 0.5 }}>
                Cancel
              </DialogButton>
              <DialogButton onClick={use} disabled={selected === null}>
                Use this image
              </DialogButton>
            </Focusable>
          </Focusable>
        )}
      </div>
    </ModalRoot>
  );
};

/** Open the picker for *row*; *onPick* hears what was chosen, and nothing when the picker is dismissed. */
export function showArtworkPicker(props: Omit<ArtworkPickerModalProps, "closeModal">): void {
  showModal(<ArtworkPickerModal {...props} />);
}
