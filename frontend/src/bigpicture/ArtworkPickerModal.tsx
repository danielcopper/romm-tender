/**
 * ArtworkPickerModal — the ARTWORK tab's picker for one slot (#2381 D2).
 *
 * A grid of SteamGridDB's candidates for the slot, in SteamGridDB's order, each labelled with its size; the first tile
 * is "Automatic". The image the slot shows is marked CURRENT and the one Tender's own rule takes today "Tender's pick
 * today". Thumbnails load straight from SteamGridDB's `thumb` URLs, so no candidate passes through the backend; more
 * candidates come a page at a time. The user selects a tile and presses "Use this image"; over a slot set in Steam the
 * picker asks once more before anything is replaced (D13), except for the icon, whose file stays where it is.
 *
 * For a cover without a SteamGridDB game or key the picker offers "Automatic" alone, with the reason why no
 * SteamGridDB cover is listed (D19), and asks SteamGridDB nothing.
 *
 * The picker only answers what was chosen; the tab sets it.
 */

import { FC, useCallback, useEffect, useRef, useState } from "react";
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
import { placeEntryFocus } from "../utils/entryFocus";
import { scrollFocusedToCenter } from "../utils/scrollHelpers";
import { REPLACE_SET_IN_STEAM, SLOT_NAMES, automaticTileLabel, sizeLabel } from "../utils/artworkTabWording";

/** What the user chose for the slot: automatic again, or one SteamGridDB image. */
export type ArtworkPick = { kind: "automatic" } | { kind: "image"; imageId: number };

export interface ArtworkPickerModalProps {
  romId: number;
  row: ArtworkSlotRow;
  gameName: string;
  /** Why only "Automatic" is offered; the picker then lists no SteamGridDB image (#2381 D19). */
  onlyAutomatic?: string | null;
  onPick: (pick: ArtworkPick, replaceSetInSteam: boolean) => void;
  /** Injected by showModal. */
  closeModal?: () => void;
}

const AUTOMATIC = "automatic";

// The grid is four tiles wide. Its first page — Automatic, 14 candidates and
// "Load more" — fills four rows exactly, and each "Load more" adds four rows of
// candidates, so "Load more" always closes the grid at its bottom right.
const FIRST_PAGE = 14;
const PAGE_SIZE = 16;

const TILE_STYLE = {
  display: "flex",
  flexDirection: "column" as const,
  alignItems: "center",
  justifyContent: "center",
  gap: "4px",
  padding: "6px",
  width: "100%",
  height: "100%",
  minHeight: "130px",
  minWidth: "0",
};

/** A tile's classes: dark at rest and focused, ringed in the accent colour on focus and while selected. */
function tileClass(selected: boolean): string {
  return selected ? "romm-artwork-tile romm-artwork-tile-selected" : "romm-artwork-tile";
}

const MARK_STYLE = {
  fontSize: "10px",
  fontWeight: "bold" as const,
  color: "#1a9fff",
  textTransform: "uppercase" as const,
};

export const ArtworkPickerModal: FC<ArtworkPickerModalProps> = ({
  romId,
  row,
  gameName,
  onlyAutomatic = null,
  onPick,
  closeModal,
}) => {
  const [candidates, setCandidates] = useState<ArtworkCandidate[]>([]);
  const [tendersPick, setTendersPick] = useState<number | null>(null);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [shown, setShown] = useState(FIRST_PAGE);
  // The candidate focus goes to once "Load more" has shown it: the first new
  // tile, which takes the place "Load more" stood in.
  const [focusIndex, setFocusIndex] = useState<number | null>(null);
  const grid = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState(onlyAutomatic === null);
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
    if (onlyAutomatic !== null) return;
    let open = true;
    detach(
      listPage(0).then((answer) => {
        if (open) takePage(0, answer);
      }),
    );
    return () => {
      open = false;
    };
  }, [listPage, onlyAutomatic]);

  const loadMore = async () => {
    if (loading) return;
    const wanted = shown + PAGE_SIZE;
    if (wanted > candidates.length && hasMore) {
      setLoading(true);
      takePage(page + 1, await listPage(page + 1));
    }
    setFocusIndex(shown);
    setShown(wanted);
  };
  const moreToShow = shown < candidates.length || hasMore;

  useEffect(() => {
    if (focusIndex === null || !grid.current) return;
    // The grid's tiles in order: Automatic, the candidates shown, "Load more".
    const tile = grid.current.querySelectorAll<HTMLElement>("button")[focusIndex + 1];
    if (tile) placeEntryFocus(grid.current, () => tile);
    setFocusIndex(null);
  }, [focusIndex, shown, candidates.length]);

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
          {onlyAutomatic ?? `SteamGridDB · ${gameName} · in SteamGridDB's order`}
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
            <div ref={grid} style={{ maxHeight: "60vh", overflowY: "auto", padding: "4px" }}>
              <Focusable
                flow-children="right"
                style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "8px" }}
              >
                <DialogButton
                  className={tileClass(selected === AUTOMATIC)}
                  onClick={() => setSelected(AUTOMATIC)}
                  onFocus={scrollFocusedToCenter}
                  style={TILE_STYLE}
                >
                  <div style={{ fontSize: "12px", color: "#fff", textAlign: "center" }}>
                    {automaticTileLabel(row.slot)}
                  </div>
                  {row.state === "automatic" && <div style={MARK_STYLE}>CURRENT</div>}
                </DialogButton>
                {candidates.slice(0, shown).map((candidate) => (
                  <DialogButton
                    key={candidate.image_id}
                    className={tileClass(selected === candidate.image_id)}
                    onClick={() => setSelected(candidate.image_id)}
                    onFocus={scrollFocusedToCenter}
                    style={TILE_STYLE}
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
                    {sizeLabel(candidate.width, candidate.height) && (
                      <div style={{ fontSize: "11px", color: "rgba(255,255,255,0.7)" }}>
                        {sizeLabel(candidate.width, candidate.height)}
                      </div>
                    )}
                    {row.state !== "automatic" && row.image_id === candidate.image_id && (
                      <div style={MARK_STYLE}>CURRENT</div>
                    )}
                    {tendersPick === candidate.image_id && <div style={MARK_STYLE}>Tender&apos;s pick today</div>}
                  </DialogButton>
                ))}
                {(moreToShow || (loading && candidates.length > 0)) && (
                  <DialogButton
                    className={tileClass(false)}
                    onClick={() => {
                      detach(loadMore());
                    }}
                    onFocus={scrollFocusedToCenter}
                    style={TILE_STYLE}
                  >
                    {loading ? (
                      <div style={{ width: "32px", height: "32px" }}>
                        <Spinner />
                      </div>
                    ) : (
                      <div style={{ fontSize: "12px", color: "#fff", textAlign: "center" }}>Load more</div>
                    )}
                  </DialogButton>
                )}
              </Focusable>
              {loading && candidates.length === 0 && (
                <div style={{ display: "flex", justifyContent: "center", paddingTop: "8px" }}>
                  <div style={{ width: "32px", height: "32px" }}>
                    <Spinner />
                  </div>
                </div>
              )}
              {failure && <div style={{ fontSize: "12px", color: "#ff8800" }}>{failure}</div>}
            </div>
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
