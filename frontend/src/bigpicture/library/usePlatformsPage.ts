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
  deletePlatformSaves,
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
  updateBiosLinks,
} from "../../api/backend";
import type { FirmwarePlatformExt, PlatformSyncSetting, SystemCoreInfo } from "../../types";
import { placedSentence } from "../../utils/biosPlaceWords";
import { detach } from "../../utils/detach";
import { clearPlatformCollection } from "../../utils/collections";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancellation,
  isPruneLeaseCancelled,
  mountPruneLeaseOwner,
  releasePruneLeasesByOwner,
  withPruneLease,
} from "../../utils/pruneLease";
import { biosAnswerStale, biosReadState, type BiosReadState } from "../../utils/biosReadState";
import { removeShortcutsPaced } from "../../utils/shortcutRemoval";
import { withTimeout } from "../../utils/withTimeout";
import {
  announceBiosChange,
  announceCoreChange,
  useBiosActions,
  type BiosActions,
  type PlatformKey,
} from "../bios/useBiosActions";
import { useLatestWrites, useLineWrites } from "./latestWrites";
import { SYNC_WRITE_FAILED } from "./syncWriteFailed";

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
  folder: string;
  scope: StatusScope;
  text: string;
  /** The line carries its operation's throbber. */
  busy?: boolean;
}

/** Where one platform's BIOS answer stands — see {@link BiosReadState}. Every row
 *  starts out `pending`, because the answers arrive one platform at a time. */
export type FirmwareState = BiosReadState;

