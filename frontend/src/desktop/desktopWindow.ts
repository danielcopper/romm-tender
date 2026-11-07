/**
 * Locates Steam's desktop client window (a popup of its own, `SP Desktop *`)
 * and React's client module. Which realm the watcher takes its constructors
 * from: docs/architecture/desktop-dom-architecture.md, "Two windows, two realms".
 */

import { findModule } from "@decky/ui";
import type { Root } from "react-dom/client";

export interface ReactClientModule {
  createRoot: (container: Element | DocumentFragment, options?: { onUncaughtError?: (error: unknown) => void }) => Root;
}

interface PopupDescriptor {
  m_strName?: string;
  m_popup?: Window;
}

interface PopupManager {
  GetPopups: () => Iterable<PopupDescriptor>;
}

/** Locates the desktop client window (`SP Desktop`) from Steam's popup manager. */
export function findDesktopWindow(): Window | undefined {
  const popupManager = (window as unknown as { g_PopupManager?: PopupManager }).g_PopupManager;
  if (!popupManager || typeof popupManager.GetPopups !== "function") {
    return undefined;
  }

  const popups = popupManager.GetPopups();
  for (const p of popups) {
    if (typeof p.m_strName === "string" && p.m_strName.startsWith("SP Desktop")) {
      return p.m_popup;
    }
  }
  return undefined;
}

const exportsCreateRoot = (m: unknown): boolean =>
  typeof m === "object" && m !== null && typeof (m as Record<string, unknown>).createRoot === "function";

/** The module in Steam's registry that exports `createRoot`. */
export function findReactClient(): ReactClientModule | undefined {
  return findModule(exportsCreateRoot) as ReactClientModule | undefined;
}

/**
 * The same search, run once when this module is evaluated: what the start-up
 * check asks after (`boot/steamModules.ts`), in the one build that carries the
 * desktop surface. Every other build leaves this module out
 * (`frontend/rollup.config.js`).
 */
export const ReactClient: ReactClientModule | undefined = findModule(exportsCreateRoot) as
  ReactClientModule | undefined;
