/**
 * The desktop dialogs for a start the launch watcher catches
 * (`utils/launchInterceptor.ts`), each drawn in a React root of its own in the
 * desktop window that exists when it is asked. What settles a question, and why
 * a start caught while the window sits in the tray waits unseen:
 * `docs/architecture/desktop-dom-architecture.md`, "Dialogs for a start the
 * launch watcher catches".
 */

import type { ReactNode } from "react";
import { logError, logWarn } from "../api/backend";
import { setDesktopLaunchPrompts } from "../utils/launchPromptRouter";
import type { LaunchPrompts } from "../utils/launchVerdict";
import { findDesktopWindow, findReactClient, type ReactClientModule } from "./desktopWindow";
import { desktopLaunchPrompts } from "./gameview/dialogs/desktopDialogs";
import type { AskDialog } from "./gameview/dialogs/askDialog";
import { DomRestorationLedger } from "./watcher/restorationLedger";

const openQuestions = new Set<(why: string) => void>();

function windowState(win: Window | undefined): "none" | "closed" | "open" {
  if (!win) return "none";
  return win.closed ? "closed" : "open";
}

export const askInDesktopWindow: AskDialog = <T,>(
  dismissed: T,
  render: (resolve: (value: T) => void) => ReactNode,
): Promise<T> => {
  let win: Window | undefined;
  let client: ReactClientModule | undefined;
  try {
    win = findDesktopWindow();
    client = findReactClient();
  } catch (e) {
    logError(
      `desktop: could not look for a window to draw a launch question into, so it was answered as dismissed: ${String(e)}`,
    );
    return Promise.resolve(dismissed);
  }
  if (!win || win.closed || !client) {
    logWarn(
      `desktop: a launch question had nothing to draw into (window=${windowState(win)}, createRoot=${client ? "found" : "missing"}), so it was answered as dismissed`,
    );
    return Promise.resolve(dismissed);
  }

  const doc = win.document;
  const ledger = new DomRestorationLedger();
  return new Promise<T>((outer) => {
    let settled = false;
    const settle = (value: T): void => {
      if (settled) return;
      settled = true;
      openQuestions.delete(dismissBecause);
      ledger.restoreAll();
      outer(value);
    };
    const answer = (value: T): void => {
      if (settled) {
        logWarn(
          `desktop: a launch question was answered after it had been dismissed, so the answer was dropped: ${String(value)}`,
        );
        return;
      }
      settle(value);
    };
    const dismissBecause = (why: string): void => {
      if (settled) return;
      logWarn(`desktop: a launch question was answered as dismissed because ${why}`);
      settle(dismissed);
    };
    const failed = (e: unknown): void => {
      logError(`desktop: could not draw a launch question, so it was answered as dismissed: ${String(e)}`);
      // React reports a render error from inside its own work loop; the root is
      // unmounted once that has unwound.
      queueMicrotask(() => settle(dismissed));
    };

    const host = doc.createElement("div");
    host.className = "tender-desktop-launch-prompt";
    try {
      doc.body.appendChild(host);
      const root = client.createRoot(host, { onUncaughtError: failed });
      ledger.recordRoot(root, host);
      ledger.addListener(win, "pagehide", () => dismissBecause("its window went"));
      ledger.addListener(win, "unload", () => dismissBecause("its window unloaded"));
      openQuestions.add(dismissBecause);
      root.render(render(answer));
    } catch (e) {
      logError(`desktop: could not draw a launch question, so it was answered as dismissed: ${String(e)}`);
      settle(dismissed);
      if (host.isConnected) host.remove();
    }
  });
};

/** The launch prompts drawn in the desktop window, or `null` while there is no open one. */
function promptsInDesktopWindow(): LaunchPrompts | null {
  const win = findDesktopWindow();
  return win && !win.closed ? desktopLaunchPrompts(askInDesktopWindow) : null;
}

/** Offer the desktop dialogs to the launch watcher. */
export function offerDesktopLaunchPrompts(): void {
  setDesktopLaunchPrompts(promptsInDesktopWindow);
}

/** Withdraw the offer and settle every open question with its dismissed answer. */
export function withdrawDesktopLaunchPrompts(): void {
  setDesktopLaunchPrompts(null);
  for (const dismissBecause of openQuestions) dismissBecause("the desktop dialogs were withdrawn");
}
