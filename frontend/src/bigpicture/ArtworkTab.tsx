/**
 * ArtworkTab — the ARTWORK pane of the RomM game detail panel (#2381 D2, D3).
 *
 * The ROM's SteamGridDB game on top — RomM's, the user's choice, or none yet — and the shortcut's five slots under it,
 * each a row that can take focus: Steam's own copy of its image (D14), its name, where the image comes from and its
 * state. Pressing a row opens the picker; what the user picks is downloaded, recorded and set here, under the
 * artwork lease and the newest-apply-wins guard (`utils/artwork`). Without a SteamGridDB game the rows that need one
 * say so and the cover's says it comes from RomM; the cover's row still opens a picker that offers "Automatic" alone
 * (D19). "Choose game…" / "Change game…" opens the game picker.
 */

import { FC, useEffect, useState } from "react";
import { DialogButton, Focusable, showModal } from "@decky/ui";
import {
  chooseArtworkSlotImage,
  isEndpointFailure,
  setArtworkSlotAutomatic,
  debugLog,
  type ArtworkSlotRow,
  type ArtworkSlots,
  type ArtworkTabSlot,
} from "../api/backend";
import { applyTabSlotImage, type TabSlotOutcome } from "../utils/artwork";
import {
  artworkThumbToken,
  keepArtworkTabOpen,
  keptArtworkSlot,
  loadArtworkSlots,
  noteArtworkWritten,
  useArtworkSlots,
} from "../utils/artworkSlotsStore";
import { slotThumbnailUrls } from "../utils/artworkThumbs";
import {
  NO_API_KEY,
  SLOT_NAMES,
  SOURCE_LINES,
  STATE_CHIPS,
  coverOnlyAutomatic,
  gameLine,
  sourceLine,
} from "../utils/artworkTabWording";
import { detach } from "../utils/detach";
import { scrollFocusedToCenter } from "../utils/scrollHelpers";
import { showToast } from "../utils/toast";
import { showArtworkPicker, type ArtworkPick } from "./ArtworkPickerModal";
import { SgdbGamePickerModalContent } from "./SgdbGamePickerModal";

interface ArtworkTabProps {
  appId: number;
  romId: number;
  romName: string;
}

/** Each slot's thumbnail box, in the slot's own aspect. */
const THUMB_SIZES: Readonly<Record<ArtworkTabSlot, { width: number; height: number }>> = {
  cover: { width: 48, height: 72 },
  grid: { width: 120, height: 56 },
  hero: { width: 150, height: 48 },
  logo: { width: 120, height: 52 },
  icon: { width: 48, height: 48 },
};

const OUTCOME_TOASTS: Readonly<Record<Exclude<TabSlotOutcome, "left">, string>> = {
  set: "Artwork updated",
  refused: "Steam did not take the image",
  none: SOURCE_LINES.none,
};

/** Steam's copy of a slot's image: the first URL that loads, else a placeholder box. */
const SlotThumb: FC<{ urls: string[]; slot: ArtworkTabSlot }> = ({ urls, slot }) => {
  const [failed, setFailed] = useState(0);
  const size = THUMB_SIZES[slot];
  const url = urls[failed];
  const box = { width: `${size.width}px`, height: `${size.height}px`, borderRadius: "3px", flexShrink: 0 };
  if (!url)
    return <div className="romm-artwork-thumb-empty" style={{ ...box, background: "rgba(255,255,255,0.08)" }} />;
  return (
    <img
      src={url}
      alt=""
      onError={() => setFailed((n) => n + 1)}
      style={{ ...box, objectFit: slot === "logo" ? "contain" : "cover" }}
    />
  );
};

/** Whether a press on a row opens the picker: a SteamGridDB game and a key are needed to list candidates. */
function canPick(slots: ArtworkSlots): boolean {
  return slots.has_api_key && slots.game.sgdb_id !== null;
}

/** Whether *row*'s press opens a picker: every row where candidates can be listed, and the cover's always (D19). */
function opensPicker(slots: ArtworkSlots, row: ArtworkSlotRow): boolean {
  return canPick(slots) || row.slot === "cover";
}

