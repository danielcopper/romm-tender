/**
 * Tender's folder as one page holds it: the state read on mount, and every
 * change the settings section and Main's notices make to it. Each change
 * answers the state as it now stands, which replaces the one held; a refusal
 * keeps the state and holds the sentence it is shown with until the next
 * change.
 */

import { useEffect, useState } from "react";
import {
  checkTenderPlace,
  chooseTenderPlace,
  forgetTenderFolderRecords,
  getTenderFolder,
  isEndpointFailure,
  logError,
  switchToRecordedTenderFolder,
} from "../api/backend";
import type { TenderFolderRefusal, TenderFolderState, TenderPlaceRefusal } from "../types";
import { detach } from "../utils/detach";
import { placeRefusalSentence } from "../utils/tenderFolderWording";
import { showCreateMissingModal, showForgetRecordedModal, showTenderNamedModal } from "./settings/TenderFolderModals";

export interface TenderFolderView {
  /** `undefined` while the read is in flight, `null` where it failed. */
  state: TenderFolderState | null | undefined;
  /** A change is in flight; a second one is refused until it ends. */
  busy: boolean;
  /** Why the last change was refused, as shown; `null` after one that was not. */
  refusal: string | null;
  chooseDefault: () => void;
  chooseDrive: (mountPoint: string) => void;
  chooseTyped: (text: string) => void;
  switchToRecorded: (folder: string) => void;
  forget: (folder: string) => void;
}

type Answer = TenderFolderState | TenderPlaceRefusal | TenderFolderRefusal;

export function useTenderFolder(): TenderFolderView {
  const [state, setState] = useState<TenderFolderState | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    detach(
      getTenderFolder()
        .then((answer) => {
          if (alive) setState(answer);
        })
        .catch((e: unknown) => {
          logError(`Failed to read Tender's folder: ${e}`);
          if (alive) setState(null);
        }),
    );
    return () => {
      alive = false;
    };
  }, []);

  const run = (change: () => Promise<Answer | null>) => {
    if (busy) return;
    setBusy(true);
    detach(
      (async () => {
        try {
          const answer = await change();
          if (answer === null) return;
          if (isEndpointFailure(answer)) {
            setRefusal(placeRefusalSentence(answer));
          } else {
            setRefusal(null);
            setState(answer);
          }
        } catch (e) {
          logError(`Failed to change Tender's folder: ${e}`);
        } finally {
          setBusy(false);
        }
      })(),
    );
  };

  /** The questions a typed place is asked before it is chosen; `null` where the user chose nothing. */
  const chooseAfterAsking = async (text: string): Promise<Answer | null> => {
    const asks = await checkTenderPlace(text);
    if (isEndpointFailure(asks)) return asks;
    let place = asks.place;
    let missing = asks;
    if (asks.tender_parent !== null) {
      const answer = await showTenderNamedModal(asks.place, asks.tender_parent);
      if (answer === "cancel") return null;
      if (answer === "parent") {
        const parent = await checkTenderPlace(asks.tender_parent);
        if (isEndpointFailure(parent)) return parent;
        place = parent.place;
        missing = parent;
      }
    }
    if (missing.missing !== null && !(await showCreateMissingModal(place, missing.existing, missing.missing))) {
      return null;
    }
    return chooseTenderPlace(place);
  };

  return {
    state,
    busy,
    refusal,
    chooseDefault: () => run(() => chooseTenderPlace(null)),
    chooseDrive: (mountPoint) => run(() => chooseTenderPlace(mountPoint)),
    chooseTyped: (text) => run(() => chooseAfterAsking(text)),
    switchToRecorded: (folder) => run(() => switchToRecordedTenderFolder(folder)),
    forget: (folder) =>
      run(async () => ((await showForgetRecordedModal(folder)) ? forgetTenderFolderRecords(folder) : null)),
  };
}
