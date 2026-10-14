/**
 * Shared hook and utilities for on-view playtime reconciliation and reactive
 * Steam overview sync.
 *
 * Folds RomM's play-session history into local total on mount / when romId becomes
 * available, updates Steam's overview via `updatePlaytimeDisplay`, and listens to
 * `romm_playtime_changed` to update display reactively.
 */

import { useState, useEffect, useRef } from "react";
import { reconcilePlaytime, debugLog } from "../api/backend";
import { formatPlaytime, formatLastPlayed, resolveLastPlayed } from "./formatters";
import { updatePlaytimeDisplay } from "./metadataPatches";
import { detach } from "./detach";

interface PlaytimeState {
  lastPlayed: string;
  /** Restored cross-device `last_played` (ISO-8601) from `reconcile_playtime`,
   *  or `null` until the server yields one. Preferred over Steam's device-local
   *  `rt_last_time_played` when rendering LAST PLAYED. */
  restoredLastPlayed: string | null;
  playtime: string;
}

export interface GamePlaytime {
  lastPlayed: string;
  playtime: string;
}

/**
 * Hook to manage playtime reconciliation and display for a game.
 *
 * @param appId Steam shortcut App ID
 * @param romId RomM ROM ID, or null if unassigned / unknown
 * @param logContext Context tag for debugLog messages
 *   (e.g. "RomMPlaySection" or "DesktopPlayButton")
 */
export function useGamePlaytime(appId: number, romId: number | null, logContext: string = "Playtime"): GamePlaytime {
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

  // Reconcile-on-view: folds RomM's play-session history into local total.
  // INTENTIONALLY NOT gated on connectivity (#1345): reconcile returns the
  // LOCAL total even when the server is unreachable, so it re-injects real
  // playtime into a rebuilt/rebound Steam overview instead of leaving it at
  // "PLAYTIME None".
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
          // Connected: adopt restored cross-device last_played (#1294).
          const ov = appStore.GetAppOverviewByAppID(appId);
          const steamSecs = ov?.rt_last_time_played ?? 0;
          setPlaytimeInfo((prev) => ({
            ...prev,
            restoredLastPlayed: result.last_played,
            lastPlayed: resolveLastPlayed(result.last_played, steamSecs),
          }));
        }
        // Re-inject local total regardless of connectivity (offline fix #1345).
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

  // Reactive PLAYTIME display: re-read Steam's overview whenever the
  // playtime write-chokepoint (updatePlaytimeDisplay) fires romm_playtime_changed
  // for this appId.
  useEffect(() => {
    const onPlaytimeChanged = (e: WindowEventMap["romm_playtime_changed"]) => {
      if (e.detail.appId !== appId) return;
      const ov = appStore.GetAppOverviewByAppID(appId);
      if (!ov) return;
      setPlaytimeInfo((prev) => ({
        ...prev,
        playtime: formatPlaytime(ov.minutes_playtime_forever ?? 0),
        lastPlayed: resolveLastPlayed(prev.restoredLastPlayed, ov.rt_last_time_played ?? 0),
      }));
    };
    globalThis.addEventListener("romm_playtime_changed", onPlaytimeChanged);
    return () => {
      globalThis.removeEventListener("romm_playtime_changed", onPlaytimeChanged);
    };
  }, [appId]);

  return { lastPlayed: playtimeInfo.lastPlayed, playtime: playtimeInfo.playtime };
}
