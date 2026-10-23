/**
 * A ROM's RetroAchievements list and progress: the lazy load of both, the map of
 * what is earned, and the earned-first order a list is drawn in.
 *
 * Nothing in the hook binds its state to the ROM it was called with: the key of
 * the component that calls it does (`docs/architecture/invariants.md`, the entry
 * on per-rom detail state).
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
 *  `isActive` goes false again. A load torn down mid-flight, answered
 *  unreachable, or that throws is tried again on the next activation or
 *  reconnect; any other answer stands. */
export function useAchievements(romId: number, raId: number | null, isActive: boolean): AchievementsLoad {
  const isOffline = useRommConnectionState() === "offline";
  const [achievements, setAchievements] = useState<Achievement[]>([]);
  const [progress, setProgress] = useState<AchievementProgress | null>(null);
  const [loading, setLoading] = useState(false);
  const loadedRef = useRef(false);

  // Lazy-load on first activation. Mirrors the game page's save-slot load's
  // offline handling (#1345 F1): a known-offline fast path (no ladder hang), a
  // reachability feed, auto-reload on reconnect, and a mid-flight-teardown guard
  // so a store flip can't wedge the spinner.
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
      // display (`useServerRetryProgress`) starts without an attempt count (#1345).
      setServerRetryProgress(null);
      setLoading(true);
      try {
        const [listResult, progressResult] = await Promise.all([getAchievements(romId), getAchievementProgress(romId)]);
        if (cancelled) return;
        settled = true;
        // Conservative reachability feed (#1345): report offline only on a
        // genuine unreachable verdict from either call. Treat a resolved
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
          // Mirror the save-slot load's failure reset: release the gate so a reconnect
          // (or a later re-activation) retries instead of caching the failure.
          loadedRef.current = false;
        } else if ((listResult.success && !listResult.stale) || (progressResult.success && !progressResult.stale)) {
          reportServerReachable(true);
        }
        // Keep the last-known values on a failed load — never clobber an
        // already-shown list / progress count to empty on a transient blip.
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
      // Torn down mid-flight (e.g. a concurrent call flipped the store offline) —
      // release the gate and drop the spinner so the re-run / reconnect isn't
      // wedged behind a stuck loading flag (#1345 F1, mirrors the save-slot load).
      if (!settled) {
        loadedRef.current = false;
        setLoading(false);
      }
    };
  }, [isActive, raId, romId, isOffline]);

  return { achievements, progress, loading, isOffline };
}

/** Each earned achievement by its badge id — the `id` of an entry in
 *  `earned_achievements` is the achievement's `badge_id`. */
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
