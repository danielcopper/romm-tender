/**
 * Everything the Library page's Platforms tab knows and does: the three
 * page-wide reads, the per-selection core read, and the actions a platform's
 * detail offers.
 *
 * It lives above the tab boundary because Steam's tabbed page renders only the
 * active tab and keys it by tab id, so switching tabs unmounts the content and
 * mounting it again would re-issue every read and thaw the list's frozen order.
 * The page holds the state; the tab and its detail render it.
 *
 * The three reads answer different questions: `get_platforms` is RomM's
 * platforms with ROMs (the list itself), `get_firmware_status` whether RomM's
 * firmware listing could be read (the downloads stand on it), and
 * `get_registry_platforms` the ROMs bound to a Steam shortcut per platform —
 * which is also what "this platform has synced games" means here, and the only
 * one of the three that answers it for every platform in the list.
 *
 * What a platform's BIOS answer IS costs a live reading of the machine for that
 * system, so it is a fourth read, asked one platform at a time
 * (`get_platform_firmware_status`) for every platform the list shows, while the
 * page already stands. This module owns the order they are asked in, when they
 * stop, and which of three states each row is in meanwhile — see
 * {@link FirmwareState}.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  countPlatformSaves,
  debugLog,
  deleteBiosFile as deleteBiosFileCall,
  deletePlatformBios,
  deletePlatformSaves,
  downloadAllFirmware,
  downloadPlatformFirmwareFile,
  downloadRequiredFirmware,
  getFirmwareStatus,
  getPlatformFirmwareStatus,
  getPlatforms,
  getRegistryPlatforms,
  getSystemCoreInfo,
  logWarn,
  removePlatformShortcuts,
  replaceBiosFile,
  reportRemovalResults,
  savePlatformSync,
  setAllPlatformsSync,
  setSystemCore,
  updateBiosLinks,
} from "../../api/backend";
import type { BiosDeleteResult } from "../../api/backend";
import type {
  BiosLinkFailure,
  EndpointFailure,
  FirmwareDownloadResult,
  FirmwarePlatformExt,
  PlatformSyncSetting,
  SystemCoreInfo,
} from "../../types";
import type { RowAttempt } from "../../utils/biosPlaceWords";
import { backupLines, keptLines, placedSentence } from "../../utils/biosPlaceWords";
import { detach } from "../../utils/detach";
import { emulatorName } from "../../utils/emulatorEntries";
import { batchConfirmLaunchOptions } from "../../utils/launchOptionsReconcile";
import { clearPlatformCollection } from "../../utils/collections";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancellation,
  isPruneLeaseCancelled,
  mountPruneLeaseOwner,
  releasePruneLeasesByOwner,
  withPruneLease,
} from "../../utils/pruneLease";
import { removeShortcutsPaced } from "../../utils/shortcutRemoval";
import { withTimeout } from "../../utils/withTimeout";
import { useLatestWrites, useLineWrites } from "./latestWrites";
import { SYNC_WRITE_FAILED } from "./syncWriteFailed";

/** How long the pressed button says `Failed` before everything returns. Two of
 *  the two-to-three seconds the device pass asked for: long enough to read one
 *  word on a pane the reader is already looking at, and the pane stays busy for
 *  exactly that long, so the other download buttons come back when the word
 *  goes rather than a moment earlier. */
const FAILED_NOTICE_MS = 2000;

const LEASE_OWNER = "library-platforms";
const LIST_LINE = "list";
const REMOVAL_REPORT_TIMEOUT_MS = 15000;

/** Which group of the detail a status line belongs under, so a failed core
 *  switch is not reported below the removal buttons. */
export type StatusScope = "core" | "bios" | "remove";

export interface DetailStatus {
  /** The platform the line is about. Moving through the list changes the
   *  detail under an action that is still running, so the line is bound to the
   *  platform it was produced for rather than cleared on every selection. */
  slug: string;
  scope: StatusScope;
  text: string;
}

/**
 * Where one platform's BIOS answer stands.
 *
 * `pending` is the state this page exists to keep separate: the answer has been
 * asked for and has not arrived, which is NOT "nothing could be established".
 * Rendering the two alike is how a reader takes an answer where none has come
 * yet — and every row starts out that way, because the answers arrive one
 * platform at a time.
 *
 * - `pending` — asked for (or queued), not back yet.
 * - `answered` — {@link PlatformRow.firmware} holds it. Every finished read
 *   answers with an entry, a reading that did not happen included: its
 *   verdict is the one that says nothing could be established.
 * - `failed` — the read did not come back and there is nothing behind it.
 *
 * An answer already held outranks a failure, so a failed RE-read leaves the
 * state where it was and raises {@link PlatformRow.firmwareStale} instead:
 * taking an answer back because a later read failed is how a pane loses an
 * answer it has.
 */
