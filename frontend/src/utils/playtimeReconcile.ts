/**
 * A game page's PLAYTIME and LAST PLAYED: read from Steam's overview, folded
 * with RomM's play sessions on view, and kept current as the overview changes.
 */

import { useState, useEffect, useRef } from "react";
import { reconcilePlaytime, debugLog } from "../api/backend";
import { formatPlaytime, formatLastPlayed, resolveLastPlayed } from "./formatters";
import { updatePlaytimeDisplay } from "./metadataPatches";
import { detach } from "./detach";

/** The play row's PLAYTIME / LAST PLAYED state. Everything the game page's surfaces
 *  share — rom identity, install state, save sync, BIOS, core — lives in the
 *  per-appId game-detail store instead; this pair is read from Steam's overview and
 *  belongs to the play row alone. */
interface PlaytimeState {
  lastPlayed: string;
  /** Restored cross-device `last_played` (ISO-8601) from `reconcile_playtime`,
   *  or `null` until the server yields one and again once a session's end has
   *  set Steam's value. Preferred over Steam's device-local
   *  `rt_last_time_played` when rendering LAST PLAYED (#1294). */
  restoredLastPlayed: string | null;
  playtime: string;
}

export interface GamePlaytime {
  lastPlayed: string;
  playtime: string;
}

/**
 * The PLAYTIME and LAST PLAYED labels for *appId*, reconciled with RomM once
 * *romId* is known.
 *
 * @param logContext Prefix for this hook's debugLog lines
 */
export function useGamePlaytime(appId: number, romId: number | null, logContext: string = "Playtime"): GamePlaytime {
  // Read playtime from Steam's own overview synchronously (already written by metadataPatches)
  // This avoids an unnecessary render from setting it inside the async effect.
  const overview = appStore.GetAppOverviewByAppID(appId);
  const initialLastPlayed = formatLastPlayed(overview?.rt_last_time_played ?? 0);
  const initialPlaytime = formatPlaytime(overview?.minutes_playtime_forever ?? 0);

  const [playtimeInfo, setPlaytimeInfo] = useState<PlaytimeState>({
    lastPlayed: initialLastPlayed,
    restoredLastPlayed: null,
    playtime: initialPlaytime,
  });

  // The play section is mounted under one constant key, so Steam can hand the same
  // instance another game; nothing the previous game restored may stay on it.
  const prevAppIdRef = useRef(appId);
  useEffect(() => {
    if (prevAppIdRef.current !== appId) {
      prevAppIdRef.current = appId;
      const ov = appStore.GetAppOverviewByAppID(appId);
      setPlaytimeInfo({
        lastPlayed: formatLastPlayed(ov?.rt_last_time_played ?? 0),
        restoredLastPlayed: null,
        playtime: formatPlaytime(ov?.minutes_playtime_forever ?? 0),
      });
    }
  }, [appId]);

  // Reconcile-on-view (#868) — pull-only: folds RomM's play-session history into
  // the local total so a session played on another device shows up the moment the
  // detail page opens. INTENTIONALLY NOT gated on connectivity (#1345): reconcile
  // returns the LOCAL total even when the server is unreachable, so it re-injects
  // real playtime into a rebuilt/rebound Steam overview instead of leaving it at
  // "PLAYTIME None". Runs once romId is known (its own effect, so it fires after
  // the store resolves romId rather than racing the connection check). Pushes
  // the total through updatePlaytimeDisplay (the write-chokepoint), which emits
  // romm_playtime_changed; the reactive PLAYTIME effect (#869) refreshes the
  // display on the same mount. A 0 total no-ops in updatePlaytimeDisplay.
  useEffect(() => {
    if (!romId) return;
    let cancelled = false;

    async function doReconcilePlaytime(rid: number, isCancelled: () => boolean) {
      try {
        const result = await reconcilePlaytime(rid);
        if (isCancelled()) return;
        if ("success" in result) {
          detach(debugLog(`${logContext}: playtime reconcile deferred: ${result.message}`));
          return;
        }
        if (!result.server_query_failed) {
          // Connected: adopt the restored cross-device last_played (#1294) and
          // refresh the display from it. Set BEFORE updatePlaytimeDisplay so the
          // synchronous romm_playtime_changed handler reads the new value; also
          // covers the sub-minute total case where updatePlaytimeDisplay emits no
          // signal. Skipped on a failed read — no cross-device push offline.
          const steamSecs = appStore.GetAppOverviewByAppID(appId)?.rt_last_time_played ?? 0;
          setPlaytimeInfo((prev) => ({
            ...prev,
            restoredLastPlayed: result.last_played,
            lastPlayed: resolveLastPlayed(result.last_played, steamSecs),
          }));
        }
        // Re-inject the local total regardless of connectivity (offline fix #1345).
        updatePlaytimeDisplay(appId, result.total_seconds, false);
      } catch (e) {
        detach(debugLog(`${logContext}: playtime reconcile error: ${e}`));
      }
    }

    detach(doReconcilePlaytime(romId, () => cancelled));
    return () => {
      cancelled = true;
    };
  }, [romId, appId, logContext]);

  // Reactive PLAYTIME display (#869) — re-read Steam's overview whenever the
  // playtime write-chokepoint (updatePlaytimeDisplay) fires romm_playtime_changed
  // for this appId. Drives the displayed PLAYTIME / LAST PLAYED from the source
  // of truth (the overview) instead of a mount-only snapshot, so a session end
  // (handleGameStop) or a multi-device reconcile-on-view refreshes the value on
  // the SAME mount — no navigate-away/back remount required. A write that set
  // Steam's last-played time itself (a session's end) is newer than anything
  // restored before it, so it drops the restored value; any other write leaves
  // it winning, since Steam's own value can be the "now" of a device cutover.
  useEffect(() => {
    const onPlaytimeChanged = (e: WindowEventMap["romm_playtime_changed"]) => {
      if (e.detail.appId !== appId) return;
      const ov = appStore.GetAppOverviewByAppID(appId);
      if (!ov) return;
      setPlaytimeInfo((prev) => {
        const restoredLastPlayed = e.detail.lastPlayedWritten ? null : prev.restoredLastPlayed;
        return {
          ...prev,
          restoredLastPlayed,
          playtime: formatPlaytime(ov.minutes_playtime_forever ?? 0),
          lastPlayed: resolveLastPlayed(restoredLastPlayed, ov.rt_last_time_played ?? 0),
        };
      });
    };
    globalThis.addEventListener("romm_playtime_changed", onPlaytimeChanged);
    return () => {
      globalThis.removeEventListener("romm_playtime_changed", onPlaytimeChanged);
    };
  }, [appId]);

  return { lastPlayed: playtimeInfo.lastPlayed, playtime: playtimeInfo.playtime };
}
