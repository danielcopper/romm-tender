/**
 * Tender's folder as one page holds it: the state read on mount, and every
 * change the settings section and Main's notices make to it. Each change
 * answers the state as it now stands, which replaces the one held; a refusal
 * keeps the state and holds the sentence it is shown with until the next
 * change.
 */

import { useEffect, useState } from "react";
import {
  chooseTenderPlace,
  forgetTenderFolderRecords,
  getTenderFolder,
  isEndpointFailure,
  logError,
  switchToRecordedTenderFolder,
} from "../api/backend";
import type { TenderFolderForgetRefusal, TenderFolderRefusal, TenderFolderState, TenderPlaceRefusal } from "../types";
import { detach } from "../utils/detach";
import { placeRefusalSentence, switchedToast } from "../utils/tenderFolderWording";
import { showToast } from "../utils/toast";
import { showForgetRecordedModal } from "./settings/TenderFolderModals";

export interface TenderFolderView {
  /** `undefined` while the read is in flight, `null` where it failed. */
  state: TenderFolderState | null | undefined;
  /** A change is in flight; a second one is refused until it ends. */
  busy: boolean;
  /** Why the last change was refused, as shown; `null` after one that was not. */
  refusal: string | null;
  chooseDefault: () => void;
  chooseDrive: (mountPoint: string) => void;
  /** A typed place the "Another folder…" window had accepted: the state it answered, confirmed with the toast. */
  acceptTyped: (state: TenderFolderState) => void;
  switchToRecorded: (folder: string) => void;
  forget: (folder: string) => void;
}

type Answer = TenderFolderState | TenderPlaceRefusal | TenderFolderRefusal | TenderFolderForgetRefusal;

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

  /** Run *change*; a *switches* change that is answered with a state says so in a toast. */
  const run = (change: () => Promise<Answer | null>, switches = false) => {
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
            if (switches) showToast(switchedToast(answer));
          }
        } catch (e) {
          logError(`Failed to change Tender's folder: ${e}`);
        } finally {
          setBusy(false);
        }
      })(),
    );
  };

  return {
    state,
    busy,
    refusal,
    chooseDefault: () => run(() => chooseTenderPlace(null), true),
    chooseDrive: (mountPoint) => run(() => chooseTenderPlace(mountPoint), true),
    acceptTyped: (accepted) => {
      setRefusal(null);
      setState(accepted);
      showToast(switchedToast(accepted));
    },
    switchToRecorded: (folder) => run(() => switchToRecordedTenderFolder(folder), true),
    forget: (folder) =>
      run(async () => ((await showForgetRecordedModal(folder)) ? forgetTenderFolderRecords(folder) : null)),
  };
}
