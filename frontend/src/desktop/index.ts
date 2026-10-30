/**
 * Tender's desktop client surface: the one place that decides what the
 * navigation watcher draws on a RomM game page, and what the surface offers the
 * launch watcher.
 */

import { logError } from "../api/backend";
import { GameView } from "./gameview/GameView";
import { PlayButton } from "./gameview/PlayButton";
import { offerDesktopLaunchPrompts, withdrawDesktopLaunchPrompts } from "./launchPromptHost";
import { startDesktopNavigationWatcher, stopDesktopNavigationWatcher, type DesktopGamePage } from "./navigationWatcher";

const GAME_PAGE: DesktopGamePage = { gameView: GameView, playButton: PlayButton };

/**
 * Offer the desktop dialogs to the launch watcher and start the navigation
 * watcher. It runs inside the panel's start-up, so a throw is logged here rather
 * than left to stop the rest of that start-up.
 */
export function startDesktopSurface(): void {
  try {
    offerDesktopLaunchPrompts();
    startDesktopNavigationWatcher(GAME_PAGE);
  } catch (e) {
    logError(`desktop: the desktop surface did not start: ${String(e)}`);
  }
}

/**
 * Withdraw the desktop dialogs, settling any open question, and stop the
 * navigation watcher. Nothing in the dev build calls it
 * (docs/architecture/frontend-bundles.md, "The desktop dev build").
 */
export function stopDesktopSurface(): void {
  withdrawDesktopLaunchPrompts();
  stopDesktopNavigationWatcher();
}
