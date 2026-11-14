/**
 * What the BIOS part's buttons do — on a platform's page and on a game's page
 * alike: the three downloads, "Replace…", a row's Delete and the delete of
 * every download of Tender's for a platform, each with the line it leaves under
 * the buttons and what it says on the rows it touched.
 *
 * The page that holds it decides what is held while a press runs and where its
 * line goes (`setBusy`, `setStatus`), how its BIOS answer is read again once a
 * press changed something (`reread`), and which game the press is for, if any
 * (`romId`).
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
  logWarn,
  replaceBiosFile,
  type BiosDeleteResult,
} from "../api/backend";
import type { BiosLinkFailure, EmulatorTab, EndpointFailure, FirmwareDownloadResult } from "../types";
import type { RommDataChangedDetail } from "../types/events";
import { downloadName, fetchedAsRequired, isFetchable } from "./biosFetchable";
import type { BiosTableRow } from "./biosFileNote";
import { backupLines, keptLines, placedSentence, type RowAttempt } from "./biosPlaceWords";
import { tabLabel } from "./biosTabs";
import { detach } from "./detach";

/** How long the pressed button says `Failed` before everything returns. Two of
 *  the two-to-three seconds the device pass asked for: long enough to read one
 *  word on a pane the reader is already looking at, and the pane stays busy for
 *  exactly that long, so the other download buttons come back when the word
 *  goes rather than a moment earlier. */
const FAILED_NOTICE_MS = 2000;

/** The line a press leaves under the BIOS buttons, bound to the platform it was
 *  produced for, by the folder RomM keeps that platform in. */
export interface BiosStatusLine {
  folder: string;
  scope: "bios";
  text: string;
}

/**
 * The platform an action is for: its slug, which finds its system, and the
 * folder RomM keeps it in (`""` where a game page does not know it), which
 * names it — two listed platforms can share a slug, and the Platforms tab shows
 * each as a row of its own. Every press passes both (`api/backend.ts`).
 */
export interface PlatformKey {
  slug: string;
  folder: string;
}

/** The fields of a platform's or a game's BIOS answer a BIOS table and its buttons read. */
export interface BiosAnswerFields {
  files?: readonly BiosTableRow[];
  emulator_tabs?: EmulatorTab[];
  server_count?: number;
  local_count?: number;
  deletable_count?: number;
  backup_root?: string;
  active_core_label?: string | null;
}

/** Every action names its platform by its {@link PlatformKey}; the page's busy
 *  key, its line and its row attempts are the folder. */
export interface BiosActionsConfig {
  /** Hold the page's actions for *folder*'s press, or let them go (`null`). */
  setBusy: (folder: string | null) => void;
  /** Put a line under the BIOS buttons, or take the page's line away (`null`). */
  setStatus: (line: BiosStatusLine | null) => void;
  /** Read *platform*'s BIOS answer again; the press's spinner ends once it has. */
  reread: (platform: PlatformKey) => Promise<void>;
  /** Tell the other pages that the firmware of *slug*'s platform kept in
   *  *folder* changed, once the press's own read is in. Defaults to
   *  {@link announceBiosChange}. */
  announce?: (slug: string, folder: string) => void;
  /** The game every press is for — the system its install record holds is the
   *  one its rows were read for and its links placed for — or `null` for the
   *  platform's own system. */
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
  downloadRequired: (platform: PlatformKey, emulator: string | null) => void;
  downloadAll: (platform: PlatformKey) => void;
  /** One library file, by its name in RomM and the id of the one the row shows,
   *  into the platform's store folder and linked. */
  downloadOne: (platform: PlatformKey, fileName: string, firmwareId: number | null) => void;
  /** "Replace…" on the row whose place is *place*, once the user confirmed it. */
  replaceBios: (platform: PlatformKey, place: string) => void;
  /**
   * What the latest attempt on this page did to one row — a link that could
   * not be placed at *place*, the file replaced there, a download of *fileName*
   * that arrived damaged. Kept until the page is left, never recorded.
   */
  attemptFor: (folder: string, place: string | null, fileName: string | null) => RowAttempt;
  /** Keep the links the latest attempt on *folder* could not place, replacing what an earlier one said. */
  noteLinkFailures: (folder: string, failures: readonly BiosLinkFailure[] | undefined) => void;
  deleteBios: (platform: PlatformKey) => void;
  deleteBiosFile: (platform: PlatformKey, fileName: string) => void;
}

/** A BIOS row's one offered action: fetch it, replace what lies at its place,
 *  or remove the copy Tender holds. */
type BiosRowActionOffer =
  | { kind: "download"; name: string; firmwareId: number | null }
  | { kind: "replace"; place: string; fileName: string }
  | { kind: "delete"; name: string };

