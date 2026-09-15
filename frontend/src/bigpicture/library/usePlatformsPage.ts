/**
 * Everything the Library page's Platforms tab knows and does: the three
 * list-shaped reads it joins per platform, the per-selection core read, and the
 * actions a platform's detail offers.
 *
 * It lives above the tab boundary because Steam's tabbed page renders only the
 * active tab and keys it by tab id, so switching tabs unmounts the content and
 * mounting it again would re-issue every read and thaw the list's frozen order.
 * The page holds the state; the tab and its detail render it.
 *
 * The three reads answer different questions and each covers a set the others
 * do not: `get_platforms` is RomM's platforms with ROMs (the list itself),
 * `get_firmware_status` WHICH platforms have a BIOS answer to give, and
 * `get_registry_platforms` the ROMs bound to a Steam shortcut per platform —
 * which is also what "this platform has synced games" means here, and the only
 * one of the three that answers it for every platform in the list.
 *
 * What a platform's BIOS answer IS costs a live reading of the machine for that
 * system, so it is a fourth read, asked one platform at a time
 * (`get_platform_firmware_status`) while the page already stands. This module
 * owns the order they are asked in, when they stop, and which of four states
 * each row is in meanwhile — see {@link FirmwareState}.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  countPlatformSaves,
  debugLog,
  deleteBiosFile as deleteBiosFileCall,
  deleteBiosFolder as deleteBiosFolderCall,
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
  reportRemovalResults,
  savePlatformSync,
  setAllPlatformsSync,
  setSystemCore,
} from "../../api/backend";
import type { FirmwarePlatformExt, PlatformSyncSetting, SystemCoreInfo } from "../../types";
import { detach } from "../../utils/detach";
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

/** How long the pressed button says `Failed` before everything returns. Two of
 *  the two-to-three seconds the device pass asked for: long enough to read one
 *  word on a pane the reader is already looking at, and the pane stays busy for
 *  exactly that long, so the other download buttons come back when the word
 *  goes rather than a moment earlier. */
const FAILED_NOTICE_MS = 2000;

const LEASE_OWNER = "library-platforms";
const REMOVAL_REPORT_TIMEOUT_MS = 15000;

/** What a sync write that did not take says when there is no answer to quote.
 *  A refusal always carries a message (`.claude/rules/callables.md`) and that
 *  message is what the reader gets. A REJECTION has no answer at all — the call
 *  never returned one — and the revert would otherwise happen in silence. */
export const SYNC_WRITE_FAILED = "Could not save that; the change was undone.";

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
 * - `answered` — {@link PlatformRow.firmware} holds it.
 * - `nothing` — a finished answer with nothing to say about this platform.
 * - `failed` — the read did not come back and there is nothing behind it.
 *
 * An answer already held outranks a failure, so a failed RE-read leaves the
 * state where it was and raises {@link PlatformRow.firmwareStale} instead:
 * "nothing is known about this platform" is an answer, and taking it back
 * because a later read failed is how a pane loses an answer it has.
 */
