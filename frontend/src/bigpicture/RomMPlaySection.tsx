/**
 * RomMPlaySection — the game page's play section, mimicking Steam's native
 * PlaySection: the play row (CustomPlayButton, the disc and version pickers,
 * the info items, and the gear buttons at the far right), under the save
 * content-dir warning and VanishedVersionBanner, which are why the root is a
 * Fragment. The info items, for example:
 *
 *   [▶ Play ▾]   LAST PLAYED    PLAYTIME    ACHIEVEMENTS    BIOS
 *                24. Jan.       14 Hours    🏆 12/40        🔴 <bios_label>
 *
 * Each item appears only when it has something to say; its condition is where
 * the item is built.
 */

import { useState, useEffect, useRef, FC, Fragment, type ReactElement } from "react";
import { showToast } from "../utils/toast";
import {
  ConfirmModal,
  DialogButton,
  Focusable,
  Menu,
  MenuItem,
  MenuSeparator,
  showContextMenu,
  showModal,
  type GamepadEvent,
} from "@decky/ui";
import { basicAppDetailsSectionStylerClasses } from "../utils/deckyUiInternals";
import { FaGamepad, FaCog, FaMicrochip, FaExclamationTriangle } from "react-icons/fa";
import { CustomPlayButton } from "./CustomPlayButton";
import { DiscSelector } from "./DiscSelector";
import { VersionPicker } from "./VersionPicker";
import { VanishedVersionBanner } from "./VanishedVersionBanner";
import { WarningCard } from "./WarningCard";
import { Muted, SELECTION_ACCENT } from "./layout/pane";
import { activeEmulatorNote } from "../utils/emulatorInstallation";
import { useTriggerHandler } from "./emulatorStepping";
import { useBumperHandler, usePlayRowRef } from "./tabStepping";
import { applyAutomaticArtwork, applyTabSlotImage, cancelArtworkApply } from "../utils/artwork";
import { settleArtworkGame } from "../utils/artworkGameChange";
import { isKnownWithoutSgdbGame, loadArtworkSlots, useArtworkSlotsChanges } from "../utils/artworkSlotsStore";
import { CHOOSE_GAME_FIRST } from "../utils/artworkTabWording";
import { useAutoArtwork } from "../utils/autoArtwork";
import { artworkApplyMessage } from "../utils/artworkApplyMessage";
import { hasAnySaveConflict } from "../utils/saveStatus";
import { scrollToTop } from "../utils/scrollHelpers";
import { getEventTarget } from "../utils/events";
import {
  testConnection,
  probeReachability,
  getSgdbResolution,
  getRomMetadata,
  isEndpointFailure,
  refreshCoverArtwork,
  setArtworkSlotAutomatic,
  refreshSgdbArtwork,
  deleteLocalSaves,
  debugLog,
} from "../api/backend";
import { isStrandedPanelFailure } from "../api/host";
import { executeManualSaveSync } from "../utils/manualSaveSync";
import { executeRomUninstall } from "../utils/romUninstall";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancellation,
  mountPruneLeaseOwner,
  releasePruneLeasesByOwner,
} from "../utils/pruneLease";
import { executeGameCoreChange } from "../utils/coreOverride";
import { frontendChoiceSentence, gameCanPickAnother, startRefusal, vanishedPickSentence } from "../utils/emulatorStart";
import { launchNote } from "../utils/gameLaunch";
import { askEntryLaunches } from "../utils/emulatorLaunches";
import { useGamePlaytime } from "../utils/playtimeReconcile";
import { buildEmulatorMenu } from "../utils/emulatorMenu";
import { formatBytes } from "../utils/formatters";
import { BIOS_MISSING_RED } from "../utils/biosColor";
import { timeoutMs } from "../utils/playSection";
import {
  getGameDetail,
  noteSaveSyncDisplay,
  refreshCoreAndBios,
  refreshSaveStatus,
  useGameDetail,
} from "../utils/gameDetailStore";

/** How long the authoritative connection check may run before the wait itself is
 *  worth a log line. It is NOT a deadline after which the server counts as
 *  unreachable — see the check below. */
const CONNECTION_CHECK_DEADLINE_MS = 5000;

/** Source of connection-check ids, in start order. Ids are handed out at module
 *  level rather than per component because the state they order is module-level
 *  too — two play sections briefly overlap while Steam swaps game pages. */
let connectionCheckSeq = 0;

/** Id of the newest check that has WRITTEN a verdict. An older check must not
 *  write over it — its answer is to a question a newer one has already answered.
 *  Ordering on the write rather than on the start is what keeps a check that is
 *  abandoned before it answers (its page closed while the server was still
 *  thinking) from silencing the verdict of a page that is still open. */
let lastSettledCheckId = 0;

/** Read this ROM's save status through the store and tell the other save-sync
 *  surfaces what came back.
 *
 *  The status travels WITH the notification. Every listener for this event — the
 *  store's fold, the info panel's — answers a payload-less one by reading the
 *  status itself, so a bare notification costs one more round-trip per listener
 *  for an answer already in hand (#1758).
 *
 *  Every caller reaches the dispatch after an await, and `isCancelled` cannot be
 *  what keeps this page's answer apart from its predecessor's. The reconnect and
 *  source-change callers are keyed on the appId alone, so a version switch never
 *  tears them down at all; and where a switch DOES re-run a caller, React
 *  commits that teardown only after the re-render, so an answer arriving before
 *  the commit still passes (#1717). What decides is the answer's own `rom_id`
 *  against the rom bound to this appId NOW — the same gate the store's fold
 *  applies, which is also why the id on the wire is the answer's rather than a
 *  captured one. */
