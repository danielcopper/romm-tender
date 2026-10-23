/**
 * A ROM's RetroAchievements list and progress: the lazy load of both, the map of
 * what is earned, and the earned-first order a list is drawn in.
 *
 * The hook binds its state to no ROM: a caller must key the component that calls
 * it by the ROM id (`docs/architecture/invariants.md`, the entry on per-rom detail
 * state).
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

interface AchievementsLoad {
  achievements: Achievement[];
  progress: AchievementProgress | null;
  loading: boolean;
  isOffline: boolean;
}

/** Load `romId`'s achievement list and progress the first time `isActive` is
 *  true with a `raId` while RomM is not known offline, and keep them while
 *  `isActive` goes false again. A load torn down mid-flight or one that throws
 *  is tried again on the next activation or reconnect, and one answered
 *  unreachable once RomM reconnects; any other answer stands. */
export function useAchievements(romId: number, raId: number | null, isActive: boolean): AchievementsLoad {
  const isOffline = useRommConnectionState() === "offline";
  const [achievements, setAchievements] = useState<Achievement[]>([]);
  const [progress, setProgress] = useState<AchievementProgress | null>(null);
  const [loading, setLoading] = useState(false);
  const loadedRef = useRef(false);

  // Mirrors the offline handling of the game page's save-slot load
  // (`useSaveSlotsLoad` in `frontend/src/bigpicture/panelSlotsLoad.ts`): a
  // known-offline fast path (no ladder hang), a reachability feed, a reload on
  // reconnect, and a mid-flight-teardown guard.
  useEffect(() => {
    if (!isActive || !raId) return;
    if (loadedRef.current) return;

    // Known-offline fast path: the server fetch runs the retry ladder, so on a
    // known-unreachable server it would hang the load for tens of seconds. Skip
    // it — `isOffline` lets the caller show a short degraded line, and any
    // last-known list stays. The ref stays false, so a flip back to
    // connected re-runs this effect (isOffline dep) and loads.
    if (isOffline) return;
    loadedRef.current = true;

    const load = beginServerLoad();
    let cancelled = false;
    let settled = false;

    async function loadAchievements() {
      // Drop stale retry progress from a prior load, so a caller's retry-progress
      // display (`useServerRetryProgress`) starts without an attempt count.
      setServerRetryProgress(null);
      setLoading(true);
      try {
        const [listResult, progressResult] = await Promise.all([getAchievements(romId), getAchievementProgress(romId)]);
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
          loadedRef.current = false;
        } else if ((listResult.success && !listResult.stale) || (progressResult.success && !progressResult.stale)) {
          reportServerReachable(true);
        }
        // A failed answer keeps the last-known list or progress rather than
        // emptying it on a transient blip.
        if (listResult.success) setAchievements(listResult.achievements);
        if (progressResult.success) setProgress(progressResult);
        setLoading(false);
      } catch (e) {
        detach(debugLog(`Failed to load achievements: ${e}`));
        if (!cancelled) {
          settled = true;
          loadedRef.current = false;
          setLoading(false);
        }
      } finally {
        settleServerLoad(load);
      }
    }

    detach(loadAchievements());
    return () => {
      cancelled = true;
      // Torn down mid-flight (e.g. a concurrent call flipped the store offline):
      // release the gate so the re-run or reconnect loads again, and clear
      // `loading`, which the dropped answer never will.
      if (!settled) {
        loadedRef.current = false;
        setLoading(false);
      }
    };
  }, [isActive, raId, romId, isOffline]);

  return { achievements, progress, loading, isOffline };
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