export type FirmwareState = "pending" | "answered" | "failed";

/** One platform as the list and the detail read it — the three reads joined. */
export interface PlatformRow {
  id: number;
  slug: string;
  name: string;
  /** RomM's own ROM count for the platform, read from the server. Distinct from
   *  `reachableCount`, which is what reached Steam; the header line shows both.
   *
   *  The two count different populations on purpose: this one is what RomM holds
   *  *now*, its partner is what our own rows say. So ROMs added on RomM since the
   *  last sync widen the gap, which is the gap being useful — but equality means
   *  "nothing outstanding as of the last sync", not a fresh server-side proof. */
  romCount: number;
  syncEnabled: boolean;
  /** This platform's BIOS answer, or `null` where none is held. Non-null exactly
   *  when {@link firmwareState} is `answered`. */
  firmware: FirmwarePlatformExt | null;
  /** Whether {@link firmware} is an answer, a question still out, or a read
   *  that did not come back. The list's dot and the detail's BIOS block are
   *  worded off this, never off `firmware` being null — two of the three states
   *  share that. */
  firmwareState: FirmwareState;
  /** Whether the last read for this platform failed over an answer it still
   *  holds — so what is shown is the state from before whatever changed it.
   *  Distinct from `firmwareState: "failed"`, which is a platform with no
   *  answer at all: one pane warns about its rows, the other has none. */
  firmwareStale: boolean;
  /** How many of the platform's ROMs are bound to a Steam shortcut, or `null`
   *  when that read failed. `null` is not zero: read as zero it disables the
   *  shortcut removal — a claim about a platform nothing was learned about.
   *
   *  This is the count of SHORTCUTS, so it is what the Remove group says and acts
   *  on. The header line states `reachableCount` instead. */
  shortcutCount: number | null;
  /** How many of the platform's ROMs are reachable from Steam (GLOSSARY.md →
   *  Reachable), less the versions RomM no longer serves
   *  (`RegistryPlatform.reachable_count`). This is what the header line states;
   *  `null` (the same read failure as `shortcutCount`) drops that half of the
   *  line rather than printing a zero nothing established.
   *
   *  The line can read right > left for one window
   *  (`docs/architecture/qam-panel.md`, § Library). */
  reachableCount: number | null;
}

/** The list's two groups, computed once and kept while the page is open, so
 *  toggling a row never moves it out from under the focus. */
export interface FrozenGroups {
  synced: string[];
  available: string[];
}

/**
 * The core read's answer for one platform. `undefined` is "not read yet",
 * `null` is "the read failed" — a detail that showed a spinner forever would
 * claim the answer is still coming.
 */
export type CoreAnswer = SystemCoreInfo | null | undefined;

/**
 * How many save files a platform holds. `undefined` is "not read yet" and
 * `null` is "the read failed" — the same three-way answer the core read gives,
 * and for the same reason: the Delete save files button disables on a real
 * zero, so an unknown must not look like one.
 */
export type SaveCountAnswer = number | null | undefined;

