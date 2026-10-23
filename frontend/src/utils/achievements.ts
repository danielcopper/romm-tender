/**
 * A ROM's RetroAchievements list and progress: the lazy load of both, the map of
 * what is earned, and the earned-first order a list is drawn in.
 *
 * The hook binds what it holds to the ROM it was read for, and shows it only while
 * that ROM is the one asked about (`docs/architecture/invariants.md`, the entry on
 * per-rom detail state): the game page calls it from the panel, which a version
 * switch does not remount.
 */

import { useState, useEffect, useRef } from "react";
import { getAchievements, getAchievementProgress, debugLog } from "../api/backend";
import type { Achievement, AchievementProgress, EarnedAchievement } from "../types";
import {
  beginServerLoad,
  reportServerReachable,
  setServerRetryProgress,
  settleServerLoad,
  useRommConnectionState,
} from "./connectionState";
import { detach } from "./detach";

export interface AchievementsLoad {
  achievements: Achievement[];
  progress: AchievementProgress | null;
  loading: boolean;
  isOffline: boolean;
}

/** One ROM's answers, tagged with the ROM they were read for. */
interface LoadedAchievements {
  romId: number;
  achievements: Achievement[];
  progress: AchievementProgress | null;
}

const NO_ACHIEVEMENTS: Achievement[] = [];

/** Load `romId`'s achievement list and progress the first time `isActive` is
 *  true with a `raId` while RomM is not known offline, and keep them while
 *  `isActive` goes false again. A load torn down mid-flight or one that throws
 *  is tried again on the next activation or reconnect, and one answered
 *  unreachable once RomM reconnects; any other answer stands. Each ROM is loaded
 *  once, and only the answers for `romId` are answered. */
export function useAchievements(romId: number | null, raId: number | null, isActive: boolean): AchievementsLoad {
  const isOffline = useRommConnectionState() === "offline";
  const [loaded, setLoaded] = useState<LoadedAchievements | null>(null);
  const [loadingFor, setLoadingFor] = useState<number | null>(null);
  // The ROM whose load has answered or is running.
  const loadedRef = useRef<number | null>(null);

  // Mirrors the offline handling of the game page's save-slot load
  // (`useSaveSlotsLoad` in `frontend/src/bigpicture/panelSlotsLoad.ts`): a
  // known-offline fast path (no ladder hang), a reachability feed, a reload on
  // reconnect, and a mid-flight-teardown guard.
  useEffect(() => {
    if (!isActive || !raId || romId === null) return;
    if (loadedRef.current === romId) return;

    // Known-offline fast path: the server fetch runs the retry ladder, so on a
    // known-unreachable server it would hang the load for tens of seconds. Skip
    // it — `isOffline` lets the caller show a short degraded line, and any
    // last-known list stays. The gate stays open, so a flip back to
    // connected re-runs this effect (isOffline dep) and loads.
    if (isOffline) return;
    loadedRef.current = romId;

    const load = beginServerLoad();
    let cancelled = false;
    let settled = false;

    async function loadAchievements(forRom: number) {
      // Drop stale retry progress from a prior load, so a caller's retry-progress
      // display (`useServerRetryProgress`) starts without an attempt count.
      setServerRetryProgress(null);
      setLoadingFor(forRom);
      try {
        const [listResult, progressResult] = await Promise.all([
          getAchievements(forRom),
          getAchievementProgress(forRom),
        ]);
        if (cancelled) return;
        settled = true;
        // Conservative reachability feed: report offline only on a genuine
        // unreachable verdict from either call. Treat a resolved
        // non-stale success as a connected signal — this can be cache-served
        // (get_achievements / get_achievement_progress answer from a warm cache
        // without touching the server), so it is not a hard reachability proof,
        // but that is acceptable: the 30s heartbeat is the reachability authority
        // and self-corrects a wrong "connected". A "no_ra_username" config gap and
        // a stale-cache fallback are neither verdict — leave the store untouched.
        const unreachable =
          (!listResult.success && listResult.reason === "server_unreachable") ||
          (!progressResult.success && progressResult.reason === "server_unreachable");
        if (unreachable) {
          reportServerReachable(false);
          // Release the gate on an unreachable answer only, so the reconnect
          // loads again; unlike the save-slot load, any other failed answer stands.
          loadedRef.current = null;
        } else if ((listResult.success && !listResult.stale) || (progressResult.success && !progressResult.stale)) {
          reportServerReachable(true);
        }
        // A failed answer keeps the last-known list or progress rather than
        // emptying it on a transient blip.
        setLoaded((prev) => {
          const kept = prev?.romId === forRom ? prev : null;
          return {
            romId: forRom,
            achievements: listResult.success ? listResult.achievements : (kept?.achievements ?? NO_ACHIEVEMENTS),
            progress: progressResult.success ? progressResult : (kept?.progress ?? null),
          };
        });
        setLoadingFor(null);
      } catch (e) {
        detach(debugLog(`Failed to load achievements: ${e}`));
        if (!cancelled) {
          settled = true;
          loadedRef.current = null;
          setLoadingFor(null);
        }
      } finally {
        settleServerLoad(load);
      }
    }

    detach(loadAchievements(romId));
    return () => {
      cancelled = true;
      // Torn down mid-flight (e.g. a concurrent call flipped the store offline):
      // release the gate so the re-run or reconnect loads again, and clear
      // `loading`, which the dropped answer never will.
      if (!settled) {
        loadedRef.current = null;
        setLoadingFor(null);
      }
    };
  }, [isActive, raId, romId, isOffline]);

  const shown = loaded?.romId === romId ? loaded : null;
  return {
    achievements: shown?.achievements ?? NO_ACHIEVEMENTS,
    progress: shown?.progress ?? null,
    loading: loadingFor !== null && loadingFor === romId,
    isOffline,
  };
}

/** Each earned achievement by its badge id (`EarnedAchievement.id`). */
export function earnedByBadgeId(progress: AchievementProgress | null): Map<string, EarnedAchievement> {
  const earnedMap = new Map<string, EarnedAchievement>();
  for (const ea of progress?.earned_achievements ?? []) {
    earnedMap.set(ea.id, ea);
  }
  return earnedMap;
}

/** A copy of `achievements`, earned first, then by `display_order`. */
export function orderEarnedFirst(
  achievements: readonly Achievement[],
  earnedMap: ReadonlyMap<string, EarnedAchievement>,
): Achievement[] {
  return [...achievements].sort((a, b) => {
    const aEarned = earnedMap.has(a.badge_id) ? 0 : 1;
    const bEarned = earnedMap.has(b.badge_id) ? 0 : 1;
    if (aEarned !== bEarned) return aEarned - bEarned;
    return (a.display_order || 0) - (b.display_order || 0);
  });
}
