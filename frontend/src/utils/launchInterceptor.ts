/**
 * Global launch watcher (ADR-0015 — the "full funnel").
 *
 * Every start of a RomM-owned shortcut that did NOT come from our Play button
 * is intercepted here. Because no Steam hook can pause a launch, run async
 * work, and then proceed, the watcher uses the cancel-then-relaunch mechanism:
 * it `CancelGameAction`s the launch IMMEDIATELY (synchronously, which wins the
 * race against the un-pausable launch), runs the shared {@link runLaunchGate}
 * funnel — unless the panel is stranded, which refuses the start — and on
 * approval relaunches via `RunGame`.
 *
 * The skip-set (`markLaunchSkipped` / `consumeLaunchSkip`, owned by
 * `launchGate.ts`) lets one start through that a caller has just handled — the
 * watcher's own relaunch and the Play button's start — so neither is gated
 * twice.
 *
 * Registered once, from the panel's factory; nothing unregisters it.
 */

import { strandedAnswer } from "../api/host";
import { showToast } from "./toast";
import { isRomMAppId } from "./rommAppIds";
import { appIdFromGameId } from "./gameId";
import {
  refreshMigrationState,
  getInstalledRom,
  getCachedGameDetail,
  getVersionList,
  logInfo,
  logError,
} from "../api/backend";
import { setMigrationStatus } from "./migrationStore";
import { getAppIdRomIdMapSnapshot, readGameRunning, refreshAppIdMap } from "./sessionManager";
import { markLaunchSkipped, consumeLaunchSkip, SERVER_CALL_LIMIT_MS } from "./launchGate";
import { ensureTrackingConfiguredSilently, makeLaunchGateOps } from "./launchGateOps";
import { runGateLoop, type LaunchPrompts } from "./launchVerdict";
import { reconfirmLaunchOptions } from "./launchOptionsReconcile";
import { detach } from "./detach";
import { tellStrandedPress } from "./strandedPanelStore";
import { strandedPanelSentence } from "./strandedPanelWording";
import { TimeoutError, withTimeout } from "./withTimeout";
import { boundAvailabilityOf, type BoundAvailability } from "./vanishedBinding";
import { emulatorRefusalForRom } from "./emulatorStart";

/**
 * A start the watcher cancelled: the appId it resolved, and the game ID Steam
 * reported it by, which a relaunch hands back to `RunGame` (see
 * {@link relaunchGameId}).
 */
interface CancelledStart {
  appId: number;
  gameId: string;
}

/** Migration block copy — surfaced as a toast (no relaunch). */
const MIGRATION_TOAST_BODY = "Pending RetroDECK migration. Open the Tender menu (QAM) to migrate or dismiss.";

const WATCHER_TAG = "Watcher";

/** Relaunch a previously-cancelled launch. Marks the appId as skipped FIRST so
 *  this RunGame doesn't re-enter the watcher and re-gate. Used directly only for
 *  the paths with nothing to re-confirm (unknown appId, no backend answer, error
 *  fallback); the gated path goes through {@link relaunch}. */
function bareRelaunch(start: CancelledStart): void {
  markLaunchSkipped(start.appId);
  SteamClient.Apps.RunGame(relaunchGameId(start), "", -1, 100);
}

/**
 * The id a relaunch starts the game by. A start reported by its 64-bit game ID
 * is started by that ID. A start reported by a value that fits in 32 bits — the
 * appId itself, a form Steam has not been seen using for a shortcut — is
 * started by the shortcut's game ID from Steam's app store where it has an
 * overview, and by the reported value otherwise.
 */
function relaunchGameId(start: CancelledStart): string {
  if (BigInt(start.gameId) > 0xffffffffn) return start.gameId;
  return appStore.GetAppOverviewByAppID(start.appId)?.GetGameID?.() ?? start.gameId;
}

/** Relaunch a previously-cancelled, now-approved launch. Heals any mid-session
 *  `launch_options` drift on the shortcut first (shared bounded-race re-confirm;
 *  ordinary I/O failure is best-effort, timeout stops), then marks the appId as
 *  skipped immediately before this RunGame so it doesn't re-enter the watcher
 *  and re-gate. The re-confirm runs in the already-detached post-cancel portion,
 *  so it only adds a bounded wait to the cancel→relaunch window: the command
 *  fetch, the confirm's report wait, re-read and reading of Steam's shortcut
 *  file, and the lease release each run under a timeout of their own. */