/**
 * Decide which single action button a BIOS row offers, or `null` when it offers
 * none.
 *
 * Download wins where a row is both fetchable and deletable — the file the
 * emulator will look for is the one that is missing. "Replace…" is the
 * backend's decision alone (`replace_offered`). The delete's condition is
 * `delete_name` and `deletable_count` alone: `downloaded` is the resolver's
 * presence at the row's place and is equally true of a file the user placed.
 */
export function biosRowAction(
  file: BiosTableRow,
  rowFetchable: boolean,
  serverOffline: boolean,
): BiosRowActionOffer | null {
  if (rowFetchable && !serverOffline) {
    return { kind: "download", name: downloadName(file), firmwareId: file.library?.firmware_id ?? null };
  }
  if (file.replace_offered && file.local_path) {
    return { kind: "replace", place: file.local_path, fileName: file.file_name };
  }
  // A row from a payload that states no `delete_name` is deleted by its own name.
  const name = file.delete_name === undefined ? file.file_name : file.delete_name;
  if (name === null || (file.deletable_count ?? 0) === 0) return null;
  return { kind: "delete", name };
}

/**
 * What a download button shows instead of its label: a spinner while its own run
 * is going, `failed` for the moment after one fails, or `null`.
 *
 * Keyed on the run's platform folder as well as the button's identity, so a
 * download running for one platform never puts a spinner on another platform's
 * buttons.
 */
export function biosDownloadButtonState(
  busyFolder: string | null,
  platformFolder: string,
  actions: Pick<BiosActions, "downloadPending" | "downloadFailed">,
  id: string,
): "spinner" | "failed" | null {
  if (busyFolder !== platformFolder) return null;
  if (actions.downloadPending === id) return "spinner";
  return actions.downloadFailed === id ? "failed" : null;
}

/** What the platform-wide and requirement-scoped BIOS buttons offer right now. */
interface BiosBulkOffer {
  shownEmulator: string | null;
  requiredLabel: string | null;
  requiredMissing: number;
  canDownloadRequired: boolean;
  canDownloadAll: boolean;
  deletable: number;
}

/**
 * Compute the bulk BIOS button offer from the answer and the shown emulator tab.
 *
 * The download affordances key off what is missing AND fetchable (`isFetchable`
 * and `fetchedAsRequired`), reading the readiness verdict nowhere: what the
 * resolver could establish is the emulator's demand, while what is fetchable is
 * what the RomM library holds. `Download required` counts the shown emulator's
 * rows marked `fetch_for_required` — its required files and the options of its
 * one-of group's uncovered regions, the rule the download itself applies — and
 * `Download all` stops at the library's own finished ratio. So neither is a
 * readiness gate: platforms such as PS2, GameCube, and PSP launch standalone
 * emulators the resolver holds no card for, so their readiness verdict declines
 * even while the RomM library holds files the user can fetch.
 *
 * Which rows are fetchable at all — the library's side of it, including why a
 * declared folder is out — is `isFetchable`.
 */
export function biosBulkOffer(
  answer: BiosAnswerFields,
  tab: EmulatorTab | null,
  shownRows: readonly BiosTableRow[],
  serverOffline: boolean,
  launching: "pick" | "shown",
): BiosBulkOffer {
  const files = answer.files ?? [];
  const otherTab = tab !== null && !tab.launching;
  // The emulator a download from this table is for: the shown one's identity on
  // another emulator's view; on the launching one's, the platform's own pick
  // (`null`) on the platform page and the entry's own emulator on a game page.
  const shownEmulator = otherTab || launching === "shown" ? (tab?.emulator ?? null) : null;
  const shownLabel = tab ? tabLabel(tab) : null;
  const requiredLabel = shownLabel === null ? null : `Download required for ${shownLabel}`;
  // The library's own progress, counted over what the library holds rather than
  // over every declared row — rows no library holds cannot be fetched.
  const total = answer.server_count ?? files.filter((f) => f.on_server).length;
  const done = answer.local_count ?? files.filter((f) => f.on_server && f.downloaded).length;
  const requiredMissing = shownRows.filter(isFetchable).filter(fetchedAsRequired).length;
  // What the delete removes is a record count, with no local fallback: the rows
  // say nothing about who placed a file, so an absent count offers no delete.
  const deletable = answer.deletable_count ?? 0;
  return {
    shownEmulator,
    requiredLabel,
    requiredMissing,
    canDownloadRequired: requiredMissing > 0 && !serverOffline,
    canDownloadAll: done !== total && files.some(isFetchable) && !serverOffline,
    deletable,
  };
}

/**
 * Tell open game-detail surfaces that this platform's firmware changed, so they
 * re-read their BIOS requirement instead of leaving the pre-change one standing.
 *
 * Call it only when firmware actually changed. The event fans out to every
 * mounted view and each one that matches the slug pays a live
 * `get_bios_status` for it — all but the view whose own BIOS action
 * ran, which has read its answer already and skips its own event — so a run
 * that moved no files must stay silent rather than send an event nothing can
 * act on.
 */