export const ArtworkTab: FC<ArtworkTabProps> = ({ appId, romId, romName }) => {
  const held = useArtworkSlots(appId, romId);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // The tab always reads afresh on opening: an image set through Steam since
    // the last read changes a row's state, and only the read sees it.
    detach(loadArtworkSlots(appId, romId));
  }, [appId, romId]);

  const slots = held?.slots ?? null;

  const setPicked = async (row: ArtworkSlotRow, pick: ArtworkPick, replaceSetInSteam: boolean) => {
    if (busy) return;
    setBusy(true);
    keepArtworkTabOpen(appId, row.slot);
    try {
      const answer = await (pick.kind === "automatic"
        ? setArtworkSlotAutomatic(romId, row.slot)
        : chooseArtworkSlotImage(romId, row.slot, pick.imageId));
      if (isEndpointFailure(answer)) {
        showToast(answer.message);
        return;
      }
      // The backend holds the new state once it answered: read it now, since
      // Steam's write below may re-render the page before it returns.
      detach(loadArtworkSlots(appId, romId));
      const outcome = await applyTabSlotImage(romId, appId, row.slot, answer, replaceSetInSteam);
      if (outcome !== "left") showToast(OUTCOME_TOASTS[outcome]);
      if (row.slot === "cover") {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "cover_refreshed", rom_id: romId } }),
        );
      }
    } catch (e) {
      detach(debugLog(`ArtworkTab: setting the ${row.slot} failed: ${e}`));
      showToast("The image could not be set");
    } finally {
      setBusy(false);
      keepArtworkTabOpen(appId, row.slot);
      noteArtworkWritten(appId);
      detach(loadArtworkSlots(appId, romId));
    }
  };

  const openPicker = (row: ArtworkSlotRow) => {
    if (!slots || !opensPicker(slots, row) || busy) return;
    showArtworkPicker({
      romId,
      row,
      gameName: slots.game.name ?? `#${slots.game.sgdb_id}`,
      onlyAutomatic: canPick(slots) ? null : coverOnlyAutomatic(slots),
      onPick: (pick, replaceSetInSteam) => {
        detach(setPicked(row, pick, replaceSetInSteam));
      },
    });
  };

  const openGamePicker = (current: ArtworkSlots) => {
    const coverWasChosen = current.slots.some((row) => row.slot === "cover" && row.state === "chosen");
    showModal(
      <SgdbGamePickerModalContent
        romId={romId}
        appId={appId}
        romName={romName}
        discardsChoices={current.slots.some((row) => row.has_choice)}
        onApplied={() => {
          // A change of game keeps a chosen cover's choice (D12): setting the
          // cover back to automatic here reloads RomM's cover and sets it in Steam.
          if (coverWasChosen) {
            const cover = current.slots.find((row) => row.slot === "cover");
            if (cover) detach(setPicked({ ...cover, state: "automatic" }, { kind: "automatic" }, false));
            return;
          }
          detach(loadArtworkSlots(appId, romId));
        }}
      />,
    );
  };

  if (!slots) {
    return (
      <div className="romm-panel-loading" style={{ padding: "12px 0" }}>
        {held?.failure ?? "Loading artwork…"}
      </div>
    );
  }

  const line = gameLine(slots.game);
  const token = artworkThumbToken(appId);
  const keptSlot = keptArtworkSlot(appId);
  return (
    <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <Focusable
        flow-children="right"
        className={`romm-artwork-game${line.notice ? " romm-artwork-game-notice" : ""}`}
        style={{ display: "flex", alignItems: "center", gap: "12px" }}
      >
        <DialogButton className="romm-artwork-game-text" onFocus={scrollFocusedToCenter} style={{ flex: 1 }}>
          <div style={{ fontSize: "14px", color: line.notice ? "#ffb74d" : "#fff" }}>{line.text}</div>
          {line.explanation && (
            <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>{line.explanation}</div>
          )}
          {!slots.has_api_key && <div style={{ fontSize: "12px", color: "#ffb74d" }}>{NO_API_KEY}</div>}
        </DialogButton>
        {line.button && slots.has_api_key && (
          <DialogButton onClick={() => openGamePicker(slots)} style={{ width: "auto", minWidth: "140px" }}>
            {line.button}
          </DialogButton>
        )}
      </Focusable>
      {slots.slots.map((row) => {
        const urls = slotThumbnailUrls(appId, row, token);
        return (
          <DialogButton
            key={row.slot}
            className="romm-artwork-row"
            preferredFocus={keptSlot === row.slot}
            onClick={() => openPicker(row)}
            onFocus={scrollFocusedToCenter}
            style={{ display: "flex", alignItems: "center", gap: "12px", textAlign: "left" }}
          >
            <SlotThumb key={urls.join("|")} urls={urls} slot={row.slot} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: "14px", color: "#fff" }}>{SLOT_NAMES[row.slot]}</div>
              <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>{sourceLine(row.slot, row.source)}</div>
            </div>
            <div className={`romm-artwork-chip romm-artwork-chip-${row.state}`}>{STATE_CHIPS[row.state]}</div>
            {opensPicker(slots, row) && <div className="romm-artwork-choose">Choose…</div>}
          </DialogButton>
        );
      })}
    </Focusable>
  );
};