async function relaunch(start: CancelledStart, romId: number): Promise<void> {
  const reconfirm = await reconfirmLaunchOptions(romId, start.appId, WATCHER_TAG);
  if (reconfirm.status === "timeout") {
    // The watcher has no game-page UI to fall back to, so the refusal would
    // otherwise be a silently dead Play press — say it out loud instead
    // (CustomPlayButton's twin path returns its trigger to "Play").
    showToast("Launch cancelled — try again");
    return;
  }
  bareRelaunch(start);
}

const nothingToShow = (): void => undefined;

/**
 * Run the shared launch gate for a cancelled start and act on its verdict. The
 * start is already stopped, so every branch either relaunches or leaves it
 * stopped; with no trigger of its own to reset, of the hooks for a start that
 * ends without a launch only the migration block's shows anything. The gate
 * itself fails open to `allow` so a gate bug never traps the user's
 * already-cancelled launch; an unexpected throw while acting on the verdict or
 * relaunching is caught by the listener's own catch, which falls back to a bare relaunch.
 */
async function runWatcherGate(start: CancelledStart, romId: number, prompts: LaunchPrompts): Promise<void> {
  const ops = makeLaunchGateOps(romId, {
    tag: WATCHER_TAG,
    toastSyncResult: false,
    // A start the watcher catches may come from no game page at all: the library
    // grid, a `steam://` link.
    ensureTrackingConfigured: async () => {
      await ensureTrackingConfiguredSilently(romId, WATCHER_TAG);
      return "proceed";
    },
    confirmCoreChange: prompts.confirmCoreChange,
    confirmUncheckedStartingSource: prompts.confirmUncheckedSource,
    askSyncWait: prompts.askSyncWait,
  });
  await runGateLoop(start.appId, romId, ops, {
    prompts,
    launch: async (options) => {
      if (options?.skipReconfirm) {
        bareRelaunch(start);
        return;
      }
      await relaunch(start, romId);
    },
    onDeclined: nothingToShow,
    onConflictCancelled: nothingToShow,
    onRetry: nothingToShow,
    onMigrationBlocked: () => showToast(MIGRATION_TOAST_BODY),
  });
}

/**
 * Is this ROM installed on disk? The funnel assumes an installed ROM; an
 * uninstalled one is a hard block (the ROM is gone — no "Start Anyway").
 * `get_installed_rom` is the live truth: a returned record means installed, a
 * `null` means not installed. Only when the call THROWS (transport hiccup) do we
 * fall back to the cached detail's `installed` flag, so a transient error never
 * trap-blocks a genuinely-installed ROM.
 */
async function isRomInstalled(appId: number, romId: number): Promise<boolean> {
  try {
    return (await getInstalledRom(romId)) != null;
  } catch (e) {
    logError(`Watcher installed check threw (falling back to cached install flag): ${e}`);
    const cached = await getCachedGameDetail(appId).catch((cacheErr) => {
      logError(`Watcher cached-detail fallback failed (treating as not installed): ${cacheErr}`);
      return null;
    });
    return cached?.installed === true;
  }
}

/**
 * How long a cancelled start waits for the watcher's first backend answer
 * before it goes ahead without the pre-launch sync. Why the wait is bounded:
 * `docs/architecture/save-file-sync-architecture.md`, "Pre-launch sync".
 */
export const FIRST_CONTACT_DEADLINE_MS = 5000;

const NOT_RESPONDING_TOAST_BODY = "Tender isn't responding — started without syncing saves.";

/**
 * Why a start of a version that is not installed is refused. A press from the
 * library grid may come before the game page ever asked RomM about the bound
 * version, so this asks the same question the version picker does; an answer
 * that establishes nothing keeps the download sentence.
 */
async function notInstalledSentence(appId: number): Promise<string> {
  let availability: BoundAvailability = "live";
  try {
    availability = boundAvailabilityOf(await withTimeout(getVersionList(appId), SERVER_CALL_LIMIT_MS));
  } catch (e) {
    logError(`Launch interceptor: version list for appId=${appId} unavailable: ${e}`);
  }
  if (availability === "game_gone") return "This game is no longer available on RomM.";
  if (availability === "version_gone") {
    return "This version is no longer available on RomM. Choose another version on its game page.";
  }
  return "ROM not downloaded. Download it from its game page first.";
}

/**
 * The ROM a cancelled start belongs to and whether it is installed, or `null`
 * for an appId the map does not know even after one refresh. Why an owned
 * appId can be missing from the map: `docs/architecture/save-file-sync-architecture.md`,
 * "Pre-launch sync".
 */
