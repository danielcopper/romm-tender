/**
 * What the BIOS part's buttons do — on a platform's page and on a game's BIOS
 * tab alike: the three downloads, "Replace…", a row's Delete and the delete of
 * every download of Tender's for a platform, each with the line it leaves under
 * the buttons and what it says on the rows it touched.
 *
 * The page that holds it decides two things only: what is held while a press
 * runs and where its line goes (`setBusy`, `setStatus` — the platform page's
 * hold covers its core switch and its removal too), and how its BIOS answer is
 * read again once a press changed something (`reread`).
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteBiosFile as deleteBiosFileCall,
  deletePlatformBios,
  downloadAllFirmware,
  downloadPlatformFirmwareFile,
  downloadRequiredFirmware,
  replaceBiosFile,
} from "../../api/backend";
import type { BiosDeleteResult } from "../../api/backend";
import type { BiosLinkFailure, EndpointFailure, FirmwareDownloadResult } from "../../types";
import type { RowAttempt } from "../../utils/biosPlaceWords";
import { backupLines, keptLines, placedSentence } from "../../utils/biosPlaceWords";
import { detach } from "../../utils/detach";

/** How long the pressed button says `Failed` before everything returns. Two of
 *  the two-to-three seconds the device pass asked for: long enough to read one
 *  word on a pane the reader is already looking at, and the pane stays busy for
 *  exactly that long, so the other download buttons come back when the word
 *  goes rather than a moment earlier. */
const FAILED_NOTICE_MS = 2000;

/** The line a press leaves under the BIOS buttons, bound to the platform it was produced for. */
export interface BiosStatusLine {
  slug: string;
  scope: "bios";
  text: string;
}

export interface BiosActionsConfig {
  /** Hold the page's actions for *slug*'s press, or let them go (`null`). */
  setBusy: (slug: string | null) => void;
  /** Put a line under the BIOS buttons, or take the page's line away (`null`). */
  setStatus: (line: BiosStatusLine | null) => void;
  /** Read *slug*'s BIOS answer again; the press's spinner ends once it has. */
  reread: (slug: string) => Promise<void>;
  /** The game "Download all" is pressed for — its links then follow the system
   *  the game's install record holds — or `null` for the platform's own. */
  romId: number | null;
}

export interface BiosActions {
  /**
   * Which download button was pressed, while it runs — `"required"`, `"all"`,
   * or a file name — and `null` otherwise.
   *
   * The pane says a download is happening by spinning THAT button and disabling
   * the others, so it needs the identity and not just the busy key. A success is
   * said by the spinner ending and the rows re-reading; only a failure gets a
   * sentence, and so does a batch that passed over files it could not fetch.
   */
  downloadPending: string | null;
  /**
   * The download button that just failed, for the two seconds it says so.
   *
   * The pressed button reports its own outcome: spinner while it runs, then a
   * red `Failed` before it returns to its label. The pane stays busy for that
   * window, so the other download buttons re-enable when the word goes rather
   * than a moment earlier. The backend's own message stays in the status line
   * under the buttons, because "Failed" says that it did, not why.
   */
  downloadFailed: string | null;
  /** The shown emulator's required files, where that emulator opens them. */
  downloadRequired: (slug: string, emulator: string | null) => void;
  downloadAll: (slug: string) => void;
  /** One library file, by its name in RomM and the id of the one the row shows,
   *  into the platform's store folder and linked. */
  downloadOne: (slug: string, fileName: string, firmwareId: number | null) => void;
  /** "Replace…" on the row whose place is *place*, once the user confirmed it. */
  replaceBios: (slug: string, place: string) => void;
  /**
   * What the latest attempt on this page did to one row — a link that could
   * not be placed at *place*, the file replaced there, a download of *fileName*
   * that arrived damaged. Kept until the page is left, never recorded.
   */
  attemptFor: (slug: string, place: string | null, fileName: string | null) => RowAttempt;
  /** Keep the links the latest attempt on *slug* could not place, replacing what an earlier one said. */
  noteLinkFailures: (slug: string, failures: readonly BiosLinkFailure[] | undefined) => void;
  deleteBios: (slug: string) => void;
  deleteBiosFile: (slug: string, fileName: string) => void;
}