export interface PlatformsPageState {
  rows: Map<string, PlatformRow>;
  groups: FrozenGroups | null;
  loading: boolean;
  /** The platform list itself could not be read — the tab has nothing to show. */
  failed: boolean;
  /** RomM is unreachable: the BIOS downloads are withdrawn, everything else stands. */
  serverOffline: boolean;
  /** The Steam shortcut counts could not be read — every row's `shortcutCount`
   *  is `null` and the detail says so where the number would have been. */
  shortcutCountsFailed: boolean;
  selectedSlug: string | null;
  select: (slug: string) => void;
  coreFor: (slug: string) => CoreAnswer;
  saveCountFor: (slug: string) => SaveCountAnswer;
  status: DetailStatus | null;
  /**
   * Why the last sync write did not take, or `null`. The list's own line, not a
   * platform pane's: Enable all is about the whole list, and a row's toggle is
   * about a row the reader is already looking at, so neither belongs in
   * {@link DetailStatus}, which is bound to one platform's pane.
   *
   * Cleared when the tab is entered, and otherwise set and cleared only by the
   * answer to the latest sync write — `docs/architecture/qam-panel.md`
   * § Library, "Only the latest write speaks".
   */
  listStatus: string | null;
  /** The tab was entered: no answer to a sync write issued before now sets or
   *  clears the list's line. */
  enter: () => void;
  /**
   * The platform whose action is in flight, or `null`. Every action the detail
   * offers disables on every platform while one is running; the list's own sync
   * writes are outside the hold. The slug is what lets a pane that is not the
   * one acting say why its buttons are dead.
   *
   * **What forbids running two at once is this page's own state, not a lock
   * anywhere else.** `status`, `removalProgress` and `busySlug` are each
   * singular, so a second action would overwrite the first's line and the first
   * `finally` would clear the busy state out from under the second. The prune
   * lease is no obstacle and must not be cited as one: leases are keyed by
   * token on both sides, and the prune rule refuses on a prune *run*
   * reservation rather than on a sibling lease, so two platform removals would
   * both be admitted. Making these three per-slug is what a per-platform
   * disable would take.
   */
  busySlug: string | null;
  /** Bound to its platform for the reason {@link DetailStatus} is: walking the
   *  list under a running removal must not show its progress on another
   *  platform's pane. */
  removalProgress: { slug: string; removed: number; total: number } | null;
  toggleSync: (row: PlatformRow, enabled: boolean) => void;
  setAllSync: (enabled: boolean) => void;
  changeCore: (slug: string, pickedLabel: string) => void;
  /**
   * Which download button was pressed, while it runs — `"required"`, `"all"`,
   * or a file name — and `null` otherwise.
   *
   * The pane says a download is happening by spinning THAT button and disabling
   * the others, so it needs the identity and not just `busySlug`. A success is
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
   * under the section, because "Failed" says that it did, not why.
   */
  downloadFailed: string | null;
  /** The shown emulator's required files, where that emulator opens them. */
  downloadRequired: (slug: string, emulator: string | null) => void;
  downloadAll: (slug: string) => void;
  /** One library file, by its name in RomM, into the platform's store folder and linked. */
  downloadOne: (slug: string, fileName: string, firmwareId: number | null) => void;
  /** "Replace…" on the row whose place is *place*, once the user confirmed it. */
  replaceBios: (slug: string, place: string) => void;
  /**
   * What the latest attempt on this page did to one row — a link that could
   * not be placed at *place*, the file replaced there, a download of *fileName*
   * that arrived damaged. Kept until the page is left, never recorded.
   */
  attemptFor: (slug: string, place: string | null, fileName: string | null) => RowAttempt;
  deleteBios: (slug: string) => void;
  deleteBiosFile: (slug: string, fileName: string) => void;
  removeShortcuts: (row: PlatformRow) => void;
  deleteSaves: (row: PlatformRow) => void;
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
function announceBiosChange(platformSlug: string): void {
  globalThis.dispatchEvent(
    new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: platformSlug } }),
  );
}

/** Synced above Available, alphabetical inside each, taken from the payload
 *  rather than from live state so a later toggle cannot reorder it. */
function freezeGroups(platforms: PlatformSyncSetting[]): FrozenGroups {
  const byName = [...platforms].sort((a, b) => a.name.localeCompare(b.name));
  return {
    synced: byName.filter((p) => p.sync_enabled).map((p) => p.slug),
    available: byName.filter((p) => !p.sync_enabled).map((p) => p.slug),
  };
}