async function readStartRom(appId: number): Promise<{ romId: number; installed: boolean } | null> {
  let romId = getAppIdRomIdMapSnapshot()[String(appId)];
  if (romId == null) {
    await refreshAppIdMap();
    romId = getAppIdRomIdMapSnapshot()[String(appId)];
    if (romId == null) return null;
  }
  return { romId, installed: await isRomInstalled(appId, romId) };
}

/**
 * `promptsForStart` is called once per start, as its gate begins
 * (docs/architecture/save-file-sync-architecture.md, "Pre-launch sync").
 */
export function registerLaunchInterceptor(promptsForStart: () => LaunchPrompts): void {
  SteamClient.Apps.RegisterForGameActionStart(
    (gameActionId: number, gameId: string, action: string, launchSource: number) => {
      if (action !== "LaunchApp") return;

      const appId = appIdFromGameId(gameId);
      if (appId === null || !isRomMAppId(appId)) return;

      // One-shot skip: a start a caller has already handled (the watcher's own
      // relaunch, or a Play-button start) — do NOT gate it again.
      if (consumeLaunchSkip(appId)) return;

      // Already-running guard. A Play press on a game that is ALREADY running
      // still fires GameActionStart. Intercepting it would cancel the launch, run
      // the pre-launch gate, and upload the save MID-SESSION while the emulator
      // holds the file open. Skip the whole funnel while the game is running.
      const pressedRomId = getAppIdRomIdMapSnapshot()[String(appId)];
      const running = readGameRunning(appId, pressedRomId);
      if (running.running) {
        logInfo(
          `Launch interceptor: appId=${appId} already running — skipping pre-launch sync [${running.diagnostics}]`,
        );
        return;
      }

      // CANCEL FIRST — synchronously, before any await. This wins the race
      // against the un-pausable launch: from here the launch is stopped and we
      // relaunch only on approval.
      SteamClient.Apps.CancelGameAction(gameActionId);

      // A stranded panel reaches no backend, so this start could only run
      // without its pre-launch sync: it is refused before the launch gate asks
      // anything.
      const stranded = strandedAnswer();
      if (stranded) {
        tellStrandedPress("Couldn't start the game", strandedPanelSentence(stranded));
        return;
      }

      logInfo(
        `Launch interceptor: appId=${appId} not running — running the launch gate [${running.diagnostics}] launchSource=${launchSource}`,
      );
      const start: CancelledStart = { appId, gameId };

      detach(
        (async () => {
          try {
            // Fire-and-forget migration refresh — picks up a RetroDECK home
            // change made since the prior session.
            refreshMigrationState()
              .then(({ retrodeck }) => {
                setMigrationStatus(retrodeck);
              })
              .catch((e) => logError(`Pre-launch migration refresh failed: ${e}`));

            // Losing this race abandons the read rather than cancelling it, and
            // the start is not waited on again: an answer arriving after the
            // deadline changes nothing.
            let rom: Awaited<ReturnType<typeof readStartRom>>;
            try {
              rom = await withTimeout(readStartRom(appId), FIRST_CONTACT_DEADLINE_MS);
            } catch (e) {
              if (!(e instanceof TimeoutError)) throw e;
              logError(
                `Launch interceptor: no backend answer for appId=${appId} within ${FIRST_CONTACT_DEADLINE_MS}ms — starting without the pre-launch sync`,
              );
              showToast(NOT_RESPONDING_TOAST_BODY);
              bareRelaunch(start);
              return;
            }

            // An appId the map does not know is not ours to gate — relaunch and bail.
            if (rom === null) {
              bareRelaunch(start);
              return;
            }

            // The funnel assumes an installed ROM. Not installed → hard block
            // (no relaunch).
            if (!rom.installed) {
              showToast(await notInstalledSentence(appId));
              return;
            }

            // The emulator the game starts with cannot start it, or the game's
            // file does not start with it: refused with the game page's own
            // words, no relaunch (#2195 D7, D2).
            const refusal = await emulatorRefusalForRom(rom.romId, WATCHER_TAG);
            if (refusal !== null) {
              showToast(refusal);
              return;
            }

            await runWatcherGate(start, rom.romId, promptsForStart());
          } catch (e) {
            // An unexpected error must not trap the launch. Use the bare
            // relaunch (no re-confirm): the failure may be the re-confirm's own
            // dependency, and the priority here is escaping the cancelled state,
            // not healing drift.
            logError(`Launch interceptor error: ${e}`);
            bareRelaunch(start);
          }
        })(),
      );
    },
  );

  logInfo("Launch interceptor registered");
}