/**
 * Tell an open game-detail page that this platform's firmware changed, so it
 * re-reads its BIOS requirement instead of leaving the pre-change one standing
 * (#939).
 *
 * Call it only when firmware actually changed. The event fans out to every
 * mounted panel and each one that matches the slug pays a live
 * `get_bios_status` for it (#1082), so a run that moved no files must stay
 * silent rather than send an event no panel can act on.
 */
export function announceBiosChange(platformSlug: string): void {
  globalThis.dispatchEvent(
    new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: platformSlug } }),
  );
}

export function useBiosActions({ setBusy, setStatus, reread, romId }: BiosActionsConfig): BiosActions {
  const [downloadPending, setDownloadPending] = useState<string | null>(null);
  const [downloadFailed, setDownloadFailed] = useState<string | null>(null);
  // What the latest attempt did to a row, by `slug|place:…` and `slug|file:…`.
  const [attempts, setAttempts] = useState<Record<string, RowAttempt>>({});
  // Cleared on unmount, so a page left during the window cannot set state on a
  // gone component. A plain timer is the right tool here: the injected-clock
  // rule is scoped to `backend/services/**`, and this file's siblings
  // (`CustomPlayButton`) already hold their timers in a ref this way.
  const failedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (failedTimer.current !== null) clearTimeout(failedTimer.current);
    },
    [],
  );

  const noteLinkFailures = useCallback((slug: string, failures: readonly BiosLinkFailure[] | undefined) => {
    setAttempts((prev) => {
      const next: Record<string, RowAttempt> = {};
      for (const [key, attempt] of Object.entries(prev)) {
        if (!key.startsWith(`${slug}|`)) {
          next[key] = attempt;
          continue;
        }
        const { linkFailure: _replaced, ...rest } = attempt;
        next[key] = rest;
      }
      for (const failure of failures ?? []) {
        const key = `${slug}|place:${failure.place}`;
        next[key] = { ...next[key], linkFailure: failure.reason };
      }
      return next;
    });
  }, []);

  /** Hold the pressed button on a red `Failed` for a moment before everything
   *  returns: a spinner that simply stops is not a message, and the two seconds
   *  are what makes the outcome legible on a pane the reader is looking at. */
  const reportFailure = useCallback(
    (pending: string) => {
      setDownloadPending(null);
      setDownloadFailed(pending);
      failedTimer.current = setTimeout(() => {
        failedTimer.current = null;
        setDownloadFailed(null);
        setBusy(null);
      }, FAILED_NOTICE_MS);
    },
    [setBusy],
  );

  const runDownload = useCallback(
    (slug: string, pending: string, work: () => Promise<FirmwareDownloadResult | EndpointFailure>) => {
      setBusy(slug);
      setDownloadPending(pending);
      // Cleared up front, so a previous failure does not stand under a run that
      // has not answered yet.
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await work();
            // Success says itself: the spinner ends and the rows come back
            // re-read. Only what failed gets words — and a batch in which every
            // file it tried failed is a failure to the reader, whatever the
            // answer's `success` says.
            if (result.success) {
              noteLinkFailures(slug, result.link_failures);
              await reread(slug);
              if (result.downloaded > 0 || (result.placed ?? 0) > 0) announceBiosChange(slug);
              const failed = result.failed ?? [];
              const lines = [
                failed.length > 0 ? result.message : null,
                placedSentence(result),
                ...backupLines(result.backups),
              ].filter((line) => line !== null);
              if (lines.length > 0) setStatus({ slug, scope: "bios", text: lines.join("\n") });
              if (failed.length > 0 && result.downloaded === 0) {
                reportFailure(pending);
                return;
              }
              setDownloadPending(null);
              setBusy(null);
              return;
            }
            if (result.reason === "bios_download_damaged") {
              setAttempts((prev) => ({
                ...prev,
                [`${slug}|file:${pending}`]: { ...prev[`${slug}|file:${pending}`], damaged: true },
              }));
            }
            setStatus({ slug, scope: "bios", text: result.message });
            reportFailure(pending);
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Download failed: ${e}` });
            reportFailure(pending);
          }
        })(),
      );
    },
    [noteLinkFailures, reread, reportFailure, setBusy, setStatus],
  );

  const downloadRequired = useCallback(
    (slug: string, emulator: string | null) =>
      runDownload(slug, "required", () => downloadRequiredFirmware(slug, emulator)),
    [runDownload],
  );
  const downloadAll = useCallback(
    (slug: string) => runDownload(slug, "all", () => downloadAllFirmware(slug, romId)),
    [romId, runDownload],
  );
  const downloadOne = useCallback(
    (slug: string, fileName: string, firmwareId: number | null) => {
      setAttempts((prev) => {
        const key = `${slug}|file:${fileName}`;
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      });
      runDownload(slug, fileName, () => downloadPlatformFirmwareFile(slug, fileName, firmwareId));
    },
    [runDownload],
  );

  const replaceBios = useCallback(
    (slug: string, place: string) => {
      setBusy(slug);
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await replaceBiosFile(slug, place);
            if (!result.success) {
              setStatus({ slug, scope: "bios", text: `Failed to replace the file: ${result.message}` });
              return;
            }
            noteLinkFailures(slug, result.link_failures);
            setAttempts((prev) => ({
              ...prev,
              [`${slug}|place:${place}`]: { ...prev[`${slug}|place:${place}`], replacedTo: result.backup_path },
            }));
            const placed = placedSentence(result);
            if (placed !== null) setStatus({ slug, scope: "bios", text: placed });
            await reread(slug);
            announceBiosChange(slug);
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Failed to replace the file: ${e}` });
          } finally {
            setBusy(null);
          }
        })(),
      );
    },
    [noteLinkFailures, reread, setBusy, setStatus],
  );

  const attemptFor = useCallback(
    (slug: string, place: string | null, fileName: string | null): RowAttempt => ({
      ...(fileName !== null ? attempts[`${slug}|file:${fileName}`] : undefined),
      ...(place !== null ? attempts[`${slug}|place:${place}`] : undefined),
    }),
    [attempts],
  );

  const deleteBios = useCallback(
    (slug: string) => {
      setBusy(slug);
      detach(
        (async () => {
          try {
            const result = await deletePlatformBios(slug);
            const kept = result.success ? keptLines(result.kept) : [];
            setStatus({ slug, scope: "bios", text: [result.message, ...kept].join("\n") });
            // A `delete_incomplete` may have removed files or dropped records
            // before the file it stopped on, so the rows are re-read as after a
            // success.
            if (result.success || result.reason === "delete_incomplete") {
              await reread(slug);
              if ((result.deleted_count ?? 0) > 0) announceBiosChange(slug);
            }
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Failed to delete BIOS files: ${e}` });
          } finally {
            setBusy(null);
          }
        })(),
      );
    },
    [reread, setBusy, setStatus],
  );

  /** One row's Delete. A success is said by the rows coming back re-read; only
   *  a failure gets a line, and a `delete_incomplete` gets both, because it may
   *  have removed files or dropped records before the file it stopped on. */
  const runRowDelete = useCallback(
    (slug: string, work: () => Promise<BiosDeleteResult>, what: string) => {
      setBusy(slug);
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await work();
            const kept = result.success ? keptLines(result.kept) : [];
            if (!result.success) setStatus({ slug, scope: "bios", text: result.message });
            else if (kept.length > 0) setStatus({ slug, scope: "bios", text: kept.join("\n") });
            if (result.success || result.reason === "delete_incomplete") {
              await reread(slug);
              if ((result.deleted_count ?? 0) > 0) announceBiosChange(slug);
            }
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Failed to delete ${what}: ${e}` });
          } finally {
            setBusy(null);
          }
        })(),
      );
    },
    [reread, setBusy, setStatus],
  );

  const deleteBiosFile = useCallback(
    (slug: string, fileName: string) => {
      runRowDelete(slug, () => deleteBiosFileCall(slug, fileName), fileName);
    },
    [runRowDelete],
  );

  return {
    downloadPending,
    downloadFailed,
    downloadRequired,
    downloadAll,
    downloadOne,
    replaceBios,
    attemptFor,
    noteLinkFailures,
    deleteBios,
    deleteBiosFile,
  };
}