export function announceBiosChange(platformSlug: string, platformFolder: string): void {
  const detail: RommDataChangedDetail = { type: "bios", platform_slug: platformSlug, platform_fs_slug: platformFolder };
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail }));
}

function downloadStatusLines(result: FirmwareDownloadResult): string[] {
  const failed = result.failed ?? [];
  return [failed.length > 0 ? result.message : null, placedSentence(result), ...backupLines(result.backups)].filter(
    (line): line is string => line !== null,
  );
}

function logBiosActionFailure(
  action: string,
  target: string,
  platform: PlatformKey,
  romId: number | null,
  detail: unknown,
): void {
  const romPart = romId === null ? "" : ` romId=${romId}`;
  logWarn(
    `useBiosActions: ${action} (${target}, slug=${platform.slug} folder=${platform.folder}${romPart}) failed: ${detail}`,
  );
}

export function useBiosActions({
  setBusy,
  setStatus,
  reread,
  announce = announceBiosChange,
  romId,
}: BiosActionsConfig): BiosActions {
  const [downloadPending, setDownloadPending] = useState<string | null>(null);
  const [downloadFailed, setDownloadFailed] = useState<string | null>(null);
  // What the latest attempt did to a row, by `folder|place:…` and `folder|file:…`.
  const [attempts, setAttempts] = useState<Record<string, RowAttempt>>({});
  // Cleared on unmount, so a page left during the window cannot set state on a
  // gone component. A plain timer is the right tool here: the injected-clock
  // rule is scoped to `backend/services/**`, and sibling hooks hold UI timers
  // in a ref this way.
  const failedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (failedTimer.current !== null) clearTimeout(failedTimer.current);
    },
    [],
  );

  const noteLinkFailures = useCallback((folder: string, failures: readonly BiosLinkFailure[] | undefined) => {
    setAttempts((prev) => {
      const next: Record<string, RowAttempt> = {};
      for (const [key, attempt] of Object.entries(prev)) {
        if (!key.startsWith(`${folder}|`)) {
          next[key] = attempt;
          continue;
        }
        const { linkFailure: _replaced, ...rest } = attempt;
        next[key] = rest;
      }
      for (const failure of failures ?? []) {
        const key = `${folder}|place:${failure.place}`;
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

  // Success says itself: the spinner ends and the rows come back re-read. Only
  // what failed gets words — and a batch in which every file it tried failed is
  // a failure to the reader, whatever the answer's `success` says.
  const finishDownloadSuccess = useCallback(
    async (platform: PlatformKey, pending: string, result: FirmwareDownloadResult) => {
      const { slug, folder } = platform;
      noteLinkFailures(folder, result.link_failures);
      await reread(platform);
      if (result.downloaded > 0 || (result.placed ?? 0) > 0) announce(slug, folder);
      const lines = downloadStatusLines(result);
      if (lines.length > 0) setStatus({ folder, scope: "bios", text: lines.join("\n") });
      if ((result.failed ?? []).length > 0 && result.downloaded === 0) {
        logBiosActionFailure("download", pending, platform, romId, result.message);
        reportFailure(pending);
        return;
      }
      setDownloadPending(null);
      setBusy(null);
    },
    [announce, noteLinkFailures, reportFailure, reread, romId, setBusy, setStatus],
  );

  const runDownload = useCallback(
    (platform: PlatformKey, pending: string, work: () => Promise<FirmwareDownloadResult | EndpointFailure>) => {
      const { folder } = platform;
      setBusy(folder);
      setDownloadPending(pending);
      // Cleared up front, so a previous failure does not stand under a run that
      // has not answered yet.
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await work();
            if (result.success) {
              await finishDownloadSuccess(platform, pending, result);
              return;
            }
            logBiosActionFailure("download", pending, platform, romId, result.message);
            if (result.reason === "bios_download_damaged") {
              setAttempts((prev) => ({
                ...prev,
                [`${folder}|file:${pending}`]: { ...prev[`${folder}|file:${pending}`], damaged: true },
              }));
            }
            setStatus({ folder, scope: "bios", text: result.message });
            reportFailure(pending);
          } catch (e) {
            logBiosActionFailure("download", pending, platform, romId, e);
            setStatus({ folder, scope: "bios", text: `Download failed: ${e}` });
            reportFailure(pending);
          }
        })(),
      );
    },
    [finishDownloadSuccess, reportFailure, romId, setBusy, setStatus],
  );

  const downloadRequired = useCallback(
    (platform: PlatformKey, emulator: string | null) =>
      runDownload(platform, "required", () =>
        downloadRequiredFirmware(platform.slug, platform.folder, emulator, romId),
      ),
    [romId, runDownload],
  );
  const downloadAll = useCallback(
    (platform: PlatformKey) =>
      runDownload(platform, "all", () => downloadAllFirmware(platform.slug, platform.folder, romId)),
    [romId, runDownload],
  );
  const downloadOne = useCallback(
    (platform: PlatformKey, fileName: string, firmwareId: number | null) => {
      setAttempts((prev) => {
        const key = `${platform.folder}|file:${fileName}`;
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      });
      runDownload(platform, fileName, () =>
        downloadPlatformFirmwareFile(platform.slug, platform.folder, fileName, firmwareId, romId),
      );
    },
    [romId, runDownload],
  );

  const replaceBios = useCallback(
    (platform: PlatformKey, place: string) => {
      const { slug, folder } = platform;
      setBusy(folder);
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await replaceBiosFile(slug, folder, place, romId);
            if (!result.success) {
              logBiosActionFailure("replaceBios", place, platform, romId, result.message);
              setStatus({ folder, scope: "bios", text: `Failed to replace the file: ${result.message}` });
              return;
            }
            noteLinkFailures(folder, result.link_failures);
            setAttempts((prev) => ({
              ...prev,
              [`${folder}|place:${place}`]: { ...prev[`${folder}|place:${place}`], replacedTo: result.backup_path },
            }));
            const placed = placedSentence(result);
            if (placed !== null) setStatus({ folder, scope: "bios", text: placed });
            await reread(platform);
            announce(slug, folder);
          } catch (e) {
            logBiosActionFailure("replaceBios", place, platform, romId, e);
            setStatus({ folder, scope: "bios", text: `Failed to replace the file: ${e}` });
          } finally {
            setBusy(null);
          }
        })(),
      );
    },
    [announce, noteLinkFailures, reread, romId, setBusy, setStatus],
  );

  const attemptFor = useCallback(
    (folder: string, place: string | null, fileName: string | null): RowAttempt => ({
      ...(fileName !== null ? attempts[`${folder}|file:${fileName}`] : undefined),
      ...(place !== null ? attempts[`${folder}|place:${place}`] : undefined),
    }),
    [attempts],
  );

  const deleteBios = useCallback(
    (platform: PlatformKey) => {
      const { slug, folder } = platform;
      setBusy(folder);
      detach(
        (async () => {
          try {
            const result = await deletePlatformBios(slug, folder);
            if (!result.success) logBiosActionFailure("deleteBios", folder, platform, romId, result.message);
            const kept = result.success ? keptLines(result.kept) : [];
            setStatus({ folder, scope: "bios", text: [result.message, ...kept].join("\n") });
            // A `delete_incomplete` may have removed files or dropped records
            // before the file it stopped on, so the rows are re-read as after a
            // success.
            if (result.success || result.reason === "delete_incomplete") {
              await reread(platform);
              if ((result.deleted_count ?? 0) > 0) announce(slug, folder);
            }
          } catch (e) {
            logBiosActionFailure("deleteBios", folder, platform, romId, e);
            setStatus({ folder, scope: "bios", text: `Failed to delete BIOS files: ${e}` });
          } finally {
            setBusy(null);
          }
        })(),
      );
    },
    [announce, reread, romId, setBusy, setStatus],
  );

  /** One row's Delete. A success is said by the rows coming back re-read; only
   *  a failure gets a line, and a `delete_incomplete` gets both, because it may
   *  have removed files or dropped records before the file it stopped on. */
  const runRowDelete = useCallback(
    (platform: PlatformKey, work: () => Promise<BiosDeleteResult>, what: string) => {
      const { slug, folder } = platform;
      setBusy(folder);
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await work();
            const kept = result.success ? keptLines(result.kept) : [];
            if (!result.success) {
              logBiosActionFailure("deleteBiosFile", what, platform, romId, result.message);
              setStatus({ folder, scope: "bios", text: result.message });
            } else if (kept.length > 0) setStatus({ folder, scope: "bios", text: kept.join("\n") });
            if (result.success || result.reason === "delete_incomplete") {
              await reread(platform);
              if ((result.deleted_count ?? 0) > 0) announce(slug, folder);
            }
          } catch (e) {
            logBiosActionFailure("deleteBiosFile", what, platform, romId, e);
            setStatus({ folder, scope: "bios", text: `Failed to delete ${what}: ${e}` });
          } finally {
            setBusy(null);
          }
        })(),
      );
    },
    [announce, reread, romId, setBusy, setStatus],
  );

  const deleteBiosFile = useCallback(
    (platform: PlatformKey, fileName: string) => {
      runRowDelete(platform, () => deleteBiosFileCall(platform.slug, platform.folder, fileName), fileName);
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