export function usePlatformsPage(): PlatformsPageState {
  const [platforms, setPlatforms] = useState<PlatformSyncSetting[]>([]);
  const [groups, setGroups] = useState<FrozenGroups | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  // The two things known about the BIOS answers, kept apart because they are
  // two different statements: what each platform's answer IS (`firmware`, a
  // landed entry) and which reads did not come back (`readFailed`). A slug in
  // neither is a question still out.
  const [firmware, setFirmware] = useState<Record<string, FirmwarePlatformExt>>({});
  const [readFailed, setReadFailed] = useState<Record<string, true>>({});
  const [serverOffline, setServerOffline] = useState(false);
  const [downloadPending, setDownloadPending] = useState<string | null>(null);
  const [downloadFailed, setDownloadFailed] = useState<string | null>(null);
  // What the latest attempt did to a row, by `slug|place:…` and `slug|file:…`.
  const [attempts, setAttempts] = useState<Record<string, RowAttempt>>({});
  // The platforms whose links were brought up to date on this visit; each is
  // asked once, after its answer is shown.
  const linksUpdated = useRef<Set<string>>(new Set());
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
  // Both counts in one entry rather than two parallel maps: they come from one
  // answer about one platform, and split across two states a failed write to
  // either would leave the header disagreeing with the Remove button.
  const [shortcutCounts, setShortcutCounts] = useState<Record<string, { bound: number; reachable: number }>>({});
  const [shortcutCountsFailed, setShortcutCountsFailed] = useState(false);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [cores, setCores] = useState<Record<string, SystemCoreInfo | null>>({});
  const [saveCounts, setSaveCounts] = useState<Record<string, number | null>>({});
  const [status, setStatus] = useState<DetailStatus | null>(null);
  const [listStatus, setListStatus] = useState<string | null>(null);
  // Touched only in handlers and in answers, never during render.
  const syncWrites = useLatestWrites<boolean>();
  const lines = useLineWrites();
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [removalProgress, setRemovalProgress] = useState<{ slug: string; removed: number; total: number } | null>(null);

  // Which slugs a core read has already been issued for. A ref, not state:
  // walking the list issues one read per row and the guard must hold within a
  // single render pass, before any answer has come back.
  const coreRequested = useRef<Set<string>>(new Set());
  const saveCountRequested = useRef<Set<string>>(new Set());

  // False from the moment the page is torn down. Every write below is guarded on
  // it, and so is the walk: an answer that lands after the reader has left is
  // DROPPED rather than written, and no further platform is asked for. Leaving
  // and coming back builds a fresh page whose own answers must not be
  // overwritten by the ones the previous visit was still waiting on.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  // One counter per platform, taken when a read is ISSUED. Two reads for one
  // platform are ordered by it, which is what a download's re-read needs: the
  // answer the walk asked for before the file landed is still in flight, and
  // without this it overwrites the fresh one — a wrong dot, no error, nothing to
  // see. It answers ordering only; the slug itself is what binds an answer to
  // its platform.
  const readSeq = useRef<Map<string, number>>(new Map());
  // Which platforms the walk has issued a read for. A ref, not state: the walk
  // picks the next platform between awaits and must see its own progress
  // immediately, before any answer has landed.
  const asked = useRef<Set<string>>(new Set());

  /** May an answer for *slug* be written — is this page still open, and is this
   *  still the newest read issued for that platform? */
  const accept = useCallback((slug: string, seq: number) => alive.current && readSeq.current.get(slug) === seq, []);

  const readPlatform = useCallback(
    async (slug: string) => {
      asked.current.add(slug);
      const seq = (readSeq.current.get(slug) ?? 0) + 1;
      readSeq.current.set(slug, seq);
      try {
        const result = await getPlatformFirmwareStatus(slug);
        if (!accept(slug, seq)) return;
        if (!result.success) {
          // Unreachable today — the endpoint always answers success — but a
          // dropped failure would leave the row waiting for an answer that is
          // never coming.
          logWarn(`BIOS state for ${slug} answered a failure: ${result.message ?? "no message"}`);
          setReadFailed((prev) => ({ ...prev, [slug]: true }));
          return;
        }
        setFirmware((prev) => ({ ...prev, [slug]: result.platform }));
        setReadFailed((prev) => {
          if (!(slug in prev)) return prev;
          const next = { ...prev };
          delete next[slug];
          return next;
        });
      } catch (e) {
        logWarn(`Failed to read the BIOS state for ${slug}: ${e}`);
        if (!accept(slug, seq)) return;
        // The previous answer, where there is one, is deliberately left in
        // place: the pane says it may be out of date, which is more use than
        // taking the rows away.
        setReadFailed((prev) => ({ ...prev, [slug]: true }));
      }
    },
    [accept],
  );

  // What a failure here leaves offered: `docs/architecture/qam-panel.md`,
  // section Library.
  const readOverview = useCallback(async () => {
    try {
      const result = await getFirmwareStatus();
      if (!alive.current) return;
      if (!result.success) {
        logWarn(`Firmware overview answered a failure: ${result.message ?? "no message"}`);
        return;
      }
      setServerOffline(result.server_offline ?? false);
    } catch (e) {
      logWarn(`Failed to read the firmware overview: ${e}`);
    }
  }, []);

  const refreshShortcutCounts = useCallback(async () => {
    try {
      const result = await getRegistryPlatforms();
      setShortcutCounts(
        Object.fromEntries(result.platforms.map((p) => [p.slug, { bound: p.count, reachable: p.reachable_count }])),
      );
      setShortcutCountsFailed(false);
    } catch (e) {
      logWarn(`Failed to read platform shortcut counts: ${e}`);
      setShortcutCountsFailed(true);
    }
  }, []);

  // The platform the reader is on, for the walk below to prefer. A ref because
  // the walk picks its next platform between awaits and has to see the
  // selection as it is THEN, not as it was when the walk started.
  const selectedRef = useRef<string | null>(null);
  useEffect(() => {
    selectedRef.current = selectedSlug;
  }, [selectedSlug]);

  // Read every listed platform's BIOS answer, one at a time, in the order the
  // list shows them — preferring whichever row the reader is looking at, which
  // is the one whose pane is open and should not wait behind the rows above it.
  //
  // One at a time: every answer is a live reading of the machine for that
  // system, too costly to pay for every platform at once (measured in
  // `docs/architecture/qam-panel.md`, "Library"), and running them together
  // would take away the one thing that makes the focused row cheap, which is
  // being able to choose what goes next. Whether a parallel walk would finish
  // the whole list sooner is unmeasured.
  //
  // The order is the frozen groups', which is the list's own: the tab renders
  // Synced above Available, each in the order `freezeGroups` left it.
  useEffect(() => {
    if (groups === null) return;
    const order = [...groups.synced, ...groups.available];
    let walking = true;
    const next = () => {
      const focused = selectedRef.current;
      if (focused !== null && !asked.current.has(focused)) return focused;
      return order.find((slug) => !asked.current.has(slug)) ?? null;
    };
    const walk = async () => {
      for (let slug = next(); slug !== null && walking && alive.current; slug = next()) {
        await readPlatform(slug);
      }
    };
    detach(walk());
    return () => {
      walking = false;
    };
  }, [groups, readPlatform]);

  const loadCore = useCallback((slug: string) => {
    if (coreRequested.current.has(slug)) return;
    coreRequested.current.add(slug);
    getSystemCoreInfo(slug)
      .then((info) => setCores((prev) => ({ ...prev, [slug]: info })))
      .catch((e) => {
        logWarn(`Failed to read the core for ${slug}: ${e}`);
        setCores((prev) => ({ ...prev, [slug]: null }));
      });
  }, []);

  const loadSaveCount = useCallback((slug: string) => {
    if (saveCountRequested.current.has(slug)) return;
    saveCountRequested.current.add(slug);
    // Back to unread while the read is in flight, which matters only on a
    // retry: the previous answer is a `null` failure, and leaving it in place
    // would show "could not be read" for the whole of the second attempt — the
    // three-state distinction the button is built on, collapsed to two at
    // exactly the moment the third state is true. `reloadCore` clears its slot
    // for the same reason.
    setSaveCounts((prev) => {
      if (!(slug in prev)) return prev;
      const next = { ...prev };
      delete next[slug];
      return next;
    });
    countPlatformSaves(slug)
      .then((result) => setSaveCounts((prev) => ({ ...prev, [slug]: result.count })))
      .catch((e) => {
        logWarn(`Failed to count the save files for ${slug}: ${e}`);
        // Forget the slug, so walking away and coming back asks again. Every
        // other failure on this pane is recovered by reopening the page; this
        // one is recovered by re-selecting the platform, which is cheaper, and
        // the line under the button says so.
        saveCountRequested.current.delete(slug);
        setSaveCounts((prev) => ({ ...prev, [slug]: null }));
      });
  }, []);

  /** Ask again after a delete: the count the button showed is now spent. */
  const reloadSaveCount = useCallback(
    (slug: string) => {
      saveCountRequested.current.delete(slug);
      loadSaveCount(slug);
    },
    [loadSaveCount],
  );

  const reloadCore = useCallback(
    (slug: string) => {
      coreRequested.current.delete(slug);
      setCores((prev) => {
        const next = { ...prev };
        delete next[slug];
        return next;
      });
      loadCore(slug);
    },
    [loadCore],
  );

  useEffect(() => {
    mountPruneLeaseOwner(LEASE_OWNER);
    getPlatforms()
      .then((result) => {
        if (!result.success) {
          setFailed(true);
          return;
        }
        syncWrites.seed(result.platforms.map((p) => [p.slug, p.sync_enabled]));
        setPlatforms(result.platforms);
        const frozen = freezeGroups(result.platforms);
        setGroups(frozen);
        const first = frozen.synced[0] ?? frozen.available[0] ?? null;
        setSelectedSlug(first);
        if (first) {
          loadCore(first);
          loadSaveCount(first);
        }
      })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
    // The BIOS state and the shortcut counts fill in beside the list rather
    // than gating it: the platform read is the only one the tab cannot render
    // without, and the other two are slower (a RomM listing, a per-platform
    // reading of the machine) than the one the user is waiting on.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial async data loads on mount are the standard React pattern; the rule is overzealous here
    detach(readOverview());
    detach(refreshShortcutCounts());
    return () => {
      detach(releasePruneLeasesByOwner(LEASE_OWNER));
    };
  }, [loadCore, loadSaveCount, readOverview, refreshShortcutCounts, syncWrites]);

  /** Where a platform's BIOS answer stands — see {@link FirmwareState}.
   *
   *  Order matters. An answer already held is read first, so a failed re-read
   *  cannot take one back — it is reported beside the answer instead. Then a
   *  failure with nothing behind it. Everything left is still coming. */
  const firmwareStateFor = (slug: string): FirmwareState => {
    if (slug in firmware) return "answered";
    if (readFailed[slug]) return "failed";
    return "pending";
  };

  const rows = new Map<string, PlatformRow>(
    platforms.map((p) => [
      p.slug,
      {
        id: p.id,
        slug: p.slug,
        name: p.name,
        romCount: p.rom_count,
        syncEnabled: p.sync_enabled,
        firmware: firmware[p.slug] ?? null,
        firmwareState: firmwareStateFor(p.slug),
        firmwareStale: readFailed[p.slug] === true && p.slug in firmware,
        shortcutCount: shortcutCountsFailed ? null : (shortcutCounts[p.slug]?.bound ?? 0),
        reachableCount: shortcutCountsFailed ? null : (shortcutCounts[p.slug]?.reachable ?? 0),
      },
    ]),
  );

  const select = useCallback(
    (slug: string) => {
      setSelectedSlug(slug);
      loadCore(slug);
      loadSaveCount(slug);
      // A read that did not come back is retried by picking the platform again
      // — the same recovery the save count offers, and the only one there is:
      // the walk asks each platform once, so nothing else would ever ask again.
      if (readFailed[slug]) detach(readPlatform(slug));
    },
    [loadCore, loadSaveCount, readFailed, readPlatform],
  );

  const coreFor = useCallback((slug: string): CoreAnswer => cores[slug], [cores]);
  const saveCountFor = useCallback((slug: string): SaveCountAnswer => saveCounts[slug], [saveCounts]);

  const listLine = useCallback(() => {
    const speaks = lines.issue(LIST_LINE);
    return (text: string | null) => {
      if (speaks()) setListStatus(text);
    };
  }, [lines]);

  const enter = useCallback(() => {
    lines.enter();
    setListStatus(null);
  }, [lines]);

  // What an answer may change on its switch and on the list's line:
  // `docs/architecture/qam-panel.md` § Library, "Only the latest write speaks".
  const toggleSync = useCallback(
    (row: PlatformRow, enabled: boolean) => {
      const show = (want: boolean) =>
        setPlatforms((prev) => prev.map((p) => (p.slug === row.slug ? { ...p, sync_enabled: want } : p)));
      const write = syncWrites.issue([row.slug], enabled);
      const line = listLine();
      const failed = (text: string) => {
        if (write.isLatest(row.slug)) show(syncWrites.stored(row.slug) ?? !enabled);
        line(text);
      };
      show(enabled);
      detach(
        savePlatformSync(row.id, enabled)
          .then((result) => {
            if (result.success) {
              write.stored();
              line(null);
              return;
            }
            failed(result.message || SYNC_WRITE_FAILED);
          })
          .catch(() => failed(SYNC_WRITE_FAILED)),
      );
    },
    [listLine, syncWrites],
  );

  const setAllSync = useCallback(
    (enabled: boolean) => {
      const write = syncWrites.issue(
        platforms.map((p) => p.slug),
        enabled,
      );
      const line = listLine();
      const failed = (text: string) => {
        setPlatforms((prev) =>
          prev.map((p) =>
            write.isLatest(p.slug) ? { ...p, sync_enabled: syncWrites.stored(p.slug) ?? p.sync_enabled } : p,
          ),
        );
        line(text);
      };
      setPlatforms((prev) => prev.map((p) => ({ ...p, sync_enabled: enabled })));
      detach(
        setAllPlatformsSync(enabled)
          .then((result) => {
            if (result.success) {
              write.stored();
              line(null);
              return;
            }
            failed(result.message || SYNC_WRITE_FAILED);
          })
          .catch(() => failed(SYNC_WRITE_FAILED)),
      );
    },
    [listLine, platforms, syncWrites],
  );

  /** Take back the core line this pick wrote, and only that one. The page holds
   *  a single status, and the clear runs after an await: naming the line means a
   *  line that landed in the meantime is left where it is rather than taken by
   *  a clear that was about something else. */
  const clearCoreLine = useCallback((slug: string) => {
    setStatus((prev) => (prev?.slug === slug && prev.scope === "core" ? null : prev));
  }, []);

  /** Keep what the latest attempt did to each row it touched, replacing what an earlier one said. */
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

  /**
   * Bring *slug*'s links up to date, and read the platform again where that
   * changed anything. Downloads nothing. A refusal — Tender's folder that
   * cannot be reached, a conflict — is left to the notice that already says
   * so; this runs on its own, without a press.
   */
  const updateLinks = useCallback(
    async (slug: string) => {
      try {
        const result = await updateBiosLinks(slug, null);
        if (!alive.current || !result.success) return;
        noteLinkFailures(slug, result.link_failures);
        const said = placedSentence(result);
        if (said !== null) setStatus({ slug, scope: "bios", text: said });
        if (result.placed + result.removed > 0) {
          await readPlatform(slug);
          announceBiosChange(slug);
        }
      } catch (e) {
        logWarn(`Failed to bring the BIOS links for ${slug} up to date: ${e}`);
      }
    },
    [noteLinkFailures, readPlatform],
  );

  // A platform's links are brought up to date the first time its pane is shown
  // with an answer on this visit — after the page stands, never as part of it.
  useEffect(() => {
    if (selectedSlug === null || !(selectedSlug in firmware) || linksUpdated.current.has(selectedSlug)) return;
    linksUpdated.current.add(selectedSlug);
    detach(updateLinks(selectedSlug));
  }, [firmware, selectedSlug, updateLinks]);

  const changeCore = useCallback(
    (slug: string, pickedLabel: string) => {
      const answer = cores[slug];
      // Picking the default-marked emulator clears the per-platform override
      // (empty label → follow the es_systems default); any other pins it.
      const defaultLabel = answer?.emulators.find((e) => e.is_default)?.label;
      const label = pickedLabel === defaultLabel ? "" : pickedLabel;
      setBusySlug(slug);
      setStatus({ slug, scope: "core", text: `Switching to ${emulatorName(answer?.emulators ?? [], pickedLabel)}…` });
      // Captured before the continuation starts, so the catch can tell a
      // cancelled continuation from a failed switch.
      const admission = capturePruneLeaseAdmission(LEASE_OWNER);
      detach(debugLog(`setSystemCore: slug=${slug} label=${label} (selected=${pickedLabel})`));
      detach(
        (async () => {
          try {
            const result = await setSystemCore(slug, label);
            detach(debugLog(`setSystemCore: result success=${result.success}`));
            if (!result.success) {
              // Nothing below re-bakes a failed switch, so every live shortcut
              // keeps its old command: report the failure rather than leaving
              // the detail looking as though the switch took effect.
              setStatus({ slug, scope: "core", text: result.message });
              return;
            }
            // Re-bake launch_options for every affected installed ROM on this
            // platform. The backend returns the fresh command per bound
            // shortcut; confirm-set each so existing shortcuts launch with the
            // new core. Bounded-concurrency batches, so a platform with many
            // ROMs does not serialize worst-case per-shortcut confirm-poll
            // timeouts.
            await withPruneLease(
              result.prune_lease_token,
              "setSystemCore",
              (signal) => batchConfirmLaunchOptions(result.rebake_items ?? [], "setSystemCore", signal),
              LEASE_OWNER,
              admission,
            );
            clearCoreLine(slug);
            reloadCore(slug);
            // A different core wants different BIOS files, at other places: its
            // links are placed from the store, nothing is downloaded, and the
            // table below the picker is stale until this platform is read again.
            await updateLinks(slug);
            await readPlatform(slug);
            globalThis.dispatchEvent(
              new CustomEvent("romm_data_changed", { detail: { type: "core_changed", platform_slug: slug } }),
            );
          } catch (e) {
            // Leaving the page cancels the continuation, which is teardown
            // rather than a failure: the switch either committed or never ran
            // (`isPruneLeaseCancellation`), and either way there is no pane
            // left to report to. The line the pick put up goes with it.
            if (isPruneLeaseCancellation(e, admission)) {
              logWarn(`Core switch continuation was cancelled: ${e}`);
              clearCoreLine(slug);
              return;
            }
            detach(debugLog(`setSystemCore: error: ${e}`));
            setStatus({ slug, scope: "core", text: "Could not change the core" });
          } finally {
            setBusySlug(null);
          }
        })(),
      );
    },
    [clearCoreLine, cores, readPlatform, reloadCore, updateLinks],
  );

  /** Hold the pressed button on a red `Failed` for a moment before everything
   *  returns: a spinner that simply stops is not a message, and the two seconds
   *  are what makes the outcome legible on a pane the reader is looking at. */
  const reportFailure = useCallback((pending: string) => {
    setDownloadPending(null);
    setDownloadFailed(pending);
    failedTimer.current = setTimeout(() => {
      failedTimer.current = null;
      setDownloadFailed(null);
      setBusySlug(null);
    }, FAILED_NOTICE_MS);
  }, []);

  const runDownload = useCallback(
    (slug: string, pending: string, work: () => Promise<FirmwareDownloadResult | EndpointFailure>) => {
      setBusySlug(slug);
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
              await readPlatform(slug);
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
              setBusySlug(null);
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
    [noteLinkFailures, readPlatform, reportFailure],
  );

  const downloadRequired = useCallback(
    (slug: string, emulator: string | null) =>
      runDownload(slug, "required", () => downloadRequiredFirmware(slug, emulator)),
    [runDownload],
  );
  const downloadAll = useCallback(
    (slug: string) => runDownload(slug, "all", () => downloadAllFirmware(slug, null)),
    [runDownload],
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
      setBusySlug(slug);
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
            await readPlatform(slug);
            announceBiosChange(slug);
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Failed to replace the file: ${e}` });
          } finally {
            setBusySlug(null);
          }
        })(),
      );
    },
    [noteLinkFailures, readPlatform],
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
      setBusySlug(slug);
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
              await readPlatform(slug);
              if ((result.deleted_count ?? 0) > 0) announceBiosChange(slug);
            }
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Failed to delete BIOS files: ${e}` });
          } finally {
            setBusySlug(null);
          }
        })(),
      );
    },
    [readPlatform],
  );

  /** One row's Delete. A success is said by the rows coming back re-read; only
   *  a failure gets a line, and a `delete_incomplete` gets both, because it may
   *  have removed files or dropped records before the file it stopped on. */
  const runRowDelete = useCallback(
    (slug: string, work: () => Promise<BiosDeleteResult>, what: string) => {
      setBusySlug(slug);
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await work();
            const kept = result.success ? keptLines(result.kept) : [];
            if (!result.success) setStatus({ slug, scope: "bios", text: result.message });
            else if (kept.length > 0) setStatus({ slug, scope: "bios", text: kept.join("\n") });
            if (result.success || result.reason === "delete_incomplete") {
              await readPlatform(slug);
              if ((result.deleted_count ?? 0) > 0) announceBiosChange(slug);
            }
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Failed to delete ${what}: ${e}` });
          } finally {
            setBusySlug(null);
          }
        })(),
      );
    },
    [readPlatform],
  );

  const deleteBiosFile = useCallback(
    (slug: string, fileName: string) => {
      runRowDelete(slug, () => deleteBiosFileCall(slug, fileName), fileName);
    },
    [runRowDelete],
  );

  const removeShortcuts = useCallback(
    (row: PlatformRow) => {
      setBusySlug(row.slug);
      setStatus({ slug: row.slug, scope: "remove", text: `Removing ${row.name} shortcuts…` });
      const admission = capturePruneLeaseAdmission(LEASE_OWNER);
      detach(
        (async () => {
          try {
            const result = await removePlatformShortcuts(row.slug);
            // A refusal by the update, migration, sync or prune rule answers
            // { success: false, reason, message } with no app_ids/rom_ids —
            // surface that message instead of cosmetically reporting a removal.
            if (!result.success) {
              setStatus({ slug: row.slug, scope: "remove", text: result.message ?? "Failed to remove shortcuts" });
              return;
            }
            await withPruneLease(
              result.prune_lease_token,
              "Platform shortcut removal",
              async (signal) => {
                await removeShortcutsPaced(
                  result.app_ids ?? [],
                  (removed, total) => setRemovalProgress({ slug: row.slug, removed, total }),
                  signal,
                );
                if (isPruneLeaseCancelled(signal)) return;
                await clearPlatformCollection(result.platform_name || row.name, signal);
                if (isPruneLeaseCancelled(signal)) return;
                if (result.rom_ids?.length || result.prune_lease_token) {
                  await withTimeout(
                    reportRemovalResults(result.rom_ids ?? [], result.prune_lease_token ?? null),
                    REMOVAL_REPORT_TIMEOUT_MS,
                  );
                }
              },
              LEASE_OWNER,
              admission,
            );
            // What was actually removed, not what the pane thought was there:
            // the shortcut count is a read of its own and may not have landed.
            const removed = result.app_ids?.length ?? 0;
            setStatus({
              slug: row.slug,
              scope: "remove",
              text: `Removed ${removed} ${row.name} game${removed === 1 ? "" : "s"}`,
            });
            await refreshShortcutCounts();
          } catch (e) {
            // Leaving the page cancels the removal continuation — the backend
            // removal already committed, so this is teardown, not a failure.
            if (isPruneLeaseCancellation(e, admission)) {
              logWarn(`Platform shortcut removal continuation was cancelled: ${e}`);
              return;
            }
            setStatus({ slug: row.slug, scope: "remove", text: "Failed to remove shortcuts" });
          } finally {
            setBusySlug(null);
            setRemovalProgress(null);
          }
        })(),
      );
    },
    [refreshShortcutCounts],
  );

  const deleteSaves = useCallback(
    (row: PlatformRow) => {
      setBusySlug(row.slug);
      setStatus({ slug: row.slug, scope: "remove", text: `Deleting ${row.name} saves…` });
      detach(
        (async () => {
          try {
            const result = await deletePlatformSaves(row.slug);
            setStatus({ slug: row.slug, scope: "remove", text: result.message });
            globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync" } }));
          } catch {
            setStatus({ slug: row.slug, scope: "remove", text: "Failed to delete saves" });
          } finally {
            setBusySlug(null);
            reloadSaveCount(row.slug);
          }
        })(),
      );
    },
    [reloadSaveCount],
  );

  return {
    rows,
    groups,
    loading,
    failed,
    serverOffline,
    shortcutCountsFailed,
    selectedSlug,
    select,
    coreFor,
    saveCountFor,
    status,
    listStatus,
    enter,
    busySlug,
    removalProgress,
    toggleSync,
    setAllSync,
    changeCore,
    downloadPending,
    downloadFailed,
    downloadRequired,
    downloadAll,
    downloadOne,
    replaceBios,
    attemptFor,
    deleteBios,
    deleteBiosFile,
    removeShortcuts,
    deleteSaves,
  };
}
