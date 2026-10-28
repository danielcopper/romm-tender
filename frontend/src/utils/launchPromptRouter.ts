/**
 * Which surface a start the launch watcher caught asks its questions through.
 * `utils/` may not import a surface, so the desktop surface registers a
 * provider of its prompts here, and each start that reaches the launch gate
 * asks here once.
 */

import { debugLog } from "../api/backend";
import { detach } from "./detach";
import type { LaunchPrompts } from "./launchVerdict";

/**
 * `SteamUIStore.MainInstanceUIMode` while the desktop client is Steam's main
 * window. What the mode reads in each of Steam's windows, and what it cannot
 * tell apart: `docs/architecture/desktop-dom-architecture.md`, "Which window
 * is Steam's main UI".
 */
const DESKTOP_UI_MODE = 7;

type DesktopLaunchPromptsProvider = () => LaunchPrompts | null;

let desktopProvider: DesktopLaunchPromptsProvider | null = null;

/** Is the desktop client Steam's main UI? An unreadable store answers no. */
export function steamUIModeIsDesktop(): boolean {
  try {
    return SteamUIStore?.MainInstanceUIMode === DESKTOP_UI_MODE;
  } catch {
    // An undeclared `SteamUIStore` throws ReferenceError, as does a getter that throws.
    return false;
  }
}

/**
 * Register the desktop surface's prompts, or withdraw them with `null`. The
 * provider answers `null` while it has no window to draw into.
 */
export function setDesktopLaunchPrompts(provider: DesktopLaunchPromptsProvider | null): void {
  desktopProvider = provider;
}

/**
 * The prompts one caught start asks through: the desktop surface's while the
 * desktop client is Steam's main UI and that surface can draw, `fallback`
 * otherwise.
 */
export function launchPromptsForThisStart(fallback: LaunchPrompts): LaunchPrompts {
  const pick = (surface: "desktop" | "gamepad", why: string): void => {
    detach(debugLog(`Launch prompts: ${surface} — ${why}`));
  };
  if (!steamUIModeIsDesktop()) {
    pick("gamepad", "the desktop client is not Steam's main UI");
    return fallback;
  }
  if (!desktopProvider) {
    pick("gamepad", "no desktop surface is registered");
    return fallback;
  }
  let desktop: LaunchPrompts | null;
  try {
    desktop = desktopProvider();
  } catch (e) {
    pick("gamepad", `the desktop surface threw: ${e}`);
    return fallback;
  }
  if (!desktop) {
    pick("gamepad", "the desktop surface has no window to draw into");
    return fallback;
  }
  pick("desktop", "the desktop client is Steam's main UI");
  return desktop;
}