export type FirmwareState = "pending" | "answered" | "nothing" | "failed";

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
  /** Whether {@link firmware} is an answer, a question still out, a finished
   *  "nothing to say", or a read that did not come back. The list's dot and the
   *  detail's BIOS block are worded off this, never off `firmware` being null —
   *  three of the four states share that. */
  firmwareState: FirmwareState;
  /** Whether the last read for this platform failed over an answer it still
   *  holds — so what is shown is the state from before whatever changed it.
   *  Distinct from `firmwareState: "failed"`, which is a platform with no
   *  answer at all: one pane warns about its rows, the other has none. */
  firmwareStale: boolean;
  /** How many of the platform's ROMs are bound to a Steam shortcut, or `null`
   *  when that read failed. `null` is not zero: read as zero it withdraws the
   *  core picker and disables the shortcut removal — two claims about a platform
   *  nothing was learned about.
   *
   *  This is the count of SHORTCUTS, so it is what the Remove group says and acts
   *  on. The header line states `reachableCount` instead. */
  shortcutCount: number | null;
  /** How many of the platform's ROMs are reachable from Steam — every version in
   *  a sibling group that holds a binding, since one shortcut serves the group
   *  and the game's page switches versions across it. This is what the header
   *  line states; `null` (the same read failure as `shortcutCount`) drops that
   *  half of the line rather than printing a zero nothing established.
   *
   *  A version RomM no longer serves keeps its row, and is excluded: the picker
   *  refuses a switch to it, so nothing reaches it. What the backend can establish
   *  is what the platform's last completed fetch returned, which leaves one
   *  window open — a version dropped from RomM since that fetch is already gone
   *  from `romCount` while still counted here, so the line can read right >
   *  left until the next sync of that platform. Closing it would need a server
   *  call this read deliberately does not make. */
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
   * Cleared by the next sync write that succeeds — a line about a toggle the
   * reader has since put right is a line about nothing.
   */
  listStatus: string | null;
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
   * token on both sides, and `@prune_active_blocked` refuses on a prune *run*
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
   * sentence.
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
  downloadRequired: (slug: string) => void;
  downloadAll: (slug: string) => void;
  downloadOne: (slug: string, fileName: string) => void;
  deleteBios: (slug: string) => void;
  deleteBiosFile: (slug: string, fileName: string) => void;
  deleteBiosFolder: (slug: string, folderPath: string) => void;
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
 * `check_platform_bios` for it (#1082), so a run that moved no files must stay
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
  // The three things known about the BIOS answers, kept apart because they are
  // three different statements: which platforms the page can speak for at all
  // (`named`, null until the overview lands), what each one's answer IS
  // (`firmware`, a landed entry or an explicit `null` for "nothing to say"), and
  // which reads did not come back (`readFailed`). A slug in none of them is a
  // question still out.
  const [named, setNamed] = useState<Set<string> | null>(null);
  const [firmware, setFirmware] = useState<Record<string, FirmwarePlatformExt | null>>({});
  const [readFailed, setReadFailed] = useState<Record<string, true>>({});
  const [serverOffline, setServerOffline] = useState(false);
  const [overviewFailed, setOverviewFailed] = useState(false);
  const [downloadPending, setDownloadPending] = useState<string | null>(null);
  const [downloadFailed, setDownloadFailed] = useState<string | null>(null);
  // Cleared on unmount, so a page left during the window cannot set state on a
  // gone component. A plain timer is the right tool here: the injected-clock
  // rule is scoped to `py_modules/services/**`, and this file's siblings
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
          // Unreachable today — the callable always answers success — but a
          // dropped failure is indistinguishable from a platform the backend has
          // nothing to say about, which is a finished answer and reads green.
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

  const readOverview = useCallback(async () => {
    try {
      const result = await getFirmwareStatus();
      if (!alive.current) return;
      if (!result.success) {
        logWarn(`Firmware overview answered a failure: ${result.message ?? "no message"}`);
        setOverviewFailed(true);
        return;
      }
      setServerOffline(result.server_offline ?? false);
      setNamed(new Set(result.platforms.map((p) => p.platform_slug)));
      setOverviewFailed(false);
    } catch (e) {
      logWarn(`Failed to read the firmware overview: ${e}`);
      if (!alive.current) return;
      setOverviewFailed(true);
    }
  }, []);

  const refreshShortcutCounts = useCallback(async () => {
    try {
      const result = await getRegistryPlatforms();
      setShortcutCounts(
        Object.fromEntries(
          result.platforms.map((p) => [p.slug, { bound: p.count, reachable: p.reachable_count ?? p.count }]),
        ),
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

  // Read each platform's BIOS answer, one at a time, in the order the list is in
  // — preferring whichever row the reader is looking at, which is the one whose
  // pane is open and should not wait behind the rows above it.
  //
  // One at a time: every answer is a live reading of the machine for that
  // system — 106-486 ms per platform, measured on the reference device — and
  // running them together would take away the one thing that makes the focused
  // row cheap, which is being able to choose what goes next. Whether a parallel
  // walk would finish the whole list sooner is unmeasured.
  //
  // A platform the overview names but the list does not show is never asked:
  // RomM files firmware under its own directory names, so a key can belong to
  // no row on this page, and no row means nothing to render the answer on.
  useEffect(() => {
    if (named === null || groups === null) return;
    const order = [...groups.synced, ...groups.available].filter((slug) => named.has(slug));
    let walking = true;
    const next = () => {
      const focused = selectedRef.current;
      if (focused !== null && named.has(focused) && !asked.current.has(focused)) return focused;
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
  }, [groups, named, readPlatform]);

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
  }, [loadCore, loadSaveCount, readOverview, refreshShortcutCounts]);

  /** Where a platform's BIOS answer stands — see {@link FirmwareState}.
   *
   *  Order matters. An answer already held is read first, so a failed re-read
   *  cannot take one back — it is reported beside the answer instead. Then a
   *  failure with nothing behind it, which includes every row when the overview
   *  itself did not land. Then a platform the overview did not name, which is a
   *  finished "nothing to say". Everything left is still coming, including every
   *  row while the overview is in flight. */
  const firmwareStateFor = (slug: string): FirmwareState => {
    if (slug in firmware) return firmware[slug] !== null ? "answered" : "nothing";
    if (named !== null && !named.has(slug)) return "nothing";
    if (readFailed[slug] || overviewFailed) return "failed";
    return "pending";
  };

  /** Is an answer for this platform held at all? Two ways to hold one: the
   *  platform answered for itself, or the overview did not name it, which is the
   *  page saying it has nothing to manage here. */
  const firmwareHeldFor = (slug: string) => slug in firmware || (named !== null && !named.has(slug));

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
        firmwareStale: readFailed[p.slug] === true && firmwareHeldFor(p.slug),
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
      // The pane's line says so, and it says so on a pane whose platform was
      // never asked at all because the overview did not land — so that is
      // retried here too, and the walk follows it.
      if (readFailed[slug]) detach(readPlatform(slug));
      else if (overviewFailed) detach(readOverview());
    },
    [loadCore, loadSaveCount, overviewFailed, readFailed, readOverview, readPlatform],
  );

  const coreFor = useCallback((slug: string): CoreAnswer => cores[slug], [cores]);
  const saveCountFor = useCallback((slug: string): SaveCountAnswer => saveCounts[slug], [saveCounts]);

  // Disable all has to be able to put the list back exactly as it was, which no
  // functional update can reconstruct. The snapshot is written in an effect
  // rather than during render, and is read only from an event handler — after
  // the commit that wrote it.
  const platformsRef = useRef<PlatformSyncSetting[]>([]);
  useEffect(() => {
    platformsRef.current = platforms;
  }, [platforms]);

  // Both sync writes below treat a refusal and a rejection as one outcome: the
  // write did not take, the optimistic flip goes back, and the reader is told.
  // A flip that silently returns to where it was is what a toggle that never
  // moved looks like, and neither callable throws to refuse.
  const toggleSync = useCallback((row: PlatformRow, enabled: boolean) => {
    const flip = (want: boolean) =>
      setPlatforms((prev) => prev.map((p) => (p.slug === row.slug ? { ...p, sync_enabled: want } : p)));
    flip(enabled);
    detach(
      savePlatformSync(row.id, enabled)
        .then((result) => {
          if (result.success) {
            setListStatus(null);
            return;
          }
          flip(!enabled);
          setListStatus(result.message || SYNC_WRITE_FAILED);
        })
        .catch(() => {
          flip(!enabled);
          setListStatus(SYNC_WRITE_FAILED);
        }),
    );
  }, []);

  const setAllSync = useCallback((enabled: boolean) => {
    const previous = platformsRef.current;
    setPlatforms((prev) => prev.map((p) => ({ ...p, sync_enabled: enabled })));
    detach(
      setAllPlatformsSync(enabled)
        .then((result) => {
          if (result.success) {
            setListStatus(null);
            return;
          }
          setPlatforms(previous);
          setListStatus(result.message || SYNC_WRITE_FAILED);
        })
        .catch(() => {
          setPlatforms(previous);
          setListStatus(SYNC_WRITE_FAILED);
        }),
    );
  }, []);

  /** Take back the core line this pick wrote, and only that one. The page holds
   *  a single status, and the clear runs after an await: naming the line means a
   *  line that landed in the meantime is left where it is rather than taken by
   *  a clear that was about something else. */
  const clearCoreLine = useCallback((slug: string) => {
    setStatus((prev) => (prev?.slug === slug && prev.scope === "core" ? null : prev));
  }, []);

  const changeCore = useCallback(
    (slug: string, pickedLabel: string) => {
      const answer = cores[slug];
      // Picking the default-marked emulator clears the per-platform override
      // (empty label → follow the es_systems default); any other pins it.
      const defaultLabel = answer?.emulators.find((e) => e.is_default)?.label;
      const label = pickedLabel === defaultLabel ? "" : pickedLabel;
      setBusySlug(slug);
      setStatus({ slug, scope: "core", text: `Switching to ${pickedLabel}…` });
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
              // #1016's frontend half: the switch did not happen and the label
              // still names the old core, so say so rather than leaving the
              // detail looking as though the pick landed.
              setStatus({ slug, scope: "core", text: result.message ?? "Could not change the core" });
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
            // A different core wants different BIOS files, so the table
            // below the picker is stale until this platform is read again.
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
    [clearCoreLine, cores, readPlatform, reloadCore],
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
    (
      slug: string,
      pending: string,
      work: () => Promise<{ success: boolean; message?: string; downloaded?: number }>,
    ) => {
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
            // re-read. Only a failure gets words, because a stopped spinner is
            // not a message and the notice that used to carry one is gone.
            if (result.success) {
              await readPlatform(slug);
              if ((result.downloaded ?? 0) > 0) announceBiosChange(slug);
              setDownloadPending(null);
              setBusySlug(null);
              return;
            }
            setStatus({ slug, scope: "bios", text: result.message ?? "Download failed" });
            reportFailure(pending);
          } catch (e) {
            setStatus({ slug, scope: "bios", text: `Download failed: ${e}` });
            reportFailure(pending);
          }
        })(),
      );
    },
    [readPlatform, reportFailure],
  );

  const downloadRequired = useCallback(
    (slug: string) => runDownload(slug, "required", () => downloadRequiredFirmware(slug)),
    [runDownload],
  );
  const downloadAll = useCallback(
    (slug: string) => runDownload(slug, "all", () => downloadAllFirmware(slug)),
    [runDownload],
  );
  const downloadOne = useCallback(
    (slug: string, fileName: string) => runDownload(slug, fileName, () => downloadPlatformFirmwareFile(slug, fileName)),
    [runDownload],
  );

  const deleteBios = useCallback(
    (slug: string) => {
      setBusySlug(slug);
      detach(
        (async () => {
          try {
            const result = await deletePlatformBios(slug);
            setStatus({ slug, scope: "bios", text: result.message });
            if (result.success) {
              await readPlatform(slug);
              // Only when something went, the same rule the row deletes and the
              // downloads keep: the event costs every mounted game panel a live
              // BIOS read, so a run that moved no files stays silent.
              if (result.deleted_count > 0) announceBiosChange(slug);
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

  /** One row's Delete, whichever end it addresses: the same success and failure
   *  handling for a file and for a folder's recorded contents. A success is
   *  said by the rows coming back re-read; only a failure gets a line. */
  const runRowDelete = useCallback(
    (slug: string, work: () => Promise<{ success: boolean; message: string; deleted_count: number }>, what: string) => {
      setBusySlug(slug);
      setStatus(null);
      detach(
        (async () => {
          try {
            const result = await work();
            if (result.success) {
              await readPlatform(slug);
              // Only when something actually went. The event fans out to every
              // mounted panel and each matching one pays a live
              // `check_platform_bios` for it, so a run that moved no files must
              // stay silent — the same rule the download path four lines up
              // keeps.
              if (result.deleted_count > 0) announceBiosChange(slug);
            } else {
              setStatus({ slug, scope: "bios", text: result.message });
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

  const deleteBiosFolder = useCallback(
    (slug: string, folderPath: string) => {
      runRowDelete(slug, () => deleteBiosFolderCall(slug, folderPath), "these files");
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
            // The @migration_blocked / @sync_active_blocked gates short-circuit
            // to { success: false, message, ... } with no app_ids/rom_ids —
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
    deleteBios,
    deleteBiosFile,
    deleteBiosFolder,
    removeShortcuts,
    deleteSaves,
  };
}
