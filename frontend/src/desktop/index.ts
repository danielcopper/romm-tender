/**
 * Tender's desktop client surface: the one place that decides what the
 * navigation watcher draws on a RomM game page.
 */

import { logError } from "../api/backend";
import { startDesktopNavigationWatcher, stopDesktopNavigationWatcher, type DesktopGamePage } from "./navigationWatcher";

const GAME_PAGE: DesktopGamePage = {};

/**
 * Start the navigation watcher. It runs inside the panel's start-up, so a throw
 * is logged here rather than left to stop the rest of that start-up.
 */
export function startDesktopSurface(): void {
  try {
    startDesktopNavigationWatcher(GAME_PAGE);
  } catch (e) {
    logError(`desktop: the desktop surface did not start: ${String(e)}`);
  }
}

/**
 * Stop the navigation watcher. Nothing in the dev build calls it
 * (docs/architecture/frontend-bundles.md, "The desktop dev build").
 */
export function stopDesktopSurface(): void {
  stopDesktopNavigationWatcher();
}