async function readAndBroadcastSaveStatus(appId: number, isCancelled: () => boolean): Promise<void> {
  try {
    const saveStatus = await refreshSaveStatus(appId);
    if (isCancelled() || saveStatus?.rom_id !== getGameDetail(appId).romId) return;
    globalThis.dispatchEvent(
      new CustomEvent("romm_data_changed", {
        detail: {
          type: "save_sync",
          rom_id: saveStatus.rom_id,
          save_status: saveStatus,
          has_conflict: hasAnySaveConflict(saveStatus),
        },
      }),
    );
  } catch (e) {
    detach(debugLog(`RomMPlaySection: background save check error: ${e}`));
  }
}

/** What reloading the cover came to: why it failed, `null` once reloaded, and whether it is Tender's SteamGridDB pick. */
interface CoverReload {
  failure: string | null;
  steamGridDb: boolean;
}

/**
 * Reload RomM's cover for *romId* and hand it to Steam — where RomM has none, Tender's pick from SteamGridDB's covers,
 * downloaded anew and set (#2381 D23, D25) — and tell the game info panel.
 */
async function refreshRommCover(romId: number, appId: number): Promise<CoverReload> {
  // Re-download the RomM cover, rename to {app_id}p.png, and patch
  // cover_path on the registry row so the game info panel can render the
  // refreshed image.
  const coverResult = await refreshCoverArtwork(romId).catch(
    (
      e,
    ): {
      success: boolean;
      reason?: WireReason | "exception";
      message: string;
      cover_path?: string;
      prune_lease_token?: string;
    } => {
      detach(debugLog(`refreshCoverArtwork rejected: ${e}`));
      return { success: false, reason: "exception", message: "the request did not complete" };
    },
  );
  let steamGridDb = false;
  if (coverResult.success) {
    await setRommCoverInSteam(romId, appId, coverResult.prune_lease_token);
  } else {
    detach(debugLog(`refreshCoverArtwork failed: ${coverResult.reason} — ${coverResult.message}`));
    if (coverResult.reason !== "no_cover") return { failure: coverResult.message, steamGridDb };
    const failure = await setSteamGridDbCover(romId, appId);
    if (failure !== null) return { failure: failure ?? coverResult.message, steamGridDb };
    steamGridDb = true;
  }
  // Notify the game info panel so it can re-render the cover image.
  globalThis.dispatchEvent(
    new CustomEvent("romm_data_changed", {
      detail: { type: "cover_refreshed", rom_id: romId },
    }),
  );
  return { failure: null, steamGridDb };
}

/**
 * Hand Steam the RomM cover the backend just reloaded, under the lease the reload answered with, as the ARTWORK tab's
 * Automatic sets RomM's cover: the grid file alone shows only after Steam restarts. A write that fails is logged; the
 * reload stands.
 */
async function setRommCoverInSteam(romId: number, appId: number, leaseToken: string | undefined): Promise<void> {
  if (!leaseToken) return;
  const outcome = await applyTabSlotImage(
    romId,
    appId,
    "cover",
    { status: "cover", prune_lease_token: leaseToken },
    false,
  ).catch((e: unknown) => {
    detach(debugLog(`Refresh Artwork: RomM's cover for appId ${appId} was not handed to Steam: ${e}`));
    return null;
  });
  if (outcome === "refused") detach(debugLog(`Refresh Artwork: Steam refused RomM's cover for appId ${appId}`));
}

/**
 * Set the cover back to automatic for a game RomM has no cover for, which serves Tender's pick from SteamGridDB, and
 * hand it to Steam. `null` once Steam took it; why not otherwise, `undefined` where no answer came.
 */
async function setSteamGridDbCover(romId: number, appId: number): Promise<string | null | undefined> {
  const answer = await setArtworkSlotAutomatic(romId, "cover").catch((e: unknown) => {
    detach(debugLog(`Refresh Artwork: the SteamGridDB cover for rom ${romId} was not served: ${e}`));
    return null;
  });
  if (!answer) return undefined;
  if (isEndpointFailure(answer)) return answer.message;
  const outcome = await applyTabSlotImage(romId, appId, "cover", answer, false);
  return outcome === "set" ? null : "Steam did not take the cover from SteamGridDB";
}

interface RomMPlaySectionProps {
  appId: number;
}
import {
  onRommConnectionChange,
  setRommConnectionState,
  setVersionError,
  useRommConnectionState,
  useVersionError,
  type RommConnectionState,
} from "../utils/connectionState";
import { registerConnectionHeartbeat } from "../utils/connectionHeartbeat";
import { useMigrationStatus } from "../utils/migrationStore";
import { useStrandedAnswer } from "../utils/strandedPanelStore";
import { detach } from "../utils/detach";
import type { WireReason } from "../types/reasons";
import { announceSaveSync } from "../utils/saveSyncEvents";