/** One platform as the list and the detail read it — the three reads joined. */
export interface PlatformRow {
  id: number;
  slug: string;
  /** The folder RomM keeps the platform in, which names it on this page: two
   *  listed platforms can share a slug, and each is a row of its own (#2452). */
  folder: string;
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

export interface PlatformsPageState extends BiosActions {
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
  selectedFolder: string | null;
  select: (folder: string) => void;
  coreFor: (folder: string) => CoreAnswer;
  saveCountFor: (folder: string) => SaveCountAnswer;
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
   * writes are outside the hold. The folder is what lets a pane that is not the
   * one acting say why its buttons are dead.
   *
   * **What forbids running two at once is this page's own state, not a lock
   * anywhere else.** `status`, `removalProgress` and `busyFolder` are each
   * singular, so a second action would overwrite the first's line and the first
   * `finally` would clear the busy state out from under the second. The prune
   * lease is no obstacle and must not be cited as one: leases are keyed by
   * token on both sides, and the prune rule refuses on a prune *run*
   * reservation rather than on a sibling lease, so two platform removals would
   * both be admitted. Making these three per-folder is what a per-platform
   * disable would take.
   */
  busyFolder: string | null;
  /** Bound to its platform for the reason {@link DetailStatus} is: walking the
   *  list under a running removal must not show its progress on another
   *  platform's pane. */
  removalProgress: { folder: string; removed: number; total: number } | null;
  toggleSync: (row: PlatformRow, enabled: boolean) => void;
  setAllSync: (enabled: boolean) => void;
  changeCore: (row: PlatformKey, pickedLabel: string) => void;
  removeShortcuts: (row: PlatformRow) => void;
  deleteSaves: (row: PlatformRow) => void;
}

/** Synced above Available, alphabetical inside each, taken from the payload
 *  rather than from live state so a later toggle cannot reorder it. */
function freezeGroups(platforms: PlatformSyncSetting[]): FrozenGroups {
  const byName = [...platforms].sort((a, b) => a.name.localeCompare(b.name));
  return {
    synced: byName.filter((p) => p.sync_enabled).map((p) => p.fs_slug),
    available: byName.filter((p) => !p.sync_enabled).map((p) => p.fs_slug),
  };
}

export function usePlatformsPage(): PlatformsPageState {
  const [platforms, setPlatforms] = useState<PlatformSyncSetting[]>([]);
  const [groups, setGroups] = useState<FrozenGroups | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  // The two things known about the BIOS answers, kept apart because they are
  // two different statements: what each platform's answer IS (`firmware`, a
  // landed entry) and which reads did not come back (`readFailed`). A folder in
  // neither is a question still out.
  const [firmware, setFirmware] = useState<Record<string, FirmwarePlatformExt>>({});
  const [readFailed, setReadFailed] = useState<Record<string, true>>({});
  const [serverOffline, setServerOffline] = useState(false);
  // The platforms whose links were brought up to date on this visit; each is
  // asked once, after its answer is shown.
  const linksUpdated = useRef<Set<string>>(new Set());
  // Both counts in one entry rather than two parallel maps: they come from one
  // answer about one platform, and split across two states a failed write to
  // either would leave the header disagreeing with the Remove button.
  const [shortcutCounts, setShortcutCounts] = useState<Record<string, { bound: number; reachable: number }>>({});
  const [shortcutCountsFailed, setShortcutCountsFailed] = useState(false);
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [cores, setCores] = useState<Record<string, SystemCoreInfo | null>>({});
  const [saveCounts, setSaveCounts] = useState<Record<string, number | null>>({});
  const [status, setStatus] = useState<DetailStatus | null>(null);
  const [listStatus, setListStatus] = useState<string | null>(null);
  // Touched only in handlers and in answers, never during render.
  const syncWrites = useLatestWrites<boolean>();
  const lines = useLineWrites();
  const [busyFolder, setBusyFolder] = useState<string | null>(null);
  const [removalProgress, setRemovalProgress] = useState<{ folder: string; removed: number; total: number } | null>(
    null,
  );

  // Which platforms a core read has already been issued for. A ref, not state:
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
  // see. It answers ordering only; the folder itself is what binds an answer to
  // its platform.
  const readSeq = useRef<Map<string, number>>(new Map());
  // Each listed platform's slug and folder, by its folder, for the reads the page
  // issues by folder alone — the walk, a selection, the links of the pane shown.
  // A ref, filled with the list in the same answer the groups come from, so a
  // folder missing from it is a defect, and it throws rather than ask with "".
  const listed = useRef<Map<string, PlatformKey>>(new Map());
  const platformOf = useCallback((folder: string): PlatformKey => {
    const platform = listed.current.get(folder);
    if (platform === undefined) throw new Error(`${folder} is not a listed platform's folder`);
    return platform;
  }, []);
  // Which platforms the walk has issued a read for. A ref, not state: the walk
  // picks the next platform between awaits and must see its own progress
  // immediately, before any answer has landed.
  const asked = useRef<Set<string>>(new Set());

  /** May an answer for *folder* be written — is this page still open, and is this
   *  still the newest read issued for that platform? */
  const accept = useCallback((folder: string, seq: number) => alive.current && readSeq.current.get(folder) === seq, []);

  const readPlatform = useCallback(
    async (platform: PlatformKey) => {
      const { folder } = platform;
      asked.current.add(folder);
      const seq = (readSeq.current.get(folder) ?? 0) + 1;
      readSeq.current.set(folder, seq);
      try {
        const result = await getPlatformFirmwareStatus(platform.slug, folder);
        if (!accept(folder, seq)) return;
        if (!result.success) {
          // Unreachable today — the endpoint always answers success — but a
          // dropped failure would leave the row waiting for an answer that is
          // never coming.
          logWarn(`BIOS state for ${folder} answered a failure: ${result.message ?? "no message"}`);
          setReadFailed((prev) => ({ ...prev, [folder]: true }));
          return;
        }
        setFirmware((prev) => ({ ...prev, [folder]: result.platform }));
        setReadFailed((prev) => {
          if (!(folder in prev)) return prev;
          const next = { ...prev };
          delete next[folder];
          return next;
        });
      } catch (e) {
        logWarn(`Failed to read the BIOS state for ${folder}: ${e}`);
        if (!accept(folder, seq)) return;
        // The previous answer, where there is one, is deliberately left in
        // place: the pane says it may be out of date, which is more use than
        // taking the rows away.
        setReadFailed((prev) => ({ ...prev, [folder]: true }));
      }
    },
    [accept],
  );
  const bios = useBiosActions({ setBusy: setBusyFolder, setStatus, reread: readPlatform, romId: null });
  const { noteLinkFailures } = bios;

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
        Object.fromEntries(result.platforms.map((p) => [p.fs_slug, { bound: p.count, reachable: p.reachable_count }])),
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
    selectedRef.current = selectedFolder;
  }, [selectedFolder]);

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
      return order.find((folder) => !asked.current.has(folder)) ?? null;
    };
    const walk = async () => {
      for (let folder = next(); folder !== null && walking && alive.current; folder = next()) {
        await readPlatform(platformOf(folder));
      }
    };
    detach(walk());
    return () => {
      walking = false;
    };
  }, [groups, platformOf, readPlatform]);

  const loadCore = useCallback((platform: PlatformKey) => {
    const { folder } = platform;
    if (coreRequested.current.has(folder)) return;
    coreRequested.current.add(folder);
    getSystemCoreInfo(platform.slug, folder)
      .then((info) => setCores((prev) => ({ ...prev, [folder]: info })))
      .catch((e) => {
        logWarn(`Failed to read the core for ${folder}: ${e}`);
        setCores((prev) => ({ ...prev, [folder]: null }));
      });
  }, []);

  const loadSaveCount = useCallback((folder: string) => {
    if (saveCountRequested.current.has(folder)) return;
    saveCountRequested.current.add(folder);
    // Back to unread while the read is in flight, which matters only on a
    // retry: the previous answer is a `null` failure, and leaving it in place
    // would show "could not be read" for the whole of the second attempt — the
    // three-state distinction the button is built on, collapsed to two at
    // exactly the moment the third state is true. `reloadCore` clears its slot
    // for the same reason.
    setSaveCounts((prev) => {
      if (!(folder in prev)) return prev;
      const next = { ...prev };
      delete next[folder];
      return next;
    });
    countPlatformSaves(folder)
      .then((result) => setSaveCounts((prev) => ({ ...prev, [folder]: result.count })))
      .catch((e) => {
        logWarn(`Failed to count the save files for ${folder}: ${e}`);
        // Forget the folder, so walking away and coming back asks again. Every
        // other failure on this pane is recovered by reopening the page; this
        // one is recovered by re-selecting the platform, which is cheaper, and
        // the line under the button says so.
        saveCountRequested.current.delete(folder);
        setSaveCounts((prev) => ({ ...prev, [folder]: null }));
      });
  }, []);

  /** Ask again after a delete: the count the button showed is now spent. */
  const reloadSaveCount = useCallback(
    (folder: string) => {
      saveCountRequested.current.delete(folder);
      loadSaveCount(folder);
    },
    [loadSaveCount],
  );

  const reloadCore = useCallback(
    (platform: PlatformKey) => {
      coreRequested.current.delete(platform.folder);
      setCores((prev) => {
        const next = { ...prev };
        delete next[platform.folder];
        return next;
      });
      loadCore(platform);
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
        syncWrites.seed(result.platforms.map((p) => [p.fs_slug, p.sync_enabled]));
        listed.current = new Map(result.platforms.map((p) => [p.fs_slug, { slug: p.slug, folder: p.fs_slug }]));
        setPlatforms(result.platforms);
        const frozen = freezeGroups(result.platforms);
        setGroups(frozen);
        const first = frozen.synced[0] ?? frozen.available[0] ?? null;
        setSelectedFolder(first);
        if (first) {
          loadCore(platformOf(first));
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
  }, [loadCore, loadSaveCount, platformOf, readOverview, refreshShortcutCounts, syncWrites]);

  const rows = new Map<string, PlatformRow>(
    platforms.map((p) => [
      p.fs_slug,
      {
        id: p.id,
        slug: p.slug,
        folder: p.fs_slug,
        name: p.name,
        romCount: p.rom_count,
        syncEnabled: p.sync_enabled,
        firmware: firmware[p.fs_slug] ?? null,
        firmwareState: biosReadState(p.fs_slug in firmware, readFailed[p.fs_slug] === true),
        firmwareStale: biosAnswerStale(p.fs_slug in firmware, readFailed[p.fs_slug] === true),
        shortcutCount: shortcutCountsFailed ? null : (shortcutCounts[p.fs_slug]?.bound ?? 0),
        reachableCount: shortcutCountsFailed ? null : (shortcutCounts[p.fs_slug]?.reachable ?? 0),
      },
    ]),
  );

  const select = useCallback(
    (folder: string) => {
      setSelectedFolder(folder);
      const platform = platformOf(folder);
      loadCore(platform);
      loadSaveCount(folder);
      // A read that did not come back is retried by picking the platform again
      // — the same recovery the save count offers, and the only one there is:
      // the walk asks each platform once, so nothing else would ever ask again.
      if (readFailed[folder]) detach(readPlatform(platform));
    },
    [loadCore, loadSaveCount, platformOf, readFailed, readPlatform],
  );

  const coreFor = useCallback((folder: string): CoreAnswer => cores[folder], [cores]);
  const saveCountFor = useCallback((folder: string): SaveCountAnswer => saveCounts[folder], [saveCounts]);

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
        setPlatforms((prev) => prev.map((p) => (p.fs_slug === row.folder ? { ...p, sync_enabled: want } : p)));
      const write = syncWrites.issue([row.folder], enabled);
      const line = listLine();
      const failed = (text: string) => {
        if (write.isLatest(row.folder)) show(syncWrites.stored(row.folder) ?? !enabled);
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
        platforms.map((p) => p.fs_slug),
        enabled,
      );
      const line = listLine();
      const failed = (text: string) => {
        setPlatforms((prev) =>
          prev.map((p) =>
            write.isLatest(p.fs_slug) ? { ...p, sync_enabled: syncWrites.stored(p.fs_slug) ?? p.sync_enabled } : p,
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
  const clearCoreLine = useCallback((folder: string) => {
    setStatus((prev) => (prev?.folder === folder && prev.scope === "core" ? null : prev));
  }, []);

  /**
   * Bring *platform*'s links up to date, and read the platform again where that
   * changed anything. Downloads nothing. A refusal — Tender's folder that
   * cannot be reached, a conflict — is left to the notice that already says
   * so; this runs on its own, without a press.
   */
  const updateLinks = useCallback(
    async (platform: PlatformKey) => {
      const { slug, folder } = platform;
      try {
        const result = await updateBiosLinks(slug, folder, null);
        if (!alive.current || !result.success) return;
        noteLinkFailures(folder, result.link_failures);
        const said = placedSentence(result);
        if (said !== null) setStatus({ folder, scope: "bios", text: said });
        if (result.placed + result.removed > 0) {
          await readPlatform(platform);
          announceBiosChange(slug, folder);
        }
      } catch (e) {
        logWarn(`Failed to bring the BIOS links for ${folder} up to date: ${e}`);
      }
    },
    [noteLinkFailures, readPlatform],
  );

  // A platform's links are brought up to date the first time its pane is shown
  // with an answer on this visit — after the page stands, never as part of it.
  useEffect(() => {
    if (selectedFolder === null || !(selectedFolder in firmware) || linksUpdated.current.has(selectedFolder)) return;
    linksUpdated.current.add(selectedFolder);
    detach(updateLinks(platformOf(selectedFolder)));
  }, [firmware, platformOf, selectedFolder, updateLinks]);

  const changeCore = useCallback(
    (platform: PlatformKey, pickedLabel: string) => {
      const { slug, folder } = platform;
      const answer = cores[folder];
      // Picking the default-marked emulator clears the per-platform override
      // (empty label → follow the default); any other pins it.
      const defaultLabel = answer?.emulators.find((e) => e.is_default)?.label;
      const label = pickedLabel === defaultLabel ? "" : pickedLabel;
      setBusyFolder(folder);
      setStatus({ folder, scope: "core", text: `Switching to ${pickedLabel}…`, busy: true });
      // Captured before the continuation starts, so the catch can tell a
      // cancelled continuation from a failed switch.
      const admission = capturePruneLeaseAdmission(LEASE_OWNER);
      detach(debugLog(`setSystemCore: folder=${folder} label=${label} (selected=${pickedLabel})`));
      detach(
        (async () => {
          try {
            const result = await setSystemCore(slug, folder, label);
            detach(debugLog(`setSystemCore: result success=${result.success}`));
            if (!result.success) {
              // Report the failure rather than leaving the detail looking as
              // though the switch took effect.
              setStatus({ folder, scope: "core", text: result.message });
              return;
            }
            clearCoreLine(folder);
            reloadCore(platform);
            // A different core wants different BIOS files, at other places: its
            // links are placed from the store, nothing is downloaded, and the
            // table below the picker is stale until this platform is read again.
            await updateLinks(platform);
            await readPlatform(platform);
            announceCoreChange(folder);
          } catch (e) {
            // Leaving the page cancels the continuation, which is teardown
            // rather than a failure: the switch either committed or never ran
            // (`isPruneLeaseCancellation`), and either way there is no pane
            // left to report to. The line the pick put up goes with it.
            if (isPruneLeaseCancellation(e, admission)) {
              logWarn(`Core switch continuation was cancelled: ${e}`);
              clearCoreLine(folder);
              return;
            }
            detach(debugLog(`setSystemCore: error: ${e}`));
            setStatus({ folder, scope: "core", text: "Could not change the core" });
          } finally {
            setBusyFolder(null);
          }
        })(),
      );
    },
    [clearCoreLine, cores, readPlatform, reloadCore, updateLinks],
  );

  const removeShortcuts = useCallback(
    (row: PlatformRow) => {
      setBusyFolder(row.folder);
      // Busy until the count line appears, which the pane hands the throbber to
      // (`removeGroupStatus` in PlatformDetail.tsx; docs/architecture/qam-panel.md, "Waiting").
      setStatus({ folder: row.folder, scope: "remove", text: `Removing ${row.name} shortcuts…`, busy: true });
      const admission = capturePruneLeaseAdmission(LEASE_OWNER);
      detach(
        (async () => {
          try {
            const result = await removePlatformShortcuts(row.folder);
            // A refusal by the update, migration, sync or prune rule answers
            // { success: false, reason, message } with no app_ids/rom_ids —
            // surface that message instead of cosmetically reporting a removal.
            if (!result.success) {
              setStatus({ folder: row.folder, scope: "remove", text: result.message ?? "Failed to remove shortcuts" });
              return;
            }
            await withPruneLease(
              result.prune_lease_token,
              "Platform shortcut removal",
              async (signal) => {
                await removeShortcutsPaced(
                  result.app_ids ?? [],
                  (removed, total) => setRemovalProgress({ folder: row.folder, removed, total }),
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
              folder: row.folder,
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
            setStatus({ folder: row.folder, scope: "remove", text: "Failed to remove shortcuts" });
          } finally {
            setBusyFolder(null);
            setRemovalProgress(null);
          }
        })(),
      );
    },
    [refreshShortcutCounts],
  );

  const deleteSaves = useCallback(
    (row: PlatformRow) => {
      setBusyFolder(row.folder);
      setStatus({ folder: row.folder, scope: "remove", text: `Deleting ${row.name} saves…`, busy: true });
      detach(
        (async () => {
          try {
            const result = await deletePlatformSaves(row.folder);
            setStatus({ folder: row.folder, scope: "remove", text: result.message });
            globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync" } }));
          } catch {
            setStatus({ folder: row.folder, scope: "remove", text: "Failed to delete saves" });
          } finally {
            setBusyFolder(null);
            reloadSaveCount(row.folder);
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
    selectedFolder,
    select,
    coreFor,
    saveCountFor,
    status,
    listStatus,
    enter,
    busyFolder,
    removalProgress,
    toggleSync,
    setAllSync,
    changeCore,
    ...bios,
    removeShortcuts,
    deleteSaves,
  };
}