// S3776 is raised on the declaration line, so its NOSONAR must stay there. prettier-ignore stops
// Prettier from relocating the trailing comment into the body (which would break the suppression).
// prettier-ignore
export const RomMPlaySection: FC<RomMPlaySectionProps> = ({ appId }) => { // NOSONAR(typescript:S3776) — React FC body; decomposed in #392. Holds Steam menu + achievements + save-sync row.
  // Subscribe to version error — re-renders when global state changes
  const versionError = useVersionError();
  const migration = useMigrationStatus();
  const onTrigger = useTriggerHandler();
  const onBumper = useBumperHandler();
  const playRowRef = usePlayRowRef();

  // Every field the game page's surfaces share — rom identity, install state,
  // save sync, BIOS, core selection — comes from the per-appId store, which owns
  // the reads and the romm_data_changed fold for all of them (#993).
  const detail = useGameDetail(appId);
  // The ROM the page shows now, read when an answer lands: a version switch
  // re-points it, and an answer asked for the version before is dropped.
  const shownRomId = useRef(detail.romId);
  useEffect(() => {
    shownRomId.current = detail.romId;
  }, [detail.romId]);
  const fileMissing = detail.fileMissingAt !== null;
  // Badge derives from the shared store so it appears/disappears live on any
  // reachability signal (mount check, a failed/succeeded call, or the offline
  // recovery probe), not just at this mount's check (#1345).
  const connectionState = useRommConnectionState();
  const stranded = useStrandedAnswer() !== null;
  const [actionPending, setActionPending] = useState<string | null>(null);

  // Drive the reachability heartbeat while this game page is mounted (#1345) —
  // it reports in both directions, so the badge follows the server going away as
  // well as coming back. A module-level guard keeps the ~30s re-probe
  // single-instance however many pages register it.
  useEffect(() => registerConnectionHeartbeat(), []);

  useEffect(() => {
    mountPruneLeaseOwner(`game-detail:${appId}`);
    return () => {
      detach(cancelArtworkApply(appId));
      detach(releasePruneLeasesByOwner(`game-detail:${appId}`));
    };
  }, [appId]);

  useAutoArtwork(appId, detail.romId, "Auto-artwork error");

  // Connection check — exactly one per mount, issued before this page's other
  // RomM reads rather than behind them. Keyed on appId alone: the verdict is
  // about the server, so nothing this page later learns about the ROM can change
  // it, and re-running the check when a store field settled meant the FIRST
  // (fast, idle-backend) answer was discarded as cancelled and the second one
  // queued behind the mount's own calls until it missed its deadline (#1670).
  useEffect(() => {
    const checkId = ++connectionCheckSeq;
    let cancelled = false;
    // Once testConnection() lands an authoritative verdict, the fast probe must
    // not override it (avoids a late probe clobbering a "connected" badge).
    let settled = false;

    /** An answer this check obtained is only still worth writing while nothing
     *  newer has answered: after unmount, and after a later check wrote its own
     *  verdict, writing it would put a stale verdict over a fresh one. A newer
     *  check that has merely STARTED does not supersede — it may yet be
     *  abandoned unanswered, and then this check's answer is the only one
     *  anybody is going to get. */
    const superseded = () => cancelled || checkId < lastSettledCheckId;

    /** Write a verdict and record this check as the newest one to have answered.
     *  The single place either happens, so the two cannot drift apart. */
    const settleWith = (next: RommConnectionState, reason: string) => {
      lastSettledCheckId = checkId;
      setRommConnectionState(next, reason);
    };

    // Fast offline-badge probe (ADR-0015): the slow `testConnection()` below
    // goes through the retrying heartbeat (3 attempts + backoff, up to ~90s on a
    // remote timeout), so the offline badge would otherwise lag the page open by
    // seconds. The fast `probeReachability()` (single attempt, ~3s) flips the
    // badge to "offline" the moment the server is unreachable. We only ACT on a
    // negative result here — a positive probe still defers to `testConnection()`
    // for the precise verdict (version gate, auth) so we never flash "connected"
    // when a version error is pending.
    const fastOfflineProbe = async () => {
      // Only an EXPLICIT `online === false` flips the badge offline here. A
      // throw, or any non-{online} payload, is "no signal" → defer to the
      // precise `testConnection()` verdict below rather than guess offline.
      const offline = await probeReachability()
        .then((r) => r.online === false)
        .catch((e) => {
          detach(debugLog(`RomMPlaySection: fast reachability probe failed (no signal — deferring to testConnection): ${e}`));
          return false;
        });
      // The fast probe is an EARLY hint, not the authority. Bail if testConnection
      // already settled an authoritative verdict (so a late probe can't clobber
      // "connected"), or if superseded / not offline. The shared store notifies
      // its subscribers on the change (#1345).
      if (superseded() || settled || !offline) return;
      settleWith("offline", "fast probe");
    };

    const applyVerdict = (result: Awaited<ReturnType<typeof testConnection>>) => {
      if (superseded()) return;
      settled = true; // authoritative verdict in hand — a late fast probe must not override it
      if (result.reason === "version_error") {
        setVersionError(result.message);
        settleWith("offline", "version error");
        return;
      }
      settleWith(result.success ? "connected" : "offline", "authoritative verdict");
    };

    /** Obtain the verdict and apply it. Declared async so a SYNCHRONOUS throw
     *  out of the endpoint call arrives here as a rejection instead of
     *  escaping into the fire-and-forget `check()` unlogged — an endpoint that
     *  cannot be called at all is as much a reachability signal as a rejected
     *  promise. Except a call refused because the panel is stranded: a backend
     *  runs and refused this panel, which says nothing about RomM, so it writes
     *  no verdict and the badge says Tender was restarted instead. Only
     *  the CALL is guarded: a throw out of applyVerdict is a subscriber's
     *  defect, not a verdict, and must not be reported as one. */
    const runVerdict = async () => {
      let result: Awaited<ReturnType<typeof testConnection>>;
      try {
        result = await testConnection();
      } catch (e) {
        if (superseded()) return;
        settled = true;
        detach(debugLog(`RomMPlaySection(${appId}): connection check failed: ${e}`));
        if (isStrandedPanelFailure(e)) return;
        settleWith("offline", "check failed");
        return;
      }
      applyVerdict(result);
    };

    const check = async () => {
      // Reset stale connection state immediately so downstream consumers
      // (e.g. CustomPlayButton) don't stay stuck on a previous "offline"
      setRommConnectionState("checking", "connection check started");

      // Snappy offline badge — runs concurrently with the precise check below.
      detach(fastOfflineProbe());

      // The verdict is consumed by runVerdict, not by the race below, so an
      // answer that arrives after the deadline is still applied.
      const verdict = runVerdict();

      // Missing the deadline means UNKNOWN, not unreachable: the badge stays at
      // "checking" and the outstanding call above settles it whenever it lands.
      // Reporting the deadline as a verdict is what put "RomM offline" on a page
      // whose server answered seconds later (#1670).
      await Promise.race([verdict, timeoutMs(CONNECTION_CHECK_DEADLINE_MS)]).catch(() => {
        if (superseded() || settled) return;
        detach(
          debugLog(
            `RomMPlaySection(${appId}): connection check unanswered after ${CONNECTION_CHECK_DEADLINE_MS}ms — holding "checking" until the verdict lands`,
          ),
        );
      });
    };
    detach(check());
    return () => {
      cancelled = true;
    };
  }, [appId]);

  // Background save-status check — detects new conflicts for a save-sync ROM.
  // The read itself and the state it produces belong to the store; what stays
  // here is the conflict notification for the other save-sync surfaces. Keyed on
  // the ROM identity and the save-sync flag, which is all it consumes: it is not
  // GATED on the connection verdict, because the read answers with the local half
  // even while the server is unreachable — that degraded answer is what the saves
  // surfaces render at all — and waiting behind a verdict only meant the read
  // landed after the store's own had settled, costing a second round-trip for the
  // same answer. The reconnect lane below adds a second OCCASION to run this
  // read; it is not a gate on this one. Playtime reconcile-on-view is a separate,
  // equally connectivity-independent effect (#1345).
  useEffect(() => {
    const romId = detail.romId;
    if (!romId || !detail.saveSyncEnabled) return;
    let cancelled = false;
    detach(readAndBroadcastSaveStatus(appId, () => cancelled));
    return () => {
      cancelled = true;
    };
  }, [appId, detail.romId, detail.saveSyncEnabled]);

  // Reconnect repair (#1758) — the server coming back while this page stays
  // open. The page's other server-fed lanes read the connection verdict and so
  // repair themselves (the achievements tab, the panel's slot load); the save
  // status has no such reader, and nothing else dispatches a save_sync event on
  // reconnect, so its degraded answer — server half nulled, `server_query_failed`
  // set — would stand until the page is closed and reopened.
  //
  // Subscribing to the transitions rather than depending on the rendered verdict
  // is what keeps a page open from reading twice: the store starts at "checking"
  // and this page's own mount check flips it to "connected", so a verdict in the
  // dep array above would re-run the read on EVERY page open. Here only a change
  // runs anything, only a change INTO a reachable server, and only when the
  // answer on hand is missing its server half — a read whose server half already
  // landed has nothing to repair, and a mount whose read is still in flight has
  // no answer to judge yet.
  useEffect(() => {
    let cancelled = false;
    const unsubscribe = onRommConnectionChange((next) => {
      if (next !== "connected") return;
      // Read the identity and the last answer at TRANSITION time, not at
      // subscribe time: this callback outlives many renders, and the answer it
      // has to judge is the one the store holds now.
      const { romId, saveSyncEnabled, saveStatus } = getGameDetail(appId);
      if (!romId || !saveSyncEnabled || !saveStatus?.server_query_failed) return;
      detach(readAndBroadcastSaveStatus(appId, () => cancelled));
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [appId]);

  // A switch or a move of an emulator source can change the save's shape, and
  // with it the conflict verdict. The store reads the status again for its own
  // fold, but the play button learns the verdict only from this section's
  // notification, so the read the change issued is announced here as well.
  useEffect(() => {
    let cancelled = false;
    const onDataChanged = (e: WindowEventMap["romm_data_changed"]) => {
      if (e.detail.type !== "emulator_sources") return;
      const { romId, saveSyncEnabled } = getGameDetail(appId);
      if (!romId || !saveSyncEnabled) return;
      detach(readAndBroadcastSaveStatus(appId, () => cancelled));
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => {
      cancelled = true;
      globalThis.removeEventListener("romm_data_changed", onDataChanged);
    };
  }, [appId]);

  const playtimeInfo = useGamePlaytime(appId, detail.romId, "RomMPlaySection");
  // Refresh Artwork is offered only once the ROM has a SteamGridDB game (#2381 D3); the visit's read also settles a
  // game RomM's id replaced (D17).
  useArtworkSlotsChanges(appId);
  useEffect(() => {
    if (detail.romId) detach(settleArtworkGame(appId, detail.romId));
  }, [appId, detail.romId]);
  const withoutSgdbGame = isKnownWithoutSgdbGame(appId, detail.romId);

  // Helper: create an info item with header and value (Steam's two-line pattern)
  const infoItem = (key: string, header: string, value: string, extraClass?: string) => (
    <div key={key} className={`romm-info-item ${extraClass || ""}`.trim()}>
      <div className="romm-info-header">{header}</div>
      <div className="romm-info-value">{value}</div>
    </div>
  );

  // --- Gear button action handlers ---

  const handleRefreshArtwork = async () => {
    if (actionPending) return;
    if (!detail.romId) {
      showToast("ROM info not loaded yet");
      return;
    }
    const romId = detail.romId;
    setActionPending("artwork");
    const admission = capturePruneLeaseAdmission(`game-detail:${appId}`);
    let coverFailure: string | null = null;
    let coverFromSteamGridDb = false;
    const say = (sgdbResult: string) => {
      if (!coverFailure || coverFailure === sgdbResult) {
        showToast(sgdbResult);
        return;
      }
      showToast(`${sgdbResult.replace(/\.$/, "")}. The RomM cover could not be updated: ${coverFailure}`);
    };
    try {
      // What each slot is decides what Refresh Artwork may replace: the cover
      // only on automatic, the SteamGridDB slots only on automatic, never a
      // slot the user chose or one set in Steam (#2381 D6).
      const slots = await loadArtworkSlots(appId, romId);
      if (!slots) {
        showToast("Failed to refresh artwork");
        return;
      }
      if (slots.game.sgdb_id === null) {
        showToast(CHOOSE_GAME_FIRST);
        return;
      }
      if (slots.slots.some((row) => row.slot === "cover" && row.state === "automatic")) {
        const reload = await refreshRommCover(romId, appId);
        coverFailure = reload.failure;
        coverFromSteamGridDb = reload.steamGridDb;
      }

      const resolution = await getSgdbResolution(romId).catch((e): null => {
        detach(debugLog(`getSgdbResolution rejected: ${e}`));
        return null;
      });
      if (!resolution) {
        say("Failed to refresh artwork");
        return;
      }
      if (isEndpointFailure(resolution)) {
        say(resolution.message);
        return;
      }

      switch (resolution.decision) {
        case "no_api_key":
          say("Set a SteamGridDB API key in Settings first");
          break;
        case "resolved": {
          const refreshed = await refreshSgdbArtwork(romId, resolution.sgdb_id);
          if (isEndpointFailure(refreshed)) {
            say(refreshed.message);
            break;
          }
          say(artworkApplyMessage(await applyAutomaticArtwork(romId, appId), coverFromSteamGridDb));
          break;
        }
        case "needs_pick":
          // The game is chosen in the ARTWORK tab alone (#2381 D3).
          say(CHOOSE_GAME_FIRST);
          break;
      }
    } catch (e) {
      // Leaving the game page cancels the artwork continuation mid-apply — that is
      // teardown, not a refresh failure, so it must not toast at the next surface.
      if (isPruneLeaseCancellation(e, admission)) {
        detach(debugLog(`handleRefreshArtwork: continuation was cancelled: ${e}`));
        return;
      }
      say("Failed to refresh artwork");
    } finally {
      setActionPending(null);
      detach(loadArtworkSlots(appId, romId));
    }
  };

  const handleRefreshMetadata = async () => {
    if (actionPending || !detail.romId) return;
    setActionPending("metadata");
    try {
      await getRomMetadata(detail.romId);
      showToast("Metadata refreshed");
      globalThis.dispatchEvent(
        new CustomEvent("romm_data_changed", { detail: { type: "metadata", rom_id: detail.romId } }),
      );
    } catch {
      showToast("Failed to refresh metadata");
    } finally {
      setActionPending(null);
    }
  };

  const handleSyncSaves = async () => {
    if (actionPending || !detail.romId) return;
    setActionPending("savesync");
    try {
      await executeManualSaveSync(appId, detail.romId);
    } finally {
      setActionPending(null);
    }
  };

  const handleUninstall = async () => {
    if (actionPending || !detail.romId) return;
    setActionPending("uninstall");
    try {
      await executeRomUninstall({
        romId: detail.romId,
        appId,
        romName: detail.romName,
        leaseOwner: `game-detail:${appId}`,
        context: "Game detail uninstall",
        tag: "handleUninstall",
      });
    } finally {
      setActionPending(null);
    }
  };

  const handleDeleteSaves = () => {
    if (actionPending || !detail.romId) return;
    const romId = detail.romId;
    showModal(
      <ConfirmModal
        strTitle="Delete Local Saves"
        strDescription="This will delete local save files for this game. Make sure saves are synced to RomM first — the next sync will re-download them from the server."
        strOKButtonText="Delete"
        strCancelButtonText="Cancel"
        onOK={() => {
          detach(
            (async () => {
              setActionPending("deletesaves");
              try {
                const result = await deleteLocalSaves(romId);
                if (result.success) {
                  showToast(result.message);
                  // Directly update the shown status — no local saves remain
                  noteSaveSyncDisplay(appId, romId, { status: "none", label: "No saves", last_sync_check_at: null });
                  announceSaveSync(romId);
                } else {
                  showToast(result.message || "Failed to delete saves");
                }
              } catch {
                showToast("Failed to delete saves");
              } finally {
                setActionPending(null);
              }
            })(),
          );
        }}
      />,
    );
  };

  /** Refresh the core badge + BIOS state from their dedicated paths and notify
   *  sibling components after a successful override pin/clear. */
  const refreshCoreDisplay = async (platformSlug: string) => {
    await refreshCoreAndBios(appId);
    globalThis.dispatchEvent(
      new CustomEvent("romm_data_changed", { detail: { type: "core_changed", platform_slug: platformSlug } }),
    );
  };

  const handleChangeGameCore = async (coreLabel: string) => {
    const romId = detail.romId;
    if (!romId || !detail.platformSlug) return;
    const platformSlug = detail.platformSlug;
    await executeGameCoreChange({
      romId,
      coreLabel,
      coreName: coreLabel,
      leaseOwner: `game-detail:${appId}`,
      onSuccess: () => refreshCoreDisplay(platformSlug),
      logTag: "handleChangeGameCore",
    });
  };

  const handleResetGameCore = async () => {
    const romId = detail.romId;
    if (!romId || !detail.platformSlug) return;
    const platformSlug = detail.platformSlug;
    await executeGameCoreChange({
      romId,
      coreLabel: null,
      leaseOwner: `game-detail:${appId}`,
      onSuccess: () => refreshCoreDisplay(platformSlug),
      logTag: "handleResetGameCore",
    });
  };

  const showCoreMenu = (e: Event) => {
    // Both pickers share the builder in utils/emulatorMenu. The game-detail menu
    // carries the "Use System Override" reset item (the only clear path, #211);
    // every entry the menu offers PINS the per-game override. For an
    // installed game it also asks, as it opens, whether each entry can start it.
    const romId = detail.romId;
    const launches =
      detail.installed && romId ? askEntryLaunches(romId, () => shownRomId.current === romId) : undefined;
    showContextMenu(
      buildEmulatorMenu({
        emulators: detail.emulators,
        emulatorDataAvailable: detail.emulatorDataAvailable,
        emulatorDataReason: detail.emulatorDataReason,
        emulatorSource: detail.emulatorSource,
        emulatorPlatformSystem: detail.emulatorPlatformSystem,
        activeLabel: detail.activeCoreLabel,
        activeColor: SELECTION_ACCENT,
        platformCoreLabel: detail.platformCoreLabel,
        followSystem: {
          hasGameOverride: detail.hasGameOverride,
          onFollowSystem: () => {
            detach(handleResetGameCore());
          },
        },
        onPick: (label) => {
          detach(handleChangeGameCore(label));
        },
        launches,
      }),
      getEventTarget(e),
    );
  };


  const showRomMMenu = (e: Event) => {
    showContextMenu(
      <Menu label="RomM Actions">
        <MenuItem
          key="refresh-artwork"
          disabled={withoutSgdbGame}
          onClick={() => {
            detach(handleRefreshArtwork());
          }}
        >
          {withoutSgdbGame ? (
            <div>
              <div>Refresh Artwork</div>
              <div style={{ fontSize: "12px", opacity: 0.7 }}>{CHOOSE_GAME_FIRST}</div>
            </div>
          ) : (
            "Refresh Artwork"
          )}
        </MenuItem>
        <MenuItem
          key="refresh-metadata"
          onClick={() => {
            detach(handleRefreshMetadata());
          }}
        >
          Refresh Metadata
        </MenuItem>
        <MenuItem
          key="sync-saves"
          onClick={() => {
            detach(handleSyncSaves());
          }}
        >
          Sync Save Files
        </MenuItem>
        <MenuSeparator key="sep" />
        <MenuItem key="delete-saves" tone="destructive" onClick={handleDeleteSaves}>
          Delete Local Saves
        </MenuItem>
        {/* A download whose file is missing is removed by the play button's
            "Forget this download" instead: there is nothing to delete. */}
        {fileMissing ? null : (
          <MenuItem
            key="uninstall"
            tone="destructive"
            onClick={() => {
              detach(handleUninstall());
            }}
          >
            Uninstall
          </MenuItem>
        )}
      </Menu>,
      getEventTarget(e),
    );
  };

  const showSteamMenu = (e: Event) => {
    showContextMenu(
      <Menu label="Steam">
        <MenuItem
          key="properties"
          onClick={() => {
            SteamClient.Apps.OpenAppSettingsDialog(appId, "general");
          }}
        >
          Properties
        </MenuItem>
      </Menu>,
      getEventTarget(e),
    );
  };

  // Version mismatch — render nothing (RomMGameInfoPanel shows the VersionErrorCard instead, or the stranded card on a
  // stranded panel)
  if (versionError) {
    return null;
  }

  // Pending RetroDECK migration — render nothing (RomMGameInfoPanel shows the MigrationBlockedCard instead, or the
  // stranded card on a stranded panel)
  if (migration.pending) {
    return null;
  }

  // Build info items array
  const infoItems: ReactElement[] = [];

  // Connection indicator (first — most prominent). A stranded panel cannot
  // reach the backend, so it holds no word on RomM either way.
  let connectionLabel: string | null = null;
  if (stranded) connectionLabel = "Tender restarted";
  else if (connectionState === "offline") connectionLabel = "RomM offline";
  if (connectionLabel) {
    infoItems.push(
      <div key="connection-indicator" className="romm-info-item">
        <div className="romm-info-header">
          <FaExclamationTriangle size={12} color="#ff8800" />
        </div>
        <div className="romm-info-value" style={{ color: "#ff8800" }}>
          {connectionLabel}
        </div>
      </div>,
    );
  }

  // Space Required (#1395) — the download footprint, shown only for an
  // uninstalled ROM whose size is above 0: RomM states 0 for a ROM it holds no
  // file for, and a size of 0 requires nothing. Placed FIRST to match Steam's
  // native game detail: an uninstalled title leads with "Space Required" ahead
  // of Last Played / Playtime, and the cell disappears once the ROM is on disk.
  // Being first also keeps it clear of the romm-info-items nowrap +
  // overflow:hidden clip, which drops cells from the right edge.
  if (!detail.installed && detail.fsSizeBytes != null && detail.fsSizeBytes > 0) {
    infoItems.push(infoItem("space-required", "SPACE REQUIRED", formatBytes(detail.fsSizeBytes)));
  }

  // Last Played
  if (playtimeInfo.lastPlayed) {
    infoItems.push(infoItem("last-played", "LAST PLAYED", playtimeInfo.lastPlayed));
  }

  // Playtime
  if (playtimeInfo.playtime) {
    infoItems.push(infoItem("playtime", "PLAYTIME", playtimeInfo.playtime));
  }

  // Achievements badge (only when RA data available). Not on a stranded panel:
  // the tab it opens has given way to the stranded card.
  if (detail.raId && !stranded) {
    const hasEarned = detail.achievementEarned > 0;
    const countLabel =
      detail.achievementTotal > 0 ? `${detail.achievementEarned}/${detail.achievementTotal}` : `${detail.achievementEarned}`;

    // Generate sparkle dots at random fixed positions (only when earned > 0)
    // Positions are deterministic per-index so they don't shift on re-render
    const sparklePositions = [
      { top: "5%", left: "80%" },
      { top: "70%", left: "10%" },
      { top: "15%", left: "35%" },
      { top: "85%", left: "70%" },
      { top: "45%", left: "90%" },
    ];
    const sparkleDurs = [2.4, 3.5, 2.8, 3.8, 3.1];
    const sparkleDelays = [0, 0.9, 0.3, 1.6, 1.1];
    const sparkleDots = hasEarned
      ? sparklePositions.map((pos, i) => {
          // Hoisted and annotated, not inlined into `style={{...}}`: an inline
          // literal is excess-property checked against React's own
          // CSSProperties, which rejects the `--*` keys outright.
          const dotStyle: CSSPropertiesWithVars = {
            "--romm-sparkle-top": pos.top,
            "--romm-sparkle-left": pos.left,
            "--romm-sparkle-delay": `${sparkleDelays[i]}s`,
            "--romm-sparkle-dur": `${sparkleDurs[i]}s`,
          };
          return <span key={`sparkle-${pos.top}-${pos.left}`} className="romm-sparkle-dot" style={dotStyle} />;
        })
      : [];

    infoItems.push(
      // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- pointer-only shortcut into the ACHIEVEMENTS tab, which Steam's tab row already reaches from the focus ring; a role/tabIndex here would add a gamepad focus stop to the play row.
      <div
        key="achievements"
        className="romm-info-item romm-cheevo-badge"
        onClick={() => {
          globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "achievements" } }));
        }}
      >
        <div className="romm-info-header">ACHIEVEMENTS</div>
        <div className="romm-cheevo-badge-sparkle">
          {/* Trophy icon with sparkle container */}
          <span style={{ position: "relative", display: "inline-block" }}>
            <span className={hasEarned ? "romm-cheevo-trophy" : "romm-cheevo-trophy-none"}>{"\uD83C\uDFC6"}</span>
            {hasEarned ? <span className="romm-sparkle-container">{sparkleDots}</span> : null}
          </span>
          <span className="romm-cheevo-count">{countLabel}</span>
        </div>
      </div>,
    );
  }

  // BIOS warning. What decides it is the backend's level `missing` — the
  // resolver's verdict, not met (`extractBiosInfo`): a file the launching
  // emulator requires is not met, or the console cannot start without one of
  // the images that emulator declares and none of those images is met.
  // Everything else the BIOS answer says — a platform whose emulators could
  // not be asked, optional files nobody launching this game needs — is
  // non-actionable here and lives in the BIOS tab.
  //
  // One appearance, always red. This badge is not rendering the four-valued
  // verdict — the BIOS tab is, off `biosColorForLevel` — it is a warning that
  // shows only for a state that is never anything but bad. Amber for "1 of 3
  // required present" would suggest a degree where there is none: a file the
  // core asks for is absent either way, and how many of its siblings are there
  // does not soften that. The console's own demand is the same kind of state —
  // the game does not start — which is why it earns the same red badge rather
  // than a softer one of its own.
  //
  // Not on a stranded panel: the BIOS tab has given way to the stranded card.
  if (detail.biosRequiredMissing && !stranded) {
    infoItems.push(
      // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- pointer-only shortcut into the BIOS tab, which Steam's tab row already reaches from the focus ring; a role/tabIndex here would add a gamepad focus stop to the play row.
      <div
        key="bios"
        className="romm-info-item"
        onClick={() => {
          globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "bios" } }));
        }}
        style={{ cursor: "pointer" }}
      >
        <div className="romm-info-header">BIOS</div>
        <div className="romm-info-value" style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span className="romm-status-dot" style={{ backgroundColor: BIOS_MISSING_RED }} />
          {detail.biosLabel}
        </div>
      </div>,
    );
  }

  // Why the game cannot start — its emulator cannot, or its file does not
  // start with that emulator (#2195 D2): Play is disabled, and the same
  // sentence stands behind the warning sign on a line of its own under the
  // row's controls, inside the play row. Not on a stranded panel, and not for a
  // download whose file is missing, where the row says that instead. A verdict
  // that lets the game start but has something to say stands on that line
  // with Play left on.
  // Where the page's check itself went wrong, nothing is said below Play: the
  // notification said what went wrong, and a press asks again (#2195 D33).
  const canPick = gameCanPickAnother(detail.emulators, detail.activeCoreLabel);
  const gameLaunch = detail.gameLaunch ?? null;
  const startCheckFailed = !stranded && !fileMissing && detail.startCheck?.state === "failed";
  const emulatorRefusal =
    stranded || fileMissing || startCheckFailed || detail.emulatorStart === null
      ? null
      : startRefusal(detail.emulatorStart, gameLaunch, canPick);
  const startNote =
    stranded || fileMissing || startCheckFailed || emulatorRefusal !== null ? null : launchNote(gameLaunch, detail.emulatorSource);

  const warningLine = (testId: string, text: string) => (
    <div
      data-testid={testId}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "6px",
        fontSize: "12px",
        color: "#ffaa00",
      }}
    >
      <FaExclamationTriangle size={12} style={{ flexShrink: 0 }} />
      <span>{text}</span>
    </div>
  );

  const playSectionRow = (
    <Focusable
      key="play-row"
      ref={playRowRef}
      // The row also carries Steam's PlaySection class; `data-romm` is what keeps
      // hideNativePlaySection's rule from hiding it with Steam's own section.
      data-romm="true"
      className={`romm-play-section-row ${basicAppDetailsSectionStylerClasses?.PlaySection || ""}`.trim()}
      flow-children="right"
      onButtonDown={(evt: GamepadEvent) => {
        onTrigger(evt);
        onBumper(evt);
      }}
      // Back to the top of the game page from the tabs below, by the d-pad or by B (`GamePageTabs`).
      onFocus={scrollToTop}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        padding: "16px 2.8vw",
        background: "rgba(14, 20, 27, 0.33)",
        boxSizing: "border-box",
      }}
    >
      {/* The controls on one line; why the game cannot start goes on its own
          line under them, inside the same band. */}
      <div data-testid="play-row-controls" style={{ display: "flex", alignItems: "center", gap: "20px" }}>
        {/* Play button on the left */}
        <CustomPlayButton
          appId={appId}
          emulatorRefusal={emulatorRefusal}
          startChecking={!stranded && !fileMissing && detail.startCheck?.state === "checking"}
          startCheckFailed={startCheckFailed}
        />
        {/* Disc picker for multi-disc ROMs — renders nothing otherwise (#865), and
            nothing for a download whose discs are missing */}
        {fileMissing || stranded ? null : <DiscSelector appId={appId} />}
        {/* Version picker for multi-version sibling groups — renders nothing otherwise (#1297) */}
        {stranded ? null : <VersionPicker appId={appId} />}
        {/* Info items row */}
        <div
          className="romm-info-items"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "20px",
            flexWrap: "nowrap",
            overflow: "hidden",
          }}
        >
          {infoItems}
        </div>
        {/* Gear icon buttons pushed to the far right; a stranded panel offers none */}
        {stranded ? null : (
          <div
            style={{
              marginLeft: "auto",
              display: "flex",
              alignItems: "center",
              gap: "8px",
              flexShrink: 0,
            }}
          >
            {/* RomM actions button */}
            <DialogButton className="romm-gear-btn" onClick={showRomMMenu} onFocus={scrollToTop} title="RomM Actions">
              <FaGamepad size={18} color="#553e98" />
            </DialogButton>
            {/* Core selection button: where there are emulators to choose between,
                and where the emulator list could not be established, whose menu
                then says why. Never for a download whose file is missing. */}
            {(detail.emulators.length > 1 || !detail.emulatorDataAvailable) && !fileMissing ? (
              <DialogButton
                key="core-btn"
                className="romm-gear-btn"
                onClick={showCoreMenu}
                onFocus={scrollToTop}
                title="Emulator Core"
              >
                <FaMicrochip size={18} color={detail.activeCoreIsDefault ? "#8f98a0" : "#d4a72c"} />
              </DialogButton>
            ) : null}
            {/* Steam properties button */}
            <DialogButton className="romm-gear-btn" onClick={showSteamMenu} onFocus={scrollToTop} title="Steam Properties">
              <FaCog size={18} color="#8f98a0" />
            </DialogButton>
          </div>
        )}
      </div>
      {emulatorRefusal === null ? null : warningLine("emulator-refusal", emulatorRefusal)}
      {startNote === null ? null : warningLine("start-note", startNote)}
    </Focusable>
  );

  // Content-dir warning (#239) — prominent banner above the play row when the
  // emulator writes this game's saves beside the game file. The play row still
  // renders below it: the game remains fully playable, only save sync is
  // unavailable. The store holds the flag whether or not save sync is on (it is
  // a fact about the emulator's answer, not about our setting); the banner is
  // about getting save sync to work, so it is only worth showing to someone who
  // has save sync on. It names RetroArch's setting only as the usual cause: the
  // answer can reach the content's directory by another route, so the banner may
  // not assume the setting is on.
  //
  // The banner is a keyed sibling under a Fragment, never a branch returning a
  // different root: the flag lands a moment after the row first paints, and a
  // root whose element type changes makes React unmount the whole row — the play
  // button drops back to "loading" and re-runs its init (#1682). A Fragment adds
  // no DOM node, so the row stays a direct child of the injected panel either way.
  const emulatorNote = activeEmulatorNote(detail.emulators, detail.activeCoreLabel);
  // The pick that no longer exists and the user's own choice in ES-DE — each
  // said once, under the row.
  const emulatorLines =
    stranded || fileMissing
      ? []
      : [
          detail.vanishedPick === null ? null : vanishedPickSentence(detail.vanishedPick),
          frontendChoiceSentence(detail.emulators, detail.activeCoreLabel),
        ].filter((line): line is string => line !== null);
  return (
    <Fragment>
      {detail.savefilesInContentDir === true && detail.saveSyncEnabled ? (
        <WarningCard
          key="savefiles-content-dir-warning"
          title="Save sync off"
          message="This game's saves are written beside the game file, so they can't be synced. The usual cause is RetroArch's 'Write Saves to Content Directory' (RetroArch › Settings › Saving); if it is on, turning it off lets save sync work again."
        />
      ) : null}
      <VanishedVersionBanner key="vanished-version-banner" appId={appId} installed={detail.installed} />
      {playSectionRow}
      {emulatorNote === null ? null : <Muted key="emulator-installation-note">{emulatorNote}</Muted>}
      {emulatorLines.map((line) => (
        <Muted key={line}>{line}</Muted>
      ))}
    </Fragment>
  );
};
